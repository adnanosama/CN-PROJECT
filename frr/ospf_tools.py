#!/usr/bin/env python3
"""OSPF lab helper (F7, OSPF half). Needs Linux + Docker + containerlab. Not yet run against a live lab.

  python3 ospf_tools.py gen ../topo_demo.json lab          # writes lab/ (containerlab file + FRR configs)
  sudo containerlab deploy -t lab/ospf-lab.clab.yml
  python3 ospf_tools.py extract lab/lab_meta.json frr_tables.json
  python3 ospf_tools.py fail lab/lab_meta.json A B         # take link A-B down, time convergence
"""
import argparse
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


def fail(meta, a, b, quiet):
    lk = next(l for l in meta["links"] if {l["a"], l["b"]} == {a, b})
    before = snapshot(meta)
    t0 = time.time()
    subprocess.run(["docker", "exec", f"clab-{meta['lab']}-{meta['name'][lk['a']]}",
                    "ip", "link", "set", lk["if_a"], "down"], check=True)
    last_change, prev = t0, before
    while time.time() - last_change < quiet:
        cur = snapshot(meta)
        if cur != prev:
            last_change, prev = time.time(), cur
        time.sleep(0.1)
    print(f"link {a}-{b} down: tables stopped changing {last_change - t0:.2f}s after the failure "
          f"(polling granularity is roughly the time one snapshot takes)")
    return prev


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen"); g.add_argument("topo"); g.add_argument("out"); g.add_argument("--image", default=IMAGE)
    e = sub.add_parser("extract"); e.add_argument("meta"); e.add_argument("out")
    f = sub.add_parser("fail"); f.add_argument("meta"); f.add_argument("a"); f.add_argument("b")
    f.add_argument("--quiet", type=float, default=8.0, help="seconds without change before we call it converged")
    a = p.parse_args()
    if a.cmd == "gen":
        gen(a.topo, a.out, a.image)
    elif a.cmd == "extract":
        json.dump(snapshot(json.load(open(a.meta))), open(a.out, "w"), indent=1)
        print("saved", a.out)
    else:
        fail(json.load(open(a.meta)), a.a, a.b, a.quiet)