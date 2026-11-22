"""Discrete-event simulation engine (F1 engine, F2 loader hookup, F5 link events).

Quick use:
    sim = Sim("topo.json", MyNodeClass, metrics=my_metrics, spf_holddown=0.1)
    sim.script([(10.0, "fail", "A", "B"),
                (30.0, "recover", "A", "B"),
                (50.0, "cost", "A", "C", 7)])
    sim.run(until=80.0)

Behaviour (contract v0.1 + the v0.2 changes proposed to the team):
  * Time is a float, rounded to 9 decimals so 0.1 + 0.2 == 0.3.
  * Events with equal timestamps run in the order they were scheduled (FIFO).
  * run() calls on_start on every node once, in sorted node-id order, at t=0.
  * A link event: metrics.mark_event -> link state updated -> on_link_change(a)
    -> on_link_change(b). So sim.neighbors() already shows the new state inside
    both callbacks.
  * Messages in flight when a link fails are lost, even if the link recovers
    before the message was due (each link has an epoch that changes on fail
    and recover).
  * send() on a down link: dropped silently, NOT counted in metrics.
    send() to a non-neighbor, or a msg without a "type": ValueError.
  * send() gives the receiver its own deep copy of msg.
  * report_table() copies the table, ignores repeats of the same table, and
    never reports the initial empty table.
  * set_cost on a down link: ValueError (use recover). fail on a down link or
    recover on an up link: ValueError.
"""
import copy
import heapq
import itertools
import math

from topology import load_topology

TIME_DIGITS = 9
DEFAULT_MAX_EVENTS = 10_000_000


def _t(x):
    return round(x, TIME_DIGITS)


_ATOMS = {str, int, float, bool, type(None), bytes}


def _copy_msg(x):
    """Independent copy of a message. Much faster than copy.deepcopy for the usual
    dict/list/tuple/number/string messages; anything else falls back to deepcopy."""
    t = type(x)
    if t in _ATOMS:
        return x
    if t is dict:
        return {k: _copy_msg(v) for k, v in x.items()}
    if t is list:
        return [_copy_msg(v) for v in x]
    if t is tuple:
        return tuple(_copy_msg(v) for v in x)
    return copy.deepcopy(x)


class _Link:
    __slots__ = ("cost", "delay", "up", "epoch")

    def __init__(self, cost, delay):
        self.cost = cost
        self.delay = delay
        self.up = True
        self.epoch = 0  # +1 on every fail and every recover


