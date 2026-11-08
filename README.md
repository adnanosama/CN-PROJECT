# Routing Algorithm Simulator: Link-State vs Distance-Vector, validated against OSPF and RIP

Computer Networks, Project 18.
Members: Zaid Zubair (24K-0796), Adnan Osama (24K-0598), Atta Ur Rehman (24K-0969).

A Python discrete-event simulator in which each router is an independent agent that only knows its own links and talks to neighbours by message passing. It implements link-state (OSPF-like) and distance-vector (RIP-like) routing, injects link events, measures convergence, and compares the results with real OSPF and RIP routers running in FRRouting.

## Repository layout

| File | Purpose | Owner |
|---|---|---|
| `contract.md` | Shared interface between engine, nodes and metrics | all (draft) |
| `fake_sim.py` | Minimal stand-in engine for testing nodes alone | Adnan |
| `metrics.py` | Convergence time, rounds, messages, bytes per event | Adnan |
| `ls_node.py` | Link-state node: LSA flooding, LSDB, Dijkstra | Adnan |
| `test_ls.py` | LS tables checked against NetworkX on random graphs | Adnan |
| `run_ls_demo.py`, `topo_demo.json` | Demo run on a 5-node topology | Adnan |
| `frr/ospf_tools.py` | Generates the containerlab + FRR OSPF lab, extracts tables, times a failure | Adnan |
| `frr/compare_ospf.py` | Path-cost agreement, simulator vs FRR OSPF | Adnan |
| engine, topology loader, random graphs, event scripts | Simulation core | Zaid |
| `dv_node.py`, RIP lab scripts, experiment runner, plots | Distance-vector and experiments | Atta |

Each file has one owner, and only the owner edits it. Each member works on their own branch and changes reach `main` through pull requests.

## Requirements

- Python 3 and `pip install networkx`
- For the FRR validation: Linux with Docker and containerlab. On Windows, use WSL2 (Ubuntu) with Docker Desktop's WSL integration, and clone the repo inside the WSL filesystem.

## Running the link-state code

```
python test_ls.py
python run_ls_demo.py topo_demo.json sim_tables.json
```

`test_ls.py` runs 40 random graphs through cost changes, failures (including partitions) and recoveries, and checks every node's table against NetworkX. It also runs a 200-node case.

## OSPF validation lab

```
python frr/ospf_tools.py gen topo_demo.json lab
sudo containerlab deploy -t lab/ospf-lab.clab.yml
docker exec clab-ospf-lab-r1 vtysh -c "show ip route ospf"
python frr/ospf_tools.py extract lab/lab_meta.json frr_tables.json
python frr/compare_ospf.py sim_tables.json frr_tables.json
python frr/ospf_tools.py fail lab/lab_meta.json A B
```

## Status

| Component | Status |
|---|---|
| `ls_node.py`, `metrics.py`, `fake_sim.py` | Tested (`test_ls.py`) |
| `contract.md` | Draft, awaiting confirmation |
| `frr/ospf_tools.py` | Written, not yet run against a live lab |
| `frr/compare_ospf.py` | Tested on synthetic data only |

Items to verify on the first live run:

- The FRR image tag in `ospf_tools.py` (`IMAGE`) exists and can be pulled.
- Whether FRR includes the loopback's cost in the OSPF metric. `compare_ospf.py` assumes 1 and warns if the observed offset differs.
- The `timers throttle spf` line and the `ip addr add` exec lines are accepted. `docker logs clab-ospf-lab-r1` shows the reason if a router fails to start.
- Real OSPF and the simulator will not agree on convergence time in seconds. Compare how the shape and message counts scale, not the raw numbers.

## Integration

`run_ls_demo.py` currently uses `FakeSim`. Once the engine follows `contract.md`, swap it in. If method names differ, update the contract rather than the node code.

## AI use

Parts of the code and documentation in this repository were drafted with the help of Claude.