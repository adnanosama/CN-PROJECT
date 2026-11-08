# Shared contract (draft v0.1)

Status: draft, to be confirmed by all three members before integration starts. After confirmation it is frozen; any change needs everyone's agreement.

## Topology file (JSON)

```json
{"nodes": ["A", "B"], "links": [{"a": "A", "b": "B", "cost": 1, "delay": 1.0}]}
```

Node ids are strings. Links are undirected with one symmetric cost. Delay is in simulated time units.

## Node interface (called by the engine)

- `__init__(self, node_id, sim)`
- `on_start(self)`: called once at t=0.
- `on_message(self, from_id, msg)`: `msg` is a dict with a `"type"` key.
- `on_link_change(self, neighbor_id, cost)`: `cost` is the new cost, or `None` if the link went down. Called on both endpoints at the event time. A non-`None` cost on a link that was down means recovery.
- `self.table`: dict `dest_id -> (next_hop_id, cost)`. The node itself is excluded and unreachable destinations are absent.

## Sim API (available to nodes)

- `sim.send(src, dst, msg)`: `dst` must be a direct neighbor. The message is dropped if the link is down.
- `sim.neighbors(node_id)`: `{neighbor: cost}` for that node's own up links only.
- `sim.schedule(delay, fn)`: run `fn` after `delay` simulated time units (SPF hold-down, periodic DV updates).
- `sim.report_table(node_id, table)`: called by a node whenever its table changes.
- `sim.now`: current simulated time.

## Engine responsibilities (metrics hooks)

- In `sim.send`: `metrics.on_send(sim.now, src, dst, msg)`.
- In `sim.report_table`: `metrics.report_table(sim.now, node_id, table)`.
- When a scripted event fires: `metrics.mark_event(sim.now, label)`, then call `on_link_change` on both endpoints.
- Messages in flight over a link that goes down are lost.
- Leave enough simulated time between events for the network to settle, otherwise one event's traffic is counted in the next.

## Notes

- Link-state convergence time includes the SPF hold-down (default 0.1 time units), similar to OSPF's SPF throttle. Report it alongside the results.
- With a uniform link delay, convergence time and rounds are equivalent. `Metrics.summary(round_length=...)` converts between them.