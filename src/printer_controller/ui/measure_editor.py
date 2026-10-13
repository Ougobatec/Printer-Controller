"""Editor of the measurement of a program line.

Two steps: the type of measurement, then only the fields that concern it. Used in the edit form
under the table (several lines at once) and in the window opened from the MEASURE column.
"""

import tkinter as tk
from tkinter import ttk

from ..core.program.model import AVERAGE, BY, COUNT, DURATION, KINDS, PATH, RATE
from ..i18n import t
from .theme import C


def _number(text, field):
    try:
        return float(str(text).strip().replace(",", "."))
    except ValueError:
        raise ValueError(t("measure.error.number", field=field)) from None


class MeasureEditor(tk.Frame):
    def __init__(self, parent, fonts, bg=None, on_enter=None):
        bg = bg or C["surface"]
        super().__init__(parent, bg=bg)
        self._bg, self._f, self._on_enter = bg, fonts, on_enter
        self.kinds = {k: t(f"measure.type.{k}") for k in KINDS}
        self.bys = {k: t(f"measure.by.{k}") for k in (DURATION, COUNT, RATE)}
        self.kind_var, self.by_var = tk.StringVar(), tk.StringVar()
        self.value_var, self.rate_var = tk.StringVar(), tk.StringVar()      # value: duration or count
        self.summary_var = tk.StringVar()
        self._entries = []
        self._count = self._duration = self._rate = ""
        self._loading = False
        self._build()
        for var in (self.kind_var, self.value_var, self.rate_var):
            var.trace_add("write", lambda *_: self._refresh())
        self.by_var.trace_add("write", lambda *_: self._by_changed())
        self._refresh()

    # --- layout ---------------------------------------------------------------------------------
    def _label(self, parent, text, column, pad=0):
        tk.Label(parent, text=text, bg=self._bg, fg=C["muted"], font=self._f["small"]).grid(
            row=0, column=column, sticky="w", padx=(pad, 6))

    def _entry(self, parent, var, column, width=6):
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(row=0, column=column)
        if self._on_enter:
            entry.bind("<Return>", lambda _e: self._on_enter())
        self._entries.append(entry)
        return entry

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.kind_combo = ttk.Combobox(self, textvariable=self.kind_var, state="readonly", values=list(self.kinds.values()))
        self.kind_combo.grid(row=0, column=0, sticky="ew")
        self.details = tk.Frame(self, bg=self._bg)
        self.details.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        top = tk.Frame(self.details, bg=self._bg)
        top.grid(row=0, column=0, sticky="w")
        self.by_label = tk.Label(top, bg=self._bg, fg=C["muted"], font=self._f["small"])
        self.by_label.grid(row=0, column=0, padx=(0, 8))
        self.by_combo = ttk.Combobox(top, textvariable=self.by_var, state="readonly", width=24)
        self.by_combo.grid(row=0, column=1)
        line = tk.Frame(self.details, bg=self._bg)
        line.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._entry(line, self.value_var, 0)
        self.value_unit = tk.Label(line, bg=self._bg, fg=C["muted"], font=self._f["small"])
        self.value_unit.grid(row=0, column=1, padx=(6, 0))
        self.at_label = tk.Label(line, text=t("measure.at"), bg=self._bg, fg=C["muted"], font=self._f["small"])
        self.at_label.grid(row=0, column=2, padx=(10, 6))
        self.rate_entry = self._entry(line, self.rate_var, 3)
        self.rate_unit = tk.Label(line, text=t("measure.rate.unit"), bg=self._bg, fg=C["muted"], font=self._f["small"])
        self.rate_unit.grid(row=0, column=4, padx=(6, 0))
        tk.Label(self, textvariable=self.summary_var, bg=self._bg, fg=C["text"], font=self._f["small"],
                 anchor="w").grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.hint = tk.Label(self, bg=self._bg, fg=C["faint"], font=self._f["small"], anchor="w", justify="left")
        self.hint.grid(row=3, column=0, sticky="ew", pady=(2, 0))
        self.bind("<Configure>", lambda e: self.hint.configure(wraplength=max(160, e.width - 4)))

    # --- state shown --------------------------------------------------------------------------------
    def _kind(self):
        return next((k for k, v in self.kinds.items() if v == self.kind_var.get()), "")

    def _by(self):
        return next((k for k, v in self.bys.items() if v == self.by_var.get()), "")

    def _refresh(self):
        kind = self._kind()
        options = BY.get(kind, ())
        self.by_combo.configure(values=[self.bys[k] for k in options])
        if kind in BY and self._by() not in options:
            self.by_var.set(self.bys[options[0]])            # a valid choice for this kind
        by = self._by()
        self.details.grid() if kind in BY else self.details.grid_remove()
        self.by_label.configure(text=t("measure.average_over") if kind == AVERAGE else t("measure.defined_by"))
        # one value (duration, number of measurements or frequency) and, for an average, a frequency
        self.value_unit.configure(text={DURATION: "s", COUNT: "", RATE: t("measure.rate.unit")}.get(by, ""))
        has_rate_field = kind == AVERAGE
        for widget in (self.at_label, self.rate_entry, self.rate_unit):
            widget.grid() if has_rate_field else widget.grid_remove()
        if kind == PATH and by == COUNT:
            self.summary_var.set(t("measure.summary.path_count", n=self.value_var.get() or "?"))
        else:
            self.summary_var.set(self._summary(kind, by))
        self.hint.configure(text=t(f"measure.hint.{kind}") if kind else "")

    def _summary(self, kind, by):
        try:
            if kind == AVERAGE:
                rate = _number(self.rate_var.get(), "")
                if rate <= 0:
                    return ""
                if by == DURATION:
                    return t("measure.summary.duration", n=max(1, round(_number(self.value_var.get(), "") * rate)))
                n = _number(self.value_var.get(), "")
                return t("measure.summary.count", n=f"{n:g}", s=n / rate)
            if kind == PATH and by == RATE:
                rate = _number(self.value_var.get(), "")
                return t("measure.summary.rate", s=1 / rate) if rate > 0 else ""
        except ValueError:
            pass
        return ""

    # --- public API -----------------------------------------------------------------------------------------
    def allow_path(self, allowed):
        """"Path" is removed from the list when it makes no sense (first line alone)."""
        self.kind_combo.configure(values=[v for k, v in self.kinds.items() if allowed or k != PATH])

    def set_enabled(self, enabled):
        self.kind_combo.configure(state="readonly" if enabled else "disabled")
        self.by_combo.configure(state="readonly" if enabled else "disabled")
        for entry in self._entries:
            entry.configure(state="normal" if enabled else "disabled")

    def set_values(self, kind, by, count, duration, rate):
        """`kind` empty: the lines differ, nothing is chosen and nothing will be changed.

        Strings: "" for a value that differs between lines."""
        self._loading = True
        self._count, self._duration, self._rate = count, duration, rate
        self.kind_var.set(self.kinds.get(kind, ""))
        self.by_var.set(self.bys.get(by, ""))
        self.rate_var.set(rate if kind == AVERAGE else "")
        self.value_var.set(self._value_for(kind, by))
        self._loading = False
        self._refresh()

    def _by_changed(self):
        """The user switched between duration / number / frequency: show the matching stored value."""
        if not self._loading:
            self.value_var.set(self._value_for(self._kind(), self._by()))
        self._refresh()

    def _value_for(self, kind, by):
        if kind == PATH and by == RATE:
            return self._rate
        return self._count if by == COUNT else self._duration

    def snapshot(self):
        kind, by = self._kind(), self._by()
        return {"kind": kind, "by": by, "value": self.value_var.get().strip(), "rate": self.rate_var.get().strip()}

    def changes(self, old, max_rate=None):
        """Fields of the measure to change (dict for `dataclasses.replace`); raises ValueError."""
        now = self.snapshot()
        kind, by = now["kind"], now["by"]
        out = {}
        if not kind:
            return out
        if kind != old["kind"]:
            out["kind"] = kind
        if kind in BY and (by != old["by"] or kind != old["kind"]):
            out["by"] = by
        if kind in BY and (now["value"] != old["value"] or by != old["by"] or kind != old["kind"]) and now["value"]:
            value = _number(now["value"], self.bys.get(by, ""))
            if by == COUNT:
                if value < 1 or value != int(value):
                    raise ValueError(t("measure.error.count"))
                out["count"] = int(value)
            elif by == DURATION:
                if value <= 0:
                    raise ValueError(t("measure.error.duration"))
                out["duration_s"] = value
            else:
                out["rate_hz"] = value
        if kind == AVERAGE and now["rate"] and (now["rate"] != old["rate"] or kind != old["kind"]):
            out["rate_hz"] = _number(now["rate"], t("measure.rate"))
        rate = out.get("rate_hz")
        if rate is not None and (rate <= 0 or (max_rate and rate > max_rate)):
            raise ValueError(t("measure.error.rate", max=max_rate or 0))
        return out
