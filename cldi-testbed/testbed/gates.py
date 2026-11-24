"""Gates. Each gate takes (baseline_summary | None, candidate_summary, scenario, thresholds)
and returns {gate, verdict, baseline, candidate, detail}. With no baseline, relative
gates degrade to absolute checks (or NA). Verdicts: NA < PASS < WARN < FAIL."""
import re

RANK = {"NA": 0, "PASS": 1, "WARN": 2, "FAIL": 3}


def worst(verdicts):
    vs = list(verdicts)
    return max(vs, key=lambda v: RANK[v]) if vs else "NA"


def _g(gate, verdict, base, cand, detail):
    return {"gate": gate, "verdict": verdict, "baseline": base, "candidate": cand, "detail": detail}


def _sig(summary, key="events"):
    return (summary or {}).get("signals", {}).get("dmesg", {}).get(key, {})


def _loops(s):
    return (s or {}).get("loops_done", 0)


def gate_crash(base, cand, scn, thr):
    exp = scn.get("expected", {})
    n = _loops(cand)
    exp_fw = exp.get("fw_crash_events_per_loop", 0) * n
    exp_fatal = exp.get("fatal_events_per_loop", 0) * n
    ev = _sig(cand)
    fatal, fw = ev.get("fatal", 0), ev.get("fw_crash", 0)
    rd = cand["signals"].get("ramdump", {})
    unexpected_fatal, unexpected_fw = max(0, fatal - exp_fatal), max(0, fw - exp_fw)
    b = _sig(base)
    base_val = {"fatal": b.get("fatal"), "fw_crash": b.get("fw_crash")} if base else None
    cval = {"fatal": fatal, "fw_crash": fw, "expected_fatal": exp_fatal, "expected_fw_crash": exp_fw,
            "new_ramdumps": len(rd.get("new", [])) if rd.get("available") else None}
    if unexpected_fatal or unexpected_fw:
        samples = cand["signals"]["dmesg"].get("samples", {})
        first = (samples.get("fatal") or samples.get("fw_crash") or [""])[0]
        return _g("crash", "FAIL", base_val, cval,
                  f"{unexpected_fatal} unexpected kernel-fatal + {unexpected_fw} unexpected FW-crash event(s); first: {first}")
    if not cand["signals"]["dmesg"].get("available"):
        return _g("crash", "NA", base_val, cval, "dmesg not available")
    return _g("crash", "PASS", base_val, cval, "no unexpected crash/assert signatures")


def gate_timeouts(base, cand, scn, thr):
    c = (cand["signals"]["dmesg"].get("hits") or {}).get("timeout", 0)
    if not cand["signals"]["dmesg"].get("available"):
        return _g("timeouts", "NA", None, None, "dmesg not available")
    if base is None:
        return _g("timeouts", "WARN" if c else "PASS", None, c, f"{c} WMI/HTT timeout line(s) (no baseline to diff)")
    b = (base["signals"]["dmesg"].get("hits") or {}).get("timeout", 0)
    first = (cand["signals"]["dmesg"].get("samples", {}).get("timeout") or [""])[0]
    if c > b and thr["gates"]["timeouts"].get("fail_on_new", True):
        return _g("timeouts", "FAIL", b, c, f"{c - b} new WMI/HTT timeout line(s) vs baseline; first: {first}")
    if c and c == b:
        return _g("timeouts", "WARN", b, c, "timeouts present in baseline too (pre-existing)")
    return _g("timeouts", "PASS", b, c, "no new WMI/HTT timeouts")


def gate_peer_leak(base, cand, scn, thr):
    """Peers left behind = post-cooldown count minus pre-scenario count, in both the stack
    (station dump) and the driver (debugfs peer list). Delta-based so a leak from an earlier
    scenario is not blamed on this one."""
    p = cand["signals"].get("peers", {})
    if not p.get("available"):
        return _g("peer_leak", "NA", None, None, p.get("why") or "peer counts not available")
    exp = scn.get("expected", {}).get("peers_after", thr["gates"]["peer_leak"]["expected_remaining"])
    pre, post = p.get("pre") or {}, p.get("post") or {}
    problems, cval = [], {"loops": _loops(cand)}
    for view, label in (("station_dump", "station dump (mac80211)"), ("driver", "driver peer table")):
        if pre.get(view) is None or post.get(view) is None:
            cval[view] = None
            continue
        left = post[view] - pre[view]
        cval[view] = f"{pre[view]}->{post[view]} ({left:+d})"
        if left > exp:
            problems.append(f"{label}: {left} peer(s) left behind (pre {pre[view]} -> post {post[view]}, expected +{exp})")
    bp = None
    if base:
        bpp = base["signals"].get("peers", {})
        bp = {v: (bpp.get("post") or {}).get(v, 0) - (bpp.get("pre") or {}).get(v, 0)
              for v in ("station_dump", "driver") if (bpp.get("post") or {}).get(v) is not None}
    if problems:
        return _g("peer_leak", "FAIL", bp, cval, f"after {_loops(cand)} cycles + cooldown: " + "; ".join(problems))
    note = "" if cval.get("driver") is not None else " (driver peer list not available; stack view only)"
    return _g("peer_leak", "PASS", bp, cval, f"no peers left behind{note}")


