"""Link-state router (OSPF-like). F3.

Each node knows only its own links. It floods sequence-numbered LSAs, keeps a
link-state database (LSDB), and runs Dijkstra locally. Written against the
shared contract in contract.md.
"""
import heapq


class LSNode:
    def __init__(self, node_id, sim, spf_delay=0.1):
        self.id = node_id
        self.sim = sim
        self.spf_delay = spf_delay   # hold-down before running Dijkstra, like OSPF's SPF throttle
        self.links = {}              # neighbor -> cost (links that are up)
        self.seq = 0
        self.lsdb = {}               # origin -> (seq, {neighbor: cost})
        self.table = {}              # dest -> (next_hop, cost)
        self._spf_pending = False

    # engine callbacks 
    def on_start(self):
        self.links = dict(self.sim.neighbors(self.id))
        self._originate()

    def on_message(self, from_id, msg):
        if msg.get("type") != "LSA":
            return
        origin, seq = msg["origin"], msg["seq"]
        if origin == self.id:
            return                               # our own LSA came back around
        known = self.lsdb.get(origin)
        if known is not None and known[0] >= seq:
            return                               # duplicate or stale: drop
        self.lsdb[origin] = (seq, dict(msg["links"]))
        self._flood(msg, exclude=from_id)
        self._request_spf()

    def on_link_change(self, neighbor_id, cost):
        was_up = neighbor_id in self.links
        if cost is None:
            self.links.pop(neighbor_id, None)
        else:
            self.links[neighbor_id] = cost
        self._originate()
        if cost is not None and not was_up:
            # Link (re)appeared: hand the neighbor our whole database,
            # like OSPF's database exchange. Without this, a recovered
            # link across a partition would never resync.
            for origin, (seq, links) in self.lsdb.items():
                if origin != self.id:
                    self.sim.send(self.id, neighbor_id, self._lsa(origin, seq, links))

    # flooding 
    @staticmethod
    def _lsa(origin, seq, links):
        return {"type": "LSA", "origin": origin, "seq": seq, "links": dict(links)}

    def _originate(self):
        self.seq += 1
        self.lsdb[self.id] = (self.seq, dict(self.links))
        self._flood(self._lsa(self.id, self.seq, self.links))
        self._request_spf()

    def _flood(self, msg, exclude=None):
        for nbr in self.links:
            if nbr != exclude:
                self.sim.send(self.id, nbr, msg)

    # shortest paths 
    def _request_spf(self):
        if self._spf_pending:
            return
        self._spf_pending = True
        self.sim.schedule(self.spf_delay, self._run_spf)

    def _run_spf(self):
        self._spf_pending = False
        dist, first = {}, {}
        best = {self.id: 0}
        heap = [(0, self.id, None)]
        while heap:
            d, u, hop = heapq.heappop(heap)
            if u in dist:
                continue
            dist[u] = d
            if u != self.id:
                first[u] = hop
            entry = self.lsdb.get(u)
            if entry is None:
                continue
            for v, c in entry[1].items():
                back = self.lsdb.get(v)
                if back is None or u not in back[1]:
                    continue                     # two-way check: both ends must advertise the link
                nd = d + c
                if nd <= best.get(v, float("inf")):
                    best[v] = nd
                    heapq.heappush(heap, (nd, v, v if u == self.id else hop))
        new = {d: (first[d], dist[d]) for d in dist if d != self.id}
        if new != self.table:
            self.table = new
            self.sim.report_table(self.id, new)
