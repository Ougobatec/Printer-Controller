"""Terminal: live exchanges with the printer, G-code input, hide / resize / clear."""

import tkinter as tk
from tkinter import ttk

from ..i18n import t
from .theme import C

MAX_LINES = 2000
HANDLE_HEIGHT = 32
MIN_HEIGHT, MAX_HEIGHT = 110, 600
TAG_COLORS = {"tx": "#7cc4ff", "rx": C["console_text"], "info": "#8b93a1", "error": "#ff8a80"}


class Console(tk.Frame):
    def __init__(self, parent, fonts, height=170, visible=True, on_send=None, on_resize=None):
        super().__init__(parent, bg=C["console"])
        self._on_send = on_send
        self._on_resize = on_resize
        self._height = max(MIN_HEIGHT, min(MAX_HEIGHT, int(height)))
        self._visible = visible
        self._history, self._history_pos = [], 0
        self._drag = None
        self.pack_propagate(False)

        # Top bar: title, buttons, and the handle used to resize
        self.handle = tk.Frame(self, bg=C["console"], height=HANDLE_HEIGHT, cursor="sb_v_double_arrow")
        self.handle.pack(fill="x")
        self.handle.pack_propagate(False)
        tk.Label(self.handle, text=t("console.title"), bg=C["console"], fg=C["console_text"],
                 font=fonts["bold"]).pack(side="left", padx=(14, 10))
        tk.Label(self.handle, text=t("console.subtitle"), bg=C["console"], fg="#5b616e",
                 font=fonts["small"]).pack(side="left")
        self.toggle_button = ttk.Button(self.handle, style="Console.TButton", command=self.toggle)
        self.toggle_button.pack(side="right", padx=(0, 10))
        ttk.Button(self.handle, text=t("console.clear"), style="Console.TButton",
                   command=self.clear).pack(side="right")
        for widget in (self.handle, *self.handle.winfo_children()[:2]):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        # Log + input line
        self.body = tk.Frame(self, bg=C["console"])
        self.text = tk.Text(self.body, bg=C["console"], fg=C["console_text"], font=fonts["mono"],
                            bd=0, highlightthickness=0, wrap="word", state="disabled",
                            padx=14, pady=4, cursor="arrow")
        bar = ttk.Scrollbar(self.body, orient="vertical", style="Dark.Vertical.TScrollbar",
                            command=self.text.yview)
        self.text.configure(yscrollcommand=bar.set)
        row = tk.Frame(self.body, bg=C["console"])
        row.pack(side="bottom", fill="x", padx=10, pady=(0, 8))
        tk.Label(row, text="›", bg=C["console"], fg="#5b8def", font=fonts["bold"]).pack(side="left")
        self.entry = tk.Entry(row, bg=C["console_input"], fg=C["console_text"], font=fonts["mono"],
                              insertbackground=C["console_text"], relief="flat",
                              highlightthickness=1, highlightbackground=C["console_line"],
                              highlightcolor="#5b8def")
        self.entry.pack(side="left", fill="x", expand=True, padx=8, ipady=5)
        self.send_button = ttk.Button(row, text=t("console.send"), style="Send.TButton",
                                      command=self._submit)
        self.send_button.pack(side="left")
        bar.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        for tag, color in TAG_COLORS.items():
            self.text.tag_configure(tag, foreground=color)
        self.entry.bind("<Return>", lambda _e: self._submit())
        self.entry.bind("<Up>", lambda _e: self._browse(-1))
        self.entry.bind("<Down>", lambda _e: self._browse(1))
        self.text.captures_wheel = True          # the wheel scrolls the log, not the page
        self._apply_visibility()

    # --- content ------------------------------------------------------------------
    def append(self, message, kind="info"):
        at_end = self.text.yview()[1] >= 0.999
        self.text.configure(state="normal")
        self.text.insert("end", message.rstrip("\n") + "\n", kind)
        extra = int(self.text.index("end-1c").split(".")[0]) - MAX_LINES
        if extra > 0:
            self.text.delete("1.0", f"{extra + 1}.0")
        self.text.configure(state="disabled")
        if at_end:
            self.text.see("end")

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _submit(self):
        command = self.entry.get().strip()
        if not command:
            return
        self._history.append(command)
        self._history_pos = len(self._history)
        self.entry.delete(0, "end")
        if self._on_send:
            self._on_send(command)

    def _browse(self, step):
        if not self._history:
            return
        self._history_pos = max(0, min(len(self._history), self._history_pos + step))
        self.entry.delete(0, "end")
        if self._history_pos < len(self._history):
            self.entry.insert(0, self._history[self._history_pos])

    # --- show / hide / resize ---------------------------------------------------------
    @property
    def visible(self):
        return self._visible

    @property
    def height(self):
        return self._height

    def toggle(self):
        self._visible = not self._visible
        self._apply_visibility()
        if self._on_resize:
            self._on_resize()

    def _apply_visibility(self):
        if self._visible:
            self.configure(height=self._height)
            self.body.pack(fill="both", expand=True)
            self.toggle_button.configure(text=t("console.hide"))
        else:
            self.body.pack_forget()
            self.configure(height=HANDLE_HEIGHT)
            self.toggle_button.configure(text=t("console.show"))

    def _drag_start(self, event):
        self._drag = (event.y_root, self._height)

    def _drag_move(self, event):
        if not self._visible or self._drag is None:
            return
        start_y, start_height = self._drag
        limit = max(MIN_HEIGHT, min(MAX_HEIGHT, self.winfo_toplevel().winfo_height() - 300))
        self._height = max(MIN_HEIGHT, min(limit, start_height + start_y - event.y_root))
        self.configure(height=self._height)
        if self._on_resize:
            self._on_resize()
