/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// types
import { API_BASE_URL } from "@plane/constants";
import type { ICsrfTokenData, IEmailCheckData, IEmailCheckResponse } from "@plane/types";
// helpers
// services
import { APIService } from "@/services/api.service";

export type TLoginData = {
  email: string;
  password: string;
  two_factor_code?: string;
};

export type TLoginResponse = {
  success?: boolean;
  redirect_path?: string;
  error_code?: string;
  error_message?: string;
  requires_two_factor?: boolean;
};

export class AuthService extends APIService {
  constructor() {
    super(API_BASE_URL);
  }

  async requestCSRFToken(): Promise<ICsrfTokenData> {
    return this.get("/auth/get-csrf-token/")
      .then((response) => response.data)
      .catch((error) => {
        throw error;
      });
  }

  emailCheck = async (data: IEmailCheckData): Promise<IEmailCheckResponse> =>
    this.post("/auth/email-check/", data, { headers: {} })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });

  /**
   * Login via the SWG internal auth API (proxied through our backend).
   * Supports optional 2FA code.
   */
  login = async (data: TLoginData): Promise<TLoginResponse> => {
    const csrfData = await this.requestCSRFToken();
    return this.post("/auth/sign-in/", data, {
      headers: {
        "X-CSRFToken": csrfData.csrf_token,
      },
    })
      .then((response) => response?.data)
      .catch((error) => {
        // The backend returns structured JSON errors
        const errorData = error?.response?.data;
        if (errorData) {
          throw errorData;
        }
        throw { error_code: "UNKNOWN_ERROR", error_message: "An unexpected error occurred." };
      });
  };

  async sendResetPasswordLink(data: { email: string }): Promise<any> {
    return this.post(`/auth/forgot-password/`, data)
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response;
      });
  }

  async setPassword(token: string, data: { password: string }): Promise<any> {
    return this.post(`/auth/set-password/`, data, {
      headers: {
        "X-CSRFTOKEN": token,
      },
    })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async generateUniqueCode(data: { email: string }): Promise<any> {
    return this.post("/auth/magic-generate/", data, { headers: {} })
      .then((response) => response?.data)
      .catch((error) => {
        throw error?.response?.data;
      });
  }

  async signOut(baseUrl: string): Promise<void> {
    const data = await this.requestCSRFToken();
    const csrfToken = data?.csrf_token;

    if (!csrfToken) throw Error("CSRF token not found");

    const form = document.createElement("form");
    const element1 = document.createElement("input");

    form.method = "POST";
    form.action = `${baseUrl}/auth/sign-out/`;

    element1.value = csrfToken;
    element1.name = "csrfmiddlewaretoken";
    element1.type = "hidden";
    form.appendChild(element1);

    document.body.appendChild(form);

    form.submit();
  }
}
