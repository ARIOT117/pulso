"""Muestreo periódico: recoge todo en un hilo y entrega snapshots inmutables."""

import os
import random
import re
import threading
import time
from dataclasses import dataclass, field

from . import procfs
from .apps import SYSTEM_KEY, AppResolver
from .gpu import GpuSampler
from .net import NetSampler
from .system import SystemExtras

# Leer smaps_rollup es caro (el kernel recorre las tablas de páginas), así que la PSS se
# refresca escalonada: procesos activos cada 5 s, inactivos cada 15 s, nuevos al momento.
PSS_ACTIVE = 5.0
_TYPE_RE = re.compile(rb"--type=([\w-]+)")
PSS_IDLE = 15.0


@dataclass
class ProcSample:
    pid: int
    name: str
    uid: int
    state: str = "S"
    cpu: float = 0.0          # en núcleos (1.0 = un núcleo al 100 %)
    pss: int | None = None
    read: float = 0.0         # B/s
    write: float = 0.0
    rx: float = 0.0
    tx: float = 0.0
    gpu: float = 0.0          # % de la GPU
    vram: int = 0


@dataclass
class AppSample:
    key: str
    name: str
    icon: str | None
    procs: list = field(default_factory=list)
    cpu: float = 0.0
    pss: int = 0
    pss_unknown: int = 0      # procesos cuya PSS no se puede leer (otro usuario)
    read: float = 0.0
    write: float = 0.0
    rx: float = 0.0
    tx: float = 0.0
    gpu: float = 0.0
    vram: int = 0
    paused: bool = False      # todos sus procesos propios están detenidos (SIGSTOP)


@dataclass
class SystemSample:
    cpu: float                # 0..100
    mem_total: int
    mem_used: int
    swap_total: int
    swap_used: int
    read: float
    write: float
    rx: float
    tx: float
    gpu: dict | None
    nproc: int = 0
    tcp_connections: int = 0
    extra: dict = field(default_factory=dict)
    cpu_model: str = ""
    cpu_cores: int | None = None
    disk_name: str | None = None
    disk_model: str | None = None
    disk_size: int = 0


@dataclass
class Snapshot:
    time: float
    apps: list
    system: SystemSample
    ncpu: int
    net_available: bool
    gpu_available: bool
    self_pid: int


class _ProcMeta:
    __slots__ = ("starttime", "uid", "cgroup", "direct", "name")


