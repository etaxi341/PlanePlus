/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
import { Controller, useForm } from "react-hook-form";
// types
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { IUser, TUserProfile, TOnboardingSteps } from "@plane/types";
// ui
import { Input, Spinner } from "@plane/ui";
// components
import { cn, getFileURL, validatePersonName } from "@plane/utils";
import { UserImageUploadModal } from "@/components/core/modals/user-image-upload-modal";
// hooks
import { useUser, useUserProfile } from "@/hooks/store/user";

type TProfileSetupFormValues = {
  first_name: string;
  last_name: string;
  avatar_url?: string | null;
  role?: string;
  use_case?: string[];
};

const defaultValues: Partial<TProfileSetupFormValues> = {
  first_name: "",
  last_name: "",
  avatar_url: "",
  role: undefined,
  use_case: [],
};

type Props = {
  user?: IUser;
  totalSteps: number;
  stepChange: (steps: Partial<TOnboardingSteps>) => Promise<void>;
  finishOnboarding: () => Promise<void>;
};

enum EProfileSetupSteps {
  ALL = "ALL",
  USER_DETAILS = "USER_DETAILS",
  USER_PERSONALIZATION = "USER_PERSONALIZATION",
}

const USER_ROLE = ["Individual contributor", "Senior Leader", "Manager", "Executive", "Freelancer", "Student"];

const USER_DOMAIN = [
  "Engineering",
  "Product",
  "Marketing",
  "Sales",
  "Operations",
  "Legal",
  "Finance",
  "Human Resources",
  "Project",
  "Other",
];

