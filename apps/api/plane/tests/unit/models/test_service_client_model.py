# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Unit tests for the ServiceClient model and its link to Project."""

from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from plane.bgtasks.deletion_task import soft_delete_related_objects
from plane.db.models import Issue, Project, ServiceBillingType, ServiceClient
from plane.tests.factories import (
    ProjectFactory,
    ServiceBillingTypeFactory,
    ServiceClientFactory,
    UserFactory,
    WorkspaceFactory,
)


@pytest.fixture
def run_soft_delete_cascade_synchronously():
    """Execute the soft delete cascade task inline instead of dispatching it.

    There is no Celery broker under test. Merely mocking `.delay` away would make
    the soft delete isolation tests below pass without ever running the reverse
    relation walk that is the actual hazard, so the task body is invoked directly.
    """

    def run_inline(app_label, model_name, instance_pk, using=None):
        return soft_delete_related_objects(app_label, model_name, instance_pk, using=using)

    with patch("plane.db.mixins.soft_delete_related_objects.delay", side_effect=run_inline) as mocked:
        yield mocked


@pytest.mark.unit
@pytest.mark.django_db
class TestServiceClientModel:
    def test_tax_id_is_normalized_on_save(self):
        workspace = WorkspaceFactory()
        client = ServiceClient.objects.create(
            workspace=workspace, name="Marubeni", tax_id="11.222.333/0001-81"
        )
        client.refresh_from_db()
        assert client.tax_id == "11222333000181"

    def test_blank_tax_id_is_stored_as_null(self):
        workspace = WorkspaceFactory()
        client = ServiceClient.objects.create(workspace=workspace, name="No CNPJ", tax_id="")
        client.refresh_from_db()
        assert client.tax_id is None

    def test_several_clients_may_have_no_tax_id(self):
        """The uniqueness constraint on tax_id excludes NULLs on purpose, so that
        foreign companies and individuals can coexist without one."""
        workspace = WorkspaceFactory()
        ServiceClient.objects.create(workspace=workspace, name="First", tax_id=None)
        ServiceClient.objects.create(workspace=workspace, name="Second", tax_id=None)
        assert ServiceClient.objects.filter(workspace=workspace, tax_id__isnull=True).count() == 2

    def test_duplicate_name_in_the_same_workspace_is_rejected(self):
        workspace = WorkspaceFactory()
        ServiceClient.objects.create(workspace=workspace, name="Marubeni")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceClient.objects.create(workspace=workspace, name="Marubeni")

    def test_duplicate_tax_id_in_the_same_workspace_is_rejected(self):
        workspace = WorkspaceFactory()
        ServiceClient.objects.create(workspace=workspace, name="One", tax_id="11222333000181")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                ServiceClient.objects.create(workspace=workspace, name="Two", tax_id="11222333000181")

    def test_same_name_in_another_workspace_is_allowed(self):
        """Criterion 14: clients are scoped to a workspace and never shared."""
        # UserFactory does not set username, which is unique, so two users would
        # collide on the empty string.
        first = WorkspaceFactory(
            slug="ws-first", owner=UserFactory(email="first@plane.so", username="first")
        )
        second = WorkspaceFactory(
            slug="ws-second", owner=UserFactory(email="second@plane.so", username="second")
        )
        ServiceClient.objects.create(workspace=first, name="Marubeni", tax_id="11222333000181")
        ServiceClient.objects.create(workspace=second, name="Marubeni", tax_id="11222333000181")
        assert ServiceClient.objects.filter(name="Marubeni").count() == 2

    def test_name_may_be_reused_after_soft_delete(self, run_soft_delete_cascade_synchronously):
        """Soft deleted rows are excluded from the partial unique index, so the
        name becomes available again without a hard delete."""
        workspace = WorkspaceFactory()
        client = ServiceClient.objects.create(workspace=workspace, name="Marubeni")
        client.delete()
        recreated = ServiceClient.objects.create(workspace=workspace, name="Marubeni")
        assert recreated.id != client.id

    def test_model_validator_rejects_an_invalid_tax_id(self):
        """Data created outside the API must not be able to persist a bad CNPJ."""
        workspace = WorkspaceFactory()
        client = ServiceClient(workspace=workspace, name="Bad", tax_id="11222333000182")
        with pytest.raises(ValidationError):
            client.full_clean()

    def test_the_default_billing_type_starts_unset(self):
        """Replaces the ``default_billing_mode`` enum this model carried before the
        billing type catalogue existed.

        Unset means "use the catalogue default", which is what
        ``resolve_default_billing_type`` does. There is no per-client default until an
        admin chooses one, so the enum's implicit "contract" default is gone: keeping
        both the enum and the catalogue would have been two sources of truth for one
        decision.
        """
        workspace = WorkspaceFactory()
        client = ServiceClient.objects.create(workspace=workspace, name="Default mode")

        assert client.default_billing_type_id is None
        assert not hasattr(client, "default_billing_mode")

    def test_the_default_billing_type_can_point_at_a_catalogue_option(self):
        workspace = WorkspaceFactory()
        ad_hoc = ServiceBillingTypeFactory(
            workspace=workspace, name="Avulso", billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT
        )

        client = ServiceClient.objects.create(workspace=workspace, name="Terlogs", default_billing_type=ad_hoc)
        client.refresh_from_db()

        assert client.default_billing_type_id == ad_hoc.id
        assert list(ad_hoc.service_clients.all()) == [client]


