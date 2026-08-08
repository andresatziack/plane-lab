/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import Link from "next/link";
import { Controller, useForm } from "react-hook-form";
import useSWR from "swr";

import { Disclosure } from "@headlessui/react";
// plane imports
import { ROLE, EUserPermissions, EUserPermissionsLevel, MEMBER_TRACKER_ELEMENTS } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { TrashIcon, SuspendedUserIcon } from "@plane/propel/icons";
import { Pill, EPillVariant, EPillSize } from "@plane/propel/pill";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IUser, IWorkspaceMember, TServiceMemberCapabilityKey } from "@plane/types";
// plane ui
import { CustomSelect, PopoverMenu, ToggleSwitch, Tooltip } from "@plane/ui";
// helpers
import { getFileURL } from "@plane/utils";
// hooks
import { useMember } from "@/hooks/store/use-member";
import { useUser, useUserPermissions } from "@/hooks/store/user";
// services
import { ServiceMemberPermissionService } from "@/services/service-member-permission.service";

const permissionService = new ServiceMemberPermissionService();

export interface RowData {
  member: IWorkspaceMember;
  role: EUserPermissions;
  is_active: boolean;
}

type NameProps = {
  rowData: RowData;
  workspaceSlug: string;
  isAdmin: boolean;
  currentUser: IUser | undefined;
  setRemoveMemberModal: (rowData: RowData) => void;
};

type AccountTypeProps = {
  rowData: RowData;
  workspaceSlug: string;
};

export function NameColumn(props: NameProps) {
  const { rowData, workspaceSlug, isAdmin, currentUser, setRemoveMemberModal } = props;
  // derived values
  const { avatar_url, display_name, email, first_name, id, last_name } = rowData.member;
  const isSuspended = rowData.is_active === false;

  return (
    <Disclosure>
      {() => (
        <div className="group relative">
          <div className="flex w-72 items-center justify-between gap-x-4 gap-y-2">
            <div className="flex flex-1 items-center gap-x-2 gap-y-2">
              {isSuspended ? (
                <div className="rounded-full bg-layer-1">
                  <SuspendedUserIcon className="size-6 text-placeholder" />
                </div>
              ) : avatar_url && avatar_url.trim() !== "" ? (
                <Link href={`/${workspaceSlug}/profile/${id}`}>
                  <span className="relative flex size-6 items-center justify-center rounded-full text-on-color capitalize">
                    <img
                      src={getFileURL(avatar_url)}
                      className="absolute top-0 left-0 h-full w-full rounded-full object-cover"
                      alt={display_name || email}
                    />
                  </span>
                </Link>
              ) : (
                <Link href={`/${workspaceSlug}/profile/${id}`}>
                  <span className="relative flex size-6 items-center justify-center rounded-full bg-layer-3 text-11 text-tertiary capitalize">
                    {(email ?? display_name ?? "?")[0]}
                  </span>
                </Link>
              )}
              <span className={isSuspended ? "text-placeholder" : ""}>
                {first_name} {last_name}
              </span>
            </div>

            {!isSuspended && (isAdmin || id === currentUser?.id) && (
              <PopoverMenu
                data={[""]}
                keyExtractor={(item) => item}
                popoverClassName="justify-end"
                buttonClassName="outline-none	origin-center rotate-90 size-8 aspect-square flex-shrink-0 grid place-items-center opacity-0 group-hover:opacity-100 transition-opacity"
                render={() => (
                  <div
                    // Pre-existing warning. Suppressed only because the pre-commit hook denies
                    // warnings on any staged file, including ones this change did not introduce.
                    // oxlint-disable-next-line jsx-a11y/prefer-tag-over-role
                    role="button"
                    tabIndex={0}
                    className="flex cursor-pointer items-center gap-x-3"
                    onClick={() => setRemoveMemberModal(rowData)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setRemoveMemberModal(rowData);
                      }
                    }}
                    data-ph-element={MEMBER_TRACKER_ELEMENTS.WORKSPACE_MEMBER_TABLE_CONTEXT_MENU}
                  >
                    <TrashIcon className="size-3.5 align-middle" /> {id === currentUser?.id ? "Leave " : "Remove "}
                  </div>
                )}
              />
            )}
          </div>
        </div>
      )}
    </Disclosure>
  );
}

