"""SIMULATED backend.

A small state model of a DUT (AP) + peer (STA) pair that answers the same commands and
file reads the real collectors issue, so the whole pipeline (steps -> collectors -> gates
-> compare -> report) runs end to end without hardware.

Nothing here is measured. Every file and line it produces carries the word SIMULATED.
Faults are seeded on purpose for the candidate build so the gates can be seen firing.
"""
import copy
import fnmatch
import json
import random
import zlib

from .config import SIM_LABEL
from .transport import Result, Transport

# Seeded faults, applied to the candidate build only (override with --sim-faults).
DEFAULT_FAULTS = {
    "peer_churn": {"peer_ref_leak_every": 10, "_what": "driver peer refcount leak: 1 of every 10 teardowns leaves the peer in the driver table (silent, no dmesg)"},
    "skb_recycler_stress": {"recycler_leak_per_loop": 150, "_what": "150 skbs per traffic burst never returned to the recycler pool"},
    "mlo_link_churn": {"fw_assert_at_loop": 7, "_what": "FW assert (preceded by WMI timeout) on MLO link removal in loop 7"},
    "nss_offload_traffic": {"throughput_factor": 0.93, "nss_drop_extra_per_step": 800, "cpu_extra_pct": 2,
                            "_what": "7% throughput dip with extra NSS tx drops"},
}

BASE_MBPS = {  # plausible, made-up numbers for a 2x2 EHT 5 GHz link; only relative deltas matter
    "default": {"tcp_dl": 1720, "tcp_ul": 1580, "udp": 1480},
    "mlo_link_churn": {"tput_1link": 1650, "tput_2link": 2850},
    "skb_recycler_stress": {"burst": 1400},
    "rekey_gtk": {"udp_result": 200},
    "roam": {"tput": 1650},
}
CPU_UTIL = {"nss_offload_traffic": 0.21, "skb_recycler_stress": 0.35}
SLAB_BASE = {"skbuff_head_cache": 1536, "skbuff_fclone_cache": 256, "kmalloc-256": 9216, "kmalloc-512": 3584,
             "kmalloc-1k": 2304, "kmalloc-2k": 1024, "kmalloc-4k": 384}
NCPU, POOL_PER_CPU = 4, 2048


