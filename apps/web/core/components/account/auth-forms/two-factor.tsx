/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// icons
import { XCircle, ShieldCheck } from "lucide-react";
// plane imports
import { useTranslation } from "@plane/i18n";
import { Button } from "@plane/propel/button";
import { Input, Spinner } from "@plane/ui";
// services
import { AuthService } from "@/services/auth.service";
import type { TLoginResponse } from "@/services/auth.service";

type Props = {
  email: string;
  password: string;
  handleEmailClear: () => void;
  onLoginSuccess: (redirectPath: string) => void;
  onError: (errorMessage: string) => void;
};

const authService = new AuthService();

export const AuthTwoFactorForm = observer(function AuthTwoFactorForm(props: Props) {
  const { email, password, handleEmailClear, onLoginSuccess, onError } = props;
  // plane imports
  const { t } = useTranslation();
  // states
  const [twoFactorCode, setTwoFactorCode] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isSubmitting || !twoFactorCode) return;

    setIsSubmitting(true);

    try {
      const response: TLoginResponse = await authService.login({
        email,
        password,
        two_factor_code: twoFactorCode,
      });

      if (response.success && response.redirect_path) {
        onLoginSuccess(response.redirect_path);
        return;
      }

      // Handle error from response
      onError(response.error_message || "Authentication failed. Please try again.");
    } catch (error: any) {
      const errorMessage = error?.error_message || "Invalid two-factor code. Please try again.";
      onError(errorMessage);
    } finally {
      setIsSubmitting(false);
    }
  };

  const isButtonDisabled = !twoFactorCode || twoFactorCode.length < 6 || isSubmitting;

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
            value={email}
            placeholder={t("auth.common.email.placeholder")}
            className={`h-10 w-full border-0 disable-autofill-style placeholder:text-placeholder`}
            disabled
          />
          {email.length > 0 && (
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
        <label htmlFor="two_factor_code" className="text-13 font-medium text-tertiary">
          <span className="flex items-center gap-1.5">
            <ShieldCheck className="size-4" />
            Zwei-Faktor-Code
          </span>
        </label>
        <div className="relative flex items-center rounded-md bg-surface-1">
          <Input
            type="text"
            id="two_factor_code"
            name="two_factor_code"
            value={twoFactorCode}
            onChange={(e) => {
              // Only allow digits
              const value = e.target.value.replace(/\D/g, "");
              if (value.length <= 6) {
                setTwoFactorCode(value);
              }
            }}
            placeholder="000000"
            className="text-lg font-mono h-10 w-full border border-strong !bg-surface-1 text-center tracking-[0.5em] disable-autofill-style placeholder:text-placeholder"
            autoComplete="one-time-code"
            inputMode="numeric"
            maxLength={6}
          />
        </div>
        <p className="text-11 text-tertiary">Geben Sie den 6-stelligen Code aus Ihrer Authenticator-App ein.</p>
      </div>

      <div className="space-y-2.5">
        <Button type="submit" variant="primary" className="w-full" size="xl" disabled={isButtonDisabled}>
          {isSubmitting ? <Spinner height="20px" width="20px" /> : "Anmelden"}
        </Button>
      </div>
    </form>
  );
});
