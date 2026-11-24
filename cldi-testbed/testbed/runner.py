"""Scenario execution: pre-collect -> setup -> loops (with per-loop sampling) -> teardown
-> cooldown -> post-collect -> absolute gates. Writes everything under the run dir."""
import datetime as dt
import json
import os
import time

from . import collectors as C
from . import gates
from .config import SIM_LABEL, Board, render, template_vars, write_json
from .scenarios import applicable
from .transport import ConsoleLogger, TransportError, make_transport


class StepAbort(RuntimeError):
    pass


class ScenarioRunner:
    def __init__(self, dut, peer, thr, out_dir, build, simulated, overrides=None,
                 allow_placeholders=False, allow_destructive=False, log=print):
        self.dut, self.peer, self.thr = dut, peer, thr
        self.out_dir, self.build, self.simulated = out_dir, build, simulated
        self.overrides = {k: v for k, v in (overrides or {}).items() if v and not k.startswith("_")}
        self.allow_placeholders = allow_placeholders or simulated
        self.allow_destructive = allow_destructive
        self.log = log

    # -------------------------------------------------------------- collection
    def _snapshot(self, caches):
        d = self.dut
        return {
            "dmesg": C.dmesg_lines(d.t),
            "slab": C.slab(d.t, caches),
            "meminfo": C.meminfo(d.t),
            "peers": C.peers(d),
            "recycler": C.recycler(d.t, d.paths.get("recycler", {})),
            "nss": C.nss(d.t, d.paths.get("nss", {})),
            "ramdumps": C.files_matching(d.t, d.paths.get("ramdump_globs", [])),
            "pstore": C.files_matching(d.t, d.paths.get("pstore_globs", [])),
            "cpu": C.cpu_sample(d.t),
        }

    def _loop_sample(self, caches, want_recycler):
        d = self.dut
        s = {"slab": C.slab(d.t, caches).get("objs", {}), "peers": C.peers(d)}
        if want_recycler:
            s["recycler"] = C.recycler(d.t, d.paths.get("recycler", {})).get("pool_total")
        return s

    # -------------------------------------------------------------- steps
    def _exec(self, scn, step, loop, sdir, rec):
        if "every" in step and loop is not None and loop % step["every"] != step.get("offset", 0):
            return
        board = self.dut if step["on"] == "dut" else self.peer
        key = f"{scn['id']}.{step['id']}"
        cmd = self.overrides.get(key) or step.get("cmd_by_driver", {}).get(self.dut.driver) or step.get("cmd")
        entry = {"phase": rec["phase"], "loop": loop, "step": step["id"], "on": step["on"], "action": step.get("action")}
        if cmd is None:
            entry.update(status="skipped", why=f"no command for {self.dut.driver}")
        elif step.get("placeholder") and key not in self.overrides and not self.allow_placeholders:
            entry.update(status="skipped", why=f"placeholder: {step['placeholder']}")
            rec["skipped_placeholder"].add(step["id"])
        else:
            cmd = render(cmd, template_vars(self.dut, self.peer, loop))
            try:
                r = board.t.run(cmd, timeout=step.get("timeout_s", 60), action=step.get("action"))
            except TransportError as e:
                r = None
                entry.update(status="error", why=str(e))
            if r is not None:
                entry.update(status="ok" if r.ok or step.get("ignore_rc") else "fail", rc=r.rc,
                             dur_s=round(r.dur, 2), cmd=cmd, out_tail=r.out[-400:], err_tail=r.err[-200:])
                if step.get("wait_reachable_s"):
                    ok = board.t.wait_reachable(step["wait_reachable_s"])
                    entry["reachable_after"] = ok
                    if not ok:
                        entry["status"] = "fail"
                if step.get("capture") == "iperf3":
                    tp = C.parse_iperf3(r.out)
                    if tp:
                        tp.update(step=step["id"], loop=loop)
                        rec["throughput"].append(tp)
                        os.makedirs(os.path.join(sdir, "iperf"), exist_ok=True)
                        with open(os.path.join(sdir, "iperf", f"{step['id']}_{loop}.json"), "w") as f:
                            f.write(r.out)
                if step.get("recovery_check"):
                    if entry["status"] == "ok":
                        rec["recovery"]["passed"] += 1
                    else:
                        rec["recovery"]["failed"] += 1
                        rec["recovery"]["failed_ids"].append(f"{step['id']}@{loop}")
            board.t.sleep(step.get("wait_s", 0))
        rec["steps"].append(entry)
        with open(os.path.join(sdir, "steps.jsonl"), "a") as f:
            f.write(json.dumps(entry) + "\n")
        if entry["status"] in ("fail", "error"):
            rec["failed_steps"] += 1
            if step.get("on_fail") == "abort":
                raise StepAbort(f"{key} failed (rc={entry.get('rc')})")

    # -------------------------------------------------------------- one scenario
    def run(self, scn):
        ok, why = applicable(scn, self.dut, self.allow_destructive)
        if not ok:
            return {"scenario": scn["id"], "status": "skipped", "why": why, "build": self.build,
                    "simulated": self.simulated}
        sdir = os.path.join(self.out_dir, scn["id"])
        os.makedirs(sdir, exist_ok=True)
        open(os.path.join(sdir, "steps.jsonl"), "w").close()
        caches = self.thr["gates"]["slab_slope"]["caches"]
        kml_path = self.dut.paths.get("kmemleak", "/sys/kernel/debug/kmemleak")
        wlan_re = self.thr["gates"]["kmemleak"]["wlan_regex"]
        want_rec = "recycler" in scn["gates"]
        for t in (self.dut.t, self.peer.t):
            t.mark(scenario=scn["id"], build=self.build)

        console = None
        con_cfg = self.dut.cfg.get("console")
        if not self.simulated and con_cfg and self.dut.cfg.get("transport") != "serial":
            console = ConsoleLogger(con_cfg["port"], con_cfg.get("baud", 115200), os.path.join(sdir, "console.log"))

        t0 = time.time()
        C.kmemleak_clear(self.dut.t, kml_path)
        pre = self._snapshot(caches)
        rec = {"phase": "setup", "steps": [], "failed_steps": 0, "throughput": [], "skipped_placeholder": set(),
               "recovery": {"passed": 0, "failed": 0, "failed_ids": []}}
        per_loop, loops_done, aborted = [], 0, None
        try:
            for st in scn["steps"].get("setup", []):
                self._exec(scn, st, None, sdir, rec)
            rec["phase"] = "loop"
            for i in range(scn["loops"]):
                for t in (self.dut.t, self.peer.t):
                    t.mark(loop=i)
                for st in scn["steps"].get("loop", []):
                    self._exec(scn, st, i, sdir, rec)
                loops_done = i + 1
                per_loop.append(self._loop_sample(caches, want_rec))
        except StepAbort as e:
            aborted = str(e)
        rec["phase"] = "teardown"
        for st in scn["steps"].get("teardown", []):
            try:
                self._exec(scn, st, None, sdir, rec)
            except StepAbort:
                pass
        cpu_post = C.cpu_sample(self.dut.t)
        self.dut.t.sleep(scn.get("cooldown_s", 10))
        post = self._snapshot(caches)
        kml = C.kmemleak(self.dut.t, kml_path, wlan_re)
        if console:
            console.stop()

        # ---- dmesg + pstore + console -> signatures
        new, rebooted = C.dmesg_new(pre["dmesg"], post["dmesg"])
        scan_lines = list(new or [])
        new_pstore = [p for p in post["pstore"] if p not in pre["pstore"]]
        pstore_txt = C.pstore_text(self.dut.t, new_pstore) if new_pstore else ""
        if pstore_txt:
            scan_lines += pstore_txt.splitlines()
            self._write(sdir, "pstore.log", pstore_txt)
        con_txt = console.text() if console else None
        if con_txt and (rebooted or not new):  # console only adds info dmesg can't have
            scan_lines += con_txt.splitlines()
        sig = C.scan_signatures(scan_lines, self.thr["signatures"], self.thr.get("crash_event_window_s", 10))
        self._write(sdir, "dmesg_new.log", "\n".join(new or []))

        # ---- slab trend
        series = {c: [s["slab"].get(c) for s in per_loop] for c in caches}
        series = {c: v for c, v in series.items() if v and all(x is not None for x in v)}
        fit = {c: C.linreg(list(range(len(v))), v) for c, v in series.items()}

        def nss_delta(a, b):
            if not (a.get("available") and b.get("available")):
                return {}
            return {k: b["stats"].get(k, 0) - v for k, v in a["stats"].items()}

        signals = {
            "dmesg": {"available": new is not None, "new_lines": len(new or []), "rebooted": rebooted,
                      "pstore_files": new_pstore, **sig},
            "ramdump": {"available": True, "pre": pre["ramdumps"], "post": post["ramdumps"],
                        "new": [p for p in post["ramdumps"] if p not in pre["ramdumps"]]},
            "meminfo": {"available": pre["meminfo"].get("available", False), "pre": pre["meminfo"], "post": post["meminfo"]},
            "slab": {"available": bool(series), "why": pre["slab"].get("why"), "series": series, "fit": fit,
                     "pre": pre["slab"].get("objs"), "post": post["slab"].get("objs")},
            "kmemleak": kml,
            "peers": {"available": post["peers"]["available"], "why": post["peers"].get("why"),
                      "pre": _pc(pre["peers"]), "post": _pc(post["peers"]),
                      "per_loop": [_pc(s["peers"]) for s in per_loop], "sources": post["peers"].get("driver_sources")},
            "recycler": {"available": pre["recycler"].get("available", False), "why": pre["recycler"].get("why"),
                         "pre_total": pre["recycler"].get("pool_total"), "post_total": post["recycler"].get("pool_total"),
                         "per_loop": [s.get("recycler") for s in per_loop], "keys": pre["recycler"].get("pool_keys")},
            "nss": {"available": pre["nss"].get("available", False), "why": pre["nss"].get("why"),
                    "delta": nss_delta(pre["nss"], post["nss"])},
            "throughput": {"available": bool(rec["throughput"]), "samples": rec["throughput"]},
            "cpu": C.cpu_util(pre["cpu"], cpu_post) if not rebooted else C.NA("board rebooted during scenario"),
            "recovery": rec["recovery"],
            "console": {"available": bool(console and console.available),
                        "why": None if console and console.available else (console.reason if console else "not configured / sim")},
        }
        summary = {
            "scenario": scn["id"], "title": scn["title"], "build": self.build, "status": "aborted" if aborted else "ran",
            "aborted": aborted, "simulated": self.simulated, "loops_planned": scn["loops"], "loops_done": loops_done,
            "duration_s": round(time.time() - t0, 1),
            "steps": {"total": len(rec["steps"]), "failed": rec["failed_steps"],
                      "skipped_placeholder": sorted(rec["skipped_placeholder"])},
            "signals": signals,
        }
        if self.simulated:
            summary["_label"] = SIM_LABEL
        summary["absolute"] = gates.evaluate(None, summary, scn, self.thr)
        write_json(os.path.join(sdir, "summary.json"), summary)
        return summary

    def _write(self, sdir, name, text):
        with open(os.path.join(sdir, name), "w") as f:
            if self.simulated:
                f.write(f"# {SIM_LABEL}\n")
            f.write(text + ("\n" if text and not text.endswith("\n") else ""))


