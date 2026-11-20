# WLAN Patch Impact — Production QA for ath10/11/12

**One report per Gerrit patch. Two questions. Evidence required.**

| Question | Answer with |
|----------|-------------|
| **Q1. What else does this affect?** | Diff → compiled functions → static neighbors → **contract hop** → **measured scenario coverage** |
| **Q2. Where else does this bug exist?** | Contract symmetry · ops-table siblings · near-clones (fix patches only) |

Pause deep knowledge-graph / ownership / full-history work until SZZ replay (step 5) shows what is missing.

## Why this direction (and why graphs alone fail)

Static call graphs over-connect (function pointers, ops tables) and under-connect (FW handlers, macros, NSS callbacks).  
The missing piece is a **scenario → functions map measured on the boards**. Impact becomes an *intersection*:

```
changed_functions ∩ scenario_coverage  →  scenarios you must run
```

## Two Alder boards

| Board | Role |
|-------|------|
| **Board 1 (DUT AP)** | Flash baseline / patched images, collect gcov/kcov, FW logs, NSS stats, kmemleak |
| **Board 2 (peer STA)** | Assoc / MLO client / peer-storm generator |

Set `DUT_HOST` and `PEER_HOST`. Leave `FIXTURE_MODE=1` (default) to demo without boards.

## Scenarios (start here)

1. `mlo_link_add_remove` — ath12k MLO  
2. `slo_assoc_churn` — ath10/11/12  
3. `peer_create_delete_storm` — peer lifetime  
4. `skb_tx_rx_pressure` — SKB / recycle / NSS  
5. `recovery_target_assert` — FW assert recycle  
6. `apss_crash_stress` — delete-during-TX  
7. `rekey_gtk_ptk` — key install races  
8. `roam_dfs_channel` — CSA / DFS  

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
FIXTURE_MODE=1 python demo/run_demo.py
```

Open `web/index.html` for the visual scenario map, or view a report:

```bash
python -m impact.report CR-mlo-tlv-len-fix
```

## Live boards

```bash
export FIXTURE_MODE=0
export DUT_HOST=alder-dut
export PEER_HOST=alder-peer
python -m runner.scenario_runner mlo_link_add_remove peer_create_delete_storm
```

## Layout

```
configs/          layers, scenarios, boards
runner/           SSH/fixture driver + scenario runner
impact/           Q1/Q2 report generator
fixtures/         coverage maps + sample patches (replace with measured data)
demo/             end-to-end demo
web/              interactive visual
```

## Next 2 weeks (locked)

1. Scenario runner on both boards (6 scenarios)  
2. Coverage map on baseline 12.5  
3. Report generator wired to real diffs  
4. Baseline vs patch gates  
5. SZZ replay on 20 CRs — that is the proof  
