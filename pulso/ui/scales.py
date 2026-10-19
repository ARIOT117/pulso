"""Escala Y de las gráficas: un único rango «bonito» que usan a la vez la línea y sus guías.

Se calcula sobre la misma serie suavizada que se dibuja: yMax = niceCeil(máx × 1.3) con un piso
por métrica; la memoria usa un auto-rango estrecho para que se note su variación.
"""

import math

GIB = 2**30
_PCT_STEPS = (10, 20, 25, 50, 100)
_MANTISSAS = (1, 2, 2.5, 5, 10)


def nice_pct(v):
    return next((s for s in _PCT_STEPS if s >= v), 100)


def nice_decimal(v):
    """Redondea hacia arriba a 1, 2, 2.5 o 5 × 10^k."""
    if v <= 0:
        return 1.0
    k = 10 ** math.floor(math.log10(v))
    return next(m * k for m in _MANTISSAS if m * k >= v - 1e-9)


def nice_bytes(v):
    """Redondea hacia arriba en la unidad binaria que toque (KiB, MiB, GiB): 1, 2, 2.5, 5, 10…"""
    if v <= 0:
        return 1024.0
    unit = 1024 ** max(0, min(4, int(math.log(v, 1024))))
    m = nice_decimal(v / unit)
    return 1024 * unit if m >= 1000 else m * unit      # «1000 KiB» → «1 MiB»


MEM_PAD = 0.5 * GIB


def mem_range(values, mem_total=None):
    """Auto-rango estrecho para la memoria (mín − 0.5 GiB … máx + 0.5 GiB, en pasos de 0.5 GiB),
    para que la variación se note en lugar de una línea plana."""
    vals = [v for v in values if v is not None]
    if not vals:
        return (0.0, 2 * GIB)
    lo = max(0.0, math.floor((min(vals) - MEM_PAD) / MEM_PAD) * MEM_PAD)
    hi = math.ceil((max(vals) + MEM_PAD) / MEM_PAD) * MEM_PAD
    if mem_total:
        hi = min(hi, mem_total)
    return (lo, hi)


def smooth_range(metric, smoothed, mem_total=None):
    """Rango Y calculado sobre la serie suavizada que se dibuja: yMax = niceCeil(máx × 1.3),
    con un piso por métrica (p. ej. máx 15 % → 20 %). Memoria usa el auto-rango estrecho."""
    vals = [v for v in smoothed if v is not None]
    if metric == "mem":
        return mem_range(vals, mem_total)
    ref = max(vals, default=0.0) * 1.3
    if metric in ("cpu", "gpu"):
        return (0.0, nice_pct(min(100.0, max(10.0, ref))))
    if metric == "app_cpu":
        return (0.0, nice_decimal(max(5.0, ref)))
    if metric == "app_mem":
        return (0.0, nice_bytes(max(64 * 2**20, ref)))
    if metric == "disk":
        return (0.0, nice_bytes(max(512 * 1024, ref)))
    if metric == "net":
        return (0.0, nice_bytes(max(4 * 1024, ref)))
    return (0.0, nice_decimal(max(ref, 1e-9)))


def ago(seconds):
    """«hace 40 s», «hace 2 min»."""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"hace {seconds} s"
    return f"hace {round(seconds / 60)} min"
