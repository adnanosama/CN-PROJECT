"""Tests for graphgen.py and the count-to-infinity scenario, plus end-to-end runs of the
engine with real protocol nodes on generated graphs.   Run: python test_scenarios.py"""
import heapq
import json
import os
import random
import tempfile
import unittest

from count_to_infinity import DemoDV, TOPO_3NODE, route_history, run_scenario
from engine import Sim
from graphgen import random_topology, write_standard_set
from ls_node import LSNode
from metrics import Metrics
from topology import is_connected, load_topology


def true_costs(topo, src, skip=()):
    """Independent Dijkstra: {dest: cost} from src, ignoring links in `skip` (as frozensets)."""
    adj = {n: [] for n in topo["nodes"]}
    for l in topo["links"]:
        if frozenset((l["a"], l["b"])) not in skip:
            adj[l["a"]].append((l["b"], l["cost"]))
            adj[l["b"]].append((l["a"], l["cost"]))
    dist, heap = {}, [(0, src)]
    while heap:
        d, u = heapq.heappop(heap)
        if u in dist:
            continue
        dist[u] = d
        for v, c in adj[u]:
            heapq.heappush(heap, (d + c, v))
    dist.pop(src)
    return dist


def check_tables(test, sim, topo, skip=()):
    """Every node's table must hold exactly the true shortest-path costs, and each
    next hop must be a live neighbor that lies on a shortest path."""
    cost = {frozenset((l["a"], l["b"])): l["cost"] for l in topo["links"]}
    truth = {n: true_costs(topo, n, skip) for n in topo["nodes"]}      # computed once per node
    for src, node in sim.nodes.items():
        want = truth[src]
        test.assertEqual(set(node.table), set(want), f"{src}: reachable set differs")
        for dest, (hop, c) in node.table.items():
            test.assertEqual(c, want[dest], f"{src}->{dest}")
            link = frozenset((src, hop))
            test.assertTrue(link in cost and link not in skip, f"{src}: bad next hop {hop}")
            rest = 0 if hop == dest else truth[hop][dest]
            test.assertEqual(cost[link] + rest, c, f"{src}->{dest} via {hop} is not shortest")


class GraphGenTests(unittest.TestCase):
    def test_standard_sizes_are_valid_and_connected(self):
        for n in (10, 50, 200):
            topo = random_topology(n, seed=1)
            self.assertEqual(len(topo["nodes"]), n)
            self.assertTrue(is_connected(topo))
            load_topology(topo, require_connected=True)
            self.assertGreaterEqual(len(topo["links"]), n - 1)

    def test_link_count_follows_avg_degree(self):
        for n in (10, 50, 200):
            topo = random_topology(n, avg_degree=3.0, seed=2)
            self.assertEqual(len(topo["links"]), round(n * 3.0 / 2))

    def test_minimum_is_a_tree_and_maximum_is_complete(self):
        self.assertEqual(len(random_topology(20, avg_degree=0.1, seed=3)["links"]), 19)
        self.assertEqual(len(random_topology(6, avg_degree=100, seed=3)["links"]), 15)

    def test_same_seed_same_graph_different_seed_different_graph(self):
        a, b, c = (random_topology(50, seed=s) for s in (7, 7, 8))
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_costs_delays_and_ids(self):
        topo = random_topology(200, seed=4, cost_range=(2, 5))
        self.assertEqual(topo["nodes"][0], "R001")
        self.assertEqual(topo["nodes"], sorted(topo["nodes"]))
        for l in topo["links"]:
            self.assertTrue(2 <= l["cost"] <= 5)
            self.assertEqual(l["delay"], 1.0)
        mixed = random_topology(30, seed=4, delay_range=(0.5, 2.0))
        self.assertTrue(all(0.5 <= l["delay"] <= 2.0 for l in mixed["links"]))
        self.assertGreater(len({l["delay"] for l in mixed["links"]}), 1)

    def test_bad_arguments(self):
        for kw in ({"n": 1}, {"n": 5, "avg_degree": 0}, {"n": 5, "cost_range": (0, 3)},
                   {"n": 5, "cost_range": (5, 2)}):
            with self.assertRaises(ValueError):
                random_topology(**kw)

    def test_write_standard_set(self):
        with tempfile.TemporaryDirectory() as d:
            paths = write_standard_set(d, seed=1)
            self.assertEqual([os.path.basename(p) for p in paths],
                             ["topo_10.json", "topo_50.json", "topo_200.json"])
            with open(paths[1]) as f:
                self.assertEqual(json.load(f), random_topology(50, seed=1))


