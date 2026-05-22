#!/usr/bin/env python3
"""
One-shot NPM proxy host config regenerator.

Context: if NPM's SQLite DB has live proxy_host rows (is_deleted=0) but
the corresponding /data/nginx/proxy_host/<id>.conf files are missing (as
happens after a DB soft-delete restore), nginx has no server blocks and
every SNI returns ERR_SSL_UNRECOGNIZED_NAME_ALERT.

NPM only writes nginx configs on create/update API calls, not on startup
and not in response to DB-level changes. This script forces regeneration
by PUTting every live proxy host back to its current state, which
triggers NPM's internal config write + nginx reload for each.

Prompts for admin credentials; no secrets are read from disk or env.
"""
import getpass
import json
import sys
import urllib.request
import urllib.error

API = "http://10.0.0.19:81"

# Fields returned by GET that PUT rejects or that are server-owned.
# Matches the STRIP list in library/npm_proxy.py.
STRIP = {
    "id", "created_on", "modified_on", "owner_user_id", "owner",
    "certificate", "access_list", "use_default_location", "enabled", "ipv6",
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


def main():
    email = input("NPM email: ").strip()
    password = getpass.getpass("NPM password: ")

    print("Authenticating against %s ..." % API)
    try:
        token = req("POST", "/api/tokens",
                    body={"identity": email, "secret": password})["token"]
    except urllib.error.HTTPError as e:
        sys.exit("Auth failed: HTTP %d %s" % (e.code, e.reason))

    print("Fetching live proxy hosts ...")
    hosts = req("GET",
                "/api/nginx/proxy-hosts?expand=owner,access_list,certificate",
                token=token)
    print("Found %d live proxy hosts.\n" % len(hosts))

    errors = []
    for h in hosts:
        domain = (h.get("domain_names") or ["<unknown>"])[0]
        payload = {k: v for k, v in h.items() if k not in STRIP}
        try:
            req("PUT", "/api/nginx/proxy-hosts/%d" % h["id"],
                token=token, body=payload)
            print("  ok:     %s" % domain)
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", errors="replace")[:300]
            print("  FAIL:   %s -- HTTP %d: %s" % (domain, e.code, msg))
            errors.append(domain)
        except Exception as e:
            print("  FAIL:   %s -- %s" % (domain, e))
            errors.append(domain)

    print("\n%d succeeded, %d failed." % (len(hosts) - len(errors), len(errors)))
    if errors:
        print("Failed domains:")
        for d in errors:
            print("  - " + d)
        sys.exit(1)


if __name__ == "__main__":
    main()
