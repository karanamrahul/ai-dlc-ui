"""Signal collectors. Each one tolerates missing paths and returns {"available": False, "why": ...}.

Pure parsers (parse_*, scan_signatures, linreg) are separated from board I/O so they
are unit-testable and shared by the real and sim backends.
"""
import json
import re

from .config import MAC_RE

NA = lambda why: {"available": False, "why": why}  # noqa: E731

# ---------------------------------------------------------------- dmesg + signatures
TS_RE = re.compile(r"^(?:<\d+>)?\[\s*(\d+\.\d+)\]")


def dmesg_lines(t):
    r = t.run("dmesg", timeout=30)
    return r.out.splitlines() if r.ok else None


def _ts(line):
    m = TS_RE.match(line)
    return float(m.group(1)) if m else None


def dmesg_new(pre, post):
    """Lines in `post` that were not in `pre`. Uses printk timestamps; falls back to
    suffix alignment when timestamps are off. A timestamp reset means the board rebooted:
    the whole new buffer is new."""
    if pre is None or post is None:
        return None, False
    pre_ts = [x for x in map(_ts, pre) if x is not None]
    post_ts = [x for x in map(_ts, post) if x is not None]
    if pre_ts and post_ts:
        last = max(pre_ts)
        if post_ts and max(post_ts) < last:  # uptime went backwards -> reboot
            return list(post), True
        return [ln for ln in post if (_ts(ln) or 0) > last], False
    seen = set(pre)
    return [ln for ln in post if ln not in seen], False


def scan_signatures(lines, sigcfg, event_window_s=10.0):
    """Count signature hits by category (first matching category wins per line).

    Crash-like categories (fatal, fw_crash) are also clustered into *events*: hits
    within event_window_s of each other are one crash (a FW assert prints several lines)."""
    compiled = [(cat, re.compile(p, re.I)) for cat, pats in sigcfg.items() for p in pats]
    hits = {cat: 0 for cat in sigcfg}
    samples = {cat: [] for cat in sigcfg}
    times = {cat: [] for cat in sigcfg}
    for i, ln in enumerate(lines or []):
        for cat, rx in compiled:
            if rx.search(ln):
                hits[cat] += 1
                if len(samples[cat]) < 8:
                    samples[cat].append(ln.strip()[:240])
                ts = _ts(ln)
                times[cat].append(ts if ts is not None else float(i) * 1e6)  # no ts: each line its own event
                break
    events = {}
    for cat, ts in times.items():
        n, last = 0, None
        for x in sorted(ts):
            if last is None or x - last > event_window_s:
                n += 1
            last = x
        events[cat] = n
    return {"hits": hits, "events": events, "samples": samples}


# ---------------------------------------------------------------- /proc parsers
def parse_slabinfo(text, caches):
    out = {}
    for ln in (text or "").splitlines():
        parts = ln.split()
        if len(parts) > 2 and parts[0] in caches:
            try:
                out[parts[0]] = int(parts[1])  # active_objs
            except ValueError:
                pass
    return out


def parse_meminfo(text, keys=("MemFree", "MemAvailable", "Slab", "SUnreclaim")):
    out = {}
    for ln in (text or "").splitlines():
        k, _, rest = ln.partition(":")
        if k in keys:
            out[k + "_kB"] = int(rest.split()[0])
    return out


def parse_cpu(text):
    for ln in (text or "").splitlines():
        if ln.startswith("cpu "):
            v = [int(x) for x in ln.split()[1:]]
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            return idle, sum(v)
    return None


def parse_kv(text):
    """'key: 12' / 'key = 12' / 'key 12' lines -> {key: int}. Single bare number -> {'value': n}."""
    out = {}
    text = (text or "").strip()
    if re.fullmatch(r"-?\d+", text):
        return {"value": int(text)}
    for ln in text.splitlines():
        m = re.match(r"^\s*([A-Za-z_][\w .\-/\[\]]*?)\s*[:=]?\s+(-?\d+)\s*$", ln) or \
            re.match(r"^\s*([A-Za-z_][\w .\-/\[\]]*?)\s*[:=]\s*(-?\d+)", ln)
        if m:
            out[m.group(1).strip()] = int(m.group(2))
    return out


def parse_iperf3(text):
    """iperf3 -J output -> {mbps, lost_percent, protocol}. None on unparsable."""
    try:
        j = json.loads(text)
    except (TypeError, ValueError):
        return None
    end = j.get("end", {})
    if "sum_received" in end:  # TCP
        return {"mbps": round(end["sum_received"]["bits_per_second"] / 1e6, 2), "lost_percent": None,
                "protocol": "tcp"}
    if "sum" in end:  # UDP
        s = end["sum"]
        return {"mbps": round(s["bits_per_second"] / 1e6, 2), "lost_percent": s.get("lost_percent"),
                "protocol": "udp"}
    return None


