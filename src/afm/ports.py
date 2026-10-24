"""Puertos: la interfaz explícita entre dos piezas (plan de piezas intercambiables, §3.1).

Generaliza `hand.WristFrame` (que queda como está: nada del motor actual depende de este módulo). Un puerto es el
marco en el que una pieza se acopla con su vecina:

* `center`: centroide de la cara de unión.
* `axis`: normal unitaria de la cara, **saliendo de la pieza hacia su vecina** (como `ContactFace.outward`).
  Dos puertos se acoplan con `axisA = −axisB`, el mismo centro y la misma referencia de giro.
* `ref`: dirección en el plano de la cara que fija el giro (en la mano, el punto 1 = lado del pulgar).
* Marco local del puerto: +X = `ref`, +Z = `axis`, +Y = Z × X (derecho).

Además del marco el puerto lleva la CARA de unión como polígonos en coordenadas (u, v) del propio puerto (u = ref,
v = axis × ref), su área y su radio equivalente, el rol (enchufe / toma / soldado), la clase de pasador y, si la pieza
lo declara, el barrido angular.

La compatibilidad barata (`port_compat`) se calcula solo con estos metadatos, sin cargar mallas.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .hand import STEP_WARN_MM, WristFrame
from .head import MIN_BASE_IOU
from .params import Params, PinSpec

PORT_CLASSES = ("cuello", "hombro", "cadera", "cintura", "muneca", "codo", "rodilla")
ROLES = ("enchufe", "toma", "soldado")
FACE_TOL = 1e-3                 # mm: distancia al plano para considerar un triángulo parte de la cara del puerto
HEIGHT_TOL_MM = 0.5             # diferencia de altura de figura a partir de la cual se avisa
# Clase de puerto -> atributo de `Params` con el pasador que lleva (muñeca: el pasador se achica solo, ver hand.fit_hand_pin)
PIN_OF_CLASS = {"cuello": "neck_pin", "hombro": "shoulder_pin", "cadera": "hip_pin", "cintura": "waist_pin"}
COMPLEMENT = {"enchufe": "toma", "toma": "enchufe", "soldado": "soldado"}


class PortError(ValueError):
    """El puerto no sirve (puntos alineados, el plano no cae sobre una cara plana de la pieza…).

    Cuando el error es de un plano que no deja cara plana, lleva además `place` (a qué lugar pertenece el plano:
    «cuello», «cintura», «muneca» o el nombre del miembro) y `diag` (resultado de `diagnose_plane`) para que la
    interfaz pinte ESE plano y diga cuánto está girado o corrido."""

    def __init__(self, message: str = "", place: str | None = None, diag: dict | None = None):
        super().__init__(message)
        self.place = place
        self.diag = diag


def port_class(name: str) -> str:
    """Clase de un puerto a partir de su nombre (`hombro_izq` → `hombro`)."""
    cls = name.split("_")[0]
    if cls not in PORT_CLASSES:
        raise PortError(f"puerto «{name}»: la clase «{cls}» no existe (hay {', '.join(PORT_CLASSES)})")
    return cls


@dataclass
class PortFrame:
    """Marco de un puerto: centro, eje (saliendo de la pieza) y referencia de giro."""

    center: np.ndarray
    axis: np.ndarray
    ref: np.ndarray

    def __post_init__(self):
        self.center = np.asarray(self.center, float)
        self.axis = np.asarray(self.axis, float)
        self.ref = np.asarray(self.ref, float)

    @classmethod
    def from_points(cls, points, piece_centroid=None) -> "PortFrame":
        """Marco a partir de los 3 puntos elegidos sobre la pieza. El punto 1 fija `ref`.

        Si se da `piece_centroid`, el eje apunta hacia afuera de la pieza (el centroide queda del lado negativo);
        si no, queda el sentido de (p1 − p0) × (p2 − p0)."""
        w = WristFrame.from_points(points)            # reutiliza validaciones y la referencia de giro de la mano
        axis = w.axis
        if piece_centroid is not None and (np.asarray(piece_centroid, float) - w.center) @ axis > 0:
            axis = -axis
        return cls(w.center, axis, w.ref)

    @classmethod
    def from_wrist(cls, wrist: WristFrame, toward_hand: bool = True) -> "PortFrame":
        """Puerto `muneca` de un `WristFrame` (eje hacia la mano). `toward_hand=True`: puerto del BRAZO (sale hacia la
        mano); `False`: puerto de la MANO (sale hacia el brazo)."""
        return cls(wrist.center.copy(), wrist.axis.copy() * (1.0 if toward_hand else -1.0), wrist.ref.copy())

    @classmethod
    def hand_standard(cls) -> "PortFrame":
        """Puerto de la mano estándar guardada (`afm-mano-1`): origen en la muñeca, dedos hacia +Z ⇒ sale hacia −Z."""
        return cls(np.zeros(3), np.array([0.0, 0.0, -1.0]), np.array([1.0, 0.0, 0.0]))

    def with_center(self, center) -> "PortFrame":
        return PortFrame(np.asarray(center, float).copy(), self.axis.copy(), self.ref.copy())

    @property
    def y(self) -> np.ndarray:
        return np.cross(self.axis, self.ref)

    def matrix(self) -> np.ndarray:
        """4×4 local del puerto → mundo (columnas: ref, y, axis, center)."""
        m = np.eye(4)
        m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = self.ref, self.y, self.axis, self.center
        return m

    def to_uv(self, pts) -> np.ndarray:
        """Coordenadas (u, v) de puntos del mundo sobre el plano del puerto (origen en `center`)."""
        d = np.atleast_2d(np.asarray(pts, float)) - self.center
        return np.stack([d @ self.ref, d @ self.y], axis=1)

    def transformed(self, T: np.ndarray) -> "PortFrame":
        """El mismo marco llevado por una transformación rígida 4×4 (sin escala)."""
        R, t = T[:3, :3], T[:3, 3]
        return PortFrame(R @ self.center + t, R @ self.axis, R @ self.ref)

    def to_dict(self) -> dict:
        return {"centro": _r(self.center), "eje": _r(self.axis), "ref": _r(self.ref)}

    @classmethod
    def from_dict(cls, d: dict) -> "PortFrame":
        return cls(d["centro"], d["eje"], d["ref"])


def coupling_transform(piece: PortFrame, target: PortFrame) -> np.ndarray:
    """Transformación rígida (4×4, sin escala) que lleva el puerto `piece` de una pieza al puerto `target` de su vecina:
    mismo centro, misma referencia de giro y ejes opuestos (`axis' = −target.axis`)."""
    mt = np.eye(4)
    mt[:3, 0], mt[:3, 1], mt[:3, 2], mt[:3, 3] = target.ref, -target.y, -target.axis, target.center
    return mt @ np.linalg.inv(piece.matrix())


@dataclass
class PortFace:
    """Cara de unión: polígonos en (u, v) del puerto (el contorno exterior y los agujeros)."""

    polygons: list                      # [{"exterior": [[u, v], …], "interiors": [[[u, v], …], …]}, …]
    area: float
    radius_eq: float

    @classmethod
    def from_shape(cls, shape) -> "PortFace":
        polys = [q for q in (shape.geoms if hasattr(shape, "geoms") else [shape])
                 if q.geom_type == "Polygon" and not q.is_empty and q.area > 1e-9]
        if not polys:
            raise PortError("la cara del puerto está vacía")
        polys.sort(key=lambda q: -q.area)
        out = [{"exterior": _r(np.asarray(q.exterior.coords)[:-1]),
                "interiors": [_r(np.asarray(r.coords)[:-1]) for r in q.interiors]} for q in polys]
        area = float(sum(q.area for q in polys))
        return cls(out, area, float(np.sqrt(area / np.pi)))

    def shape(self):
        """La cara como geometría shapely en (u, v)."""
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
        return unary_union([Polygon(p["exterior"], p["interiors"]).buffer(0) for p in self.polygons])

    def to_dict(self) -> dict:
        return {"area_mm2": round(self.area, 4), "radio_eq_mm": round(self.radius_eq, 4), "poligonos": self.polygons}

    @classmethod
    def from_dict(cls, d: dict) -> "PortFace":
        return cls(d["poligonos"], float(d["area_mm2"]), float(d["radio_eq_mm"]))


@dataclass
class Port:
    clase: str
    rol: str
    frame: PortFrame
    face: PortFace | None = None
    sweep_deg: tuple | None = None     # (mín, máx) de giro libre que el miembro declara (cadera: 0…sit_angle + hip_margin)

    def __post_init__(self):
        if self.clase not in PORT_CLASSES:
            raise PortError(f"clase de puerto desconocida: {self.clase!r}")
        if self.rol not in ROLES:
            raise PortError(f"rol de puerto desconocido: {self.rol!r} (hay {', '.join(ROLES)})")

    def transformed(self, T: np.ndarray) -> "Port":
        return Port(self.clase, self.rol, self.frame.transformed(T), self.face, self.sweep_deg)

    def to_dict(self) -> dict:
        d = {"clase": self.clase, "rol": self.rol, **self.frame.to_dict()}
        if self.face is not None:
            d["cara"] = self.face.to_dict()
        if self.sweep_deg is not None:
            d["barrido_deg"] = [float(x) for x in self.sweep_deg]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Port":
        return cls(d["clase"], d["rol"], PortFrame.from_dict(d),
                   PortFace.from_dict(d["cara"]) if d.get("cara") else None,
                   tuple(d["barrido_deg"]) if d.get("barrido_deg") is not None else None)


NEAR_FACE_MM = 3.0              # mm: cara «cercana» al plano del puerto (para decir cuánto está corrida o girada)
TILT_OK_DEG = 0.3               # grados: por debajo de esto se considera que el plano coincide con la cara


def diagnose_plane(mesh, frame: PortFrame, tol: float = FACE_TOL, near_mm: float = NEAR_FACE_MM) -> dict:
    """Por qué (o si) el plano del puerto cae sobre una cara plana de `mesh` que mira hacia `frame.axis`.

    Devuelve: `triangulos` (de la pieza), `en_plano` (los que yacen sobre el plano, a menos de `tol`, mirando hacia
    afuera), `cercanos` (los que miran hacia afuera con el centro a menos de `near_mm` del plano), y de esos cercanos
    la cara dominante: `inclinacion_deg` (ángulo entre su normal y el eje del puerto) y `desvio_mm` (distancia con
    signo de la cara al plano, medida en el centro del puerto). `inclinacion_deg`/`desvio_mm` son None si no hay
    ninguna cara cercana."""
    tri = np.asarray(mesh.triangles, float)
    out = {"triangulos": int(len(tri)), "en_plano": 0, "cercanos": 0, "inclinacion_deg": None, "desvio_mm": None}
    if len(tri) == 0:
        return out
    axis = np.asarray(frame.axis, float)
    nrm = np.asarray(mesh.face_normals, float)
    dist = ((tri.reshape(-1, 3) - frame.center) @ axis).reshape(-1, 3)
    facing = nrm @ axis
    out["en_plano"] = int(((np.abs(dist) < tol).all(axis=1) & (facing > 0.999)).sum())
    near = (np.abs(dist.mean(axis=1)) < near_mm) & (facing > 0.8)
    out["cercanos"] = int(near.sum())
    if not near.any():
        return out
    t, n = tri[near], nrm[near]
    area = 0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)
    if area.sum() <= 0:
        return out
    # cara dominante: la normal media ponderada por área de los triángulos cercanos que miran casi igual que la mayor
    big = n[int(np.argmax(area))]
    same = (n @ big) > 0.995
    wn = (n[same] * area[same, None]).sum(axis=0)
    wn /= np.linalg.norm(wn)
    out["inclinacion_deg"] = float(np.degrees(np.arccos(np.clip(wn @ axis, -1.0, 1.0))))
    # distancia de la cara dominante al centro del puerto, medida a lo largo de la normal del puerto
    cen = t[same].mean(axis=1)
    c = (cen * area[same, None]).sum(axis=0) / area[same].sum()
    out["desvio_mm"] = float((c - frame.center) @ axis)
    return out


def describe_plane_failure(diag: dict, tol: float = FACE_TOL) -> str:
    """El diagnóstico en lenguaje llano (una frase): qué pasó con el plano y qué hacer."""
    if diag["triangulos"] == 0:
        return "la pieza quedó vacía: el plano no pasa por la figura"
    ang, off = diag["inclinacion_deg"], diag["desvio_mm"]
    if ang is None:
        return (f"ninguna cara de la pieza mira hacia afuera a menos de {NEAR_FACE_MM:g} mm del plano: "
                "el plano no toca la pieza o la cara de corte desapareció")
    txt = f"hay una cara plana de la pieza girada {ang:.1f}° y a {abs(off):.2f} mm del plano".replace(".", ",")
    if ang < TILT_OK_DEG and abs(off) < 0.05:
        return txt + (": el plano coincide, pero ningún vértice cae dentro de la tolerancia de "
                      f"{tol:g} mm (la reparación de la malla los movió)").replace(".", ",")
    return txt + (": el plano no coincide con esa cara; girá el plano para igualarla"
                  if ang >= TILT_OK_DEG else ": el plano está paralelo pero corrido; movelo hasta la cara")


def face_from_mesh(mesh, frame: PortFrame, tol: float = FACE_TOL, place: str | None = None) -> PortFace:
    """Cara del puerto: unión de los triángulos de `mesh` (trimesh) que yacen sobre el plano del puerto y miran hacia
    `frame.axis` (la normal de la cara sale de la pieza). Devuelve sus polígonos en (u, v) del puerto.

    `place`: a qué lugar pertenece el puerto (se guarda en el `PortError` para que la interfaz pinte ese plano)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    tri = np.asarray(mesh.triangles, float)
    d = np.abs((tri.reshape(-1, 3) - frame.center) @ frame.axis).reshape(-1, 3)
    on_plane = (d < tol).all(axis=1) & (np.asarray(mesh.face_normals) @ frame.axis > 0.999)
    tri = tri[on_plane]
    if len(tri) == 0:
        diag = diagnose_plane(mesh, frame, tol)
        raise PortError(f"{'«' + place + '»: ' if place else ''}el plano del puerto no cae sobre una cara plana de la "
                        f"pieza que mire hacia afuera — {describe_plane_failure(diag, tol)}", place, diag)
    polys = [Polygon(frame.to_uv(t)).buffer(0) for t in tri]
    return PortFace.from_shape(unary_union([q for q in polys if q.area > 1e-12]))