@pytest.mark.unit
@pytest.mark.django_db
class TestServiceClientParent:
    """Criterion 13: the parent field accepts a value and has no effect anywhere."""

    def test_parent_can_be_set_and_read(self):
        workspace = WorkspaceFactory()
        parent = ServiceClientFactory(workspace=workspace, name="Marubeni")
        child = ServiceClientFactory(workspace=workspace, name="Terlogs", parent=parent)
        child.refresh_from_db()
        assert child.parent_id == parent.id
        assert list(parent.children.all()) == [child]

    def test_parent_does_not_affect_the_default_billing_type(self):
        workspace = WorkspaceFactory()
        contract = ServiceBillingTypeFactory(
            workspace=workspace, name="Contrato", billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL
        )
        ad_hoc = ServiceBillingTypeFactory(
            workspace=workspace, name="Avulso", billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT
        )
        parent = ServiceClientFactory(workspace=workspace, name="Holding", default_billing_type=ad_hoc)
        child = ServiceClientFactory(
            workspace=workspace, name="Subsidiary", parent=parent, default_billing_type=contract
        )

        # The child keeps its own default; nothing is inherited. D18: the parent field
        # records a shareholding relationship and carries no behaviour.
        assert child.default_billing_type_id == contract.id
        assert parent.default_billing_type_id == ad_hoc.id

    def test_the_parent_default_is_not_inherited_when_the_child_has_none(self):
        workspace = WorkspaceFactory()
        ad_hoc = ServiceBillingTypeFactory(
            workspace=workspace, name="Avulso", billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT
        )
        parent = ServiceClientFactory(workspace=workspace, name="Holding", default_billing_type=ad_hoc)
        child = ServiceClientFactory(workspace=workspace, name="Subsidiary", parent=parent)

        child.refresh_from_db()

        assert child.default_billing_type_id is None

    def test_parent_does_not_affect_project_links(self):
        workspace = WorkspaceFactory()
        parent = ServiceClientFactory(workspace=workspace, name="Marubeni")
        child = ServiceClientFactory(workspace=workspace, name="Terlogs", parent=parent)
        ProjectFactory(workspace=workspace, name="Terlogs Support", identifier="TS", service_client=child)

        # The parent gains nothing from its child's projects.
        assert parent.projects.count() == 0
        assert child.projects.count() == 1

    def test_soft_deleting_the_parent_only_clears_the_link(self, run_soft_delete_cascade_synchronously):
        workspace = WorkspaceFactory()
        parent = ServiceClientFactory(workspace=workspace, name="Marubeni")
        child = ServiceClientFactory(workspace=workspace, name="Terlogs", parent=parent)

        parent.delete()

        child.refresh_from_db()
        # The child survives; only the shareholding reference is dropped.
        assert ServiceClient.objects.filter(id=child.id).exists()


