import { Fragment, useState } from "react";
import { usePopper } from "react-popper";
import { X } from "lucide-react";
import { Popover } from "@headlessui/react";

export const ForgotPasswordPopover = () => {
  // popper-js refs
  const [referenceElement, setReferenceElement] = useState<HTMLButtonElement | null>(null);
  const [popperElement, setPopperElement] = useState<HTMLDivElement | null>(null);
  // popper-js init
  const { styles, attributes } = usePopper(referenceElement, popperElement, {
    placement: "right-start",
    modifiers: [
      {
        name: "preventOverflow",
        options: {
          padding: 12,
        },
      },
    ],
  });

  return (
    <Popover className="relative">
    </Popover>
  );
};
