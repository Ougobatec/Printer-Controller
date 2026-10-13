"""Look of the interface: palette, fonts and ttk styles."""

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

C = {
    "bg": "#f4f5f7",
    "surface": "#ffffff",
    "line": "#e4e6eb",
    "soft": "#f1f2f5",
    "soft_hover": "#e6e8ed",
    "soft_press": "#dcdfe5",
    "text": "#1b1d21",
    "muted": "#7a808b",
    "faint": "#b6bbc5",
    "accent": "#2563eb",
    "accent_hover": "#1d4fd0",
    "accent_press": "#1a43b3",
    "accent_soft": "#e6eeff",
    "danger": "#d92d20",
    "danger_hover": "#b42318",
    "danger_press": "#912018",
    "danger_soft": "#fdecea",
    "danger_soft_hover": "#fadad6",
    "ok": "#12a150",
    "ok_soft": "#e3f6ea",
    "warn": "#e8890c",
    "warn_soft": "#fff4e0",
    "grid": "#eceef2",
    "edge": "#d3d7de",
    "path": "#9db4ee",
    "axis_x": "#e5484d",
    "axis_y": "#30a46c",
    "axis_z": "#3e63dd",
    "console": "#14161a",
    "console_text": "#d7dae0",
    "console_input": "#1d2027",
    "console_line": "#2a2e37",
}

STATUS_COLORS = {
    "idle": C["faint"],
    "ok": C["ok"],
    "busy": C["warn"],
    "run": C["accent"],
    "error": C["danger"],
}

# Simplified "viridis" palette used to colour measurements by value.
_STOPS = ((68, 1, 84), (59, 82, 139), (33, 145, 140), (94, 201, 98), (253, 231, 37))


def colormap(t):
    """t between 0 and 1 -> hex colour."""
    t = 0.0 if t != t else min(1.0, max(0.0, t))
    pos = t * (len(_STOPS) - 1)
    i = min(int(pos), len(_STOPS) - 2)
    f = pos - i
    r, g, b = (round(_STOPS[i][k] + (_STOPS[i + 1][k] - _STOPS[i][k]) * f) for k in range(3))
    return f"#{r:02x}{g:02x}{b:02x}"


def make_fonts(root):
    """Fonts used by the whole interface (dict name -> tkfont.Font)."""
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
        tkfont.nametofont(name).configure(size=10)
    family = tkfont.nametofont("TkDefaultFont").actual("family")
    available = set(tkfont.families(root))
    mono = next((f for f in ("Cascadia Mono", "Consolas", "SF Mono", "Menlo",
                             "DejaVu Sans Mono", "Liberation Mono") if f in available),
                tkfont.nametofont("TkFixedFont").actual("family"))
    return {
        "base": tkfont.Font(family=family, size=10),
        "small": tkfont.Font(family=family, size=9),
        "section": tkfont.Font(family=family, size=8, weight="bold"),
        "bold": tkfont.Font(family=family, size=10, weight="bold"),
        "title": tkfont.Font(family=family, size=12, weight="bold"),
        "name": tkfont.Font(family=family, size=13, weight="bold"),
        "jog": tkfont.Font(family=family, size=11, weight="bold"),
        "mono": tkfont.Font(family=mono, size=9),
        "mono_big": tkfont.Font(family=mono, size=15, weight="bold"),
        "mono_huge": tkfont.Font(family=mono, size=22, weight="bold"),
    }


