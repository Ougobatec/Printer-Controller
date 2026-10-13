"""Application settings (header button), sorted by category: general, program, measurement, results."""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..i18n import LANGUAGES, t
from .theme import C

# (category, "section.key", kind, unit, maximum provider)
FIELDS = (
    ("program", "program.speed", "float", "mm/min", "speed"),
    ("program", "program.pause", "float0", "s", None),
    ("program", "program.rate_hz", "float", "Hz", "rate"),
    ("program", "program.duration_s", "float", "s", None),
    ("program", "program.count", "int", "", None),
    ("program", "program.folder", "dir", "", None),
    ("measure", "sensor.live_rate_hz", "float", "Hz", "rate"),
    ("measure", "sensor.live_history", "int", "", None),
    ("results", "results.export_dir", "dir", "", None),
    ("results", "results.csv_delimiter", "text", "", None),
    ("results", "results.csv_decimal", "text", "", None),
)


def open_app_settings(root, fonts, app):
    """`app` provides: settings, speed_limit(), rate_limit(), acquisition."""
    settings = app.settings
    win = tk.Toplevel(root)
    win.title(t("settings.title"))
    win.configure(bg=C["surface"])
    win.transient(root)
    win.resizable(False, False)
    pad = tk.Frame(win, bg=C["surface"])
    pad.pack(padx=22, pady=18)
    vars_ = {}
    limits = {"speed": app.speed_limit(), "rate": app.rate_limit()}
    row = 0

    def category(key):
        nonlocal row
        tk.Label(pad, text=t(f"settings.cat.{key}"), bg=C["surface"], fg=C["muted"], font=fonts["section"]).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0 if row == 0 else 16, 6))
        row += 1

    category("general")
    tk.Label(pad, text=t("settings.language"), bg=C["surface"], fg=C["text"], font=fonts["small"]).grid(
        row=row, column=0, sticky="w", pady=3)
    language = tk.StringVar(value=LANGUAGES.get(settings.app.language, LANGUAGES["en"]))
    ttk.Combobox(pad, textvariable=language, values=list(LANGUAGES.values()), state="readonly", width=16).grid(
        row=row, column=1, sticky="w", padx=10)
    row += 1
    tk.Label(pad, text=t("settings.language.note"), bg=C["surface"], fg=C["faint"], font=fonts["small"],
             wraplength=380, justify="left").grid(row=row, column=0, columnspan=3, sticky="w")
    row += 1

    current_category = None
    for cat, path, kind, unit, maximum in FIELDS:
        if cat != current_category:
            category(cat)
            current_category = cat
        label = t(f"settings.{path}")
        tk.Label(pad, text=label, bg=C["surface"], fg=C["text"], font=fonts["small"]).grid(row=row, column=0, sticky="w", pady=3)
        var = tk.StringVar(value=(f"{settings.get(path):g}" if kind in ("float", "float0", "int") else str(settings.get(path))))
        vars_[path] = var
        width = 34 if kind == "dir" else 10
        ttk.Entry(pad, textvariable=var, width=width).grid(row=row, column=1, sticky="w", padx=10)
        if unit:
            tk.Label(pad, text=unit, bg=C["surface"], fg=C["faint"], font=fonts["small"]).grid(row=row, column=2, sticky="w")
        if kind == "dir":
            ttk.Button(pad, text=t("settings.browse"), style="Soft.TButton", command=lambda v=var: v.set(
                filedialog.askdirectory(parent=win, initialdir=v.get()) or v.get())).grid(row=row, column=2, padx=6)
        row += 1
        if maximum and limits[maximum]:
            tk.Label(pad, text=t(f"settings.max.{maximum}", max=limits[maximum]), bg=C["surface"], fg=C["faint"],
                     font=fonts["small"]).grid(row=row, column=1, columnspan=2, sticky="w", padx=10)
            row += 1

    def save():
        values = {}
        for cat, path, kind, unit, maximum in FIELDS:
            text, label = vars_[path].get().strip(), t(f"settings.{path}")
            try:
                if kind in ("float", "float0", "int"):
                    value = float(text.replace(",", "."))
                    if value < 0 or (value == 0 and kind != "float0") or (kind == "int" and value != int(value)):
                        raise ValueError
                    if maximum and limits[maximum] and value > limits[maximum]:
                        raise OverflowError(t("settings.error.max", label=label, max=limits[maximum], unit=unit))
                    value = int(value) if kind == "int" else value
                elif kind == "text":
                    value = text
                    if path.endswith("delimiter") and len(text) != 1:
                        raise OverflowError(t("settings.error.separator"))
                    if path.endswith("decimal") and text not in (".", ","):
                        raise OverflowError(t("settings.error.decimal"))
                else:
                    value = text
                    if not text:
                        raise ValueError
            except ValueError:
                messagebox.showwarning(t("settings.title"), t("settings.error.number", label=label), parent=win)
                return
            except OverflowError as exc:
                messagebox.showwarning(t("settings.title"), str(exc), parent=win)
                return
            values[path] = value
        for path, value in values.items():
            settings.set(path, value)
        code = next((c for c, name in LANGUAGES.items() if name == language.get()), settings.app.language)
        changed_language = code != settings.app.language
        settings.app.language = code
        settings.save()
        app.acquisition.apply_settings()
        if changed_language:
            messagebox.showinfo(t("settings.restart.title"), t("settings.restart.message"), parent=win)
        win.destroy()

    buttons = tk.Frame(pad, bg=C["surface"])
    buttons.grid(row=row, column=0, columnspan=3, sticky="e", pady=(18, 0))
    ttk.Button(buttons, text=t("settings.close"), style="Soft.TButton", command=win.destroy).pack(side="left", padx=(0, 8))
    ttk.Button(buttons, text=t("settings.save"), style="Accent.TButton", command=save).pack(side="left")
    win.grab_set()
