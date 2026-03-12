/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useEffect, useRef, useState } from "react";
import { observer } from "mobx-react";
import Link from "next/link";
// icons
import { Eye, EyeOff, XCircle } from "lucide-react";
// plane imports
import { API_BASE_URL, AUTH_TRACKER_ELEMENTS } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { Input, Spinner } from "@plane/ui";
// components
import { ForgotPasswordPopover } from "@/components/account/auth-forms/forgot-password-popover";
// helpers
import { EAuthModes, EAuthSteps } from "@/helpers/authentication.helper";
// services
import { AuthService } from "@/services/auth.service";

type Props = {
  email: string;
  isSMTPConfigured: boolean;
  mode: EAuthModes;
  handleEmailClear: () => void;
  handleAuthStep: (step: EAuthSteps) => void;
  nextPath: string | undefined;
};

type TPasswordFormValues = {
  email: string;
  password: string;
};

const defaultValues: TPasswordFormValues = {
  email: "",
  password: "",
};

const authService = new AuthService();

export const AuthPasswordForm = observer(function AuthPasswordForm(props: Props) {
  const { email, isSMTPConfigured, handleAuthStep, handleEmailClear, nextPath } = props;
  // plane imports
  const { t } = useTranslation();
  // ref
  const formRef = useRef<HTMLFormElement>(null);
  // states
  const [csrfPromise, setCsrfPromise] = useState<Promise<{ csrf_token: string }> | undefined>(undefined);
  const [passwordFormData, setPasswordFormData] = useState<TPasswordFormValues>({ ...defaultValues, email });
  const [showPassword, setShowPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleFormChange = (key: keyof TPasswordFormValues, value: string) =>
    setPasswordFormData((prev) => ({ ...prev, [key]: value }));

  useEffect(() => {
    if (csrfPromise === undefined) {
      const promise = authService.requestCSRFToken();
      setCsrfPromise(promise);
    }
  }, [csrfPromise]);

  const redirectToUniqueCodeSignIn = async () => {
    handleAuthStep(EAuthSteps.UNIQUE_CODE);
  };

  const isButtonDisabled = !isSubmitting && !!passwordFormData.password ? false : true;

  const handleCSRFToken = async () => {
    if (!formRef || !formRef.current) return;
    const token = await csrfPromise;
    if (!token?.csrf_token) return;
    const csrfElement = formRef.current.querySelector("input[name=csrfmiddlewaretoken]");
    csrfElement?.setAttribute("value", token?.csrf_token);
  };

  return (
    <form
      ref={formRef}
      className="space-y-4"
      method="POST"
      action={`${API_BASE_URL}/auth/sign-in/`}
      onSubmit={async (event) => {
        event.preventDefault();
        await handleCSRFToken();
        setIsSubmitting(true);
        if (formRef.current) formRef.current.submit();
      }}
      onError={() => {
        setIsSubmitting(false);
      }}
    >
      <input type="hidden" name="csrfmiddlewaretoken" />
      <input type="hidden" value={passwordFormData.email} name="email" />
      {nextPath && <input type="hidden" value={nextPath} name="next_path" />}
      <div className="space-y-1">
        <label htmlFor="email" className="text-13 font-medium text-tertiary">
          {t("auth.common.email.label")}
        </label>
        <div className={`relative flex items-center rounded-md border border-strong bg-surface-1`}>
          <Input
            id="email"
            name="email"
            type="email"
            value={passwordFormData.email}
            onChange={(e) => handleFormChange("email", e.target.value)}
            placeholder={t("auth.common.email.placeholder")}
            className={`h-10 w-full border-0 disable-autofill-style placeholder:text-placeholder`}
            disabled
          />
          {passwordFormData.email.length > 0 && (
            <button
              type="button"
              className="absolute right-3 size-5"
              onClick={handleEmailClear}
              aria-label={t("aria_labels.auth_forms.clear_email")}
            >
              <XCircle className="size-5 stroke-placeholder" />
            </button>
          )}
        </div>
      </div>

      <div className="space-y-1">
        <label htmlFor="password" className="text-13 font-medium text-tertiary">
          {t("auth.common.password.label")}
        </label>
        <div className="relative flex items-center rounded-md bg-surface-1">
          <Input
            type={showPassword ? "text" : "password"}
            id="password"
            name="password"
            value={passwordFormData.password}
            onChange={(e) => handleFormChange("password", e.target.value)}
            placeholder={t("auth.common.password.placeholder")}
            className="h-10 w-full border border-strong !bg-surface-1 pr-12 disable-autofill-style placeholder:text-placeholder"
            autoComplete="off"
            autoFocus
          />
          <button
            type="button"
            onClick={() => setShowPassword((prev) => !prev)}
            className="absolute right-3 grid size-5 place-items-center"
            aria-label={t(
              showPassword ? "aria_labels.auth_forms.hide_password" : "aria_labels.auth_forms.show_password"
            )}
          >
            {showPassword ? (
              <EyeOff className="size-5 stroke-placeholder" />
            ) : (
              <Eye className="size-5 stroke-placeholder" />
            )}
          </button>
        </div>
        <div className="w-full">
          {isSMTPConfigured ? (
            <Link
              data-ph-element={AUTH_TRACKER_ELEMENTS.FORGOT_PASSWORD_FROM_SIGNIN}
              href={`/accounts/forgot-password?email=${encodeURIComponent(email)}`}
              className="text-11 font-medium text-accent-primary"
            >
              {t("auth.common.forgot_password")}
            </Link>
          ) : (
            <ForgotPasswordPopover />
          )}
        </div>
      </div>

      <div className="space-y-2.5">
        <Button type="submit" variant="primary" className="w-full" size="xl" disabled={isButtonDisabled}>
          {isSubmitting ? (
            <Spinner height="20px" width="20px" />
          ) : isSMTPConfigured ? (
            t("common.continue")
          ) : (
            t("common.go_to_workspace")
          )}
        </Button>
        {isSMTPConfigured && (
          <Button
            type="button"
            data-ph-element={AUTH_TRACKER_ELEMENTS.SIGN_IN_WITH_UNIQUE_CODE}
            onClick={redirectToUniqueCodeSignIn}
            variant="secondary"
            className="w-full"
            size="xl"
          >
            {t("auth.common.sign_in_with_unique_code")}
          </Button>
        )}
      </div>
    </form>
  );
});