def install_styles(root, fonts):
    """Configure every ttk style used by the interface."""
    s = ttk.Style(root)
    try:
        s.theme_use("clam")
    except tk.TclError:
        pass
    F = fonts
    s.configure(".", background=C["surface"], foreground=C["text"], font=F["base"],
                borderwidth=0, focuscolor=C["surface"], focusthickness=0)

    def button(name, bg, fg, hover, press, dis_bg, dis_fg, pad=(14, 8), font=None):
        s.configure(name, background=bg, foreground=fg, bordercolor=bg, lightcolor=bg,
                    darkcolor=bg, focuscolor=bg, focusthickness=0, relief="flat",
                    borderwidth=0, padding=pad, font=font or F["base"], anchor="center")
        for key in ("background", "bordercolor", "lightcolor", "darkcolor"):
            s.map(name, **{key: [("disabled", dis_bg), ("pressed", press), ("active", hover)]})
        s.map(name, foreground=[("disabled", dis_fg)])

    soft = (C["soft"], C["text"], C["soft_hover"], C["soft_press"], C["soft"], C["faint"])
    button("TButton", *soft)
    button("Soft.TButton", *soft)
    button("Accent.TButton", C["accent"], "white", C["accent_hover"], C["accent_press"],
           "#b9cbf7", "white", pad=(14, 10), font=F["bold"])
    button("Danger.TButton", C["danger"], "white", C["danger_hover"], C["danger_press"],
           "#f1b9b4", "white", pad=(14, 10), font=F["bold"])
    button("DangerSoft.TButton", C["danger_soft"], C["danger"], C["danger_soft_hover"],
           "#f6c4be", C["soft"], C["faint"], pad=(14, 10), font=F["bold"])
    button("Ghost.TButton", C["surface"], C["muted"], C["soft"], C["soft_hover"],
           C["surface"], C["faint"], pad=(10, 6), font=F["small"])
    button("Jog.TButton", C["soft"], C["text"], C["soft_hover"], C["accent_soft"],
           C["soft"], C["faint"], pad=(0, 10), font=F["jog"])
    button("Home.TButton", C["accent_soft"], C["accent"], "#d7e4ff", "#c8daff",
           C["soft"], C["faint"], pad=(0, 10), font=F["bold"])
    button("Tool.TButton", *soft, pad=(8, 7))
    button("Chip.TButton", C["soft"], C["muted"], C["soft_hover"], C["soft_press"],
           C["soft"], C["faint"], pad=(5, 4), font=F["small"])
    button("ChipOn.TButton", C["accent_soft"], C["accent"], C["accent_soft"], C["accent_soft"],
           C["accent_soft"], C["faint"], pad=(5, 4), font=F["bold"])
    button("Mini.TButton", C["soft"], C["text"], C["soft_hover"], C["soft_press"],
           C["soft"], C["faint"], pad=(0, 6), font=F["bold"])
    button("Seg.TButton", C["soft"], C["muted"], C["soft_hover"], C["soft_press"],
           C["soft"], C["faint"], pad=(12, 6), font=F["base"])
    button("SegOn.TButton", C["accent"], "white", C["accent_hover"], C["accent_press"],
           "#b9cbf7", "white", pad=(12, 6), font=F["bold"])
    button("Console.TButton", C["console"], C["console_text"], C["console_line"],
           C["console_line"], C["console"], "#5b616e", pad=(10, 4), font=F["small"])
    button("Send.TButton", C["accent"], "white", C["accent_hover"], C["accent_press"],
           "#2b3a66", "#7f8bb0", pad=(14, 5), font=F["bold"])

    field = dict(fieldbackground=C["surface"], foreground=C["text"], bordercolor=C["line"],
                 lightcolor=C["line"], darkcolor=C["line"], insertcolor=C["text"],
                 padding=(8, 6))
    focus = dict(bordercolor=[("focus", C["accent"])], lightcolor=[("focus", C["accent"])],
                 darkcolor=[("focus", C["accent"])],
                 fieldbackground=[("disabled", C["soft"])], foreground=[("disabled", C["faint"])])
    for name in ("TEntry", "TSpinbox"):
        s.configure(name, **field)
        s.map(name, **focus)
    s.configure("TCombobox", **field, background=C["soft"], arrowcolor=C["muted"],
                selectbackground=C["surface"], selectforeground=C["text"])
    s.map("TCombobox", **focus, background=[("active", C["soft_hover"])],
          arrowcolor=[("disabled", C["faint"])])
    root.option_add("*TCombobox*Listbox.font", F["base"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent_soft"])
    root.option_add("*TCombobox*Listbox.selectForeground", C["text"])

    s.configure("TCheckbutton", background=C["surface"], foreground=C["text"],
                font=F["base"], focuscolor=C["surface"])
    s.map("TCheckbutton", background=[("active", C["surface"])],
          foreground=[("disabled", C["faint"])])

    s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    s.configure("Treeview", background=C["surface"], fieldbackground=C["surface"],
                foreground=C["text"], rowheight=30, borderwidth=0, font=F["base"])
    s.map("Treeview", background=[("selected", C["accent_soft"])],
          foreground=[("selected", C["text"])])
    s.configure("Treeview.Heading", background=C["surface"], foreground=C["muted"],
                font=F["section"], relief="flat", borderwidth=1, bordercolor=C["line"],
                lightcolor=C["surface"], darkcolor=C["surface"], padding=(6, 8))
    s.map("Treeview.Heading", background=[("active", C["surface"])])

    for name, thumb, trough in (("Thin", C["faint"], C["bg"]),
                                ("Dark", C["console_line"], C["console"])):
        style = f"{name}.Vertical.TScrollbar"
        s.layout(style, [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        s.configure(style, background=thumb, troughcolor=trough, bordercolor=trough,
                    lightcolor=thumb, darkcolor=thumb, width=5, gripcount=0)
        s.map(style, background=[("active", C["muted"])])


def install_theme(root):
    """Fonts + styles; returns the fonts dict."""
    fonts = make_fonts(root)
    install_styles(root, fonts)
    return fonts
