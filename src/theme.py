"""Palette de l'interface."""

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

# Palette « viridis » simplifiée pour colorer les mesures selon leur valeur.
_STOPS = ((68, 1, 84), (59, 82, 139), (33, 145, 140), (94, 201, 98), (253, 231, 37))


def colormap(t):
    """t entre 0 et 1 -> couleur hexadécimale."""
    t = 0.0 if t != t else min(1.0, max(0.0, t))
    pos = t * (len(_STOPS) - 1)
    i = min(int(pos), len(_STOPS) - 2)
    f = pos - i
    r, g, b = (round(_STOPS[i][k] + (_STOPS[i + 1][k] - _STOPS[i][k]) * f) for k in range(3))
    return f"#{r:02x}{g:02x}{b:02x}"
