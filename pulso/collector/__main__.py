"""CLI de prueba: imprime el consumo por aplicación. Uso: python3 -m pulso.collector"""

import time

from ..format import fmt_bytes, fmt_pct, fmt_rate
from .sampler import Collector


def main():
    c = Collector()
    c.collect()
    time.sleep(1)
    c.collect()
    time.sleep(1)
    t0 = time.perf_counter()
    snap = c.collect()
    took = (time.perf_counter() - t0) * 1000
    apps = sorted(snap.apps, key=lambda a: a.pss, reverse=True)
    print(f"{'Aplicación':32} {'Procs':>5} {'PSS':>11} {'CPU':>8} {'GPU':>7} {'VRAM':>10} "
          f"{'Disco L/E':>22} {'Red ↓/↑ (TCP)':>24}")
    for a in apps:
        pss = fmt_bytes(a.pss) + ("*" if a.pss_unknown else "")
        print(f"{a.name[:32]:32} {len(a.procs):>5} {pss:>11} {fmt_pct(a.cpu / snap.ncpu * 100):>8} "
              f"{fmt_pct(a.gpu):>7} {fmt_bytes(a.vram):>10} "
              f"{fmt_rate(a.read) + ' / ' + fmt_rate(a.write):>22} "
              f"{fmt_rate(a.rx) + ' / ' + fmt_rate(a.tx):>24}")
    s = snap.system
    print(f"\nSistema: CPU {fmt_pct(s.cpu)}  RAM usada {fmt_bytes(s.mem_used)} / {fmt_bytes(s.mem_total)}"
          f"  (PSS sumada de todo: {fmt_bytes(sum(a.pss for a in apps))})")
    if s.gpu:
        print(f"GPU {s.gpu['name']}: {s.gpu['util']:.0f} %  VRAM {fmt_bytes(s.gpu['mem_used'])}"
              f"  {s.gpu['temp']} °C  {s.gpu['power']} W")
    print(f"* = incluye procesos de otros usuarios cuya PSS no se puede leer sin root")
    print(f"Tiempo de una muestra: {took:.1f} ms")


main()
