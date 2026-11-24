"""Talk to Alder boards over SSH, or fall back to fixture mode."""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


@dataclass
class BoardResult:
    board_id: str
    ok: bool
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


class BoardDriver:
    """SSH driver. When FIXTURE_MODE=1 (default), never touches a real board."""

    def __init__(self, board_id: str, host: str | None = None, user: str = "root"):
        self.board_id = board_id
        self.host = host or os.environ.get(
            "DUT_HOST" if board_id == "dut_ap" else "PEER_HOST", ""
        )
        self.user = user
        self.fixture = os.environ.get("FIXTURE_MODE", "1") == "1" or not self.host

    def run(self, cmd: str, timeout: int = 120) -> BoardResult:
        t0 = time.time()
        if self.fixture:
            return self._fixture_run(cmd, time.time() - t0)
        ssh = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "ConnectTimeout=8",
            f"{self.user}@{self.host}",
            cmd,
        ]
        try:
            p = subprocess.run(ssh, capture_output=True, text=True, timeout=timeout)
            return BoardResult(
                self.board_id,
                p.returncode == 0,
                p.stdout,
                p.stderr,
                time.time() - t0,
            )
        except Exception as e:  # noqa: BLE001
            return BoardResult(self.board_id, False, "", str(e), time.time() - t0)

    def _fixture_run(self, cmd: str, dur: float) -> BoardResult:
        # Deterministic stub — demo proves the pipeline without boards.
        return BoardResult(
            self.board_id,
            True,
            f"[fixture] ran: {cmd}\n",
            "",
            max(dur, 0.05),
            meta={"mode": "fixture"},
        )

    def collect_logs(self, out_dir: Path) -> dict[str, Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}
        if self.fixture:
            src = FIXTURES / "logs" / self.board_id
            if src.exists():
                for f in src.glob("*"):
                    dest = out_dir / f.name
                    dest.write_text(f.read_text())
                    paths[f.stem] = dest
            else:
                dmesg = out_dir / "dmesg.txt"
                dmesg.write_text(
                    f"[fixture] dmesg for {self.board_id}\n"
                    "ath12k: wmi ready\nnss_wifi: vdev create ok\n"
                )
                paths["dmesg"] = dmesg
            return paths

        for name, cmd in {
            "dmesg": "dmesg -T | tail -n 2000",
            "nss_stats": "cat /sys/kernel/debug/qca-nss-drv/stats 2>/dev/null || true",
            "kmemleak": "cat /sys/kernel/debug/kmemleak 2>/dev/null || true",
        }.items():
            r = self.run(cmd)
            p = out_dir / f"{name}.txt"
            p.write_text(r.stdout or r.stderr)
            paths[name] = p
        return paths


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())
