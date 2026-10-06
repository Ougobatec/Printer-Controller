"""Graphiques dessinés sur un Canvas Tk (aucune dépendance externe)."""

import math
import tkinter as tk

from theme import C, colormap


def nice_ticks(lo, hi, target=5):
    """Graduations « rondes » entre lo et hi. Retourne (liste, pas)."""
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / max(1, target)
    mag = 10 ** math.floor(math.log10(raw))
    step = mag * 10
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            step = m * mag
            break
    value = math.ceil(lo / step - 1e-9) * step
    ticks = []
    while value <= hi + step * 1e-6 and len(ticks) < 60:
        ticks.append(value)
        value += step
    return ticks, step


def format_tick(value, step):
    decimals = 0
    while decimals < 8 and abs(round(step, decimals) - step) > 1e-9 * max(1.0, abs(step)):
        decimals += 1
    text = f"{value:.{decimals}f}"
    return "0" if float(text) == 0 else text


def _finite(v):
    return v is not None and v == v and abs(v) != math.inf


class Plot2D(tk.Canvas):
    """Courbe valeur = f(x). Sobre, avec curseur de lecture au survol."""

    def __init__(self, parent, fonts, compact=False, bg=None):
        super().__init__(parent, bg=bg or C["surface"], highlightthickness=0, bd=0,
                         height=110 if compact else 220)
        self.fonts = fonts
        self.compact = compact
        self._lines = []
        self._dots = []
        self._xlabel = ""
        self._unit = ""
        self._x_range = None
        self._empty = ""
        self._highlight = None
        self._hits = []
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda _e: self.delete("hover"))

    def show(self, lines=(), dots=(), xlabel="", unit="", x_range=None, empty="", highlight=None):
        """lines : [(xs, ys, couleur)]  dots : [(xs, ys, couleur, rayon)]."""
        self._lines = list(lines)
        self._dots = list(dots)
        self._xlabel = xlabel
        self._unit = unit
        self._x_range = x_range
        self._empty = empty
        self._highlight = highlight
        self.redraw()

    def redraw(self):
        self.delete("all")
        self._hits = []
        w, h = self.winfo_width(), self.winfo_height()
        if w < 60 or h < 40:
            return
        small, mono = self.fonts["small"], self.fonts["mono"]
        left, right, top, bottom = (40, 8, 8, 16) if self.compact else (62, 18, 14, 34)
        pw, ph = w - left - right, h - top - bottom

        xs_all, ys_all = [], []
        for xs, ys, *_ in self._lines + self._dots:
            for x, y in zip(xs, ys):
                if _finite(x) and _finite(y):
                    xs_all.append(x)
                    ys_all.append(y)
        if not xs_all:
            self.create_text(w / 2, h / 2, text=self._empty, fill=C["faint"], font=small,
                             justify="center", width=w - 40)
            return

        if self._x_range:
            x0, x1 = self._x_range
        else:
            x0, x1 = min(xs_all), max(xs_all)
        if x1 - x0 < 1e-12:
            x0, x1 = x0 - 0.5, x1 + 0.5
        y0, y1 = min(ys_all), max(ys_all)
        if y1 - y0 < 1e-12:
            pad = max(abs(y0) * 0.05, 0.5)
            y0, y1 = y0 - pad, y1 + pad
        else:
            pad = (y1 - y0) * 0.1
            y0, y1 = y0 - pad, y1 + pad

        def px(x):
            return left + (x - x0) / (x1 - x0) * pw

        def py(y):
            return top + (1.0 - (y - y0) / (y1 - y0)) * ph

        yticks, ystep = nice_ticks(y0, y1, 3 if self.compact else 5)
        xticks, xstep = nice_ticks(x0, x1, 3 if self.compact else 6)
        for t in yticks:
            y = py(t)
            self.create_line(left, y, w - right, y, fill=C["grid"])
            self.create_text(left - 6, y, text=format_tick(t, ystep), anchor="e",
                             fill=C["muted"], font=mono)
        if not self.compact:
            for t in xticks:
                x = px(t)
                self.create_line(x, top, x, top + ph, fill=C["grid"])
                self.create_text(x, top + ph + 6, text=format_tick(t, xstep), anchor="n",
                                 fill=C["muted"], font=mono)
            self.create_text(left + pw, h - 2, text=self._xlabel, anchor="se",
                             fill=C["muted"], font=small)
            if self._unit:
                self.create_text(4, 2, text=self._unit, anchor="nw", fill=C["muted"], font=small)
        self.create_rectangle(left, top, left + pw, top + ph, outline=C["line"])

        budget = max(100, pw * 2)
        for xs, ys, color in self._lines:
            pts = [(x, y) for x, y in zip(xs, ys) if _finite(x) and _finite(y)]
            if len(pts) < 2:
                continue
            stride = max(1, len(pts) // budget)
            sel = pts[::stride]
            if sel[-1] != pts[-1]:
                sel.append(pts[-1])
            coords = []
            for x, y in sel:
                coords += [px(x), py(y)]
                self._hits.append((px(x), py(y), x, y))
            self.create_line(*coords, fill=color, width=1.5 if self.compact else 2,
                             capstyle="round", joinstyle="round")
        for xs, ys, color, radius in self._dots:
            pts = [(x, y) for x, y in zip(xs, ys) if _finite(x) and _finite(y)]
            stride = max(1, len(pts) // 3000)
            for x, y in pts[::stride]:
                cx, cy = px(x), py(y)
                self.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                                 fill=color, outline=C["surface"])
                self._hits.append((cx, cy, x, y))
        if self._highlight and all(_finite(v) for v in self._highlight):
            cx, cy = px(self._highlight[0]), py(self._highlight[1])
            if left <= cx <= left + pw and top <= cy <= top + ph:
                self.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, outline=C["accent"], width=2)

    def _hover(self, event):
        self.delete("hover")
        if self.compact or not self._hits:
            return
        best = min(self._hits, key=lambda hit: abs(hit[0] - event.x))
        if abs(best[0] - event.x) > 40:
            return
        px_, py_, x, y = best
        h = self.winfo_height()
        self.create_line(px_, 8, px_, h - 34, fill=C["edge"], dash=(3, 3), tags="hover")
        self.create_oval(px_ - 4, py_ - 4, px_ + 4, py_ + 4, fill=C["accent"], outline="white",
                         width=2, tags="hover")
        text = f"{x:.5g}  →  {y:.6g} {self._unit}".strip()
        w = self.winfo_width()
        anchor, tx = ("ne", px_ - 8) if px_ > w / 2 else ("nw", px_ + 8)
        label = self.create_text(tx, 14, text=text, anchor=anchor, fill=C["text"],
                                 font=self.fonts["mono"], tags="hover")
        x0, y0, x1, y1 = self.bbox(label)
        box = self.create_rectangle(x0 - 5, y0 - 2, x1 + 5, y1 + 2, fill=C["surface"],
                                    outline=C["line"], tags="hover")
        self.tag_lower(box, label)


