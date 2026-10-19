"""Tabla de aplicaciones: Gtk.ColumnView ordenable, filtrable y con selección estable."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from ..format import fmt_bytes, fmt_pct, fmt_rate
from . import settings


def app_icon(icon_str):
    if icon_str:
        try:
            return Gio.Icon.new_for_string(icon_str)
        except Exception:
            pass
    return Gio.ThemedIcon.new("application-x-executable-symbolic")


def level_class(value, warn, hot):
    return "hot" if value >= hot else "warn" if value >= warn else None


def set_level(widget, cls):
    for c in ("warn", "hot"):
        if c != cls:
            widget.remove_css_class(c)
    if cls:
        widget.add_css_class(cls)


def rates_text(down, up):
    """«↓ x  ↑ y» mostrando solo lo que no es cero; «—» si no hay actividad."""
    parts = []
    if down >= 1:
        parts.append("↓ " + fmt_rate(down))
    if up >= 1:
        parts.append("↑ " + fmt_rate(up))
    return "  ".join(parts)


EMPTY = "·"


def set_cell(label, text):
    """Celda numérica: sin actividad se muestra un «·» al 30 % para que solo destaque lo activo."""
    if text:
        label.set_label(text)
        label.remove_css_class("empty-cell")
    else:
        label.set_label(EMPTY)
        label.add_css_class("empty-cell")


def cpu_pct(cores_used, ncpu):
    return cores_used * 100 if settings.cpu_per_core else cores_used / ncpu * 100


class AppItem(GObject.Object):
    __gtype_name__ = "PulsoAppItem"
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    name = GObject.Property(type=str, default="")
    pss = GObject.Property(type=float, default=0)
    cpu = GObject.Property(type=float, default=0)
    gpu = GObject.Property(type=float, default=0)
    disk = GObject.Property(type=float, default=0)
    net = GObject.Property(type=float, default=0)

    def __init__(self, sample):
        super().__init__()
        self.key = sample.key
        self.icon = app_icon(sample.icon)
        self.sample = None
        self.snap = None

    def update(self, sample, snap):
        self.sample = sample
        self.snap = snap
        self.name = sample.name
        self.pss = sample.pss
        self.cpu = sample.cpu
        self.gpu = sample.gpu + sample.vram / 1e12  # desempate por VRAM
        self.disk = sample.read + sample.write
        self.net = sample.rx + sample.tx
        self.emit("changed")


# ---- celdas ----

def _setup_app():
    box = Gtk.Box(spacing=12)
    img = Gtk.Image(pixel_size=24)
    name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["app-name"])
    procs = Gtk.Label(css_classes=["faint", "numeric"])
    badge = Gtk.Label(label="Detenida", css_classes=["paused-badge"], visible=False)
    for w in (img, name, procs, badge):
        box.append(w)
    box.img, box.name, box.procs, box.badge = img, name, procs, badge
    return box


def _update_app(w, item):
    s = item.sample
    w.img.set_from_gicon(item.icon)
    w.name.set_label(s.name)
    w.procs.set_label(str(len(s.procs)))
    w.badge.set_visible(s.paused)


def _setup_mem():
    from .sparkline import Meter
    from .theme import COLORS
    # la barra va pegada al nombre y ocupa el espacio libre (mín. 120 px); la cifra a la derecha
    box = Gtk.Box(spacing=14, hexpand=True)
    bar = Meter(COLORS["mem"], thickness=4, length=120, alpha=0.75)
    bar.set_hexpand(True)
    lbl = Gtk.Label(xalign=1, width_chars=9, css_classes=["numeric", "cell-num"])
    box.append(bar)
    box.append(lbl)
    box.bar, box.lbl = bar, lbl
    return box


def _update_mem(w, item):
    s, snap = item.sample, item.snap
    txt = fmt_bytes(s.pss)
    if s.pss_unknown:
        txt = "≥ " + txt
        w.set_tooltip_text(f"{s.pss_unknown} procesos de otros usuarios: su PSS solo se puede leer como root")
    else:
        w.set_tooltip_text(None)
    w.lbl.set_label(txt)
    top = max((a.pss for a in snap.apps), default=1) or 1
    w.bar.set_value(max(0.02, s.pss / top))
    set_level(w.lbl, level_class(s.pss, 1.5 * 2**30, 4 * 2**30))


def _setup_label():
    return Gtk.Label(xalign=1, css_classes=["numeric", "cell-num"])


def _update_cpu(w, item):
    ncpu = item.snap.ncpu
    w.set_label(fmt_pct(cpu_pct(item.sample.cpu, ncpu)))
    set_level(w, level_class(item.sample.cpu / ncpu * 100, 25, 60))


def _update_gpu(w, item):
    s = item.sample
    if s.gpu < 0.05 and not s.vram:
        set_cell(w, "")
        return
    w.remove_css_class("empty-cell")
    vram = (f'  <span size="small" foreground="#6f6e69">{GLib.markup_escape_text(fmt_bytes(s.vram))}</span>'
            if s.vram else "")
    w.set_markup(GLib.markup_escape_text(fmt_pct(s.gpu)) + vram)


def _update_disk(w, item):
    set_cell(w, rates_text(item.sample.read, item.sample.write))


def _update_net(w, item):
    set_cell(w, rates_text(item.sample.rx, item.sample.tx))


def _factory(setup, update):
    f = Gtk.SignalListItemFactory()

    def on_setup(_f, li):
        li.set_child(setup())

    def on_bind(_f, li):
        item, w = li.get_item(), li.get_child()
        update(w, item)
        li._pulso_handler = (item, item.connect("changed", lambda it: update(w, it)))

    def on_unbind(_f, li):
        h = getattr(li, "_pulso_handler", None)
        if h:
            h[0].disconnect(h[1])
            li._pulso_handler = None

    f.connect("setup", on_setup)
    f.connect("bind", on_bind)
    f.connect("unbind", on_unbind)
    return f


class TableHeader(Gtk.Box):
    """Cabecera propia: títulos numéricos alineados a la derecha y flecha de orden en texto."""

    def __init__(self, app_list):
        super().__init__(css_classes=["table-header"])
        self.app_list = app_list
        self.labels = {}
        for title, col in app_list.columns.items():
            btn = Gtk.Button(css_classes=["th"])
            lbl = Gtk.Label(xalign=0 if title == "Aplicación" else 1)
            btn.set_child(lbl)
            if col.get_expand():
                btn.set_hexpand(True)
            else:
                btn.set_size_request(col.get_fixed_width(), -1)
            btn.connect("clicked", lambda _b, t=title: self._clicked(t))
            col.connect("notify::visible", lambda c, _p, b=btn: b.set_visible(c.get_visible()))
            self.labels[title] = lbl
            self.append(btn)
        self.sort_title, self.descending = "Memoria", True
        self.refresh()

    def _clicked(self, title):
        if title == self.sort_title:
            self.descending = not self.descending
        else:
            self.sort_title, self.descending = title, title != "Aplicación"
        order = Gtk.SortType.DESCENDING if self.descending else Gtk.SortType.ASCENDING
        self.app_list.cv.sort_by_column(self.app_list.columns[title], order)
        self.app_list.resort()
        self.refresh()

    def refresh(self):
        for title, lbl in self.labels.items():
            arrow = (" ↓" if self.descending else " ↑") if title == self.sort_title else ""
            lbl.set_label(title + arrow)
            (lbl.add_css_class if arrow else lbl.remove_css_class)("th-active")


class AppList(Gtk.ScrolledWindow):
    __gtype_name__ = "PulsoAppList"
    __gsignals__ = {
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "app-activated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.store = Gio.ListStore(item_type=AppItem)
        self.items = {}
        self.query = ""
        self.selected_key = None
        self._syncing = False

        self.filter = Gtk.CustomFilter.new(self._match)
        filtered = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.cv = Gtk.ColumnView(show_row_separators=False, reorderable=False, css_classes=["app-table"])
        self.sorted = Gtk.SortListModel(model=filtered, sorter=self.cv.get_sorter())
        self.selection = Gtk.SingleSelection(model=self.sorted, autoselect=False, can_unselect=True)
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self.selection.connect("selection-changed", self._on_selection)
        self.cv.set_model(self.selection)
        self.cv.connect("activate", self._on_activate)

        def num(prop):
            return Gtk.NumericSorter(expression=Gtk.PropertyExpression.new(AppItem, None, prop))

        name_sorter = Gtk.StringSorter(expression=Gtk.PropertyExpression.new(AppItem, None, "name"))
        cols = [
            ("Aplicación", _setup_app, _update_app, name_sorter, False, 300),
            ("Memoria", _setup_mem, _update_mem, num("pss"), True, None),
            ("CPU", _setup_label, _update_cpu, num("cpu"), False, 90),
            ("GPU", _setup_label, _update_gpu, num("gpu"), False, 130),
            ("Disco", _setup_label, _update_disk, num("disk"), False, 210),
            ("Red", _setup_label, _update_net, num("net"), False, 210),
        ]
        self.columns = {}
        for title, setup, update, sorter, expand, width in cols:
            col = Gtk.ColumnViewColumn(title=title, factory=_factory(setup, update),
                                       sorter=sorter, expand=expand, resizable=False)
            if width:
                col.set_fixed_width(width)
            self.cv.append_column(col)
            self.columns[title] = col
        self.cv.sort_by_column(self.columns["Memoria"], Gtk.SortType.DESCENDING)
        self.set_child(self.cv)
        # la cabecera nativa no permite alinear títulos a la derecha: se oculta y se usa TableHeader
        native_header = self.cv.get_first_child()
        if native_header is not None and native_header.get_css_name() == "header":
            native_header.set_visible(False)
        self.header = TableHeader(self)

        # Al reordenar, la vista sigue a la fila "ancla" y se desplaza sola. Si el usuario
        # estaba arriba del todo, lo mantenemos arriba; solo su propio scroll lo cambia.
        self._follow_top = True
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", lambda *_: GLib.idle_add(self._user_moved) and False)
        self.cv.add_controller(scroll)
        drag = Gtk.GestureDrag()
        drag.connect("drag-end", lambda *_: self._user_moved())
        self.get_vscrollbar().add_controller(drag)
        self.get_vadjustment().connect("value-changed", self._on_value_changed)

    # ---- scroll ----
    def _user_moved(self):
        self._follow_top = self.get_vadjustment().get_value() < 1
        return False

    def _on_value_changed(self, adj):
        if self._follow_top and adj.get_value() > 0:
            GLib.idle_add(lambda: self._follow_top and adj.set_value(0) and False)

    # ---- selección ----
    def _on_selection(self, *_):
        if self._syncing:
            return
        item = self.selection.get_selected_item()
        self.selected_key = item.key if item else None
        self.emit("selection-changed")

    def _sync_selection(self):
        """Tras reordenar o filtrar, vuelve a seleccionar la misma app por su clave."""
        self._syncing = True
        try:
            pos = Gtk.INVALID_LIST_POSITION
            if self.selected_key:
                for i in range(self.sorted.get_n_items()):
                    if self.sorted.get_item(i).key == self.selected_key:
                        pos = i
                        break
            if self.selection.get_selected() != pos:
                self.selection.set_selected(pos)
        finally:
            self._syncing = False

    def clear_selection(self):
        self.selected_key = None
        self._sync_selection()
        self.emit("selection-changed")

    def selected_item(self):
        return self.items.get(self.selected_key) if self.selected_key else None

    def _on_activate(self, _cv, pos):
        item = self.sorted.get_item(pos)
        if item:
            self.emit("app-activated", item.key)

    # ---- filtro ----
    def _match(self, item):
        return not self.query or self.query in item.name.lower()

    def set_query(self, text):
        self.query = text.strip().lower()
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        self._sync_selection()

    def set_gpu_visible(self, visible):
        self.columns["GPU"].set_visible(visible)

    # ---- datos ----
    def update(self, snap):
        seen = set()
        for s in snap.apps:
            seen.add(s.key)
            item = self.items.get(s.key)
            if item is None:
                item = self.items[s.key] = AppItem(s)
                item.update(s, snap)
                self.store.append(item)
            else:
                item.update(s, snap)
        for key in [k for k in self.items if k not in seen]:
            item = self.items.pop(key)
            ok, pos = self.store.find(item)
            if ok:
                self.store.remove(pos)
        self.resort()
        if self.selected_key and self.selected_key not in self.items:
            self.selected_key = None
            self.emit("selection-changed")
        self._sync_selection()

    def resort(self):
        self._syncing = True
        self.cv.get_sorter().changed(Gtk.SorterChange.DIFFERENT)
        self._syncing = False
        self._sync_selection()

    def refresh(self):
        for item in self.items.values():
            item.emit("changed")
