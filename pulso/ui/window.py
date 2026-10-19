"""Ventana principal (diseño «papel oscuro»): cabecera, franja de métricas, tabla y acciones."""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import VERSION_LABEL
from ..collector.history import History
from ..format import fmt_bytes, fmt_pct
from . import settings
from .actions import ACTIONS, ActionBar, confirm_kill, send_signal
from .app_dialog import AppDialog
from .app_list import AppList
from .metrics import MetricDialog, MetricStrip

LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "data", "io.github.ariot117.Pulso.svg")


class PulsoWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Pulso", default_width=1280, default_height=800,
                         css_classes=["pulso"])
        self.history = History()
        self._last = None
        self._first = True
        self.dialog = None

        # ---- cabecera: logo · búsqueda · controles ----
        header = Adw.HeaderBar(css_classes=["pulso-header"])
        brand = Gtk.Box(spacing=10, width_request=200)
        logo = Gtk.Image(pixel_size=26)
        if os.path.exists(LOGO):
            logo.set_from_file(LOGO)
        else:
            logo.set_from_icon_name("io.github.ariot117.Pulso")
        brand.append(logo)
        brand.append(Gtk.Label(label="Pulso", css_classes=["brand"]))
        self.brand = brand
        header.pack_start(brand)

        self.search = Gtk.SearchEntry(placeholder_text="Buscar aplicación", css_classes=["pill-search"],
                                      width_request=380)
        self.search.connect("search-changed", lambda e: self.app_list.set_query(e.get_text()))
        header.set_title_widget(self.search)

        menu = Gio.Menu()
        menu.append("CPU de la tabla: % de un núcleo", "win.cpu-per-core")
        menu.append("Abrir detalle de métricas", "win.metrics")
        menu.append("Acerca de Pulso", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Menú", css_classes=["flat"]))

        # ---- contenido ----
        self.strip = MetricStrip()
        self.strip.connect("open", lambda _s, key: self._open_metric(key))

        self.app_list = AppList()
        self.app_list.add_css_class("table-wrap")
        self.app_list.connect("selection-changed", self._on_selection)
        self.app_list.connect("app-activated", lambda *_: self._open_details())

        self.actions = ActionBar()
        self.actions.connect("action", self._on_action)
        self.actions.connect("details", lambda *_: self._open_details())

        # Adw.Spinner existe desde libadwaita 1.6; en 1.5 se usa el de GTK
        spinner_cls = getattr(Adw, "Spinner", None) or (lambda **kw: Gtk.Spinner(spinning=True, **kw))
        self.loading = spinner_cls(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                                   width_request=32, height_request=32, vexpand=True)
        self.stack = Gtk.Stack()
        self.stack.add_named(self.loading, "loading")
        self.stack.add_named(self.app_list, "list")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.strip)
        self.app_list.header.add_css_class("table-wrap")
        # tabla con ancho máximo de 1400 px centrada: sin huecos enormes en pantallas anchas
        table = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        table.append(self.app_list.header)
        table.append(self.stack)
        content.append(Adw.Clamp(child=table, maximum_size=1400, tightening_threshold=1400, vexpand=True))

        toolbar = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT, bottom_bar_style=Adw.ToolbarStyle.FLAT)
        toolbar.add_top_bar(header)
        toolbar.set_content(content)
        toolbar.add_bottom_bar(self.actions)
        self.toasts = Adw.ToastOverlay(child=toolbar)
        self.set_content(self.toasts)

        # ---- acciones y atajos ----
        act = Gio.SimpleAction.new_stateful("cpu-per-core", None, GLib.Variant.new_boolean(settings.cpu_per_core))
        act.connect("change-state", self._on_cpu_mode)
        self.add_action(act)
        metrics = Gio.SimpleAction.new("metrics", None)
        metrics.connect("activate", lambda *_: self._open_metric(settings.last_metric))
        self.add_action(metrics)
        app.set_accels_for_action("win.metrics", ["<Control>d"])
        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", self._on_about)
        self.add_action(about)

        keys = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>f"),
                                           Gtk.CallbackAction.new(lambda *_: self.search.grab_focus() or True)))
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                           Gtk.CallbackAction.new(lambda *_: self.app_list.clear_selection() or True)))
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Delete"),
                                           Gtk.CallbackAction.new(lambda *_: self._on_action(None, "term") or True)))
        self.add_controller(keys)

    # ---- eventos ----
    def _on_cpu_mode(self, action, value):
        action.set_state(value)
        settings.cpu_per_core = value.get_boolean()
        settings.save()
        self.app_list.refresh()

    def _on_about(self, *_):
        Adw.AboutDialog(application_name="Pulso", application_icon="io.github.ariot117.Pulso",
                        developer_name="Diego Esparza", version=VERSION_LABEL,
                        license_type=Gtk.License.MIT_X11, website="https://github.com/ARIOT117/pulso",
                        issue_url="https://github.com/ARIOT117/pulso/issues",
                        comments="Monitor de recursos por aplicación con memoria PSS real, "
                                 "GPU NVIDIA, disco y red.").present(self)

    def _selected_sample(self):
        item = self.app_list.selected_item()
        return item.sample if item else None

    def _on_selection(self, *_):
        self.actions.set_selection(self._selected_sample(), self._last)

    def _on_action(self, _bar, kind):
        sample = self._selected_sample()
        if sample is None or not self.actions.split.get_sensitive():
            return
        sig, verb = ACTIONS[kind]

        def run():
            sent, blocked = send_signal(sample, sig, self._last.self_pid if self._last else os.getpid())
            if sent and not blocked:
                msg = f"{sample.name} {verb}"
            elif sent:
                msg = f"{sample.name} {verb} en parte: el sistema protege {blocked} procesos"
            else:
                msg = f"El sistema no permite enviar señales a {sample.name}"
            self.toasts.add_toast(Adw.Toast(title=msg, timeout=3))
            if kind in ("term", "kill"):
                self.app_list.clear_selection()

        if kind == "kill":
            confirm_kill(self, sample.name, run)
        else:
            run()

    def _open_metric(self, key):
        self._present(MetricDialog(key, gpu_available=self._last.gpu_available if self._last else True))

    def _open_details(self):
        sample = self._selected_sample()
        if sample:
            self._present(AppDialog(sample))

    def _present(self, dialog):
        # máximo 88 % del alto de la ventana (en 1080p cabe todo sin scroll)
        dialog.set_content_height(min(760, int(self.get_height() * 0.88)))
        dialog.set_content_width(min(1000, int(self.get_width() * 0.92)))
        self.dialog = dialog
        dialog.connect("closed", lambda d: setattr(self, "dialog", None) if self.dialog is d else None)
        if self._last:
            dialog.update(self._last, self.history)
        dialog.present(self)

    # ---- datos ----
    def on_snapshot(self, snap):
        self._last = snap
        self.history.push(snap)
        if self._first:
            self._first = False
            self.stack.set_visible_child_name("list")
            self.app_list.set_gpu_visible(snap.gpu_available)
            self.strip.set_gpu_visible(snap.gpu_available)
        self.strip.update(snap, self.history)
        self.app_list.update(snap)
        self.actions.set_selection(self._selected_sample(), snap)
        if self.dialog:
            self.dialog.update(snap, self.history)
        me = next((p for a in snap.apps for p in a.procs if p.pid == snap.self_pid), None)
        if me:
            self.brand.set_tooltip_text(f"Pulso usa {fmt_bytes(me.pss)} (PSS) · "
                                        f"{fmt_pct(me.cpu / snap.ncpu * 100)} de CPU")
        return False
