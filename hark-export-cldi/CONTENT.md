# CLDI Time Machine

Source: https://hark.com/site/f43d67fc-3b43-4c40-879d-3e76a87f6031?hark_region=us

> Offline capture. Hark itself is a WebGL shell around the board; the runnable offline app is `../CLDI-time-machine.html`. Screenshots under `screenshots/` are the visual record from the live site (built 2026-11-24).

## Nav

Feature 0 — hero · CR journey · All fixes · How it works

## Hero

- Title: CLDI Time Machine
- Meta: 2,324 commits · git.kernel.org torvalds/linux (cgit), upstream ath11k + ath12k · built 2026-11-24
- Default feature: **MLO (Multi-Link Operation) · ath12k** — Wi-Fi 7 multi-link: one station (MLD) talking on several radios at once.

## Feature chips (examples)

| Feature | Commits |
|---------|---------|
| MLO (Multi-Link Operation) | 237 |
| Peer lifecycle | 113 |
| Crash & recovery | 134 |
| AHB / IPQ SoC bring-up | 47 |
| Monitor mode | 116 |
| WoW / power | 82 |
| ath11k peer lifecycle | 87 |

Search: “Build your own timeline” (word or regex) · driver filter `ath12k` / `ath11k`.

## KPI strip (MLO example)

| Stat | Value |
|------|-------|
| first commit | 24 Mar 2023 |
| age | 3.7 years |
| commits | 237 |
| lines | +13,512 (−4,355) |
| are fixes | 23% |
| authors | 46 |
| components | 15 |
| chapter now | Harden |

## Panels

1. **0 → hero** — cumulative code added; chapter bands (Inception / Build / Harden); brush a window
2. **What happened, component by component** — swimlane dots (size = lines); zoom All → 10 min
3. **Chapters** — months labelled Build (&lt;40% fixes) or Harden (≥40%); neighbouring months merge
4. **Rhythm** — yearly commit heatmap
5. **Where it lives in code** — hottest functions
6. **Every commit** — full list for the active feature / window

## Related

- Interactive HTML: `CLDI-time-machine.html`
- Earlier Hark (Patch QA storyboard): `hark-export/`