def gate_slab_slope(base, cand, scn, thr):
    cfg = thr["gates"]["slab_slope"]
    cs = cand["signals"].get("slab", {})
    if not cs.get("available"):
        return _g("slab_slope", "NA", None, None, cs.get("why", "slabinfo not available"))
    bs = (base or {}).get("signals", {}).get("slab", {}) if base else {}
    worst_v, findings, cvals, bvals = "PASS", [], {}, {}
    for cache, fit in (cs.get("fit") or {}).items():
        if not fit or fit["n"] < cfg["min_points"]:
            continue
        b_fit = (bs.get("fit") or {}).get(cache) if bs else None
        slope = fit["slope"] - (b_fit["slope"] if b_fit else 0.0)
        cvals[cache] = fit
        if b_fit:
            bvals[cache] = b_fit
        if fit["r2"] < cfg["min_r2"]:
            continue  # noisy, not a trend
        v = "FAIL" if slope > cfg["fail_per_loop"] else "WARN" if slope > cfg["warn_per_loop"] else "PASS"
        if v != "PASS":
            findings.append(f"{cache}: +{slope:.1f} objs/loop (r2={fit['r2']:.2f}"
                            + (f", baseline {b_fit['slope']:+.1f}/loop)" if b_fit else ")"))
            worst_v = worst([worst_v, v])
    if not cvals:
        return _g("slab_slope", "NA", None, None, f"fewer than {cfg['min_points']} per-loop samples")
    return _g("slab_slope", worst_v, bvals or None, cvals,
              "; ".join(findings) if findings else "no monotonic growth above threshold")


def gate_recycler(base, cand, scn, thr):
    cfg = thr["gates"]["recycler"]
    r = cand["signals"].get("recycler", {})
    if not r.get("available") or r.get("pre_total") is None:
        return _g("recycler", "NA", None, None, r.get("why", "recycler pool counters not available"))
    pre, post = r["pre_total"], r["post_total"]
    tol = max(cfg["tolerance_abs"], pre * cfg["tolerance_pct"] / 100.0)
    delta = post - pre
    br = (base or {}).get("signals", {}).get("recycler", {}) if base else {}
    bval = {"pre": br.get("pre_total"), "post": br.get("post_total")} if br.get("available") else None
    cval = {"pre": pre, "post": post, "delta": delta, "tolerance": round(tol)}
    if abs(delta) > tol:
        return _g("recycler", "FAIL", bval, cval, f"pool did not return to baseline after cooldown: {pre} -> {post} ({delta:+d}, tol ±{tol:.0f})")
    return _g("recycler", "PASS", bval, cval, f"pool back to baseline ({delta:+d})")


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


def _by_step(summary):
    out = {}
    for x in (summary or {}).get("signals", {}).get("throughput", {}).get("samples", []):
        out.setdefault(x["step"], []).append(x["mbps"])
    return out


def gate_throughput(base, cand, scn, thr):
    """Per traffic step: median Mbps candidate vs baseline. The worst step decides."""
    cfg = thr["gates"]["throughput"]
    c = _by_step(cand)
    if not c:
        return _g("throughput", "NA", None, None, "no iperf3 samples")
    if base is None:
        return _g("throughput", "NA", None, {k: round(_median(v), 1) for k, v in c.items()}, "no baseline to compare")
    b = _by_step(base)
    rows = []
    for step in sorted(set(b) & set(c)):
        bm, cm = _median(b[step]), _median(c[step])
        drop = 100.0 * (bm - cm) / bm if bm else 0.0
        rows.append((drop, step, bm, cm, len(c[step])))
    if not rows:
        return _g("throughput", "NA", None, None, "no traffic step common to both builds")
    drop, step, bm, cm, n = max(rows)
    v = "FAIL" if drop > cfg["fail_drop_pct"] else "WARN" if drop > cfg["warn_drop_pct"] else "PASS"
    return _g("throughput", v, {r[1]: round(r[2], 1) for r in rows}, {r[1]: round(r[3], 1) for r in rows},
              f"worst step '{step}': median {cm:.1f} vs {bm:.1f} Mbps ({-drop:+.1f}%, n={n}; "
              f"warn >{cfg['warn_drop_pct']:g}% drop, fail >{cfg['fail_drop_pct']:g}%)")


