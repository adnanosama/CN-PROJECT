"""Metrics collector (F6). Works for LS and DV: it only sees messages and table changes.

The engine calls:
  mark_event(time, label)          when a scripted event fires (starts a new window)
  on_send(time, src, dst, msg)     inside sim.send
  report_table(time, node, table)  when a node's routing table changes
Leave enough simulated time between events for the network to settle,
or one window's traffic bleeds into the next.
"""
import json
import math


def size_of(msg):
    return len(json.dumps(msg, sort_keys=True, separators=(",", ":")))


class Metrics:
    def __init__(self):
        self.windows = [self._new(0.0, "start")]

    @staticmethod
    def _new(t, label):
        return {"label": label, "t0": t, "messages": 0, "bytes": 0, "by_type": {},
                "table_changes": 0, "last_change": t, "max_cost_seen": 0}

    def mark_event(self, time, label):
        self.windows.append(self._new(time, label))

    def on_send(self, time, src, dst, msg):
        w = self.windows[-1]
        w["messages"] += 1
        w["bytes"] += size_of(msg)
        kind = msg.get("type", "?")
        w["by_type"][kind] = w["by_type"].get(kind, 0) + 1

    def report_table(self, time, node, table):
        w = self.windows[-1]
        w["table_changes"] += 1
        w["last_change"] = time
        for _nh, cost in table.values():
            w["max_cost_seen"] = max(w["max_cost_seen"], cost)

    def summary(self, round_length=1.0):
        """round_length = the uniform link delay. With unit delays, time == rounds."""
        out = []
        for w in self.windows:
            conv = w["last_change"] - w["t0"]
            out.append({"label": w["label"], "convergence_time": round(conv, 3),
                        "rounds": math.ceil(conv / round_length - 1e-9) if conv > 0 else 0,
                        "messages": w["messages"], "bytes": w["bytes"],
                        "by_type": dict(w["by_type"]), "table_changes": w["table_changes"],
                        "max_cost_seen": w["max_cost_seen"]})
        return out