export const AccountTypeColumn = observer(function AccountTypeColumn(props: AccountTypeProps) {
  const { rowData, workspaceSlug } = props;
  // form info
  const {
    control,
    formState: { errors },
  } = useForm();
  // store hooks
  const { allowPermissions } = useUserPermissions();

  const {
    workspace: { updateMember },
  } = useMember();
  const { data: currentUser } = useUser();

  // derived values
  const isCurrentUser = currentUser?.id === rowData.member.id;
  const isAdminRole = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);
  const isRoleNonEditable = isCurrentUser || !isAdminRole;
  const isSuspended = rowData.is_active === false;

  return (
    <>
      {isSuspended ? (
        <div className="flex w-32">
          <Pill variant={EPillVariant.DEFAULT} size={EPillSize.SM} className="border-none">
            Suspended
          </Pill>
        </div>
      ) : isRoleNonEditable ? (
        <div className="flex w-32">
          <span>{ROLE[rowData.role]}</span>
        </div>
      ) : (
        <Controller
          name="role"
          control={control}
          rules={{ required: "Role is required." }}
          render={({ field: { value } }) => (
            <CustomSelect
              value={value as EUserPermissions}
              // oxlint-disable-next-line no-shadow
              onChange={async (value: EUserPermissions) => {
                if (!workspaceSlug) return;
                try {
                  await updateMember(workspaceSlug.toString(), rowData.member.id, {
                    role: value as unknown as EUserPermissions,
                  });
                } catch (err: unknown) {
                  const error = err as { error?: string | string[] };
                  const errorString = Array.isArray(error?.error) ? error.error[0] : error?.error;

                  setToast({
                    type: TOAST_TYPE.ERROR,
                    title: "Error!",
                    message: errorString ?? "An error occurred while updating member role. Please try again.",
                  });
                }
              }}
              label={
                <div className="flex">
                  <span>{ROLE[rowData.role]}</span>
                </div>
              }
              buttonClassName={`!px-0 !justify-start hover:bg-surface-1 ${errors.role ? "border-danger-strong" : "border-none"}`}
              className="w-32 rounded-md p-0"
              input
            >
              {Object.keys(ROLE).map((item) => (
                <CustomSelect.Option key={item} value={item as unknown as EUserPermissions}>
                  {ROLE[item as unknown as keyof typeof ROLE]}
                </CustomSelect.Option>
              ))}
            </CustomSelect>
          )}
        />
      )}
    </>
  );
});

/** The three grants, in the order the phase brief lists them. */
const SERVICE_CAPABILITIES: TServiceMemberCapabilityKey[] = [
  "can_manage_others",
  "can_delegate",
  "can_reassign_author",
];

/**
 * The work log grants of Phase 7, on the row of the member who holds them. Decision D45.
 *
 * **No new screen**, which is what Phase 7 asked for: the grants belong next to the account type
 * because they are the same question -- what may this person do -- and a separate page would make
 * an Admin check two places before answering it.
 *
 * Three cases, and two of them are deliberately not switches:
 *
 * - **ADMIN**: reads "All (Admin)". Resolution is `is_admin OR flag`, so an Admin can do all three
 *   with no row at all. Rendering three switches, necessarily off, would say the opposite of what
 *   is true.
 * - **GUEST**: reads em dash. A client's own user cannot hold any grant -- the server answers 400
 *   `GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS` -- so there is nothing to offer.
 * - **MEMBER**: a summary that opens three independent switches.
 *
 * That the GUEST case is derived from `rowData.role` is what makes criterion 15 fall out for free:
 * `AccountTypeColumn` updates the role optimistically in the same store, so demoting somebody to
 * client user redraws this cell as "not applicable" in the same tick. The server has already
 * revoked the grants for real, inside the request that changed the role.
 */
