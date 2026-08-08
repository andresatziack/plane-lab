/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { Dialog, Transition } from "@headlessui/react";
import React, { Fragment } from "react";
// constants
import { cn } from "../utils";
import { EModalPosition, EModalWidth } from "./constants";
// helpers

type Props = {
  children: React.ReactNode;
  handleClose?: () => void;
  isOpen: boolean;
  position?: EModalPosition;
  width?: EModalWidth;
  className?: string;
};
export function ModalCore(props: Props) {
  const {
    children,
    handleClose,
    isOpen,
    position = EModalPosition.CENTER,
    width = EModalWidth.XXL,
    className = "",
  } = props;

  return (
    <Transition.Root show={isOpen} as={Fragment}>
      <Dialog as="div" className="relative z-30" onClose={() => handleClose && handleClose()}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-300"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-200"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-backdrop transition-opacity" />
        </Transition.Child>

        <div className="fixed inset-0 z-30 overflow-y-auto">
          <div className={position}>
            <Transition.Child
              as={Fragment}
              enter="ease-out duration-300"
              enterFrom="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
              enterTo="opacity-100 translate-y-0 sm:scale-100"
              leave="ease-in duration-200"
              leaveFrom="opacity-100 translate-y-0 sm:scale-100"
              leaveTo="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
            >
              {/*
                The panel is capped to the viewport and scrolls inside itself.

                **Why a cap at all.** Nothing here bounded the panel's height, so a tall form on a
                short screen simply overflowed the window: the work log form is 612px and
                `EModalPosition.TOP` adds `my-10 md:my-20`, so it demanded 772px. On a ~620px
                viewport -- an ordinary 1366x768 laptop once the OS and browser chrome are
                subtracted -- the save button rendered *below the fold from the moment it opened*,
                and because the container centres its child, the first focus scroll pushed the
                header off the top while the footer came in. The modal appeared to slide off the
                screen and could not be submitted.

                **Why `dvh` and not `vh`.** On mobile browsers `vh` is the tallest the viewport ever
                gets and ignores the address bar, so `vh` would reintroduce the same overflow on a
                phone. `dvh` tracks the space actually available.
                
                **Why the numbers are 5rem and 10rem.** They mirror the margins in
                `EModalPosition`: `my-10` is 2.5rem each side and `md:my-20` is 5rem each side.
                Deriving the cap from the margins is what makes this adapt to any resolution
                instead of trading one hard-coded height for another. `CENTER` uses `p-4`, so for
                that position the cap is 3rem more conservative than it strictly needs to be --
                which costs a little height and can never overflow.

                **The clipping trade-off.** `overflow-y-auto` makes the panel a scroll container,
                which would clip an absolutely positioned child. The dropdowns in these forms render
                through a floating portal *outside* the panel -- measured, not assumed -- so they
                are unaffected.
              */}
              <Dialog.Panel
                className={cn(
                  "relative w-full transform rounded-lg bg-surface-1 text-left shadow-raised-200 transition-all",
                  "max-h-[calc(100dvh-5rem)] overflow-y-auto md:max-h-[calc(100dvh-10rem)]",
                  width,
                  className
                )}
              >
                {children}
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition.Root>
  );
}
