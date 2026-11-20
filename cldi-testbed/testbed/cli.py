"""python3 -m testbed {run,compare,select,list} ..."""
import argparse
import datetime as dt
import json
import os
import sys

from . import report, runner
from . import scenarios as S
from .config import load_boards, load_json, load_thresholds


def _pick(args, catalog, dut_cfg):
    if args.select_from_impact or args.select_from_patch:
        if args.select_from_patch:
            with open(args.select_from_patch) as f:
                impact = S.impact_from_patch(f.read())
        else:
            impact = load_json(args.select_from_impact)
        dut = runner.Board("dut", dut_cfg, None)
        sel = S.select(impact, catalog, S.load_impact_map(args.impact_map), dut, args.allow_destructive)
        print(f"impact -> components: {', '.join(sel['touched_components']) or '(none)'}")
        for r in sel["selected"]:
            print(f"  select {r['id']:<24} score={r['score']:<3} {r['why']}")
        for w in sel.get("warnings", []):
            print(f"  WARNING: {w}")
        for r in sel["skipped"]:
            print(f"  skip   {r['id']:<24} {r['why']}")
        ids = [r["id"] for r in sel["selected"]]
        if args.top:
            ids = ids[:args.top]
        return ids, sel
    if not args.scenarios or args.scenarios == "all":
        return sorted(catalog), None
    ids = [x.strip() for x in args.scenarios.split(",") if x.strip()]
    unknown = [x for x in ids if x not in catalog]
    if unknown:
        raise SystemExit(f"unknown scenario(s): {unknown}; see `python3 -m testbed list`")
    return ids, None


def cmd_run(args):
    boards = load_boards(args.boards)
    catalog = S.load_catalog(args.scenario_dir)
    ids, sel = _pick(args, catalog, boards["dut"])
    out = args.out or os.path.join("runs", f"{args.build}-{dt.datetime.now():%Y%m%d-%H%M%S}")
    sim_opts = None
    if args.backend == "sim":
        faults = None
        if args.sim_faults == "none":
            faults = {}
        elif args.sim_faults:
            faults = load_json(args.sim_faults)
        sim_opts = {"seed": args.sim_seed, "faults": faults}
        print("NOTE: --backend sim -> every artifact is SIMULATED, not measured on hardware.")
    res = runner.run_build(boards, catalog, ids, args.build, args.backend, out, load_thresholds(args.thresholds),
                           args.allow_placeholders, args.allow_destructive, sim_opts)
    if sel:
        with open(os.path.join(out, "selection.json"), "w") as f:
            json.dump(sel, f, indent=2)
    print(f"run written to {out}")
    return 0 if all(r["verdict"] != "FAIL" for r in res.values()) else 1


def cmd_compare(args):
    catalog = S.load_catalog(args.scenario_dir)
    out = args.out or os.path.join(args.candidate, "compare")
    r = report.compare(args.baseline, args.candidate, out, catalog, load_thresholds(args.thresholds))
    if r["simulated"]:
        print("NOTE: SIMULATED results (sim backend).")
    print(f"overall: {r['overall']}")
    for f in r["fired"]:
        print(f"  {f['verdict']:<4} {f['scenario']:<22} {f['gate']:<11} {f['detail']}")
    print(f"report: {os.path.join(out, 'report.md')}")
    bad = {"fail": ("FAIL",), "warn": ("FAIL", "WARN")}[args.fail_on]
    return 2 if r["overall"] in bad else 0


def cmd_select(args):
    boards = load_boards(args.boards)
    catalog = S.load_catalog(args.scenario_dir)
    args.scenarios = None
    ids, _ = _pick(args, catalog, boards["dut"])
    print(",".join(ids))
    return 0


def cmd_list(args):
    catalog = S.load_catalog(args.scenario_dir)
    print(f"{'id':<24} {'drivers':<22} {'loops':>5}  gates")
    for sid, s in sorted(catalog.items()):
        a = s["applies_to"]
        drv = ",".join(a.get("drivers", [])) + (" +QSDK" if a.get("requires_qsdk") else "") + (" !destr" if a.get("destructive") else "")
        if args.driver and args.driver not in a.get("drivers", []):
            continue
        print(f"{sid:<24} {drv:<22} {s['loops']:>5}  {','.join(s['gates'])}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="python3 -m testbed", description="Two-board Wi-Fi per-patch QA")
    p.add_argument("--boards", help="boards.json (default: package copy)")
    p.add_argument("--thresholds", help="thresholds override JSON (deep-merged)")
    p.add_argument("--scenario-dir", help="scenario catalog dir (default: package scenarios/)")
    p.add_argument("--impact-map", help="impact_map.json override")
    sub = p.add_subparsers(dest="cmd", required=True)

    def selection_args(sp):
        sp.add_argument("--select-from-impact", metavar="impact.json",
                        help="{'components':[..]} or {'files':[..],'functions':[..]} or a list of CLDI fids")
        sp.add_argument("--select-from-patch", metavar="x.patch", help="derive impact from a unified diff")
        sp.add_argument("--allow-destructive", action="store_true", help="allow scenarios that reboot the DUT")
        sp.add_argument("--top", type=int, help="keep only the N highest-ranked selected scenarios")

    r = sub.add_parser("run", help="run scenarios on one build")
    r.add_argument("--build", required=True, choices=["baseline", "candidate"])
    r.add_argument("--scenarios", default="all", help="comma list or 'all'")
    selection_args(r)
    r.add_argument("--backend", default="real", choices=["real", "sim"])
    r.add_argument("--out", help="run dir (default runs/<build>-<timestamp>)")
    r.add_argument("--allow-placeholders", action="store_true",
                   help="run QSDK placeholder steps as written instead of skipping them (real backend)")
    r.add_argument("--sim-seed", type=int, default=1)
    r.add_argument("--sim-faults", help="JSON {scenario: {fault: value}} or 'none' (default: built-in faults on candidate)")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("compare", help="compare a baseline run dir with a candidate run dir")
    c.add_argument("baseline")
    c.add_argument("candidate")
    c.add_argument("--out", help="report dir (default <candidate>/compare)")
    c.add_argument("--fail-on", choices=["fail", "warn"], default="fail", help="exit 2 when overall verdict reaches this")
    c.set_defaults(fn=cmd_compare)

    s = sub.add_parser("select", help="print scenarios selected from an impact file or patch")
    selection_args(s)
    s.set_defaults(fn=cmd_select)

    ls = sub.add_parser("list", help="list the scenario catalog")
    ls.add_argument("--driver", choices=["ath10k", "ath11k", "ath12k"])
    ls.set_defaults(fn=cmd_list)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