def parse_kmemleak(text, wlan_regex):
    blocks = re.split(r"(?=^unreferenced object)", text or "", flags=re.M)
    blocks = [b for b in blocks if b.startswith("unreferenced object")]
    rx = re.compile(wlan_regex, re.I)
    wlan = [b for b in blocks if rx.search(b)]
    return {"total": len(blocks), "wlan_related": len(wlan),
            "wlan_samples": [b.strip()[:400] for b in wlan[:3]]}


def linreg(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 if ss_tot == 0 else max(0.0, 1 - ss_res / ss_tot)
    return {"slope": round(slope, 3), "r2": round(r2, 3), "n": n}


# ---------------------------------------------------------------- board collectors
def slab(t, caches):
    txt = t.read_file("/proc/slabinfo")
    if not txt:
        return NA("/proc/slabinfo unreadable (needs root / CONFIG_SLUB_DEBUG)")
    vals = parse_slabinfo(txt, caches)
    if not vals:
        return NA("none of the configured caches present (SLUB cache merging? boot with slub_nomerge)")
    return {"available": True, "objs": vals}


def meminfo(t):
    txt = t.read_file("/proc/meminfo")
    return {"available": True, **parse_meminfo(txt)} if txt else NA("/proc/meminfo unreadable")


def cpu_sample(t):
    return parse_cpu(t.read_file("/proc/stat"))


def cpu_util(a, b):
    if not a or not b or b[1] == a[1]:
        return NA("/proc/stat unavailable")
    busy = 1 - (b[0] - a[0]) / (b[1] - a[1])
    return {"available": True, "util_pct": round(100 * busy, 1)}


def peers(dut):
    """Station-dump count (mac80211/cfg80211 view) vs driver peer list (debugfs view).

    A disagreement between the two is itself a finding: a peer the stack has deleted
    but the driver/FW still holds."""
    ifaces = dut.cfg.get("vars", {}).get("peer_count_ifaces") or [dut.cfg.get("vars", {}).get("ap_if", "wlan0")]
    sta, sta_ok = 0, False
    for ifc in ifaces:
        r = dut.t.run(f"iw dev {ifc} station dump", timeout=20)
        if r.ok:
            sta_ok = True
            sta += sum(1 for ln in r.out.splitlines() if ln.startswith("Station "))
    drv, drv_files = None, []
    for g in dut.paths.get("peer_debugfs_globs", []):
        for path in dut.t.glob(g):
            txt = dut.t.read_file(path)
            if txt is not None:
                drv_files.append(path)
                drv = (drv or 0) + sum(1 for ln in txt.splitlines() if MAC_RE.search(ln))
    return {"available": sta_ok or drv is not None,
            "station_dump": sta if sta_ok else None,
            "driver": drv, "driver_sources": drv_files,
            "why": None if drv is not None else "no driver peer list found (set paths.peer_debugfs_globs)"}


def recycler(t, cfg):
    d = cfg.get("dir")
    names = t.listdir(d) if d else None
    if not names:
        return NA(f"{d} not present (not a QSDK kernel, or recycler disabled)")
    vals = {}
    for n in names:
        kv = parse_kv(t.read_file(f"{d}/{n}") or "")
        for k, v in kv.items():
            vals[f"{n}.{k}"] = v
    rx = re.compile(cfg.get("pool_key_regex", "pool"), re.I)
    pool = {k: v for k, v in vals.items() if rx.search(k)}
    return {"available": True, "pool_total": sum(pool.values()) if pool else None,
            "pool_keys": sorted(pool), "raw": vals}


def nss(t, cfg):
    d = cfg.get("dir")
    names = t.listdir(d) if d else None
    if not names:
        return NA(f"{d} not present (NSS driver not loaded / debugfs not mounted)")
    want = cfg.get("files") or names
    vals = {}
    for n in want:
        if n in names:
            for k, v in parse_kv(t.read_file(f"{d}/{n}") or "").items():
                vals[f"{n}.{k}"] = v
    return {"available": True, "stats": vals}


def kmemleak(t, path, wlan_regex, scan=True):
    if not t.exists(path):
        return NA(f"{path} missing (CONFIG_DEBUG_KMEMLEAK off or debugfs not mounted)")
    if scan:
        t.run(f"echo scan > {path}", timeout=120)
    return {"available": True, **parse_kmemleak(t.read_file(path, timeout=120) or "", wlan_regex)}


def kmemleak_clear(t, path):
    if t.exists(path):
        t.run(f"echo clear > {path}", timeout=60)


def files_matching(t, globs):
    out = []
    for g in globs:
        out += t.glob(g)
    return sorted(set(out))


def pstore_text(t, globs):
    txt = []
    for f in files_matching(t, globs):
        c = t.read_file(f)
        if c:
            txt.append(c)
    return "\n".join(txt)


def build_identity(t, cmds):
    ident = {}
    for k, c in cmds.items():
        r = t.run(c, timeout=20)
        ident[k] = r.out.strip()[:300] if r.ok else None
    return ident
