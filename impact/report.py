"""Patch Impact Report — answers Q1 (what else?) and Q2 (where else?)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs"
FIXTURES = ROOT / "fixtures"
COVERAGE = FIXTURES / "coverage"


@dataclass
class Claim:
    text: str
    evidence: str  # static-only | measured | contract-hop | q2-symmetry | q2-sibling | q2-clone
    layer: str = ""
    confidence: float = 0.5


@dataclass
class ImpactReport:
    patch_id: str
    title: str
    is_fix: bool
    changed_functions: list[str]
    q1_layers: list[str]
    q1_scenarios: list[dict[str, Any]]
    q1_claims: list[Claim] = field(default_factory=list)
    q2_findings: list[Claim] = field(default_factory=list)
    board_plan: list[str] = field(default_factory=list)


def _load_patch(patch_id: str) -> dict[str, Any]:
    path = FIXTURES / "patches" / f"{patch_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"missing patch fixture: {path}")
    return json.loads(path.read_text())


def _all_coverage() -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    for f in COVERAGE.glob("*.json"):
        data = json.loads(f.read_text())
        out[f.stem] = data.get("functions", {})
    return out


def _scenarios() -> list[dict[str, Any]]:
    return yaml.safe_load((CONFIG / "scenarios.yaml").read_text())["scenarios"]


def _contracts() -> list[dict[str, Any]]:
    return yaml.safe_load((CONFIG / "layers.yaml").read_text())["contracts"]


def _intersect_scenarios(changed: set[str], cov: dict[str, dict[str, list[str]]]) -> list[dict[str, Any]]:
    hits = []
    scen_meta = {s["id"]: s for s in _scenarios()}
    for sid, layers in cov.items():
        touched = []
        for layer, fns in layers.items():
            overlap = sorted(set(fns) & changed)
            if overlap:
                touched.append({"layer": layer, "functions": overlap, "evidence": "measured"})
        if touched:
            meta = scen_meta.get(sid, {})
            hits.append(
                {
                    "scenario_id": sid,
                    "name": meta.get("name", sid),
                    "family": meta.get("family", ""),
                    "overlap": touched,
                    "priority": len(touched),
                }
            )
    hits.sort(key=lambda x: -x["priority"])
    return hits


def _static_neighbors(patch: dict[str, Any]) -> list[Claim]:
    claims = []
    for n in patch.get("static_callers", []):
        claims.append(Claim(f"Caller may be affected: {n}", "static-only", patch.get("layer", "host"), 0.55))
    for n in patch.get("ops_siblings", []):
        claims.append(Claim(f"Ops-table sibling: {n}", "static-only", patch.get("layer", "host"), 0.6))
    return claims


def _contract_hop(patch: dict[str, Any]) -> list[Claim]:
    claims = []
    for hop in patch.get("contract_hops", []):
        claims.append(
            Claim(
                f"Contract {hop['contract']}: {hop['from_fn']} → {hop['to_fn']} ({hop['to_layer']})",
                "contract-hop",
                hop["to_layer"],
                0.8,
            )
        )
        # Pull reachable on the other side if listed
        for r in hop.get("reachable", []):
            claims.append(
                Claim(f"Reachable after hop: {r}", "contract-hop", hop["to_layer"], 0.7)
            )
    return claims


def _q2(patch: dict[str, Any]) -> list[Claim]:
    if not patch.get("is_fix"):
        return []
    findings: list[Claim] = []
    for s in patch.get("q2_symmetry", []):
        findings.append(
            Claim(
                f"[symmetry] {s['detail']}",
                "q2-symmetry",
                s.get("layer", ""),
                s.get("score", 0.85),
            )
        )
    for s in patch.get("q2_siblings", []):
        findings.append(
            Claim(
                f"[sibling] {s['detail']}",
                "q2-sibling",
                s.get("layer", ""),
                s.get("score", 0.75),
            )
        )
    for s in patch.get("q2_clones", []):
        findings.append(
            Claim(
                f"[clone] {s['detail']}",
                "q2-clone",
                s.get("layer", ""),
                s.get("score", 0.7),
            )
        )
    findings.sort(key=lambda c: -c.confidence)
    return findings


def generate_report(patch_id: str) -> ImpactReport:
    patch = _load_patch(patch_id)
    changed = set(patch["changed_functions"])
    # Expand with hop targets so scenario intersection sees cross-layer hits
    for hop in patch.get("contract_hops", []):
        changed.add(hop["to_fn"])
        changed.update(hop.get("reachable", []))

    scenarios = _intersect_scenarios(changed, _all_coverage())
    claims = _static_neighbors(patch) + _contract_hop(patch)
    for sc in scenarios:
        for ov in sc["overlap"]:
            claims.append(
                Claim(
                    f"Scenario {sc['scenario_id']} executes {', '.join(ov['functions'][:3])}",
                    "measured",
                    ov["layer"],
                    0.95,
                )
            )

    layers = sorted({c.layer for c in claims if c.layer} | set(patch.get("layers_touched", [])))
    board_plan = [s["scenario_id"] for s in scenarios]

    return ImpactReport(
        patch_id=patch_id,
        title=patch.get("title", patch_id),
        is_fix=bool(patch.get("is_fix")),
        changed_functions=patch["changed_functions"],
        q1_layers=layers,
        q1_scenarios=scenarios,
        q1_claims=claims,
        q2_findings=_q2(patch),
        board_plan=board_plan,
    )


def render_markdown(report: ImpactReport) -> str:
    lines = [
        f"# Patch Impact Report: {report.patch_id}",
        "",
        f"**{report.title}**",
        "",
        f"- Fix patch: {'yes' if report.is_fix else 'no'}",
        f"- Changed functions: {', '.join(f'`{f}`' for f in report.changed_functions)}",
        f"- Layers: {', '.join(report.q1_layers) or '(none)'}",
        "",
        "## Q1 — What else does this affect?",
        "",
    ]
    if not report.q1_scenarios:
        lines.append("_No measured scenario overlap yet. Run coverage map first._")
    else:
        lines.append("| Scenario | Family | Overlap (measured) |")
        lines.append("|---|---|---|")
        for s in report.q1_scenarios:
            bits = []
            for ov in s["overlap"]:
                bits.append(f"{ov['layer']}: {', '.join(ov['functions'][:4])}")
            lines.append(f"| `{s['scenario_id']}` | {s['family']} | {'; '.join(bits)} |")
    lines += ["", "### Claims", ""]
    for c in report.q1_claims:
        lines.append(f"- **[{c.evidence}]** ({c.layer}) {c.text} _(conf {c.confidence:.2f})_")

    lines += ["", "## Q2 — Where else does this bug exist?", ""]
    if not report.is_fix:
        lines.append("_Q2 runs on fix patches only._")
    elif not report.q2_findings:
        lines.append("_No twin candidates from symmetry / siblings / clones._")
    else:
        for f in report.q2_findings:
            lines.append(f"- **[{f.evidence}]** ({f.layer}) {f.text} _(conf {f.confidence:.2f})_")

    lines += ["", "## Board plan (baseline → patched)", ""]
    if report.board_plan:
        for i, s in enumerate(report.board_plan, 1):
            lines.append(f"{i}. `{s}` on DUT (board 1), peer assist on board 2")
    else:
        lines.append("_No board scenarios selected._")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    pid = sys.argv[1] if len(sys.argv) > 1 else "CR-example-mlo-tlv"
    rep = generate_report(pid)
    out = ROOT / "demo" / "out" / f"impact_{pid}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    md = render_markdown(rep)
    out.write_text(md)
    (out.with_suffix(".json")).write_text(
        json.dumps(
            {
                **asdict(rep),
                "q1_claims": [asdict(c) for c in rep.q1_claims],
                "q2_findings": [asdict(c) for c in rep.q2_findings],
            },
            indent=2,
        )
    )
    print(md)
    print(f"\nWrote {out}")
