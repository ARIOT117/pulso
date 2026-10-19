"""Barra inferior: app seleccionada, botón de detalles y «Terminar aplicación» con más acciones."""

import os
import signal
import subprocess

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk, Pango  # noqa: E402

from ..collector.apps import SYSTEM_KEY
from ..format import fmt_bytes

# acción -> (señal, texto del toast)
ACTIONS = {
    "term": (signal.SIGTERM, "terminada"),
    "kill": (signal.SIGKILL, "forzada a cerrar"),
    "stop": (signal.SIGSTOP, "detenida"),
    "cont": (signal.SIGCONT, "reanudada"),
}


def _app_unit(pid):
    """Unidad systemd de usuario propia de la app (app-*.scope/.service o snap.*), o None."""
    try:
        with open(f"/proc/{pid}/cgroup") as f:
            path = f.read().strip().split("::", 1)[-1]
    except OSError:
        return None
    if f"/user@{os.getuid()}.service/" not in path:
        return None
    leaf = path.rsplit("/", 1)[-1]
    if leaf.startswith(("app-", "snap.")) and leaf.endswith((".scope", ".service")):
        return leaf
    return None


def send_signal(sample, sig, self_pid):
    """Envía `sig` a los procesos propios de la app (nunca a Pulso).

    Si el kernel lo deniega (p. ej. AppArmor protege una app snap), se pide a systemd que
    envíe la señal a la unidad de la app, que sí puede hacerlo. Devuelve (enviados, bloqueados).
    """
    me, sent, denied = os.getuid(), 0, []
    for p in sample.procs:
        if p.uid != me or p.pid == self_pid:
            continue
        try:
            os.kill(p.pid, sig)
            sent += 1
        except PermissionError:
            denied.append(p.pid)
        except ProcessLookupError:
            pass
    units = {u for u in map(_app_unit, denied) if u}
    for unit in units:
        r = subprocess.run(["systemctl", "--user", "kill", f"--signal={int(sig)}", "--kill-whom=all", unit],
                           capture_output=True, timeout=5)
        if r.returncode == 0:
            sent += sum(1 for pid in denied if _app_unit(pid) in (unit, None))
            denied = [pid for pid in denied if _app_unit(pid) not in (unit, None)]
    return sent, len(denied)


class ActionBar(Gtk.Box):
    __gtype_name__ = "PulsoActionBar"
    __gsignals__ = {
        "action": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "details": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self):
        super().__init__(spacing=12, css_classes=["pulso-actionbar"])

        self.sel_box = Gtk.Box(spacing=10, visible=False)
        self.sel_icon = Gtk.Image(pixel_size=22)
        self.sel_name = Gtk.Label(css_classes=["sel-name"], ellipsize=Pango.EllipsizeMode.END)
        self.sel_info = Gtk.Label(css_classes=["faint", "numeric"])
        for w in (self.sel_icon, self.sel_name, self.sel_info):
            self.sel_box.append(w)
        self.append(self.sel_box)
        self.hint = Gtk.Label(label="Selecciona una aplicación · toca una métrica para ver el detalle",
                              css_classes=["faint", "hint"], ellipsize=Pango.EllipsizeMode.END)
        self.append(self.hint)

        right = Gtk.Box(spacing=10)
        self.info_btn = Gtk.Button(icon_name="help-about-symbolic", css_classes=["info-btn", "circular"],
                                   tooltip_text="Detalles de la aplicación", valign=Gtk.Align.CENTER)
        self.info_btn.connect("clicked", lambda *_: self.emit("details"))
        right.append(self.info_btn)

        split = Gtk.Box(css_classes=["term-split"], valign=Gtk.Align.CENTER)
        self.term_btn = Gtk.Button(label="Terminar aplicación", css_classes=["term-main"])
        self.term_btn.connect("clicked", lambda *_: self.emit("action", "term"))
        split.append(self.term_btn)

        pop = Gtk.Popover(position=Gtk.PositionType.TOP, has_arrow=False, css_classes=["actions-menu"])
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for key, text, hint in (("kill", "Matar aplicación", "SIGKILL"),
                                ("stop", "Detener aplicación", "SIGSTOP"),
                                ("cont", "Reanudar aplicación", "SIGCONT")):
            b = Gtk.Button(css_classes=["menu-item"])
            row = Gtk.Box(spacing=16)
            row.append(Gtk.Label(label=text, xalign=0, hexpand=True))
            row.append(Gtk.Label(label=hint, css_classes=["mono-hint"]))
            b.set_child(row)
            b.connect("clicked", lambda _b, k=key: (pop.popdown(), self.emit("action", k)))
            menu.append(b)
        pop.set_child(menu)
        self.more_btn = Gtk.MenuButton(popover=pop, icon_name="pan-down-symbolic",
                                       css_classes=["term-more"], tooltip_text="Más acciones")
        split.append(self.more_btn)
        self.split = split
        right.append(split)
        # sin selección no hay acciones: se ocultan con un fundido en lugar de verse apagadas
        self.revealer = Gtk.Revealer(child=right, transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                     transition_duration=200, halign=Gtk.Align.END, hexpand=True)
        self.append(self.revealer)
        self.set_selection(None, None)

    def set_selection(self, sample, snap):
        has = sample is not None
        self.revealer.set_reveal_child(has)
        self.sel_box.set_visible(has)
        self.hint.set_visible(not has)
        actionable = has and sample.key != SYSTEM_KEY
        for w in (self.info_btn,):
            w.set_sensitive(has)
        self.split.set_sensitive(actionable)
        self.split.set_tooltip_text(None if actionable or not has else
                                    "Los procesos del sistema no se pueden terminar desde aquí")
        if has:
            from .app_list import app_icon
            self.sel_icon.set_from_gicon(app_icon(sample.icon))
            self.sel_name.set_label(sample.name)
            n = len(sample.procs)
            mem = ("≥ " if sample.pss_unknown else "") + fmt_bytes(sample.pss)
            state = " · detenida" if sample.paused else ""
            self.sel_info.set_label(f"{n} proceso{'s' if n != 1 else ''} · {mem}{state}")


def confirm_kill(parent, name, on_yes):
    dialog = Adw.AlertDialog(
        heading=f"¿Matar {name}?",
        body="Sus procesos se cerrarán al instante (SIGKILL) y se perderá lo que no esté guardado.",
    )
    dialog.add_response("cancel", "Cancelar")
    dialog.add_response("ok", "Matar")
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, r: r == "ok" and on_yes())
    dialog.present(parent)
