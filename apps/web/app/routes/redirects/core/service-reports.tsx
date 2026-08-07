/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { redirect } from "react-router";
import type { Route } from "./+types/service-reports";

/**
 * `/:workspaceSlug/service-reports` lands on the attention panel.
 *
 * Not on a chart, deliberately: section 3b is the tab that says something has to be *done*, and a
 * dashboard whose front door is a graph trains people to browse rather than to act. A client
 * needing a call should be noticed before the renewal conversation, not during it.
 */
export const clientLoader = ({ params }: Route.ClientLoaderArgs) => {
  const { workspaceSlug } = params;
  throw redirect(`/${workspaceSlug}/service-reports/attention/`);
};

export default function ServiceReports() {
  return null;
}
