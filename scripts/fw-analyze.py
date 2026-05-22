#!/usr/bin/env python3
"""
Analyze firewalld rich rules to surface structure.

A long rich-rules list is usually a matrix: N source networks times M
ports/services, with most rules duplicated across every source. This
script parses the rules, groups them by source, and separates ports
shared across all sources from ports specific to one source - the
input you need before deciding whether to collapse into an ipset or
split into multiple firewalld zones.

Usage:
  # From a file (output of firewall-cmd --zone=X --list-rich-rules)
  python3 scripts/fw-analyze.py rules.txt

  # Or from stdin (numbered [nn] prefix from fw-audit is tolerated)
  ssh primary-dns 'sudo firewall-cmd --zone=public --list-rich-rules' \\
    | python3 scripts/fw-analyze.py

  # Or paste the fw-audit job output into a file and pipe it in.
"""
import re
import sys
from collections import defaultdict

# rule family="ipv4" source address="X" port port="Y" protocol="tcp|udp" accept
# rule family="ipv4" source address="X" service name="N" accept
RE_RULE = re.compile(
    r'rule\s+family="(?P<family>[^"]+)"\s+'
    r'source\s+address="(?P<source>[^"]+)"\s+'
    r'(?:'
    r'(?:port\s+port="(?P<port>[^"]+)"\s+protocol="(?P<proto>[^"]+)")'
    r'|'
    r'(?:service\s+name="(?P<service>[^"]+)"))'
    r'\s+(?P<action>accept|drop|reject|mark)'
)

# Tolerate "[  3] rule ..." from fw-audit output or " rule ..." indented
RE_STRIP_PREFIX = re.compile(r'^\s*(?:\[\s*\d+\]\s*)?')


def port_sort_key(p):
    # Handle ranges like "30000-32767" by sorting on the low end.
    return int(str(p).split('-')[0])


def join_ports(ports):
    return ', '.join(sorted(ports, key=port_sort_key))


def main():
    text = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()

    per_source_ports = defaultdict(set)
    per_source_services = defaultdict(set)
    all_sources = set()
    unparsed = []

    for raw in text.splitlines():
        line = RE_STRIP_PREFIX.sub('', raw).strip()
        if not line or not line.startswith('rule'):
            continue
        m = RE_RULE.match(line)
        if not m:
            unparsed.append(line)
            continue
        src = m.group('source')
        all_sources.add(src)
        if m.group('service'):
            per_source_services[src].add(m.group('service'))
        else:
            per_source_ports[src].add((m.group('port'), m.group('proto')))

    if not all_sources:
        print("No rich rules parsed. Did you pass the right file?", file=sys.stderr)
        sys.exit(1)

    shared_ports = set.intersection(*(per_source_ports[s] for s in all_sources))
    shared_services = set.intersection(*(per_source_services[s] for s in all_sources))

    # Overview
    print("=== Sources (%d) ===" % len(all_sources))
    for s in sorted(all_sources):
        print("  %-20s  %3d port rules   %2d service rules" % (
            s, len(per_source_ports[s]), len(per_source_services[s])))
    print()

    # Shared: candidates for a single collapsed rule (ipset / trusted zone)
    print("=== Shared across ALL %d sources ===" % len(all_sources))
    print("   (each of these is duplicated %dx; collapse first)" % len(all_sources))
    if shared_services:
        print("  services: %s" % ', '.join(sorted(shared_services)))
    shared_tcp = [p for p, proto in shared_ports if proto == 'tcp']
    shared_udp = [p for p, proto in shared_ports if proto == 'udp']
    if shared_tcp:
        print("  tcp: %s" % join_ports(shared_tcp))
    if shared_udp:
        print("  udp: %s" % join_ports(shared_udp))
    print()

    # Source-specific: the stuff that genuinely needs per-source scoping
    print("=== Source-specific access (not in shared set) ===")
    for src in sorted(all_sources):
        uniq_ports = per_source_ports[src] - shared_ports
        uniq_services = per_source_services[src] - shared_services
        if not uniq_ports and not uniq_services:
            continue
        print("\n  %s only:" % src)
        if uniq_services:
            print("    services: %s" % ', '.join(sorted(uniq_services)))
        u_tcp = [p for p, proto in uniq_ports if proto == 'tcp']
        u_udp = [p for p, proto in uniq_ports if proto == 'udp']
        if u_tcp:
            print("    tcp: %s" % join_ports(u_tcp))
        if u_udp:
            print("    udp: %s" % join_ports(u_udp))
    print()

    # Headline
    total_rules = sum(len(v) for v in per_source_ports.values()) + \
                  sum(len(v) for v in per_source_services.values())
    after_collapse = (len(shared_ports) + len(shared_services)) + \
                     sum(len(per_source_ports[s] - shared_ports) +
                         len(per_source_services[s] - shared_services)
                         for s in all_sources)
    print("=== Summary ===")
    print("  Current rich rules : %d" % total_rules)
    print("  After collapsing shared set into one source-group: %d" % after_collapse)
    print("  Reduction          : %d rules" % (total_rules - after_collapse))

    if unparsed:
        print("\n=== Unparsed rules (%d) ===" % len(unparsed))
        for l in unparsed[:10]:
            print("  %s" % l)
        if len(unparsed) > 10:
            print("  ... and %d more" % (len(unparsed) - 10))


if __name__ == "__main__":
    main()
