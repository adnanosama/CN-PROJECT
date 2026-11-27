#!/usr/bin/env python3
"""OSPF lab helper (F7, OSPF half). Needs Linux + Docker + containerlab. Not yet run against a live lab.

  python3 ospf_tools.py gen ../topo_demo.json lab          # writes lab/ (containerlab file + FRR configs)
  sudo containerlab deploy -t lab/ospf-lab.clab.yml
  python3 ospf_tools.py extract lab/lab_meta.json frr_tables.json
  python3 ospf_tools.py fail lab/lab_meta.json A B         # take link A-B down, time convergence
  python3 ospf_tools.py recover lab/lab_meta.json A B      # bring it back, time convergence
  python3 ospf_tools.py cost lab/lab_meta.json A B 20      # change the OSPF cost on both ends, time convergence
  python3 ospf_tools.py bench lab/lab_meta.json A B --runs 3   # fail/recover/cost up/cost down x3, mean and spread
"""
import argparse
import statistics
import ipaddress
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

IMAGE = "quay.io/frrouting/frr:10.1.1"    # check this tag exists, or pick another from the FRR docs
LAB = "ospf-lab"
LO_COST = 1

DAEMONS = """zebra=yes
bgpd=no
ospfd=yes
ospf6d=no
ripd=no
ripngd=no
isisd=no
pimd=no
ldpd=no
nhrpd=no
eigrpd=no
babeld=no
sharpd=no
pbrd=no
bfdd=no
fabricd=no
vrrpd=no
pathd=no
vtysh_enable=yes
zebra_options="  -A 127.0.0.1 -s 90000000"
ospfd_options="  -A 127.0.0.1"
"""


def gen(topo_path, out, image):
    topo = json.load(open(topo_path))
    nodes = sorted(topo["nodes"])
    name = {n: f"r{i + 1}" for i, n in enumerate(nodes)}
    loop = {n: f"10.255.0.{i + 1}" for i, n in enumerate(nodes)}
    ifaces = {n: [] for n in nodes}
    links_meta, ip_to_node = [], {}
    for j, l in enumerate(topo["links"]):
        base = int(ipaddress.IPv4Address("10.0.0.0")) + 4 * j
        ip = {l["a"]: str(ipaddress.IPv4Address(base + 1)), l["b"]: str(ipaddress.IPv4Address(base + 2))}
        ifn = {}
        for end in (l["a"], l["b"]):
            ifn[end] = f"eth{len(ifaces[end]) + 1}"
            ifaces[end].append((ifn[end], ip[end], l["cost"]))
            ip_to_node[ip[end]] = end
        links_meta.append({"a": l["a"], "b": l["b"], "if_a": ifn[l["a"]], "if_b": ifn[l["b"]], "cost": l["cost"]})
    os.makedirs(out, exist_ok=True)
    yml = [f"name: {LAB}", "topology:", "  kinds:", "    linux:", f"      image: {image}", "  nodes:"]
    for n in nodes:
        d = os.path.join(out, name[n])
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "daemons"), "w").write(DAEMONS)
        open(os.path.join(d, "vtysh.conf"), "w").write("service integrated-vtysh-config\n")
        conf = [f"frr defaults traditional", f"hostname {name[n]}", "log stdout", "!",
                "interface lo", f" ip ospf cost {LO_COST}", "!"]
        for ifname, _ip, cost in ifaces[n]:
            conf += [f"interface {ifname}", " ip ospf network point-to-point", f" ip ospf cost {cost}",
                     " ip ospf hello-interval 1", " ip ospf dead-interval 4", "!"]
        conf += ["router ospf", f" ospf router-id {loop[n]}", " network 10.0.0.0/16 area 0",
                 f" network {loop[n]}/32 area 0", " timers throttle spf 50 100 200", "!"]
        open(os.path.join(d, "frr.conf"), "w").write("\n".join(conf) + "\n")
        yml += [f"    {name[n]}:", "      kind: linux", "      binds:",
                f"        - {name[n]}/daemons:/etc/frr/daemons",
                f"        - {name[n]}/frr.conf:/etc/frr/frr.conf",
                f"        - {name[n]}/vtysh.conf:/etc/frr/vtysh.conf", "      exec:"]
        yml += [f"        - ip addr add {ip}/30 dev {ifname}" for ifname, ip, _c in ifaces[n]]
        yml += [f"        - ip addr add {loop[n]}/32 dev lo"]
    yml += ["  links:"]
    for lm in links_meta:
        yml.append(f'    - endpoints: ["{name[lm["a"]]}:{lm["if_a"]}", "{name[lm["b"]]}:{lm["if_b"]}"]')
    open(os.path.join(out, f"{LAB}.clab.yml"), "w").write("\n".join(yml) + "\n")
    meta = {"lab": LAB, "name": name, "loopback_to_node": {f"{loop[n]}/32": n for n in nodes},
            "ip_to_node": ip_to_node, "links": links_meta, "lo_cost": LO_COST}
    json.dump(meta, open(os.path.join(out, "lab_meta.json"), "w"), indent=1)
    print(f"wrote {out}/ ({len(nodes)} routers, {len(links_meta)} links)")


