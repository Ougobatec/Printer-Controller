"""Reading and writing programs (JSON, version 2) and conversion of the old files."""

import json

from .model import AVERAGE, COUNT, DURATION, KINDS, NONE, PATH, SIMPLE, Measure, Point, Program

VERSION = 2


class ProgramFileError(Exception):
    pass


def _number(value, default=None):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProgramFileError(f"number expected: {value!r}")
    return float(value)


def point_to_dict(point):
    data = {key: getattr(point, key) for key in ("x", "y", "z") if getattr(point, key) is not None}
    data["speed"], data["pause"] = point.speed, point.pause
    m = point.measure
    measure = {"kind": m.kind}
    if m.kind == AVERAGE:
        measure["by"] = m.by
        measure["count" if m.by == COUNT else "duration_s"] = m.count if m.by == COUNT else m.duration_s
    # PATH deliberately stores no sample count or frequency; both are program-wide.
    data["measure"] = measure
    return data


def point_from_dict(data):
    if not isinstance(data, dict):
        raise ProgramFileError("point expected")
    m = data.get("measure") or {}
    kind = m.get("kind", NONE)
    if kind not in KINDS:
        raise ProgramFileError(f"unknown measurement kind: {kind!r}")
    default = Measure()
    measure = Measure(kind=kind, by=(m.get("by", DURATION) if kind == AVERAGE else DURATION),
                      duration_s=_number(m.get("duration_s"), default.duration_s),
                      count=int(_number(m.get("count"), default.count)))
    return Point(x=_number(data.get("x")), y=_number(data.get("y")), z=_number(data.get("z")),
                 speed=_number(data.get("speed"), 300.0), pause=_number(data.get("pause"), 0.0), measure=measure)


def to_dict(program):
    return {"version": VERSION, "name": program.name, "points": [point_to_dict(p) for p in program.points]}


def from_dict(data):
    if not isinstance(data, dict):
        raise ProgramFileError("invalid program file")
    if data.get("version") != VERSION:
        data = _convert_legacy(data)
    points = data.get("points")
    if not isinstance(points, list):
        raise ProgramFileError("'points' must be a list")
    program = Program(str(data.get("name", "")), [point_from_dict(p) for p in points])
    program.normalize()
    return program


def _convert_legacy(old):
    """Files of the first version: French keys, modes point / continu / parcours."""
    positions = old.get("positions")
    if not isinstance(positions, list):
        raise ProgramFileError("invalid program file")
    route = old.get("parcours") or {} if old.get("mode") == "parcours" else None
    points = []
    for i, p in enumerate(positions):
        if not isinstance(p, dict):
            raise ProgramFileError("point expected")
        # Legacy files may contain per-point rates; they are intentionally ignored now.
        old_kind = p.get("mesure", "aucune")
        n = int(_number(p.get("mesure_n"), 5))
        if route is not None:
            measure = {"kind": PATH} if route.get("mesure", True) and i > 0 else {"kind": NONE}
        elif old_kind == "simple" or (old_kind == "point" and n == 1):
            measure = {"kind": SIMPLE}
        elif old_kind in ("point", "moyenne") and (old_kind == "point" or p.get("mesure_par") == "nombre"):
            measure = {"kind": AVERAGE, "by": COUNT, "count": n}
        elif old_kind in ("continu", "moyenne"):
            measure = {"kind": AVERAGE, "by": DURATION, "duration_s": _number(p.get("mesure_duree"), 2.0)}
        elif old_kind == "trajet":
            measure = {"kind": PATH}
        else:
            measure = {"kind": NONE}
        speed = p.get("vitesse", (route or {}).get("vitesse", 300.0))
        points.append({"x": p.get("x"), "y": p.get("y"), "z": p.get("z"), "speed": speed,
                       "pause": p.get("attente", 0.0), "measure": measure})
    return {"version": VERSION, "name": old.get("nom", ""), "points": points}


def save(program, path):
    program.normalize()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(to_dict(program), f, indent=2, ensure_ascii=False)
        f.write("\n")


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return from_dict(json.load(f))
    except ValueError as exc:
        raise ProgramFileError(f"invalid JSON: {exc}") from None
