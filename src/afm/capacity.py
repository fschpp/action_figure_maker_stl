"""Capacidad del anfitrión: cuánto cuerpo hay detrás de la cara de una toma para alojar el pasador (plan de compatibilidad, Fase 3, §4.4).

`measure` mide, sobre la malla del torso o la pelvis, la profundidad disponible desde la cara hacia adentro (rayos sobre
la huella de la cabeza del pasador) y, en la cadera, dónde queda la cabeza respecto del plano de simetría. `demand` dice
cuánto pide el pasador de la línea. `check` compara las dos y devuelve motivos («no»), avisos y márgenes para `ports.port_compat`.

Profundidad: el pasador entra `shaft_len + head_h` desde la cara; la cavidad lleva `clearance` de holgura y detrás tiene
que quedar `min_wall` de piel. Solo cuentan los rayos que arrancan al menos `min_wall` adentro del borde de la cara: cerca del
borde la piel se curva y el rayo sale por el costado, no por la piel opuesta; eso (el pasador que sobresale de la piel) ya lo
dice el margen del pasador de `port_compat`.

Cabezas enfrentadas (solo la cadera, como `pipeline._validate_hip_pin`): la cabeza de este lado y la espejada no pueden
cruzarse. Con `dir_x` (hacia dónde entra el pasador: + si se aleja del plano de simetría) se calcula el borde interno de la
cabeza igual que `pipeline.hip_pin_walls` y se exige `separación ≥ 2·holgura`.

La demanda se calcula siempre desde los `Params` de la línea (no se guarda en las piezas: cambiar el pasador no deja datos viejos).
La capacidad sí se guarda en el puerto de la toma (clave opcional `capacidad` de `afm-pieza-1`), con la huella con que se midió.
"""
from __future__ import annotations

import numpy as np

from .params import Params
from .ports import COMPLEMENT, PIN_OF_CLASS, Port

MIRROR_CLASSES = ("cadera",)                 # las que el motor valida por cabezas enfrentadas (`_validate_hip_pin`)
CAP_WARN_MM = 0.5                            # margen por debajo del cual se avisa (plan §4.4: «hasta 0,5 mm de margen, aviso»)
RAY_EPS_MM = 0.02                            # los rayos arrancan este tanto adentro del cuerpo, para no empezar sobre la cara
MAX_RAYS = 61


def pin_of(clase: str, params: Params):
    attr = PIN_OF_CLASS.get(clase)
    return getattr(params, attr) if attr else None


def demand(clase: str, params: Params) -> dict | None:
    """Lo que el pasador de la línea le pide al anfitrión (None si la clase no lleva pasador: muñeca, rodilla, codo)."""
    pin = pin_of(clase, params)
    if pin is None:
        return None
    depth = float(pin.shaft_len) + float(pin.head_h) + float(params.clearance)
    d = {"profundidad_mm": depth, "pared_min_mm": float(params.min_wall), "huella_radio_mm": float(pin.head_d) / 2}
    if clase in MIRROR_CLASSES:
        d["separacion_min_mm"] = 2.0 * float(params.clearance)
    return d


def head_gap(pin, x: float, dir_x: float) -> float:
    """Separación entre la cabeza de este lado y su espejo (mm; negativa si se cruzan): 2 · borde interno de la cabeza.
    Misma cuenta que `pipeline.hip_pin_walls` con `dir_x` = componente en X de la dirección en que entra el pasador (+ si se
    aleja del plano de simetría) y `x` = |x| del centro del puerto."""
    half_x = float(pin.head_d) / 2 * float(np.sqrt(max(0.0, 1.0 - dir_x ** 2)))
    base = x + dir_x * float(pin.shaft_len) - half_x
    tip = x + dir_x * (float(pin.shaft_len) + float(pin.head_h)) - half_x
    return 2.0 * min(base, tip)


# ---------------------------------------------------------------------------------------------------- medir
def _footprint(face, radius: float, margin: float = 0.0, n_rings: int = 4, n_ang: int = 12) -> np.ndarray:
    """Puntos (u, v) de la huella del pasador (disco de radio `radius` alrededor del centro) que caen dentro de la cara,
    a más de `margin` mm de su borde. Si no queda ninguno (cara chica) se usa el centro del puerto."""
    from shapely.geometry import Point

    pts = [(0.0, 0.0)]
    for i in range(1, n_rings + 1):
        r = radius * i / n_rings
        for k in range(n_ang):
            a = 2 * np.pi * (k + 0.5 * (i % 2)) / n_ang
            pts.append((r * np.cos(a), r * np.sin(a)))
    pts = np.asarray(pts)[:MAX_RAYS]
    if face is None:
        return pts[:1]
    inner = face.shape().buffer(-margin) if margin > 0 else face.shape().buffer(0.05)
    if inner.is_empty:
        return pts[:1]
    keep = [i for i, (u, v) in enumerate(pts) if inner.contains(Point(u, v))]
    return pts[keep] if keep else pts[:1]


