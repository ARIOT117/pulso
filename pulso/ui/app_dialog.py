"""Detalles de una aplicación (botón «i» o doble clic): cifras, gráficas de 5 min y procesos."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Pango  # noqa: E402

from ..collector.apps import SYSTEM_KEY
from ..collector.history import WINDOW
from ..format import fmt_bytes, fmt_pct, fmt_rate
from .app_list import app_icon, cpu_pct
from .scales import smooth_range
from .sparkline import Sparkline
from .theme import COLORS

MAX_PROCS = 80


class _ProcRow(Gtk.Box):
    def __init__(self):
        super().__init__(spacing=14, css_classes=["proc-row"])
        self.name = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, css_classes=["proc-name"])
        self.pid = Gtk.Label(xalign=1, width_chars=7, css_classes=["proc-pid", "numeric"])
        self.mem = Gtk.Label(xalign=1, width_chars=10, css_classes=["proc-num", "numeric"])
        self.cpu = Gtk.Label(xalign=1, width_chars=7, css_classes=["proc-num", "numeric"])
        for w in (self.name, self.pid, self.mem, self.cpu):
            self.append(w)


class AppDialog(Adw.Dialog):
    def __init__(self, sample):
        super().__init__(content_width=820, content_height=680, css_classes=["metric-dialog"])
        self.key = sample.key

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(spacing=14, css_classes=["app-head"])
        head.append(Gtk.Image(gicon=app_icon(sample.icon), pixel_size=44))
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER, hexpand=True)
        titles.append(Gtk.Label(label=sample.name, xalign=0, css_classes=["app-title"],
                                ellipsize=Pango.EllipsizeMode.END))
        self.subtitle = Gtk.Label(xalign=0, css_classes=["d-sub"])
        titles.append(self.subtitle)
        head.append(titles)
        close = Gtk.Button(label="×", css_classes=["close-btn"], valign=Gtk.Align.CENTER, tooltip_text="Cerrar (Esc)")
        close.connect("clicked", lambda *_: self.close())
        head.append(close)
        root.append(head)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, css_classes=["dialog-main"])

        grid = Gtk.Grid(column_homogeneous=True, css_classes=["stats-grid"])
        self.cells = {}
        for i, (k, label) in enumerate((("pss", "Memoria (PSS)"), ("cpu", "CPU"), ("gpu", "GPU · VRAM"),
                                        ("disk", "Disco"), ("net", "Red (TCP)"), ("state", "Estado"))):
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["stat-cell"])
            cell.append(Gtk.Label(label=label, xalign=0, css_classes=["stat-k"]))
            v = Gtk.Label(xalign=0, css_classes=["stat-v", "numeric"], ellipsize=Pango.EllipsizeMode.END)
            cell.append(v)
            grid.attach(cell, i % 3, i // 3, 1, 1)
            self.cells[k] = v
        body.append(grid)

        charts = Gtk.Box(spacing=24, homogeneous=True)
        self.charts = {}
        for k, label, color in (("cpu", "CPU · últimos 5 min", COLORS["cpu"]),
                                ("pss", "Memoria · últimos 5 min", COLORS["mem"])):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.append(Gtk.Label(label=label, xalign=0, css_classes=["d-section"]))
            ch = Sparkline([color], window=WINDOW, points=120, height=80, grid=True, end_dot=True)
            box.append(ch)
            charts.append(box)
            self.charts[k] = ch
        body.append(charts)

        procs_head = Gtk.Box(spacing=14, css_classes=["proc-head"])
        for text, w, x in (("Proceso", None, 0), ("PID", 7, 1), ("Memoria", 10, 1), ("CPU", 7, 1)):
            lbl = Gtk.Label(label=text, xalign=x, css_classes=["stat-k"])
            if w:
                lbl.set_width_chars(w)
            else:
                lbl.set_hexpand(True)
            procs_head.append(lbl)
        body.append(procs_head)
        self.proc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.proc_box)
        self.rows = []
        self.note = Gtk.Label(xalign=0, wrap=True, css_classes=["faint", "caption"])
        body.append(self.note)

        root.append(Gtk.ScrolledWindow(child=body, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.set_child(root)

    def update(self, snap, history):
        sample = next((a for a in snap.apps if a.key == self.key), None)
        h = history.apps.get(self.key)
        if h:
            scale = cpu_pct(1, snap.ncpu)
            times = list(h["t"])
            cpu = [v * scale for v in h["cpu"]]
            self.charts["cpu"].set_data(times, [cpu], scale=lambda sm: smooth_range("app_cpu", sm))
            self.charts["pss"].set_data(times, [list(h["pss"])], scale=lambda sm: smooth_range("app_mem", sm))
        if sample is None:
            self.subtitle.set_label("La aplicación se ha cerrado")
            for v in self.cells.values():
                v.set_label("—")
            for r in self.rows:
                r.set_visible(False)
            return

        n = len(sample.procs)
        self.subtitle.set_label(f"{n} proceso{'s' if n != 1 else ''}"
                                + (f" · {sample.key}" if sample.key != SYSTEM_KEY else ""))
        c = self.cells
        c["pss"].set_label(("≥ " if sample.pss_unknown else "") + fmt_bytes(sample.pss))
        c["cpu"].set_label(fmt_pct(cpu_pct(sample.cpu, snap.ncpu)))
        c["gpu"].set_label(f"{fmt_pct(sample.gpu)} · {fmt_bytes(sample.vram)}")
        c["disk"].set_label(f"↓ {fmt_rate(sample.read)}  ↑ {fmt_rate(sample.write)}")
        c["net"].set_label(f"↓ {fmt_rate(sample.rx)}  ↑ {fmt_rate(sample.tx)}")
        c["state"].set_label("Detenida" if sample.paused else "En ejecución")

        procs = sorted(sample.procs, key=lambda p: (p.pss or 0, p.cpu), reverse=True)[:MAX_PROCS]
        while len(self.rows) < len(procs):
            r = _ProcRow()
            self.proc_box.append(r)
            self.rows.append(r)
        for i, r in enumerate(self.rows):
            if i >= len(procs):
                r.set_visible(False)
                continue
            p = procs[i]
            r.set_visible(True)
            r.name.set_label(p.name + ("  (detenido)" if p.state in "Tt" else ""))
            r.pid.set_label(str(p.pid))
            r.mem.set_label(fmt_bytes(p.pss) if p.pss is not None else "sin permiso")
            r.cpu.set_label(fmt_pct(cpu_pct(p.cpu, snap.ncpu)))
        notes = []
        if n > MAX_PROCS:
            notes.append(f"Se muestran los {MAX_PROCS} procesos con más memoria de {n}.")
        if sample.pss_unknown:
            notes.append(f"{sample.pss_unknown} procesos son de otro usuario (root): su memoria no se "
                         "puede leer sin privilegios y no está sumada.")
        self.note.set_label(" ".join(notes))
        self.note.set_visible(bool(notes))
