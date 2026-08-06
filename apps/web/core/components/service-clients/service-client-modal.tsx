/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IServiceClient } from "@plane/types";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
import { normalizeCNPJ } from "@plane/utils";
// hooks
import { useServiceClient } from "@/hooks/store/use-service-client";
// local imports
import type { TServiceClientFormValues } from "./service-client-form";
import { ServiceClientForm } from "./service-client-form";

type Props = {
  isOpen: boolean;
  handleClose: () => void;
  workspaceSlug: string;
  data?: IServiceClient;
};

export const ServiceClientModal = observer(function ServiceClientModal(props: Props) {
  const { isOpen, handleClose, workspaceSlug, data } = props;
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { createServiceClient, updateServiceClient, serviceClientIds, getServiceClientById } = useServiceClient();

  // A client cannot be its own parent, so exclude the record being edited.
  const parentOptions = serviceClientIds
    .map((id) => getServiceClientById(id))
    .filter((option): option is IServiceClient => Boolean(option) && option?.id !== data?.id);

  const handleFormSubmit = async (values: TServiceClientFormValues) => {
    const payload = {
      ...values,
      // Send the digits only; the mask is presentation. Empty becomes null so the
      // partial unique index does not treat blanks as duplicates.
      tax_id: values.tax_id?.trim() ? normalizeCNPJ(values.tax_id) : null,
    };

    try {
      if (data) {
        await updateServiceClient(workspaceSlug, data.id, payload);
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t("common.success"),
          message: t("workspace_settings.settings.service_clients.toasts.updated"),
        });
      } else {
        await createServiceClient(workspaceSlug, payload);
        setToast({
          type: TOAST_TYPE.SUCCESS,
          title: t("common.success"),
          message: t("workspace_settings.settings.service_clients.toasts.created"),
        });
      }
      handleClose();
    } catch (error) {
      // Surface the server's field errors rather than a generic message: a
      // duplicate name and an invalid CNPJ need different corrections.
      const errorDetail = error as Record<string, string[] | string> | undefined;
      const firstError = errorDetail ? Object.values(errorDetail).flat().find(Boolean) : undefined;

      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message:
          typeof firstError === "string"
            ? firstError
            : t("workspace_settings.settings.service_clients.toasts.save_failed"),
      });
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={handleClose} position={EModalPosition.TOP} width={EModalWidth.XXL}>
      <ServiceClientForm
        data={data}
        parentOptions={parentOptions}
        handleClose={handleClose}
        onSubmit={handleFormSubmit}
      />
    </ModalCore>
  );
});
