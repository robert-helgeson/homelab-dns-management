#!/usr/bin/env python3
"""
One-shot bootstrap: export every live NPM proxy host as a YAML block
suitable for pasting under `proxies:` in hosts.yml.

Fields matching the playbook's defaults (forward_scheme=http,
certificate_id=1, ssl_forced=true, allow_websocket_upgrade=true) are
omitted so the generated block is compact. The npm_proxy module fills
those in at runtime, so `npm-check` still reports no_change.

Optional fields (advanced_config, hsts_*, http2_support, block_exploits,
caching_enabled, access_list_id) are only emitted when they differ from
the NPM factory defaults. That way a proxy like pve1 with custom
advanced_config gets captured in hosts.yml, while the other 40 simple
entries stay a few lines each.

Prompts for NPM admin credentials; nothing persisted.
"""
import getpass
import json
import sys
import urllib.request
import urllib.error

API = "http://10.0.0.19:81"
NPM_TARGET = "npm_primary"

# Core fields the playbook always manages. Values here match the
# defaults in library/npm_proxy.py so matching entries produce no_change.
CORE_DEFAULTS = {
    "forward_scheme": "http",
    "certificate_id": 1,
    "ssl_forced": True,
    "allow_websocket_upgrade": True,
}

# Optional fields: managed in hosts.yml only when present. These are the
# NPM factory defaults — when the live value matches, we don't emit and
# the playbook leaves them alone (preserve-on-update).
OPTIONAL_DEFAULTS = {
    "advanced_config": "",
    "http2_support": False,
    "hsts_enabled": False,
    "hsts_subdomains": False,
    "block_exploits": False,
    "caching_enabled": False,
    "access_list_id": 0,
}


def req(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    resp = urllib.request.urlopen(r, timeout=30)
    raw = resp.read()
    return json.loads(raw) if raw else None


def yaml_scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def emit_block_scalar(key, text, indent=4):
    """Emit a multi-line string using YAML's | block scalar."""
    pad = " " * indent
    # Normalize line endings, drop trailing empty lines
    lines = text.replace("\r\n", "\n").rstrip("\n").split("\n")
    print("%s%s: |" % (pad, key))
    for line in lines:
        print("%s  %s" % (pad, line))


def emit_optional(h, key, indent=4):
    """Emit an optional field iff it differs from NPM's factory default."""
    if key not in h:
        return
    v = h[key]
    if v == OPTIONAL_DEFAULTS[key]:
        return
    if isinstance(v, str) and "\n" in v:
        emit_block_scalar(key, v, indent=indent)
    else:
        pad = " " * indent
        print("%s%s: %s" % (pad, key, yaml_scalar(v)))


def main():
    email = input("NPM email: ").strip()
    password = getpass.getpass("NPM password: ")

    print("# Authenticating against %s ..." % API, file=sys.stderr)
    try:
        token = req("POST", "/api/tokens",
                    body={"identity": email, "secret": password})["token"]
    except urllib.error.HTTPError as e:
        sys.exit("Auth failed: HTTP %d %s" % (e.code, e.reason))

    print("# Fetching live proxy hosts ...", file=sys.stderr)
    hosts = req("GET", "/api/nginx/proxy-hosts", token=token)
    print("# Found %d live proxy hosts.\n" % len(hosts), file=sys.stderr)

    hosts.sort(key=lambda h: ((h.get("domain_names") or [""])[0]).lower())

    print("proxies:")
    for h in hosts:
        domains = h.get("domain_names") or []
        if not domains:
            continue
        if len(domains) > 1:
            print("  # NOTE: proxy id %d has multiple domain_names %r; only"
                  " the first is emitted. Add the rest manually if needed."
                  % (h["id"], domains), file=sys.stderr)
        print("  - domain: %s" % domains[0])
        print("    npm_target: %s" % NPM_TARGET)
        print("    forward_host: %s" % h["forward_host"])
        print("    forward_port: %s" % h["forward_port"])
        for key, default in CORE_DEFAULTS.items():
            if h.get(key) != default:
                print("    %s: %s" % (key, yaml_scalar(h[key])))
        for key in OPTIONAL_DEFAULTS:
            emit_optional(h, key)


if __name__ == "__main__":
    main()
