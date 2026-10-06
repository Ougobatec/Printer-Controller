"""Composants d'interface réutilisables."""

import tkinter as tk
from tkinter import ttk

from theme import C


class Tooltip:
    """Petite bulle d'aide affichée après un court survol."""

    def __init__(self, widget, text, delay=550):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._job = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._hide()
        self._job = self.widget.after(self.delay, self._show)

    def _show(self):
        self._job = None
        if self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        tk.Label(tip, text=self.text, bg=C["text"], fg="white", padx=9, pady=5,
                 justify="left", wraplength=300).pack()
        self._tip = tip

    def _hide(self, _event=None):
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class ThinProgress(tk.Canvas):
    """Barre de progression fine (4 px)."""

    def __init__(self, parent, bg=None):
        super().__init__(parent, height=4, bg=C["soft"], highlightthickness=0)
        self._percent = 0.0
        self.bind("<Configure>", lambda _e: self._redraw())

    def set(self, percent):
        self._percent = max(0.0, min(100.0, float(percent)))
        self._redraw()

    def _redraw(self):
        self.delete("all")
        width = self.winfo_width()
        if self._percent > 0:
            self.create_rectangle(0, 0, width * self._percent / 100, 4,
                                  fill=C["accent"], outline="")


class ScrollColumn(tk.Frame):
    """Zone verticalement défilable.

    Les widgets se placent dans `.body`. Le contenu occupe au moins toute la
    hauteur visible (les zones « expand » restent donc extensibles) ; la barre
    de défilement n'apparaît que si le contenu dépasse.
    """

    def __init__(self, parent, bg=None):
        bg = bg or C["bg"]
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, width=1, height=1)
        self.bar = ttk.Scrollbar(self, orient="vertical", style="Thin.Vertical.TScrollbar",
                                 command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self._bar_on = False
        self.canvas.bind("<Configure>", self._layout)
        self.body.bind("<Configure>", self._layout)

    def _layout(self, _event=None):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 2 or height < 2:
            return
        needed = self.body.winfo_reqheight()
        total = max(needed, height)
        self.canvas.itemconfigure(self._window, width=width, height=total)
        self.canvas.configure(scrollregion=(0, 0, width, total))
        overflow = needed > height + 1
        if overflow and not self._bar_on:
            self.bar.pack(side="right", fill="y", padx=(4, 0))
            self._bar_on = True
        elif not overflow and self._bar_on:
            self.bar.pack_forget()
            self._bar_on = False
            self.canvas.yview_moveto(0)

    def scroll_units(self, units):
        if self._bar_on:
            self.canvas.yview_scroll(units, "units")


def install_wheel_router(root):
    """Envoie la molette à la colonne défilante située sous le pointeur.

    Les zones qui gèrent déjà la molette (vue 3D, tableaux, console) sont
    laissées tranquilles, et les listes déroulantes ne changent plus de
    valeur par accident quand on fait défiler la page.
    """
    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        root.unbind_class("TCombobox", sequence)

    def handler(event):
        try:
            widget = root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            return
        while widget is not None:
            if getattr(widget, "captures_wheel", False):
                return
            if isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview)):
                return
            if isinstance(widget, ScrollColumn):
                if getattr(event, "num", None) == 4:
                    units = -3
                elif getattr(event, "num", None) == 5:
                    units = 3
                else:
                    units = -3 if getattr(event, "delta", 0) > 0 else 3
                widget.scroll_units(units)
                return
            widget = getattr(widget, "master", None)

    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        root.bind_all(sequence, handler, add="+")


class TabBar(tk.Frame):
    """Onglets sobres : texte + filet de couleur sous l'onglet actif."""

    def __init__(self, parent, font, on_change=None):
        super().__init__(parent, bg=C["bg"])
        self._font = font
        self._on_change = on_change
        self._bar = tk.Frame(self, bg=C["bg"])
        self._bar.pack(fill="x")
        tk.Frame(self, bg=C["line"], height=1).pack(fill="x")
        self._holder = tk.Frame(self, bg=C["bg"])
        self._holder.pack(fill="both", expand=True, pady=(10, 0))
        self._holder.grid_rowconfigure(0, weight=1)
        self._holder.grid_columnconfigure(0, weight=1)
        self._tabs = {}
        self.current = None

    def add(self, key, text):
        frame = tk.Frame(self._holder, bg=C["bg"])
        frame.grid(row=0, column=0, sticky="nsew")
        cell = tk.Frame(self._bar, bg=C["bg"])
        cell.pack(side="left", padx=(0, 6))
        label = tk.Label(cell, text=text, bg=C["bg"], fg=C["muted"], font=self._font,
                         padx=14, pady=7, cursor="hand2")
        label.pack()
        line = tk.Frame(cell, bg=C["bg"], height=2)
        line.pack(fill="x")
        label.bind("<Button-1>", lambda _e, k=key: self.select(k))
        label.bind("<Enter>", lambda _e, k=key: self._hover(k, True))
        label.bind("<Leave>", lambda _e, k=key: self._hover(k, False))
        self._tabs[key] = {"label": label, "line": line, "frame": frame}
        if self.current is None:
            self.select(key)
        else:
            # Un cadre créé plus tard ne doit pas recouvrir l'onglet actif.
            self._tabs[self.current]["frame"].tkraise()
        return frame

    def _hover(self, key, inside):
        if key != self.current:
            self._tabs[key]["label"].configure(fg=C["text"] if inside else C["muted"])

    def select(self, key):
        for name, tab in self._tabs.items():
            active = name == key
            tab["label"].configure(fg=C["accent"] if active else C["muted"])
            tab["line"].configure(bg=C["accent"] if active else C["bg"])
        self._tabs[key]["frame"].tkraise()
        changed = key != self.current
        self.current = key
        if changed and self._on_change:
            self._on_change(key)

    def set_text(self, key, text):
        self._tabs[key]["label"].configure(text=text)


class Segmented(tk.Frame):
    """Choix exclusif présenté en boutons accolés."""

    def __init__(self, parent, options, variable, command=None, bg=None):
        super().__init__(parent, bg=bg or C["surface"])
        self.options = list(options)
        self.variable = variable
        self.command = command
        self._buttons = {}
        self._enabled = True
        for i, (value, label) in enumerate(self.options):
            button = ttk.Button(self, text=label, style="Seg.TButton",
                                command=lambda v=value: self._pick(v))
            button.pack(side="left", padx=(0 if i == 0 else 2, 0))
            self._buttons[value] = button
        self._restyle()

    def _pick(self, value):
        if not self._enabled:
            return
        self.variable.set(value)
        self._restyle()
        if self.command:
            self.command(value)

    def set(self, value):
        self.variable.set(value)
        self._restyle()

    def set_enabled(self, enabled):
        self._enabled = enabled
        for button in self._buttons.values():
            button.configure(state="normal" if enabled else "disabled")
        self._restyle()

    def _restyle(self):
        current = self.variable.get()
        for value, button in self._buttons.items():
            on = value == current
            button.configure(style="SegOn.TButton" if on else "Seg.TButton")
