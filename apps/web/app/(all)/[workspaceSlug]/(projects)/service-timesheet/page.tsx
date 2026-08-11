/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useTranslation } from "@plane/i18n";
import { PageHead } from "@/components/core/page-title";
import { TimesheetPage } from "@/components/service-timesheet";
import { useWorkspace } from "@/hooks/store/use-workspace";

function ServiceTimesheetPageRoute() {
  const { t } = useTranslation();
  const { currentWorkspace } = useWorkspace();

  const pageTitle = currentWorkspace?.name
    ? t("service_timesheet.page_label", { workspace: currentWorkspace.name })
    : undefined;

  return (
    <>
      <PageHead title={pageTitle} />
      <TimesheetPage />
    </>
  );
}

export default observer(ServiceTimesheetPageRoute);
