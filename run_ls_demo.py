"""Run the LS simulation on a topology file, print metrics, save final tables for the FRR comparison.
Usage: python3 run_ls_demo.py topo_demo.json sim_tables.json"""
import json
import sys
from fake_sim import FakeSim      # swap for Zaid's engine once it exists
from ls_node import LSNode
from metrics import Metrics

topo = json.load(open(sys.argv[1]))
m = Metrics()
sim = FakeSim(topo, LSNode, m)
sim.start(); sim.run()
json.dump({n: {d: list(v) for d, v in node.table.items()} for n, node in sim.nodes.items()},
          open(sys.argv[2], "w"), indent=1)       # tables at steady state, before any event
a, b = topo["links"][0]["a"], topo["links"][0]["b"]
sim.fail(a, b); sim.run()
sim.recover(a, b); sim.run()
for w in m.summary():
    print(w)
