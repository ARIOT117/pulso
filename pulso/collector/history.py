"""Historial en memoria: un ring buffer por app y por métrica (5 min a 1 muestra/s).

Se llena desde que arranca la app, esté abierta o no la ventana de detalle; las tarjetas y el
detalle leen el mismo buffer. Cada muestra lleva su marca de tiempo en la serie "t".
"""

from collections import deque

INTERVAL = 1.0                       # segundos entre muestras
WINDOW = 300.0                       # ventana del detalle: 5 min
CAPACITY = int(WINDOW / INTERVAL)
APP_METRICS = ("pss", "cpu", "gpu", "vram", "read", "write", "rx", "tx")
SYSTEM_METRICS = ("cpu", "mem", "gpu", "vram", "read", "write", "rx", "tx")


class History:
    def __init__(self):
        self.apps = {}      # key -> {metric: deque}
        self.system = {m: deque(maxlen=CAPACITY) for m in SYSTEM_METRICS + ("t",)}
        self._last_seen = {}

    def push(self, snap):
        s = snap.system
        g = s.gpu or {}
        for m, v in (("cpu", s.cpu), ("mem", s.mem_used), ("gpu", g.get("util", 0.0)),
                     ("vram", g.get("mem_used", 0)), ("read", s.read), ("write", s.write),
                     ("rx", s.rx), ("tx", s.tx), ("t", snap.time)):
            self.system[m].append(v)

        present = set()
        for a in snap.apps:
            present.add(a.key)
            h = self.apps.get(a.key)
            if h is None:
                h = self.apps[a.key] = {m: deque(maxlen=CAPACITY) for m in APP_METRICS + ("t",)}
            for m in APP_METRICS:
                h[m].append(getattr(a, m))
            h["t"].append(snap.time)
            self._last_seen[a.key] = snap.time

        # Las apps cerradas siguen registrando ceros hasta que su historial deja de verse
        for key, h in list(self.apps.items()):
            if key in present:
                continue
            if snap.time - self._last_seen[key] > CAPACITY:
                del self.apps[key]
                del self._last_seen[key]
            else:
                for m in APP_METRICS:
                    h[m].append(0)
                h["t"].append(snap.time)
