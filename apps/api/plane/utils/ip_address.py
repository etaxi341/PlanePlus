# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import ipaddress
import os


def _parse_ip(value):
    if not value:
        return None

    candidate = str(value).strip().strip('"')
    if not candidate:
        return None

    if candidate.lower().startswith("for="):
        candidate = candidate[4:].strip().strip('"')

    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    elif candidate.count(":") == 1 and "." in candidate:
        candidate = candidate.rsplit(":", 1)[0]

    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _get_first_ip_from_csv(value):
    if not value:
        return None

    for part in str(value).split(","):
        parsed_ip = _parse_ip(part)
        if parsed_ip:
            return parsed_ip
    return None


def _get_first_ip_from_forwarded(value):
    if not value:
        return None

    for part in str(value).split(","):
        for token in part.split(";"):
            token = token.strip()
            if token.lower().startswith("for="):
                parsed_ip = _parse_ip(token)
                if parsed_ip:
                    return parsed_ip
    return None


def _get_trusted_proxy_networks():
    trusted_proxies = os.environ.get("TRUSTED_PROXIES", "127.0.0.1/32,::1/128")
    networks = []

    for value in trusted_proxies.split(","):
        candidate = value.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            continue

    return networks


def _is_trusted_proxy(ip_address_value):
    parsed_ip = _parse_ip(ip_address_value)
    if not parsed_ip:
        return False

    ip_obj = ipaddress.ip_address(parsed_ip)
    return any(ip_obj in network for network in _get_trusted_proxy_networks())


def get_client_ip_details(request):
    remote_addr = _parse_ip(request.META.get("REMOTE_ADDR"))
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    x_real_ip = request.META.get("HTTP_X_REAL_IP", "")
    x_swg_client_ip = request.META.get("HTTP_X_SWG_CLIENT_IP", "")
    forwarded = request.META.get("HTTP_FORWARDED", "")
    trusted_proxy = _is_trusted_proxy(remote_addr)

    resolved_ip = remote_addr
    if trusted_proxy:
        resolved_ip = (
            _get_first_ip_from_csv(x_swg_client_ip)
            or _get_first_ip_from_forwarded(forwarded)
            or _get_first_ip_from_csv(x_forwarded_for)
            or _get_first_ip_from_csv(x_real_ip)
            or remote_addr
        )

    return {
        "remote_addr": remote_addr,
        "x_forwarded_for": x_forwarded_for,
        "x_real_ip": x_real_ip,
        "x_swg_client_ip": x_swg_client_ip,
        "forwarded": forwarded,
        "resolved_ip": resolved_ip,
        "trusted_proxy": trusted_proxy,
    }


def get_client_ip(request):
    return get_client_ip_details(request)["resolved_ip"]
