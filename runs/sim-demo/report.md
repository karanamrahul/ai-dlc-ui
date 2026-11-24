> **SIMULATED RESULTS.** Produced by the testbed sim backend with deliberately seeded faults in the
> candidate build. Nothing in this report was measured on hardware. Use it to check the pipeline,
> not the patch.

# Patch QA report: candidate vs baseline

- Overall: **FAIL**
- DUT: name=alder-1, driver=ath12k, chip=QCN9274 (VERIFY), platform=IPQ95xx 'Alder' reference board (VERIFY), qsdk=True, capabilities=['mlo', 'dfs', 'nss', 'recycler']
- Baseline image: QSDK 12.5 SIMULATED image (baseline)
- Candidate image: QSDK 12.5 SIMULATED image (candidate)
- Generated: 2026-11-20T08:02:33+00:00

Seeded faults in this simulated candidate:

- `peer_churn`: driver peer refcount leak: 1 of every 10 teardowns leaves the peer in the driver table (silent, no dmesg)
- `skb_recycler_stress`: 150 skbs per traffic burst never returned to the recycler pool
- `mlo_link_churn`: FW assert (preceded by WMI timeout) on MLO link removal in loop 7
- `nss_offload_traffic`: 7% throughput dip with extra NSS tx drops

## Gates that fired

| Scenario | Gate | Verdict | Detail |
|---|---|---|---|
| mlo_link_churn | crash | **FAIL** | 0 unexpected kernel-fatal + 1 unexpected FW-crash event(s); first: [  728.980000] ath12k_pci 0000:01:00.0: firmware crashed: MHI_CB_EE_RDDM (SIMULATED) |
| mlo_link_churn | timeouts | **FAIL** | 2 new WMI/HTT timeout line(s) vs baseline; first: [  725.770000] ath12k_pci 0000:01:00.0: wmi command 24581 timeout (SIMULATED) |
| nss_offload_traffic | throughput | WARN | worst step 'tcp_dl': median 1602.5 vs 1727.5 Mbps (-7.2%, n=5; warn >5% drop, fail >10%) |
| nss_offload_traffic | nss | WARN | wifili.tx_drops: 915 -> 12903 |
| peer_churn | peer_leak | **FAIL** | after 50 cycles + cooldown: driver peer table: 5 peer(s) left behind (pre 0 -> post 5, expected +0) |
| skb_recycler_stress | recycler | **FAIL** | pool did not return to baseline after cooldown: 8179 -> 3760 (-4419, tol ±164) |
| skb_recycler_stress | slab_slope | **FAIL** | skbuff_head_cache: +149.8 objs/loop (r2=1.00, baseline -0.1/loop) |

## Per scenario

| Scenario | Verdict | Loops (base/cand) | Gates |
|---|---|---|---|
| apss_crash_recovery | PASS | 1/1 | crash:PASS, recovery:PASS |
| dfs_cac_radar | PASS | 3/3 | crash:PASS, timeouts:PASS, peer_leak:PASS |
| fw_crash_recovery | PASS | 3/3 | crash:PASS, recovery:PASS, timeouts:PASS, peer_leak:PASS, throughput:PASS |
| mlo_link_churn | **FAIL** | 20/20 | crash:FAIL, timeouts:FAIL, peer_leak:PASS, throughput:PASS, slab_slope:PASS |
| nss_offload_traffic | WARN | 5/5 | crash:PASS, timeouts:PASS, throughput:WARN, udp_loss:PASS, cpu:PASS, nss:WARN |
| peer_churn | **FAIL** | 50/50 | crash:PASS, timeouts:PASS, peer_leak:FAIL, slab_slope:PASS, kmemleak:PASS |
| rekey_gtk | PASS | 10/10 | crash:PASS, timeouts:PASS, udp_loss:PASS, throughput:PASS |
| reset_no_suspend | PASS | 5/5 | crash:PASS, timeouts:PASS, slab_slope:PASS, kmemleak:PASS |
| roam | PASS | 10/10 | crash:PASS, timeouts:PASS, peer_leak:PASS, throughput:PASS |
| skb_ownership_traffic | PASS | 15/15 | crash:PASS, timeouts:PASS, slab_slope:PASS, kmemleak:PASS, peer_leak:PASS |
| skb_recycler_stress | **FAIL** | 30/30 | crash:PASS, recycler:FAIL, slab_slope:FAIL |
| slo_assoc_basic | PASS | 3/3 | crash:PASS, timeouts:PASS, peer_leak:PASS, throughput:PASS |
| vdev_updown_churn | PASS | 20/20 | crash:PASS, timeouts:PASS, peer_leak:PASS, slab_slope:PASS |