class Sim:
    def __init__(self, topology, node_cls, metrics=None, **node_options):
        """topology: path to JSON file or a dict. node_options go to every node."""
        topo = load_topology(topology)
        self.now = 0.0
        self.metrics = metrics
        self.stats = {"sent": 0, "delivered": 0, "lost_in_flight": 0}
        self.tables = {n: {} for n in topo["nodes"]}  # last reported table per node
        self._q = []
        self._seq = itertools.count()
        self._started = False
        self._links = {}
        self._adj = {n: {} for n in topo["nodes"]}
        for l in topo["links"]:
            link = _Link(l["cost"], l["delay"])
            self._links[frozenset((l["a"], l["b"]))] = link
            self._adj[l["a"]][l["b"]] = link
            self._adj[l["b"]][l["a"]] = link
        self.nodes = {n: node_cls(n, self, **node_options) for n in topo["nodes"]}

    # ------------------------------------------------------------------
    # API for nodes (contract)
    # ------------------------------------------------------------------
    def neighbors(self, node_id):
        """{neighbor: cost} for this node's own *up* links."""
        adj = self._adj.get(node_id)
        if adj is None:
            raise ValueError(f"unknown node {node_id!r}")
        return {m: l.cost for m, l in adj.items() if l.up}

    def send(self, src, dst, msg):
        link = self._link(src, dst)
        if not isinstance(msg, dict) or "type" not in msg:
            raise ValueError('msg must be a dict with a "type" key')
        if not link.up:
            return
        if self.metrics is not None:
            self.metrics.on_send(self.now, src, dst, msg)
        self.stats["sent"] += 1
        msg = _copy_msg(msg)
        epoch = link.epoch
        self._push(link.delay, lambda: self._deliver(src, dst, msg, link, epoch))

    def schedule(self, delay, fn):
        """Run fn() after `delay` simulated time units."""
        self._push(delay, fn)

    def report_table(self, node_id, table):
        if node_id not in self.tables:
            raise ValueError(f"unknown node {node_id!r}")
        snap = {dest: (hop, cost) for dest, (hop, cost) in table.items()}
        if snap == self.tables[node_id]:
            return
        self.tables[node_id] = snap
        if self.metrics is not None:
            self.metrics.report_table(self.now, node_id, snap)

    # ------------------------------------------------------------------
    # Running the clock
    # ------------------------------------------------------------------
    def at(self, t, fn):
        """Run fn() at absolute simulated time t (t >= now)."""
        if t < self.now:
            raise ValueError(f"cannot schedule in the past (t={t}, now={self.now})")
        self._push(t - self.now, fn)

    def start(self):
        """Call on_start on every node (sorted order). Safe to call twice."""
        if self._started:
            return
        self._started = True
        for n in sorted(self.nodes):
            self.nodes[n].on_start()

    def run(self, until=None, max_events=DEFAULT_MAX_EVENTS):
        """Process events in time order and return the final time.

        until=T   stop after all events with time <= T, clock ends at T.
        until=None run until the queue is empty. Nodes with periodic timers
                  never empty it, so always pass until= for those.
        max_events is a safety net against runaway loops.
        """
        self.start()
        if until is not None:
            until = _t(until)
        done = 0
        while self._q:
            if until is not None and self._q[0][0] > until:
                break
            if max_events is not None and done >= max_events:
                raise RuntimeError(
                    f"stopped after {max_events} events at t={self.now}; "
                    "the queue is not draining (periodic timers? pass until=)")
            t, _, fn = heapq.heappop(self._q)
            self.now = t
            fn()
            done += 1
        if until is not None and until > self.now:
            self.now = until
        return self.now

    # ------------------------------------------------------------------
    # Link events (F5). Fire immediately, so call them from inside run()
    # (via at()/script()) or after start().
    # ------------------------------------------------------------------
    def set_cost(self, a, b, cost):
        link = self._link(a, b)
        if isinstance(cost, bool) or not isinstance(cost, int) or cost <= 0:
            raise ValueError(f"cost must be a positive integer, got {cost!r}")
        if not link.up:
            raise ValueError(f"link {a}-{b} is down; use recover()")
        self._fire(f"cost {a}-{b} -> {cost}", a, b, link, up=True, cost=cost)

    def fail(self, a, b):
        link = self._link(a, b)
        if not link.up:
            raise ValueError(f"link {a}-{b} is already down")
        self._fire(f"fail {a}-{b}", a, b, link, up=False, cost=None)

    def recover(self, a, b):
        """Bring the link back with the cost it had when it failed."""
        link = self._link(a, b)
        if link.up:
            raise ValueError(f"link {a}-{b} is already up")
        self._fire(f"recover {a}-{b}", a, b, link, up=True, cost=link.cost)

    def script(self, events):
        """Schedule link events in advance.

        events: iterable of (t, "fail", a, b), (t, "recover", a, b) or
        (t, "cost", a, b, new_cost). Leave enough time between events for
        the network to settle (see contract notes).
        """
        for ev in events:
            if len(ev) < 4:
                raise ValueError(f"bad event {ev!r}")
            t, kind, a, b = ev[:4]
            args = tuple(ev[4:])
            if kind == "cost" and len(args) == 1:
                fn = self.set_cost
            elif kind in ("fail", "recover") and not args:
                fn = getattr(self, kind)
            else:
                raise ValueError(f"bad event {ev!r}")
            self._link(a, b)  # fail early on unknown links
            self.at(t, lambda fn=fn, a=a, b=b, args=args: fn(a, b, *args))

    def link_info(self, a, b):
        """Current state of a link: {'cost', 'delay', 'up'}."""
        l = self._link(a, b)
        return {"cost": l.cost, "delay": l.delay, "up": l.up}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _link(self, a, b):
        link = self._links.get(frozenset((a, b)))
        if link is None:
            raise ValueError(f"no link between {a!r} and {b!r}")
        return link

    def _push(self, delay, fn):
        if not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0:
            raise ValueError(f"delay must be a finite number >= 0, got {delay!r}")
        heapq.heappush(self._q, (_t(self.now + delay), next(self._seq), fn))

    def _deliver(self, src, dst, msg, link, epoch):
        if not link.up or link.epoch != epoch:
            self.stats["lost_in_flight"] += 1
            return
        self.stats["delivered"] += 1
        self.nodes[dst].on_message(src, msg)

    def _fire(self, label, a, b, link, up, cost):
        if not self._started:
            raise RuntimeError(
                "link events need a started sim: call start()/run() first, "
                "or schedule them ahead of time with at()/script()")
        if self.metrics is not None:
            self.metrics.mark_event(self.now, label)
        if link.up != up:
            link.epoch += 1  # kills every message currently in flight on this link
        link.up = up
        if cost is not None:
            link.cost = cost
        self.nodes[a].on_link_change(b, cost)
        self.nodes[b].on_link_change(a, cost)