/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useState } from "react";
import { observer } from "mobx-react";
import { useRouter } from "next/navigation";
import { useTranslation } from "@plane/i18n";
import { Tabs } from "@plane/propel/tabs";
import { cn } from "@plane/utils";
import { PageHead } from "@/components/core/page-title";
import { useServiceReportTabs } from "@/components/service-reports";
import { useWorkspace } from "@/hooks/store/use-workspace";
import type { Route } from "./+types/page";

/**
 * Consumption dashboards and billing reports. Phase 9.
 *
 * **A route of its own rather than tabs inside `analytics/`.** The two answer different questions
 * for different audiences: work item analytics is about delivery, and this is about hours sold and
 * money owed. Folding them together would put a technician one click from a tab R11 keeps from
 * them, and would make the analytics filter toolbar -- projects, cycles, modules -- sit above
 * charts that filter by client, contract and competency.
 */
function ServiceReportsPage({ params }: Route.ComponentProps) {
  const { tabId, workspaceSlug } = params;

  const router = useRouter();
  const { t } = useTranslation();
  const { currentWorkspace } = useWorkspace();

  const TABS = useServiceReportTabs();

  const [selectedTab, setSelectedTab] = useState(tabId || TABS[0]?.key);

  useEffect(() => {
    if (tabId) setSelectedTab(tabId);
  }, [tabId]);

  const handleTabChange = (value: string) => {
    setSelectedTab(value);
    router.push(`/${workspaceSlug}/service-reports/${value}`);
  };

  const pageTitle = currentWorkspace?.name
    ? t("service_reports.page_label", { workspace: currentWorkspace.name })
    : undefined;

  return (
    <>
      <PageHead title={pageTitle} />
      <div className="flex h-full overflow-hidden">
        <Tabs value={selectedTab} onValueChange={handleTabChange} className="h-full w-full">
          <div className="flex h-full w-full flex-col">
            <div
              className={cn(
                "flex w-full items-center justify-between gap-4 overflow-hidden border-b border-subtle bg-surface-1 px-6 py-2"
              )}
            >
              <Tabs.List className="flex h-7 w-fit overflow-x-auto">
                {TABS.map((tab) => (
                  <Tabs.Trigger
                    key={tab.key}
                    value={tab.key}
                    size="md"
                    className="h-6 px-3"
                    onClick={() => handleTabChange(tab.key)}
                  >
                    {tab.label}
                  </Tabs.Trigger>
                ))}
              </Tabs.List>
            </div>
            {TABS.map((tab) => (
              <Tabs.Content key={tab.key} value={tab.key} className="h-full overflow-hidden overflow-y-auto">
                <tab.content />
              </Tabs.Content>
            ))}
          </div>
        </Tabs>
      </div>
    </>
  );
}

export default observer(ServiceReportsPage);