### apss_crash_recovery: APSS (host kernel) crash -> reboot -> ramdump/pstore -> service restored

| Gate | Verdict | Baseline | Candidate | Detail |
|---|---|---|---|---|
| crash | PASS | fatal=1, fw_crash=0 | fatal=1, fw_crash=0, expected_fatal=1, expected_fw_crash=0, new_ramdumps=1 | no unexpected crash/assert signatures |
| recovery | PASS | - | injected=1, crash_events_seen=1, checks_passed=2, checks_failed=0 | 1 crash(es) observed, all 2 recovery checks passed |

### dfs_cac_radar: DFS: CAC, simulated radar, CSA away, peer survives

| Gate | Verdict | Baseline | Candidate | Detail |
|---|---|---|---|---|
| crash | PASS | fatal=0, fw_crash=0 | fatal=0, fw_crash=0, expected_fatal=0, expected_fw_crash=0, new_ramdumps=0 | no unexpected crash/assert signatures |
| timeouts | PASS | 0 | 0 | no new WMI/HTT timeouts |
| peer_leak | PASS | station_dump=0, driver=0 | loops=3, station_dump=0->0 (+0), driver=0->0 (+0) | no peers left behind |

### fw_crash_recovery: WLAN FW crash injection + recovery (SSR)

| Gate | Verdict | Baseline | Candidate | Detail |
|---|---|---|---|---|
| crash | PASS | fatal=0, fw_crash=3 | fatal=0, fw_crash=3, expected_fatal=0, expected_fw_crash=3, new_ramdumps=3 | no unexpected crash/assert signatures |
| recovery | PASS | - | injected=3, crash_events_seen=3, checks_passed=9, checks_failed=0 | 3 crash(es) observed, all 9 recovery checks passed |
| timeouts | PASS | 0 | 0 | no new WMI/HTT timeouts |
| peer_leak | PASS | station_dump=0, driver=0 | loops=3, station_dump=0->0 (+0), driver=0->0 (+0) | no peers left behind |
| throughput | PASS | tcp_dl=1717.3 | tcp_dl=1758.8 | worst step 'tcp_dl': median 1758.8 vs 1717.3 Mbps (+2.4%, n=3; warn >5% drop, fail >10%) |

### mlo_link_churn: MLO: multi-link association, repeated link removal/addition under traffic

| Gate | Verdict | Baseline | Candidate | Detail |
|---|---|---|---|---|
| crash | **FAIL** | fatal=0, fw_crash=0 | fatal=0, fw_crash=1, expected_fatal=0, expected_fw_crash=0, new_ramdumps=1 | 0 unexpected kernel-fatal + 1 unexpected FW-crash event(s); first: [  728.980000] ath12k_pci 0000:01:00.0: firmware crashed: MHI_CB_EE_RDDM (SIMULATED) |
| timeouts | **FAIL** | 0 | 2 | 2 new WMI/HTT timeout line(s) vs baseline; first: [  725.770000] ath12k_pci 0000:01:00.0: wmi command 24581 timeout (SIMULATED) |
| peer_leak | PASS | station_dump=0, driver=0 | loops=20, station_dump=0->0 (+0), driver=0->0 (+0) | no peers left behind |
| throughput | PASS | tput_1link=1640.6, tput_2link=2859.3 | tput_1link=1653.3, tput_2link=2854.6 | worst step 'tput_2link': median 2854.6 vs 2859.3 Mbps (-0.2%, n=20; warn >5% drop, fail >10%) |
| slab_slope | PASS | skbuff_head_cache=slope=-0.328, r2=0.097, n=20, skbuff_fclone_cache=slope=-0.098, r2=0.005, n=20, kmalloc-256=slope=-0.517, r2=0.143, n=20, kmalloc-512=slope=-0 | skbuff_head_cache=slope=-0.019, r2=0.0, n=20, skbuff_fclone_cache=slope=0.27, r2=0.036, n=20, kmalloc-256=slope=0.281, r2=0.033, n=20, kmalloc-512=slope=0.047,  | no monotonic growth above threshold |

