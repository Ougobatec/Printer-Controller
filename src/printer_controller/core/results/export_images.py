"""PNG export of the graphs (matplotlib is imported only here, when needed)."""

import math

from ...i18n import t

MAX_LABELS = 120          # above this, only some points get their coordinates written
VIEWS = ("time", "x", "y", "z", "3d")
COLORS = {"line": "#2563eb", "dot": "#4a5160"}


def view_to_angles(yaw, pitch):
    """Angles of the on-screen 3D view -> (azimuth, elevation) in degrees for matplotlib."""
    return math.degrees(math.atan2(-math.cos(yaw), -math.sin(yaw))), math.degrees(pitch)


def _figure(width, height):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    figure = Figure(figsize=(width, height), dpi=200, facecolor="white")
    FigureCanvasAgg(figure)
    return figure


def _subset(items, limit=MAX_LABELS):
    if len(items) <= limit:
        return items
    step = len(items) / limit
    return [items[int(i * step)] for i in range(limit)]


def _coordinates(m):
    def f(v):
        return "–" if v is None else f"{v:.1f}"
    return f"({f(m.x)} ; {f(m.y)} ; {f(m.z)})"


def _labelled(measurements):
    """Points to annotate: the measured points first (not each sample of a path), no duplicates."""
    chosen = [m for m in measurements if m.kind != "path"] or list(measurements)
    seen, out = set(), []
    for m in chosen:
        key = tuple(None if v is None else round(v, 2) for v in (m.x, m.y, m.z))
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def render_2d(path, measurements, view, unit, coordinates=True):
    """value = f(time | X | Y | Z). Returns (points annotated, points drawn)."""
    if not measurements:
        raise ValueError(t("graph.none"))
    t0 = measurements[0].timestamp

    def x_of(m):
        return m.timestamp - t0 if view == "time" else getattr(m, view)

    usable = [m for m in measurements if x_of(m) is not None and m.value == m.value]
    if not usable:
        raise ValueError(t("graph.no_coords"))
    figure = _figure(9, 4.6)
    ax = figure.add_subplot()
    paths = [m for m in usable if m.kind == "path"]
    points = [m for m in usable if m.kind != "path"]
    if paths:
        ax.plot([x_of(m) for m in paths], [m.value for m in paths], color=COLORS["line"], linewidth=1.2,
                marker="." if view != "time" else None, markersize=3)
    if points:
        ax.scatter([x_of(m) for m in points], [m.value for m in points], s=22, color=COLORS["dot"], zorder=3)
    ax.set_xlabel(t("graph.time.axis") if view == "time" else t("graph.pos.axis", axis=view.upper()))
    ax.set_ylabel(t("graph.value", unit=unit))
    ax.set_title(f"{t('graph.' + view)} — {t('graph.count', n=len(usable))}", fontsize=11)
    ax.grid(True, color="#e3e5ea", linewidth=0.6)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    drawn = 0
    if coordinates:
        for m in _subset(_labelled(points or paths)):
            ax.annotate(_coordinates(m), (x_of(m), m.value), xytext=(0, 5), textcoords="offset points",
                        ha="center", fontsize=4.5, color="#2b2f36")
            drawn += 1
    figure.tight_layout()
    figure.savefig(path, dpi=200)
    return drawn, len(usable)


def render_3d(path, measurements, unit, azim, elev, coordinates=True, colormap=None):
    """3D point cloud coloured by value. Axes X and Y are swapped like in the on-screen view."""
    points = [m for m in measurements if None not in (m.x, m.y, m.z) and m.value == m.value]
    if not points:
        raise ValueError(t("graph.no_coords"))
    shown = points[::max(1, len(points) // 20000)]
    figure = _figure(9, 7.2)
    ax = figure.add_subplot(projection="3d")
    h, d, z = [m.y for m in shown], [m.x for m in shown], [m.z for m in shown]
    scatter = ax.scatter(h, d, z, c=[m.value for m in shown], cmap=colormap or "viridis", s=22,
                         depthshade=False, edgecolors="none")
    figure.colorbar(scatter, ax=ax, shrink=0.6, pad=0.08).set_label(t("graph.value", unit=unit))
    spans = [max(v) - min(v) for v in (h, d, z)]
    floor = max(max(spans) * 0.05, 1.0)
    for setter, values, span in ((ax.set_xlim, h, spans[0]), (ax.set_ylim, d, spans[1]), (ax.set_zlim, z, spans[2])):
        middle, half = (max(values) + min(values)) / 2, max(span, floor) / 2
        setter(middle - half * 1.05, middle + half * 1.05)
    ax.set_box_aspect([max(s, floor) for s in spans])
    from matplotlib.ticker import MaxNLocator
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.set_major_locator(MaxNLocator(5))
    ax.set_xlabel("Y (mm)")
    ax.set_ylabel("X (mm)")
    ax.set_zlabel("Z (mm)")
    ax.view_init(elev=elev, azim=azim)
    ax.set_title(t("graph.3d.title", n=len(points)), fontsize=11)
    figure.text(0.01, 0.01, t("graph.view", azim=azim, elev=elev), fontsize=6, color="#888")
    drawn = 0
    if coordinates:
        for m in _subset(_labelled(points)):
            ax.text(m.y, m.x, m.z, _coordinates(m), fontsize=4.5, color="#2b2f36", zorder=10)
            drawn += 1
    figure.savefig(path, dpi=200, bbox_inches="tight")
    return drawn, len(points)
