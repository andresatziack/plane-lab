# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import factory
from uuid import uuid4
from django.utils import timezone

from decimal import Decimal

from plane.db.models import (
    Issue,
    User,
    Workspace,
    WorkspaceMember,
    Project,
    ProjectMember,
    ServiceBillingType,
    ServiceClient,
    ServiceHourType,
    ServiceLog,
    State,
)


class UserFactory(factory.django.DjangoModelFactory):
    """Factory for creating User instances"""

    class Meta:
        model = User
        django_get_or_create = ("email",)

    id = factory.LazyFunction(uuid4)
    email = factory.Sequence(lambda n: f"user{n}@plane.so")
    # User.username is unique and this factory used to leave it at "", so the second
    # user built in any test collided on users_username_key. That is why the earlier
    # contract tests hand-rolled their own user helpers instead of using this factory.
    username = factory.Sequence(lambda n: f"user{n}")
    password = factory.PostGenerationMethodCall("set_password", "password")
    first_name = factory.Sequence(lambda n: f"First{n}")
    last_name = factory.Sequence(lambda n: f"Last{n}")
    is_active = True
    is_superuser = False
    is_staff = False


class WorkspaceFactory(factory.django.DjangoModelFactory):
    """Factory for creating Workspace instances"""

    class Meta:
        model = Workspace
        django_get_or_create = ("slug",)

    id = factory.LazyFunction(uuid4)
    name = factory.Sequence(lambda n: f"Workspace {n}")
    slug = factory.Sequence(lambda n: f"workspace-{n}")
    owner = factory.SubFactory(UserFactory)
    created_at = factory.LazyFunction(timezone.now)
    updated_at = factory.LazyFunction(timezone.now)


class WorkspaceMemberFactory(factory.django.DjangoModelFactory):
    """Factory for creating WorkspaceMember instances"""

    class Meta:
        model = WorkspaceMember

    id = factory.LazyFunction(uuid4)
    workspace = factory.SubFactory(WorkspaceFactory)
    member = factory.SubFactory(UserFactory)
    role = 20  # Admin role by default
    created_at = factory.LazyFunction(timezone.now)
    updated_at = factory.LazyFunction(timezone.now)


class ProjectFactory(factory.django.DjangoModelFactory):
    """Factory for creating Project instances"""

    class Meta:
        model = Project
        django_get_or_create = ("name", "workspace")

    id = factory.LazyFunction(uuid4)
    name = factory.Sequence(lambda n: f"Project {n}")
    # Project has a unique (identifier, workspace) constraint for live rows, and the
    # column is not nullable. Without a distinct value here, the second project built
    # in one workspace collides on the empty string.
    identifier = factory.Sequence(lambda n: f"PRJ{n}")
    workspace = factory.SubFactory(WorkspaceFactory)
    created_by = factory.SelfAttribute("workspace.owner")
    updated_by = factory.SelfAttribute("workspace.owner")
    created_at = factory.LazyFunction(timezone.now)
    updated_at = factory.LazyFunction(timezone.now)


class ProjectMemberFactory(factory.django.DjangoModelFactory):
    """Factory for creating ProjectMember instances"""

    class Meta:
        model = ProjectMember

    id = factory.LazyFunction(uuid4)
    project = factory.SubFactory(ProjectFactory)
    member = factory.SubFactory(UserFactory)
    role = 20  # Admin role by default
    created_at = factory.LazyFunction(timezone.now)
    updated_at = factory.LazyFunction(timezone.now)



class ServiceClientFactory(factory.django.DjangoModelFactory):
    """Factory for creating ServiceClient instances"""

    class Meta:
        model = ServiceClient
        django_get_or_create = ("name", "workspace")

    id = factory.LazyFunction(uuid4)
    name = factory.Sequence(lambda n: f"Service Client {n}")
    trade_name = factory.Sequence(lambda n: f"Client {n}")
    # Left as None by default: tax_id is unique per workspace, so generating one
    # would make every extra client in a test collide unless the test cares.
    tax_id = None
    is_active = True
    workspace = factory.SubFactory(WorkspaceFactory)
    created_at = factory.LazyFunction(timezone.now)
    updated_at = factory.LazyFunction(timezone.now)