class Collector:
    def __init__(self):
        self.resolver = AppResolver()
        self.gpu = GpuSampler()
        self.net = NetSampler()
        self.extras = SystemExtras()
        self._meta = {}       # pid -> _ProcMeta
        self._prev_ticks = {}
        self._prev_io = {}
        self._pss = {}        # pid -> (pss, momento de lectura)
        self._prev_time = None
        self._prev_sys_cpu = procfs.read_system_cpu()
        self._prev_disk = procfs.read_system_disk()
        self._prev_net = procfs.read_system_net()

    def _meta_for(self, st):
        m = self._meta.get(st.pid)
        if m and m.starttime == st.starttime:
            return m
        m = _ProcMeta()
        m.starttime = st.starttime
        m.uid = procfs.proc_uid(st.pid)
        m.cgroup = procfs.read_cgroup(st.pid)
        exe = procfs.read_exe(st.pid)
        by_cgroup = self.resolver.from_cgroup(m.cgroup)
        if by_cgroup and self.resolver.has_desktop(by_cgroup):
            m.direct = by_cgroup
        else:
            # scope sin .desktop (p. ej. un lanzador que reutiliza otro nombre): mejor el ejecutable
            m.direct = self.resolver.from_exe(m.uid, exe, st.comm) or by_cgroup
        name = os.path.basename(exe) if exe else st.comm
        if exe and exe.endswith(" (deleted)"):
            name = name[:-10]
        # Chromium/Electron reescriben su cmdline en una sola cadena con espacios
        m_type = _TYPE_RE.search(b" ".join(procfs.read_cmdline(st.pid)))
        if m_type:
            name += " · " + m_type.group(1).decode(errors="replace")
        m.name = name
        self._meta[st.pid] = m
        return m

    def collect(self):
        now = time.monotonic()
        dt = (now - self._prev_time) if self._prev_time else None
        self._prev_time = now

        stats = {}
        for pid in procfs.list_pids():
            st = procfs.read_stat(pid)
            if st and not st.kthread:
                stats[pid] = st
        for pid in list(self._meta):
            if pid not in stats:
                del self._meta[pid]
                self._prev_ticks.pop(pid, None)
                self._prev_io.pop(pid, None)
                self._pss.pop(pid, None)

        metas = {pid: self._meta_for(st) for pid, st in stats.items()}

        # Herencia del padre cuando comparte cgroup y no se resolvió solo
        resolved = {}

        def owner(pid, depth=0):
            if pid in resolved:
                return resolved[pid]
            m = metas.get(pid)
            if m is None:
                return None
            app = m.direct
            if app is None and depth < 64:
                ppid = stats[pid].ppid
                pm = metas.get(ppid)
                if pm is not None and pm.cgroup == m.cgroup:
                    app = owner(ppid, depth + 1)
            resolved[pid] = app
            return app

        net = self.net.sample()
        gpu_total, gpu_util, gpu_vram = self.gpu.sample()

        apps = {}
        for pid, st in stats.items():
            m = metas[pid]
            key = owner(pid) or SYSTEM_KEY
            p = ProcSample(pid=pid, name=m.name, uid=m.uid, state=st.state)

            prev = self._prev_ticks.get(pid)
            self._prev_ticks[pid] = st.ticks
            if dt and prev is not None:
                p.cpu = max(st.ticks - prev, 0) / procfs.CLK_TCK / dt

            cached = self._pss.get(pid)
            if cached is None:
                # desfase aleatorio para que las relecturas no coincidan todas en el mismo tick
                cached = self._pss[pid] = (procfs.read_pss(pid), now - random.uniform(0, PSS_ACTIVE))
            elif cached[0] is not None and now - cached[1] >= (PSS_ACTIVE if p.cpu > 0 else PSS_IDLE):
                cached = self._pss[pid] = (procfs.read_pss(pid), now)
            p.pss = cached[0]

            io = procfs.read_io(pid)
            if io is not None:
                pio = self._prev_io.get(pid)
                self._prev_io[pid] = io
                if dt and pio is not None:
                    p.read = max(io[0] - pio[0], 0) / dt
                    p.write = max(io[1] - pio[1], 0) / dt

            if pid in net and dt:
                p.rx, p.tx = net[pid][0] / dt, net[pid][1] / dt
            p.gpu = gpu_util.get(pid, 0.0)
            p.vram = gpu_vram.get(pid, 0)

            a = apps.get(key)
            if a is None:
                info = self.resolver.app(key)
                a = apps[key] = AppSample(key=key, name=info.name, icon=info.icon)
            a.procs.append(p)
            a.cpu += p.cpu
            if p.pss is None:
                a.pss_unknown += 1
            else:
                a.pss += p.pss
            a.read += p.read
            a.write += p.write
            a.rx += p.rx
            a.tx += p.tx
            a.gpu += p.gpu
            a.vram += p.vram

        me = os.getuid()
        for a in apps.values():
            own = [p for p in a.procs if p.uid == me]
            a.paused = bool(own) and all(p.state in "Tt" for p in own)

        # Totales del sistema
        busy, total = procfs.read_system_cpu()
        pb, pt = self._prev_sys_cpu
        self._prev_sys_cpu = (busy, total)
        cpu = 100.0 * (busy - pb) / (total - pt) if total > pt else 0.0
        raw_mem = procfs.read_meminfo_raw()
        mem = procfs.read_meminfo(raw_mem)
        disk = procfs.read_system_disk()
        netsys = procfs.read_system_net()
        d = dt or 1.0
        system = SystemSample(
            cpu=cpu, mem_total=mem["total"], mem_used=mem["used"],
            swap_total=mem["swap_total"], swap_used=mem["swap_used"],
            read=(disk[0] - self._prev_disk[0]) / d, write=(disk[1] - self._prev_disk[1]) / d,
            rx=(netsys[0] - self._prev_net[0]) / d, tx=(netsys[1] - self._prev_net[1]) / d,
            gpu=gpu_total,
            nproc=len(stats),
            tcp_connections=self.net.connections,
            extra=self.extras.sample(dt, raw_mem),
            cpu_model=self.extras.cpu_model, cpu_cores=self.extras.cpu_cores,
            disk_name=self.extras.disk, disk_model=self.extras.disk_model, disk_size=self.extras.disk_size,
        )
        self._prev_disk, self._prev_net = disk, netsys

        return Snapshot(time=time.time(), apps=list(apps.values()), system=system,
                        ncpu=procfs.NCPU, net_available=self.net.available,
                        gpu_available=self.gpu.available, self_pid=os.getpid())


class Sampler(threading.Thread):
    """Hilo que llama a `callback(snapshot)` cada `interval` segundos."""

    def __init__(self, callback, interval=1.0):
        super().__init__(daemon=True, name="pulso-sampler")
        self.callback = callback
        self.interval = interval
        self._halt = threading.Event()

    def run(self):
        collector = Collector()
        while not self._halt.is_set():
            t0 = time.monotonic()
            try:
                snap = collector.collect()
            except Exception as e:  # nunca matar el hilo por un proceso raro
                print("pulso: error al muestrear:", e)
            else:
                self.callback(snap)
            self._halt.wait(max(0.05, self.interval - (time.monotonic() - t0)))

    def stop(self):
        self._halt.set()
