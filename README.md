# AI-DLC UI

Interactive prototypes and WLAN Patch QA tooling.

## Motion prototypes

| Path | Name | Character |
|------|------|-----------|
| [`/helix/`](./helix/) | **Helix** | Cinematic 10-section autoplay with glass staging, agent orbit, live telemetry, device farm, typed CLI |
| [`/god-mode/`](./god-mode/) | **God Mode** | Touch-first dock nav, swipeable CR cards, interactive terminal, showcase chrome |

Landing page: [`index.html`](./index.html)

```bash
python3 -m http.server 8787
# open http://127.0.0.1:8787
```

Helix shortcuts: `←` `→` navigate · `space` pause · `1`–`0` jump to section

Plain HTML / CSS / JS. No build tools.

---

## WLAN Patch Impact (ath10/11/12)

**One report per Gerrit patch. Two questions. Evidence required.**

| Question | Answer with |
|----------|-------------|
| **Q1. What else does this affect?** | Diff → compiled functions → static neighbors → **contract hop** → **measured scenario coverage** |
| **Q2. Where else does this bug exist?** | Contract symmetry · ops-table siblings · near-clones (fix patches only) |

### Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
FIXTURE_MODE=1 python demo/run_demo.py
```

- **`hiring/index.html`** / **`web/guide.html`** — Patch QA storyboard
- **`CLDI-time-machine.html`** / **`hiring/CLDI-time-machine.html`** — CLDI Time Machine (feature 0 → hero CR journey)  
- **`web/rings.html`** — SRNG / CE / interrupt architecture  
- **`cldi-testbed/`** — two-board baseline↔candidate runner (sim + SSH)  
- **`gifts/My_Pet_Fish_by_Chloe.mp4`** — narrated gift short  

```bash
cd cldi-testbed
python3 -m testbed run --backend sim --build baseline  --allow-destructive --out runs/demo/baseline
python3 -m testbed run --backend sim --build candidate --allow-destructive --out runs/demo/candidate
python3 -m testbed compare runs/demo/baseline runs/demo/candidate --out runs/demo
```

**Nothing has run on the Alder boards yet** — fill every `VERIFY` in `boards.json` first.
