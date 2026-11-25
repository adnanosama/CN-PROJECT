"""Random connected topology generator (F4).

  python graphgen.py 50 --seed 1 --out topo_50.json     # one graph
  python graphgen.py --standard                         # topo_10.json, topo_50.json, topo_200.json

How a graph is built:
  1. Random spanning tree (every new node attaches to a random earlier node),
     so the graph is always connected.
  2. Random extra links are added until the target link count is reached:
     round(n * avg_degree / 2), at least n-1, at most a complete graph.

Same (n, avg_degree, seed, ...) always gives exactly the same file, so
experiments can be repeated. Node ids are zero-padded ("R001" .. "R200") so
sorted order is numeric order. Costs are random integers in cost_range; delay is
1.0 on every link by default, which keeps convergence time and rounds equivalent
(see the contract notes). Pass delay_range=(lo, hi) for mixed delays.
"""
import argparse
import os
import random

from topology import save_topology, validate_topology

STANDARD_SIZES = (10, 50, 200)


def random_topology(n, avg_degree=3.0, seed=None, cost_range=(1, 10), delay=1.0,
                    delay_range=None):
    """Return a validated, connected topology dict with n nodes."""
    if not isinstance(n, int) or n < 2:
        raise ValueError("n must be an integer >= 2")
    lo, hi = cost_range
    if not (isinstance(lo, int) and isinstance(hi, int) and 1 <= lo <= hi):
        raise ValueError("cost_range must be two integers with 1 <= lo <= hi")
    if avg_degree <= 0:
        raise ValueError("avg_degree must be > 0")

    rng = random.Random(seed)
    width = len(str(n))
    ids = [f"R{i + 1:0{width}d}" for i in range(n)]
    target = min(n * (n - 1) // 2, max(n - 1, round(n * avg_degree / 2)))

    order = ids[:]
    rng.shuffle(order)
    edges, seen = [], set()

    def add(a, b):
        key = tuple(sorted((a, b)))
        if key in seen:
            return False
        seen.add(key)
        edges.append(key)
        return True

    for i in range(1, n):                       # 1. spanning tree
        add(order[i], order[rng.randrange(i)])
    while len(edges) < target:                  # 2. extra links
        a, b = rng.sample(ids, 2)
        add(a, b)

    links = []
    for a, b in sorted(edges):                  # sorted -> stable file contents
        d = delay if delay_range is None else round(rng.uniform(*delay_range), 3)
        links.append({"a": a, "b": b, "cost": rng.randint(lo, hi), "delay": d})
    return validate_topology({"nodes": ids, "links": links}, require_connected=True)


def write_standard_set(out_dir=".", seed=1, **kwargs):
    """Write topo_10.json, topo_50.json and topo_200.json. Returns the paths."""
    paths = []
    for n in STANDARD_SIZES:
        path = os.path.join(out_dir, f"topo_{n}.json")
        save_topology(random_topology(n, seed=seed, **kwargs), path)
        paths.append(path)
    return paths


def main():
    p = argparse.ArgumentParser(description="Random connected topology generator")
    p.add_argument("n", nargs="?", type=int, help="number of nodes")
    p.add_argument("--standard", action="store_true", help="write the 10/50/200-node set")
    p.add_argument("--avg-degree", type=float, default=3.0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--max-cost", type=int, default=10)
    p.add_argument("--out", help="output file for a single graph (default topo_<n>.json)")
    a = p.parse_args()
    kw = {"avg_degree": a.avg_degree, "cost_range": (1, a.max_cost)}
    if a.standard:
        for path in write_standard_set(".", a.seed, **kw):
            print("wrote", path)
    elif a.n:
        topo = random_topology(a.n, seed=a.seed, **kw)
        path = a.out or f"topo_{a.n}.json"
        save_topology(topo, path)
        print(f"wrote {path}: {len(topo['nodes'])} nodes, {len(topo['links'])} links")
    else:
        p.error("give a node count or --standard")


if __name__ == "__main__":
    main()