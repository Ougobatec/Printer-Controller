"""Results tab: live tables (measurements, groups), graphs (time, X, Y, Z, 3D) and exports."""

import os
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from ..core.results.export_xlsx import GROUP_COLUMNS, MEASURE_COLUMNS, export_csv, export_xlsx, group_rows, measurement_rows
from ..i18n import t
from .image_export_dialog import open_image_export
from .plots import Plot2D, Scatter3D
from .theme import C
from .widgets import Segmented, Tooltip

MAX_ROWS = 5000                   # rows kept in the on-screen table (the export has everything)
VIEWS = ("measures", "groups", "time", "x", "y", "z", "3d")
TITLES = {"elapsed": "results.col.elapsed.short"}
FIXED = {"x": 3, "y": 3, "z": 3, "elapsed": 3}            # decimals shown for positions and times
WIDTHS = {"n": 50, "timestamp": 175, "elapsed": 80, "x": 62, "y": 62, "z": 62, "position": 70, "kind": 70, "raw": 90,
          "value": 90, "unit": 50, "group": 60, "first": 175, "duration": 80, "count": 80, "mean": 90, "std": 90,
          "min": 80, "max": 80}


def _text(value, column=""):
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.{FIXED[column]}f}" if column in FIXED else f"{value:.6g}"
    return str(value)


