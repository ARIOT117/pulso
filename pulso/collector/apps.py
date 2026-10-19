"""Asigna cada proceso a una aplicación.

Orden de resolución:
1. Scope de systemd del cgroup (app-*.scope, snap.*.scope, terminales).
2. Ejecutable del proceso emparejado con un .desktop (Exec / StartupWMClass / id).
3. Herencia del padre si comparte cgroup (p. ej. los hijos de Spotify).
Lo que quede va a "Procesos del sistema".
"""

import os
import re

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

SYSTEM_KEY = "__system__"

_LAUNCHERS = {"gnome", "flatpak", "kde", "xdg", "dbus"}
_SNAP_RE = re.compile(
    r"^snap\.([^.]+)\.(.+?)(?:-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?\.(?:scope|service)$"
)
_TERMINAL_SCOPES = {"ptyxis-spawn-": "org.gnome.Ptyxis", "vte-spawn-": "org.gnome.Terminal",
                    "kgx-spawn-": "org.gnome.Console"}
# Binarios que no deben convertirse en "app" aunque haya un .desktop con ese Exec
_NEVER_APPS = {"gnome-shell", "xwayland", "gnome-session-binary", "gnome-session",
               "dbus-daemon", "dbus-broker", "systemd", "sh", "bash", "zsh", "fish", "env",
               "python", "python3", "node", "java", "gjs", "sudo", "pkexec", "snap", "flatpak",
               "bwrap", "electron"}


_INTERPRETER_RE = re.compile(r"^(python|perl|ruby|node|php|lua)[\d.]*$")


def _is_generic(name):
    return name in _NEVER_APPS or bool(_INTERPRETER_RE.match(name))


def _unescape(s):
    return re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), s)


def _desktop(app_id):
    for cand in (app_id, app_id.lower()):
        try:
            info = Gio.DesktopAppInfo.new(cand + ".desktop")
        except TypeError:
            info = None
        if info:
            return info
    return None


class AppInfo:
    __slots__ = ("key", "name", "icon")

    def __init__(self, key, name, icon):
        self.key, self.name, self.icon = key, name, icon


class AppResolver:
    def __init__(self):
        self._uid = os.getuid()
        self._apps = {SYSTEM_KEY: AppInfo(SYSTEM_KEY, "Procesos del sistema", "application-x-executable-symbolic")}
        self._exe_index = {}
        self._build_index()

    # ---- índice de .desktop ----
    def _build_index(self):
        index = {}
        for info in Gio.AppInfo.get_all():
            if not isinstance(info, Gio.DesktopAppInfo):
                continue
            app_id = (info.get_id() or "").removesuffix(".desktop")
            if not app_id:
                continue
            if not info.should_show():
                continue  # portales, ayudantes, etc. solo cuentan si vienen por cgroup
            names = set()
            try:
                ok, argv = GLib.shell_parse_argv(info.get_commandline() or "")
            except GLib.Error:
                ok, argv = False, []
            if ok:
                for tok in argv:
                    base = os.path.basename(tok)
                    if "=" in tok or base in ("env", "sh", "bash"):
                        continue
                    if not _is_generic(base):
                        names.add(base.lower())
                    break
            wm = info.get_startup_wm_class()
            if wm:
                names.add(wm.lower())
            names.add(app_id.rsplit(".", 1)[-1].lower())
            names.add(app_id.split("_", 1)[0].lower())  # brave_brave -> brave
            for n in names:
                if n and not _is_generic(n):
                    index.setdefault(n, app_id)
        self._exe_index = index

    def app(self, key):
        return self._apps[key]

    def has_desktop(self, key):
        return self._apps[key].icon is not None or _desktop(key) is not None

    def _register(self, app_id):
        if app_id in self._apps:
            return app_id
        info = _desktop(app_id)
        if info:
            icon = info.get_icon()
            self._apps[app_id] = AppInfo(app_id, info.get_display_name() or app_id,
                                         icon.to_string() if icon else None)
        else:
            self._apps[app_id] = AppInfo(app_id, app_id.rsplit(".", 1)[-1], None)
        return app_id

    # ---- resolución ----
    def from_cgroup(self, cgroup):
        leaf = cgroup.rsplit("/", 1)[-1]
        if leaf.startswith("app-") and leaf.endswith((".scope", ".service")):
            body = leaf[4:].rsplit(".", 1)[0].split("@", 1)[0]
            parts = body.split("-")
            if parts[0] in _LAUNCHERS and len(parts) >= 2:
                app_id = parts[1]
            else:
                app_id = parts[0]
            app_id = _unescape(app_id)
            return self._register(app_id) if app_id else None
        m = _SNAP_RE.match(leaf)
        if m:
            app_id = f"{m.group(1)}_{m.group(2)}"
            if _desktop(app_id):
                return self._register(app_id)
            return None
        for prefix, app_id in _TERMINAL_SCOPES.items():
            if leaf.startswith(prefix) and _desktop(app_id):
                return self._register(app_id)
        return None

    def from_exe(self, uid, exe, comm):
        if uid != self._uid:
            return None
        for name in (os.path.basename(exe) if exe else None, comm):
            if not name:
                continue
            name = name.lower()
            if _is_generic(name):
                return None
            app_id = self._exe_index.get(name)
            if app_id:
                return self._register(app_id)
        return None