def _depths(mesh, origins: np.ndarray, direction: np.ndarray) -> list[float]:
    """Distancia, desde cada origen (sobre la cara), hasta la primera salida del cuerpo en `direction`. Los rayos que no
    cruzan el cuerpo se ignoran. Usa `pipeline._ray_hits` (Möller–Trumbore, sin rtree ni embree)."""
    from .pipeline import _ray_hits

    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    tris = np.asarray(mesh.triangles, float)
    out = []
    for o in origins:
        t = _ray_hits(tris, o + d * RAY_EPS_MM, d)
        if len(t) == 0:
            continue
        # el rayo arranca adentro del cuerpo: la 1.ª intersección es la salida (con un nº impar de cruces; con par, el
        # origen quedó afuera, y entonces esa huella no apoya en el cuerpo y no cuenta)
        if len(t) % 2 == 1:
            out.append(float(t[0]) + RAY_EPS_MM)
    return out


def measure(mesh, port: Port, params: Params) -> dict | None:
    """Capacidad de una toma sobre la malla (trimesh) que la recibe, en el marco de la malla.

    `profundidad_mm`: el menor camino, a lo largo de −eje (hacia adentro del cuerpo), de la cara a la piel opuesta, sobre la
    huella de la cabeza del pasador (sin los puntos a menos de `min_wall` del borde de la cara). En la cadera, además, `x_mm`
    (|x| del centro) y `dir_x` (X de la dirección de entrada del pasador, + si se aleja del plano de simetría).
    None si la clase no lleva pasador, no es una toma o ningún rayo atraviesa el cuerpo."""
    pin = pin_of(port.clase, params)
    if pin is None or port.rol != "toma":
        return None
    radius = float(pin.head_d) / 2
    uv = _footprint(port.face, radius, float(params.min_wall))
    fr = port.frame
    origins = fr.center + np.outer(uv[:, 0], fr.ref) + np.outer(uv[:, 1], fr.y)
    depths = _depths(mesh, origins, -fr.axis)
    if not depths:
        return None
    cap = {"profundidad_mm": round(min(depths), 3), "huella_radio_mm": round(radius, 3), "rayos": len(depths)}
    if port.clase in MIRROR_CLASSES and abs(float(fr.center[0])) > 1e-6:
        d = -np.asarray(fr.axis, float) / np.linalg.norm(fr.axis)
        cap["x_mm"] = round(abs(float(fr.center[0])), 3)
        cap["dir_x"] = round(float(d[0]) * float(np.sign(fr.center[0])), 4)
    return cap


# ---------------------------------------------------------------------------------------------------- comparar
def check(a: Port, b: Port, params: Params) -> tuple[list, list, dict]:
    """(motivos, avisos, márgenes) de la capacidad del par enchufe–toma. Sin par válido (clases o roles que no se acoplan,
    clase sin pasador) devuelve todo vacío: eso ya lo dice `port_compat`."""
    if a.clase != b.clase or COMPLEMENT.get(a.rol) != b.rol or "toma" not in (a.rol, b.rol):
        return [], [], {}
    sock = a if a.rol == "toma" else b
    dem = demand(sock.clase, params)
    if dem is None:
        return [], [], {}
    cap = sock.capacity
    if not cap:
        return [], [], {}              # sin dato no se juzga (ver `missing`): un catálogo viejo no baja de nivel de golpe
    motivos, avisos, marg = [], [], {}
    need = dem["profundidad_mm"] + dem["pared_min_mm"]
    m = float(cap["profundidad_mm"]) - need
    marg["capacidad_profundidad"] = m
    if m < 0:
        motivos.append(f"el cuerpo deja {cap['profundidad_mm']:.2f} mm detrás de la cara y el pasador pide {need:.2f} mm "
                       f"({dem['profundidad_mm']:.2f} de pasador y holgura + {dem['pared_min_mm']:g} de pared)")
    elif m < CAP_WARN_MM:
        avisos.append(f"poca profundidad detrás de la cara: sobran {m:.2f} mm (aviso por debajo de {CAP_WARN_MM:g} mm)")
    if "separacion_min_mm" in dem and cap.get("x_mm") is not None:
        gap = head_gap(pin_of(sock.clase, params), float(cap["x_mm"]), float(cap.get("dir_x", 0.0)))
        ms = gap - dem["separacion_min_mm"]
        marg["capacidad_cabezas"] = ms
        if ms < 0:
            motivos.append(f"las cabezas de los dos lados quedan a {gap:.2f} mm (hace falta {dem['separacion_min_mm']:.2f} mm = 2·holgura)")
        elif ms < CAP_WARN_MM:
            avisos.append(f"cabezas casi tocándose en el centro: sobran {ms:.2f} mm (aviso por debajo de {CAP_WARN_MM:g} mm)")
    if float(cap.get("huella_radio_mm", 0.0)) < dem["huella_radio_mm"] - 1e-6:
        avisos.append(f"la capacidad se midió con una cabeza de Ø{2 * float(cap['huella_radio_mm']):.1f} mm y la línea usa "
                      f"Ø{2 * dem['huella_radio_mm']:.1f} mm")
    return motivos, avisos, marg


def missing(a: Port, b: Port, params: Params) -> bool:
    """True si el par enchufe–toma debería llevar capacidad (la clase lleva pasador) y la toma no la trae. No cambia el
    nivel: queda como dato (`Compat.sin_capacidad`) para decirlo en los informes (plan §4.4: piezas viejas sin datos)."""
    if a.clase != b.clase or COMPLEMENT.get(a.rol) != b.rol or "toma" not in (a.rol, b.rol):
        return False
    sock = a if a.rol == "toma" else b
    return demand(sock.clase, params) is not None and not sock.capacity
