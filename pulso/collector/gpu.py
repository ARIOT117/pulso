"""GPU NVIDIA vía NVML (ctypes sobre libnvidia-ml.so.1, sin dependencias extra)."""

import ctypes
from ctypes import byref, c_uint, c_ulonglong, c_void_p

NVML_ERROR_INSUFFICIENT_SIZE = 7
NVML_VALUE_NOT_AVAILABLE = 0xFFFFFFFFFFFFFFFF


class _ProcInfo(ctypes.Structure):
    _fields_ = [("pid", c_uint), ("usedGpuMemory", c_ulonglong),
                ("gpuInstanceId", c_uint), ("computeInstanceId", c_uint)]


class _UtilSample(ctypes.Structure):
    _fields_ = [("pid", c_uint), ("timeStamp", c_ulonglong), ("smUtil", c_uint),
                ("memUtil", c_uint), ("encUtil", c_uint), ("decUtil", c_uint)]


class _Utilization(ctypes.Structure):
    _fields_ = [("gpu", c_uint), ("memory", c_uint)]


class _Memory(ctypes.Structure):
    _fields_ = [("total", c_ulonglong), ("free", c_ulonglong), ("used", c_ulonglong)]


class GpuSampler:
    def __init__(self):
        self.available = False
        self.name = None
        self.driver = None
        self._devs = []
        self._last_ts = {}
        try:
            self._lib = ctypes.CDLL("libnvidia-ml.so.1")
            if self._lib.nvmlInit_v2() != 0:
                return
            count = c_uint()
            self._lib.nvmlDeviceGetCount_v2(byref(count))
            for i in range(count.value):
                dev = c_void_p()
                if self._lib.nvmlDeviceGetHandleByIndex_v2(i, byref(dev)) == 0:
                    self._devs.append(dev)
                    self._last_ts[i] = 0
            if self._devs:
                buf = ctypes.create_string_buffer(96)
                self._lib.nvmlDeviceGetName(self._devs[0], buf, 96)
                self.name = buf.value.decode()
                drv = ctypes.create_string_buffer(80)
                self.driver = drv.value.decode() if self._lib.nvmlSystemGetDriverVersion(drv, 80) == 0 else None
                self.available = True
        except OSError:
            pass

    def _running(self, fn, dev, vram):
        count = c_uint(0)
        rc = fn(dev, byref(count), None)
        if rc not in (0, NVML_ERROR_INSUFFICIENT_SIZE) or count.value == 0:
            return
        n = count.value + 8
        arr = (_ProcInfo * n)()
        count = c_uint(n)
        if fn(dev, byref(count), arr) != 0:
            return
        for p in arr[:count.value]:
            mem = p.usedGpuMemory if p.usedGpuMemory != NVML_VALUE_NOT_AVAILABLE else 0
            vram[p.pid] = max(vram.get(p.pid, 0), mem)  # un PID puede salir como C y G

    def _utilization(self, i, dev, util):
        count = c_uint(0)
        rc = self._lib.nvmlDeviceGetProcessUtilization(dev, None, byref(count),
                                                       c_ulonglong(self._last_ts[i]))
        if rc != NVML_ERROR_INSUFFICIENT_SIZE or count.value == 0:
            return
        arr = (_UtilSample * count.value)()
        rc = self._lib.nvmlDeviceGetProcessUtilization(dev, arr, byref(count),
                                                       c_ulonglong(self._last_ts[i]))
        if rc != 0:
            return
        acc = {}
        for s in arr[:count.value]:
            acc.setdefault(s.pid, []).append(s.smUtil)
            self._last_ts[i] = max(self._last_ts[i], s.timeStamp)
        for pid, vals in acc.items():
            util[pid] = util.get(pid, 0.0) + sum(vals) / len(vals)

    def sample(self):
        """(info del dispositivo, {pid: % uso}, {pid: bytes VRAM}) o (None, {}, {})."""
        if not self.available:
            return None, {}, {}
        util, vram = {}, {}
        total = {"name": self.name, "util": 0.0, "mem_used": 0, "mem_total": 0,
                 "temp": None, "power": None,
                 "fan": None, "clock": None, "driver": self.driver}
        for i, dev in enumerate(self._devs):
            self._running(self._lib.nvmlDeviceGetComputeRunningProcesses_v3, dev, vram)
            self._running(self._lib.nvmlDeviceGetGraphicsRunningProcesses_v3, dev, vram)
            self._utilization(i, dev, util)
            u = _Utilization()
            if self._lib.nvmlDeviceGetUtilizationRates(dev, byref(u)) == 0:
                total["util"] = max(total["util"], float(u.gpu))
            m = _Memory()
            if self._lib.nvmlDeviceGetMemoryInfo(dev, byref(m)) == 0:
                total["mem_used"] += m.used
                total["mem_total"] += m.total
            if i == 0:
                t = c_uint()
                if self._lib.nvmlDeviceGetTemperature(dev, 0, byref(t)) == 0:
                    total["temp"] = t.value
                pw = c_uint()
                if self._lib.nvmlDeviceGetPowerUsage(dev, byref(pw)) == 0:
                    total["power"] = pw.value / 1000
                fan = c_uint()
                if self._lib.nvmlDeviceGetFanSpeed(dev, byref(fan)) == 0:
                    total["fan"] = fan.value
                clk = c_uint()
                if self._lib.nvmlDeviceGetClockInfo(dev, 0, byref(clk)) == 0:  # NVML_CLOCK_GRAPHICS
                    total["clock"] = clk.value
        return total, util, vram