class Scatter3D(tk.Canvas):
    """Nuage de points 3D coloré par valeur (rotation, déplacement, zoom)."""

    PRESETS = {"iso": (0.68 + math.pi, 0.52), "dessus": (math.pi, 1.5), "face": (math.pi, 0.0)}

    def __init__(self, parent, fonts, bg=None):
        super().__init__(parent, bg=bg or C["surface"], highlightthickness=0, bd=0, height=240)
        self.fonts = fonts
        self.captures_wheel = True
        self._points = []
        self._unit = ""
        self._empty = ""
        self._drag = None
        self.set_view("iso", redraw=False)
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<ButtonPress-1>", lambda e: self._press(e, "rotate"))
        self.bind("<B1-Motion>", self._move)
        self.bind("<Shift-ButtonPress-1>", lambda e: self._press(e, "pan"))
        self.bind("<Shift-B1-Motion>", self._move)
        for button in (2, 3):
            self.bind(f"<ButtonPress-{button}>", lambda e: self._press(e, "pan"))
            self.bind(f"<B{button}-Motion>", self._move)
        self.bind("<MouseWheel>", lambda e: self._zoom(1.1 if e.delta > 0 else 0.9))
        self.bind("<Button-4>", lambda _e: self._zoom(1.1))
        self.bind("<Button-5>", lambda _e: self._zoom(0.9))

    def set_view(self, name, redraw=True):
        self.yaw, self.pitch = self.PRESETS.get(name, self.PRESETS["iso"])
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        if redraw:
            self.redraw()

    def show(self, points, unit="", empty=""):
        """points : [(x, y, z, valeur)]"""
        self._points = points
        self._unit = unit
        self._empty = empty
        self.redraw()

    def _press(self, event, mode):
        self._drag = (event.x, event.y, self.yaw, self.pitch, self.pan_x, self.pan_y, mode)

    def _move(self, event):
        if not self._drag:
            return
        x0, y0, yaw, pitch, panx, pany, mode = self._drag
        if mode == "rotate":
            self.yaw = yaw - (event.x - x0) * 0.012
            self.pitch = max(-1.55, min(1.55, pitch + (event.y - y0) * 0.012))
        else:
            self.pan_x = panx + (event.x - x0)
            self.pan_y = pany + (event.y - y0)
        self.redraw()

    def _zoom(self, factor):
        self.zoom = max(0.25, min(6.0, self.zoom * factor))
        self.redraw()

    def redraw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 120 or h < 80:
            return
        small, mono = self.fonts["small"], self.fonts["mono"]
        pts = [p for p in self._points if all(_finite(v) for v in p)]
        if not pts:
            self.create_text(w / 2, h / 2, text=self._empty, fill=C["faint"], font=small,
                             justify="center", width=w - 40)
            return
        stride = max(1, len(pts) // 4000)
        pts = pts[::stride]

        lows = [min(p[i] for p in pts) for i in range(3)]
        highs = [max(p[i] for p in pts) for i in range(3)]
        biggest = max(highs[i] - lows[i] for i in range(3))
        minimum = max(biggest * 0.05, 1.0)
        for i in range(3):
            if highs[i] - lows[i] < minimum:
                mid = (highs[i] + lows[i]) / 2
                lows[i], highs[i] = mid - minimum / 2, mid + minimum / 2
        centre = [(lows[i] + highs[i]) / 2 for i in range(3)]
        radius = 0.5 * math.sqrt(sum((highs[i] - lows[i]) ** 2 for i in range(3)))
        area_w = w - 74
        scale = (min(area_w, h) / 2 - 24) / radius * 1.1 * self.zoom
        cyaw, syaw = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)

        def project(x, y, z):
            x, y, z = x - centre[0], y - centre[1], z - centre[2]
            gx, gy = y, x            # même convention que le schéma de la machine
            sx = gx * cyaw - gy * syaw
            depth = gx * syaw + gy * cyaw
            up = z * cp - depth * sp
            return area_w / 2 + sx * scale + self.pan_x, h / 2 - up * scale + self.pan_y, depth

        corners = {(i, j, k): project(highs[0] if i else lows[0], highs[1] if j else lows[1],
                                      highs[2] if k else lows[2])
                   for i in (0, 1) for j in (0, 1) for k in (0, 1)}
        for (i, j, k), a in corners.items():
            for di, dj, dk in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
                b = corners.get((i + di, j + dj, k + dk))
                if b:
                    self.create_line(a[0], a[1], b[0], b[1], fill=C["edge"] if k == 0 and dk == 0
                                     else C["grid"])
        origin = corners[(0, 0, 0)]
        for axis, end, colour in (("X", corners[(1, 0, 0)], C["axis_x"]),
                                  ("Y", corners[(0, 1, 0)], C["axis_y"]),
                                  ("Z", corners[(0, 0, 1)], C["axis_z"])):
            self.create_line(origin[0], origin[1], end[0], end[1], fill=colour, width=2)
            self.create_text(end[0] + 6, end[1], text=f"{axis} {highs['XYZ'.index(axis)]:.4g}",
                             anchor="w", fill=colour, font=small)

        values = [p[3] for p in pts]
        vmin, vmax = min(values), max(values)
        span = vmax - vmin if vmax - vmin > 1e-12 else 1.0
        projected = sorted(((*project(p[0], p[1], p[2]), p[3]) for p in pts), key=lambda q: -q[2])
        r = 3 if len(projected) > 400 else 4.5
        for sx, sy, _depth, v in projected:
            self.create_oval(sx - r, sy - r, sx + r, sy + r, fill=colormap((v - vmin) / span),
                             outline="")

        # Échelle de couleurs
        bx, top, bottom = w - 52, 24, h - 30
        steps = 40
        for i in range(steps):
            y0 = top + (bottom - top) * i / steps
            y1 = top + (bottom - top) * (i + 1) / steps
            self.create_rectangle(bx, y0, bx + 12, y1 + 1, fill=colormap(1 - (i + 0.5) / steps),
                                  outline="")
        self.create_text(bx + 18, top, text=f"{vmax:.5g}", anchor="w", fill=C["muted"], font=mono)
        self.create_text(bx + 18, bottom, text=f"{vmin:.5g}", anchor="w", fill=C["muted"], font=mono)
        self.create_text(bx, top - 12, text=self._unit, anchor="w", fill=C["muted"], font=small)
        self.create_text(8, h - 8, anchor="sw", fill=C["faint"], font=small,
                         text="Glisser : pivoter · Maj + glisser : déplacer · Molette : zoom")