def centroid_of_face_triangles(mesh, frame: PortFrame, tol: float = FACE_TOL) -> np.ndarray | None:
    """Centroide (mundo) de la cara plana del puerto, ponderado por área; None si no hay cara."""
    tri = np.asarray(mesh.triangles, float)
    d = np.abs((tri.reshape(-1, 3) - frame.center) @ frame.axis).reshape(-1, 3)
    on = tri[(d < tol).all(axis=1) & (np.asarray(mesh.face_normals) @ frame.axis > 0.999)]
    if len(on) == 0:
        return None
    area = 0.5 * np.linalg.norm(np.cross(on[:, 1] - on[:, 0], on[:, 2] - on[:, 0]), axis=1)
    if area.sum() <= 0:
        return None
    return (on.mean(axis=1) * area[:, None]).sum(axis=0) / area.sum()


def port_from_points(mesh, points, clase: str, rol: str, sweep_deg=None) -> Port:
    """Puerto de `mesh` (trimesh) a partir de 3 puntos sobre su cara de unión: el eje sale de la pieza, el centro es
    el centroide de la cara y la referencia de giro es el punto 1."""
    centroid = np.asarray(mesh.center_mass if mesh.is_watertight else mesh.centroid, float)
    frame = PortFrame.from_points(points, centroid)
    c = centroid_of_face_triangles(mesh, frame)
    if c is None:
        raise PortError("el plano de los 3 puntos no cae sobre una cara plana de la pieza que mire hacia afuera "
                        "(¿los 3 puntos están sobre la cara de corte?)")
    frame = frame.with_center(c)
    return Port(clase, rol, frame, face_from_mesh(mesh, frame), sweep_deg)


