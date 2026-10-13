"""Window to export the graphs as PNG images, with the orientation of the 3D view of your choice."""

import math
import os
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from ..core.results import export_images as images
from ..i18n import t
from .plots import Scatter3D
from .theme import C

PRESETS = (("current", None), ("iso", "iso"), ("top", "top"), ("face", "face"), ("side", (0.0, 0.0)))


def open_image_export(root, fonts, store, settings, current_view, current_angles, log):
    """`current_angles`: (yaw, pitch) of the 3D view shown; `log(text)` reports the result."""
    win = tk.Toplevel(root)
    win.title(t("image.title"))
    win.configure(bg=C["surface"])
    win.transient(root)
    win.resizable(False, False)
    pad = tk.Frame(win, bg=C["surface"])
    pad.pack(padx=20, pady=16)

    def title(text, top=0):
        tk.Label(pad, text=text, bg=C["surface"], fg=C["muted"], font=fonts["section"]).pack(anchor="w", pady=(top, 4))

    scope = tk.StringVar(value="current")
    title(t("image.scope"))
    ttk.Radiobutton(pad, text=t("image.scope.current", name=t(f"graph.{current_view}")), variable=scope,
                    value="current").pack(anchor="w")
    ttk.Radiobutton(pad, text=t("image.scope.all"), variable=scope, value="all").pack(anchor="w")
    title(t("image.orientation"), 14)
    azim, elev = tk.DoubleVar(), tk.DoubleVar()
    body = tk.Frame(pad, bg=C["surface"])
    body.pack(fill="x")
    preview = tk.Canvas(body, width=150, height=150, bg=C["soft"], highlightthickness=0)
    preview.pack(side="right", padx=(14, 0))
    controls = tk.Frame(body, bg=C["surface"])
    controls.pack(side="left")

    def draw(*_):
        preview.delete("all")
        az, el = math.radians(azim.get()), math.radians(elev.get())
        right = (-math.sin(az), math.cos(az), 0.0)
        up = (-math.sin(el) * math.cos(az), -math.sin(el) * math.sin(az), math.cos(el))

        def project(p):
            p = [c - 0.5 for c in p]
            return 75 + 70 * sum(a * b for a, b in zip(p, right)), 75 - 70 * sum(a * b for a, b in zip(p, up))

        corners = {(i, j, k): project((i, j, k)) for i in (0, 1) for j in (0, 1) for k in (0, 1)}
        for (i, j, k), a in corners.items():
            for d in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
                b = corners.get((i + d[0], j + d[1], k + d[2]))
                if b:
                    preview.create_line(*a, *b, fill=C["edge"])
        for end, name, color in ((corners[(1, 0, 0)], "Y", C["axis_y"]), (corners[(0, 1, 0)], "X", C["axis_x"]),
                                 (corners[(0, 0, 1)], "Z", C["axis_z"])):
            preview.create_line(*corners[(0, 0, 0)], *end, fill=color, width=2)
            preview.create_text(end[0] + 7, end[1], text=name, fill=color, font=fonts["small"])

    for text, var, low, high in ((t("image.azimuth"), azim, -180, 180), (t("image.elevation"), elev, -90, 90)):
        row = tk.Frame(controls, bg=C["surface"])
        row.pack(fill="x", pady=(0, 4))
        tk.Label(row, text=text, bg=C["surface"], fg=C["muted"], font=fonts["small"], width=12, anchor="w").pack(side="left")
        tk.Scale(row, variable=var, from_=low, to=high, orient="horizontal", resolution=1, length=170, bg=C["surface"],
                 highlightthickness=0, troughcolor=C["soft"], command=draw).pack(side="left")

    def preset(spec):
        yaw_pitch = current_angles if spec is None else Scatter3D.PRESETS[spec] if isinstance(spec, str) else None
        angles = images.view_to_angles(*yaw_pitch) if yaw_pitch else spec
        azim.set(round(max(-180, min(180, angles[0]))))
        elev.set(round(max(-90, min(90, angles[1]))))
        draw()

    chips = tk.Frame(controls, bg=C["surface"])
    chips.pack(fill="x", pady=(4, 0))
    for name, spec in PRESETS:
        ttk.Button(chips, text=t(f"image.preset.{name}"), style="Chip.TButton", command=lambda s=spec: preset(s)).pack(
            side="left", padx=(0, 4))
    preset(None)
    coordinates = tk.BooleanVar(value=True)
    ttk.Checkbutton(pad, text=t("image.coordinates"), variable=coordinates).pack(anchor="w", pady=(14, 0))
    tk.Label(pad, text=t("image.coordinates.note"), bg=C["surface"], fg=C["faint"], font=fonts["small"]).pack(anchor="w")

    def export():
        measurements, _ = store.snapshot()
        views = [current_view] if scope.get() == "current" else list(images.VIEWS)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder = settings.results.export_dir
        os.makedirs(folder, exist_ok=True)
        if len(views) == 1:
            path = filedialog.asksaveasfilename(parent=win, initialdir=folder, defaultextension=".png",
                                                initialfile=f"graph_{views[0]}_{stamp}.png",
                                                filetypes=[("PNG", "*.png")])
            targets = {views[0]: path} if path else {}
        else:
            chosen = filedialog.askdirectory(parent=win, initialdir=folder, title=t("image.folder"))
            targets = {v: os.path.join(chosen, f"graph_{v}_{stamp}.png") for v in views} if chosen else {}
        if not targets:
            return
        win.configure(cursor="watch")
        win.update_idletasks()
        problems = []
        try:
            for view, path in targets.items():
                try:
                    if view == "3d":
                        shown, total = images.render_3d(path, measurements, settings.sensor.unit, azim.get(), elev.get(),
                                                        coordinates.get())
                    else:
                        shown, total = images.render_2d(path, measurements, view, settings.sensor.unit, coordinates.get())
                    log(t("image.done", path=path))
                    if coordinates.get() and shown < total:
                        log(t("image.partial", name=t(f"graph.{view}"), shown=shown, total=total))
                except ValueError as exc:
                    problems.append(f"{t('graph.' + view)}: {exc}")
        except ImportError:
            messagebox.showerror(t("image.title"), t("image.missing"), parent=win)
            return
        except OSError as exc:
            messagebox.showerror(t("image.title"), str(exc), parent=win)
            return
        finally:
            win.configure(cursor="")
        if problems:
            messagebox.showwarning(t("image.title"), "\n".join(problems), parent=win)
        else:
            win.destroy()

    buttons = tk.Frame(pad, bg=C["surface"])
    buttons.pack(fill="x", pady=(16, 0))
    ttk.Button(buttons, text=t("image.export"), style="Accent.TButton", command=export).pack(side="right")
    ttk.Button(buttons, text=t("image.cancel"), style="Soft.TButton", command=win.destroy).pack(side="right", padx=(0, 8))
    win.grab_set()
