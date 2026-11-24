# Wi-Fi Patch QA: How Every Scenario Connects

Source: https://hark.com/site/eea040f8-f57a-4193-8688-f0470b20198d?hark_region=us

> Offline capture. Hark itself is a WebGL canvas app; screenshots under `screenshots/` are the faithful visual record. Text below was transcribed from those views.

## Nav

Setup · Gates · Scenarios · Drivers · Real vs sim · Start

## Hero

- Badge: For Rahul · QSDK 12.5 · 2 Alder boards
- Title: Wi-Fi Patch QA: How Every Scenario Connects
- Body: Testing a patch has two halves. Static, with no board: what the patch touches and where the same bug may live. Dynamic, on the boards: only the scenarios it touches, baseline then patched, judged by gates.
- Stack: Host driver ath10k/ath11k/ath12k · WLAN FW · NSS + NSS FW · SKB recycler
- Next step: Run the testbed on the Alder boards with a baseline image. Start with `slo_assoc_basic` and `peer_churn`.

## Setup — two-board

One board runs the image under test, the other plays the clients.

1. Host PC runs the testbed (crash capture via serial)
2. Static, no board — impact from the patch
3. Dynamic, on the boards — selected scenarios, baseline then candidate, gates

## Gates

| Gate | Rule |
|------|------|
| crash | Unexpected kernel panic or FW assert |
| timeouts | New WMI/HTT timeout lines vs baseline |
| peer_leak | Driver peer table delta must be 0 |
| slab_slope | skbuff/kmalloc growth per loop vs baseline |
| kmemleak | New unreferenced objects |
| recycler | SKB recycler pool returns after cooldown |
| throughput | Drop vs baseline (warn >5%, fail >10%) |
| udp_loss, cpu, nss | Change vs baseline |
| recovery | After injected crash, all recovery checks pass |

## Scenarios (story panels)

MLO · SLO · Peer lifecycle · SKB ownership · SKB recycler · NSS offload · FW crash · APSS crash · Rekey

Also in catalog: roam, dfs_cac_radar, vdev_updown_churn, reset_no_suspend

Live finding called out on Hark: FW crash — possible ath12k twin of an ath11k recovery bug.

### MLO (example)

- ath12k only
- Flow: hostapd MLD link add/remove → nl80211/cfg80211 → mac80211 change_*_links → driver → WMI → FW → WMI_PEER_DELETE_RESP → race on delete resp
- Static: commit `01cc0f59aac` (2026-06-29) "wifi: ath12k: fix MLO peer delete race"
- Board test `mlo_link_churn` (simulated in testbed): seeded FW assert + WMI timeouts

## Drivers

Most scenarios apply to every driver; MLO and QSDK parts do not. On Alder (ath12k-class), "all" rows run on ath12k only.

## Real vs sim

- Static results: REAL on upstream Linux
- Testbed results: SIMULATED (pipeline proof)
- On Alder boards: not run yet
- KPIs on Hark: 6 upstream commits · 44s static · 4 seeded faults · 0 false alarms across 5 seeds

## Start — five steps to CI

1. Configure — fill `boards.json` (IPs, interfaces, driver, QSDK flags)
2. Debug image — enable kmemleak and GCOV
3. Baseline — run `slo_assoc_basic` + `peer_churn` twice; confirm noise is low
4. Prove the gates — known-bad historical patch; confirm gates fire
5. CI — `patch_impact → testbed select → run → compare` into Gerrit

## Related local work

- Interactive recreation: `wlan-patch-impact/web/guide.html`
- SRNG / interrupt diagram: `wlan-patch-impact/web/rings.html`
- CLDI testbed: `wlan-patch-impact/cldi-testbed/`