export const ProfileSetup = observer(function ProfileSetup(props: Props) {
  const { user, totalSteps, stepChange, finishOnboarding } = props;
  // states
  const [profileSetupStep, setProfileSetupStep] = useState<EProfileSetupSteps>(EProfileSetupSteps.USER_DETAILS);
  const [isImageUploadModalOpen, setIsImageUploadModalOpen] = useState(false);
  // plane hooks
  const { t } = useTranslation();
  // store hooks
  const { updateCurrentUser } = useUser();
  const { updateUserProfile } = useUserProfile();
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

  const handleSubmitProfileSetup = async (formData: TProfileSetupFormValues) => {
    const userDetailsPayload: Partial<IUser> = {
      first_name: formData.first_name,
      last_name: formData.last_name,
      avatar_url: formData.avatar_url ?? undefined,
    };
    const profileUpdatePayload: Partial<TUserProfile> = {
      use_case: formData.use_case && formData.use_case.length > 0 ? formData.use_case.join(". ") : undefined,
      role: formData.role,
    };
    try {
      await Promise.all([
        updateCurrentUser(userDetailsPayload),
        updateUserProfile(profileUpdatePayload),
        totalSteps > 2 && stepChange({ profile_complete: true }),
      ]);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: "Success",
        message: "Profile setup completed!",
      });
      // For Invited Users, they will skip all other steps and finish onboarding.
      if (totalSteps <= 2) {
        finishOnboarding();
      }
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Error",
        message: "Profile setup failed. Please try again!",
      });
    }
  };

  const handleSubmitUserDetail = async (formData: TProfileSetupFormValues) => {
    const userDetailsPayload: Partial<IUser> = {
      first_name: formData.first_name,
      last_name: formData.last_name,
      avatar_url: formData.avatar_url ?? undefined,
    };
    try {
      await updateCurrentUser(userDetailsPayload).then(() => {
        setProfileSetupStep(EProfileSetupSteps.USER_PERSONALIZATION);
      });
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Error",
        message: "User details update failed. Please try again!",
      });
    }
  };

  const handleSubmitUserPersonalization = async (formData: TProfileSetupFormValues) => {
    const profileUpdatePayload: Partial<TUserProfile> = {
      use_case: formData.use_case && formData.use_case.length > 0 ? formData.use_case.join(". ") : undefined,
      role: formData.role,
    };
    try {
      await Promise.all([
        updateUserProfile(profileUpdatePayload),
        totalSteps > 2 && stepChange({ profile_complete: true }),
      ]);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: "Success",
        message: "Profile setup completed!",
      });
      // For Invited Users, they will skip all other steps and finish onboarding.
      if (totalSteps <= 2) {
        finishOnboarding();
      }
    } catch {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Error",
        message: "Profile setup failed. Please try again!",
      });
    }
  };

  const onSubmit = async (formData: TProfileSetupFormValues) => {
    if (!user) return;
    if (profileSetupStep === EProfileSetupSteps.ALL) await handleSubmitProfileSetup(formData);
    if (profileSetupStep === EProfileSetupSteps.USER_DETAILS) await handleSubmitUserDetail(formData);
    if (profileSetupStep === EProfileSetupSteps.USER_PERSONALIZATION) await handleSubmitUserPersonalization(formData);
  };

  const handleDelete = (url: string | null | undefined) => {
    if (!url) return;
    setValue("avatar_url", "");
  };

  const isButtonDisabled = !isSubmitting && isValid ? false : true;

  return (
    <div className="flex h-full w-full">
      <div className="mt-6 flex w-full flex-col items-center justify-center p-8">
        <form onSubmit={handleSubmit(onSubmit)} className="mx-auto mt-2 w-full space-y-4 sm:w-96">
          {profileSetupStep !== EProfileSetupSteps.USER_PERSONALIZATION && (
            <>
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
              <div className="flex items-center justify-center space-y-1">
                <button type="button" onClick={() => setIsImageUploadModalOpen(true)}>
                  {!userAvatar || userAvatar === "" ? (
                    <div className="flex flex-col items-center justify-between">
                      <div className="relative h-14 w-14 overflow-hidden">
                        <div className="absolute top-0 left-0 flex h-full w-full items-center justify-center rounded-full bg-accent-primary text-24 font-medium text-on-color uppercase">
                          {watch("first_name")[0] ?? "R"}
                        </div>
                      </div>
                      <div className="pt-1 text-13 font-medium text-accent-secondary hover:text-tertiary">
                        Choose image
                      </div>
                    </div>
                  ) : (
                    <div className="relative mr-3 h-16 w-16 overflow-hidden">
                      <img
                        src={getFileURL(userAvatar ?? "")}
                        className="absolute top-0 left-0 h-full w-full rounded-full object-cover"
                        onClick={() => setIsImageUploadModalOpen(true)}
                        alt={user?.display_name}
                      />
                    </div>
                  )}
                </button>
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-1">
                  <label
                    className="text-13 font-medium text-tertiary after:ml-0.5 after:text-danger-primary after:content-['*']"
                    htmlFor="first_name"
                  >
                    First name
                  </label>
                  <Controller
                    control={control}
                    name="first_name"
                    rules={{
                      required: "First name is required",
                      validate: validatePersonName,
                      maxLength: {
                        value: 50,
                        message: "First name must be within 50 characters.",
                      },
                    }}
                    render={({ field: { value, onChange, ref } }) => (
                      <Input
                        id="first_name"
                        name="first_name"
                        type="text"
                        value={value}
                        autoFocus
                        onChange={onChange}
                        ref={ref}
                        hasError={Boolean(errors.first_name)}
                        placeholder="Wilbur"
                        className="w-full border-strong"
                        autoComplete="on"
                      />
                    )}
                  />
                  {errors.first_name && (
                    <span className="text-13 text-danger-primary">{errors.first_name.message}</span>
                  )}
                </div>
                <div className="space-y-1">
                  <label
                    className="text-13 font-medium text-tertiary after:ml-0.5 after:text-danger-primary after:content-['*']"
                    htmlFor="last_name"
                  >
                    Last name
                  </label>
                  <Controller
                    control={control}
                    name="last_name"
                    rules={{
                      required: "Last name is required",
                      validate: validatePersonName,
                      maxLength: {
                        value: 50,
                        message: "Last name must be within 50 characters.",
                      },
                    }}
                    render={({ field: { value, onChange, ref } }) => (
                      <Input
                        id="last_name"
                        name="last_name"
                        type="text"
                        value={value}
                        onChange={onChange}
                        ref={ref}
                        hasError={Boolean(errors.last_name)}
                        placeholder="Wright"
                        className="w-full border-strong"
                        autoComplete="on"
                      />
                    )}
                  />
                  {errors.last_name && <span className="text-13 text-danger-primary">{errors.last_name.message}</span>}
                </div>
              </div>
            </>
          )}

          {/* user role once the password is set */}
          {profileSetupStep !== EProfileSetupSteps.USER_DETAILS && (
            <>
              <div className="space-y-1">
                <label
                  className="text-13 font-medium text-tertiary after:ml-0.5 after:text-danger-primary after:content-['*']"
                  htmlFor="role"
                >
                  What role are you working on? Choose one.
                </label>
                <Controller
                  control={control}
                  name="role"
                  rules={{
                    required: "This field is required",
                  }}
                  render={({ field: { value, onChange } }) => (
                    <div className="flex flex-wrap gap-2 overflow-auto py-2 break-all">
                      {USER_ROLE.map((userRole) => (
                        <div
                          key={userRole}
                          className={cn(
                            "shrink-0 rounded border-[0.5px] px-3 py-1.5 text-13 font-medium hover:cursor-pointer hover:bg-surface-2",
                            {
                              "border-accent-strong": value === userRole,
                              "border-strong": value !== userRole,
                            }
                          )}
                          onClick={() => onChange(userRole)}
                        >
                          {userRole}
                        </div>
                      ))}
                    </div>
                  )}
                />
                {errors.role && <span className="text-13 text-danger-primary">{errors.role.message}</span>}
              </div>
              <div className="space-y-1">
                <label
                  className="text-13 font-medium text-tertiary after:ml-0.5 after:text-danger-primary after:content-['*']"
                  htmlFor="use_case"
                >
                  What is your domain expertise? Choose one or more.
                </label>
                <Controller
                  control={control}
                  name="use_case"
                  rules={{
                    required: "Please select at least one option",
                    validate: (value) => (value && value.length > 0) || "Please select at least one option",
                  }}
                  render={({ field: { value, onChange } }) => (
                    <div className="flex flex-wrap gap-2 overflow-auto py-2 break-all">
                      {USER_DOMAIN.map((userDomain) => {
                        const isSelected = value?.includes(userDomain) || false;
                        return (
                          <div
                            key={userDomain}
                            className={`flex-shrink-0 border-[0.5px] hover:cursor-pointer hover:bg-surface-2 ${
                              isSelected ? "border-accent-strong" : "border-strong"
                            } rounded px-3 py-1.5 text-13 font-medium`}
                            onClick={() => {
                              const currentValue = value || [];
                              if (isSelected) {
                                onChange(currentValue.filter((item) => item !== userDomain));
                              } else {
                                onChange([...currentValue, userDomain]);
                              }
                            }}
                          >
                            {userDomain}
                          </div>
                        );
                      })}
                    </div>
                  )}
                />
                {errors.use_case && <span className="text-13 text-danger-primary">{errors.use_case.message}</span>}
              </div>
            </>
          )}
          <Button variant="primary" type="submit" size="xl" className="w-full" disabled={isButtonDisabled}>
            {isSubmitting ? <Spinner height="20px" width="20px" /> : "Continue"}
          </Button>
        </form>
      </div>
    </div>
  );
});
