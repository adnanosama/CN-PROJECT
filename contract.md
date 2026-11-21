# Shared contract (draft v0.2)

Status: draft, to be confirmed by all three members. v0.2 keeps everything in v0.1 and adds the engine behaviour that `engine.py` and `topology.py` already implement, plus node options. Once confirmed it is frozen.

## Topology file (JSON)

```json
{"nodes": ["A", "B"], "links": [{"a": "A", "b": "B", "cost": 1, "delay": 1.0}]}
```

- Node ids are non-empty strings with no duplicates.
- Links are undirected: no self-loops, no duplicate pairs (A-B is the same as B-A), and both endpoints must be listed in `nodes`.
- `cost` is a positive integer. `delay` is a finite number greater than 0 and defaults to 1.0.
- `load_topology()` returns a normalised dict in which every link has a float `delay`.

## Node interface (called by the engine)

- `__init__(self, node_id, sim, **options)`: `options` are the keyword arguments passed to `Sim(...)`; every node receives the same ones (for example `spf_holddown`).
- `on_start(self)`: called once at t=0, in sorted node-id order.
- `on_message(self, from_id, msg)`: `msg` is a dict with a `"type"` key. The node receives its own deep copy.
- `on_link_change(self, neighbor_id, cost)`: `cost` is the new cost, or `None` if the link went down. Called on both endpoints. The link state is already updated, so `sim.neighbors()` shows the new state inside the callback. A non-`None` cost on a link that was down means recovery.
- `self.table`: dict `dest_id -> (next_hop_id, cost)`. The node itself is excluded and unreachable destinations are absent.

## Sim API (available to nodes)

- `sim.send(src, dst, msg)`: `dst` must be a direct neighbor, otherwise `ValueError`. `msg` must be a dict with a `"type"` key. If the link is down the message is dropped silently and not counted in metrics.
- `sim.neighbors(node_id)`: `{neighbor: cost}` for that node's own up links only.
- `sim.schedule(delay, fn)`: run `fn()` after `delay` simulated time units (delay is finite and at least 0).
- `sim.report_table(node_id, table)`: called by a node whenever its table changes. The engine copies the table, ignores repeats of an identical table and never reports the initial empty table.
- `sim.now`: current simulated time.

## Engine behaviour

- Time is a float rounded to 9 decimals. Events with equal timestamps run in the order they were scheduled.
- Link events: `metrics.mark_event`, then the link state is updated, then `on_link_change` on both endpoints.
- Messages in flight on a link that fails are lost, even if the link recovers before they were due.
- `set_cost` on a down link, `fail` on a down link and `recover` on an up link all raise `ValueError`. `recover` restores the cost the link had when it failed.
- `run(until=T)` must be used when nodes have periodic timers (distance-vector). `run()` with no `until` runs until the queue is empty.
- Leave enough simulated time between events for the network to settle, otherwise one event's traffic is counted in the next.

## Metrics hooks (called by the engine)

- In `send`: `metrics.on_send(sim.now, src, dst, msg)`.
- In `report_table`: `metrics.report_table(sim.now, node_id, table)`.
- When a link event fires: `metrics.mark_event(sim.now, label)`.

## Notes

- Link-state convergence time includes the SPF hold-down (`spf_holddown`, default 0.1 time units), similar to OSPF's SPF throttle. Report it with the results.
- With a uniform link delay, convergence time and rounds are equivalent. `Metrics.summary(round_length=...)` converts between them.