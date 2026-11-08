"""Checks LSNode against NetworkX on random graphs, through cost changes, failures
(including ones that partition the graph) and recoveries."""
import random
import time
import networkx as nx
from fake_sim import FakeSim
from ls_node import LSNode
from metrics import Metrics


def random_graph(n, extra, rng):
    G = nx.Graph()
    nodes = [str(i) for i in range(n)]
    G.add_nodes_from(nodes)
    for i in range(1, n):
        G.add_edge(nodes[i], nodes[rng.randrange(i)], weight=rng.randint(1, 10))
    while extra > 0:
        a, b = rng.sample(nodes, 2)
        if not G.has_edge(a, b):
            G.add_edge(a, b, weight=rng.randint(1, 10))
            extra -= 1
    return G


def to_topo(G):
    return {"nodes": list(G.nodes), "links": [{"a": a, "b": b, "cost": d["weight"], "delay": 1.0}
                                              for a, b, d in G.edges(data=True)]}


def check(sim, G, where):
    for src, node in sim.nodes.items():
        want = {d: c for d, c in nx.single_source_dijkstra_path_length(G, src, weight="weight").items() if d != src}
        got = node.table
        assert set(got) == set(want), f"{where}: node {src} reachable set differs"
        for dest, (nh, cost) in got.items():
            assert cost == want[dest], f"{where}: {src}->{dest} cost {cost} != {want[dest]}"
            assert G.has_edge(src, nh), f"{where}: {src} next hop {nh} not a neighbor"
            rest = nx.dijkstra_path_length(G, nh, dest, weight="weight")
            assert G[src][nh]["weight"] + rest == cost, f"{where}: {src}->{dest} next hop {nh} is not on a shortest path"


def run_case(seed, n, extra, events=6):
    rng = random.Random(seed)
    G = random_graph(n, extra, rng)
    m = Metrics()
    sim = FakeSim(to_topo(G), LSNode, m)
    sim.start(); sim.run()
    check(sim, G, f"seed{seed} start")
    down = []
    for e in range(events):
        kind = rng.choice(["cost", "fail", "recover"] if down else ["cost", "fail"])
        if kind == "cost":
            a, b = rng.choice(list(G.edges))
            c = rng.randint(1, 20)
            G[a][b]["weight"] = c
            sim.set_cost(a, b, c)
        elif kind == "fail":
            a, b = rng.choice(list(G.edges))
            down.append((a, b, G[a][b]["weight"]))
            G.remove_edge(a, b)
            sim.fail(a, b)
        else:
            a, b, c = down.pop(rng.randrange(len(down)))
            G.add_edge(a, b, weight=c)
            sim.recover(a, b)
        sim.run()
        check(sim, G, f"seed{seed} event{e} {kind}")
    return m.summary()


if __name__ == "__main__":
    for seed in range(40):
        run_case(seed, n=random.Random(seed).randint(4, 25), extra=random.Random(seed).randint(0, 20))
    print("40 random graphs x 6 events each: all tables match NetworkX")
    t = time.time()
    s = run_case(1, n=200, extra=400, events=3)
    print(f"200-node run ok in {time.time() - t:.1f}s")
    for w in s:
        print(w)