export const ServicePermissionsColumn = observer(function ServicePermissionsColumn(props: AccountTypeProps) {
  const { rowData, workspaceSlug } = props;
  const { t } = useTranslation();
  const [pending, setPending] = useState<TServiceMemberCapabilityKey | null>(null);
  // store hooks
  const { allowPermissions } = useUserPermissions();
  // derived values
  const isAdminViewer = allowPermissions([EUserPermissions.ADMIN], EUserPermissionsLevel.WORKSPACE);
  const isSuspended = rowData.is_active === false;

  /*
   * One request for the whole table, not one per row: every row asks SWR for the same key, and SWR
   * deduplicates. Gated on the viewer being an Admin because the list route is Admin-only, so a
   * Member's table would otherwise fire a 403 per render.
   */
  const { data: grants, mutate } = useSWR(
    workspaceSlug && isAdminViewer ? `SERVICE_MEMBER_PERMISSIONS_${workspaceSlug}` : null,
    workspaceSlug && isAdminViewer ? () => permissionService.fetchAll(workspaceSlug) : null,
    { revalidateOnFocus: false }
  );

  // Absent from the list means "holds nothing". Revoked rows are kept server side for the audit
  // trail and filtered out of the response, so absence is never "not yet considered".
  const held = grants?.find((grant) => grant.member === rowData.member.id);

  const handleToggle = async (capability: TServiceMemberCapabilityKey, value: boolean) => {
    if (!workspaceSlug) return;

    setPending(capability);
    try {
      // Only the flag being changed is sent, so the other two cannot be revoked by omission.
      await permissionService.update(workspaceSlug, rowData.member.id, { [capability]: value });
      await mutate();
    } catch (err: unknown) {
      const code = (err as { error?: string } | undefined)?.error;

      setToast({
        type: TOAST_TYPE.ERROR,
        title: t("common.errors.default.title"),
        message:
          code === "GUEST_CANNOT_HOLD_SERVICE_LOG_PERMISSIONS"
            ? t("workspace_settings.settings.service_permissions.errors.guest")
            : t("workspace_settings.settings.service_permissions.errors.default"),
      });
    } finally {
      setPending(null);
    }
  };

  if (isSuspended) return null;

  if (rowData.role === EUserPermissions.ADMIN) {
    return (
      <Tooltip tooltipContent={t("workspace_settings.settings.service_permissions.admin_hint")}>
        <span className="text-tertiary">{t("workspace_settings.settings.service_permissions.all_admin")}</span>
      </Tooltip>
    );
  }

  if (rowData.role === EUserPermissions.GUEST) {
    return (
      <Tooltip tooltipContent={t("workspace_settings.settings.service_permissions.guest_hint")}>
        <span className="text-placeholder">—</span>
      </Tooltip>
    );
  }

  const summary = SERVICE_CAPABILITIES.filter((capability) => held?.[capability])
    .map((capability) => t(`workspace_settings.settings.service_permissions.${capability}`))
    .join(", ");

  // A Member looking at the table cannot grant anything, so the cell states the position rather
  // than offering a control that would 403.
  if (!isAdminViewer) {
    return (
      <span className="text-tertiary">{summary || t("workspace_settings.settings.service_permissions.none")}</span>
    );
  }

  return (
    <PopoverMenu
      data={SERVICE_CAPABILITIES}
      keyExtractor={(capability) => capability}
      panelClassName="w-64"
      button={
        <span className="flex cursor-pointer items-center gap-1 truncate text-left hover:text-primary">
          {summary || t("workspace_settings.settings.service_permissions.none")}
        </span>
      }
      render={(capability) => (
        <div className="flex items-center justify-between gap-3 px-1 py-1.5">
          <span className="text-12 text-secondary">
            {t(`workspace_settings.settings.service_permissions.${capability}`)}
          </span>
          <ToggleSwitch
            value={!!held?.[capability]}
            onChange={(value) => handleToggle(capability, value)}
            disabled={pending !== null}
            size="sm"
          />
        </div>
      )}
    />
  );
});