def vtysh(meta, node, cmd):
    r = subprocess.run(["docker", "exec", f"clab-{meta['lab']}-{meta['name'][node]}", "vtysh", "-c", cmd],
                       capture_output=True, text=True)
    return r.stdout


def snapshot(meta):
    nodes = list(meta["name"])

    def one(n):
        data = json.loads(vtysh(meta, n, "show ip route ospf json") or "{}")
        tab = {}
        for prefix, routes in data.items():
            dest = meta["loopback_to_node"].get(prefix)
            if dest is None:
                continue
            r = routes[0]
            nhs = sorted({meta["ip_to_node"].get(h.get("ip")) for h in r.get("nexthops", []) if h.get("ip")} - {None})
            tab[dest] = {"next_hops": nhs, "cost": r["metric"]}
        return n, tab

    with ThreadPoolExecutor(max_workers=len(nodes)) as ex:
        return dict(ex.map(one, nodes))


def _link(meta, a, b):
    return next(l for l in meta["links"] if {l["a"], l["b"]} == {a, b})


def _docker(meta, node, *cmd):
    subprocess.run(["docker", "exec", f"clab-{meta['lab']}-{meta['name'][node]}", *cmd], check=True)


def do_fail(meta, a, b):
    lk = _link(meta, a, b)
    _docker(meta, lk["a"], "ip", "link", "set", lk["if_a"], "down")


def do_recover(meta, a, b):
    lk = _link(meta, a, b)
    _docker(meta, lk["a"], "ip", "link", "set", lk["if_a"], "up")


def do_cost(meta, a, b, cost):
    lk = _link(meta, a, b)
    for node, ifn in ((lk["a"], lk["if_a"]), (lk["b"], lk["if_b"])):
        _docker(meta, node, "vtysh", "-c", "configure terminal", "-c", f"interface {ifn}",
                "-c", f"ip ospf cost {cost}")


def timed(meta, action, quiet):
    """Run action(), then poll until the routing tables stop changing for `quiet` seconds.
    Returns seconds from the action to the last table change."""
    prev = snapshot(meta)
    t0 = time.time()
    action()
    last = t0
    while time.time() - last < quiet:
        cur = snapshot(meta)
        if cur != prev:
            last, prev = time.time(), cur
        time.sleep(0.1)
    return last - t0


def bench(meta, a, b, runs, quiet, bump, out):
    orig = _link(meta, a, b)["cost"]
    do_recover(meta, a, b)                   # make sure the link starts up
    do_cost(meta, a, b, orig)
    time.sleep(quiet)
    steps = [("fail", lambda: do_fail(meta, a, b)),
             ("recover", lambda: do_recover(meta, a, b)),
             ("cost_up", lambda: do_cost(meta, a, b, orig * bump)),
             ("cost_down", lambda: do_cost(meta, a, b, orig))]
    res = {name: [] for name, _ in steps}
    for i in range(runs):
        line = []
        for name, fn in steps:
            t = round(timed(meta, fn, quiet), 3)
            res[name].append(t)
            line.append(f"{name} {t}s")
        print(f"run {i + 1}: " + ", ".join(line))
    summary = {n: {"runs": v, "mean": round(statistics.mean(v), 3),
                   "stdev": round(statistics.stdev(v), 3) if len(v) > 1 else 0.0} for n, v in res.items()}
    for n, s in summary.items():
        print(f"{n:10s} mean {s['mean']}s  stdev {s['stdev']}s")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump({"link": [a, b], "orig_cost": orig, "bump": bump, "quiet_s": quiet, "results": summary},
              open(out, "w"), indent=1)
    print("saved", out)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen"); g.add_argument("topo"); g.add_argument("out"); g.add_argument("--image", default=IMAGE)
    e = sub.add_parser("extract"); e.add_argument("meta"); e.add_argument("out")
    for name in ("fail", "recover", "cost", "bench"):
        c = sub.add_parser(name); c.add_argument("meta"); c.add_argument("a"); c.add_argument("b")
        c.add_argument("--quiet", type=float, default=8.0, help="seconds without change before we call it converged")
        if name == "cost":
            c.add_argument("new_cost", type=int)
        if name == "bench":
            c.add_argument("--runs", type=int, default=3)
            c.add_argument("--bump", type=int, default=5, help="cost_up sets the cost to original x this")
            c.add_argument("--out", default="results/ospf_bench.json")
    a = p.parse_args()
    if a.cmd == "gen":
        gen(a.topo, a.out, a.image)
    elif a.cmd == "extract":
        json.dump(snapshot(json.load(open(a.meta))), open(a.out, "w"), indent=2)
        print("saved", a.out)
    else:
        meta = json.load(open(a.meta))
        if a.cmd == "bench":
            bench(meta, a.a, a.b, a.runs, a.quiet, a.bump, a.out)
        else:
            act = {"fail": lambda: do_fail(meta, a.a, a.b), "recover": lambda: do_recover(meta, a.a, a.b),
                   "cost": lambda: do_cost(meta, a.a, a.b, getattr(a, "new_cost", 0))}[a.cmd]
            t = timed(meta, act, a.quiet)
            print(f"{a.cmd} {a.a}-{a.b}: tables stopped changing {t:.2f}s after the action "
                  f"(polling granularity is roughly the time one snapshot takes)")