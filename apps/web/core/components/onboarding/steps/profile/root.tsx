/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Controller, useForm } from "react-hook-form";
import { ImageIcon } from "lucide-react";
// plane imports
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IUser } from "@plane/types";
import { EOnboardingSteps } from "@plane/types";
import { cn, getFileURL, validatePersonName } from "@plane/utils";
// components
import { UserImageUploadModal } from "@/components/core/modals/user-image-upload-modal";
// hooks
import { useInstance } from "@/hooks/store/use-instance";
import { useUser, useUserProfile } from "@/hooks/store/user";
// local components
import { CommonOnboardingHeader } from "../common";
import { MarketingConsent } from "./consent";

type Props = {
  handleStepChange: (step: EOnboardingSteps, skipInvites?: boolean) => void;
};

export type TProfileSetupFormValues = {
  first_name: string;
  last_name: string;
  avatar_url?: string | null;
  role?: string;
  use_case?: string[];
  has_marketing_email_consent?: boolean;
};

const defaultValues: Partial<TProfileSetupFormValues> = {
  first_name: "",
  last_name: "",
  avatar_url: "",
  has_marketing_email_consent: true,
};

export const ProfileSetupStep = observer(function ProfileSetupStep({ handleStepChange }: Props) {
  // states
  const [isImageUploadModalOpen, setIsImageUploadModalOpen] = useState(false);
  // store hooks
  const { data: user, updateCurrentUser } = useUser();
  const { updateUserProfile } = useUserProfile();
  const { config: instanceConfig } = useInstance();
  // form info
  const {
    getValues,
    handleSubmit,
    control,
    watch,
    setValue,
    formState: { errors, isSubmitting, isValid },
  } = useForm<TProfileSetupFormValues>({
    defaultValues: {
      ...defaultValues,
      first_name: user?.first_name,
      last_name: user?.last_name,
      avatar_url: user?.avatar_url,
    },
    mode: "onChange",
  });
  // derived values
  const userAvatar = watch("avatar_url");

  const handleSubmitUserDetail = async (formData: TProfileSetupFormValues) => {
    const userDetailsPayload: Partial<IUser> = {
      first_name: formData.first_name,
      last_name: formData.last_name,
      avatar_url: formData.avatar_url ?? undefined,
    };
    try {
      await updateCurrentUser(userDetailsPayload);
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Error",
        message: "User details update failed. Please try again!",
      });
    }
  };

  const onSubmit = async (formData: TProfileSetupFormValues) => {
    if (!user) return;
    updateUserProfile({
      has_marketing_email_consent: formData.has_marketing_email_consent,
    });
    await handleSubmitUserDetail(formData);
    handleStepChange(EOnboardingSteps.PROFILE_SETUP);
  };

  const handleDelete = (url: string | null | undefined) => {
    if (!url) return;
    setValue("avatar_url", "");
  };

  const isButtonDisabled = !isSubmitting && isValid ? false : true;

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-10">
      {/* Header */}
      <CommonOnboardingHeader title="Create your profile." description="This is how you will appear in Plane." />

      {/* Profile Picture Section */}
      <Controller
        control={control}
        name="avatar_url"
        render={({ field: { onChange, value } }) => (
          <UserImageUploadModal
            isOpen={isImageUploadModalOpen}
            onClose={() => setIsImageUploadModalOpen(false)}
            handleRemove={async () => handleDelete(getValues("avatar_url"))}
            onSuccess={(url) => {
              onChange(url);
              setIsImageUploadModalOpen(false);
            }}
            value={value && value.trim() !== "" ? value : null}
          />
        )}
      />
      <div className="flex items-center gap-4">
        <button
          className="flex size-12 items-center justify-center rounded-full bg-accent-primary text-18 font-semibold text-on-color"
          type="button"
          onClick={() => setIsImageUploadModalOpen(true)}
        >
          {userAvatar ? (
            <img
              src={getFileURL(userAvatar ?? "")}
              onClick={() => setIsImageUploadModalOpen(true)}
              alt={user?.display_name}
              className="h-full w-full rounded-full object-cover"
            />
          ) : (
            <>{watch("first_name")[0] ?? "R"}</>
          )}
        </button>
        <input type="file" className="hidden" id="profile-image-input" />
        <button
          className="flex items-center gap-1.5 px-2 py-1 text-13 text-tertiary hover:text-secondary"
          type="button"
          onClick={() => setIsImageUploadModalOpen(true)}
        >
          <ImageIcon className="size-4" />
          <span className="text-13">{userAvatar ? "Change image" : "Upload image"}</span>
        </button>
      </div>

      <div className="flex w-full flex-col gap-6">
        {/* Name Input */}
        <div className="flex flex-col gap-2">
          <label
            className="block text-13 font-medium text-tertiary after:ml-0.5 after:text-danger-primary after:content-['*']"
            htmlFor="first_name"
          >
            Name
          </label>
          <Controller
            control={control}
            name="first_name"
            rules={{
              required: "Name is required",
              validate: validatePersonName,
              maxLength: {
                value: 50,
                message: "Name must be within 50 characters.",
              },
            }}
            render={({ field: { value, onChange, ref } }) => (
              <input
                ref={ref}
                id="first_name"
                name="first_name"
                type="text"
                value={value}
                onChange={(e) => onChange(e.target.value)}
                autoFocus
                className={cn(
                  "w-full rounded-md border border-strong bg-surface-1 px-3 py-2 text-secondary transition-all duration-200 placeholder:text-placeholder focus:border-transparent focus:ring-2 focus:ring-accent-strong focus:outline-none",
                  {
                    "border-strong": !errors.first_name,
                    "border-danger-strong": errors.first_name,
                  }
                )}
                placeholder="Enter your full name"
                autoComplete="on"
              />
            )}
          />
          {errors.first_name && <span className="text-13 text-danger-primary">{errors.first_name.message}</span>}
        </div>
      </div>
      {/* Continue Button */}
      <Button variant="primary" type="submit" className="w-full" size="xl" disabled={isButtonDisabled}>
        Continue
      </Button>

      {/* Marketing Consent */}
      {!instanceConfig?.is_self_managed && (
        <MarketingConsent
          isChecked={!!watch("has_marketing_email_consent")}
          handleChange={(has_marketing_email_consent) =>
            setValue("has_marketing_email_consent", has_marketing_email_consent)
          }
        />
      )}
    </form>
  );
});