def gate_udp_loss(base, cand, scn, thr):
    cfg = thr["gates"]["udp_loss"]

    def loss(s):
        xs = [x["lost_percent"] for x in (s or {}).get("signals", {}).get("throughput", {}).get("samples", [])
              if x.get("lost_percent") is not None]
        return _median(xs)
    c, b = loss(cand), loss(base) if base else None
    if c is None:
        return _g("udp_loss", "NA", None, None, "no UDP iperf3 samples")
    if b is None:
        return _g("udp_loss", "NA", None, c, "no baseline UDP samples")
    inc = c - b
    v = "FAIL" if inc > cfg["fail_increase_pts"] else "WARN" if inc > cfg["warn_increase_pts"] else "PASS"
    return _g("udp_loss", v, b, c, f"median UDP loss {c:.2f}% vs {b:.2f}% ({inc:+.2f} pts)")


def gate_kmemleak(base, cand, scn, thr):
    k = cand["signals"].get("kmemleak", {})
    if not k.get("available"):
        return _g("kmemleak", "NA", None, None, k.get("why", "kmemleak not available"))
    c = k["wlan_related"]
    b = (base or {}).get("signals", {}).get("kmemleak", {}).get("wlan_related") if base else None
    new = c - (b or 0)
    if new > 0:
        return _g("kmemleak", "FAIL", b, c, f"{new} new wlan-related unreferenced object(s)")
    return _g("kmemleak", "PASS", b, c, "no new wlan-related kmemleak reports")


def gate_cpu(base, cand, scn, thr):
    c = cand["signals"].get("cpu", {}).get("util_pct")
    b = (base or {}).get("signals", {}).get("cpu", {}).get("util_pct") if base else None
    if c is None or b is None:
        return _g("cpu", "NA", b, c, "cpu utilisation not available for both builds")
    inc = c - b
    v = "WARN" if inc > thr["gates"]["cpu"]["warn_increase_pts"] else "PASS"
    return _g("cpu", v, b, c, f"APSS CPU {c:.1f}% vs {b:.1f}% ({inc:+.1f} pts)")


def gate_nss(base, cand, scn, thr):
    cfg = thr["gates"]["nss"]
    cn = cand["signals"].get("nss", {})
    if not cn.get("available"):
        return _g("nss", "NA", None, None, cn.get("why", "NSS stats not available"))
    rx = re.compile(cfg["drop_key_regex"], re.I)
    cd = {k: v for k, v in cn.get("delta", {}).items() if rx.search(k)}
    if base is None or not base["signals"].get("nss", {}).get("available"):
        return _g("nss", "NA", None, cd, "no baseline NSS stats")
    bd = {k: v for k, v in base["signals"]["nss"].get("delta", {}).items() if rx.search(k)}
    flagged = []
    for k, v in cd.items():
        bv = bd.get(k, 0)
        if v - bv >= cfg["min_abs"] and (bv == 0 or 100.0 * (v - bv) / bv > cfg["warn_increase_pct"]):
            flagged.append(f"{k}: {bv} -> {v}")
    return _g("nss", "WARN" if flagged else "PASS", bd, cd,
              "; ".join(flagged) if flagged else "no drop/exception counter grew beyond threshold")


def gate_recovery(base, cand, scn, thr):
    r = cand["signals"].get("recovery", {})
    exp = scn.get("expected", {})
    n = _loops(cand)
    want = (exp.get("fw_crash_events_per_loop", 0) + exp.get("fatal_events_per_loop", 0)) * n
    ev = _sig(cand)
    seen = ev.get("fw_crash", 0) + ev.get("fatal", 0)
    cval = {"injected": n, "crash_events_seen": seen, "checks_passed": r.get("passed"), "checks_failed": r.get("failed")}
    if r.get("failed"):
        return _g("recovery", "FAIL", None, cval, f"{r['failed']} recovery check(s) failed: {', '.join(r.get('failed_ids', [])[:5])}")
    if want and seen < want:
        return _g("recovery", "FAIL", None, cval, f"injected {want} crash(es) but observed {seen}: injection or detection broken")
    if not r.get("passed"):
        return _g("recovery", "NA", None, cval, "no recovery checks ran")
    return _g("recovery", "PASS", None, cval, f"{seen} crash(es) observed, all {r['passed']} recovery checks passed")


GATES = {"crash": gate_crash, "timeouts": gate_timeouts, "peer_leak": gate_peer_leak, "slab_slope": gate_slab_slope,
         "recycler": gate_recycler, "throughput": gate_throughput, "udp_loss": gate_udp_loss, "kmemleak": gate_kmemleak,
         "cpu": gate_cpu, "nss": gate_nss, "recovery": gate_recovery}


def evaluate(base, cand, scn, thr):
    results = [GATES[g](base, cand, scn, thr) for g in scn["gates"]]
    return {"verdict": worst(r["verdict"] for r in results), "gates": results}
