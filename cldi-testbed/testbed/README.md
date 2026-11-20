# testbed: two-board per-patch Wi-Fi QA (baseline vs candidate)

The testbed runs the same scenarios on a **baseline** image and then on a **candidate** (patched) image of the same DUT. It collects kernel, driver, firmware, memory and traffic signals, applies gates, and writes a JSON + Markdown report. It is built for QSDK 12.5 routers with ath10k/ath11k/ath12k-style host drivers, WLAN FW, NSS offload and the SKB recycler.

Python 3 stdlib only. It talks to the boards over `ssh`. A serial console is optional: install `pyserial` to use it for crash capture or as the transport.

> **Status, stated plainly.** The code runs end to end with the **simulated** backend, and the unit tests pass. It has **not** been run against real Alder boards yet. Commands and paths marked `placeholder` or `VERIFY` are assumptions about QSDK 12.5 that you need to check on your image (see [Unverified](#unverified-placeholders-and-assumptions)).

```
            controller (Linux, python3, ssh, optional pyserial)
               |  ssh / serial                 |  ssh / serial
        +------+------+     RF (cabled+atten   +------+------+
        | board1: DUT |  <------------------>  | board2: peer|
        | AP, image   |   or shield box)       | STA / MLO   |
        | under test  |                        | STA, iperf3 |
        +-------------+                        +-------------+
```

## Quick start (simulated, no hardware)

From the directory that contains `testbed/`:

```sh
python3 -m testbed list
python3 -m testbed run --backend sim --build baseline  --allow-destructive --out testbed/runs/demo/baseline
python3 -m testbed run --backend sim --build candidate --allow-destructive --out testbed/runs/demo/candidate
python3 -m testbed compare testbed/runs/demo/baseline testbed/runs/demo/candidate --out testbed/runs/demo
python3 -m unittest discover -s testbed/tests -t .
```

In the sim backend, the candidate build carries four seeded faults: a peer refcount leak, a recycler skb leak, a FW assert on MLO link removal, and a 7% NSS throughput dip. These show the gates firing. Every sim artifact is labelled **SIMULATED**. Use `--sim-faults none` to check for false positives. With faults off, five seeds (2–6) all compared PASS.

## Per-patch flow on real boards

1. Flash the **baseline** image on board1 with your normal sysupgrade/TFTP flow. Flashing is not automated.
2. `python3 -m testbed run --build baseline --select-from-patch my.patch --out runs/P123/baseline`
3. Flash the **candidate** image (baseline + patch, same config).
4. `python3 -m testbed run --build candidate --scenarios <same list> --out runs/P123/candidate`
5. `python3 -m testbed compare runs/P123/baseline runs/P123/candidate --out runs/P123` exits 2 on FAIL (`--fail-on warn` to be stricter). This makes it usable as a CI step.

Set `builds.<build>.expect_image_contains` in `boards.json`. The runner then refuses to label a run "candidate" while the DUT is still on the baseline image.

Before you trust the throughput gates, run the baseline twice and compare it against itself. If self-compare is not PASS, raise `throughput.warn_drop_pct` or add iperf repetitions. RF setup (cabled + attenuators or a shield box) matters more than any threshold.

## Scenario selection from impact

```sh
python3 -m testbed select --select-from-patch patches/0001-ath12k-dp-srng-setup-leak.patch
python3 -m testbed select --select-from-impact testbed/examples/impact_peer_mlo.json --top 4
python3 -m testbed run --build candidate --select-from-impact impact.json ...
```

`impact_map.json` maps regexes over touched files/functions to components. Each scenario declares the components it exercises, and selection ranks them by weighted overlap. `slo_assoc_basic` is always kept as a smoke test. The tool also warns when a patch touches a different driver than the one the DUT runs (e.g. an ath10k patch with an ath12k DUT): that code never executes there.

This map is static and coarse; most scenarios touch WMI/peers. Test-impact analysis gets precise once you record per-scenario GCOV coverage on the boards (below) and replace the regex map with "which scenarios executed these lines".

## What to install / enable on the boards

Both boards: `iperf3`, `iw`, a working `ssh` server (dropbear; put the controller key in `/etc/dropbear/authorized_keys`), and busybox `seq`/`ping`.

| Board | Needs |
|---|---|
| DUT | `hostapd` with control interface (`hostapd_cli`). For rekey: REKEY_GTK/REKEY_PTK support. For MLO: a hostapd built with 802.11be/MLO and ML-reconfiguration support |
| Peer | `wpa_supplicant` + `wpa_cli` with 802.11be/MLO for `mlo_link_churn`; config files at `vars.wpa_conf_slo` / `vars.wpa_conf_mlo` |

Kernel options on the DUT image. These are kernel symbols; the OpenWrt/QSDK menuconfig names for them (e.g. `CONFIG_KERNEL_*`) are **VERIFY**.

| Signal | Kernel config / boot args |
|---|---|
| driver debugfs (peers, simulate_fw_crash, simulate_radar) | `CONFIG_DEBUG_FS`, `CONFIG_ATH1xK_DEBUGFS` |
| slabinfo | `CONFIG_SLUB_DEBUG` (for `/proc/slabinfo`), boot with `slub_nomerge` so `skbuff_head_cache` is not merged into a `kmalloc-*` cache |
| kmemleak | `CONFIG_DEBUG_KMEMLEAK=y`, `CONFIG_DEBUG_KMEMLEAK_DEFAULT_OFF=n` (or boot `kmemleak=on`); raise `CONFIG_DEBUG_KMEMLEAK_MEM_POOL_SIZE` on 1 GB boards |
| FW dump | `CONFIG_DEV_COREDUMP` (devcoredump), plus the QSDK ramdump collection path (VERIFY; add it to `paths.ramdump_globs`) |
| APSS crash evidence | `CONFIG_PSTORE`, `CONFIG_PSTORE_RAM` + ramoops reserved-memory in DT, `CONFIG_MAGIC_SYSRQ`; a serial console + `pyserial` on the controller |

kmemleak and slub debugging change timing and memory. Run gate builds with them on, and run throughput comparisons on a separate pair of images with them off.

### GCOV (coverage → precise test selection)

1. Kernel: `CONFIG_GCOV_KERNEL=y`. Do **not** use `GCOV_PROFILE_ALL` on a router (size and speed). Instead add `GCOV_PROFILE := y` to the Makefile/Kbuild of the modules you care about: the ath1xk driver dir, and the backports `mac80211`/`cfg80211` if QSDK builds them from backports. Do the same for the NSS host driver (`qca-nss-drv`) and the recycler, if it is a module.
2. Boot, then reset the counters: `echo 1 > /sys/kernel/debug/gcov/reset`.
3. Run one scenario, then copy `/sys/kernel/debug/gcov/<build-path>/**/*.gcda` off the board (tar over ssh).
4. On the build host, put the `.gcda` next to the matching `.gcno` from the **same** build and run `gcov`/`lcov`.
5. Save per scenario the list of `file:line` executed. This becomes the scenario→code map that replaces `impact_map.json`.

FW and NSS FW coverage is not visible this way. For FW, use the FW log/trace IDs you already get (QDSS/diag/fwlog) per scenario.

## Signals and gates

| Collector | Source (missing → "not available", never a failure) |
|---|---|
| dmesg diff + signatures | `dmesg` before/after (timestamp-based, reboot-aware), plus pstore and serial console after a host crash |
| crash events | fatal: `Kernel panic`, `Unable to handle kernel`, `BUG:`, `skb_over_panic`, lockups/stalls. FW: `firmware crashed`, `FW_ASSERT`, `Target Assert`, `fatal error received`, `MHI_CB_EE_RDDM`. Lines within 10 s count as one event |
| timeouts | `wmi command N timeout`, `WMI timeout`, `HTT timeout`, `failed to …: -110` |
| ramdump | new files under `paths.ramdump_globs` (devcoredump, QSDK dump paths) |
| memory | `/proc/meminfo`, `/proc/slabinfo` (skbuff_head_cache, skbuff_fclone_cache, kmalloc-256…4k) sampled every loop |
| kmemleak | `echo scan`, then count unreferenced objects whose backtrace matches ath1xk/mac80211/cfg80211/nss/skb |
| peers | `iw dev <ap_if> station dump` count vs driver debugfs peer list (`paths.peer_debugfs_globs`), pre/per-loop/post |
| recycler | `/proc/net/skb_recycler/*`, summing keys matching `pool_key_regex` |
| NSS | `/sys/kernel/debug/qca-nss-drv/stats/{n2h,wifili,drv,…}`, delta pre→post |
| throughput | iperf3 `-J` (TCP `sum_received`, UDP `sum` + `lost_percent`) |
| CPU | `/proc/stat` busy % over the scenario |

| Gate | Rule (thresholds in `thresholds.json`, override with `--thresholds`) |
|---|---|
| crash | any fatal or FW-crash event beyond what the scenario injects → FAIL |
| timeouts | candidate WMI/HTT timeout lines > baseline → FAIL; equal and >0 → WARN (pre-existing) |
| peer_leak | peers left behind after teardown + cooldown (post − pre, in stack **and** driver view) > 0 → FAIL. Delta-based, so a leak from an earlier scenario is not blamed on this one |
| slab_slope | least-squares slope of active objects per loop (candidate − baseline) > 32 → WARN, > 128 → FAIL; only if r² ≥ 0.7 and ≥ 5 points |
| recycler | pool total after cooldown differs from pre by more than max(64, 2%) → FAIL |
| throughput | worst traffic step median drop vs baseline > 5% → WARN, > 10% → FAIL |
| udp_loss | median loss +1 pt → WARN, +5 pts → FAIL |
| kmemleak | new wlan-related unreferenced objects vs baseline → FAIL |
| cpu | APSS CPU +15 pts → WARN (offload falling back to host) |
| nss | drop/exception counters grew > 20% and ≥ 100 vs baseline → WARN |
| recovery | injected crash not observed, or any recovery check (AP up, STA reassoc, ping) failed → FAIL |

## Scenario catalog (`scenarios/*.json`)

| id | drivers | what it proves |
|---|---|---|
| slo_assoc_basic | all | Smoke test: SLO association, TCP DL/UL, clean teardown |
| peer_churn | all | 50 assoc/disassoc cycles (alternating STA- and AP-initiated) leave no peer in mac80211, driver or FW; catches silent refcount leaks |
| mlo_link_churn | ath12k, needs `mlo` | ML peer + per-link peer bookkeeping across repeated link removal/addition under traffic |
| skb_ownership_traffic | all | Peer teardown during traffic: no leaked or double-freed skbs from late TX completions / RX flush |
| skb_recycler_stress | all, QSDK, needs `recycler` | Recycler pool returns to baseline after burst/idle cycles |
| nss_offload_traffic | all, QSDK, needs `nss` | NSS data path keeps throughput, drops and CPU at baseline |
| fw_crash_recovery | all | FW assert injection → dump → SSR → vdevs/peers restored → traffic |
| apss_crash_recovery | all, **destructive** | Host panic → pstore/ramdump → reboot → WLAN back (needs `--allow-destructive`) |
| rekey_gtk | all | GTK/PTK rekey during UDP traffic without loss spikes or WMI timeouts |
| roam | all | Reassociation between two BSSes on the DUT leaves no stale peer |
| dfs_cac_radar | all, needs `dfs` | Simulated radar → CSA → BSS and peer survive |
| vdev_updown_churn | all | BSS disable/enable and netdev down/up keep vdev/peer create/delete balanced |
| reset_no_suspend | all | wifi reload and module unload/reload (no system suspend) release memory, and FW re-init has no timeouts |

Each step has `on` (dut|peer), an `action` (metadata the sim uses), and `cmd` or `cmd_by_driver`. Optional fields: `every`/`offset` (alternate loops), `capture: iperf3`, `recovery_check`, `wait_s`, `wait_reachable_s`, `ignore_rc`, `on_fail: abort`. Templates like `{ap_if}` come from `boards.json` `vars`.

**Placeholder steps** (`"placeholder": "<reason>"`) are skipped on real boards unless you either:

- give a replacement in `boards.json` → `command_overrides["<scenario>.<step>"]`, or
- pass `--allow-placeholders`.

Skipped placeholders are listed in the report.

## Output layout

```
runs/<id>/meta.json                 build, backend, DUT identity (uname, image, FW version)
runs/<id>/run_summary.json          per-scenario status + absolute verdict
runs/<id>/<scenario>/summary.json   all signals + absolute gates
runs/<id>/<scenario>/steps.jsonl    every step: cmd, rc, duration, output tail
runs/<id>/<scenario>/dmesg_new.log  new kernel lines; pstore.log / console.log when present
runs/<id>/<scenario>/iperf/*.json   raw iperf3 output
<compare-out>/report.json, report.md
```

## Unverified: placeholders and assumptions

- **Board/chip**: "Alder" is assumed to be an IPQ95xx reference board with QCN9274 radios. Interface names (`phy0-ap0`, `ap-mld0`, `wlan0`) and IPs are examples.
- **Driver debugfs**: the peer list path (`/sys/kernel/debug/ath12k/*/mac*/peers`) and `{debugfs_dev}/simulate_fw_crash` / `mac0/simulate_radar` for ath12k are not verified. The ath10k/ath11k paths follow upstream naming.
- **MLO link remove/add**: `hostapd_cli link_remove` / `link_add` are placeholders. Use whatever your hostapd/QSDK exposes (ML reconfiguration or disabling the affiliated BSS).
- **Rekey**: `hostapd_cli rekey_gtk` / `rekey_ptk` may need a testing build of hostapd.
- **QSDK paths**: `/proc/net/skb_recycler/*`, its file and key names (the gate sums keys matching `pool`), and `/sys/kernel/debug/qca-nss-drv/stats/{n2h,wifili,drv,eth_rx}`.
- **Proprietary tools**: if your stack uses `cfg80211tool`/`iwpriv` (qca-wifi), add them via `command_overrides`; none are hard-coded.
- **Module names / load order** for `reset_no_suspend` (`ath12k_pci ath12k`, NSS dependencies).
- **Ramdump location** on QSDK 12.5 beyond devcoredump.
- **Signature strings**: upstream-style messages. Add your FW's real assert/timeout strings to `thresholds.json`.

## Limits

- One peer board gives one STA (or one MLO STA). Scale tests (many clients) need more VIFs on the peer, or a traffic generator.
- FW-internal state (peer tables in FW, MLO manager) is only seen through host symptoms here. Add a FW-log collector if you can pull fwlog/QDSS per scenario.
- Simulated numbers (Mbps, slab counts) are invented for plausibility. Only the pipeline is demonstrated, not any property of your code.
