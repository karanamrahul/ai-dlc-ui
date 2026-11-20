#!/usr/bin/env python3
"""End-to-end demo: run scenarios → generate impact reports → print summary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impact.report import generate_report, render_markdown  # noqa: E402
from runner.scenario_runner import run_all  # noqa: E402


def main() -> None:
    print("=" * 64)
    print("WLAN Patch Impact — live demo (FIXTURE_MODE)")
    print("=" * 64)
    print("\n[1/3] Running all scenarios on two-board topology (fixture)...\n")
    runs = run_all()
    passed = sum(1 for r in runs if r.ok)
    print(f"\nScenarios: {passed}/{len(runs)} passed\n")
    for r in runs:
        gpass = sum(1 for g in r.gates if g.passed)
        print(f"  {'PASS' if r.ok else 'FAIL':4}  {r.scenario_id:28}  gates {gpass}/{len(r.gates)}  {r.duration_s}s")

    print("\n[2/3] Generating Patch Impact Reports (Q1 + Q2)...\n")
    patches = [
        "CR-mlo-tlv-len-fix",
        "CR-peer-delete-nss-race",
        "CR-skb-recycle-underflow",
    ]
    out_dir = ROOT / "demo" / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for pid in patches:
        rep = generate_report(pid)
        md = render_markdown(rep)
        (out_dir / f"impact_{pid}.md").write_text(md)
        summary.append(
            {
                "patch": pid,
                "title": rep.title,
                "scenarios": [s["scenario_id"] for s in rep.q1_scenarios],
                "q2": len(rep.q2_findings),
                "layers": rep.q1_layers,
            }
        )
        print(f"  {pid}")
        print(f"    Q1 scenarios: {', '.join(summary[-1]['scenarios']) or '(none)'}")
        print(f"    Q2 findings:  {summary[-1]['q2']}")
        print(f"    Layers:       {', '.join(rep.q1_layers)}")
        print()

    (out_dir / "demo_summary.json").write_text(json.dumps({"scenarios": [
        {"id": r.scenario_id, "ok": r.ok, "mode": r.mode} for r in runs
    ], "patches": summary}, indent=2))

    print("[3/3] Example report (MLO TLV fix):\n")
    print(render_markdown(generate_report("CR-mlo-tlv-len-fix")))
    print(f"\nArtifacts → {out_dir}")


if __name__ == "__main__":
    main()
