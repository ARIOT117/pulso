"""Gráficas y barras dibujadas con rutas de Gsk (renderizado por GPU).

Sparkline:
- Eje X por tiempo real: mientras el historial no cubre toda la ventana, la serie se estira a
  todo el ancho; cuando la cubre, cada muestra nueva desplaza la línea hacia la izquierda con un
  scroll continuo interpolado entre ticks.
- Escala Y fijada desde fuera (yMax o yMin–yMax), la misma que muestra la etiqueta; cuando
  cambia se interpola en 400 ms.
- Submuestreo por bloques + EMA ligera (sin el serrucho de 1 px, pero siguiendo los picos).
- Curva cúbica monótona (Fritsch-Carlson): sin sobrepicos por debajo de 0.
- Relleno en degradado vertical que nace en la propia curva y se desvanece en la base.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Graphene, Gsk, Gtk, Pango  # noqa: E402

SCROLL_MS = 1000     # = intervalo de muestreo: el scroll nunca se detiene entre ticks
SCALE_MS = 400
FADE_MS = 150
BAR_MS = 800


def rgba(color, alpha=None):
    c = Gdk.RGBA()
    c.parse(color)
    if alpha is not None:
        c.alpha = alpha
    return c


def _stop(offset, color):
    s = Gsk.ColorStop()
    s.offset = offset
    s.color = color
    return s


def cubic_bezier(x1, y1, x2, y2):
    """Función de easing equivalente a CSS cubic-bezier(x1, y1, x2, y2)."""
    def coord(t, a, b):
        return 3 * a * (1 - t) ** 2 * t + 3 * b * (1 - t) * t ** 2 + t ** 3

    def ease(x):
        if x <= 0 or x >= 1:
            return min(max(x, 0.0), 1.0)
        lo, hi, t = 0.0, 1.0, x
        for _ in range(20):          # bisección: robusta y suficiente para animaciones
            t = (lo + hi) / 2
            if coord(t, x1, x2) < x:
                lo = t
            else:
                hi = t
        return coord(t, y1, y2)
    return ease


BAR_EASE = cubic_bezier(0.3, 0.7, 0.2, 1.0)


def _monotone(builder, pts):
    """Curva cúbica monótona por `pts` (Fritsch-Carlson). Requiere move_to(pts[0]) previo."""
    n = len(pts)
    if n == 2:
        builder.line_to(*pts[1])
        return
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    d = []
    for i in range(n - 1):
        dx = xs[i + 1] - xs[i]
        d.append((ys[i + 1] - ys[i]) / dx if dx else 0.0)
    m = [d[0]] + [0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2 for i in range(1, n - 1)] + [d[-1]]
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / d[i], m[i + 1] / d[i]
        s = a * a + b * b
        if s > 9:
            t = 3 / s ** 0.5
            m[i], m[i + 1] = t * a * d[i], t * b * d[i]
    for i in range(n - 1):
        h = (xs[i + 1] - xs[i]) / 3
        builder.cubic_to(xs[i] + h, ys[i] + m[i] * h, xs[i + 1] - h, ys[i + 1] - m[i + 1] * h, xs[i + 1], ys[i + 1])


class Sparkline(Gtk.Widget):
    """Gráfica de una serie (o varias) sobre una ventana de tiempo.

    Antes de dibujar, las muestras se agrupan en `points` bloques alineados a tiempo absoluto
    (estables al desplazarse la ventana) y la media de cada bloque se suaviza con una EMA. Una
    sola serie limpia: línea, relleno en degradado y punto final con halo.

    La escala puede venir dada (`y_range`) o calcularse con `scale(valores_suavizados)` sobre
    exactamente la serie que se dibuja; `target_range` expone el rango resultante.
    """

    __gtype_name__ = "PulsoSparkline"

    def __init__(self, colors, window, points=150, height=48, min_height=None, fill_alpha=0.22,
                 line_width=2.0, grid=False, end_dot=False, band=False, ema=0.5, label_fmt=None):
        super().__init__()
        self._colors = list(colors)
        self.window = window
        self.points = points
        self.band = band
        self.ema = ema
        self._times = []          # marcas de tiempo crudas (para span y scroll)
        self._buckets = []        # por serie: [(t, media_suavizada, mín, máx)]
        self.fill_alpha = fill_alpha
        self.line_width = line_width
        self.grid = grid
        self.end_dot = end_dot
        self._height = height
        self._min_height = min_height or height
        self.label_fmt = label_fmt   # si se da, las guías llevan etiqueta (mín, ½, máx)
        self._range = self._range_from = self._range_to = None   # (lo, hi)
        self._scroll = 0.0        # 1 → recién llegó una muestra; 0 → en su sitio
        self._step = 0.0          # segundos que avanzó la última muestra
        self.set_hexpand(True)
        self.set_overflow(Gtk.Overflow.HIDDEN)   # lo que pase de yMax se recorta

        self._scroll_anim = Adw.TimedAnimation.new(self, 1.0, 0.0, SCROLL_MS,
                                                   Adw.CallbackAnimationTarget.new(self._on_scroll))
        self._scroll_anim.set_easing(Adw.Easing.LINEAR)
        self._scale_anim = Adw.TimedAnimation.new(self, 0.0, 1.0, SCALE_MS,
                                                  Adw.CallbackAnimationTarget.new(self._on_scale))
        self._scale_anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._fade = Adw.TimedAnimation.new(self, 0.0, 1.0, FADE_MS,
                                            Adw.PropertyAnimationTarget.new(self, "opacity"))
        self._fade.set_easing(Adw.Easing.EASE_OUT_CUBIC)

    # ---- animaciones ----
    def _on_scroll(self, v):
        self._scroll = v
        self.queue_draw()

    def _on_scale(self, v):
        (a0, b0), (a1, b1) = self._range_from, self._range_to
        self._range = (a0 + (a1 - a0) * v, b0 + (b1 - b0) * v)
        self.queue_draw()

    # ---- datos ----
    def _bucketize(self, times, values):
        size = self.window / self.points
        groups = {}
        for t, v in zip(times, values):
            groups.setdefault(int(t // size), []).append((t, v or 0.0))
        out, smooth = [], None
        keys = sorted(groups)
        for i, k in enumerate(keys):
            g = groups[k]
            vals = [v for _, v in g]
            mean = sum(vals) / len(vals)
            smooth = mean if smooth is None else smooth + self.ema * (mean - smooth)
            # el último bloque (aún incompleto) se ancla en la muestra más reciente
            t = g[-1][0] if i == len(keys) - 1 else sum(t for t, _ in g) / len(g)
            out.append((t, smooth, min(vals), max(vals)))
        return out

    @property
    def target_range(self):
        return self._range_to

    def set_data(self, times, series, y_range=None, animate=True, scale=None):
        """`times`: marcas de tiempo; `series`: listas alineadas con `times`;
        `y_range`: yMax o (yMin, yMax); o bien `scale(valores_suavizados)` que lo devuelve."""
        times = list(times)
        if times:
            cut = times[-1] - self.window - 0.5
            first = next((i for i, t in enumerate(times) if t >= cut), 0)
        else:
            first = 0
        prev_last = self._times[-1] if self._times else None
        self._times = times[first:]
        self._buckets = [self._bucketize(self._times, list(s)[first:]) for s in series]
        if y_range is None:
            y_range = scale([b[1] for bs in self._buckets for b in bs])
        rng = tuple(y_range) if isinstance(y_range, (tuple, list)) else (0.0, float(y_range))
        live = animate and self.get_mapped()

        if self._range is None or not live:
            self._scale_anim.reset()
            self._range = self._range_from = self._range_to = rng
        elif rng != self._range_to:
            self._range_from, self._range_to = self._range, rng
            self._scale_anim.reset()
            self._scale_anim.play()

        if live and prev_last is not None and self._times and self._times[-1] > prev_last:
            self._step = self._times[-1] - prev_last
            self._scroll_anim.reset()
            self._scroll_anim.play()
        else:
            self._scroll_anim.reset()
            self._scroll = 0.0
        self.queue_draw()

    def crossfade(self):
        """Fundido de entrada de 150 ms (al cambiar de métrica no se interpola la línea)."""
        self.set_opacity(0.0)
        self._fade.reset()
        self._fade.play()

    def set_colors(self, *colors):
        self._colors = list(colors)
        self.queue_draw()

    @property
    def span(self):
        """Segundos de historial que cubre ahora el eje X."""
        return (self._times[-1] - self._times[0]) if len(self._times) > 1 else 0.0

    # ---- dibujo ----
    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return self._min_height, self._height, -1, -1   # puede encogerse antes que el contenido
        return 40, 120, -1, -1

    def _guides(self, snapshot, w, h, y_top):
        """3 guías al 4 % alineadas con la escala (base, ½, máx) y su etiqueta a la izquierda."""
        lo, hi = self._range
        base = h - 1.5
        for frac in (0.0, 0.5, 1.0):
            y = round(base - (base - y_top) * frac)
            snapshot.append_color(rgba("#ffffff", 0.04), Graphene.Rect().init(0, y, w, 1))
            if self.label_fmt:
                layout = self.create_pango_layout(self.label_fmt(lo + (hi - lo) * frac))
                layout.set_font_description(Pango.FontDescription.from_string("Adwaita Mono 7.5"))
                _lw, lh = layout.get_pixel_size()
                snapshot.save()
                snapshot.translate(Graphene.Point().init(2, max(y - lh - 1, 0) if frac else y - lh - 2))
                snapshot.append_layout(layout, rgba("#6f6e69"))
                snapshot.restore()

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0 or not self._range:
            return
        y_top = 5 + (5 if self.end_dot else 0) + (12 if self.label_fmt else 0)
        if self.grid:
            self._guides(snapshot, w, h, y_top)
        if len(self._times) < 2:
            return

        right = w - (7 if self.end_dot else 0)   # sitio para el punto final
        t0, t1 = self._times[0], self._times[-1]
        span = t1 - t0
        if span >= self.window - 1.0:
            # ventana completa: eje fijo en «hace N … ahora» y scroll continuo
            px_per_s = right / self.window
            shift = self._scroll * self._step * px_per_s

            def xof(t):
                return right - (t1 - t) * px_per_s + shift
        else:
            # aún sin historial completo: se estira a todo el ancho
            def xof(t):
                return (t - t0) / span * right

        lo, hi = self._range
        rng = (hi - lo) or 1.0
        usable = h - y_top - 1.5

        def yof(v):
            return h - 1.5 - usable * (v - lo) / rng

        stroke = Gsk.Stroke.new(self.line_width)
        stroke.set_line_join(Gsk.LineJoin.ROUND)
        stroke.set_line_cap(Gsk.LineCap.ROUND)

        for color, buckets in zip(self._colors, self._buckets):
            if len(buckets) < 2:
                continue
            xs = [xof(b[0]) for b in buckets]
            pts = [(x, yof(b[1])) for x, b in zip(xs, buckets)]

            if self.band:
                band = Gsk.PathBuilder.new()
                band.move_to(xs[0], yof(buckets[0][3]))
                for x, b in zip(xs[1:], buckets[1:]):
                    band.line_to(x, yof(b[3]))
                for x, b in zip(reversed(xs), reversed(buckets)):
                    band.line_to(x, yof(b[2]))
                band.close()
                snapshot.append_fill(band.to_path(), Gsk.FillRule.WINDING, rgba(color, 0.08))

            if self.fill_alpha:
                # el relleno no sube por encima de yMax: lo recortado es solo la línea
                fpts = [(x, max(y, y_top)) for x, y in pts]
                area = Gsk.PathBuilder.new()
                area.move_to(*fpts[0])
                _monotone(area, fpts)
                area.line_to(fpts[-1][0], h)
                area.line_to(fpts[0][0], h)
                area.close()
                crest = min(y for _, y in fpts)
                snapshot.push_fill(area.to_path(), Gsk.FillRule.WINDING)
                snapshot.append_linear_gradient(
                    Graphene.Rect().init(0, 0, w, h), Graphene.Point().init(0, crest), Graphene.Point().init(0, h),
                    [_stop(0.0, rgba(color, self.fill_alpha)), _stop(1.0, rgba(color, 0.0))])
                snapshot.pop()

            line = Gsk.PathBuilder.new()
            line.move_to(*pts[0])
            _monotone(line, pts)
            snapshot.append_stroke(line.to_path(), stroke, rgba(color, 1.0))

            if self.end_dot:
                x, y = pts[-1]
                y = min(max(y, 6), h - 6)
                halo = Gsk.PathBuilder.new()
                halo.add_circle(Graphene.Point().init(x, y), 10)
                snapshot.append_fill(halo.to_path(), Gsk.FillRule.WINDING, rgba(color, 0.18))
                dot = Gsk.PathBuilder.new()
                dot.add_circle(Graphene.Point().init(x, y), 5)
                snapshot.append_fill(dot.to_path(), Gsk.FillRule.WINDING, rgba(color, 1.0))


class SegmentBar(Gtk.Widget):
    """Segmentos redondeados de 6 px separados 4 px (mínimo 6 px cada uno), con anchos animados."""

    __gtype_name__ = "PulsoSegmentBar"

    def __init__(self, height=6, gap=4):
        super().__init__()
        self._colors = []
        self._from = self._to = self._cur = []
        self._height = height
        self._gap = gap
        self.set_hexpand(True)
        self._anim = Adw.TimedAnimation.new(self, 0.0, 1.0, BAR_MS, Adw.CallbackAnimationTarget.new(self._on_anim))
        self._anim.set_easing(Adw.Easing.LINEAR)

    def _on_anim(self, t):
        e = BAR_EASE(t)
        self._cur = [a + (b - a) * e for a, b in zip(self._from, self._to)]
        self.queue_draw()

    def set_segments(self, segs, animate=True):
        total = sum(v for v, _ in segs) or 1
        fracs = [v / total for v, _ in segs]
        self._colors = [c for _, c in segs]
        if animate and len(fracs) == len(self._cur) and self.get_mapped():
            if fracs != self._to:
                self._from, self._to = list(self._cur), fracs
                self._anim.reset()
                self._anim.play()
        else:
            self._anim.reset()
            self._from = self._to = self._cur = fracs
            self.queue_draw()

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return self._height, self._height, -1, -1
        return 60, 200, -1, -1

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        visible = [(f, c) for f, c in zip(self._cur, self._colors) if f > 0]
        if not visible:
            return
        avail = w - self._gap * (len(visible) - 1)
        widths = [max(6.0, avail * f) for f, _ in visible]
        k = avail / sum(widths)
        x = 0.0
        for (_f, color), sw in zip(visible, widths):
            sw *= k
            rr = Gsk.RoundedRect()
            rr.init_from_rect(Graphene.Rect().init(x, 0, sw, h), h / 2)
            snapshot.push_rounded_clip(rr)
            snapshot.append_color(rgba(color), Graphene.Rect().init(x, 0, sw, h))
            snapshot.pop()
            x += sw + self._gap


class Meter(Gtk.Widget):
    """Barra en píldora con transición de 800 ms cubic-bezier(.3,.7,.2,1) al cambiar (0..1)."""

    __gtype_name__ = "PulsoMeter"

    def __init__(self, color, thickness=4, length=48, vertical=False, trough_alpha=0.04, alpha=1.0):
        super().__init__()
        self._color = rgba(color, alpha)
        self._trough = rgba("#ffffff", trough_alpha)
        self._thickness = thickness
        self._length = length
        self._vertical = vertical
        self._value = self._from = self._to = 0.0
        self._anim = Adw.TimedAnimation.new(self, 0.0, 1.0, BAR_MS, Adw.CallbackAnimationTarget.new(self._on_anim))
        self._anim.set_easing(Adw.Easing.LINEAR)
        if vertical:
            self.set_vexpand(True)
            self.set_halign(Gtk.Align.CENTER)
        else:
            self.set_valign(Gtk.Align.CENTER)

    def _on_anim(self, t):
        self._value = self._from + (self._to - self._from) * BAR_EASE(t)
        self.queue_draw()

    def set_color(self, color, alpha=1.0):
        self._color = rgba(color, alpha)
        self.queue_draw()

    def set_value(self, value, animate=True):
        value = max(0.0, min(float(value), 1.0))
        if abs(value - self._to) < 1e-4:
            return
        self._from, self._to = self._value, value
        if animate and self.get_mapped():
            self._anim.reset()
            self._anim.play()
        else:
            self._anim.reset()
            self._value = value
            self.queue_draw()

    def do_measure(self, orientation, for_size):
        along = (orientation == Gtk.Orientation.VERTICAL) == self._vertical
        if along:
            return self._length, self._length, -1, -1
        return self._thickness, self._thickness, -1, -1

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        r = (w if self._vertical else h) / 2
        rr = Gsk.RoundedRect()
        rr.init_from_rect(Graphene.Rect().init(0, 0, w, h), r)
        snapshot.push_rounded_clip(rr)
        snapshot.append_color(self._trough, Graphene.Rect().init(0, 0, w, h))
        if self._value > 0:
            if self._vertical:
                fh = max(h * self._value, w)
                fill = Graphene.Rect().init(0, h - fh, w, fh)
            else:
                fw = max(w * self._value, h)
                fill = Graphene.Rect().init(0, 0, fw, h)
            fr = Gsk.RoundedRect()
            fr.init_from_rect(fill, r)
            snapshot.push_rounded_clip(fr)
            snapshot.append_color(self._color, fill)
            snapshot.pop()
        snapshot.pop()
