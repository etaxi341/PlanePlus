/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { redirect } from "next/navigation";

// Sign-up is disabled — authentication is handled via LDAP.
// New users are auto-provisioned on first LDAP login.
export default function SignUpPage() {
  redirect("/");
}