# ------------------------------------------------------------------ compatibilidad barata (solo metadatos)
@dataclass
class Compat:
    nivel: str                         # "ok" | "aviso" | "no"
    iou: float | None = None           # coincidencia de las dos caras alineadas
    escalon_mm: float | None = None    # |radio equivalente A − radio equivalente B|
    margenes_mm: dict = field(default_factory=dict)   # margen del pasador (vástago al borde de la cara) por lado
    motivos: list = field(default_factory=list)       # por qué «no»
    avisos: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"nivel": self.nivel, "iou": None if self.iou is None else round(self.iou, 4),
                "escalon_mm": None if self.escalon_mm is None else round(self.escalon_mm, 4),
                "margenes_mm": {k: round(v, 3) for k, v in self.margenes_mm.items()},
                "motivos": list(self.motivos), "avisos": list(self.avisos)}


def _pin_for(clase: str, params: Params) -> PinSpec | None:
    attr = PIN_OF_CLASS.get(clase)
    return getattr(params, attr) if attr else None


def face_iou(a: PortFace, b: PortFace) -> float:
    """IoU de dos caras acopladas: se alinean por centro y referencia; como los ejes son opuestos, la cara de B se
    ve espejada en v desde el marco de A."""
    from shapely import affinity
    sa, sb = a.shape(), affinity.scale(b.shape(), 1.0, -1.0, origin=(0, 0))
    union = sa.union(sb).area
    return float(sa.intersection(sb).area / union) if union > 1e-12 else 0.0


