"""3D view of the printer: work volume, program path, target point, measurements, head position."""

import math
import tkinter as tk
from tkinter import ttk

from ..i18n import t
from .theme import C, colormap
from .widgets import Tooltip

AXES = ("X", "Y", "Z")


class MachineView(tk.Canvas):
    """Draws what `set_scene()` is given; call `request_draw()` after any change."""

    def __init__(self, parent, fonts, height=340):
        super().__init__(parent, bg=C["surface"], highlightthickness=0, height=height, width=300)
        self.f = fonts
        self.captures_wheel = True
        self.scene = {"limits": {a: (0.0, 200.0) for a in AXES}, "points": [], "selected": set(), "current": None,
                      "done": set(), "target": None, "machine": None, "measures": []}
        self.show_measures = False
        self._job = None
        self._drag = None
        self.reset_camera(redraw=False)
        self.bind("<Configure>", lambda _e: self.request_draw())
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
        reset = ttk.Button(self, text="⟲", width=3, style="Ghost.TButton", command=self.reset_camera)
        reset.place(relx=1.0, x=-8, y=8, anchor="ne")
        Tooltip(reset, t("view.reset"))
        self.chip = ttk.Button(self, text=t("view.measures"), style="Chip.TButton", command=self._toggle_measures)
        self.chip.place(relx=1.0, x=-8, y=44, anchor="ne")
        Tooltip(self.chip, t("view.measures.tip"))

    # --- data ---------------------------------------------------------------------------------
    def set_scene(self, **data):
        self.scene.update(data)
        self.request_draw()

    def _toggle_measures(self):
        self.show_measures = not self.show_measures
        self.chip.configure(style="ChipOn.TButton" if self.show_measures else "Chip.TButton")
        self.request_draw()

    # --- camera ---------------------------------------------------------------------------------
    def reset_camera(self, redraw=True):
        self.yaw, self.pitch, self.zoom, self.pan_x, self.pan_y = 0.68 + math.pi, 0.52, 1.0, 0.0, 0.0
        if redraw:
            self.request_draw()

    def _press(self, event, mode):
        self._drag = (event.x, event.y, self.yaw, self.pitch, self.pan_x, self.pan_y, mode)

    def _move(self, event):
        if not self._drag:
            return
        x0, y0, yaw, pitch, pan_x, pan_y, mode = self._drag
        if mode == "rotate":
            self.yaw = yaw - (event.x - x0) * 0.012
            self.pitch = max(-1.35, min(1.35, pitch + (event.y - y0) * 0.012))
        else:
            self.pan_x, self.pan_y = pan_x + event.x - x0, pan_y + event.y - y0
        self.request_draw()

    def _zoom(self, factor):
        self.zoom = max(0.25, min(5.0, self.zoom * factor))
        self.request_draw()

    def _project(self, x, y, z, width, height, bounds):
        xmin, xmax, ymin, ymax, zmin, zmax = bounds
        x, y, z = x - (xmin + xmax) / 2, y - (ymin + ymax) / 2, z - (zmin + zmax) / 2
        cyaw, syaw, cp, sp = math.cos(self.yaw), math.sin(self.yaw), math.cos(self.pitch), math.sin(self.pitch)
        gx, gy = y, x                                    # X and Y are swapped on purpose (drawing only)
        screen_x = gx * cyaw - gy * syaw
        depth = gx * syaw + gy * cyaw
        up = z * cp - depth * sp
        radius = 0.5 * math.sqrt((xmax - xmin) ** 2 + (ymax - ymin) ** 2 + (zmax - zmin) ** 2)
        scale = (min(width, height) / 2 - 30) / radius * 1.12 * self.zoom
        return width / 2 + screen_x * scale + self.pan_x, height / 2 - up * scale + self.pan_y

    # --- drawing -------------------------------------------------------------------------------------
    def request_draw(self):
        if self._job is None:
            self._job = self.after(15, self._draw)

    def _label(self, x, y, text, anchor, fill, font):
        """Text with a white halo, readable over the drawing."""
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
            self.create_text(x + dx, y + dy, text=text, anchor=anchor, fill=C["surface"], font=font)
        self.create_text(x, y, text=text, anchor=anchor, fill=fill, font=font)

    def _marker(self, P, p, floor_z, color, kind, label=None, label_color=None, below=False):
        x, y, z = p
        top = P(x, y, z)
        self.create_line(*top, *P(x, y, floor_z), fill=C["faint"], dash=(2, 3))
        if kind == "ring":
            self.create_oval(top[0] - 8, top[1] - 8, top[0] + 8, top[1] + 8, outline=color, width=2)
            self.create_oval(top[0] - 3, top[1] - 3, top[0] + 3, top[1] + 3, fill=color, outline="")
        else:
            self.create_polygon(top[0], top[1] - 7, top[0] + 7, top[1], top[0], top[1] + 7, top[0] - 7, top[1],
                                fill=color, outline="white", width=1)
        if label:
            dy, anchor = (8, "nw") if below else (-8, "sw")
            self._label(top[0] + 12, top[1] + dy, label, anchor, label_color or color, self.f["small"])

    def _draw(self):
        self._job = None
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 80 or h < 80:
            return
        sc = self.scene
        (xmin, xmax), (ymin, ymax), (zmin, zmax) = (sc["limits"][a] for a in AXES)
        bounds = (xmin, xmax, ymin, ymax, zmin, zmax)

        def P(x, y, z):
            return self._project(x, y, z, w, h, bounds)

        for i in range(5):                                           # floor grid
            f = i / 4
            y, x = ymin + (ymax - ymin) * f, xmin + (xmax - xmin) * f
            self.create_line(*P(xmin, y, zmin), *P(xmax, y, zmin), fill=C["grid"])
            self.create_line(*P(x, ymin, zmin), *P(x, ymax, zmin), fill=C["grid"])
        corners = {(i, j, k): P(xmax if i else xmin, ymax if j else ymin, zmax if k else zmin)
                   for i in (0, 1) for j in (0, 1) for k in (0, 1)}
        for (i, j, k), p in corners.items():                         # work volume
            for di, dj, dk in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
                q = (i + di, j + dj, k + dk)
                if q in corners:
                    self.create_line(*p, *corners[q], fill=C["edge"] if k == 0 and dk == 0 else C["grid"])
        origin = P(xmin, ymin, zmin)
        for end, label, color in (((xmin + (xmax - xmin) * 0.25, ymin, zmin), "X", C["axis_x"]),
                                  ((xmin, ymin + (ymax - ymin) * 0.25, zmin), "Y", C["axis_y"]),
                                  ((xmin, ymin, zmin + (zmax - zmin) * 0.25), "Z", C["axis_z"])):
            p = P(*end)
            self.create_line(*origin, *p, fill=color, width=2, arrow=tk.LAST)
            self._label(p[0] + 8, p[1], label, "w", color, self.f["bold"])

        # Program path: points with the three coordinates
        pts = [(i, P(p.x, p.y, p.z)) for i, p in enumerate(sc["points"]) if None not in (p.x, p.y, p.z)]
        for a, b in zip(pts, pts[1:]):
            active = sc["current"] is not None and b[0] == sc["current"]
            self.create_line(*a[1], *b[1], fill=C["ok"] if active else C["path"], width=3 if active else 2,
                             capstyle="round")
        for i, xy in pts:
            if i == sc["current"]:
                self.create_oval(xy[0] - 10, xy[1] - 10, xy[0] + 10, xy[1] + 10, outline=C["ok"], width=2)
            chosen = i in sc["selected"]
            radius = 7 if chosen else 4
            self.create_oval(xy[0] - radius, xy[1] - radius, xy[0] + radius, xy[1] + radius,
                             fill=C["accent"] if chosen else (C["ok"] if i in sc["done"] else "#4a5160"),
                             outline="white", width=2 if chosen else 1)
            self._label(xy[0] - 8, xy[1] - 7, str(i + 1), "se", C["accent"] if chosen else C["muted"],
                        self.f["bold"] if chosen else self.f["small"])

        if self.show_measures and sc["measures"]:
            values = [m[3] for m in sc["measures"]]
            low, high = min(values), max(values)
            span = high - low if high - low > 1e-12 else 1.0
            for x, y, z, v in sc["measures"]:
                sx, sy = P(x, y, z)
                self.create_oval(sx - 3, sy - 3, sx + 3, sy + 3, fill=colormap((v - low) / span), outline="")
        if sc["target"]:
            x, y, z = sc["target"]
            self._marker(P, (x, y, z), zmin, C["warn"], "diamond", f"({x:g}, {y:g}, {z:g})", C["text"])
        if sc["machine"]:
            m = sc["machine"]
            self._marker(P, (m["X"], m["Y"], m["Z"]), zmin, C["accent"], "ring", t("view.machine"), below=True)

        x = 16
        for text, color in ((t("view.machine"), C["accent"]), (t("view.target"), C["warn"]),
                            (t("view.program"), "#4a5160")):
            self.create_oval(x, 14, x + 8, 22, fill=color, outline="")
            item = self.create_text(x + 14, 18, text=text, anchor="w", fill=C["muted"], font=self.f["small"])
            x = self.bbox(item)[2] + 14
        self.create_text(16, h - 14, anchor="w", fill=C["faint"], font=self.f["small"],
                         text=t("view.hint") if w >= 440 else t("view.hint.short"))
