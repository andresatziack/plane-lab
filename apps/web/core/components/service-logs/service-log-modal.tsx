/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import type { IServiceLogPayload } from "@plane/types";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
// local imports
import { ServiceLogForm } from "./service-log-form";

type Props = {
  isOpen: boolean;
  workspaceSlug: string;
  projectId: string;
  issueId: string;
  /** Present when editing. The whole batch is replaced, never a single segment. */
  batchId?: string;
  initialValues?: IServiceLogPayload;
  handleClose: () => void;
};

export const ServiceLogModal = observer(function ServiceLogModal(props: Props) {
  const { isOpen, workspaceSlug, projectId, issueId, batchId, initialValues, handleClose } = props;
  const { t } = useTranslation();

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} position={EModalPosition.TOP} width={EModalWidth.XXL}>
      {/*
        `p-4` below `sm`, the original `p-5` from `sm` up.

        `ModalCore` panels are `w-full` with a `sm:max-w-*` cap and `EModalPosition.TOP` adds
        `mx-4`, so on a 390px screen the panel is 358px wide -- the width class is already
        responsive and needs nothing. What was pinched is the content: `p-5` left 318px for the
        widest form in this folder (a date input, two time inputs, two dropdowns and a textarea).
        `p-4` gives back 8px, which is the difference between the time inputs fitting and not.
      */}
      <div className="flex flex-col gap-4 p-4 sm:p-5">
        <h3 className="text-lg text-custom-text-100 font-medium">
          {batchId ? t("work_item.service_log.edit") : t("work_item.service_log.add")}
        </h3>
        {/* Keyed on the batch so switching between editing two entries remounts the
            form rather than keeping the previous entry's values. */}
        <ServiceLogForm
          key={batchId ?? "create"}
          workspaceSlug={workspaceSlug}
          projectId={projectId}
          issueId={issueId}
          batchId={batchId}
          initialValues={initialValues}
          handleClose={handleClose}
        />
      </div>
    </ModalCore>
  );
});