### nss_offload_traffic: NSS offload: TCP/UDP through the NSS data path

| Gate | Verdict | Baseline | Candidate | Detail |
|---|---|---|---|---|
| crash | PASS | fatal=0, fw_crash=0 | fatal=0, fw_crash=0, expected_fatal=0, expected_fw_crash=0, new_ramdumps=0 | no unexpected crash/assert signatures |
| timeouts | PASS | 0 | 0 | no new WMI/HTT timeouts |
| throughput | WARN | tcp_dl=1727.5, tcp_ul=1580.6, udp_dl=1484.5 | tcp_dl=1602.5, tcp_ul=1473.7, udp_dl=1378.8 | worst step 'tcp_dl': median 1602.5 vs 1727.5 Mbps (-7.2%, n=5; warn >5% drop, fail >10%) |
| udp_loss | PASS | 0.289 | 0.303 | median UDP loss 0.30% vs 0.29% (+0.01 pts) |
| cpu | PASS | 21.0 | 23.0 | APSS CPU 23.0% vs 21.0% (+2.0 pts) |
| nss | WARN | wifili.tx_drops=915, wifili.rx_exceptions=45, drv.tx_fail=0 | wifili.tx_drops=12903, wifili.rx_exceptions=45, drv.tx_fail=0 | wifili.tx_drops: 915 -> 12903 |

### peer_churn: Peer lifecycle churn: N associate/disassociate cycles, STA- and AP-initiated teardown

| Gate | Verdict | Baseline | Candidate | Detail |
|---|---|---|---|---|
| crash | PASS | fatal=0, fw_crash=0 | fatal=0, fw_crash=0, expected_fatal=0, expected_fw_crash=0, new_ramdumps=0 | no unexpected crash/assert signatures |
| timeouts | PASS | 0 | 0 | no new WMI/HTT timeouts |
| peer_leak | **FAIL** | station_dump=0, driver=0 | loops=50, station_dump=0->0 (+0), driver=0->5 (+5) | after 50 cycles + cooldown: driver peer table: 5 peer(s) left behind (pre 0 -> post 5, expected +0) |
| slab_slope | PASS | skbuff_head_cache=slope=-0.059, r2=0.011, n=50, skbuff_fclone_cache=slope=0.049, r2=0.006, n=50, kmalloc-256=slope=-0.006, r2=0.0, n=50, kmalloc-512=slope=-0.04 | skbuff_head_cache=slope=0.101, r2=0.043, n=50, skbuff_fclone_cache=slope=0.029, r2=0.004, n=50, kmalloc-256=slope=0.352, r2=0.302, n=50, kmalloc-512=slope=0.09, | no monotonic growth above threshold |
| kmemleak | PASS | 0 | 0 | no new wlan-related kmemleak reports |

### rekey_gtk: GTK/PTK rekey under UDP traffic

| Gate | Verdict | Baseline | Candidate | Detail |
|---|---|---|---|---|
| crash | PASS | fatal=0, fw_crash=0 | fatal=0, fw_crash=0, expected_fatal=0, expected_fw_crash=0, new_ramdumps=0 | no unexpected crash/assert signatures |
| timeouts | PASS | 0 | 0 | no new WMI/HTT timeouts |
| udp_loss | PASS | 0.038500000000000006 | 0.0595 | median UDP loss 0.06% vs 0.04% (+0.02 pts) |
| throughput | PASS | udp_result=200.6 | udp_result=200.1 | worst step 'udp_result': median 200.1 vs 200.6 Mbps (-0.2%, n=10; warn >5% drop, fail >10%) |

