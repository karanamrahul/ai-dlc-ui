"""Scenario catalog: load + validate, applicability per board, risk-based selection."""
import glob
import os
import re

from .config import DRIVERS, PKG_DIR, load_json

REQUIRED = ("id", "title", "proves", "applies_to", "components", "loops", "signals", "gates", "steps")
KNOWN_GATES = {"crash", "timeouts", "peer_leak", "slab_slope", "recycler", "throughput", "udp_loss",
               "kmemleak", "cpu", "nss", "recovery"}


def load_catalog(directory=None):
    directory = directory or os.path.join(PKG_DIR, "scenarios")
    cat = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        s = load_json(path)
        validate(s, path)
        cat[s["id"]] = s
    return cat


def validate(s, where="scenario", vocabulary=None):
    for k in REQUIRED:
        if k not in s:
            raise ValueError(f"{where}: missing '{k}'")
    bad = set(s["applies_to"].get("drivers", [])) - set(DRIVERS)
    if bad:
        raise ValueError(f"{where}: unknown drivers {bad}")
    bad = set(s["gates"]) - KNOWN_GATES
    if bad:
        raise ValueError(f"{where}: unknown gates {bad}")
    if vocabulary:
        bad = set(s["components"]) - set(vocabulary)
        if bad:
            raise ValueError(f"{where}: components not in impact_map vocabulary: {bad}")
    ids = set()
    for phase in ("setup", "loop", "teardown"):
        for st in s["steps"].get(phase, []):
            if st["on"] not in ("dut", "peer"):
                raise ValueError(f"{where}: step {st.get('id')} has on={st['on']!r}")
            if "cmd" not in st and "cmd_by_driver" not in st:
                raise ValueError(f"{where}: step {st.get('id')} has no cmd")
            ids.add(st["id"])


def applicable(s, dut, allow_destructive=False):
    """(ok, reason). Applicability is decided by the DUT's driver and capabilities."""
    a = s["applies_to"]
    if dut.driver not in a.get("drivers", DRIVERS):
        return False, f"not applicable to {dut.driver} (applies to {', '.join(a['drivers'])})"
    if a.get("requires_qsdk") and not dut.qsdk:
        return False, "QSDK-only scenario; DUT is not marked qsdk"
    missing = set(a.get("requires_caps", [])) - dut.caps
    if missing:
        return False, f"DUT lacks capabilities {sorted(missing)}"
    if a.get("destructive") and not allow_destructive:
        return False, "destructive (reboots DUT); pass --allow-destructive"
    return True, ""


# ------------------------------------------------------------------ impact -> scenarios
def load_impact_map(path=None):
    return load_json(path or os.path.join(PKG_DIR, "impact_map.json"))


def components_from_items(items, imap):
    """Touched files / function names -> {component: [items that implied it]}."""
    out = {}
    for item in items:
        for rule in imap["rules"]:
            if re.search(rule["re"], item, re.I):
                for c in rule["components"]:
                    out.setdefault(c, [])
                    if item not in out[c]:
                        out[c].append(item)
    return out


def impact_from_patch(text):
    """Unified diff -> {'files': [...], 'functions': [...]} using '+++' paths and @@ hunk context."""
    files, funcs = [], []
    for ln in text.splitlines():
        if ln.startswith("+++ b/"):
            files.append(ln[6:].strip())
        elif ln.startswith("@@"):
            m = re.search(r"@@[^@]*@@\s*(.*)", ln)
            ctx = m.group(1) if m else ""
            fm = re.search(r"([A-Za-z_]\w*)\s*\(", ctx)
            if fm:
                funcs.append(fm.group(1))
    return {"files": sorted(set(files)), "functions": sorted(set(funcs))}


def normalise_impact(impact):
    """Accepts {'components': [...]}, {'files': [...], 'functions': [...]}, or a list of
    CLDI function ids like 'ath12k:ath12k/ce.c:ath12k_ce_rx_post_pipe'."""
    if isinstance(impact, list):
        impact = {"functions": impact}
    items = list(impact.get("files", [])) + list(impact.get("functions", []))
    for f in impact.get("fids", []):
        items += f.split(":")[1:]
    return set(impact.get("components", [])), items


def select(impact, catalog, imap, dut=None, allow_destructive=False):
    """Rank scenarios by weighted overlap between touched components and scenario components."""
    explicit, items = normalise_impact(impact)
    derived = components_from_items(items, imap)
    touched = set(explicit) | set(derived)
    w = imap.get("weights", {})
    ranked, skipped = [], []
    for sid, s in catalog.items():
        hit = sorted(touched & set(s["components"]))
        always = sid in imap.get("always", [])
        if not hit and not always:
            continue
        if dut is not None:
            ok, why = applicable(s, dut, allow_destructive)
            if not ok:
                skipped.append({"id": sid, "why": why})
                continue
        score = sum(w.get(c, 1) for c in hit)
        ranked.append({"id": sid, "score": score, "matched": hit,
                       "why": "smoke (always)" if always and not hit else
                       "; ".join(f"{c} <- {', '.join(derived.get(c, ['explicit'])[:3])}" for c in hit)})
    min_score = imap.get("min_score", 1)
    for r in ranked:
        if r["score"] < min_score and r["id"] not in imap.get("always", []):
            skipped.append({"id": r["id"], "why": f"score {r['score']} < min_score {min_score} ({', '.join(r['matched'])})"})
    ranked = [r for r in ranked if r["score"] >= min_score or r["id"] in imap.get("always", [])]
    ranked.sort(key=lambda r: (-r["score"], r["id"]))
    warnings = []
    touched_drivers = sorted({m.group(0) for i in items for m in [re.search(r"ath1[012]k", i)] if m})
    if dut is not None and touched_drivers and dut.driver not in touched_drivers:
        warnings.append(f"patch touches {', '.join(touched_drivers)} but the DUT runs {dut.driver}: the changed code "
                        f"does not execute on this DUT; results only show the patch builds/loads without side effects")
    return {"touched_components": sorted(touched), "touched_drivers": touched_drivers, "derived_from": derived,
            "selected": ranked, "skipped": skipped, "warnings": warnings}