def port_compat(a: Port, b: Port, height_a: float | None = None, height_b: float | None = None,
                params: Params | None = None) -> Compat:
    """Compatibilidad barata del par (a, b): clase y roles, IoU y escalón de las caras, margen del pasador y altura de figura.

    `no` si la clase no coincide, los roles no son complementarios o el pasador no entra en la cara; `aviso` si el IoU baja
    de `MIN_BASE_IOU`, el escalón supera `STEP_WARN_MM`, la pared que queda es menor que `min_wall`, el vástago es más
    fino que `min_pin_d` o las alturas difieren más de `HEIGHT_TOL_MM`. El pasador de la muñeca no se mide acá (se achica
    solo hasta que entra, `hand.fit_hand_pin`). Con el pasador centrado en el centroide de la cara."""
    params = params or Params()
    c = Compat("ok")
    if a.clase != b.clase:
        c.motivos.append(f"clases distintas: «{a.clase}» y «{b.clase}»")
    if COMPLEMENT[a.rol] != b.rol:
        c.motivos.append(f"roles que no se acoplan: {a.rol} con {b.rol} (hace falta enchufe con toma)"
                         if "soldado" not in (a.rol, b.rol) else f"roles que no se acoplan: {a.rol} con {b.rol}")
    if a.face is not None and b.face is not None:
        c.iou = face_iou(a.face, b.face)
        c.escalon_mm = abs(a.face.radius_eq - b.face.radius_eq)
        if c.iou < MIN_BASE_IOU:
            c.avisos.append(f"las caras coinciden solo {100 * c.iou:.0f} % (mínimo recomendado {100 * MIN_BASE_IOU:.0f} %)")
        if c.escalon_mm > STEP_WARN_MM:
            c.avisos.append(f"escalón de {c.escalon_mm:.2f} mm entre los radios de las caras (aviso desde {STEP_WARN_MM:g} mm)")
        pin = _pin_for(a.clase, params)
        if pin is not None:
            from .limbs import pin_fit
            for lado, p in (("A", a), ("B", b)):
                m = pin_fit(p.face.shape(), (0.0, 0.0), pin.shaft_d)[1]
                c.margenes_mm[lado] = m
                if m < 0:
                    c.motivos.append(f"el pasador de {pin.shaft_d:g} mm no entra en la cara {lado} (sobresale {-m:.2f} mm)")
                elif m < params.min_wall:
                    c.avisos.append(f"pared de {m:.2f} mm alrededor del pasador en la cara {lado} (mínimo {params.min_wall:g} mm)")
            if pin.shaft_d < params.min_pin_d:
                c.avisos.append(f"vástago de {pin.shaft_d:g} mm, menos que el mínimo {params.min_pin_d:g} mm")
    else:
        c.avisos.append("falta la cara de unión de algún puerto: no se pudo medir el ajuste")
    if height_a is not None and height_b is not None and abs(height_a - height_b) > HEIGHT_TOL_MM:
        c.avisos.append(f"alturas de figura distintas: {height_a:g} y {height_b:g} mm")
    c.nivel = "no" if c.motivos else ("aviso" if c.avisos else "ok")
    return c


def _r(a, nd: int = 6):
    return np.round(np.asarray(a, float), nd).tolist()
