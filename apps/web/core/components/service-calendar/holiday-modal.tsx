/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
// hooks
import { useServiceCatalog } from "@/hooks/store/use-service-catalog";
// local imports
import { CALENDAR_ERROR_KEYS, extractCalendarErrorCode } from "./calendar.helpers";
import type { THolidayFormValues } from "./holiday-form";
import { HolidayForm } from "./holiday-form";

type Props = {
  isOpen: boolean;
  workspaceSlug: string;
  handleClose: () => void;
};

export const HolidayModal = observer(function HolidayModal(props: Props) {
  const { isOpen, workspaceSlug, handleClose } = props;
  const { t } = useTranslation();
  const { createHoliday } = useServiceCatalog();

  const handleSubmit = async (values: THolidayFormValues) => {
    try {
      await createHoliday(workspaceSlug, values);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: t("workspace_settings.settings.worklog.holidays.toasts.created.title"),
        message: t("workspace_settings.settings.worklog.holidays.toasts.created.message"),
      });
      handleClose();
    } catch (error) {
      // The server refuses a recurrence collision in either direction, and the browser does
      // not evaluate that rule -- so the message has to come from the code it returns.
      const code = extractCalendarErrorCode(error);
      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("workspace_settings.settings.worklog.holidays.toasts.not_created.title"),
        message: code
          ? t(CALENDAR_ERROR_KEYS[code] ?? "common.errors.default.message")
          : t("common.errors.default.message"),
      });
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} position={EModalPosition.TOP} width={EModalWidth.XL}>
      <HolidayForm handleClose={handleClose} onSubmit={handleSubmit} />
    </ModalCore>
  );
});
