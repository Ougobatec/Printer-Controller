"""Table of the program lines: tick boxes, values, delete crosses, direct editing of a value."""

import tkinter as tk
from tkinter import ttk

from ..core.program.model import AVERAGE, COUNT, DURATION, PATH, SIMPLE
from ..i18n import t
from .theme import C

COLUMNS = ("sel", "n", "x", "y", "z", "speed", "pause", "measure", "del")
EDITABLE = {"x": "x", "y": "y", "z": "z", "speed": "speed", "pause": "pause"}
CHECK_ON, CHECK_OFF, DELETE = "☑", "☐", "✕"


def number(value):
    return "" if value is None else f"{value:g}"


def measure_text(m):
    """Short text of a measurement, including its duration or number of readings."""
    if m.kind == SIMPLE:
        return t("measure.label.simple")
    if m.kind == AVERAGE:
        if m.by == DURATION:
            return t("measure.label.average_duration", d=m.duration_s)
        return t("measure.label.average_count", n=m.count)
    if m.kind == PATH:
        return t("measure.label.path_global")
    return t("measure.label.none")


class ProgramTable(tk.Frame):
    """Callbacks: `on_selection()`, `on_delete(index)`, `on_cell(index, field, text)`, `on_measure(index)`."""

    def __init__(self, parent, fonts, on_selection, on_delete, on_cell, on_measure):
        super().__init__(parent, bg=C["surface"])
        self._on_selection, self._on_delete, self._on_cell, self._on_measure = on_selection, on_delete, on_cell, on_measure
        self._editor = None
        self.locked = False                                  # no edit while a program runs
        titles = {"sel": CHECK_OFF, "n": "#", "x": "X", "y": "Y", "z": "Z", "speed": t("program.col.speed"),
                  "pause": t("program.col.pause"), "measure": t("program.col.measure"), "del": ""}
        widths = {"sel": 32, "n": 30, "x": 54, "y": 54, "z": 54, "speed": 140, "pause": 84, "measure": 210, "del": 32}
        self.tree = ttk.Treeview(self, columns=COLUMNS, show="headings", selectmode="extended", height=6)
        for column in COLUMNS:
            fixed = column in ("sel", "del")
            anchor = "w" if column == "measure" else "center"
            self.tree.heading(column, text=titles[column], anchor=anchor)
            self.tree.column(column, width=widths[column], minwidth=widths[column] if fixed else 40, anchor=anchor,
                             stretch=not fixed)
        self.tree.heading("sel", command=self.toggle_all)
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.captures_wheel = True
        self.tree.tag_configure("current", background="#dff5e8")
        self.empty = tk.Label(self.tree, text=t("program.empty"), bg=C["surface"], fg=C["faint"], font=fonts["base"],
                              justify="center")
        self.tree.bind("<<TreeviewSelect>>", self._selected)
        self.tree.bind("<Control-a>", lambda _e: (self.toggle_all(), "break")[1])
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<Double-1>", self._double_click)

    # --- content ------------------------------------------------------------------------------------
    def refresh(self, points, keep=None, current=None):
        """Rebuild the rows. `keep`: rows to tick afterwards (default: the same ones)."""
        self._close_editor()
        rows = self.selected_rows() if keep is None else keep
        self.tree.delete(*self.tree.get_children())
        for i, p in enumerate(points, start=1):
            self.tree.insert("", "end", tags=("current",) if current == i - 1 else (),
                             values=(CHECK_OFF, i, number(p.x), number(p.y), number(p.z), number(p.speed),
                                     number(p.pause), measure_text(p.measure), DELETE))
        if points:
            self.empty.place_forget()
            self.select([r for r in rows if r < len(points)])
        else:
            self.empty.place(relx=0.5, rely=0.42, anchor="center")
        self._sync_checks()

    def selected_rows(self):
        position = {item: i for i, item in enumerate(self.tree.get_children())}
        return sorted(position[s] for s in self.tree.selection() if s in position)

    def select(self, rows):
        items = self.tree.get_children()
        chosen = [items[i] for i in rows if 0 <= i < len(items)]
        self.tree.selection_set(chosen)
        if chosen:
            self.tree.see(chosen[0])

    def toggle_all(self):
        """Header box: tick everything, or untick everything if all is ticked."""
        items = self.tree.get_children()
        if not items:
            return
        self.tree.selection_set(() if len(self.tree.selection()) == len(items) else items)

    def _selected(self, _event=None):
        self._sync_checks()
        self._on_selection()

    def _sync_checks(self):
        chosen, items = set(self.tree.selection()), self.tree.get_children()
        for item in items:
            mark = CHECK_ON if item in chosen else CHECK_OFF
            if self.tree.set(item, "sel") != mark:
                self.tree.set(item, "sel", mark)
        self.tree.heading("sel", text=CHECK_ON if items and len(chosen) == len(items) else CHECK_OFF)

    # --- clicks -----------------------------------------------------------------------------------------
    def _cell(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None, None
        item = self.tree.identify_row(event.y)
        try:
            return item, COLUMNS[int(self.tree.identify_column(event.x)[1:]) - 1]
        except (ValueError, IndexError):
            return None, None

    def _click(self, event):
        item, column = self._cell(event)
        if not item:
            return None
        if column == "sel":
            self.tree.selection_toggle(item)
            return "break"
        if column == "del":
            if not self.locked:
                self._on_delete(self.tree.index(item))
            return "break"
        return None

    def _double_click(self, event):
        item, column = self._cell(event)
        if not item or self.locked:
            return
        if column == "measure":
            self._on_measure(self.tree.index(item))
        elif column in EDITABLE:
            self._edit(item, column)

    # --- direct editing of one value (one line at a time) ------------------------------------------------------
    def _close_editor(self):
        if self._editor is not None:
            self._editor.destroy()
            self._editor = None

    def _edit(self, item, column):
        self._close_editor()
        x, y, w, h = self.tree.bbox(item, column)
        var = tk.StringVar(value=self.tree.set(item, column))
        entry = ttk.Entry(self.tree, textvariable=var, justify="center")
        entry.place(x=x, y=y, width=w, height=h)
        entry.focus_set()
        entry.select_range(0, "end")
        index, state = self.tree.index(item), {"done": False}

        def finish(commit):
            if state["done"]:
                return
            state["done"] = True
            self._close_editor()
            if commit:
                self._on_cell(index, EDITABLE[column], var.get())

        entry.bind("<Return>", lambda _e: finish(True))
        entry.bind("<FocusOut>", lambda _e: finish(True))
        entry.bind("<Escape>", lambda _e: finish(False))
        self._editor = entry
