"""compare runs/A (baseline) runs/B (candidate) -> report.json + report.md."""
import datetime as dt
import json
import os

from . import gates
from .config import SIM_LABEL, load_json, write_json

ICON = {"PASS": "PASS", "WARN": "WARN", "FAIL": "**FAIL**", "NA": "n/a"}


def _load_summary(run_dir, sid):
    p = os.path.join(run_dir, sid, "summary.json")
    return load_json(p) if os.path.exists(p) else None


def compare(base_dir, cand_dir, out_dir, catalog, thr):
    bm, cm = load_json(os.path.join(base_dir, "meta.json")), load_json(os.path.join(cand_dir, "meta.json"))
    simulated = bool(bm.get("simulated") or cm.get("simulated"))
    mixed = bool(bm.get("simulated")) != bool(cm.get("simulated"))
    sids = [s for s in cm["scenarios"] if s in bm["scenarios"]] + \
           [s for s in bm["scenarios"] if s not in cm["scenarios"]]
    rows, not_compared = [], []
    for sid in sids:
        scn = catalog.get(sid)
        b, c = _load_summary(base_dir, sid), _load_summary(cand_dir, sid)
        if scn is None or b is None or c is None:
            not_compared.append({"id": sid, "why": "scenario missing from catalog or from one run (skipped / not applicable)"})
            continue
        ev = gates.evaluate(b, c, scn, thr)
        base_abs = {g["gate"]: g["verdict"] for g in gates.evaluate(None, b, scn, thr)["gates"]}
        for g in ev["gates"]:
            g["pre_existing"] = g["verdict"] in ("WARN", "FAIL") and base_abs.get(g["gate"]) == g["verdict"] \
                and g["gate"] not in ("throughput", "udp_loss", "cpu", "nss")
        rows.append({"id": sid, "title": scn["title"], "verdict": ev["verdict"], "gates": ev["gates"],
                     "loops": {"baseline": b.get("loops_done"), "candidate": c.get("loops_done")},
                     "skipped_placeholder_steps": c.get("steps", {}).get("skipped_placeholder", []),
                     "candidate_aborted": c.get("aborted")})
    overall = gates.worst(r["verdict"] for r in rows)
    report = {
        "_label": SIM_LABEL if simulated else "hardware runs",
        "simulated": simulated,
        "warning": "baseline and candidate were collected on different backends (sim vs real); results are not comparable" if mixed else None,
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "baseline": {"dir": os.path.abspath(base_dir), "build": bm["build"], "identity": bm.get("identity")},
        "candidate": {"dir": os.path.abspath(cand_dir), "build": cm["build"], "identity": cm.get("identity")},
        "dut": cm.get("dut"), "sim": cm.get("sim"),
        "overall": overall,
        "fired": [{"scenario": r["id"], **{k: g[k] for k in ("gate", "verdict", "detail")}, "pre_existing": g["pre_existing"]}
                  for r in rows for g in r["gates"] if g["verdict"] in ("WARN", "FAIL")],
        "scenarios": rows, "not_compared": not_compared,
        "thresholds": thr["gates"],
    }
    os.makedirs(out_dir, exist_ok=True)
    write_json(os.path.join(out_dir, "report.json"), report)
    with open(os.path.join(out_dir, "report.md"), "w") as f:
        f.write(markdown(report))
    return report


def _fmt(v):
    if v is None:
        return "-"
    if isinstance(v, dict):
        return ", ".join(f"{k}={_fmt(x)}" for k, x in v.items() if x is not None)[:160]
    return str(v)


def markdown(r):
    L = []
    if r["simulated"]:
        L += ["> **SIMULATED RESULTS.** Produced by the testbed sim backend with deliberately seeded faults in the",
              "> candidate build. Nothing in this report was measured on hardware. Use it to check the pipeline,",
              "> not the patch.", ""]
    if r.get("warning"):
        L += [f"> **Warning:** {r['warning']}", ""]
    L += [f"# Patch QA report: {r['candidate']['build']} vs {r['baseline']['build']}", "",
          f"- Overall: **{r['overall']}**",
          f"- DUT: {_fmt(r.get('dut'))}",
          f"- Baseline image: {_fmt((r['baseline'].get('identity') or {}).get('image'))}",
          f"- Candidate image: {_fmt((r['candidate'].get('identity') or {}).get('image'))}",
          f"- Generated: {r['generated']}", ""]
    if r.get("sim"):
        L += ["Seeded faults in this simulated candidate:", ""]
        L += [f"- `{k}`: {v}" for k, v in r["sim"].get("faults_active", {}).items()] + [""]
    L += ["## Gates that fired", ""]
    if r["fired"]:
        L += ["| Scenario | Gate | Verdict | Detail |", "|---|---|---|---|"]
        for f in r["fired"]:
            pe = " (pre-existing in baseline)" if f["pre_existing"] else ""
            L.append(f"| {f['scenario']} | {f['gate']} | {ICON[f['verdict']]}{pe} | {f['detail']} |")
    else:
        L.append("None.")
    L += ["", "## Per scenario", "", "| Scenario | Verdict | Loops (base/cand) | Gates |", "|---|---|---|---|"]
    for s in r["scenarios"]:
        gs = ", ".join(f"{g['gate']}:{g['verdict']}" for g in s["gates"])
        L.append(f"| {s['id']} | {ICON[s['verdict']]} | {s['loops']['baseline']}/{s['loops']['candidate']} | {gs} |")
    for s in r["scenarios"]:
        L += ["", f"### {s['id']}: {s['title']}", "", "| Gate | Verdict | Baseline | Candidate | Detail |", "|---|---|---|---|---|"]
        for g in s["gates"]:
            L.append(f"| {g['gate']} | {ICON[g['verdict']]} | {_fmt(g['baseline'])} | {_fmt(g['candidate'])} | {g['detail']} |")
        if s["skipped_placeholder_steps"]:
            L.append(f"\nPlaceholder steps skipped (no override configured): {', '.join(s['skipped_placeholder_steps'])}")
        if s.get("candidate_aborted"):
            L.append(f"\nCandidate run aborted: {s['candidate_aborted']}")
    if r["not_compared"]:
        L += ["", "## Not compared", ""] + [f"- {x['id']}: {x['why']}" for x in r["not_compared"]]
    L += ["", "## Thresholds", "", "```json", json.dumps({k: {kk: vv for kk, vv in v.items() if not kk.startswith('_')}
                                                          for k, v in r["thresholds"].items()}, indent=1), "```", ""]
    if r["simulated"]:
        L += ["---", f"*{SIM_LABEL}*", ""]
    return "\n".join(L)
