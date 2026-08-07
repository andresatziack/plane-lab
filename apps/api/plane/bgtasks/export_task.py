# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import io
import zipfile
from typing import List
import boto3
from botocore.client import Config
from uuid import UUID

# Third party imports
from celery import shared_task

# Django imports
from django.conf import settings
from django.utils import timezone
from django.db.models import Prefetch

# Module imports
from plane.db.models import (
    ExporterHistory,
    Issue,
    IssueComment,
    IssueRelation,
    IssueSubscriber,
    ServiceLog,
)
from plane.utils.exception_logger import log_exception
from plane.utils.exporters.exporter import Exporter
from plane.utils.exporters.schemas import ServiceLogExportSchema
from plane.utils.porters.exporter import DataExporter
from plane.utils.porters.serializers.issue import IssueExportSerializer
from plane.utils.service_reports_filters import (
    ServiceLogFilterSet,
    format_competence,
)


def create_zip_file(files: List[tuple[str, str | bytes]]) -> io.BytesIO:
    """
    Create a ZIP file from the provided files.
    """
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for filename, file_content in files:
            zipf.writestr(filename, file_content)

    zip_buffer.seek(0)
    return zip_buffer


# TODO: Change the upload_to_s3 function to use the new storage method with entry in file asset table
def upload_to_s3(zip_file: io.BytesIO, workspace_id: UUID, token_id: str, slug: str) -> None:
    """
    Upload a ZIP file to S3 and generate a presigned URL.
    """
    file_name = f"{workspace_id}/export-{slug}-{token_id[:6]}-{str(timezone.now().date())}.zip"
    expires_in = 7 * 24 * 60 * 60

    if settings.USE_MINIO:
        upload_s3 = boto3.client(
            "s3",
            endpoint_url=settings.AWS_S3_ENDPOINT_URL,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4"),
        )
        upload_s3.upload_fileobj(
            zip_file,
            settings.AWS_STORAGE_BUCKET_NAME,
            file_name,
            ExtraArgs={"ACL": "public-read", "ContentType": "application/zip"},
        )

        # Generate presigned url for the uploaded file with different base
        presign_s3 = boto3.client(
            "s3",
            endpoint_url=(
                f"{settings.AWS_S3_URL_PROTOCOL}//{str(settings.AWS_S3_CUSTOM_DOMAIN).replace('/uploads', '')}/"
            ),
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4"),
        )

        presigned_url = presign_s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.AWS_STORAGE_BUCKET_NAME, "Key": file_name},
            ExpiresIn=expires_in,
        )
    else:
        # If endpoint url is present, use it
        if settings.AWS_S3_ENDPOINT_URL:
            s3 = boto3.client(
                "s3",
                endpoint_url=settings.AWS_S3_ENDPOINT_URL,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                config=Config(signature_version="s3v4"),
            )
        else:
            s3 = boto3.client(
                "s3",
                region_name=settings.AWS_REGION,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                config=Config(signature_version="s3v4"),
            )

        # Upload the file to S3
        s3.upload_fileobj(
            zip_file,
            settings.AWS_STORAGE_BUCKET_NAME,
            file_name,
            ExtraArgs={"ContentType": "application/zip"},
        )

        # Generate presigned url for the uploaded file
        presigned_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.AWS_STORAGE_BUCKET_NAME, "Key": file_name},
            ExpiresIn=expires_in,
        )

    exporter_instance = ExporterHistory.objects.get(token=token_id)

    # Update the exporter instance with the presigned url
    if presigned_url:
        exporter_instance.url = presigned_url
        exporter_instance.status = "completed"
        exporter_instance.key = file_name
    else:
        exporter_instance.status = "failed"

    exporter_instance.save(update_fields=["status", "url", "key"])


@shared_task
def issue_export_task(
    provider: str,
    workspace_id: UUID,
    project_ids: List[str],
    token_id: str,
    multiple: bool,
    slug: str,
):
    """
    Export issues from the workspace.
    provider (str): The provider to export the issues to csv | json | xlsx.
    token_id (str): The export object token id.
    multiple (bool): Whether to export the issues to multiple files per project.
    """
    try:
        exporter_instance = ExporterHistory.objects.get(token=token_id)
        exporter_instance.status = "processing"
        exporter_instance.save(update_fields=["status"])

        # Build base queryset for issues
        workspace_issues = (
            Issue.objects.filter(
                workspace__id=workspace_id,
                project_id__in=project_ids,
                project__project_projectmember__member=exporter_instance.initiated_by_id,
                project__project_projectmember__is_active=True,
                project__archived_at__isnull=True,
            )
            .select_related(
                "project",
                "workspace",
                "state",
                "created_by",
                "estimate_point",
            )
            .prefetch_related(
                "labels",
                "issue_cycle__cycle",
                "issue_module__module",
                "assignees",
                "issue_link",
                Prefetch(
                    "issue_subscribers",
                    queryset=IssueSubscriber.objects.select_related("subscriber"),
                ),
                Prefetch(
                    "issue_comments",
                    queryset=IssueComment.objects.select_related("actor").order_by("created_at"),
                ),
                Prefetch(
                    "issue_relation",
                    queryset=IssueRelation.objects.select_related("related_issue", "related_issue__project"),
                ),
                Prefetch(
                    "issue_related",
                    queryset=IssueRelation.objects.select_related("issue", "issue__project"),
                ),
                Prefetch(
                    "parent",
                    queryset=Issue.objects.select_related("type", "project"),
                ),
            )
        )

        # Create exporter for the specified format
        try:
            exporter = DataExporter(IssueExportSerializer, format_type=provider)
        except ValueError as e:
            # Invalid format type
            exporter_instance = ExporterHistory.objects.get(token=token_id)
            exporter_instance.status = "failed"
            exporter_instance.reason = str(e)
            exporter_instance.save(update_fields=["status", "reason"])
            return

        files = []
        if multiple:
            # Export each project separately with its own queryset
            for project_id in project_ids:
                project_issues = workspace_issues.filter(project_id=project_id)
                export_filename = f"{slug}-{project_id}"
                filename, content = exporter.export(export_filename, project_issues)
                files.append((filename, content))
        else:
            # Export all issues in a single file
            export_filename = f"{slug}-{workspace_id}"
            filename, content = exporter.export(export_filename, workspace_issues)
            files.append((filename, content))

        zip_buffer = create_zip_file(files)
        upload_to_s3(zip_buffer, workspace_id, token_id, slug)

    except Exception as e:
        exporter_instance = ExporterHistory.objects.get(token=token_id)
        exporter_instance.status = "failed"
        exporter_instance.reason = str(e)
        exporter_instance.save(update_fields=["status", "reason"])
        log_exception(e)
        return



