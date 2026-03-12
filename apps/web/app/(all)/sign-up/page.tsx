/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { redirect } from "react-router";
import type { Route } from "./+types/page";

// Sign-up is disabled — authentication is handled via LDAP.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function clientLoader(_: Route.ClientLoaderArgs) {
  throw redirect("/");
}

export default function SignUpPage() {
  return null;
}
