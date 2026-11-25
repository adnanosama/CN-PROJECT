"""Tests for engine.py and topology.py using dummy nodes.  Run: python -m unittest -v"""
import json
import os
import tempfile
import unittest

from engine import Sim
from topology import TopologyError, load_topology

TWO = {"nodes": ["A", "B"], "links": [{"a": "A", "b": "B", "cost": 1, "delay": 1.0}]}
CHAIN = {"nodes": ["A", "B", "C"],
         "links": [{"a": "A", "b": "B", "cost": 1}, {"a": "B", "b": "C", "cost": 1}]}

TRACE = []


class Recorder:
    """Dummy router: logs everything the engine does to it."""

    def __init__(self, node_id, sim, **opts):
        self.id, self.sim, self.opts = node_id, sim, opts
        self.log, self.table = [], {}

    def on_start(self):
        self.log.append((self.sim.now, "start"))

    def on_message(self, from_id, msg):
        self.log.append((self.sim.now, "msg", from_id, msg))

    def on_link_change(self, neighbor_id, cost):
        TRACE.append(("link", self.id))
        self.log.append((self.sim.now, "link", neighbor_id, cost,
                         dict(self.sim.neighbors(self.id))))

    def msgs(self):
        return [e for e in self.log if e[1] == "msg"]


class Ticker(Recorder):
    def on_start(self):
        self.ticks = []
        self.sim.schedule(1.0, self.tick)

    def tick(self):
        self.ticks.append(self.sim.now)
        self.sim.schedule(1.0, self.tick)


class RecMetrics:
    def __init__(self):
        self.sends, self.tables, self.events = [], [], []

    def on_send(self, t, src, dst, msg):
        self.sends.append((t, src, dst, msg["type"]))

    def report_table(self, t, node_id, table):
        self.tables.append((t, node_id, table))

    def mark_event(self, t, label):
        TRACE.append(("mark", label))
        self.events.append((t, label))


