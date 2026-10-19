"""Preferencias sencillas guardadas en ~/.config/pulso/settings.json."""

import json
import os

_PATH = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
                     "pulso", "settings.json")

cpu_per_core = False   # False: % del total del sistema (por defecto); True: 100 % = un núcleo
last_metric = "cpu"    # última pestaña abierta en el detalle de métricas


def load():
    global cpu_per_core, last_metric
    try:
        with open(_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    # «cpu_mode» sustituye al antiguo «cpu_per_core»: el valor por defecto vuelve a ser % del total
    cpu_per_core = data.get("cpu_mode", "total") == "core"
    if data.get("last_metric") in ("cpu", "mem", "gpu", "disk", "net"):
        last_metric = data["last_metric"]


def save():
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        with open(_PATH, "w") as f:
            json.dump({"cpu_mode": "core" if cpu_per_core else "total", "last_metric": last_metric}, f)
    except OSError:
        pass
