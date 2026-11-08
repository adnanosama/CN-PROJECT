#!/usr/bin/env python3
"""Compare simulator LS tables with FRR OSPF tables by total path cost.
Usage: python3 compare_ospf.py sim_tables.json frr_tables.json [--stub-cost 1]

FRR's metric to a router's loopback includes the loopback's own stub cost, so we
subtract it. We check that the offset really is constant instead of assuming it."""
import argparse
import json
from collections import Counter

p = argparse.ArgumentParser()
p.add_argument("sim"); p.add_argument("frr"); p.add_argument("--stub-cost", type=int, default=1)
a = p.parse_args()
sim, frr = json.load(open(a.sim)), json.load(open(a.frr))

diffs, rows, missing = [], [], 0
for src, table in sim.items():
    for dest, (nh, cost) in table.items():
        f = frr.get(src, {}).get(dest)
        if f is None:
            missing += 1
            rows.append((src, dest, cost, None, None, "missing in FRR"))
            continue
        d = f["cost"] - cost
        diffs.append(d)
        ok_cost = d == a.stub_cost
        ok_hop = nh in f["next_hops"]            # ECMP: any listed next hop is acceptable
        rows.append((src, dest, cost, f["cost"], nh, "ok" if ok_cost and ok_hop else
                     ("COST differs" if not ok_cost else "next hop differs")))
total = len(rows)
good = sum(r[5] == "ok" for r in rows)
print(f"{good}/{total} routes agree on cost and next hop ({100 * good / max(total, 1):.1f}%)")
if diffs:
    mode, _ = Counter(diffs).most_common(1)[0]
    if mode != a.stub_cost:
        print(f"WARNING: most common FRR-minus-sim offset is {mode}, not {a.stub_cost}. "
              f"Check the loopback cost with 'show ip ospf interface lo' and rerun with --stub-cost {mode}.")
for r in rows:
    if r[5] != "ok":
        print("  ", r)