class EngineTests(unittest.TestCase):
    def setUp(self):
        TRACE.clear()
        self.m = RecMetrics()

    def make(self, topo=TWO, cls=Recorder, **kw):
        return Sim(topo, cls, metrics=self.m, **kw)

    def test_delivery_after_link_delay(self):
        sim = self.make()
        sim.start()
        sim.send("A", "B", {"type": "x"})
        sim.run()
        self.assertEqual(sim.nodes["B"].msgs(), [(1.0, "msg", "A", {"type": "x"})])
        self.assertEqual(sim.now, 1.0)
        self.assertEqual(self.m.sends, [(0.0, "A", "B", "x")])

    def test_on_start_called_once_in_sorted_order(self):
        sim = self.make(CHAIN)
        sim.run()
        sim.run()
        for n in "ABC":
            self.assertEqual(sim.nodes[n].log, [(0.0, "start")])

    def test_flap_loses_in_flight_message(self):
        # sent at 0, due at 1.0; link fails at 0.2 and recovers at 0.5
        sim = self.make()
        sim.start()
        sim.send("A", "B", {"type": "x"})
        sim.at(0.2, lambda: sim.fail("A", "B"))
        sim.at(0.5, lambda: sim.recover("A", "B"))
        sim.run()
        self.assertEqual(sim.nodes["B"].msgs(), [])
        self.assertEqual(sim.stats["lost_in_flight"], 1)

    def test_message_sent_after_recovery_is_delivered(self):
        sim = self.make()
        sim.start()
        sim.at(0.2, lambda: sim.fail("A", "B"))
        sim.at(0.5, lambda: sim.recover("A", "B"))
        sim.at(0.6, lambda: sim.send("A", "B", {"type": "x"}))
        sim.run()
        self.assertEqual(len(sim.nodes["B"].msgs()), 1)
        self.assertEqual(sim.nodes["B"].msgs()[0][0], 1.6)

    def test_cost_change_does_not_kill_in_flight_message(self):
        sim = self.make()
        sim.start()
        sim.send("A", "B", {"type": "x"})
        sim.at(0.5, lambda: sim.set_cost("A", "B", 9))
        sim.run()
        self.assertEqual(len(sim.nodes["B"].msgs()), 1)

    def test_send_on_down_link_dropped_and_not_counted(self):
        sim = self.make()
        sim.start()
        sim.fail("A", "B")
        sim.send("A", "B", {"type": "x"})
        sim.run()
        self.assertEqual(self.m.sends, [])
        self.assertEqual(sim.nodes["B"].msgs(), [])
        self.assertEqual(sim.stats["sent"], 0)

    def test_send_errors(self):
        sim = self.make(CHAIN)
        sim.start()
        with self.assertRaises(ValueError):
            sim.send("A", "C", {"type": "x"})        # not a neighbor
        with self.assertRaises(ValueError):
            sim.send("A", "A", {"type": "x"})        # to itself
        with self.assertRaises(ValueError):
            sim.send("A", "B", {"no_type": 1})       # bad message
        with self.assertRaises(ValueError):
            sim.send("A", "B", "hello")

    def test_link_event_order_and_state_visible_in_callbacks(self):
        sim = self.make()
        sim.start()
        TRACE.clear()
        sim.fail("A", "B")
        self.assertEqual(TRACE, [("mark", "fail A-B"), ("link", "A"), ("link", "B")])
        self.assertEqual(sim.nodes["A"].log[-1], (0.0, "link", "B", None, {}))
        self.assertEqual(sim.nodes["B"].log[-1], (0.0, "link", "A", None, {}))

    def test_recover_restores_last_cost(self):
        sim = self.make()
        sim.start()
        sim.set_cost("A", "B", 7)
        sim.fail("A", "B")
        sim.recover("A", "B")
        self.assertEqual(sim.nodes["A"].log[-1], (0.0, "link", "B", 7, {"B": 7}))
        self.assertEqual(sim.neighbors("B"), {"A": 7})

    def test_invalid_link_events_raise(self):
        sim = self.make()
        sim.start()
        with self.assertRaises(ValueError):
            sim.recover("A", "B")                    # already up
        sim.fail("A", "B")
        with self.assertRaises(ValueError):
            sim.fail("A", "B")                       # already down
        with self.assertRaises(ValueError):
            sim.set_cost("A", "B", 3)                # down: must use recover
        sim.recover("A", "B")
        for bad in (0, -1, 2.5, True, None):
            with self.assertRaises(ValueError):
                sim.set_cost("A", "B", bad)
        with self.assertRaises(ValueError):
            sim.fail("A", "Z")

    def test_link_event_before_start_raises(self):
        sim = self.make()
        with self.assertRaises(RuntimeError):
            sim.fail("A", "B")

    def test_equal_timestamps_run_fifo(self):
        sim = self.make()
        order = []
        for i in (1, 2, 3):
            sim.at(1.0, lambda i=i: order.append(i))
        sim.run()
        self.assertEqual(order, [1, 2, 3])

    def test_messages_on_one_link_arrive_in_order(self):
        sim = self.make()
        sim.start()
        for i in range(5):
            sim.send("A", "B", {"type": "x", "i": i})
        sim.run()
        self.assertEqual([e[3]["i"] for e in sim.nodes["B"].msgs()], [0, 1, 2, 3, 4])

    def test_receiver_gets_its_own_copy_of_message(self):
        sim = self.make()
        sim.start()
        msg = {"type": "x", "v": [1]}
        sim.send("A", "B", msg)
        msg["v"].append(2)
        sim.run()
        self.assertEqual(sim.nodes["B"].msgs()[0][3], {"type": "x", "v": [1]})

    def test_copy_is_deep_for_nested_and_unusual_messages(self):
        sim = self.make()
        sim.start()
        msg = {"type": "x", "d": {"k": [1, {"z": 2}]}, "t": (1, [2]), "s": {1, 2}, "n": None}
        sim.send("A", "B", msg)
        msg["d"]["k"][1]["z"] = 99
        msg["t"][1].append(3)
        msg["s"].add(3)
        sim.run()
        got = sim.nodes["B"].msgs()[0][3]
        self.assertEqual(got, {"type": "x", "d": {"k": [1, {"z": 2}]}, "t": (1, [2]),
                               "s": {1, 2}, "n": None})
        self.assertIsInstance(got["t"], tuple)

    def test_report_table_dedup_copy_and_no_initial_empty(self):
        sim = self.make()
        sim.start()
        sim.report_table("A", {})                          # initial empty: ignored
        t = {"B": ["B", 1]}                                 # list normalised to tuple
        sim.report_table("A", t)
        sim.report_table("A", {"B": ("B", 1)})              # identical: ignored
        t["B"] = ["B", 99]                                  # node mutates afterwards
        self.assertEqual(self.m.tables, [(0.0, "A", {"B": ("B", 1)})])
        sim.report_table("A", {"B": ("B", 2)})
        self.assertEqual(len(self.m.tables), 2)
        self.assertEqual(sim.tables["A"], {"B": ("B", 2)})

    def test_run_until_with_periodic_timers(self):
        sim = self.make(cls=Ticker)
        self.assertEqual(sim.run(until=5.5), 5.5)
        self.assertEqual(sim.nodes["A"].ticks, [1.0, 2.0, 3.0, 4.0, 5.0])
        sim.run(until=7.0)                                  # can continue from where it stopped
        self.assertEqual(sim.nodes["A"].ticks[-1], 7.0)

    def test_run_without_until_on_endless_timers_raises(self):
        sim = self.make(cls=Ticker)
        with self.assertRaises(RuntimeError):
            sim.run(max_events=100)

    def test_float_time_rounding(self):
        sim = self.make()
        seen = []
        sim.start()
        sim.schedule(0.1, lambda: sim.schedule(0.2, lambda: seen.append(sim.now)))
        sim.run()
        self.assertEqual(seen, [0.3])

    def test_script_fires_events_in_order(self):
        sim = self.make(cls=Ticker)
        sim.script([(2.0, "fail", "A", "B"), (4.0, "recover", "A", "B"),
                    (6.0, "cost", "A", "B", 9)])
        sim.run(until=10.0)
        self.assertEqual(self.m.events, [(2.0, "fail A-B"), (4.0, "recover A-B"),
                                         (6.0, "cost A-B -> 9")])
        self.assertEqual(sim.link_info("A", "B"), {"cost": 9, "delay": 1.0, "up": True})

    def test_script_rejects_bad_events(self):
        sim = self.make()
        for bad in [(1.0, "explode", "A", "B"), (1.0, "cost", "A", "B"),
                    (1.0, "fail", "A", "Z"), (1.0, "fail", "A")]:
            with self.assertRaises(ValueError):
                sim.script([bad])

    def test_cannot_schedule_in_the_past(self):
        sim = self.make()
        sim.run(until=5.0)
        with self.assertRaises(ValueError):
            sim.at(4.0, lambda: None)
        with self.assertRaises(ValueError):
            sim.schedule(-1, lambda: None)

    def test_node_options_are_passed_to_every_node(self):
        sim = self.make(spf_holddown=0.2)
        self.assertEqual(sim.nodes["A"].opts, {"spf_holddown": 0.2})
        self.assertEqual(sim.nodes["B"].opts, {"spf_holddown": 0.2})

    def test_neighbors_only_lists_up_links(self):
        sim = self.make(CHAIN)
        sim.start()
        self.assertEqual(sim.neighbors("B"), {"A": 1, "C": 1})
        sim.fail("A", "B")
        self.assertEqual(sim.neighbors("B"), {"C": 1})
        self.assertEqual(sim.neighbors("A"), {})


