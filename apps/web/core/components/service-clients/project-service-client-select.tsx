/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import useSWR from "swr";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { CustomSelect, EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useServiceClient } from "@/hooks/store/use-service-client";

type Props = {
  workspaceSlug: string;
  projectId: string;
  disabled?: boolean;
};

/**
 * Links a project to a client company.
 *
 * Two things are deliberate here:
 *
 * 1. When a client is being set and the project is not yet configured for client
 *    users, a confirmation step *suggests* enabling `guest_view_all_features` and
 *    `is_time_tracking_enabled`. The master context requires this to be
 *    suggested, not imposed silently: with guest_view_all_features off, a client
 *    user only sees the work items they themselves created, not those opened by
 *    their colleagues or by technicians on their behalf.
 * 2. Clearing the client is allowed here, but a later phase must reject it for
 *    projects that already have work logs. That guard does not exist yet; see
 *    docs/worklog/03-worklog-core.md.
 */
export const ProjectServiceClientSelect = observer(function ProjectServiceClientSelect(props: Props) {
  const { workspaceSlug, projectId, disabled = false } = props;
  // states
  const [pendingClientId, setPendingClientId] = useState<string | null>(null);
  const [isConfirmOpen, setIsConfirmOpen] = useState(false);
  const [enableGuestViewAll, setEnableGuestViewAll] = useState(true);
  const [enableTimeTracking, setEnableTimeTracking] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { getProjectById, updateProject } = useProject();
  const { activeServiceClients, fetchServiceClients, serviceClients } = useServiceClient();

  const project = getProjectById(projectId);

  // Members can read the client list, which is all this picker needs.
  useSWR(
    workspaceSlug ? `SERVICE_CLIENTS_LIST_${workspaceSlug}` : null,
    workspaceSlug ? () => fetchServiceClients(workspaceSlug) : null,
    { revalidateIfStale: false, revalidateOnFocus: false }
  );

  const selectedClientId = project?.service_client ?? null;
  const selectedClient = activeServiceClients.find((client) => client.id === selectedClientId);

  const persist = async (payload: Record<string, unknown>) => {
    setIsSubmitting(true);
    try {
      await updateProject(workspaceSlug, projectId, payload);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("common.success"),
        message: t("project_settings.service_client.toasts.updated"),
      });
    } catch (error) {
      const errorDetail = error as Record<string, string[] | string> | undefined;
      const firstError = errorDetail ? Object.values(errorDetail).flat().find(Boolean) : undefined;
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message: typeof firstError === "string" ? firstError : t("project_settings.service_client.toasts.failed"),
      });
    }
    setIsSubmitting(false);
  };

  const handleChange = async (value: string | null) => {
    if (value === selectedClientId) return;

    // Clearing the client, or a project already configured for client users:
    // nothing to suggest, apply straight away.
    const alreadyConfigured = project?.guest_view_all_features && project?.is_time_tracking_enabled;
    if (!value || alreadyConfigured) {
      await persist({ service_client: value });
      return;
    }

    setPendingClientId(value);
    setEnableGuestViewAll(!project?.guest_view_all_features);
    setEnableTimeTracking(!project?.is_time_tracking_enabled);
    setIsConfirmOpen(true);
  };

  const handleConfirm = async () => {
    const payload: Record<string, unknown> = { service_client: pendingClientId };
    if (enableGuestViewAll) payload.guest_view_all_features = true;
    if (enableTimeTracking) payload.is_time_tracking_enabled = true;

    await persist(payload);
    setIsConfirmOpen(false);
    setPendingClientId(null);
  };

  const pendingClientName = activeServiceClients.find((client) => client.id === pendingClientId)?.name ?? "";

  return (
    <>
      <ModalCore
        isOpen={isConfirmOpen}
        handleClose={() => setIsConfirmOpen(false)}
        position={EModalPosition.CENTER}
        width={EModalWidth.XL}
      >
        <div className="space-y-4 p-5">
          <h3 className="text-lg font-medium text-primary">{t("project_settings.service_client.confirm.title")}</h3>
          <p className="text-sm text-secondary">
            {t("project_settings.service_client.confirm.description", { name: pendingClientName })}
          </p>
          <div className="space-y-3 rounded-md border border-subtle bg-surface-2 p-3">
            {/* The hint sits outside the label on purpose: it is supporting text,
                not part of the control's accessible name. */}
            <div>
              <label htmlFor="enable-guest-view-all" className="flex cursor-pointer items-start gap-2">
                <input
                  id="enable-guest-view-all"
                  type="checkbox"
                  checked={enableGuestViewAll}
                  onChange={(event) => setEnableGuestViewAll(event.target.checked)}
                  className="mt-0.5"
                />
                <span className="text-sm font-medium text-primary">
                  {t("project_settings.service_client.confirm.guest_view_all")}
                </span>
              </label>
              <p className="text-xs mt-0.5 pl-6 text-tertiary">
                {t("project_settings.service_client.confirm.guest_view_all_hint")}
              </p>
            </div>
            <div>
              <label htmlFor="enable-time-tracking" className="flex cursor-pointer items-start gap-2">
                <input
                  id="enable-time-tracking"
                  type="checkbox"
                  checked={enableTimeTracking}
                  onChange={(event) => setEnableTimeTracking(event.target.checked)}
                  className="mt-0.5"
                />
                <span className="text-sm font-medium text-primary">
                  {t("project_settings.service_client.confirm.time_tracking")}
                </span>
              </label>
              <p className="text-xs mt-0.5 pl-6 text-tertiary">
                {t("project_settings.service_client.confirm.time_tracking_hint")}
              </p>
            </div>
          </div>
          <div className="flex items-center justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={() => setIsConfirmOpen(false)}>
              {t("common.cancel")}
            </Button>
            <Button variant="primary" size="sm" onClick={handleConfirm} loading={isSubmitting}>
              {t("common.confirm")}
            </Button>
          </div>
        </div>
      </ModalCore>

      <div className="flex flex-col gap-1">
        <CustomSelect
          value={selectedClientId}
          label={
            <span className="text-sm">
              {selectedClient?.name ?? t("project_settings.service_client.internal_work")}
            </span>
          }
          onChange={handleChange}
          disabled={disabled || !serviceClients}
          buttonClassName="w-full justify-between"
          input
        >
          <CustomSelect.Option value={null}>{t("project_settings.service_client.internal_work")}</CustomSelect.Option>
          {activeServiceClients.map((client) => (
            <CustomSelect.Option key={client.id} value={client.id}>
              {client.name}
            </CustomSelect.Option>
          ))}
        </CustomSelect>
        <span className="text-xs text-tertiary">{t("project_settings.service_client.hint")}</span>
      </div>
    </>
  );
});