@pytest.mark.unit
@pytest.mark.django_db
class TestProjectServiceClientLink:
    def test_project_starts_without_a_client(self):
        """Existing projects are not migrated and must not be blocked."""
        project = ProjectFactory(name="Internal", identifier="INT")
        assert project.service_client_id is None

    def test_two_projects_may_share_one_client(self):
        """Criterion 3: one client, several projects, all resolving to that client."""
        workspace = WorkspaceFactory()
        client = ServiceClientFactory(workspace=workspace, name="Marubeni")
        support = ProjectFactory(
            workspace=workspace, name="Marubeni Support", identifier="MSUP", service_client=client
        )
        erp = ProjectFactory(workspace=workspace, name="Marubeni ERP", identifier="MERP", service_client=client)

        assert {support.service_client_id, erp.service_client_id} == {client.id}
        assert client.projects.count() == 2

    def test_work_item_has_no_client_field(self):
        """Criterion 9, as a negative assertion on the model itself.

        The client of a work item is derived from its project. If a client field
        is ever added to Issue, this fails and D19 has been broken.
        """
        field_names = {field.name for field in Issue._meta.get_fields()}
        assert "service_client" not in field_names
        assert "customer" not in field_names

    def test_no_user_to_client_association_table_exists(self):
        """Criterion 8, as a negative assertion on the schema.

        The link between a user and their clients is derived from project
        membership. A second, duplicated source of truth about access is exactly
        what would drift and cause either a leak or a wrongful block.
        """
        related_models = {
            rel.related_model.__name__
            for rel in ServiceClient._meta.get_fields()
            if rel.is_relation and rel.related_model is not None
        }
        # Only workspace, project, user audit fields, self reference and projects.
        assert "ServiceClientMember" not in related_models
        assert "ServiceClientUser" not in related_models

        member_like = [
            field.name
            for field in ServiceClient._meta.get_fields()
            if field.is_relation
            and field.related_model is not None
            and field.related_model.__name__ == "User"
            # created_by / updated_by are audit columns, not membership.
            and field.name not in {"created_by", "updated_by"}
        ]
        assert member_like == []


@pytest.mark.unit
@pytest.mark.django_db
class TestServiceClientSoftDeleteIsolation:
    """The most important regression test of this phase.

    plane.bgtasks.deletion_task.soft_delete_related_objects walks every reverse
    relation and branches on the on_delete name. CASCADE and PROTECT both fall
    into its catch-all branch, which soft deletes the related objects
    recursively. With either of those, soft deleting a client would soft delete
    its projects and their work items: silent data loss triggered by a routine
    admin action. DO_NOTHING is skipped by that task, which is why the foreign
    key uses it.
    """

    def test_soft_deleting_a_client_does_not_touch_its_projects_or_work_items(
        self, run_soft_delete_cascade_synchronously
    ):
        workspace = WorkspaceFactory()
        client = ServiceClientFactory(workspace=workspace, name="Marubeni")
        project = ProjectFactory(
            workspace=workspace, name="Marubeni Support", identifier="MS", service_client=client
        )
        issue = Issue.objects.create(workspace=workspace, project=project, name="Ticket")

        client.delete()

        project.refresh_from_db()
        issue.refresh_from_db()

        # The client is gone from the default manager...
        assert not ServiceClient.objects.filter(id=client.id).exists()
        # ...while the project and its work items are untouched.
        assert Project.objects.filter(id=project.id).exists()
        assert project.deleted_at is None
        assert Issue.objects.filter(id=issue.id).exists()
        assert issue.deleted_at is None

    def test_soft_deleting_a_client_does_not_unlink_its_projects(self, run_soft_delete_cascade_synchronously):
        """SET_NULL would have silently cleared the link on every project, losing
        the attribution needed to reconstruct history."""
        workspace = WorkspaceFactory()
        client = ServiceClientFactory(workspace=workspace, name="Marubeni")
        project = ProjectFactory(
            workspace=workspace, name="Marubeni Support", identifier="MS2", service_client=client
        )

        client.delete()

        project.refresh_from_db()
        assert project.service_client_id == client.id

    def test_hard_deleting_a_referenced_client_is_refused_by_the_database(self):
        """Criterion 12, enforced at the database level rather than in app code.

        The foreign key is emitted as NO ACTION, so Postgres refuses the delete.
        It is DEFERRABLE INITIALLY DEFERRED, so the error surfaces at commit,
        which is why this runs inside an explicit atomic block.
        """
        workspace = WorkspaceFactory()
        client = ServiceClientFactory(workspace=workspace, name="Marubeni")
        ProjectFactory(workspace=workspace, name="Marubeni Support", identifier="MS3", service_client=client)

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                # The foreign key is DEFERRABLE INITIALLY DEFERRED, so Postgres
                # would only check it at commit, which never happens inside the
                # test transaction. Make it immediate so the refusal surfaces here.
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                client.delete(soft=False)