### reset_no_suspend: Suspend-less reset: wifi reload and driver unload/reload

| Gate | Verdict | Baseline | Candidate | Detail |
|---|---|---|---|---|
| crash | PASS | fatal=0, fw_crash=0 | fatal=0, fw_crash=0, expected_fatal=0, expected_fw_crash=0, new_ramdumps=0 | no unexpected crash/assert signatures |
| timeouts | PASS | 0 | 0 | no new WMI/HTT timeouts |
| slab_slope | PASS | skbuff_head_cache=slope=0.4, r2=0.004, n=5, skbuff_fclone_cache=slope=1.1, r2=0.159, n=5, kmalloc-256=slope=-0.1, r2=0.001, n=5, kmalloc-512=slope=1.9, r2=0.247 | skbuff_head_cache=slope=-1.8, r2=0.106, n=5, skbuff_fclone_cache=slope=1.8, r2=0.16, n=5, kmalloc-256=slope=-1.2, r2=0.058, n=5, kmalloc-512=slope=-2.9, r2=0.60 | no monotonic growth above threshold |
| kmemleak | PASS | 0 | 0 | no new wlan-related kmemleak reports |

### roam: Roam between two BSSes on the DUT (same SSID, different radios)

| Gate | Verdict | Baseline | Candidate | Detail |
|---|---|---|---|---|
| crash | PASS | fatal=0, fw_crash=0 | fatal=0, fw_crash=0, expected_fatal=0, expected_fw_crash=0, new_ramdumps=0 | no unexpected crash/assert signatures |
| timeouts | PASS | 0 | 0 | no new WMI/HTT timeouts |
| peer_leak | PASS | station_dump=0, driver=0 | loops=10, station_dump=0->0 (+0), driver=0->0 (+0) | no peers left behind |
| throughput | PASS | tput=1647.7 | tput=1644.3 | worst step 'tput': median 1644.3 vs 1647.7 Mbps (-0.2%, n=10; warn >5% drop, fail >10%) |

### skb_ownership_traffic: SKB ownership: traffic with peer teardown mid-flight

| Gate | Verdict | Baseline | Candidate | Detail |
|---|---|---|---|---|
| crash | PASS | fatal=0, fw_crash=0 | fatal=0, fw_crash=0, expected_fatal=0, expected_fw_crash=0, new_ramdumps=0 | no unexpected crash/assert signatures |
| timeouts | PASS | 0 | 0 | no new WMI/HTT timeouts |
| slab_slope | PASS | skbuff_head_cache=slope=-0.275, r2=0.043, n=15, skbuff_fclone_cache=slope=0.071, r2=0.002, n=15, kmalloc-256=slope=-0.814, r2=0.097, n=15, kmalloc-512=slope=-0. | skbuff_head_cache=slope=-0.146, r2=0.006, n=15, skbuff_fclone_cache=slope=0.386, r2=0.119, n=15, kmalloc-256=slope=0.346, r2=0.04, n=15, kmalloc-512=slope=-0.3, | no monotonic growth above threshold |
| kmemleak | PASS | 0 | 0 | no new wlan-related kmemleak reports |
| peer_leak | PASS | station_dump=0, driver=0 | loops=15, station_dump=0->0 (+0), driver=0->0 (+0) | no peers left behind |

### skb_recycler_stress: SKB recycler: burst traffic then idle; pool must return to baseline

| Gate | Verdict | Baseline | Candidate | Detail |
|---|---|---|---|---|
| crash | PASS | fatal=0, fw_crash=0 | fatal=0, fw_crash=0, expected_fatal=0, expected_fw_crash=0, new_ramdumps=0 | no unexpected crash/assert signatures |
| recycler | **FAIL** | pre=8180, post=8194 | pre=8179, post=3760, delta=-4419, tolerance=164 | pool did not return to baseline after cooldown: 8179 -> 3760 (-4419, tol ±164) |
| slab_slope | **FAIL** | skbuff_head_cache=slope=-0.117, r2=0.02, n=30, skbuff_fclone_cache=slope=-0.002, r2=0.0, n=30, kmalloc-256=slope=0.164, r2=0.027, n=30, kmalloc-512=slope=0.035, | skbuff_head_cache=slope=149.711, r2=1.0, n=30, skbuff_fclone_cache=slope=0.089, r2=0.012, n=30, kmalloc-256=slope=0.063, r2=0.005, n=30, kmalloc-512=slope=-0.11 | skbuff_head_cache: +149.8 objs/loop (r2=1.00, baseline -0.1/loop) |

