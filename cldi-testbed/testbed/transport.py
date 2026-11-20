"""Transports: how the runner reaches a board.

Every transport implements run(cmd) -> Result. read_file/listdir/glob are built on
run() so collectors work the same over SSH, serial, or the sim backend.
"""
import os
import random
import re
import shlex
import subprocess
import threading
import time

try:  # optional dependency
    import serial  # type: ignore
except ImportError:  # pragma: no cover - depends on host
    serial = None


class TransportError(RuntimeError):
    pass


class Result:
    __slots__ = ("rc", "out", "err", "dur")

    def __init__(self, rc, out="", err="", dur=0.0):
        self.rc, self.out, self.err, self.dur = rc, out, err, dur

    @property
    def ok(self):
        return self.rc == 0

    def __repr__(self):
        return f"Result(rc={self.rc}, out={len(self.out)}B, dur={self.dur:.2f}s)"


class Transport:
    name = "base"

    def run(self, cmd, timeout=60, action=None):  # action: metadata, used only by sim
        raise NotImplementedError

    def read_file(self, path, timeout=30):
        r = self.run(f"cat {shlex.quote(path)} 2>/dev/null", timeout)
        return r.out if r.rc == 0 else None

    def listdir(self, path):
        r = self.run(f"ls -1 {shlex.quote(path)} 2>/dev/null")
        return [x for x in r.out.split("\n") if x] if r.rc == 0 else None

    def glob(self, pattern):
        # pattern intentionally unquoted so the board's shell expands it
        r = self.run(f"for f in {pattern}; do [ -e \"$f\" ] && echo \"$f\"; done; true")
        return [x for x in r.out.split("\n") if x]

    def exists(self, path):
        return self.run(f"test -e {shlex.quote(path)}").rc == 0

    def reachable(self):
        try:
            return self.run("true", timeout=10).rc == 0
        except TransportError:
            return False

    def wait_reachable(self, timeout_s, poll_s=5):
        end = time.time() + timeout_s
        while time.time() < end:
            if self.reachable():
                return True
            self.sleep(poll_s)
        return False

    def sleep(self, seconds):
        if seconds > 0:
            time.sleep(seconds)

    def mark(self, **kw):
        """Runner context (scenario, loop). No-op on real boards."""

    def close(self):
        pass


class SSHTransport(Transport):
    name = "ssh"

    def __init__(self, host, user="root", port=22, opts=None, ssh_bin="ssh"):
        self.host, self.user, self.port = host, user, int(port)
        self.opts = list(opts or [])
        self.ssh_bin = ssh_bin

    def _argv(self, cmd):
        return [self.ssh_bin, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
                "-p", str(self.port), *self.opts, f"{self.user}@{self.host}", cmd]

    def run(self, cmd, timeout=60, action=None):
        t0 = time.time()
        try:
            p = subprocess.run(self._argv(cmd), capture_output=True, text=True,
                               errors="replace", timeout=timeout)
            return Result(p.returncode, p.stdout, p.stderr, time.time() - t0)
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            return Result(124, out, "timeout", time.time() - t0)
        except FileNotFoundError:
            raise TransportError(f"ssh client '{self.ssh_bin}' not found on the controller")


class SerialTransport(Transport):
    """Run commands on the board console. Needs a logged-in root shell on the console."""
    name = "serial"

    def __init__(self, port, baud=115200):
        if serial is None:
            raise TransportError("pyserial not installed on the controller (pip install pyserial)")
        self.ser = serial.Serial(port, int(baud), timeout=0.2)
        self.lock = threading.Lock()

    def run(self, cmd, timeout=60, action=None):
        tok = f"__TB{random.getrandbits(32):08x}__"
        done = re.compile(re.escape(tok) + r"(\d+)" + re.escape(tok))
        t0 = time.time()
        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write(f"{cmd}; echo {tok}$?{tok}\n".encode())
            buf = ""
            while time.time() - t0 < timeout:
                buf += self.ser.read(4096).decode(errors="replace")
                m = done.search(buf)
                if m:
                    body = buf[:m.start()].replace("\r", "")
                    body = body.split("\n", 1)[1] if "\n" in body else ""  # drop echoed cmd
                    return Result(int(m.group(1)), body, "", time.time() - t0)
        return Result(124, buf, "timeout", time.time() - t0)

    def close(self):
        self.ser.close()


class ConsoleLogger:
    """Passively tee a serial console to a file during a scenario.

    This is what still sees a kernel panic / FW assert after SSH has died.
    Degrades to a no-op (available=False) without pyserial or a console port.
    """

    def __init__(self, port, baud, path):
        self.path, self.available, self.reason = path, False, ""
        self._stop = threading.Event()
        if serial is None:
            self.reason = "pyserial not installed"
            return
        try:
            self.ser = serial.Serial(port, int(baud), timeout=0.5)
        except Exception as e:  # noqa: BLE001 - any open failure means "not available"
            self.reason = f"cannot open {port}: {e}"
            return
        self.available = True
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()

    def _loop(self):
        with open(self.path, "a", errors="replace") as f:
            while not self._stop.is_set():
                data = self.ser.read(4096)
                if data:
                    f.write(data.decode(errors="replace"))
                    f.flush()

    def stop(self):
        if self.available:
            self._stop.set()
            self.th.join(timeout=3)
            self.ser.close()

    def text(self):
        if not self.available or not os.path.exists(self.path):
            return None
        with open(self.path, errors="replace") as f:
            return f.read()


def make_transport(board_cfg):
    kind = board_cfg.get("transport", "ssh")
    if kind == "ssh":
        s = board_cfg["ssh"]
        return SSHTransport(s["host"], s.get("user", "root"), s.get("port", 22), s.get("opts"))
    if kind == "serial":
        c = board_cfg["console"]
        return SerialTransport(c["port"], c.get("baud", 115200))
    raise TransportError(f"unknown transport {kind!r}")