class ServiceHourTypeFactory(factory.django.DjangoModelFactory):
    """Factory for creating ServiceHourType instances.

    Note that the model forces the first live option of a workspace to be the
    default, so the first instance built for a given workspace will come back with
    is_default=True whatever this factory passes.
    """

    class Meta:
        model = ServiceHourType
        django_get_or_create = ("name", "workspace")

    id = factory.LazyFunction(uuid4)
    name = factory.Sequence(lambda n: f"Hour Type {n}")
    description = ""
    multiplier = Decimal("1.00")
    color = "#60646C"
    is_active = True
    is_default = False
    workspace = factory.SubFactory(WorkspaceFactory)
    created_at = factory.LazyFunction(timezone.now)
    updated_at = factory.LazyFunction(timezone.now)


class ServiceBillingTypeFactory(factory.django.DjangoModelFactory):
    """Factory for creating ServiceBillingType instances."""

    class Meta:
        model = ServiceBillingType
        django_get_or_create = ("name", "workspace")

    id = factory.LazyFunction(uuid4)
    name = factory.Sequence(lambda n: f"Billing Type {n}")
    description = ""
    billing_route = ServiceBillingType.BillingRoute.DEBIT_POOL
    is_active = True
    is_default = False
    workspace = factory.SubFactory(WorkspaceFactory)
    created_at = factory.LazyFunction(timezone.now)
    updated_at = factory.LazyFunction(timezone.now)



class StateFactory(factory.django.DjangoModelFactory):
    """Factory for creating State instances.

    Only needed so that IssueFactory has a state to point at. ``Issue.save()`` calls
    ``_ensure_default_state()``, which looks for the project's default state and
    leaves the field null when the project has none -- and a null state makes
    ``sort_order`` grouping and most issue queries behave oddly. Creating one
    explicitly keeps work log tests focused on work logs.
    """

    class Meta:
        model = State
        django_get_or_create = ("name", "project")

    id = factory.LazyFunction(uuid4)
    name = "Backlog"
    project = factory.SubFactory(ProjectFactory)
    workspace = factory.SelfAttribute("project.workspace")
    group = "backlog"
    default = True
    created_at = factory.LazyFunction(timezone.now)
    updated_at = factory.LazyFunction(timezone.now)


class IssueFactory(factory.django.DjangoModelFactory):
    """Factory for creating Issue instances.

    ``sequence_id`` and ``sort_order`` are set by ``Issue.save()`` under a per-project
    advisory lock, so they are deliberately not specified here.
    """

    class Meta:
        model = Issue

    id = factory.LazyFunction(uuid4)
    name = factory.Sequence(lambda n: f"Work item {n}")
    project = factory.SubFactory(ProjectFactory)
    workspace = factory.SelfAttribute("project.workspace")
    state = factory.SubFactory(StateFactory, project=factory.SelfAttribute("..project"))
    created_at = factory.LazyFunction(timezone.now)
    updated_at = factory.LazyFunction(timezone.now)


class ServiceLogFactory(factory.django.DjangoModelFactory):
    """Factory for creating ServiceLog instances.

    The three hour quantities and the two R4 snapshots default to a coherent set for
    one billable hour at multiplier 1.00, so a test that does not care about the
    arithmetic gets a valid row. A test that *does* care should go through
    ``plane.utils.service_log.build_batch_rows`` instead, which is the code the API
    uses -- the check constraint tying ``debited_hours`` to the billing route will
    reject an incoherent combination passed here, which is the point of it.
    """

    class Meta:
        model = ServiceLog

    id = factory.LazyFunction(uuid4)
    issue = factory.SubFactory(IssueFactory)
    project = factory.SelfAttribute("issue.project")
    workspace = factory.SelfAttribute("issue.workspace")
    author = factory.SubFactory(UserFactory)
    worked_on = factory.LazyFunction(lambda: timezone.now().date())
    description = "Worked on the thing"
    entry_mode = ServiceLog.EntryMode.DURATION
    start_time = None
    end_time = None
    source = ServiceLog.Source.MANUAL
    raw_duration_minutes = 60
    logged_hours = Decimal("1.0000")
    equivalent_hours = Decimal("1.0000")
    debited_hours = Decimal("1.0000")
    applied_multiplier = Decimal("1.00")
    applied_billing_route = ServiceBillingType.BillingRoute.DEBIT_POOL
    hour_type = factory.SubFactory(ServiceHourTypeFactory, workspace=factory.SelfAttribute("..issue.workspace"))
    billing_type = factory.SubFactory(ServiceBillingTypeFactory, workspace=factory.SelfAttribute("..issue.workspace"))
    suggested_hour_type = None
    is_hour_type_overridden = False
    classification_reason = ""
    batch_id = factory.LazyFunction(uuid4)
    segment_index = 0
    created_at = factory.LazyFunction(timezone.now)
    updated_at = factory.LazyFunction(timezone.now)
