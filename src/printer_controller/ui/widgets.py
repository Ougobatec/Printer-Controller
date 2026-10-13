"""Reusable interface components."""

import tkinter as tk
from tkinter import ttk

from .theme import C


class Tooltip:
    """Small help bubble shown after a short hover."""

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
    """Thin (4 px) progress bar."""

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
    """Vertically scrollable area.

    Widgets go in `.body`. The content fills at least the whole visible height
    ("expand" zones stay extensible); the scrollbar only appears on overflow.
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
    """Send the mouse wheel to the scrollable column under the pointer.

    Areas that already handle the wheel (3D view, tables, console) are left alone,
    and drop-down lists no longer change value by accident while scrolling the page.
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
            # A frame created later must not cover the active tab.
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

class Segmented(tk.Frame):
    """Exclusive choice shown as joined buttons."""

    def __init__(self, parent, options, variable, command=None, bg=None, width=None):
        super().__init__(parent, bg=bg or C["surface"])
        self.options = list(options)
        self.variable = variable
        self.command = command
        self._buttons = {}
        self._enabled = True
        for i, (value, label) in enumerate(self.options):
            button = ttk.Button(self, text=label, style="Seg.TButton", width=width or 0,
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
        self._refresh_states()
        self._restyle()

    def _refresh_states(self):
        for button in self._buttons.values():
            button.configure(state="normal" if self._enabled else "disabled")

    def _restyle(self):
        current = self.variable.get()
        for value, button in self._buttons.items():
            on = value == current
            button.configure(style="SegOn.TButton" if on else "Seg.TButton")


class HeaderButton(tk.Frame):
    """Header button with a coloured indicator (connection state) and a text."""

    def __init__(self, parent, text, font, command, bg=None):
        self._bg = bg or C["surface"]
        super().__init__(parent, bg=self._bg, highlightthickness=1,
                         highlightbackground=C["line"], cursor="hand2")
        self.dot = tk.Canvas(self, width=10, height=10, bg=self._bg, highlightthickness=0)
        self.dot.pack(side="left", padx=(12, 8), pady=9)
        self._item = self.dot.create_oval(1, 1, 9, 9, fill=C["faint"], outline="")
        self.label = tk.Label(self, text=text, bg=self._bg, fg=C["text"], font=font,
                              cursor="hand2")
        self.label.pack(side="left", padx=(0, 14))
        self._command = command
        for widget in (self, self.dot, self.label):
            widget.bind("<Button-1>", lambda _e: self._command())
            widget.bind("<Enter>", lambda _e: self._paint(C["soft"]))
            widget.bind("<Leave>", lambda _e: self._paint(self._bg))

    def _paint(self, color):
        for widget in (self, self.dot, self.label):
            widget.configure(bg=color)

    def set(self, color, text):
        self.dot.itemconfigure(self._item, fill=color)
        self.label.configure(text=text)

    def set_open(self, is_open):
        self.configure(highlightbackground=C["accent"] if is_open else C["line"])


class Popovers:
    """Frames shown under a header button; a click elsewhere closes them."""

    def __init__(self, root, width=400):
        self.root = root
        self.width = width
        self._items = {}
        self._open = None
        root.bind_all("<Button-1>", self._on_click, add="+")

    def add(self, name, button, build, width=None):
        """`build(frame)` fills the frame (created once, then only shown / hidden)."""
        frame = tk.Frame(self.root, bg=C["surface"], highlightthickness=1,
                         highlightbackground=C["line"])
        build(frame)
        self._items[name] = (button, frame, width or self.width)

    def toggle(self, name):
        was_open = self._open == name
        self.hide()
        if not was_open:
            self.show(name)

    def show(self, name):
        button, frame, width = self._items[name]
        self.root.update_idletasks()
        x = button.winfo_rootx() - self.root.winfo_rootx()
        y = button.winfo_rooty() - self.root.winfo_rooty() + button.winfo_height() + 6
        x = max(8, min(x, self.root.winfo_width() - width - 8))
        frame.place(x=x, y=y, width=width)
        frame.lift()
        button.set_open(True)
        self._open = name

    def hide(self):
        if self._open:
            button, frame, _ = self._items[self._open]
            frame.place_forget()
            button.set_open(False)
            self._open = None

    def _on_click(self, event):
        if not self._open:
            return
        button, frame, _ = self._items[self._open]
        path = str(event.widget)
        # clicks inside the frame, on its button, or in a drop-down list opened from it
        if path.startswith(str(frame)) or path.startswith(str(button)) or ".popdown" in path:
            return
        self.hide()
