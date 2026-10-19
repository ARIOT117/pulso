"""Datos extra del sistema para los paneles de detalle (por hilo, frecuencia, disco, red…)."""

import glob
import os
import socket
import struct
import fcntl

from . import procfs


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _cpu_info():
    model, cores = None, None
    for line in (_read("/proc/cpuinfo", "") or "").splitlines():
        k, _, v = line.partition(":")
        k = k.strip()
        if k == "model name" and not model:
            model = " ".join(v.split()).replace("(R)", "").replace("(TM)", "").split(" @ ")[0]
            model = model.replace(" CPU", "")
        elif k == "cpu cores" and not cores:
            cores = int(v)
    return model or "Procesador", cores


def _hwmon_temp(names):
    for h in glob.glob("/sys/class/hwmon/hwmon*"):
        if _read(f"{h}/name") in names:
            t = _read(f"{h}/temp1_input")
            if t and t.isdigit():
                return h
    return None


def _main_disk():
    best, size = None, 0
    for d in procfs._DISKS:
        s = int(_read(f"/sys/block/{d}/size", "0") or 0) * 512
        if s > size:
            best, size = d, s
    model = _read(f"/sys/block/{best}/device/model") if best else None
    return best, " ".join(model.split()) if model else None, size


def _default_iface():
    for line in (_read("/proc/net/route", "") or "").splitlines()[1:]:
        p = line.split()
        if len(p) > 2 and p[1] == "00000000":
            return p[0]
    return None


def _ipv4(iface):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            packed = fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", iface[:15].encode()))  # SIOCGIFADDR
        finally:
            s.close()
        return socket.inet_ntoa(packed[20:24])
    except OSError:
        return None


class SystemExtras:
    def __init__(self):
        self.cpu_model, self.cpu_cores = _cpu_info()
        self._cpu_temp = _hwmon_temp({"coretemp", "k10temp", "zenpower"})
        self.disk, self.disk_model, self.disk_size = _main_disk()
        self._prev_cores = None
        self._prev_io_ticks = None

    def _cores(self):
        cur = []
        for line in (_read("/proc/stat", "") or "").splitlines()[1:]:
            if not line.startswith("cpu"):
                break
            v = [int(x) for x in line.split()[1:9]]
            idle = v[3] + v[4]
            cur.append((sum(v) - idle, sum(v)))
        prev, self._prev_cores = self._prev_cores, cur
        if not prev or len(prev) != len(cur):
            return [0.0] * len(cur)
        return [100.0 * (b - pb) / (t - pt) if t > pt else 0.0 for (b, t), (pb, pt) in zip(cur, prev)]

    def _freq_ghz(self):
        vals = []
        for p in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"):
            v = _read(p)
            if v and v.isdigit():
                vals.append(int(v))
        return sum(vals) / len(vals) / 1e6 if vals else None

    def _disk_busy(self, dt):
        ticks = None
        for line in (_read("/proc/diskstats", "") or "").splitlines():
            p = line.split()
            if p[2] == self.disk:
                ticks = int(p[12])
        prev, self._prev_io_ticks = self._prev_io_ticks, ticks
        if ticks is None or prev is None or not dt:
            return 0.0
        return min(100.0, (ticks - prev) / (dt * 1000) * 100)

    def sample(self, dt, meminfo_raw):
        load = (_read("/proc/loadavg", "0 0 0") or "0").split()
        uptime = float((_read("/proc/uptime", "0") or "0").split()[0])
        temp = None
        if self._cpu_temp:
            t = _read(f"{self._cpu_temp}/temp1_input")
            temp = int(t) / 1000 if t and t.isdigit() else None
        try:
            st = os.statvfs("/")
            fs_total, fs_free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        except OSError:
            fs_total = fs_free = 0
        iface = _default_iface()
        wireless = bool(iface) and os.path.isdir(f"/sys/class/net/{iface}/wireless")
        disk_r, disk_w = procfs.read_system_disk()
        net_rx, net_tx = procfs.read_system_net()
        return {
            "cores": self._cores(),
            "freq_ghz": self._freq_ghz(),
            "cpu_temp": temp,
            "load1": float(load[0]),
            "uptime": uptime,
            "mem_free": meminfo_raw["MemFree"],
            "mem_available": meminfo_raw["MemAvailable"],
            "mem_cached": meminfo_raw.get("Cached", 0) + meminfo_raw.get("Buffers", 0),
            "mem_shared": meminfo_raw.get("Shmem", 0),
            "disk_busy": self._disk_busy(dt),
            "disk_read_total": disk_r,
            "disk_written_total": disk_w,
            "fs_total": fs_total,
            "fs_used": fs_total - fs_free,
            "iface": iface,
            "iface_kind": "Wi-Fi" if wireless else "Ethernet",
            "ip": _ipv4(iface) if iface else None,
            "net_rx_total": net_rx,
            "net_tx_total": net_tx,
        }
