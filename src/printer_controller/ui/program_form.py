"""Edit form under the table: position, move, measurement. Nothing changes before OK."""

import tkinter as tk
from tkinter import ttk

from ..i18n import t
from .measure_editor import MeasureEditor
from .program_table import number
from .theme import C

FIELDS = ("x", "y", "z", "speed", "pause")


def _number(text, field):
    try:
        return float(text.replace(",", "."))
    except ValueError:
        raise ValueError(field) from None


class ProgramForm(tk.Frame):
    def __init__(self, parent, fonts, on_ok):
        super().__init__(parent, bg=C["soft"])
        self.vars = {f: tk.StringVar() for f in FIELDS}
        self.title, self.note = tk.StringVar(), tk.StringVar()
        self._loaded = {}
        self._entries = []
        inner = tk.Frame(self, bg=C["soft"])
        inner.pack(fill="x", padx=12, pady=10)
        inner.grid_columnconfigure(0, weight=1)
        small = fonts["small"]

        def caption(row, text):
            tk.Label(inner, text=text, bg=C["soft"], fg=C["muted"], font=fonts["section"], anchor="w").grid(
                row=row, column=0, sticky="w", pady=(12, 4))

        def field(parent, label, key, unit, column, width=7):
            tk.Label(parent, text=label, bg=C["soft"], fg=C["muted"], font=small).grid(
                row=0, column=column, sticky="w", padx=(0 if column == 0 else 16, 6))
            entry = ttk.Entry(parent, textvariable=self.vars[key], width=width)
            entry.grid(row=0, column=column + 1)
            entry.bind("<Return>", lambda _e: on_ok())
            self._entries.append(entry)
            if unit:
                tk.Label(parent, text=unit, bg=C["soft"], fg=C["faint"], font=small).grid(row=0, column=column + 2, padx=(4, 0))

        tk.Label(inner, textvariable=self.title, bg=C["soft"], fg=C["text"], font=fonts["section"], anchor="w").grid(
            row=0, column=0, sticky="w")
        self.note_label = tk.Label(inner, textvariable=self.note, bg=C["soft"], fg=C["muted"], font=small, anchor="w",
                                   justify="left")
        self.note_label.grid(row=1, column=0, sticky="ew")
        caption(2, t("form.position"))
        coords = tk.Frame(inner, bg=C["soft"])
        coords.grid(row=3, column=0, sticky="w")
        for i, axis in enumerate("xyz"):
            field(coords, axis.upper(), axis, "mm" if i == 2 else "", 3 * i, 6)
        caption(4, t("form.motion"))
        motion = tk.Frame(inner, bg=C["soft"])
        motion.grid(row=5, column=0, sticky="w")
        field(motion, t("form.speed"), "speed", "mm/min", 0)
        field(motion, t("form.pause"), "pause", "s", 3, 5)
        caption(6, t("form.measure"))
        self.measure = MeasureEditor(inner, fonts, C["soft"], on_enter=on_ok)
        self.measure.grid(row=7, column=0, sticky="ew")
        buttons = tk.Frame(inner, bg=C["soft"])
        buttons.grid(row=8, column=0, sticky="e", pady=(14, 0))
        self.cancel_button = ttk.Button(buttons, text=t("form.cancel"), style="Soft.TButton", command=self.reload)
        self.cancel_button.pack(side="left", padx=(0, 8))
        self.ok_button = ttk.Button(buttons, text=t("form.ok"), style="Accent.TButton", width=8, command=on_ok)
        self.ok_button.pack(side="left")
        inner.bind("<Configure>", lambda e: self.note_label.configure(wraplength=max(200, e.width - 4)))
        self._points, self._rows = [], []
        self.load([], [])

    # --- showing the ticked lines ---------------------------------------------------------------------------
    def load(self, points, rows):
        """Show the values of the ticked lines; a field where they differ stays empty."""
        self._points, self._rows = points, rows
        chosen = [points[i] for i in rows if i < len(points)]
        enabled = bool(chosen)
        for widget in self._entries + [self.ok_button, self.cancel_button]:
            widget.configure(state="normal" if enabled else "disabled")
        self.measure.set_enabled(enabled)
        if not chosen:
            self.title.set(t("form.title.none"))
            self.note.set(t("form.note.none"))
            for var in self.vars.values():
                var.set("")
            self.measure.set_values("", "", "", "")
        else:
            self.title.set(t("form.line", n=rows[0] + 1) if len(chosen) == 1 else t("form.lines", n=len(chosen)))
            self.note.set(t("form.note.one") if len(chosen) == 1 else t("form.note.many"))

            def common(get):
                values = {get(p) for p in chosen}
                return values.pop() if len(values) == 1 else None

            for key in FIELDS:
                self.vars[key].set(number(common(lambda p, k=key: getattr(p, k))))
            self.measure.allow_path(len(chosen) > 1 or rows[0] > 0)
            m = lambda get: common(lambda p: get(p.measure))                       # noqa: E731
            kind, by = m(lambda x: x.kind) or "", m(lambda x: x.by) or ""
            self.measure.set_values(kind, by, number(m(lambda x: x.count)), number(m(lambda x: x.duration_s)))
        self._loaded = self._snapshot()

    def reload(self):
        self.load(self._points, self._rows)

    def set_locked(self, locked):
        """No editing while a program runs."""
        if locked:
            for widget in self._entries + [self.ok_button, self.cancel_button]:
                widget.configure(state="disabled")
            self.measure.set_enabled(False)
        else:
            self.reload()

    def _snapshot(self):
        data = {k: v.get().strip() for k, v in self.vars.items()}
        data["measure"] = self.measure.snapshot()
        return data

    def changes(self, max_rate=None):
        """({field: value} for the points, {field: value} for their measures); raises ValueError.

        Only what was edited. An emptied field clears the value of a single line (nothing for several)."""
        now, old = self._snapshot(), self._loaded
        single = len(self._rows) == 1
        point = {}
        for key in FIELDS:
            if now[key] == old[key]:
                continue
            if now[key] == "":
                if single and key in ("x", "y", "z"):
                    point[key] = None
                continue
            value = _number(now[key], key.upper())
            if key == "speed" and value <= 0:
                raise ValueError(t("form.speed"))
            if key == "pause" and value < 0:
                raise ValueError(t("form.pause"))
            point[key] = value
        return point, self.measure.changes(old["measure"], max_rate)
