"""Unit tests. Run from the directory that contains testbed/:
    python3 -m unittest discover -s testbed/tests -t .
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from testbed import collectors as C  # noqa: E402
from testbed import gates, report, runner  # noqa: E402
from testbed import scenarios as S  # noqa: E402
from testbed.config import Board, load_boards, load_thresholds  # noqa: E402

THR = load_thresholds()


class Parsers(unittest.TestCase):
    def test_linreg_exact(self):
        fit = C.linreg([0, 1, 2, 3, 4], [10, 20, 30, 40, 50])
        self.assertAlmostEqual(fit["slope"], 10.0)
        self.assertAlmostEqual(fit["r2"], 1.0)

    def test_dmesg_new_and_reboot(self):
        pre = ["[  10.000000] a", "[  11.000000] b"]
        new, rebooted = C.dmesg_new(pre, pre + ["[  12.500000] c"])
        self.assertEqual(new, ["[  12.500000] c"])
        self.assertFalse(rebooted)
        new, rebooted = C.dmesg_new(pre, ["[   0.100000] boot"])
        self.assertTrue(rebooted)

    def test_signature_events_cluster(self):
        lines = ["[ 100.0] ath12k_pci x: firmware crashed: MHI_CB_EE_RDDM",
                 "[ 100.5] ath12k_pci x: FW_ASSERT: foo",
                 "[ 200.0] ath12k_pci x: firmware crashed: MHI_CB_EE_RDDM",
                 "[ 201.0] ath12k_pci x: wmi command 123 timeout",
                 "[ 202.0] <0> Unable to handle kernel NULL pointer dereference"]
        sig = C.scan_signatures(lines, THR["signatures"], 10)
        self.assertEqual(sig["hits"]["fw_crash"], 3)
        self.assertEqual(sig["events"]["fw_crash"], 2)
        self.assertEqual(sig["hits"]["timeout"], 1)
        self.assertEqual(sig["events"]["fatal"], 1)

    def test_iperf3(self):
        tcp = json.dumps({"end": {"sum_received": {"bits_per_second": 9.5e8}}})
        udp = json.dumps({"end": {"sum": {"bits_per_second": 2e8, "lost_percent": 0.4}}})
        self.assertEqual(C.parse_iperf3(tcp)["mbps"], 950.0)
        self.assertEqual(C.parse_iperf3(udp)["lost_percent"], 0.4)
        self.assertIsNone(C.parse_iperf3("not json"))

    def test_slabinfo_and_kv(self):
        txt = "slabinfo - version: 2.1\nskbuff_head_cache   1536   1600 256 32 2 : tunables\n"
        self.assertEqual(C.parse_slabinfo(txt, ["skbuff_head_cache"]), {"skbuff_head_cache": 1536})
        self.assertEqual(C.parse_kv("cpu0_pool_count: 12\ntx_drops = 7\n"), {"cpu0_pool_count": 12, "tx_drops": 7})


def _summary(**signals):
    base = {"dmesg": {"available": True, "hits": {}, "events": {}, "samples": {}}, "ramdump": {"available": True, "new": []}}
    base.update(signals)
    return {"loops_done": 10, "signals": base}


class Gates(unittest.TestCase):
    scn = {"id": "x", "gates": [], "expected": {"peers_after": 0}}

    def test_throughput_thresholds(self):
        def tp(m):
            return _summary(throughput={"samples": [{"step": "tcp_dl", "mbps": m}]})
        self.assertEqual(gates.gate_throughput(tp(1000), tp(960), self.scn, THR)["verdict"], "PASS")
        self.assertEqual(gates.gate_throughput(tp(1000), tp(930), self.scn, THR)["verdict"], "WARN")
        self.assertEqual(gates.gate_throughput(tp(1000), tp(880), self.scn, THR)["verdict"], "FAIL")

    def test_peer_leak_is_delta_based(self):
        ok = _summary(peers={"available": True, "pre": {"station_dump": 0, "driver": 3}, "post": {"station_dump": 0, "driver": 3}})
        bad = _summary(peers={"available": True, "pre": {"station_dump": 0, "driver": 0}, "post": {"station_dump": 0, "driver": 2}})
        self.assertEqual(gates.gate_peer_leak(None, ok, self.scn, THR)["verdict"], "PASS")
        self.assertEqual(gates.gate_peer_leak(None, bad, self.scn, THR)["verdict"], "FAIL")

    def test_expected_crash_is_not_a_failure(self):
        scn = {"id": "fw", "expected": {"fw_crash_events_per_loop": 1}}
        s = _summary()
        s["loops_done"] = 2
        s["signals"]["dmesg"]["events"] = {"fw_crash": 2, "fatal": 0}
        self.assertEqual(gates.gate_crash(None, s, scn, THR)["verdict"], "PASS")
        s["signals"]["dmesg"]["events"]["fw_crash"] = 3
        self.assertEqual(gates.gate_crash(None, s, scn, THR)["verdict"], "FAIL")

    def test_missing_signal_is_na(self):
        s = _summary(recycler={"available": False, "why": "nope"})
        self.assertEqual(gates.gate_recycler(None, s, self.scn, THR)["verdict"], "NA")


class Catalog(unittest.TestCase):
    def test_catalog_valid_against_vocabulary(self):
        cat = S.load_catalog()
        vocab = S.load_impact_map()["vocabulary"]
        for sid, s in cat.items():
            S.validate(s, sid, vocab)
        self.assertEqual(cat["mlo_link_churn"]["applies_to"]["drivers"], ["ath12k"])

    def test_applicability(self):
        cfg = dict(load_boards()["dut"], driver="ath11k", qsdk=False, capabilities=[])
        dut = Board("dut", cfg, None)
        cat = S.load_catalog()
        self.assertFalse(S.applicable(cat["mlo_link_churn"], dut)[0])
        self.assertFalse(S.applicable(cat["skb_recycler_stress"], dut)[0])
        self.assertTrue(S.applicable(cat["peer_churn"], dut)[0])

    def test_select_from_patch(self):
        diff = ("+++ b/drivers/net/wireless/ath/ath12k/peer.c\n"
                "@@ -1,3 +1,4 @@ int ath12k_peer_delete(struct ath12k *ar, u32 vdev_id, u8 *addr)\n")
        sel = S.select(S.impact_from_patch(diff), S.load_catalog(), S.load_impact_map())
        ids = [r["id"] for r in sel["selected"]]
        self.assertIn("peer_churn", ids)
        self.assertIn("slo_assoc_basic", ids)  # smoke always


class SimEndToEnd(unittest.TestCase):
    def test_peer_leak_fires_only_on_candidate(self):
        boards, cat = load_boards(), S.load_catalog()
        with tempfile.TemporaryDirectory() as d:
            for build in ("baseline", "candidate"):
                runner.run_build(boards, cat, ["peer_churn"], build, "sim", os.path.join(d, build), THR,
                                 sim_opts={"seed": 3, "faults": None}, log=lambda *a: None)
            r = report.compare(os.path.join(d, "baseline"), os.path.join(d, "candidate"), os.path.join(d, "cmp"), cat, THR)
            self.assertTrue(r["simulated"])
            fired = {(f["scenario"], f["gate"]): f["verdict"] for f in r["fired"]}
            self.assertEqual(fired.get(("peer_churn", "peer_leak")), "FAIL")
            with open(os.path.join(d, "cmp", "report.md")) as f:
                self.assertIn("SIMULATED", f.read())


if __name__ == "__main__":
    unittest.main()