@shared_task
def service_log_export_task(
    provider: str,
    workspace_id: UUID,
    project_ids: List[str],
    token_id: str,
    slug: str,
    filters: dict = None,
):
    """Export work logs with their hours, their value in reais and their reasons.

    Fills the ``("issue_worklogs", "Issue Worklogs")`` choice that has existed on
    ``ExporterHistory`` with no handler behind it. **Nothing new is built**: the queue, the
    history row, the status transitions, ``create_zip_file``, ``upload_to_s3``, the
    presigned URL and ``exporter_expired_task``'s eight-day purge all come from the
    pipeline ``issue_export_task`` already uses. Only the queryset and the schema differ.

    ``filters`` carries the selection, persisted on ``ExporterHistory.filters`` -- a column
    that has been on the model unused since before this feature. A monthly billing
    consolidation is always *of* something, and putting the selection on the row rather than
    only in the task arguments is what lets the history say which month a finished file
    covers.

    **Phase 9 made that selection a full ``ServiceLogFilterSet``, and that is what closes
    acceptance criterion 9.** "O CSV contém os mesmos números da tela" was previously a
    comparison somebody had to do by hand, because the screen could filter by technician,
    project, hour type and route while the export only understood a competency and a client --
    so the two legitimately disagreed. Now both consume the same descriptor and there is
    nothing left to reconcile. Phase 6's ``{year, month, service_client_id}`` rows are
    translated by ``from_export_filters`` rather than handled separately, so there is one
    filtering path and an old history row cannot quietly export everything.

    **The membership re-check inside the task is deliberate and is copied from
    ``issue_export_task``.** A queued task can run long after the request that queued it,
    by which time the initiator may have lost access to a project -- so the export is built
    against the projects they can see *now*, not the ones they could see then. This carries
    money, which makes that gap worth closing rather than noting.
    """
    try:
        exporter_instance = ExporterHistory.objects.get(token=token_id)
        exporter_instance.status = "processing"
        exporter_instance.save(update_fields=["status"])

        service_logs = (
            ServiceLog.objects.filter(
                workspace__id=workspace_id,
                project_id__in=project_ids,
                project__project_projectmember__member=exporter_instance.initiated_by_id,
                project__project_projectmember__is_active=True,
                project__archived_at__isnull=True,
            )
            .select_related(
                "project",
                "project__service_client",
                "issue",
                "author",
            )
            .order_by("project__name", "worked_on", "batch_id", "segment_index")
            .distinct()
        )

        # `hour_type` and `billing_type` are DO_NOTHING foreign keys that may point at a soft
        # deleted catalogue option, so they are NOT select_related: the forward descriptor
        # resolves through the filtered manager and would raise `DoesNotExist` on exactly the
        # historical rows an export exists to preserve. The schema reads them through
        # `all_objects` instead.
        filters = filters or {}
        filterset = ServiceLogFilterSet.from_export_filters(filters)

        # The membership and archive constraints above stay outside the descriptor: they are
        # about who may see what, and a descriptor arrives from a browser. The descriptor
        # narrows what was asked for, within what is allowed.
        service_logs = filterset.queryset(workspace_id, queryset=service_logs)

        try:
            exporter = Exporter(format_type=provider, schema_class=ServiceLogExportSchema)
        except ValueError as e:
            exporter_instance = ExporterHistory.objects.get(token=token_id)
            exporter_instance.status = "failed"
            exporter_instance.reason = str(e)
            exporter_instance.save(update_fields=["status", "reason"])
            return

        # Named after the competency when there is a single one, which is the common case and
        # the one somebody files next to an invoice. A range or an open selection gets no
        # suffix rather than a misleading one.
        competence = (
            f"-{format_competence(filterset.competence_from)}"
            if filterset.competence_from is not None
            and filterset.competence_from == filterset.competence_to
            else ""
        )
        filename, content = exporter.export(f"{slug}-apontamentos{competence}", service_logs)

        zip_buffer = create_zip_file([(filename, content)])
        upload_to_s3(zip_buffer, workspace_id, token_id, slug)

    except Exception as e:
        exporter_instance = ExporterHistory.objects.get(token=token_id)
        exporter_instance.status = "failed"
        exporter_instance.reason = str(e)
        exporter_instance.save(update_fields=["status", "reason"])
        log_exception(e)
        return
