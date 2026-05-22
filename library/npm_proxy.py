#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Manage a single Nginx Proxy Manager proxy host.

Two tiers of managed fields:

- Core (always written). Module-level defaults ship so every reconcile
  settles these fields. They are the minimal set to route traffic:
  domain_names, forward_host, forward_port, forward_scheme,
  certificate_id, ssl_forced, allow_websocket_upgrade.

- Optional (written only when supplied in hosts.yml). When omitted,
  the existing NPM value is preserved. Lets per-host tweaks live in
  the NPM UI *or* in hosts.yml on a field-by-field basis:
  advanced_config, http2_support, hsts_enabled, hsts_subdomains,
  block_exploits, caching_enabled, access_list_id.

Fields not listed in either tier (locations, meta, etc.) are always
preserved from the existing NPM entry on update.
"""
from __future__ import annotations

import json

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import open_url


CORE_FIELDS = (
    "domain_names",
    "forward_host",
    "forward_port",
    "forward_scheme",
    "certificate_id",
    "ssl_forced",
    "allow_websocket_upgrade",
)

OPTIONAL_FIELDS = (
    "advanced_config",
    "http2_support",
    "hsts_enabled",
    "hsts_subdomains",
    "block_exploits",
    "caching_enabled",
    "access_list_id",
)

# NPM defaults applied only on CREATE. Merge order on UPDATE ensures
# these never clobber existing UI state.
CREATE_DEFAULTS = {
    "block_exploits": False,
    "caching_enabled": False,
    "http2_support": False,
    "hsts_enabled": False,
    "hsts_subdomains": False,
    "advanced_config": "",
    "locations": [],
    "access_list_id": 0,
    "meta": {},
}

# Server-computed fields that GET returns but PUT rejects.
PUT_STRIP = (
    "id",
    "created_on",
    "modified_on",
    "owner_user_id",
    "owner",
    "certificate",
    "access_list",
    "use_default_location",
    "enabled",
    "ipv6",
)


def _request(module, url, method, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    try:
        resp = open_url(
            url,
            method=method,
            headers=headers,
            data=data,
            validate_certs=module.params["validate_certs"],
            timeout=30,
        )
        raw = resp.read()
        return json.loads(raw) if raw else None
    except Exception as e:
        module.fail_json(msg="%s %s failed: %s" % (method, url, e))


def _auth(module):
    body = {"identity": module.params["email"], "secret": module.params["password"]}
    resp = _request(module, module.params["api_url"] + "/api/tokens", "POST", body=body)
    if not resp or "token" not in resp:
        module.fail_json(msg="NPM auth returned no token")
    return resp["token"]


def _list_proxies(module, token):
    return _request(module, module.params["api_url"] + "/api/nginx/proxy-hosts", "GET", token=token) or []


def _find_by_domain(proxies, domain):
    for p in proxies:
        if domain in (p.get("domain_names") or []):
            return p
    return None


def _desired(module):
    d = {
        "domain_names": [module.params["domain"]],
        "forward_host": module.params["forward_host"],
        "forward_port": module.params["forward_port"],
        "forward_scheme": module.params["forward_scheme"],
        "certificate_id": module.params["certificate_id"],
        "ssl_forced": module.params["ssl_forced"],
        "allow_websocket_upgrade": module.params["allow_websocket_upgrade"],
    }
    for f in OPTIONAL_FIELDS:
        if module.params.get(f) is not None:
            d[f] = module.params[f]
    return d


def _diff_managed(existing, desired):
    # Only compare the fields we're actually managing in this call.
    # Optional fields omitted from hosts.yml are not in desired and
    # therefore not compared, preserving existing NPM state.
    before, after = {}, {}
    for f in desired.keys():
        if existing.get(f) != desired[f]:
            before[f] = existing.get(f)
            after[f] = desired[f]
    return before, after


def main():
    module = AnsibleModule(
        argument_spec=dict(
            # connection
            api_url=dict(type="str", required=True),
            email=dict(type="str", required=True),
            password=dict(type="str", required=True, no_log=True),
            validate_certs=dict(type="bool", default=False),
            # identity
            domain=dict(type="str", required=True),
            # core managed (module-level defaults)
            forward_host=dict(type="str"),
            forward_port=dict(type="int"),
            forward_scheme=dict(type="str", default="http", choices=["http", "https"]),
            certificate_id=dict(type="int", default=1),
            ssl_forced=dict(type="bool", default=True),
            allow_websocket_upgrade=dict(type="bool", default=True),
            # optional managed (absent = preserve existing)
            advanced_config=dict(type="str"),
            http2_support=dict(type="bool"),
            hsts_enabled=dict(type="bool"),
            hsts_subdomains=dict(type="bool"),
            block_exploits=dict(type="bool"),
            caching_enabled=dict(type="bool"),
            access_list_id=dict(type="int"),
            # control
            state=dict(type="str", default="present", choices=["present", "absent"]),
        ),
        required_if=[("state", "present", ["forward_host", "forward_port"])],
        supports_check_mode=True,
    )

    base = module.params["api_url"]
    token = _auth(module)
    existing = _find_by_domain(_list_proxies(module, token), module.params["domain"])

    if module.params["state"] == "absent":
        if not existing:
            module.exit_json(changed=False, action="no_change")
        if module.check_mode:
            module.exit_json(changed=True, action="delete", proxy_id=existing["id"])
        _request(module, "%s/api/nginx/proxy-hosts/%s" % (base, existing["id"]), "DELETE", token=token)
        module.exit_json(changed=True, action="deleted", proxy_id=existing["id"])

    desired = _desired(module)

    if existing is None:
        if module.check_mode:
            module.exit_json(changed=True, action="create", diff={"before": {}, "after": desired})
        body = dict(CREATE_DEFAULTS)
        body.update(desired)
        created = _request(module, base + "/api/nginx/proxy-hosts", "POST", token=token, body=body)
        module.exit_json(changed=True, action="created", proxy_id=(created or {}).get("id"))

    before, after = _diff_managed(existing, desired)
    if not before:
        module.exit_json(changed=False, action="no_change", proxy_id=existing["id"])

    if module.check_mode:
        module.exit_json(changed=True, action="update", proxy_id=existing["id"], diff={"before": before, "after": after})

    merged = dict(existing)
    merged.update(desired)
    for ro in PUT_STRIP:
        merged.pop(ro, None)
    _request(module, "%s/api/nginx/proxy-hosts/%s" % (base, existing["id"]), "PUT", token=token, body=merged)
    module.exit_json(changed=True, action="updated", proxy_id=existing["id"], diff={"before": before, "after": after})


if __name__ == "__main__":
    main()
