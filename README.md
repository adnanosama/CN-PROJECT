# Routing Algorithm Simulator: Link-State vs Distance-Vector, validated against OSPF and RIP

Computer Networks, Project 18.
Members: Zaid Zubair (24K-0796), Adnan Osama (24K-0598), Atta Ur Rehman (24K-0969).

A Python discrete-event simulator in which each router is an independent agent that only knows its own links and talks to neighbours by message passing. It implements link-state (OSPF-like) and distance-vector (RIP-like) routing, injects link events, measures convergence, and compares the results with real OSPF and RIP routers running in FRRouting.

## Repository layout

| File | Purpose | Owner |
|---|---|---|
| `contract.md` | Shared interface between engine, nodes and metrics (v0.2, confirmed by all members) | all |
| `engine.py`, `topology.py`, `test_engine.py` | Simulation engine, topology loader and validator, engine tests | Zaid |
| `graphgen.py`, `topo_10.json`, `topo_50.json`, `topo_200.json` | Random connected topology generator (seeded, so runs are repeatable) and the generated 10/50/200-node topologies | Zaid |
| `count_to_infinity.py`, `topo_3node.json` | The 3-node count-to-infinity scenario: link B-C fails and recovers, and plain DV, split horizon, poison reverse and link-state are compared. Includes a stand-in DV node until `dv_node.py` is ready | Zaid |
| `test_scenarios.py` | Tests for the generator and the scenario, plus end-to-end runs of DV and LS on generated graphs checked against Dijkstra | Zaid |
| `fake_sim.py` | Minimal stand-in engine for testing nodes alone | Adnan |
| `metrics.py` | Convergence time, rounds, messages, bytes per event | Adnan |
| `ls_node.py` | Link-state node: LSA flooding, LSDB, Dijkstra | Adnan |
| `test_ls.py` | LS tables checked against NetworkX on random graphs | Adnan |
| `run_ls_demo.py`, `topo_demo.json` | Demo run on a 5-node topology | Adnan |
| `frr/ospf_tools.py` | Generates the containerlab + FRR OSPF lab, extracts tables, times failure, recovery and cost changes, runs the benchmark | Adnan |
| `frr/compare_ospf.py` | Path-cost agreement, simulator vs FRR OSPF | Adnan |
| `Captures/` | OSPF packet captures (`.pcapng`) and annotated Wireshark screenshots | Adnan |
| `results/ospf_bench.json` | OSPF convergence benchmark on the real routers | Adnan |
| `dv_node.py`, RIP lab scripts, experiment runner, plots | Distance-vector and experiments | Atta |

Each file has one owner, and only the owner edits it. Each member works on their own branch and changes reach `main` through pull requests.

## Requirements

- Python 3 and `pip install networkx`
- For the FRR validation: Linux with Docker and containerlab. On Windows, use a Linux VM or WSL2 (Ubuntu) with Docker, and clone the repo inside the Linux filesystem.

## Running the code

```
python test_engine.py
python test_ls.py
python test_scenarios.py
python run_ls_demo.py topo_demo.json sim_tables.json
python count_to_infinity.py
python graphgen.py --standard
```

`test_engine.py` tests the engine and topology loader with dummy nodes. `test_ls.py` runs 40 random graphs through cost changes, failures (including partitions) and recoveries, and checks every node's table against NetworkX. It also runs a 200-node case. `test_scenarios.py` tests the generator and the count-to-infinity scenario, and runs distance-vector and link-state on generated 10, 50 and 200-node graphs. `count_to_infinity.py` prints how plain DV counts up to infinity after the B-C failure and compares it with split horizon, poison reverse and link-state. `graphgen.py --standard` rewrites `topo_10.json`, `topo_50.json` and `topo_200.json`; the same seed always gives the same files. `run_ls_demo.py` runs the link-state nodes on the engine and prints metrics for the start-up, a link failure and a recovery.

## OSPF validation lab

Run from the repository root (containerlab treats an unknown path as a repository name).

```
python3 frr/ospf_tools.py gen topo_demo.json lab
sudo containerlab deploy -t lab/ospf-lab.clab.yml
docker exec clab-ospf-lab-r1 vtysh -c "show ip route ospf"
python3 frr/ospf_tools.py extract lab/lab_meta.json frr_tables.json
python3 frr/compare_ospf.py sim_tables.json frr_tables.json
python3 frr/ospf_tools.py fail lab/lab_meta.json A B
python3 frr/ospf_tools.py recover lab/lab_meta.json A B
python3 frr/ospf_tools.py cost lab/lab_meta.json A B 20
python3 frr/ospf_tools.py bench lab/lab_meta.json A B --runs 3
sudo containerlab destroy -t lab/ospf-lab.clab.yml --cleanup
```

## Results so far

Link-state simulator against FRR OSPF on `topo_demo.json` (5 routers): 20 of 20 routes agree on cost and next hop.

OSPF convergence on the real routers, link A–B, 3 runs per condition (`results/ospf_bench.json`):

| Condition | Mean | Stdev |
|---|---|---|
| Link failure | 1.831 s | 0.382 s |
| Link recovery | 6.360 s | 2.997 s |
| Cost increase (1 to 5) | 2.684 s | 0.760 s |
| Cost decrease (5 to 1) | 2.168 s | 0.082 s |

Recovery is slower and varies more because the returning link first has to form a new adjacency (Hello, then database exchange). The packet captures in `Captures/` show the Hello, database exchange and LSA flooding on the lab.

## Status

| Component | Status |
|---|---|
| `contract.md` | v0.2, confirmed by all members |
| `ls_node.py`, `metrics.py`, `fake_sim.py` | Tested (`test_ls.py`) |
| `engine.py`, `topology.py` | Tested (`test_engine.py`); link-state runs on it (`run_ls_demo.py`) |
| `graphgen.py`, `count_to_infinity.py` | Tested (`test_scenarios.py`) |
| `frr/ospf_tools.py`, `frr/compare_ospf.py` | Run on `topo_demo.json`: 20/20 agreement, benchmark and captures done |
| `dv_node.py`, RIP lab, experiment runner | In progress (Atta) |

## Notes

- `count_to_infinity.py` uses a small stand-in DV node (`DemoDV`). Once `dv_node.py` is ready, the scenario should be run with it too.
- Real OSPF and the simulator will not agree on convergence time in seconds, because the simulator uses abstract time units and has no retransmission timers. Compare how the shape and message counts scale, not the raw numbers.
- The lab runs on 5 routers. 50 and 200-node topologies are used for simulator scaling only.
- If a router fails to start in the lab, `docker logs clab-ospf-lab-r1` shows the reason.

## AI use

Parts of the code and documentation in this repository were drafted with the help of Claude (Anthropic). The full disclosure is in the project report.