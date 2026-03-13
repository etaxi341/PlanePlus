/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// icons
import { Eye, EyeOff, XCircle } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { Input, Spinner } from "@plane/ui";
// helpers
import { EAuthModes, EAuthSteps } from "@/helpers/authentication.helper";
// services
import { AuthService } from "@/services/auth.service";
import type { TLoginResponse } from "@/services/auth.service";

type Props = {
  email: string;
  isSMTPConfigured: boolean;
  mode: EAuthModes;
  handleEmailClear: () => void;
  handleAuthStep: (step: EAuthSteps) => void;
  nextPath: string | undefined;
  onLoginSuccess: (redirectPath: string) => void;
  onTwoFactorRequired: (email: string, password: string) => void;
  onError: (errorMessage: string) => void;
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
  const { email, handleEmailClear, onLoginSuccess, onTwoFactorRequired, onError } = props;
  // plane imports
  const { t } = useTranslation();
  // states
  const [passwordFormData, setPasswordFormData] = useState<TPasswordFormValues>({ ...defaultValues, email });
  const [showPassword, setShowPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleFormChange = (key: keyof TPasswordFormValues, value: string) =>
    setPasswordFormData((prev) => ({ ...prev, [key]: value }));

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting || !passwordFormData.password) return;

    setIsSubmitting(true);

    try {
      const response: TLoginResponse = await authService.login({
        email: passwordFormData.email,
        password: passwordFormData.password,
      });

      if (response.success && response.redirect_path) {
        // Successful login — redirect
        onLoginSuccess(response.redirect_path);
        return;
      }

      if (response.requires_two_factor) {
        // 2FA code needed — switch to 2FA step
        onTwoFactorRequired(passwordFormData.email, passwordFormData.password);
        return;
      }

      // Should not reach here normally, but handle gracefully
      onError(response.error_message || "Authentication failed. Please try again.");
    } catch (error: any) {
      const errorMessage = error?.error_message || "Authentication failed. Please try again.";
      onError(errorMessage);
    } finally {
      setIsSubmitting(false);
    }
  };

  const isButtonDisabled = !(!isSubmitting && !!passwordFormData.password);

  return (
    <form className="space-y-4" onSubmit={handleSubmit}>
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
      </div>

      <div className="space-y-2.5">
        <Button type="submit" variant="primary" className="w-full" size="xl" disabled={isButtonDisabled}>
          {isSubmitting ? <Spinner height="20px" width="20px" /> : t("common.continue")}
        </Button>
      </div>
    </form>
  );
});