class CountToInfinityTests(unittest.TestCase):
    def cost_sequence(self, m):
        return [c for _, node, c in route_history(m, "C", 20.0, 60.0, nodes={"A", "B"})]

    def test_plain_dv_counts_up_to_infinity(self):
        _, m = run_scenario(DemoDV)
        seq = self.cost_sequence(m)
        self.assertEqual(seq[:6], [3, 4, 5, 6, 7, 8])        # +1 every time step
        self.assertEqual(seq[-3:], [15, None, None])
        self.assertEqual(max(c for c in seq if c is not None), 15)
        self.assertEqual(seq[-2:], [None, None])             # both give up at infinity (16)
        self.assertEqual(m.summary()[1]["convergence_time"], 14.0)
        self.assertEqual(m.summary()[1]["max_cost_seen"], 15)

    def test_plain_dv_alternates_between_a_and_b(self):
        _, m = run_scenario(DemoDV)
        who = [n for _, n, _ in route_history(m, "C", 20.0, 60.0, nodes={"A", "B"})]
        self.assertEqual(who[:6], ["B", "A", "B", "A", "B", "A"])

    def test_split_horizon_and_poison_reverse_avoid_counting(self):
        for opts in ({"split_horizon": True}, {"poison_reverse": True}):
            _, m = run_scenario(DemoDV, **opts)
            w = m.summary()[1]
            self.assertLessEqual(w["convergence_time"], 2.0, opts)
            self.assertLessEqual(w["max_cost_seen"], 2, opts)

    def test_link_state_converges_in_about_the_spf_holddown(self):
        _, m = run_scenario(LSNode, spf_holddown=0.1)
        self.assertLessEqual(m.summary()[1]["convergence_time"], 1.5)

    def test_route_to_c_is_back_after_recovery(self):
        for cls, opts in ((DemoDV, {}), (DemoDV, {"split_horizon": True}),
                          (LSNode, {"spf_holddown": 0.1})):
            sim, _ = run_scenario(cls, **opts)
            self.assertEqual(sim.nodes["A"].table["C"], ("B", 2), cls.__name__)
            self.assertEqual(sim.nodes["C"].table["A"], ("B", 2), cls.__name__)

    def test_metrics_windows_start_fail_recover(self):
        _, m = run_scenario(DemoDV)
        self.assertEqual([w["label"] for w in m.summary()],
                         ["start", "fail B-C", "recover B-C"])

    def test_topo_3node_file_is_the_example(self):
        topo = load_topology(TOPO_3NODE, require_connected=True)
        self.assertEqual(sorted(topo["nodes"]), ["A", "B", "C"])
        self.assertEqual({frozenset((l["a"], l["b"])) for l in topo["links"]},
                         {frozenset("AB"), frozenset("BC")})


class ProtocolsOnGeneratedGraphs(unittest.TestCase):
    """End-to-end: engine + a real protocol + generated graph, checked against Dijkstra."""

    def build(self, cls, topo, **opts):
        sim = Sim(topo, cls, Metrics(), **opts)
        sim.run()
        return sim

    def test_dv_matches_dijkstra_at_startup_10_50(self):
        for n in (10, 50):
            topo = random_topology(n, seed=5)
            check_tables(self, self.build(DemoDV, topo, infinity=10 ** 6), topo)

    def test_ls_matches_dijkstra_at_startup_10_50_200(self):
        for n in (10, 50, 200):
            topo = random_topology(n, seed=6)
            check_tables(self, self.build(LSNode, topo), topo)

    def test_dv_and_ls_survive_a_failure_and_a_recovery(self):
        topo = random_topology(50, seed=9)
        rng = random.Random(9)
        links = [(l["a"], l["b"]) for l in topo["links"]]
        rng.shuffle(links)
        a, b = next((a, b) for a, b in links      # a link whose loss keeps the graph connected
                    if is_connected({"nodes": topo["nodes"], "links": [
                        l for l in topo["links"] if frozenset((l["a"], l["b"])) != frozenset((a, b))]}))
        for cls, opts in ((DemoDV, {"infinity": 10 ** 6}), (LSNode, {})):
            sim = self.build(cls, topo, **opts)
            sim.fail(a, b)
            sim.run()
            check_tables(self, sim, topo, skip={frozenset((a, b))})
            sim.recover(a, b)
            sim.run()
            check_tables(self, sim, topo)


if __name__ == "__main__":
    unittest.main()