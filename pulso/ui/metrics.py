"""Franja superior de métricas del sistema y su panel de detalle con pestañas."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, GObject, Gtk, Pango  # noqa: E402

from ..collector.apps import SYSTEM_KEY
from ..collector.history import WINDOW
from ..format import fmt_bytes, fmt_pct, fmt_rate
from . import settings
from .app_list import app_icon, cpu_pct
from .scales import ago, smooth_range
from .sparkline import Meter, SegmentBar, Sparkline
from .theme import COLORS, FREE, SHADES

METRICS = (
    ("cpu", "Procesador"),
    ("mem", "Memoria"),
    ("gpu", "GPU"),
    ("disk", "Disco"),
    ("net", "Red"),
)
STRIP_WINDOW = 60.0   # las tarjetas muestran el último minuto del mismo buffer que el detalle


def _axis_text(key, value):
    """Etiqueta de una guía del eje: el valor «bonito» exacto, sin decimales de más."""
    if value <= 0:
        return "0"
    if key in ("cpu", "gpu"):
        return f"{value:.0f} %"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    i = 0
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    text = f"{value:g} {units[i]}"
    return text + "/s" if key in ("disk", "net") else text


def _short_gpu(name):
    return (name or "GPU").replace("NVIDIA ", "").replace("GeForce ", "")


def _uptime(sec):
    h, m = int(sec // 3600), int(sec % 3600 // 60)
    return f"{h} h {m} min" if h else f"{m} min"


def _series(history, key):
    h = history.system
    if key == "disk":
        return [r + w for r, w in zip(h["read"], h["write"])]
    if key == "net":
        return [r + t for r, t in zip(h["rx"], h["tx"])]
    return list(h[key])


def strip_values(key, snap):
    """(valor, subtítulo) de la franja superior."""
    s, x = snap.system, snap.system.extra
    if key == "cpu":
        sub = f"{snap.ncpu} hilos"
        if x.get("cpu_temp") is not None:
            sub += f" · {x['cpu_temp']:.0f} °C"
        return fmt_pct(s.cpu), sub
    if key == "mem":
        return fmt_bytes(s.mem_used), f"de {fmt_bytes(s.mem_total)}"
    if key == "gpu":
        g = s.gpu or {}
        sub = _short_gpu(g.get("name"))
        if g.get("temp") is not None:
            sub += f" · {g['temp']} °C"
        return fmt_pct(g.get("util", 0)), sub
    if key == "disk":
        return fmt_rate(s.read + s.write), f"↓ {fmt_rate(s.read)}  ↑ {fmt_rate(s.write)}"
    return fmt_rate(s.rx + s.tx), f"↓ {fmt_rate(s.rx)}  ↑ {fmt_rate(s.tx)}"


class MetricCell(Gtk.Button):
    def __init__(self, key, label):
        super().__init__(css_classes=["metric-cell"], hexpand=True, overflow=Gtk.Overflow.HIDDEN)
        self.key = key
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, css_classes=["metric-text"])
        outer.append(box)
        head = Gtk.Box(spacing=7)
        dot = Gtk.Label()
        dot.set_markup(f'<span foreground="{COLORS[key]}" size="small">●</span>')
        head.append(dot)
        head.append(Gtk.Label(label=label, xalign=0, hexpand=True, css_classes=["metric-label"]))
        box.append(head)
        self.value = Gtk.Label(xalign=0, css_classes=["metric-value", "numeric"])
        box.append(self.value)
        self.sub = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["metric-sub", "numeric"])
        box.append(self.sub)
        # la gráfica va a sangre, sin el padding del texto
        self.chart = Sparkline([COLORS[key]], window=STRIP_WINDOW, points=40, height=48, fill_alpha=0.20,
                               line_width=1.5)
        self.chart.set_margin_top(14)
        outer.append(self.chart)
        self.set_child(outer)

    def update(self, snap, history):
        value, sub = strip_values(self.key, snap)
        self.value.set_label(value)
        self.sub.set_label(sub)
        times, vals = list(history.system["t"]), _series(history, self.key)
        total = snap.system.mem_total
        self.chart.set_data(times, [vals], scale=lambda sm: smooth_range(self.key, sm, total))


class MetricStrip(Gtk.Box):
    __gtype_name__ = "PulsoMetricStrip"
    __gsignals__ = {"open": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self):
        super().__init__(homogeneous=True, spacing=12, css_classes=["metric-strip"])
        self.cells = {}
        for key, label in METRICS:
            cell = MetricCell(key, label)
            cell.connect("clicked", lambda c: self.emit("open", c.key))
            self.cells[key] = cell
            self.append(cell)

    def set_gpu_visible(self, visible):
        self.cells["gpu"].set_visible(visible)

    def update(self, snap, history):
        for cell in self.cells.values():
            if cell.get_visible():
                cell.update(snap, history)


# ---------------------------------------------------------------- detalle

class _TopRow(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=7, css_classes=["top-row"])
        head = Gtk.Box(spacing=10)
        self.icon = Gtk.Image(pixel_size=22)
        self.name = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, css_classes=["top-name"])
        self.value = Gtk.Label(xalign=1, css_classes=["numeric", "top-value"])
        for w in (self.icon, self.name, self.value):
            head.append(w)
        self.append(head)
        self.bar = Meter(COLORS["cpu"], thickness=3, length=40, trough_alpha=0.035, alpha=0.7)
        self.bar.set_margin_start(32)
        self.bar.set_hexpand(True)
        self.append(self.bar)


class _CoreBars(Gtk.Grid):
    """Uso por hilo: cuadrícula de 2 columnas con «Hilo N», pista de 4 px y porcentaje."""

    def __init__(self):
        super().__init__(column_spacing=32, row_spacing=10, column_homogeneous=True, css_classes=["core-grid"])
        self.rows = []

    def update(self, values):
        per_col = (len(values) + 1) // 2
        while len(self.rows) < len(values):
            i = len(self.rows)
            row = Gtk.Box(spacing=12)
            name = Gtk.Label(label=f"Hilo {i}", xalign=0, width_chars=7, css_classes=["core-name", "numeric"])
            bar = Meter(COLORS["cpu"], thickness=5, length=60, trough_alpha=0.08)
            bar.set_hexpand(True)
            pct = Gtk.Label(xalign=1, width_chars=5, css_classes=["core-pct", "numeric"])
            for w in (name, bar, pct):
                row.append(w)
            self.attach(row, i // per_col, i % per_col, 1, 1)
            self.rows.append((pct, bar))
        for (pct, bar), v in zip(self.rows, values):
            pct.set_label(f"{v:.0f} %")
            bar.set_value(v / 100)


class MetricDialog(Adw.Dialog):
    def __init__(self, key, gpu_available=True):
        super().__init__(content_width=1000, content_height=700, css_classes=["metric-dialog"])
        self.set_follows_content_size(False)
        self.key = key
        self._gpu = gpu_available

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # pestañas
        tabs = Gtk.Box(spacing=4, css_classes=["dialog-tabs"])
        self.tabs = {}
        group = None
        for k, label in METRICS:
            if k == "gpu" and not gpu_available:
                continue
            btn = Gtk.ToggleButton(css_classes=["tab-btn"], group=group)
            group = group or btn
            inner = Gtk.Box(spacing=7)
            dot = Gtk.Label()
            dot.set_markup(f'<span foreground="{COLORS[k]}" size="small">●</span>')
            inner.append(dot)
            inner.append(Gtk.Label(label=label))
            btn.set_child(inner)
            btn.connect("toggled", self._on_tab, k)
            tabs.append(btn)
            self.tabs[k] = btn
        close = Gtk.Button(label="×", css_classes=["close-btn"], halign=Gtk.Align.END, hexpand=True,
                           tooltip_text="Cerrar (Esc)")
        close.connect("clicked", lambda *_: self.close())
        tabs.append(close)
        root.append(tabs)

        body = Gtk.Box(vexpand=True)

        # columna principal
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, css_classes=["dialog-main"], hexpand=True)
        head = Gtk.Box(spacing=20)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        self.title = Gtk.Label(xalign=0, css_classes=["d-title"])
        self.value = Gtk.Label(xalign=0, css_classes=["d-value", "numeric"])
        self.sub = Gtk.Label(xalign=0, css_classes=["d-sub"], ellipsize=Pango.EllipsizeMode.END)
        for w in (self.title, self.value, self.sub):
            titles.append(w)
        head.append(titles)
        live = Gtk.Label(valign=Gtk.Align.END, css_classes=["d-live"])
        live.set_markup('<span foreground="#55c483">●</span>  En vivo')
        head.append(live)
        main.append(head)

        chart_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        # la gráfica es lo primero que se encoge si falta alto (mín. 140 px, natural 170 px)
        self.chart = Sparkline([COLORS[key]], window=WINDOW, points=150, height=170, min_height=140,
                               fill_alpha=0.22, line_width=2, grid=True, end_dot=True,
                               label_fmt=lambda v: _axis_text(self.key, v))
        self.chart.set_vexpand(True)
        chart_box.set_vexpand(True)
        chart_box.append(self.chart)
        axis = Gtk.Box()
        self.since_label = Gtk.Label(xalign=0, hexpand=True, css_classes=["axis-label"])
        axis.append(self.since_label)
        axis.append(Gtk.Label(label="ahora", xalign=1, css_classes=["axis-label"]))
        chart_box.append(axis)
        main.append(chart_box)

        self.stats = Gtk.Grid(column_homogeneous=True, css_classes=["stats-grid"])
        self.stat_cells = []
        for i in range(6):
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["stat-cell"])
            k = Gtk.Label(xalign=0, css_classes=["stat-k"])
            v = Gtk.Label(xalign=0, css_classes=["stat-v", "numeric"], ellipsize=Pango.EllipsizeMode.END)
            cell.append(k)
            cell.append(v)
            self.stats.attach(cell, i % 3, i // 3, 1, 1)
            self.stat_cells.append((k, v, cell))
        self._stats_layout = None
        main.append(self.stats)

        brk = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.break_title = Gtk.Label(xalign=0, css_classes=["d-section"])
        brk.append(self.break_title)
        self.cores = _CoreBars()
        brk.append(self.cores)
        self.segbar = SegmentBar()
        brk.append(self.segbar)
        self.legend = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=22, row_spacing=8,
                                  max_children_per_line=6)
        brk.append(self.legend)
        self._legend_labels = []
        main.append(brk)

        # si aun encogiendo la gráfica no cabe, la columna hace scroll con una barra fina siempre visible
        scroller = Gtk.ScrolledWindow(child=main, hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      overlay_scrolling=False, css_classes=["thin-scroll"])
        body.append(scroller)

        # columna lateral: mayores consumidores
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["dialog-side"])
        side.set_size_request(300, -1)
        side.set_hexpand(False)
        self.top_title = Gtk.Label(xalign=0, css_classes=["d-section", "side-title"])
        side.append(self.top_title)
        self.top_rows = []
        for _ in range(7):
            r = _TopRow()
            side.append(r)
            self.top_rows.append(r)
        self.top_empty = Gtk.Label(label="Sin actividad en este momento", xalign=0, visible=False,
                                   css_classes=["top-empty"])
        side.append(self.top_empty)
        body.append(side)
        root.append(body)
        self.set_child(root)

        self._snap = self._history = None
        if key not in self.tabs:          # p. ej. la última pestaña era GPU y ya no hay GPU
            key = self.key = "cpu"
        self.tabs[key].set_active(True)

    def _on_tab(self, btn, key):
        if not btn.get_active():
            return
        changed = key != self.key
        self.key = key
        settings.last_metric = key
        settings.save()
        self.chart.set_colors(COLORS[key])
        if self._snap:
            # otra serie: sin morph; se muestra ya completa y entra con un fundido de 150 ms
            self.update(self._snap, self._history, animate=False)
            if changed:
                self.chart.crossfade()

    # ---- contenido por métrica ----
    def _content(self, snap, history):
        s, x, key = snap.system, snap.system.extra, self.key
        hs = _series(history, key)
        avg = sum(hs) / len(hs) if hs else 0
        peak = max(hs) if hs else 0
        apps = [a for a in snap.apps]

        def top(attr, fmt):
            val = attr if callable(attr) else (lambda a: getattr(a, attr))
            ranked = sorted(apps, key=val, reverse=True)
            # nunca se listan valores en cero (CPU < 0.05 %, B/s < 1, VRAM 0…)
            ranked = [a for a in ranked if val(a) >= (0.0005 if attr == "cpu" else 1)]
            return [(a, fmt(a), val(a)) for a in ranked]

        if key == "cpu":
            sub = s.cpu_model + (f" · {s.cpu_cores} núcleos" if s.cpu_cores else "") + f" · {snap.ncpu} hilos"
            freq = x.get("freq_ghz")
            stats = [("Promedio 5 min", fmt_pct(avg)), ("Máximo 5 min", fmt_pct(peak)),
                     ("Frecuencia", f"{freq:.2f} GHz" if freq else "—"), ("Procesos", str(s.nproc)),
                     ("Carga (1 min)", f"{x.get('load1', 0):.2f}"), ("Tiempo activo", _uptime(x.get("uptime", 0)))]
            if x.get("cpu_temp") is not None:
                sub += f" · {x['cpu_temp']:.0f} °C"
            return dict(title="Uso del procesador", value=fmt_pct(s.cpu), sub=sub,
                        stats=stats, break_title="Uso por hilo", cores=x.get("cores", []),
                        top_title="Mayor uso de CPU",
                        top=top("cpu", lambda a: fmt_pct(cpu_pct(a.cpu, snap.ncpu)))), hs

        if key == "mem":
            apps_pss = sum(a.pss for a in apps if a.key != SYSTEM_KEY)
            free = x.get("mem_free", 0)
            cache = max(x.get("mem_available", 0) - free, 0)
            system = max(s.mem_used - apps_pss, 0)
            sh = SHADES["mem"]
            stats = [("Disponible", fmt_bytes(x.get("mem_available", 0))), ("Apps (PSS)", fmt_bytes(apps_pss)),
                     ("En caché", fmt_bytes(x.get("mem_cached", 0))),
                     ("Swap", f"{fmt_bytes(s.swap_used)} de {fmt_bytes(s.swap_total)}"),
                     ("Compartida", fmt_bytes(x.get("mem_shared", 0))), ("Máximo 5 min", fmt_bytes(peak))]
            segs = [("Apps", apps_pss, sh[0]), ("Sistema", system, sh[1]), ("Caché", cache, sh[2]), ("Libre", free, FREE)]
            return dict(title="Memoria en uso", value=fmt_bytes(s.mem_used), sub=f"de {fmt_bytes(s.mem_total)}",
                        stats=stats,
                        break_title="Composición", segs=[(l, v, fmt_bytes(v), c) for l, v, c in segs],
                        top_title="Mayor uso de memoria", top=top("pss", lambda a: fmt_bytes(a.pss))), hs

        if key == "gpu":
            g = s.gpu or {}
            sub = g.get("name", "GPU") + (f" · driver {g['driver']}" if g.get("driver") else "")
            stats = [("VRAM", f"{fmt_bytes(g.get('mem_used', 0))} de {fmt_bytes(g.get('mem_total', 0))}"),
                     ("Temperatura", f"{g['temp']} °C" if g.get("temp") is not None else "—"),
                     ("Consumo", f"{g['power']:.1f} W" if g.get("power") is not None else "—"),
                     ("Ventilador", f"{g['fan']} %" if g.get("fan") is not None else "—"),
                     ("Reloj", f"{g['clock']} MHz" if g.get("clock") is not None else "—"),
                     ("Promedio 5 min", fmt_pct(avg))]
            by_vram = sorted((a for a in apps if a.vram), key=lambda a: a.vram, reverse=True)
            sh = SHADES["gpu"]
            segs = [(a.name, a.vram, sh[i]) for i, a in enumerate(by_vram[:2])]
            rest = max(g.get("mem_used", 0) - sum(v for _, v, _ in segs), 0)
            segs.append(("Otras", rest, sh[2]))
            segs.append(("Libre", max(g.get("mem_total", 0) - g.get("mem_used", 0), 0), FREE))
            return dict(title="Uso de GPU", value=fmt_pct(g.get("util", 0)), sub=sub,
                        stats=stats, break_title="VRAM por aplicación",
                        segs=[(l, v, fmt_bytes(v), c) for l, v, c in segs],
                        top_title="Mayor uso de VRAM", top=top("vram", lambda a: fmt_bytes(a.vram))), hs

        if key == "disk":
            sub = " · ".join(p for p in (s.disk_name, s.disk_model,
                                         fmt_bytes(s.disk_size) if s.disk_size else None) if p)
            stats = [("Lectura", fmt_rate(s.read)), ("Escritura", fmt_rate(s.write)),
                     ("Máximo 5 min", fmt_rate(peak)), ("Tiempo activo", f"{x.get('disk_busy', 0):.0f} %"),
                     ("Leído desde el arranque", fmt_bytes(x.get("disk_read_total", 0))),
                     ("Escrito desde el arranque", fmt_bytes(x.get("disk_written_total", 0)))]
            used, total = x.get("fs_used", 0), x.get("fs_total", 0)
            sh = SHADES["disk"]
            segs = [("Usado", used, sh[0]), ("Libre", max(total - used, 0), FREE)]
            return dict(title="Actividad de disco", value=fmt_rate(s.read + s.write), sub=sub,
                        stats=stats, break_title="Almacenamiento ( / )",
                        segs=[(l, v, fmt_bytes(v), c) for l, v, c in segs],
                        top_title="Mayor actividad de disco",
                        top=top(lambda a: a.read + a.write, lambda a: fmt_rate(a.read + a.write))), hs

        sub = " · ".join(p for p in (x.get("iface"), x.get("iface_kind") if x.get("iface") else None, x.get("ip")) if p)
        stats = [("Descarga", fmt_rate(s.rx)), ("Subida", fmt_rate(s.tx)), ("Máximo 5 min", fmt_rate(peak)),
                 ("Recibido desde el arranque", fmt_bytes(x.get("net_rx_total", 0))),
                 ("Enviado desde el arranque", fmt_bytes(x.get("net_tx_total", 0))),
                 ("Conexiones TCP", str(s.tcp_connections))]
        sh = SHADES["net"]
        segs = [("Descarga", s.rx, sh[0]), ("Subida", s.tx, sh[2])]
        return dict(title="Tráfico de red", value=fmt_rate(s.rx + s.tx), sub=sub or "Sin conexión",
                    stats=stats, break_title="Tráfico actual",
                    segs=[(l, v, fmt_rate(v), c) for l, v, c in segs],
                    top_title="Mayor uso de red (TCP)",
                    top=top(lambda a: a.rx + a.tx, lambda a: fmt_rate(a.rx + a.tx))), hs

    def update(self, snap, history, animate=True):
        self._snap, self._history = snap, history
        d, hs = self._content(snap, history)
        self.title.set_label(d["title"])
        self.value.set_label(d["value"])
        self.sub.set_label(d["sub"])
        times = list(history.system["t"])
        total = snap.system.mem_total
        self.chart.set_data(times, [hs], animate=animate, scale=lambda sm: smooth_range(self.key, sm, total))
        span = self.chart.span
        self.since_label.set_label("hace 5 min" if span >= WINDOW - 1 else ago(span))

        # estadísticas sin dato («—») se ocultan y la cuadrícula se reacomoda
        stats = [(sk, sv) for sk, sv in d["stats"] if sv not in ("—", "", None)]
        layout = tuple(sk for sk, _ in stats)
        if layout != self._stats_layout:
            self._stats_layout = layout
            for _k, _v, cell in self.stat_cells:
                if cell.get_parent() is not None:
                    self.stats.remove(cell)
            for i, (_k, _v, cell) in enumerate(self.stat_cells[:len(stats)]):
                self.stats.attach(cell, i % 3, i // 3, 1, 1)
        for (k, v, _c), (sk, sv) in zip(self.stat_cells, stats):
            k.set_label(sk)
            v.set_label(sv)

        self.break_title.set_label(d["break_title"])
        is_cpu = "cores" in d
        self.cores.set_visible(is_cpu)
        self.segbar.set_visible(not is_cpu)
        self.legend.set_visible(not is_cpu)
        if is_cpu:
            self.cores.update(d["cores"])
        else:
            segs = d["segs"]
            self.segbar.set_segments([(v, c) for _, v, _, c in segs], animate=animate)
            if len(self._legend_labels) != len(segs):
                self.legend.remove_all()
                self._legend_labels = []
                for _ in segs:
                    lbl = Gtk.Label(css_classes=["legend-item", "numeric"])
                    self.legend.append(lbl)
                    self._legend_labels.append(lbl)
            for lbl, (name, _v, text, color) in zip(self._legend_labels, segs):
                swatch = color if not color.startswith("rgba") else "#3a3a38"
                lbl.set_markup(f'<span foreground="{swatch}">■</span>  {GLib.markup_escape_text(name)}  '
                               f'<span foreground="#6f6e69">{GLib.markup_escape_text(text)}</span>')

        self.top_title.set_label(d["top_title"])
        ranked = d["top"]
        self.top_empty.set_visible(not ranked)
        best = ranked[0][2] if ranked and ranked[0][2] else 1
        for row, entry in zip(self.top_rows, ranked + [None] * len(self.top_rows)):
            row.set_visible(entry is not None)
            if entry is None:
                continue
            a, text, raw = entry
            row.icon.set_from_gicon(app_icon(a.icon))
            row.name.set_label(a.name)
            row.value.set_label(text)
            row.bar.set_color(COLORS[self.key], 0.7)
            row.bar.set_value(max(0.02, (raw or 0) / best) if best else 0.02, animate=animate)