class TopologyTests(unittest.TestCase):
    def link(self, **kw):
        d = {"a": "A", "b": "B", "cost": 1}
        d.update(kw)
        return {"nodes": ["A", "B", "C"], "links": [d]}

    def test_valid_and_default_delay(self):
        topo = load_topology(self.link())
        self.assertEqual(topo["links"][0]["delay"], 1.0)

    def test_load_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.json")
            with open(p, "w") as f:
                json.dump(TWO, f)
            self.assertEqual(load_topology(p)["nodes"], ["A", "B"])
            with open(p, "w") as f:
                f.write("{not json")
            with self.assertRaises(TopologyError):
                load_topology(p)

    def test_rejects_bad_topologies(self):
        bad = [
            {"nodes": ["A"]},                                                    # no links
            {"nodes": ["A", "A"], "links": []},                                  # duplicate node
            {"nodes": ["A", 5], "links": []},                                    # non-string id
            self.link(b="Z"),                                                    # unknown endpoint
            self.link(b="A"),                                                    # self-loop
            self.link(cost=0),
            self.link(cost=1.5),
            self.link(cost=True),
            self.link(delay=0),
            self.link(delay=-1),
            self.link(delay="fast"),
            {"nodes": ["A", "B"], "links": [{"a": "A", "b": "B", "cost": 1},
                                            {"a": "B", "b": "A", "cost": 2}]},  # duplicate link
            {"nodes": ["A", "B"], "links": [{"a": "A", "b": "B"}]},             # missing cost
        ]
        for t in bad:
            with self.assertRaises(TopologyError, msg=str(t)):
                load_topology(t)

    def test_require_connected(self):
        load_topology(CHAIN, require_connected=True)
        with self.assertRaises(TopologyError):
            load_topology(self.link(), require_connected=True)  # C is isolated


if __name__ == "__main__":
    unittest.main()