class SimWorld:
    def __init__(self, boards_cfg, build, seed=1, faults=None):
        self.cfg, self.build, self.seed = boards_cfg, build, seed
        self.rng = random.Random(zlib.crc32(f"{seed}:{build}".encode()))
        if faults is None:
            faults = DEFAULT_FAULTS if build == "candidate" else {}
        self.faults = copy.deepcopy(faults)
        d = boards_cfg["dut"]
        self.v = {**boards_cfg["peer"].get("vars", {}), **d.get("vars", {})}
        self.drv = d["driver"]
        self.dev = {"ath10k": "ath10k_pci 0000:01:00.0", "ath11k": "ath11k_pci 0000:01:00.0",
                    "ath12k": "ath12k_pci 0000:01:00.0"}[self.drv]
        self.t = 1834.0 + self.rng.random()
        self.dmesg = []
        self.stations = set()          # mac80211 view (station dump)
        self.drv_peers = []            # driver view: list of (mac, link_id)
        self.leaked = 0
        self.disconnects = 0
        self.slab_extra = {k: 0 for k in SLAB_BASE}
        self.pool = [POOL_PER_CPU] * NCPU
        self.nss = {"n2h.rx_pkts": 0, "n2h.tx_pkts": 0, "wifili.tx_drops": 0, "wifili.rx_exceptions": 0, "drv.tx_fail": 0}
        self.cpu_idle = self.cpu_total = 0.0
        self.coredumps, self.pstore, self.tmp_files = [], {}, []
        self.scenario, self.loop = None, None
        self.crashed_this_loop = False
        self._log(f"{self.dev}: fw_version 0x1103ffff fw_build_id WLAN.SIM.1.0 (SIMULATED)")

    # ------------------------------------------------------------------ helpers
    def describe(self):
        return {"label": SIM_LABEL, "seed": self.seed, "build": self.build,
                "faults_active": {k: v.get("_what", v) for k, v in self.faults.items()}}

    def transport(self, role):
        return SimTransport(self, role)

    def fault(self, key):
        return self.faults.get(self.scenario, {}).get(key)

    def _log(self, msg, dt=0.01):
        self.t += dt
        self.dmesg.append(f"[{self.t:12.6f}] {msg}")

    def advance(self, secs):
        if secs <= 0:
            return
        util = CPU_UTIL.get(self.scenario, 0.42) + (self.fault("cpu_extra_pct") or 0) / 100.0
        tot = secs * 100 * NCPU
        self.cpu_total += tot
        self.cpu_idle += tot * (1 - util)
        self.t += secs

    def _noise(self, sd):
        return self.rng.gauss(0, sd)

    def _fw_crash(self, reason, timeout_first=False):
        if timeout_first:
            self._log(f"{self.dev}: wmi command 24581 timeout (SIMULATED)")
            self._log(f"{self.dev}: failed to send WMI_MLO_LINK_SET_ACTIVE_CMDID: -110 (SIMULATED)", dt=0.2)
            self.t += 3.0
        self._log(f"{self.dev}: firmware crashed: MHI_CB_EE_RDDM (SIMULATED)")
        self._log(f"{self.dev}: FW_ASSERT: {reason} (SIMULATED)", dt=0.05)
        n = len(self.coredumps)
        self.coredumps.append(f"/sys/class/devcoredump/devcd{n + 1}")
        self._log(f"{self.dev}: coredump collected (SIMULATED devcd{n + 1})", dt=1.5)
        # recovery re-creates peers for associated stations; leaked entries are dropped with the old FW state
        self.drv_peers = [p for p in self.drv_peers if p[0] in self.stations]
        self._log(f"{self.dev}: SSR recovery complete, vdevs and peers restored (SIMULATED)", dt=6.0)

    # ------------------------------------------------------------------ actions (scenario steps)
    def act(self, role, action, cmd):
        sta = self.v.get("sta_mac", "00:03:7f:00:10:01")
        mlo = self.scenario == "mlo_link_churn"
        if action == "assoc":
            if sta not in self.stations:
                self.stations.add(sta)
                self.drv_peers += [(sta, 0), (sta, 1)] if mlo else [(sta, 0)]
            self.advance(1.2)
            return Result(0, "OK\n")
        if action in ("disassoc", "deauth"):
            if sta in self.stations:
                self.stations.discard(sta)
                self.disconnects += 1
                every = self.fault("peer_ref_leak_every")
                self.drv_peers = [p for p in self.drv_peers if p[0] != sta]
                if every and self.disconnects % every == 0:
                    self.drv_peers.append((f"02:00:00:00:{self.leaked // 256:02x}:{self.leaked % 256:02x}", 0))
                    self.leaked += 1
                    self.slab_extra["kmalloc-1k"] += 1
                    self.slab_extra["kmalloc-256"] += 3
            self.advance(0.5)
            return Result(0, "OK\n")
        if action == "mlo_link_del":
            at = self.fault("fw_assert_at_loop")
            if at is not None and self.loop == at:
                self._fw_crash("mlo_mgr_link_del: ml peer refcnt underflow [seeded fault]", timeout_first=True)
                self.crashed_this_loop = True
                return Result(255, "", "FAIL\n")
            for i, p in enumerate(self.drv_peers):
                if p[1] == 1:
                    self.drv_peers.pop(i)
                    break
            self.advance(0.3)
            return Result(0, "OK\n")
        if action == "mlo_link_add":
            for mac in self.stations:
                if (mac, 1) not in self.drv_peers:
                    self.drv_peers.append((mac, 1))
            self.crashed_this_loop = False
            self.advance(0.3)
            return Result(0, "OK\n")
        if action in ("traffic", "traffic_collect"):
            return self._traffic(cmd)
        if action == "traffic_bg":
            self.advance(0.2)
            return Result(0, "")
        if action == "fw_crash_inject":
            self._fw_crash("assert injected via debugfs simulate_fw_crash")
            return Result(0, "")
        if action == "apss_crash":
            panic = "\n".join([f"<0>[{self.t:12.6f}] sysrq: Trigger a crash (SIMULATED)",
                               f"<0>[{self.t:12.6f}] Kernel panic - not syncing: sysrq triggered crash (SIMULATED)"])
            self.pstore["/sys/fs/pstore/dmesg-ramoops-0"] = f"# {SIM_LABEL}\n" + panic
            self.tmp_files.append("/tmp/apss_ramdump_SIMULATED.bin")
            self.t, self.dmesg = 0.0, []
            self.stations.clear()
            self.drv_peers, self.pool = [], [POOL_PER_CPU] * NCPU
            self._log("Booting Linux on physical CPU 0x0 (SIMULATED)", dt=0.0)
            self._log(f"{self.dev}: fw_version 0x1103ffff (SIMULATED)", dt=14.0)
            self.advance(60)
            return Result(255, "", "Connection closed\n")
        if action == "vdev_down":
            self.stations.clear()
            self.drv_peers = [p for p in self.drv_peers if p[0].startswith("02:")]  # leaked ones stay
            self.advance(1)
            return Result(0, "OK\n")
        if action in ("wifi_reload", "module_reload"):
            self.stations.clear()
            self.drv_peers, self.leaked = [], 0
            self._log(f"{self.dev}: fw_version 0x1103ffff (SIMULATED reload)", dt=4.0)
            self.advance(8)
            return Result(0, "")
        if action == "radar":
            self._log(f"{self.dev}: radar detected on channel {self.v.get('dfs_channel', '52')} (SIMULATED)")
            self.advance(2)
            return Result(0, "")
        if action == "chan_switch":
            self.advance(1)
            return Result(0, "OK\n")
        if action == "check" and "COMPLETED" in cmd and not self.stations and "reconnect" not in cmd:
            # recovery/reassoc wait: the STA comes back on its own
            self.stations.add(sta)
            self.drv_peers.append((sta, 0))
        self.advance(0.3)
        return Result(0, "OK\n")

    def _traffic(self, cmd):
        step_secs = 5
        for tok, nxt in zip(cmd.split(), cmd.split()[1:]):
            if tok == "-t" and nxt.isdigit():
                step_secs = int(nxt)
        if self.crashed_this_loop and self.scenario == "mlo_link_churn":
            self.crashed_this_loop = False
            self.advance(1)
            return Result(1, "", "iperf3: error - unable to connect to server (SIMULATED, FW recovering)\n")
        sid = self.scenario
        base = BASE_MBPS.get(sid, {})
        step = getattr(self, "_cur_step", "tcp_dl")
        udp = " -u" in cmd or "rekey" in cmd
        if sid == "mlo_link_churn":
            key = "tput_2link" if any(p[1] == 1 for p in self.drv_peers) else "tput_1link"
        else:
            key = step if step in base else ("udp" if udp else "tcp_ul" if " -R" not in cmd else "tcp_dl")
        mbps = base.get(key) or BASE_MBPS["default"].get(key, 1600)
        mbps *= (self.fault("throughput_factor") or 1.0) * (1 + self._noise(0.012))
        leak = self.fault("recycler_leak_per_loop")
        if sid == "skb_recycler_stress" and leak:
            for i in range(NCPU):
                self.pool[i] = max(0, self.pool[i] - leak // NCPU)
            self.slab_extra["skbuff_head_cache"] += leak
        if sid == "nss_offload_traffic":
            self.nss["n2h.rx_pkts"] += int(mbps * step_secs * 85)
            self.nss["wifili.tx_drops"] += 60 + int(self._noise(6)) + (self.fault("nss_drop_extra_per_step") or 0)
            self.nss["wifili.rx_exceptions"] += 3
        self.advance(step_secs + 0.5)
        bps = mbps * 1e6
        out = {"title": "SIMULATED", "start": {"test_start": {"protocol": "UDP" if udp else "TCP", "duration": step_secs}},
               "intervals": []}
        if udp:
            loss = max(0.0, (0.3 if sid == "nss_offload_traffic" else 0.05) + self._noise(0.02))
            out["end"] = {"sum": {"bits_per_second": bps, "lost_percent": round(loss, 3), "jitter_ms": 0.04}}
        else:
            out["end"] = {"sum_sent": {"bits_per_second": bps * 1.001}, "sum_received": {"bits_per_second": bps}}
        return Result(0, json.dumps(out, indent=1))

    # ------------------------------------------------------------------ virtual filesystem
    def vfs(self):
        dbg = self.v.get("debugfs_dev", "/sys/kernel/debug/ath12k/pci-0000:01:00.0")
        f = {
            "/proc/slabinfo": self._slabinfo,
            "/proc/meminfo": self._meminfo,
            "/proc/stat": lambda: f"cpu  {int(self.cpu_total - self.cpu_idle)} 0 0 {int(self.cpu_idle)} 0 0 0 0 0 0\n",
            "/sys/kernel/debug/kmemleak": lambda: "",
            f"{dbg}/mac0/peers": lambda: f"# {SIM_LABEL}\n" + "".join(
                f"peer {m} vdev_id 0 link_id {lnk}\n" for m, lnk in self.drv_peers),
            "/proc/net/skb_recycler/pool_stats": lambda: "".join(f"cpu{i}_pool_count: {n + int(self._noise(3))}\n"
                                                                 for i, n in enumerate(self.pool)),
            "/etc/openwrt_version": lambda: f"QSDK 12.5 SIMULATED image ({self.build})\n",
        }
        for name in ("n2h", "wifili", "drv"):
            f[f"/sys/kernel/debug/qca-nss-drv/stats/{name}"] = (
                lambda n=name: "".join(f"{k.split('.', 1)[1]} = {v}\n" for k, v in self.nss.items() if k.startswith(n + ".")))
        for p in self.coredumps:
            f[p + "/data"] = lambda: "SIMULATED devcoredump\n"
            f[p] = lambda: None
        for p, txt in self.pstore.items():
            f[p] = (lambda x=txt: x)
        for p in self.tmp_files:
            f[p] = lambda: "SIMULATED\n"
        return f

    def _slabinfo(self):
        rows = ["slabinfo - version: 2.1 (SIMULATED)",
                "# name            <active_objs> <num_objs> <objsize> <objperslab> <pagesperslab> : tunables..."]
        for k, base in SLAB_BASE.items():
            v = int(base + self.slab_extra[k] + self._noise(8))
            rows.append(f"{k:<20} {v:>7} {v + 64:>7}    256   32    2 : tunables    0    0    0 : slabdata   0   0   0")
        return "\n".join(rows) + "\n"

    def _meminfo(self):
        extra_kb = (self.slab_extra["skbuff_head_cache"] * 256 + self.slab_extra["kmalloc-1k"] * 1024) // 1024
        return (f"MemTotal:        1015808 kB\nMemFree:          {412000 - extra_kb + int(self._noise(200))} kB\n"
                f"MemAvailable:     {598000 - extra_kb} kB\nSlab:             {61000 + extra_kb} kB\n"
                f"SUnreclaim:       {38000 + extra_kb} kB\n")


class SimTransport(Transport):
    name = "sim"

    def __init__(self, world, role):
        self.w, self.role = world, role

    def mark(self, **kw):
        if "scenario" in kw:
            self.w.scenario, self.w.loop = kw["scenario"], None
            self.w.crashed_this_loop = False
        if "loop" in kw:
            self.w.loop = kw["loop"]

    def sleep(self, seconds):
        self.w.advance(seconds)

    def wait_reachable(self, timeout_s, poll_s=5):
        return True

    def run(self, cmd, timeout=60, action=None):
        w = self.w
        w.advance(0.05)
        if action and action != "setup":
            w._cur_step = _step_hint(cmd)
            return w.act(self.role, action, cmd)
        if cmd.strip() == "dmesg":
            return Result(0, "\n".join(w.dmesg) + "\n")
        if cmd.startswith("iw dev ") and "station dump" in cmd:
            ifc = cmd.split()[2]
            first = (w.v.get("peer_count_ifaces") or [w.v.get("ap_if")])[0]
            if ifc != first:
                return Result(0, "")
            return Result(0, "".join(f"Station {m} (on {ifc})\n\tinactive time:\t20 ms\n\tsignal:\t-38 dBm\n"
                                     for m in sorted(w.stations)))
        if cmd.startswith("uname"):
            return Result(0, "5.4.213-SIMULATED\n")
        if "fw_version" in cmd:
            return Result(0, f"{w.dev}: fw_version 0x1103ffff (SIMULATED)\n")
        if "openwrt_version" in cmd:
            return Result(0, w.vfs()["/etc/openwrt_version"]())
        return Result(0, "")

    def read_file(self, path, timeout=30):
        fn = self.w.vfs().get(path)
        return fn() if fn else None

    def exists(self, path):
        return any(p == path or p.startswith(path.rstrip("/") + "/") for p in self.w.vfs())

    def listdir(self, path):
        pre = path.rstrip("/") + "/"
        names = sorted({p[len(pre):].split("/")[0] for p in self.w.vfs() if p.startswith(pre)})
        return names or None

    def glob(self, pattern):
        depth = pattern.count("/")
        return sorted(p for p in self.w.vfs() if p.count("/") == depth and fnmatch.fnmatch(p, pattern))


def _step_hint(cmd):
    """Map an iperf command to the BASE_MBPS key the scenario uses (sim only)."""
    if "tb_rekey" in cmd:
        return "udp_result"
    if " -u" in cmd and "-l 1400" in cmd:
        return "burst"
    if " -t 5 -R" in cmd:
        return "tput"
    return "tcp_dl" if " -R" in cmd else "tcp_ul"
