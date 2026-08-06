/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { redirect } from "react-router";
import type { Route } from "./+types/worklog-settings";

/**
 * `/settings/worklog` has no content of its own: the panel is a set of sections.
 * Land on the first one, the same way the analytics route does.
 */
export const clientLoader = ({ params }: Route.ClientLoaderArgs) => {
  const { workspaceSlug } = params;
  throw redirect(`/${workspaceSlug}/settings/worklog/hour-types/`);
};

export default function WorklogSettings() {
  return null;
}