class ResultsTab(tk.Frame):
    def __init__(self, parent, fonts, app):
        super().__init__(parent, bg=C["surface"], highlightthickness=1, highlightbackground=C["line"])
        self.f, self.app, self.store = fonts, app, app.store
        self._version = -1
        self._shown = 0                                  # measurements already in the table
        pad = tk.Frame(self, bg=C["surface"])
        pad.pack(fill="both", expand=True, padx=16, pady=14)

        head = tk.Frame(pad, bg=C["surface"])
        head.pack(fill="x")
        self.view_var = tk.StringVar(value="measures")
        labels = [(v, t(f"results.view.{v}") if v in ("measures", "groups") else
                   t("graph.time") if v == "time" else v.upper()) for v in VIEWS]
        Segmented(head, labels, self.view_var, command=lambda _v: self._show_view(), width=7).pack(side="left")
        bar = tk.Frame(pad, bg=C["surface"])
        bar.pack(fill="x", pady=(8, 4))
        self.count_var = tk.StringVar()
        tk.Label(bar, textvariable=self.count_var, bg=C["surface"], fg=C["muted"], font=fonts["small"], anchor="w").pack(
            side="left")
        self.clear_button = ttk.Button(bar, text=t("results.clear"), style="Ghost.TButton", command=self._clear)
        self.clear_button.pack(side="right")
        self.image_button = ttk.Button(bar, text=t("results.image"), style="Soft.TButton", command=self._export_image)
        self.image_button.pack(side="right", padx=6)
        Tooltip(self.image_button, t("results.image.tip"))
        self.export_button = ttk.Button(bar, text=t("results.export"), style="Soft.TButton", command=self._export)
        self.export_button.pack(side="right")
        Tooltip(self.export_button, t("results.export.tip"))

        self.body = tk.Frame(pad, bg=C["surface"])
        self.body.pack(fill="both", expand=True)
        self.measure_tree = self._table(MEASURE_COLUMNS)
        self.group_tree = self._table(GROUP_COLUMNS)
        self.plot = Plot2D(self.body, fonts)
        self.cloud = Scatter3D(self.body, fonts)
        self._show_view()
        self.refresh()

    def _table(self, columns):
        frame = tk.Frame(self.body, bg=C["surface"])
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        for column in columns:
            tree.heading(column, text=t(TITLES.get(column, f"results.col.{column}")), anchor="center")
            tree.column(column, width=WIDTHS.get(column, 80), minwidth=40, anchor="center", stretch=False)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        horizontal.pack(side="bottom", fill="x")
        vertical.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        tree.captures_wheel = True
        tree.frame = frame
        return tree

    # --- views ---------------------------------------------------------------------------------------
    def current_view(self):
        return self.view_var.get()

    def _show_view(self):
        for widget in (self.measure_tree.frame, self.group_tree.frame, self.plot, self.cloud):
            widget.pack_forget()
        view = self.current_view()
        widget = {"measures": self.measure_tree.frame, "groups": self.group_tree.frame, "3d": self.cloud}.get(view, self.plot)
        widget.pack(fill="both", expand=True)
        self._version = -1
        self.refresh()

    def refresh(self):
        """Called several times per second: redraws only when the results changed."""
        if self.store.version == self._version:
            return
        self._version = self.store.version
        measurements, groups = self.store.snapshot()
        self.count_var.set(t("results.count", n=len(measurements), g=len(groups)) if measurements else t("results.empty"))
        view = self.current_view()
        if view == "measures":
            self._fill_measures(measurements)
        elif view == "groups":
            self._fill_groups(groups)
        elif view == "3d":
            points = [(m.x, m.y, m.z, m.value) for m in measurements if None not in (m.x, m.y, m.z)]
            self.cloud.show(points, self.app.settings.sensor.unit, t("results.empty"))
        else:
            self._draw_plot(view, measurements)

    def _fill_measures(self, measurements):
        tree = self.measure_tree
        if len(measurements) < self._shown:                         # cleared or a new run
            tree.delete(*tree.get_children())
            self._shown = 0
        for row in measurement_rows(measurements[self._shown:]):
            tree.insert("", "end", values=[_text(v, c) for v, c in zip(row, MEASURE_COLUMNS)])
        self._shown = len(measurements)
        children = tree.get_children()
        if len(children) > MAX_ROWS + 1000:
            tree.delete(*children[:len(children) - MAX_ROWS])
        if children:
            tree.see(tree.get_children()[-1])

    def _fill_groups(self, groups):
        tree = self.group_tree
        tree.delete(*tree.get_children())
        for row in group_rows(groups):
            tree.insert("", "end", values=[_text(v, c) for v, c in zip(row, GROUP_COLUMNS)])

    def _draw_plot(self, view, measurements):
        t0 = measurements[0].timestamp if measurements else 0.0
        pick = (lambda m: m.timestamp - t0) if view == "time" else (lambda m: getattr(m, view))
        path = [(pick(m), m.value) for m in measurements if m.kind == "path" and pick(m) is not None]
        dots = [(pick(m), m.value) for m in measurements if m.kind != "path" and pick(m) is not None]
        label = t("graph.time.axis") if view == "time" else t("graph.pos.axis", axis=view.upper())
        self.plot.show(lines=[([p[0] for p in path], [p[1] for p in path], C["accent"])] if path else [],
                       dots=[([d[0] for d in dots], [d[1] for d in dots], "#4a5160", 3)] if dots else [],
                       xlabel=label, unit=self.app.settings.sensor.unit, empty=t("results.empty"))

    # --- actions ---------------------------------------------------------------------------------------------
    def _clear(self):
        if len(self.store) and messagebox.askyesno(t("results.clear"), t("results.clear.confirm"), parent=self.winfo_toplevel()):
            self.store.clear()
            self.refresh()

    def _export(self):
        if not len(self.store):
            return
        folder = self.app.settings.results.export_dir
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(), initialdir=folder, defaultextension=".xlsx", initialfile=f"results_{stamp}.xlsx",
            filetypes=[(t("results.filetype.xlsx"), "*.xlsx"), (t("results.filetype.csv"), "*.csv")])
        if not path:
            return
        r = self.app.settings.results
        try:
            count = (export_csv(self.store, path, r.csv_delimiter, r.csv_decimal) if path.lower().endswith(".csv")
                     else export_xlsx(self.store, path))
        except ImportError:
            messagebox.showerror(t("results.export.error"), t("results.export.missing", lib="openpyxl"),
                                 parent=self.winfo_toplevel())
            return
        except OSError as exc:
            messagebox.showerror(t("results.export.error"), str(exc), parent=self.winfo_toplevel())
            return
        self.app.log(t("results.export.done", n=count, path=path))

    def _export_image(self):
        if len(self.store):
            view = self.current_view()
            view = view if view in ("time", "x", "y", "z", "3d") else "time"
            open_image_export(self.winfo_toplevel(), self.f, self.store, self.app.settings, view,
                              (self.cloud.yaw, self.cloud.pitch), self.app.log)
