/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { redirect } from "react-router";
import type { Route } from "./+types/page";

// Set-password is disabled — LDAP users do not set passwords manually.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function clientLoader(_: Route.ClientLoaderArgs) {
  throw redirect("/");
}

export default function SetPasswordPage() {
  return null;
}
