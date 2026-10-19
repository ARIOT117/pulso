"""Lectura de bajo nivel de /proc: estado, CPU, PSS e I/O por proceso, y totales del sistema."""

import os

CLK_TCK = os.sysconf("SC_CLK_TCK")
NCPU = os.cpu_count() or 1
PF_KTHREAD = 0x00200000


def _read(path):
    with open(path, "rb") as f:
        return f.read()


class Stat:
    __slots__ = ("pid", "comm", "state", "ppid", "starttime", "ticks", "kthread")


def read_stat(pid):
    """Devuelve Stat o None si el proceso ya no existe."""
    try:
        data = _read(f"/proc/{pid}/stat")
    except OSError:
        return None
    lp = data.find(b"(")
    rp = data.rfind(b")")
    fields = data[rp + 2:].split()
    s = Stat()
    s.pid = pid
    s.comm = data[lp + 1:rp].decode(errors="replace")
    s.state = fields[0].decode()
    s.ppid = int(fields[1])
    s.kthread = bool(int(fields[6]) & PF_KTHREAD)
    s.ticks = int(fields[11]) + int(fields[12])  # utime + stime
    s.starttime = int(fields[19])
    return s


def read_pss(pid):
    """PSS en bytes desde smaps_rollup; None si no hay permiso o el proceso murió."""
    try:
        data = _read(f"/proc/{pid}/smaps_rollup")
    except OSError:
        return None
    i = data.find(b"\nPss:")
    if i < 0:
        return None
    return int(data[i + 5:data.index(b"kB", i)]) * 1024


def read_io(pid):
    """(read_bytes, write_bytes) de I/O real a disco; None si no hay permiso."""
    try:
        data = _read(f"/proc/{pid}/io")
    except OSError:
        return None
    r = w = 0
    for line in data.splitlines():
        if line.startswith(b"read_bytes:"):
            r = int(line[11:])
        elif line.startswith(b"write_bytes:"):
            w = int(line[12:])
    return r, w


def read_cgroup(pid):
    try:
        data = _read(f"/proc/{pid}/cgroup").decode(errors="replace")
    except OSError:
        return ""
    for line in data.splitlines():
        if line.startswith("0::"):
            return line[3:]
    return ""


def read_exe(pid):
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def read_cmdline(pid):
    try:
        return _read(f"/proc/{pid}/cmdline").split(b"\0")
    except OSError:
        return []


def proc_uid(pid):
    try:
        return os.stat(f"/proc/{pid}").st_uid
    except OSError:
        return None


def list_pids():
    return [int(d) for d in os.listdir("/proc") if d.isdigit()]


# ---- Totales del sistema ----

def read_system_cpu():
    """(ticks ocupados, ticks totales) de /proc/stat."""
    line = _read("/proc/stat").split(b"\n", 1)[0].split()[1:]
    vals = [int(v) for v in line]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
    total = sum(vals[:8])  # sin guest, que ya va dentro de user
    return total - idle, total


def read_meminfo_raw():
    info = {}
    for line in _read("/proc/meminfo").decode().splitlines():
        key, _, rest = line.partition(":")
        info[key] = int(rest.split()[0]) * 1024
    return info


def read_meminfo(info=None):
    info = info or read_meminfo_raw()
    return {
        "total": info["MemTotal"],
        "used": info["MemTotal"] - info["MemAvailable"],
        "swap_total": info.get("SwapTotal", 0),
        "swap_used": info.get("SwapTotal", 0) - info.get("SwapFree", 0),
    }


def _physical_disks():
    skip = ("loop", "ram", "zram", "dm-", "md", "sr", "fd")
    return {d for d in os.listdir("/sys/block") if not d.startswith(skip)}


_DISKS = _physical_disks()


def read_system_disk():
    """(bytes leídos, bytes escritos) acumulados en discos físicos."""
    r = w = 0
    for line in _read("/proc/diskstats").decode().splitlines():
        p = line.split()
        if p[2] in _DISKS:
            r += int(p[5]) * 512
            w += int(p[9]) * 512
    return r, w


def read_system_net():
    """(bytes recibidos, bytes enviados) acumulados en interfaces físicas."""
    rx = tx = 0
    for line in _read("/proc/net/dev").decode().splitlines()[2:]:
        name, _, rest = line.partition(":")
        name = name.strip()
        if not os.path.exists(f"/sys/class/net/{name}/device"):
            continue
        p = rest.split()
        rx += int(p[0])
        tx += int(p[8])
    return rx, tx
