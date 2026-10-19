"""Formato de números para la interfaz."""

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


def fmt_bytes(n):
    if n is None:
        return "—"
    n = float(n)
    for unit in _UNITS:
        if abs(n) < 1024 or unit == _UNITS[-1]:
            break
        n /= 1024
    if unit == "B":
        return f"{n:.0f} B"
    return f"{n:.2f} {unit}" if n < 10 else f"{n:.1f} {unit}" if n < 100 else f"{n:.0f} {unit}"


def fmt_rate(n):
    return fmt_bytes(n) + "/s"


def fmt_pct(n):
    return f"{n:.1f} %"
