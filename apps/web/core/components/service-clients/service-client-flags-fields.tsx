/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useTranslation } from "@plane/i18n";

type Props = {
  guestViewAll: boolean;
  onGuestViewAllChange: (value: boolean) => void;
  timeTracking: boolean;
  onTimeTrackingChange: (value: boolean) => void;
  /** Distinguishes the two instances' checkbox ids when both are mounted. */
  idPrefix?: string;
};

/**
 * The two flags a project needs before a client's users get anything out of it.
 *
 * **Extracted as the field and not as the whole modal**, deliberately. Both the per-project
 * picker and the bulk assignment offer exactly these two controls with exactly this copy, and
 * that is where the duplication would have been. The modals around them are *not* the same
 * thing: one is a confirmation about a single project, with copy in the singular and defaults
 * derived from that project's current flags, and the other is a form over N projects that have
 * N different current states. Lifting the shell to serve both would have meant a component
 * parameterised by which of two flows it was in, which is the abstraction that costs more than
 * the duplication it removes.
 *
 * Neither caller ever sends `false`. The flags are **suggested, never imposed** -- the master
 * context is explicit -- and more practically, unchecking a box here means "leave it as it is",
 * not "turn it off": a bulk assignment must not silently disable time tracking on a project
 * that already had it.
 *
 * Why they matter, since the copy has to be short: with `guest_view_all_features` off, a client
 * user sees only the work items they opened themselves -- not the ones their colleagues opened,
 * and not the ones technicians opened on their behalf. Without `is_time_tracking_enabled` there
 * are no work logs, so there is no consumption to report.
 */
export function ServiceClientFlagsFields(props: Props) {
  const { guestViewAll, onGuestViewAllChange, timeTracking, onTimeTrackingChange, idPrefix = "" } = props;
  const { t } = useTranslation();

  const guestFieldId = `${idPrefix}enable-guest-view-all`;
  const trackingFieldId = `${idPrefix}enable-time-tracking`;

  return (
    <div className="space-y-3 rounded-md border border-subtle bg-surface-2 p-3">
      {/* The hint sits outside the label on purpose: it is supporting text,
          not part of the control's accessible name. */}
      <div>
        <label htmlFor={guestFieldId} className="flex cursor-pointer items-start gap-2">
          <input
            id={guestFieldId}
            type="checkbox"
            checked={guestViewAll}
            onChange={(event) => onGuestViewAllChange(event.target.checked)}
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
        <label htmlFor={trackingFieldId} className="flex cursor-pointer items-start gap-2">
          <input
            id={trackingFieldId}
            type="checkbox"
            checked={timeTracking}
            onChange={(event) => onTimeTrackingChange(event.target.checked)}
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
  );
}
