# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Contract tests for the work log catalogue endpoints and the audit trail.

Covers the acceptance criteria of the configurable catalogues phase that can be
verified without work logs existing:

* the admin panel CRUD being restricted to workspace admins (6)
* reordering being reflected in the listing order (2, API half)
* deactivating an option removing it from the work log form's list (3, API half)
* exactly one default per catalogue at any moment (6)
* the client's default billing type overriding the catalogue's (7, API half)

Plus the security boundary of the audit trail, which holds multiplier history today
and price history from the pricing phase on: it is commercial intelligence and must
never be reachable by a client.
"""

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    ServiceBillingType,
    ServiceClient,
    ServiceConfigActivity,
    ServiceHourType,
    User,
    Workspace,
    WorkspaceMember,
)

HOUR_TYPES_URL = "/api/workspaces/{slug}/service-hour-types/"
HOUR_TYPE_URL = "/api/workspaces/{slug}/service-hour-types/{pk}/"
HOUR_TYPE_MARK_DEFAULT_URL = "/api/workspaces/{slug}/service-hour-types/{pk}/mark-default/"
BILLING_TYPES_URL = "/api/workspaces/{slug}/service-billing-types/"
BILLING_TYPE_URL = "/api/workspaces/{slug}/service-billing-types/{pk}/"
BILLING_TYPE_MARK_DEFAULT_URL = "/api/workspaces/{slug}/service-billing-types/{pk}/mark-default/"
ACTIVITIES_URL = "/api/workspaces/{slug}/service-config-activities/"
SERVICE_CLIENTS_URL = "/api/workspaces/{slug}/service-clients/"
SERVICE_CLIENT_URL = "/api/workspaces/{slug}/service-clients/{pk}/"

pytestmark = pytest.mark.contract


def _make_user(role_label):
    unique_id = uuid4().hex[:8]
    user = User.objects.create(
        email=f"{role_label}-{unique_id}@plane.so",
        username=f"{role_label}_{unique_id}",
        first_name=role_label.title(),
        last_name="User",
    )
    user.set_password("test-password")
    user.save()
    return user


def _authenticated_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture(autouse=True)
def no_celery_dispatch():
    """Swallow background task dispatch. There is no broker under test."""
    with patch("celery.app.task.Task.apply_async", return_value=None):
        yield


@pytest.fixture
def admin_user(db, workspace):
    """The workspace owner fixture is already an admin; this is a second one."""
    user = _make_user("admin")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=20)
    return user


@pytest.fixture
def member_user(db, workspace):
    user = _make_user("member")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=15)
    return user


@pytest.fixture
def guest_user(db, workspace):
    user = _make_user("guest")
    WorkspaceMember.objects.create(workspace=workspace, member=user, role=5)
    return user


@pytest.fixture
def admin_client(admin_user):
    return _authenticated_client(admin_user)


@pytest.fixture
def member_client(member_user):
    return _authenticated_client(member_user)


@pytest.fixture
def guest_client(guest_user):
    return _authenticated_client(guest_user)


@pytest.fixture
def hour_types(workspace):
    """Three hour types, mirroring the seed. The first created is the default."""
    business = ServiceHourType.objects.create(
        workspace=workspace, name="Horário comercial", multiplier=Decimal("1.00")
    )
    after_hours = ServiceHourType.objects.create(
        workspace=workspace, name="Fora do expediente", multiplier=Decimal("1.50")
    )
    weekend = ServiceHourType.objects.create(
        workspace=workspace, name="Domingos e feriados", multiplier=Decimal("2.00")
    )
    business.refresh_from_db()
    return business, after_hours, weekend


@pytest.fixture
def billing_types(workspace):
    contract = ServiceBillingType.objects.create(
        workspace=workspace, name="Contrato", billing_route=ServiceBillingType.BillingRoute.DEBIT_POOL
    )
    ad_hoc = ServiceBillingType.objects.create(
        workspace=workspace, name="Avulso", billing_route=ServiceBillingType.BillingRoute.BILL_AMOUNT
    )
    warranty = ServiceBillingType.objects.create(
        workspace=workspace, name="Garantia", billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE
    )
    courtesy = ServiceBillingType.objects.create(
        workspace=workspace, name="Cortesia", billing_route=ServiceBillingType.BillingRoute.NON_BILLABLE
    )
    contract.refresh_from_db()
    return contract, ad_hoc, warranty, courtesy


@pytest.mark.django_db
class TestCatalogPermissions:
    """Writes are admin only, reads reach members, and GUEST sees nothing.

    GUEST exclusion is not incidental: the hour type payload carries the multiplier,
    which rule R11 keeps away from clients.
    """

    def test_admin_can_create_an_hour_type(self, admin_client, workspace):
        response = admin_client.post(
            HOUR_TYPES_URL.format(slug=workspace.slug),
            {"name": "Sábado", "multiplier": "1.50", "color": "#F59E0B"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["multiplier"] == "1.50"
        assert ServiceHourType.objects.filter(workspace=workspace, name="Sábado").exists()

    def test_member_can_read_but_not_write(self, member_client, workspace, hour_types):
        business, _, _ = hour_types

        assert member_client.get(HOUR_TYPES_URL.format(slug=workspace.slug)).status_code == status.HTTP_200_OK
        assert (
            member_client.get(HOUR_TYPE_URL.format(slug=workspace.slug, pk=business.pk)).status_code
            == status.HTTP_200_OK
        )

        create = member_client.post(
            HOUR_TYPES_URL.format(slug=workspace.slug), {"name": "Novo", "multiplier": "1.00"}, format="json"
        )
        patch_response = member_client.patch(
            HOUR_TYPE_URL.format(slug=workspace.slug, pk=business.pk), {"multiplier": "9.99"}, format="json"
        )
        delete = member_client.delete(HOUR_TYPE_URL.format(slug=workspace.slug, pk=business.pk))

        assert create.status_code == status.HTTP_403_FORBIDDEN
        assert patch_response.status_code == status.HTTP_403_FORBIDDEN
        assert delete.status_code == status.HTTP_403_FORBIDDEN

    def test_guest_is_refused_everywhere(self, guest_client, workspace, hour_types, billing_types):
        business, _, _ = hour_types
        contract, _, _, _ = billing_types

        forbidden_requests = [
            guest_client.get(HOUR_TYPES_URL.format(slug=workspace.slug)),
            guest_client.get(HOUR_TYPE_URL.format(slug=workspace.slug, pk=business.pk)),
            guest_client.get(BILLING_TYPES_URL.format(slug=workspace.slug)),
            guest_client.get(BILLING_TYPE_URL.format(slug=workspace.slug, pk=contract.pk)),
            guest_client.post(
                HOUR_TYPES_URL.format(slug=workspace.slug), {"name": "X", "multiplier": "1.00"}, format="json"
            ),
            guest_client.patch(
                HOUR_TYPE_URL.format(slug=workspace.slug, pk=business.pk), {"multiplier": "1.00"}, format="json"
            ),
            guest_client.delete(HOUR_TYPE_URL.format(slug=workspace.slug, pk=business.pk)),
        ]

        assert [response.status_code for response in forbidden_requests] == [status.HTTP_403_FORBIDDEN] * 7

    def test_a_user_outside_the_workspace_is_refused(self, workspace, hour_types):
        outsider = _authenticated_client(_make_user("outsider"))

        response = outsider.get(HOUR_TYPES_URL.format(slug=workspace.slug))

        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
class TestCatalogListing:
    def test_options_are_listed_in_display_order(self, admin_client, workspace, hour_types):
        business, after_hours, weekend = hour_types

        response = admin_client.get(HOUR_TYPES_URL.format(slug=workspace.slug))

        assert [option["name"] for option in response.data] == [
            "Horário comercial",
            "Fora do expediente",
            "Domingos e feriados",
        ]

    def test_reordering_changes_the_listing_order(self, admin_client, workspace, hour_types):
        """Acceptance criterion 2, API half. The client computes a midpoint and
        PATCHes the moved row only, which is the house pattern for drag and drop."""
        business, after_hours, weekend = hour_types
        midpoint = (business.sequence + after_hours.sequence) / 2

        response = admin_client.patch(
            HOUR_TYPE_URL.format(slug=workspace.slug, pk=weekend.pk), {"sequence": midpoint}, format="json"
        )
        assert response.status_code == status.HTTP_200_OK

        listing = admin_client.get(HOUR_TYPES_URL.format(slug=workspace.slug))

        assert [option["name"] for option in listing.data] == [
            "Horário comercial",
            "Domingos e feriados",
            "Fora do expediente",
        ]

    def test_only_active_hides_deactivated_options(self, admin_client, workspace, hour_types):
        """Acceptance criterion 3, API half: an inactive option must not reach the work
        log form, while the admin panel still needs to see it to reactivate it."""
        _, after_hours, _ = hour_types
        admin_client.patch(
            HOUR_TYPE_URL.format(slug=workspace.slug, pk=after_hours.pk), {"is_active": False}, format="json"
        )

        full_listing = admin_client.get(HOUR_TYPES_URL.format(slug=workspace.slug))
        form_listing = admin_client.get(HOUR_TYPES_URL.format(slug=workspace.slug) + "?only_active=true")

        assert "Fora do expediente" in [option["name"] for option in full_listing.data]
        assert "Fora do expediente" not in [option["name"] for option in form_listing.data]


@pytest.mark.django_db
class TestDefaultInvariant:
    """Acceptance criterion 6: exactly one default per catalogue at any moment."""

    def test_the_first_option_is_the_default(self, admin_client, workspace):
        response = admin_client.post(
            HOUR_TYPES_URL.format(slug=workspace.slug), {"name": "Primeiro", "multiplier": "1.00"}, format="json"
        )

        assert response.data["is_default"] is True

    def test_mark_default_swaps_the_incumbent(self, admin_client, workspace, hour_types):
        business, after_hours, _ = hour_types

        response = admin_client.post(HOUR_TYPE_MARK_DEFAULT_URL.format(slug=workspace.slug, pk=after_hours.pk))

        assert response.status_code == status.HTTP_200_OK
        business.refresh_from_db()
        after_hours.refresh_from_db()
        assert business.is_default is False
        assert after_hours.is_default is True
        assert ServiceHourType.objects.filter(workspace=workspace, is_default=True).count() == 1

    def test_unsetting_the_default_is_refused(self, admin_client, workspace, hour_types):
        business, _, _ = hour_types

        response = admin_client.patch(
            HOUR_TYPE_URL.format(slug=workspace.slug, pk=business.pk), {"is_default": False}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == ["DEFAULT_CANNOT_BE_UNSET"]
        business.refresh_from_db()
        assert business.is_default is True

    def test_deactivating_the_default_is_refused(self, admin_client, workspace, hour_types):
        business, _, _ = hour_types

        response = admin_client.patch(
            HOUR_TYPE_URL.format(slug=workspace.slug, pk=business.pk), {"is_active": False}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == ["DEFAULT_CANNOT_BE_DEACTIVATED"]

    def test_deleting_the_default_is_refused(self, admin_client, workspace, hour_types):
        business, _, _ = hour_types

        response = admin_client.delete(HOUR_TYPE_URL.format(slug=workspace.slug, pk=business.pk))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "DEFAULT_CANNOT_BE_DELETED"
        assert ServiceHourType.objects.filter(pk=business.pk).exists()

    def test_deleting_a_non_default_is_allowed(self, admin_client, workspace, hour_types):
        _, after_hours, _ = hour_types

        response = admin_client.delete(HOUR_TYPE_URL.format(slug=workspace.slug, pk=after_hours.pk))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not ServiceHourType.objects.filter(pk=after_hours.pk).exists()

    def test_an_inactive_option_cannot_be_promoted(self, admin_client, workspace, hour_types):
        _, after_hours, _ = hour_types
        ServiceHourType.objects.filter(pk=after_hours.pk).update(is_active=False)

        response = admin_client.post(HOUR_TYPE_MARK_DEFAULT_URL.format(slug=workspace.slug, pk=after_hours.pk))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "INACTIVE_OPTION_CANNOT_BE_DEFAULT"

    def test_the_two_catalogues_have_independent_defaults(self, admin_client, workspace, hour_types, billing_types):
        assert ServiceHourType.objects.filter(workspace=workspace, is_default=True).count() == 1
        assert ServiceBillingType.objects.filter(workspace=workspace, is_default=True).count() == 1


@pytest.mark.django_db
class TestCatalogValidation:
    def test_a_duplicate_name_is_rejected_with_a_usable_code(self, admin_client, workspace, hour_types):
        response = admin_client.post(
            HOUR_TYPES_URL.format(slug=workspace.slug),
            {"name": "Horário comercial", "multiplier": "1.00"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["name"] == ["NAME_ALREADY_EXISTS"]

    def test_a_duplicate_name_is_matched_case_insensitively(self, admin_client, workspace, hour_types):
        response = admin_client.post(
            HOUR_TYPES_URL.format(slug=workspace.slug),
            {"name": "horário COMERCIAL", "multiplier": "1.00"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_blank_name_is_rejected(self, admin_client, workspace):
        response = admin_client.post(
            HOUR_TYPES_URL.format(slug=workspace.slug), {"name": "   ", "multiplier": "1.00"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_zero_multiplier_is_rejected(self, admin_client, workspace):
        """There has to be exactly one mechanism for "do not charge", and it is the
        non-billable billing route."""
        response = admin_client.post(
            HOUR_TYPES_URL.format(slug=workspace.slug), {"name": "Gratis", "multiplier": "0.00"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "multiplier" in response.data

    def test_a_multiplier_below_one_is_accepted(self, admin_client, workspace):
        """Travel time at half rate is normal practice."""
        response = admin_client.post(
            HOUR_TYPES_URL.format(slug=workspace.slug), {"name": "Deslocamento", "multiplier": "0.50"}, format="json"
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["multiplier"] == "0.50"

    def test_a_multiplier_with_too_many_decimals_is_rejected(self, admin_client, workspace):
        """Scale is fixed by section 4b of the master context. A third decimal place
        here would make the four decimal places of hour quantities insufficient."""
        response = admin_client.post(
            HOUR_TYPES_URL.format(slug=workspace.slug), {"name": "Fino", "multiplier": "1.001"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_unknown_billing_route_is_rejected(self, admin_client, workspace):
        response = admin_client.post(
            BILLING_TYPES_URL.format(slug=workspace.slug),
            {"name": "Estranho", "billing_route": "something_else"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_the_workspace_cannot_be_set_from_the_payload(self, admin_client, workspace):
        other_workspace = Workspace.objects.create(
            name="Other", slug="other-workspace", owner=_make_user("other-owner")
        )

        response = admin_client.post(
            HOUR_TYPES_URL.format(slug=workspace.slug),
            {"name": "Tentativa", "multiplier": "1.00", "workspace": str(other_workspace.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert ServiceHourType.objects.get(name="Tentativa").workspace_id == workspace.id


@pytest.mark.django_db
class TestWorkspaceIsolation:
    def test_an_option_of_another_workspace_is_not_reachable(self, admin_client, workspace, hour_types):
        other_workspace = Workspace.objects.create(
            name="Other", slug="other-workspace", owner=_make_user("other-owner")
        )
        foreign_option = ServiceHourType.objects.create(workspace=other_workspace, name="Alheio")

        retrieve = admin_client.get(HOUR_TYPE_URL.format(slug=workspace.slug, pk=foreign_option.pk))
        patch_response = admin_client.patch(
            HOUR_TYPE_URL.format(slug=workspace.slug, pk=foreign_option.pk), {"multiplier": "9.99"}, format="json"
        )
        delete = admin_client.delete(HOUR_TYPE_URL.format(slug=workspace.slug, pk=foreign_option.pk))

        assert retrieve.status_code == status.HTTP_404_NOT_FOUND
        assert patch_response.status_code == status.HTTP_404_NOT_FOUND
        assert delete.status_code == status.HTTP_404_NOT_FOUND

    def test_the_listing_never_crosses_a_workspace_boundary(self, admin_client, workspace, hour_types):
        other_workspace = Workspace.objects.create(
            name="Other", slug="other-workspace", owner=_make_user("other-owner")
        )
        ServiceHourType.objects.create(workspace=other_workspace, name="Alheio")

        response = admin_client.get(HOUR_TYPES_URL.format(slug=workspace.slug))

        assert "Alheio" not in [option["name"] for option in response.data]


@pytest.mark.django_db
class TestServiceClientDefaultBillingType:
    """Acceptance criterion 7, API half, and decision D7.

    The client's default overrides the catalogue's. Replaces the two-value
    default_billing_mode enum that the client entity carried before this phase.
    """

    def test_a_client_can_point_at_a_catalogue_option(self, admin_client, workspace, billing_types):
        _, ad_hoc, _, _ = billing_types
        service_client = ServiceClient.objects.create(workspace=workspace, name="Terlogs")

        response = admin_client.patch(
            SERVICE_CLIENT_URL.format(slug=workspace.slug, pk=service_client.pk),
            {"default_billing_type": str(ad_hoc.pk)},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        service_client.refresh_from_db()
        assert service_client.default_billing_type_id == ad_hoc.pk

    def test_the_legacy_billing_mode_field_is_gone(self, admin_client, workspace, billing_types):
        """Two sources of truth for one decision is the class of bug this whole
        specification is built to avoid, so the enum was removed rather than
        deprecated."""
        service_client = ServiceClient.objects.create(workspace=workspace, name="Marubeni")

        response = admin_client.get(SERVICE_CLIENT_URL.format(slug=workspace.slug, pk=service_client.pk))

        assert "default_billing_mode" not in response.data
        assert "default_billing_type" in response.data

    def test_a_billing_type_from_another_workspace_is_rejected(self, admin_client, workspace):
        other_workspace = Workspace.objects.create(
            name="Other", slug="other-workspace", owner=_make_user("other-owner")
        )
        foreign_type = ServiceBillingType.objects.create(workspace=other_workspace, name="Contrato")
        service_client = ServiceClient.objects.create(workspace=workspace, name="Terlogs")

        response = admin_client.patch(
            SERVICE_CLIENT_URL.format(slug=workspace.slug, pk=service_client.pk),
            {"default_billing_type": str(foreign_type.pk)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_inactive_billing_type_is_rejected(self, admin_client, workspace, billing_types):
        """Accepting it would set a default that never takes effect, because the
        resolver falls back when the override is inactive."""
        _, ad_hoc, _, _ = billing_types
        ServiceBillingType.objects.filter(pk=ad_hoc.pk).update(is_active=False)
        service_client = ServiceClient.objects.create(workspace=workspace, name="Terlogs")

        response = admin_client.patch(
            SERVICE_CLIENT_URL.format(slug=workspace.slug, pk=service_client.pk),
            {"default_billing_type": str(ad_hoc.pk)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["default_billing_type"] == ["BILLING_TYPE_IS_INACTIVE"]


@pytest.mark.django_db
class TestConfigActivityEndpointSecurity:
    """SECURITY: the trail holds multiplier history now and price history from the
    pricing phase on. It is commercial intelligence.

    Written in this phase rather than left for the client portal phase to discover.
    This endpoint is also listed in that phase's criterion about new endpoints not
    leaking client data.
    """

    def test_guest_is_refused(self, guest_client, workspace):
        response = guest_client.get(ACTIVITIES_URL.format(slug=workspace.slug))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_member_is_refused(self, member_client, workspace):
        """A technician reads the catalogues but has no business reading the price
        history."""
        response = member_client.get(ACTIVITIES_URL.format(slug=workspace.slug))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_user_outside_the_workspace_is_refused(self, workspace):
        outsider = _authenticated_client(_make_user("outsider"))

        response = outsider.get(ACTIVITIES_URL.format(slug=workspace.slug))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_is_allowed(self, admin_client, workspace):
        response = admin_client.get(ACTIVITIES_URL.format(slug=workspace.slug))

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("method", ["post", "patch", "put", "delete"])
    def test_the_trail_is_append_only_over_http(self, admin_client, workspace, method):
        """No write verb is routed. A trail that can be rewritten is not a trail."""
        response = getattr(admin_client, method)(ACTIVITIES_URL.format(slug=workspace.slug))

        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED


@pytest.mark.django_db
class TestConfigActivityEndpointContent:
    def test_a_multiplier_change_through_the_api_is_recorded(self, admin_client, admin_user, workspace, hour_types):
        _, after_hours, _ = hour_types

        admin_client.patch(
            HOUR_TYPE_URL.format(slug=workspace.slug, pk=after_hours.pk), {"multiplier": "1.80"}, format="json"
        )

        response = admin_client.get(
            ACTIVITIES_URL.format(slug=workspace.slug) + f"?entity_identifier={after_hours.pk}"
        )
        rows = response.data["results"]

        assert len(rows) == 1
        assert rows[0]["field_name"] == "multiplier"
        assert rows[0]["old_value"] == "1.50"
        assert rows[0]["new_value"] == "1.80"
        assert rows[0]["entity_name"] == "service_hour_type"

    def test_the_actor_is_expanded_with_a_name_and_an_email(self, admin_client, admin_user, workspace, hour_types):
        """Whoever reads this is answering a client who disputed an invoice. Needing a
        second request to find out who made the change would defeat the purpose."""
        _, after_hours, _ = hour_types
        admin_client.patch(
            HOUR_TYPE_URL.format(slug=workspace.slug, pk=after_hours.pk), {"multiplier": "1.80"}, format="json"
        )

        response = admin_client.get(ACTIVITIES_URL.format(slug=workspace.slug))
        actor_detail = response.data["results"][0]["actor_detail"]

        assert actor_detail["email"] == admin_user.email
        assert actor_detail["display_name"] == admin_user.display_name

    def test_the_trail_can_be_filtered_by_entity_name(self, admin_client, workspace, hour_types, billing_types):
        _, after_hours, _ = hour_types
        _, ad_hoc, _, _ = billing_types

        admin_client.patch(
            HOUR_TYPE_URL.format(slug=workspace.slug, pk=after_hours.pk), {"multiplier": "1.80"}, format="json"
        )
        admin_client.patch(
            BILLING_TYPE_URL.format(slug=workspace.slug, pk=ad_hoc.pk),
            {"billing_route": "non_billable"},
            format="json",
        )

        hour_rows = admin_client.get(
            ACTIVITIES_URL.format(slug=workspace.slug) + "?entity_name=service_hour_type"
        ).data["results"]
        billing_rows = admin_client.get(
            ACTIVITIES_URL.format(slug=workspace.slug) + "?entity_name=service_billing_type"
        ).data["results"]

        assert [row["field_name"] for row in hour_rows] == ["multiplier"]
        assert [row["field_name"] for row in billing_rows] == ["billing_route"]

    def test_a_malformed_date_filter_is_a_400_not_a_500(self, admin_client, workspace):
        response = admin_client.get(ACTIVITIES_URL.format(slug=workspace.slug) + "?created_at__gte=not-a-date")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "INVALID_DATE"

    def test_a_date_filter_narrows_the_result(self, admin_client, workspace, hour_types):
        _, after_hours, _ = hour_types
        admin_client.patch(
            HOUR_TYPE_URL.format(slug=workspace.slug, pk=after_hours.pk), {"multiplier": "1.80"}, format="json"
        )

        included = admin_client.get(ACTIVITIES_URL.format(slug=workspace.slug) + "?created_at__gte=2000-01-01")
        excluded = admin_client.get(ACTIVITIES_URL.format(slug=workspace.slug) + "?created_at__gte=2999-01-01")

        assert len(included.data["results"]) == 1
        assert excluded.data["results"] == []

    def test_a_bare_upper_bound_date_includes_that_whole_day(self, admin_client, workspace, hour_types):
        """`?created_at__lte=<today>` has to include today's changes. Taken literally a
        bare date means midnight, which would silently exclude everything on the day
        the reader is actually asking about."""
        _, after_hours, _ = hour_types
        admin_client.patch(
            HOUR_TYPE_URL.format(slug=workspace.slug, pk=after_hours.pk), {"multiplier": "1.80"}, format="json"
        )
        today = timezone.localdate().isoformat()

        response = admin_client.get(ACTIVITIES_URL.format(slug=workspace.slug) + f"?created_at__lte={today}")

        assert len(response.data["results"]) == 1

    def test_a_full_timestamp_is_also_accepted(self, admin_client, workspace, hour_types):
        _, after_hours, _ = hour_types
        admin_client.patch(
            HOUR_TYPE_URL.format(slug=workspace.slug, pk=after_hours.pk), {"multiplier": "1.80"}, format="json"
        )

        response = admin_client.get(
            ACTIVITIES_URL.format(slug=workspace.slug) + "?created_at__gte=2000-01-01T00:00:00Z"
        )

        assert len(response.data["results"]) == 1

    def test_the_trail_never_crosses_a_workspace_boundary(self, admin_client, admin_user, workspace, hour_types):
        other_workspace = Workspace.objects.create(
            name="Other", slug="other-workspace", owner=_make_user("other-owner")
        )
        ServiceConfigActivity.objects.create(
            workspace=other_workspace,
            entity_name="service_hour_type",
            entity_identifier=uuid4(),
            field_name="multiplier",
            old_value="1.00",
            new_value="7.77",
            actor=admin_user,
        )

        response = admin_client.get(ACTIVITIES_URL.format(slug=workspace.slug))

        assert "7.77" not in [row["new_value"] for row in response.data["results"]]

    def test_renaming_an_option_is_not_an_audit_event(self, admin_client, workspace, hour_types):
        """Only the financially meaningful fields are tracked."""
        _, after_hours, _ = hour_types

        admin_client.patch(
            HOUR_TYPE_URL.format(slug=workspace.slug, pk=after_hours.pk),
            {"name": "Fora do expediente renomeado", "description": "nova descrição"},
            format="json",
        )

        response = admin_client.get(ACTIVITIES_URL.format(slug=workspace.slug))

        assert response.data["results"] == []