### slo_assoc_basic: SLO smoke: associate, TCP DL/UL, disassociate

| Gate | Verdict | Baseline | Candidate | Detail |
|---|---|---|---|---|
| crash | PASS | fatal=0, fw_crash=0 | fatal=0, fw_crash=0, expected_fatal=0, expected_fw_crash=0, new_ramdumps=0 | no unexpected crash/assert signatures |
| timeouts | PASS | 0 | 0 | no new WMI/HTT timeouts |
| peer_leak | PASS | station_dump=0, driver=0 | loops=3, station_dump=0->0 (+0), driver=0->0 (+0) | no peers left behind |
| throughput | PASS | tcp_dl=1722.4, tcp_ul=1592.8 | tcp_dl=1707.9, tcp_ul=1583.5 | worst step 'tcp_dl': median 1707.9 vs 1722.4 Mbps (-0.8%, n=3; warn >5% drop, fail >10%) |

### vdev_updown_churn: vdev up/down: BSS disable/enable and netdev down/up with a client

| Gate | Verdict | Baseline | Candidate | Detail |
|---|---|---|---|---|
| crash | PASS | fatal=0, fw_crash=0 | fatal=0, fw_crash=0, expected_fatal=0, expected_fw_crash=0, new_ramdumps=0 | no unexpected crash/assert signatures |
| timeouts | PASS | 0 | 0 | no new WMI/HTT timeouts |
| peer_leak | PASS | station_dump=0, driver=0 | loops=20, station_dump=0->0 (+0), driver=0->0 (+0) | no peers left behind |
| slab_slope | PASS | skbuff_head_cache=slope=-0.214, r2=0.023, n=20, skbuff_fclone_cache=slope=0.517, r2=0.083, n=20, kmalloc-256=slope=-0.334, r2=0.056, n=20, kmalloc-512=slope=0.5 | skbuff_head_cache=slope=-0.096, r2=0.009, n=20, skbuff_fclone_cache=slope=-0.601, r2=0.212, n=20, kmalloc-256=slope=0.279, r2=0.026, n=20, kmalloc-512=slope=-0. | no monotonic growth above threshold |

## Thresholds

```json
{
 "crash": {},
 "timeouts": {
  "fail_on_new": true
 },
 "peer_leak": {
  "expected_remaining": 0
 },
 "slab_slope": {
  "caches": [
   "skbuff_head_cache",
   "skbuff_fclone_cache",
   "kmalloc-256",
   "kmalloc-512",
   "kmalloc-1k",
   "kmalloc-2k",
   "kmalloc-4k"
  ],
  "warn_per_loop": 32,
  "fail_per_loop": 128,
  "min_r2": 0.7,
  "min_points": 5
 },
 "recycler": {
  "tolerance_abs": 64,
  "tolerance_pct": 2.0
 },
 "throughput": {
  "warn_drop_pct": 5.0,
  "fail_drop_pct": 10.0
 },
 "udp_loss": {
  "warn_increase_pts": 1.0,
  "fail_increase_pts": 5.0
 },
 "kmemleak": {
  "wlan_regex": "ath1[012]k|mac80211|cfg80211|nss|skb|qca|wifi"
 },
 "cpu": {
  "warn_increase_pts": 15.0
 },
 "nss": {
  "drop_key_regex": "drop|fail|exception|err",
  "warn_increase_pct": 20.0,
  "min_abs": 100
 },
 "recovery": {}
}
```

---
*SIMULATED - generated by the testbed sim backend; NOT measured on hardware*
