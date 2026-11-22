"""The 3-node count-to-infinity example (F5), run on the real engine.

  python count_to_infinity.py

Topology: A --1-- B --1-- C   (topo_3node.json). Link B-C fails at t=20 and comes
back at t=60. Plain distance-vector (no split horizon) makes A and B bounce the
route to C back and forth, adding 1 each time, until it reaches infinity (16).
The same scenario is then run with split horizon, poison reverse and link-state.

DemoDV below is a small stand-in so the engine and this example can be tested
before dv_node.py (Atta) is ready. It follows contract.md; swap it for the real
node by passing a different class to run_scenario().
"""
import json
import os

from engine import Sim
from ls_node import LSNode
from metrics import Metrics

HERE = os.path.dirname(os.path.abspath(__file__))
TOPO_3NODE = os.path.join(HERE, "topo_3node.json")


class DemoDV:
    """Triggered-update distance-vector node (no periodic timers, so run() drains).

    options: infinity (default 16; cost >= infinity means unreachable and the
    table entry is removed), split_horizon, poison_reverse. Other options are ignored.
    """

    def __init__(self, node_id, sim, infinity=16, split_horizon=False,
                 poison_reverse=False, **_ignored):
        self.id, self.sim = node_id, sim
        self.infinity = infinity
        self.split_horizon = split_horizon
        self.poison_reverse = poison_reverse
        self.vectors = {}      # neighbor -> last vector it sent us {dest: cost}
        self.table = {}        # dest -> (next_hop, cost)

    # ---- engine callbacks -------------------------------------------------
    def on_start(self):
        self._recompute()
        self._advertise()

    def on_message(self, from_id, msg):
        if msg.get("type") != "DV":
            return
        self.vectors[from_id] = dict(msg["vector"])
        if self._recompute():
            self._advertise()

    def on_link_change(self, neighbor_id, cost):
        if cost is None:
            self.vectors.pop(neighbor_id, None)      # forget what the dead neighbor told us
        if self._recompute():
            self._advertise()
        elif cost is not None:                       # link (re)appeared: introduce ourselves
            self._send_vector(neighbor_id)

    # ---- internals --------------------------------------------------------
    def _recompute(self):
        """Rebuild the table from stored vectors. Returns True if it changed."""
        best = {}
        for nbr, link_cost in sorted(self.sim.neighbors(self.id).items()):
            known = {nbr: 0}
            known.update(self.vectors.get(nbr, {}))
            for dest, c in known.items():
                total = link_cost + c
                if dest == self.id or total >= self.infinity:
                    continue
                if dest not in best or total < best[dest][1]:   # ties: smallest neighbor id
                    best[dest] = (nbr, total)
        if best == self.table:
            return False
        self.table = best
        self.sim.report_table(self.id, best)
        return True

    def _vector_for(self, nbr):
        vec = {self.id: 0}
        for dest, (hop, cost) in self.table.items():
            if hop == nbr and (self.split_horizon or self.poison_reverse):
                if self.poison_reverse:
                    vec[dest] = self.infinity        # "do not route to it through me"
                continue                             # split horizon: just leave it out
            vec[dest] = cost
        return vec

    def _send_vector(self, nbr):
        self.sim.send(self.id, nbr, {"type": "DV", "vector": self._vector_for(nbr)})

    def _advertise(self):
        for nbr in sorted(self.sim.neighbors(self.id)):
            self._send_vector(nbr)


class TraceMetrics(Metrics):
    """The normal Metrics plus a log of every table change: (time, node, table)."""

    def __init__(self):
        super().__init__()
        self.changes = []

    def report_table(self, time, node, table):
        super().report_table(time, node, table)
        self.changes.append((time, node, dict(table)))


def run_scenario(node_cls, topo=TOPO_3NODE, link=("B", "C"), t_fail=20.0,
                 t_recover=60.0, until=120.0, **options):
    """Start up, fail `link` at t_fail, recover it at t_recover. Returns (sim, metrics).

    Metrics windows: [0] start-up, [1] after the failure, [2] after the recovery.
    """
    m = TraceMetrics()
    sim = Sim(topo, node_cls, m, **options)
    sim.script([(t_fail, "fail", *link), (t_recover, "recover", *link)])
    sim.run(until=until)
    return sim, m


def route_history(metrics, dest, t_from, t_to, nodes=None):
    """[(time, node, cost or None)] for every table change of `dest` in [t_from, t_to)."""
    out = []
    for t, node, table in metrics.changes:
        if t_from <= t < t_to and (nodes is None or node in nodes):
            out.append((t, node, table[dest][1] if dest in table else None))
    return out


def main():
    topo = json.load(open(TOPO_3NODE))
    print(f"Topology: {len(topo['nodes'])} nodes, {len(topo['links'])} links; "
          "link B-C fails at t=20, recovers at t=60\n")

    _, m = run_scenario(DemoDV)
    print("Plain distance-vector: A's and B's route to C after B-C fails")
    for t, node, cost in route_history(m, "C", 20.0, 60.0):
        print(f"  t={t:5.1f}  {node}: " + (f"cost {cost}" if cost is not None else "unreachable (removed)"))

    variants = [
        ("DV, plain", DemoDV, {}),
        ("DV, split horizon", DemoDV, {"split_horizon": True}),
        ("DV, poison reverse", DemoDV, {"poison_reverse": True}),
        ("Link-state", LSNode, {"spf_holddown": 0.1}),
    ]
    print("\nAfter the failure at t=20 (convergence = time of the last table change)")
    print(f"  {'method':20} {'conv. time':>10} {'messages':>9} {'table changes':>14} {'max cost seen':>14}")
    for name, cls, opts in variants:
        _, m = run_scenario(cls, **opts)
        w = m.summary()[1]
        print(f"  {name:20} {w['convergence_time']:>10} {w['messages']:>9} "
              f"{w['table_changes']:>14} {w['max_cost_seen']:>14}")


if __name__ == "__main__":
    main()