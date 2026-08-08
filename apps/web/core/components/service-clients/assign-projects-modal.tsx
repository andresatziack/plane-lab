/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo, useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IServiceClient } from "@plane/types";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useServiceClient } from "@/hooks/store/use-service-client";
// components
import { ServiceClientFlagsFields } from "./service-client-flags-fields";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  workspaceSlug: string;
  serviceClient: IServiceClient;
};

/**
 * Point several projects at one client company in a single act. Phase 8b, criterion 16.
 *
 * The one-way path for this existed since Phase 1 -- `assignProjects` on the service and the
 * store -- with nothing calling it, so the only way to attach twenty projects to a Cliente was to
 * open twenty project settings pages.
 *
 * **All or nothing, and that is the server's rule rather than a choice made here.** A project that
 * already has work logs under a *different* client is refused, because reassigning it would move
 * invoiced hours to another company's account; one such project rejects the whole batch. The
 * failure is surfaced as it arrives -- there is no partial success to report, and pretending
 * otherwise would leave an Admin guessing which half went through.
 *
 * The two flags are **suggested and never imposed**, the same as the per-project path, and reuse
 * its field component so the copy cannot drift. Unchecked means "leave as it is": neither is ever
 * sent as `false`, so a bulk assignment cannot silently disable time tracking on a project that
 * already had it.
 */
export const AssignProjectsModal = observer(function AssignProjectsModal(props: Props) {
  const { isOpen, onClose, workspaceSlug, serviceClient } = props;
  const { t } = useTranslation();
  // states
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [enableGuestViewAll, setEnableGuestViewAll] = useState(true);
  const [enableTimeTracking, setEnableTimeTracking] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // store hooks
  const { workspaceProjectIds, getProjectById } = useProject();
  const { assignProjects, getServiceClientNameById } = useServiceClient();

  const projects = useMemo(() => {
    const all = (workspaceProjectIds ?? []).map((id) => getProjectById(id)).filter(Boolean);
    const needle = search.trim().toLowerCase();

    return all
      .filter((project) => !needle || project?.name?.toLowerCase().includes(needle))
      .filter((project) => project?.service_client !== serviceClient.id);
  }, [workspaceProjectIds, getProjectById, search, serviceClient.id]);

  const handleClose = () => {
    setSelectedIds([]);
    setSearch("");
    setEnableGuestViewAll(true);
    setEnableTimeTracking(true);
    onClose();
  };

  const toggle = (projectId: string) => {
    setSelectedIds((current) =>
      current.includes(projectId) ? current.filter((id) => id !== projectId) : [...current, projectId]
    );
  };

  const handleSubmit = async () => {
    if (selectedIds.length === 0) return;

    setIsSubmitting(true);
    try {
      await assignProjects(workspaceSlug, serviceClient.id, {
        project_ids: selectedIds,
        // Only ever true. See the note on ServiceClientFlagsFields.
        ...(enableGuestViewAll ? { guest_view_all_features: true } : {}),
        ...(enableTimeTracking ? { is_time_tracking_enabled: true } : {}),
      });

      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("common.success"),
        message: t("workspace_settings.settings.service_clients.assign_projects.success", {
          count: selectedIds.length,
        }),
      });
      handleClose();
    } catch (error) {
      // Two shapes, both 400 and both all-or-nothing: projects outside the workspace, and
      // projects already carrying work logs under another client.
      const detail = error as { error?: string; projects?: unknown[] } | undefined;

      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message: detail?.projects
          ? t("workspace_settings.settings.service_clients.assign_projects.blocked")
          : t("workspace_settings.settings.service_clients.assign_projects.failed"),
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} position={EModalPosition.CENTER} width={EModalWidth.XXL}>
      <div className="space-y-4 p-5">
        <h3 className="text-lg font-medium text-primary">
          {t("workspace_settings.settings.service_clients.assign_projects.title", {
            name: serviceClient.trade_name || serviceClient.name,
          })}
        </h3>
        <p className="text-sm text-secondary">
          {t("workspace_settings.settings.service_clients.assign_projects.description")}
        </p>

        <input
          type="text"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={t("workspace_settings.settings.service_clients.assign_projects.search")}
          className="text-sm w-full rounded-md border border-subtle bg-surface-1 px-3 py-2 outline-none focus:border-strong"
        />

        <div className="max-h-64 overflow-y-auto rounded-md border border-subtle">
          {projects.length === 0 ? (
            <p className="text-sm p-4 text-tertiary">
              {t("workspace_settings.settings.service_clients.assign_projects.empty")}
            </p>
          ) : (
            <ul className="divide-y divide-subtle/60">
              {projects.map((project) => {
                if (!project) return null;

                const currentClientId = project.service_client ?? null;

                return (
                  <li key={project.id}>
                    <label
                      htmlFor={`assign-project-${project.id}`}
                      className="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-surface-2"
                    >
                      <input
                        id={`assign-project-${project.id}`}
                        type="checkbox"
                        checked={selectedIds.includes(project.id)}
                        onChange={() => toggle(project.id)}
                      />
                      <span className="text-sm flex-1 truncate text-primary">{project.name}</span>
                      {/* Where the project stands today, so a reassignment is never accidental.
                          A project with no client is internal work, which is not the same as
                          unset -- naming it is what keeps the distinction visible. */}
                      <span className="text-xs text-tertiary">
                        {currentClientId
                          ? t("workspace_settings.settings.service_clients.assign_projects.current_client", {
                              name: getServiceClientNameById(currentClientId),
                            })
                          : t("workspace_settings.settings.service_clients.assign_projects.internal")}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <ServiceClientFlagsFields
          guestViewAll={enableGuestViewAll}
          onGuestViewAllChange={setEnableGuestViewAll}
          timeTracking={enableTimeTracking}
          onTimeTrackingChange={setEnableTimeTracking}
          idPrefix="bulk-"
        />

        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-tertiary">
            {t("workspace_settings.settings.service_clients.assign_projects.selected", {
              count: selectedIds.length,
            })}
          </span>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={handleClose}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleSubmit}
              loading={isSubmitting}
              disabled={selectedIds.length === 0}
            >
              {t("workspace_settings.settings.service_clients.assign_projects.submit")}
            </Button>
          </div>
        </div>
      </div>
    </ModalCore>
  );
});