def _pc(p):
    return {"station_dump": p.get("station_dump"), "driver": p.get("driver")}


def make_boards(boards_cfg, backend, build, sim_opts=None):
    if backend == "sim":
        from .sim import SimWorld
        world = SimWorld(boards_cfg, build, **(sim_opts or {}))
        return Board("dut", boards_cfg["dut"], world.transport("dut")), \
            Board("peer", boards_cfg["peer"], world.transport("peer")), world
    return Board("dut", boards_cfg["dut"], make_transport(boards_cfg["dut"])), \
        Board("peer", boards_cfg["peer"], make_transport(boards_cfg["peer"])), None


def run_build(boards_cfg, catalog, scenario_ids, build, backend, out_dir, thr, allow_placeholders=False,
              allow_destructive=False, sim_opts=None, log=print):
    simulated = backend == "sim"
    os.makedirs(out_dir, exist_ok=True)
    dut, peer, world = make_boards(boards_cfg, backend, build, sim_opts)
    for b in (dut, peer):
        if not b.t.reachable():
            raise SystemExit(f"{b.name} ({b.role}) unreachable over {b.t.name}")
    identity = C.build_identity(dut.t, dut.cfg.get("identity_cmds", {}))
    expect = (boards_cfg.get("builds", {}).get(build) or {}).get("expect_image_contains")
    if expect and expect not in (identity.get("image") or ""):
        raise SystemExit(f"DUT image does not look like build '{build}' (expected '{expect}' in {identity.get('image')!r})")
    meta = {"build": build, "backend": backend, "simulated": simulated,
            "started": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "dut": {k: dut.cfg.get(k) for k in ("name", "driver", "chip", "platform", "qsdk", "capabilities")},
            "peer": {k: peer.cfg.get(k) for k in ("name", "driver", "capabilities")},
            "identity": identity, "scenarios": scenario_ids}
    if simulated:
        meta["_label"] = SIM_LABEL
        meta["sim"] = world.describe()
    write_json(os.path.join(out_dir, "meta.json"), meta)
    runner = ScenarioRunner(dut, peer, thr, out_dir, build, simulated, boards_cfg.get("command_overrides"),
                            allow_placeholders, allow_destructive, log)
    results = {}
    for sid in scenario_ids:
        log(f"[{build}] {sid} ...")
        s = runner.run(catalog[sid])
        results[sid] = {"status": s["status"], "why": s.get("why"),
                        "verdict": s.get("absolute", {}).get("verdict", "NA"),
                        "loops_done": s.get("loops_done")}
        log(f"[{build}] {sid}: {results[sid]['status']} {results[sid]['verdict']} {s.get('why') or ''}")
    write_json(os.path.join(out_dir, "run_summary.json"),
               {"_label": SIM_LABEL if simulated else "hardware run", "build": build, "results": results})
    for b in (dut, peer):
        b.t.close()
    return results
