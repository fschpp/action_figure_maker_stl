"""Retoque manual del color de una pieza del catálogo: pinceladas en 3D con un rol de la paleta (`PLAN_retoque_color_pieza.md`, R01).

Una **pincelada** dice «las muestras de color que estén a menos de `r` mm de `c` pasan a tener el rol `rol`». Vive en el marco del STL de la
pieza (mm reales), así que sobrevive a cualquier cambio de la geometría al armar (cavidades, cortes) y se lleva al marco del huésped con la
misma 4×4 que el color. `rol = None` es el **borrador**: devuelve las muestras al rol que traía el paquete de roles.

`n` (normal de la superficie en el punto pintado) es opcional: con ella el pincel no atraviesa una solapa fina ni pinta la cara de atrás.

Sin PIL, sin scipy, sin GTK al importar (R3). Todo lo que sale a un informe es número, `str`, `bool`, lista o diccionario de eso (R5).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass

import numpy as np

FORMAT = "afm-retoque-1"
FORMAT_FILL = "afm-retoque-2"    # el mismo archivo pero con pinceladas de relleno; sin rellenos se sigue escribiendo `afm-retoque-1` (bytes idénticos)
FORMAT_ORPHAN = "afm-retoque-3"  # con pinceladas «sin origen» (`huerfana`); sin ellas se sigue escribiendo `-1` o `-2` (bytes idénticos)
FORMAT_TRI = "afm-retoque-4"     # con pinceladas de polígono (`tri`); sin ellas se sigue escribiendo `-1`, `-2` o `-3` (bytes idénticos)
FORMAT_REPLACE = "afm-retoque-5"  # con pinceladas de reemplazo de color (`desde`); sin ellas se sigue escribiendo `-1` … `-4` (bytes idénticos)
FORMATS = (FORMAT, FORMAT_FILL, FORMAT_ORPHAN, FORMAT_TRI, FORMAT_REPLACE)
FILL_KIND = "relleno"
ORPHAN_KIND = "huerfana"
REPLACE_KIND = "reemplazo"
MAX_STROKES = 20_000             # pinceladas por pieza
MIN_RADIUS_MM, MAX_RADIUS_MM = 0.05, 15.0
MIN_DOT = 0.3                    # el mismo criterio que el horneado (`bake_piece`): la normal de la muestra tiene que ser compatible con `n`
TRI_DEPTH_MM = 0.4               # pincelada de polígono: las muestras a esta distancia del plano del triángulo (a cualquier lado) cuentan como «del polígono»
TRI_EDGE_TOL = 0.02              # ...y su proyección puede salirse de los bordes esta fracción (coordenadas baricéntricas ≥ -tol)
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")         # igual que `palette.Role`


class RetouchError(ValueError):
    """El retoque no sirve (formato, límites, rol inválido); el mensaje dice cuál es el problema."""


def _vec3(x, what: str) -> tuple:
    try:
        v = tuple(float(a) for a in x)
    except (TypeError, ValueError) as exc:
        raise RetouchError(f"{what}: se esperaban tres números") from exc
    if len(v) != 3 or not all(np.isfinite(v)):
        raise RetouchError(f"{what}: se esperaban tres números finitos")
    return v


@dataclass(frozen=True)
class Stroke:
    c: tuple                                     # (x, y, z) mm, marco del STL de la pieza
    r: float                                     # radio, mm
    rol: str | None                              # id de rol de la paleta; None = borrador
    n: tuple | None = None                       # normal de la superficie (unitaria) o None
    relleno: bool = False                        # True = «rellenar región»: `c` es la semilla, `r` el alcance máximo (mm) y `rol` no puede ser None
    huerfana: bool = False                       # True = «sin origen»: pinta con `rol`, dentro del radio, SOLO lo que la fuente de color no cubre (sin muestra a ≤ eps con normal compatible); `rol` no puede ser None
    tri: tuple | None = None                     # «polígono entero»: los 3 vértices (marco del STL) de un triángulo de la pieza; pinta todo lo que cae sobre él (no una bola). `c` es su centro y `r` lo cubre
    desde: str | None = None                     # «reemplazar color de la pieza»: todo lo que tiene HOY el rol `desde` (en toda la pieza, sin importar `c` ni `r`) pasa a `rol`; `rol` no puede ser None ni igual a `desde`

    def __post_init__(self):
        object.__setattr__(self, "c", _vec3(self.c, "pincelada «c»"))
        r = float(self.r)
        if not np.isfinite(r) or not MIN_RADIUS_MM <= r <= MAX_RADIUS_MM:
            raise RetouchError(f"pincelada: el radio {r:g} mm tiene que estar entre {MIN_RADIUS_MM:g} y {MAX_RADIUS_MM:g}")
        object.__setattr__(self, "r", r)
        if self.rol is not None and not (isinstance(self.rol, str) and _ROLE_RE.match(self.rol)):
            raise RetouchError(f"pincelada: id de rol inválido {self.rol!r}")
        if self.relleno and self.rol is None:
            raise RetouchError("pincelada de relleno: hace falta un rol (el borrador es del pincel)")
        object.__setattr__(self, "relleno", bool(self.relleno))
        if self.huerfana and self.rol is None:
            raise RetouchError("pincelada «sin origen»: hace falta un rol (el borrador es del pincel)")
        if self.huerfana and self.relleno:
            raise RetouchError("una pincelada no puede ser a la vez de relleno y «sin origen»")
        object.__setattr__(self, "huerfana", bool(self.huerfana))
        if self.tri is not None:
            tri = tuple(_vec3(v, "pincelada «tri»") for v in self.tri) if len(self.tri) == 3 else None
            if tri is None:
                raise RetouchError("pincelada «tri»: se esperaban tres vértices")
            if self.relleno:
                raise RetouchError("una pincelada no puede ser a la vez de relleno y de polígono")
            if np.linalg.norm(np.cross(np.subtract(tri[1], tri[0]), np.subtract(tri[2], tri[0]))) < 1e-12:
                raise RetouchError("pincelada «tri»: el triángulo es degenerado")
            object.__setattr__(self, "tri", tri)
        if self.desde is not None:
            if not (isinstance(self.desde, str) and _ROLE_RE.match(self.desde)):
                raise RetouchError(f"reemplazo: id de rol inválido {self.desde!r}")
            if self.rol is None:
                raise RetouchError("reemplazo de color: hace falta el rol por el que se reemplaza")
            if self.rol == self.desde:
                raise RetouchError(f"reemplazo de color: el color a buscar y el nuevo son el mismo («{self.rol}»)")
            if self.relleno or self.huerfana or self.tri is not None or self.n is not None:
                raise RetouchError("un reemplazo de color no puede ser a la vez de relleno, «sin origen», de polígono ni llevar normal")
        if self.n is not None:
            n = np.asarray(_vec3(self.n, "pincelada «n»"))
            norm = float(np.linalg.norm(n))
            if norm < 1e-9:
                raise RetouchError("pincelada: la normal «n» es nula")
            object.__setattr__(self, "n", tuple(float(a) for a in n / norm))

    def to_dict(self) -> dict:
        d = {"c": [round(a, 4) for a in self.c], "r": round(self.r, 4), "rol": self.rol}
        d["n"] = None if self.n is None else [round(a, 4) for a in self.n]
        if self.desde is not None:
            d["k"] = REPLACE_KIND
            d["d"] = self.desde
        elif self.relleno:
            d["k"] = FILL_KIND
        elif self.huerfana:
            d["k"] = ORPHAN_KIND
        if self.tri is not None:
            d["t"] = [[round(a, 4) for a in v] for v in self.tri]
        return d

    @classmethod
    def from_dict(cls, d) -> "Stroke":
        if not isinstance(d, dict) or "c" not in d or "r" not in d:
            raise RetouchError("pincelada: faltan «c» o «r»")
        kind = d.get("k")
        if kind not in (None, FILL_KIND, ORPHAN_KIND, REPLACE_KIND):
            raise RetouchError(f"pincelada: tipo desconocido {kind!r}")
        if kind == REPLACE_KIND and d.get("d") is None:
            raise RetouchError("pincelada de reemplazo: falta «d» (el rol a buscar)")
        return cls(d["c"], d["r"], d.get("rol"), d.get("n"), kind == FILL_KIND, kind == ORPHAN_KIND, d.get("t"), d.get("d") if kind == REPLACE_KIND else None)


class Retouch:
    """Lista ordenada de pinceladas (la última gana)."""

    def __init__(self, strokes=()):
        self.strokes: list[Stroke] = [s if isinstance(s, Stroke) else Stroke.from_dict(s) for s in strokes]
        if len(self.strokes) > MAX_STROKES:
            raise RetouchError(f"{len(self.strokes)} pinceladas: el máximo por pieza es {MAX_STROKES}")

    def __len__(self) -> int:
        return len(self.strokes)

    def __eq__(self, other) -> bool:
        return isinstance(other, Retouch) and self.strokes == other.strokes

    def roles_used(self) -> list[str]:
        """Roles que alguna pincelada pinta (sin repetir, en orden de primera aparición; el borrador no cuenta). Un reemplazo de color suma además
        el rol que busca (la paleta tiene que tenerlo para poder reconocerlo)."""
        out = []
        for s in self.strokes:
            out.extend(r for r in (s.desde, s.rol) if r is not None)
        return list(dict.fromkeys(out))

    def mirrored(self, axis: int = 0) -> "Retouch":
        """El mismo retoque reflejado en el eje `axis` (0 = X) del marco del STL: de una pieza izquierda a su par derecho (`mirror_x`, como el motor)."""
        M = np.eye(4)
        M[axis, axis] = -1.0
        return Retouch(transform_strokes(self.strokes, M))

    def add(self, stroke: Stroke) -> None:
        if len(self.strokes) >= MAX_STROKES:
            raise RetouchError(f"el máximo por pieza es {MAX_STROKES} pinceladas")
        self.strokes.append(stroke)

    def to_dict(self) -> dict:
        fmt = FORMAT_REPLACE if any(s.desde is not None for s in self.strokes) else FORMAT_TRI if any(s.tri is not None for s in self.strokes) else FORMAT_ORPHAN if any(s.huerfana for s in self.strokes) else FORMAT_FILL if any(s.relleno for s in self.strokes) else FORMAT
        return {"formato": fmt, "pinceladas": [s.to_dict() for s in self.strokes]}

    @classmethod
    def from_dict(cls, d) -> "Retouch":
        if not isinstance(d, dict) or d.get("formato") not in FORMATS:
            raise RetouchError(f"formato desconocido: {d.get('formato') if isinstance(d, dict) else d!r} (se esperaba «{FORMAT}», «{FORMAT_FILL}», «{FORMAT_ORPHAN}», «{FORMAT_TRI}» o «{FORMAT_REPLACE}»)")
        items = d.get("pinceladas")
        if not isinstance(items, list):
            raise RetouchError("falta la lista «pinceladas»")
        rt = cls(items)
        if d["formato"] == FORMAT and any(s.relleno for s in rt.strokes):
            raise RetouchError(f"las pinceladas de relleno son del formato «{FORMAT_FILL}»")
        if d["formato"] not in (FORMAT_ORPHAN, FORMAT_TRI, FORMAT_REPLACE) and any(s.huerfana for s in rt.strokes):
            raise RetouchError(f"las pinceladas «sin origen» son del formato «{FORMAT_ORPHAN}»")
        if d["formato"] not in (FORMAT_TRI, FORMAT_REPLACE) and any(s.tri is not None for s in rt.strokes):
            raise RetouchError(f"las pinceladas de polígono son del formato «{FORMAT_TRI}»")
        if d["formato"] != FORMAT_REPLACE and any(s.desde is not None for s in rt.strokes):
            raise RetouchError(f"los reemplazos de color son del formato «{FORMAT_REPLACE}»")
        return rt

    def digest(self) -> str:
        """SHA-256 del contenido normalizado (orden de pinceladas incluido)."""
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ disco
    def save(self, path) -> str:
        """Escritura atómica (archivo temporal + `os.replace`); valida antes de escribir. Devuelve la ruta."""
        data = Retouch.from_dict(self.to_dict()).to_dict()          # re-valida: nadie dejó una pincelada rota
        path = os.fspath(path)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path) -> "Retouch":
        try:
            with open(os.fspath(path), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            raise RetouchError(f"{os.path.basename(os.fspath(path))}: no es un retoque legible ({exc})") from exc
        return cls.from_dict(data)


def transform_strokes(strokes, M) -> list[Stroke]:
    """Las pinceladas llevadas por la 4×4 `M` (marco de la pieza → otro marco): centro con `M`, radio por `∛|det|`, normal con la parte lineal
    (vale para giros, escala uniforme y espejo, que es lo que lleva el motor: `T`, `M·T`)."""
    M = np.asarray(M, float)
    if M.shape != (4, 4) or not np.all(np.isfinite(M)):
        raise RetouchError("la transformación tiene que ser una 4×4 finita")
    A = M[:3, :3]
    det = abs(float(np.linalg.det(A)))
    if det < 1e-12:
        raise RetouchError("la transformación es singular")
    k = float(np.cbrt(det))
    out = []
    for s in strokes:
        n = None if s.n is None else tuple(A @ np.asarray(s.n))          # `Stroke` la vuelve a normalizar
        # con una escala extrema el radio puede salirse del rango válido: se recorta en vez de fallar al armar
        r = float(np.clip(s.r * k, MIN_RADIUS_MM, MAX_RADIUS_MM))
        tri = None if s.tri is None else tuple(tuple(A @ np.asarray(v) + M[:3, 3]) for v in s.tri)
        out.append(Stroke(tuple(A @ np.asarray(s.c) + M[:3, 3]), r, s.rol, n, s.relleno, s.huerfana, tri, s.desde))
    return out


def triangle_of(tri, margin: float = 0.0) -> tuple:
    """`(centro, radio que cubre el triángulo + margin)` de `tri` (3 vértices): la bola donde buscar candidatos de una pincelada de polígono."""
    T = np.asarray(tri, float)
    c = T.mean(axis=0)
    return c, float(np.linalg.norm(T - c, axis=1).max()) + float(margin)


def in_triangle_prism(points, tri, depth: float = TRI_DEPTH_MM, tol: float = TRI_EDGE_TOL) -> np.ndarray:
    """Máscara de los `points` (N, 3) que caen sobre el triángulo `tri`: a ≤ `depth` mm de su plano (a cualquier lado) y con su proyección dentro
    del triángulo (coordenadas baricéntricas ≥ -`tol`). Es lo que una pincelada de polígono pinta."""
    P = np.atleast_2d(np.asarray(points, float))
    a, b, c = (np.asarray(v, float) for v in tri)
    e1, e2 = b - a, c - a
    nrm = np.cross(e1, e2)
    area2 = float(np.linalg.norm(nrm))
    if area2 < 1e-12 or len(P) == 0:
        return np.zeros(len(P), bool)
    nrm = nrm / area2
    rel = P - a
    dist = rel @ nrm
    d11, d12, d22 = e1 @ e1, e1 @ e2, e2 @ e2
    d1, d2 = rel @ e1, rel @ e2
    den = d11 * d22 - d12 * d12
    v = (d22 * d1 - d12 * d2) / den
    w = (d11 * d2 - d12 * d1) / den
    u = 1.0 - v - w
    return (np.abs(dist) <= depth) & (u >= -tol) & (v >= -tol) & (w >= -tol)
