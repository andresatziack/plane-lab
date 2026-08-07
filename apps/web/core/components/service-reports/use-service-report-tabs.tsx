/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useMemo } from "react";
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { useUserPermissions } from "@/hooks/store/user";
import { AttentionTab } from "./attention-tab";
import { BillingTab } from "./billing-tab";
import { ConsumptionTab } from "./consumption-tab";
import { OperationalTab } from "./operational-tab";

/**
 * The tabs of the service reports route.
 *
 * **Billing is hidden from a non-Admin rather than shown and refused.** The endpoint enforces it
 * -- that is the boundary, and it is tested -- but offering a tab that always 403s teaches people
 * that errors are normal. The other three admit Members and simply arrive without money in them.
 *
 * Attention comes first because it is the one that says something has to be done. A dashboard
 * whose default tab is a chart trains people to browse rather than to act, and section 3b exists
 * precisely so that a client needing a call is noticed before the renewal conversation.
 */
export const useServiceReportTabs = () => {
  const { t } = useTranslation();
  const { allowPermissions } = useUserPermissions();

  const isWorkspaceAdmin = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);

  return useMemo(
    () =>
      [
        { key: "attention", label: t("service_reports.tabs.attention"), content: AttentionTab },
        { key: "consumption", label: t("service_reports.tabs.consumption"), content: ConsumptionTab },
        { key: "operational", label: t("service_reports.tabs.operational"), content: OperationalTab },
        ...(isWorkspaceAdmin
          ? [{ key: "billing", label: t("service_reports.tabs.billing"), content: BillingTab }]
          : []),
      ] as const,
    [t, isWorkspaceAdmin]
  );
};
