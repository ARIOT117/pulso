"""Paleta del diseño «papel oscuro» (convertida de los oklch del mockup)."""

BG = "#151514"
FG = "#ecebe7"
MUTED = "#8b8a85"
FAINT = "#6f6e69"

# Color de cada métrica: oklch(0.72 0.13 h)
COLORS = {
    "cpu": "#60aaf3",   # h 250
    "mem": "#be8ce1",   # h 310
    "gpu": "#57bc80",   # h 155
    "disk": "#d49838",  # h 75
    "net": "#00bcc5",   # h 200
}
# Tonos más oscuros de cada métrica para segmentos y series secundarias
SHADES = {
    "cpu": ("#60aaf3", "#3f86c9", "#1666aa"),
    "mem": ("#be8ce1", "#8959aa", "#5d2e7b"),
    "gpu": ("#57bc80", "#2c965d", "#006933"),
    "disk": ("#d49838", "#a86f0c", "#8d5500"),
    "net": ("#00bcc5", "#009aa2", "#007780"),
}
FREE = "rgba(255,255,255,0.08)"
LOGO_GREEN = "#55c483"
AMBER = "#f5b75b"
