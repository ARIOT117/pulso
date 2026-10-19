"""Tráfico TCP por proceso sin root, a partir de los contadores tcp_info que expone `ss`.

Limitación: solo TCP. El tráfico UDP/QUIC (p. ej. parte del de Chromium) no se ve aquí.
"""

import re
import subprocess

_USER_RE = re.compile(r'users:\(\("[^"]*",pid=(\d+)')
_ACKED_RE = re.compile(r"bytes_acked:(\d+)")
_RECV_RE = re.compile(r"bytes_received:(\d+)")


def _is_loopback(addr):
    host = addr.rsplit(":", 1)[0].strip("[]")
    if host.startswith("::ffff:"):
        host = host[7:]
    return host.startswith("127.") or host == "::1"


class NetSampler:
    def __init__(self):
        self._prev = None  # (local, peer) -> (pid, rx, tx)
        self.available = True
        self.connections = 0

    def _read(self):
        try:
            out = subprocess.run(
                ["ss", "-tinpHO"], capture_output=True, text=True, timeout=2
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            self.available = False
            return {}
        socks = {}
        for line in out.splitlines():
            m = _USER_RE.search(line)
            if not m:
                continue
            p = line.split()
            if _is_loopback(p[4]):
                continue  # tráfico local entre procesos, no es red
            rx = _RECV_RE.search(line)
            tx = _ACKED_RE.search(line)
            socks[(p[3], p[4])] = (
                int(m.group(1)),
                int(rx.group(1)) if rx else 0,
                int(tx.group(1)) if tx else 0,
            )
        self.connections = len(socks)
        return socks

    def sample(self):
        """Bytes {pid: (rx, tx)} transferidos desde la muestra anterior."""
        cur = self._read()
        prev, self._prev = self._prev, cur
        if prev is None:
            return {}
        out = {}
        for key, (pid, rx, tx) in cur.items():
            prx, ptx = (0, 0)
            old = prev.get(key)
            if old:
                prx, ptx = old[1], old[2]
            drx, dtx = max(rx - prx, 0), max(tx - ptx, 0)
            if drx or dtx:
                a, b = out.get(pid, (0, 0))
                out[pid] = (a + drx, b + dtx)
        return out
