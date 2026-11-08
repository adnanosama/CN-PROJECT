"""Tiny stand-in for the real engine (Zaid's F1), for testing F3/F6 on my own.
It implements only what contract.md says the real engine provides."""
import heapq
import itertools


class FakeSim:
    def __init__(self, topo, node_cls, metrics=None, **node_kwargs):
        self.now = 0.0
        self.metrics = metrics
        self._q, self._n = [], itertools.count()
        self.links = {}
        for l in topo["links"]:
            self.links[frozenset((l["a"], l["b"]))] = {
                "cost": l["cost"], "orig": l["cost"], "delay": l.get("delay", 1.0), "up": True}
        self.tables = {}
        self.nodes = {n: node_cls(n, self, **node_kwargs) for n in topo["nodes"]}

    # --- API nodes may use ---
    def neighbors(self, node):
        return {next(iter(k - {node})): v["cost"] for k, v in self.links.items() if node in k and v["up"]}

    def send(self, src, dst, msg):
        link = self.links.get(frozenset((src, dst)))
        if link is None or not link["up"]:
            return
        if self.metrics:
            self.metrics.on_send(self.now, src, dst, msg)
        self.schedule(link["delay"], lambda: self._deliver(src, dst, msg))

    def schedule(self, delay, fn):
        heapq.heappush(self._q, (self.now + delay, next(self._n), fn))

    def report_table(self, node, table):
        self.tables[node] = dict(table)
        if self.metrics:
            self.metrics.report_table(self.now, node, table)

    # --- engine side ---
    def _deliver(self, src, dst, msg):
        if self.links[frozenset((src, dst))]["up"]:      # in-flight messages die with the link
            self.nodes[dst].on_message(src, msg)

    def start(self):
        for n in sorted(self.nodes):
            self.nodes[n].on_start()

    def run(self):
        while self._q:
            t, _, fn = heapq.heappop(self._q)
            self.now = t
            fn()

    def _event(self, label, a, b, cost):
        if self.metrics:
            self.metrics.mark_event(self.now, label)
        link = self.links[frozenset((a, b))]
        link["up"] = cost is not None
        if cost is not None:
            link["cost"] = cost
        self.nodes[a].on_link_change(b, cost)
        self.nodes[b].on_link_change(a, cost)

    def set_cost(self, a, b, cost):
        self._event(f"cost {a}-{b} -> {cost}", a, b, cost)

    def fail(self, a, b):
        self._event(f"fail {a}-{b}", a, b, None)

    def recover(self, a, b):
        self._event(f"recover {a}-{b}", a, b, self.links[frozenset((a, b))]["cost"])
