"""Punto de entrada de Pulso."""

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .collector.sampler import Sampler  # noqa: E402
from .ui import settings  # noqa: E402
from .ui.window import PulsoWindow  # noqa: E402

APP_ID = "io.github.ariot117.Pulso"


class PulsoApp(Adw.Application):
    def __init__(self):
        flags = Gio.ApplicationFlags.DEFAULT_FLAGS
        if os.environ.get("PULSO_NON_UNIQUE"):
            flags |= Gio.ApplicationFlags.NON_UNIQUE  # para desarrollo: no reutilizar la ventana abierta
        super().__init__(application_id=APP_ID, flags=flags)
        self.window = None
        self.sampler = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        settings.load()
        # el diseño es «papel oscuro»: forzamos el esquema oscuro de libadwaita
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(os.path.dirname(__file__), "ui", "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # icono propio también cuando se ejecuta sin instalar
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
        Gtk.Window.set_default_icon_name(APP_ID)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("window.close", ["<Control>w"])

    def do_activate(self):
        if not self.window:
            self.window = PulsoWindow(self)
            self.sampler = Sampler(lambda snap: GLib.idle_add(self.window.on_snapshot, snap))
            self.sampler.start()
        self.window.present()

    def do_shutdown(self):
        if self.sampler:
            self.sampler.stop()
        Adw.Application.do_shutdown(self)


def main():
    GLib.set_application_name("Pulso")
    return PulsoApp().run(sys.argv)
