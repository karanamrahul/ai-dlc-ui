# AI-DLC UI — Motion Prototypes

Interactive UI/UX motion prototypes for an **AI Development Lifecycle** product surface.

## Prototypes

| Path | Name | Character |
|------|------|-----------|
| [`/helix/`](./helix/) | **Helix** | Cinematic 10-section autoplay with glass staging, agent orbit, live telemetry, device farm, typed CLI |
| [`/god-mode/`](./god-mode/) | **God Mode** | Touch-first dock nav, swipeable CR cards, interactive terminal, showcase chrome |

Landing page: [`index.html`](./index.html)

## Sections covered

New task (feature / bug) · Quick actions · Dashboard · Mission Control · Insights · Telemetry · CR list · Collaborate · Test farm · Terminal

## Run locally

```bash
python3 -m http.server 8787
# open http://127.0.0.1:8787
```

Helix shortcuts: `←` `→` navigate · `space` pause · `1`–`0` jump to section

## Stack

Plain HTML / CSS / JS. No build tools, no frameworks, no network calls beyond Google Fonts.
