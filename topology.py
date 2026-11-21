"""Topology loader and validator (F2).

Rules (contract.md v0.1 plus the proposed v0.2 additions):
  * node ids are non-empty strings, no duplicates
  * links are undirected: no self-loops, no duplicate pairs (A-B == B-A)
  * link endpoints must be listed in "nodes"
  * cost is a positive int, delay is a finite number > 0 (optional, default 1.0)

load_topology() always returns a *normalised* dict, so every link has a float
"delay" and the engine never has to guess.
"""
import json
import math
from pathlib import Path


class TopologyError(ValueError):
    """The topology breaks one of the rules above."""


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def is_connected(topo):
    """True if every node can reach every other node (ignores link up/down)."""
    nodes = topo["nodes"]
    if not nodes:
        return True
    adj = {n: [] for n in nodes}
    for l in topo["links"]:
        adj[l["a"]].append(l["b"])
        adj[l["b"]].append(l["a"])
    seen = {nodes[0]}
    stack = [nodes[0]]
    while stack:
        for m in adj[stack.pop()]:
            if m not in seen:
                seen.add(m)
                stack.append(m)
    return len(seen) == len(nodes)


def validate_topology(data, require_connected=False):
    """Check `data` and return a normalised copy. Raises TopologyError."""
    if not isinstance(data, dict) or "nodes" not in data or "links" not in data:
        raise TopologyError('topology must be an object with "nodes" and "links"')
    nodes, links = data["nodes"], data["links"]
    if not isinstance(nodes, list) or not isinstance(links, list):
        raise TopologyError('"nodes" and "links" must both be lists')

    seen = set()
    for n in nodes:
        if not isinstance(n, str) or not n:
            raise TopologyError(f"node id must be a non-empty string, got {n!r}")
        if n in seen:
            raise TopologyError(f"duplicate node {n!r}")
        seen.add(n)

    out, pairs = [], set()
    for i, l in enumerate(links):
        if not isinstance(l, dict):
            raise TopologyError(f"link #{i} must be an object")
        for key in ("a", "b", "cost"):
            if key not in l:
                raise TopologyError(f'link #{i} is missing "{key}"')
        a, b = l["a"], l["b"]
        if not (isinstance(a, str) and a in seen and isinstance(b, str) and b in seen):
            raise TopologyError(f"link #{i} ({a!r}-{b!r}) uses a node not listed in \"nodes\"")
        if a == b:
            raise TopologyError(f"link #{i}: self-loop on {a!r}")
        pair = frozenset((a, b))
        if pair in pairs:
            raise TopologyError(f"link #{i}: duplicate link {a}-{b}")
        pairs.add(pair)
        cost = l["cost"]
        if not _is_int(cost) or cost <= 0:
            raise TopologyError(f"link {a}-{b}: cost must be a positive integer, got {cost!r}")
        delay = l.get("delay", 1.0)
        if not _is_num(delay) or not math.isfinite(delay) or delay <= 0:
            raise TopologyError(f"link {a}-{b}: delay must be a number > 0, got {delay!r}")
        out.append({"a": a, "b": b, "cost": cost, "delay": float(delay)})

    topo = {"nodes": list(nodes), "links": out}
    if require_connected and not is_connected(topo):
        raise TopologyError("topology is not connected")
    return topo


def load_topology(source, require_connected=False):
    """`source` is a path to a JSON file (str or Path) or an already-parsed dict."""
    if isinstance(source, (str, Path)):
        try:
            with open(source, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise TopologyError(f"{source}: not valid JSON ({e})") from e
    elif isinstance(source, dict):
        data = source
    else:
        raise TopologyError("source must be a file path or a dict")
    return validate_topology(data, require_connected)


def save_topology(topo, path):
    """Write a topology dict as JSON (used by the random graph generator)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(topo, f, indent=2)