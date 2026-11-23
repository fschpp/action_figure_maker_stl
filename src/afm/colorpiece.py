"""Color propio de una pieza: el FBX de la pieza llevado al marco del huésped (plan color de catálogo y cabeza, §3, T32).

Una pieza que trae su propio FBX con color necesita su fuente de color en el MISMO marco en que el motor le entrega la pieza a
`Colorizer`. Se hace una sola vez y en dos tramos, y después `register_piece`, `unwrap` y `bake_piece` no cambian:

    FBX ──A──► STL de la pieza ──T──► marco del huésped            S = T · A

* `A` (alineación de origen) se calcula al importar el FBX contra el STL de la pieza (`FigureFrame.from_piece_similarity`). Es lo que
  el catálogo guarda (T35, `PieceMeta.color.similitud`): al volver a armar no se recalcula.
* `T` la pone quien arma: `PlacedPart.T` en el catálogo; en la cabeza importada suelta, la de `fit_replacement` (T33).

`S` se aplica a los vértices del `TexturedMesh` (`TexturedMesh.transformed`; los UV no se tocan) y con eso se arma la `ColorSource`.
`PIL` y `trimesh` no se importan aquí (D10). Todo lo del informe es número, `str`, `bool`, lista o diccionario de eso (R13).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

from .frame import CUT_MIN_COVER, FRONT_MAX_DEG, SIMILARITY_OK_MM, FigureFrame, FrontHint
from .texmesh import TexturedMesh

_MIRROR_X = np.diag([-1.0, 1.0, 1.0, 1.0])       # el lado derecho es `mirror_x` del izquierdo (`pipeline`, `colorreg`)


class PieceColorError(ValueError):
    """El FBX de la pieza no se puede usar como su fuente de color (no coincide con el STL, o no trae textura de color)."""


class PieceFrontError(PieceColorError):
    """O09 · El FBX calza con la pieza pero la cara quedó hacia atrás (o no se puede decidir hacia dónde mira). Lleva el `FigureFrame` calculado (`.frame`)
    para poder mostrarlo antes de guardar y, si la persona lo confirma, guardarlo igual."""

    def __init__(self, message: str, frame: FigureFrame):
        super().__init__(message)
        self.frame = frame


class PaletteRolesError(PieceColorError):
    """La paleta de la línea no sirve para colorear por roles las piezas elegidas (no hay paleta, no se puede leer o le faltan roles).

    A diferencia de un FBX que no calza, que deja la pieza en color plano con un aviso, esto **frena el armado** antes de registrar nada:
    no se inventa un color para un rol que no existe (plan de paleta de línea, §5.3, T43)."""


@dataclass
class PieceColor:
    source: object                       # ColorSource, en el marco del huésped
    similarity: np.ndarray               # (4, 4) A: FBX → STL de la pieza (lo que se guarda en el catálogo)
    to_host: np.ndarray                  # (4, 4) T: STL de la pieza → marco del huésped
    matrix: np.ndarray                   # (4, 4) S = T · A: FBX → marco del huésped
    report: dict = field(default_factory=dict)


def _as_4x4(M, what: str) -> np.ndarray:
    M = np.asarray(M, float)
    if M.shape != (4, 4) or not np.all(np.isfinite(M)):
        raise PieceColorError(f"{what}: se esperaba una matriz 4×4 finita")
    if abs(np.linalg.det(M[:3, :3])) < 1e-12:
        raise PieceColorError(f"{what}: la matriz es singular")
    return M


REGION_BOX_PAD = 0.15            # y esta fracción de cada lado de la caja de la pieza
REGION_MAX_DIST_MM = 3.0         # O10: para agrupar colores, la región también queda a esta distancia de la superficie de la pieza (la v2 es otro modelado: ≈ 2 mm de media)
NECK_MARGIN_MM = 0.5             # O04: la región «cabeza» del FBX llega hasta este tanto por debajo del plano del cuello de la pieza


def _port_parts(meta, name: str):
    """(centro, eje, ref) del puerto `name` de una ficha (`PieceMeta` o su dict `afm-pieza-1`), o `None` si no lo trae."""
    ports = meta.get("puertos") if isinstance(meta, dict) else getattr(meta, "puertos", None)
    p = (ports or {}).get(name)
    if p is None:
        return None
    if isinstance(p, dict):
        return tuple(np.asarray(p[k], float) for k in ("centro", "eje", "ref"))
    return p.frame.center, p.frame.axis, p.frame.ref


def piece_front(meta=None, mesh=None) -> dict:
    """O02 · El frente de una pieza `cabeza` en su marco: `{front, up, center, ref_dev_deg}`.

    El marco del motor deja siempre el frente de la figura en +Y (`frame.py`: giro de 180° sobre Z si `front == "-Y"`), así que `front` es +Y
    tomado en el plano perpendicular a `up`. `up` es el eje del cuello hacia la cabeza (opuesto al eje del puerto `cuello`, que sale de la pieza;
    sin puerto, +Z) y `center` un punto de ese eje (centro del puerto, o el centro de la caja de `mesh`). `ref_dev_deg` es el ángulo entre ese
    frente y la referencia de giro del puerto (`None` sin puerto): si es grande, la pieza no sigue la convención y hay que mirarla."""
    parts = None if meta is None else _port_parts(meta, "cuello")
    if parts is not None:
        center, axis, ref = parts
        up = -np.asarray(axis, float) / np.linalg.norm(axis)
    else:
        up = np.array([0.0, 0.0, 1.0])
        center = np.asarray(mesh.bounds, float).mean(0) if mesh is not None else np.zeros(3)
        ref = None
    front = np.array([0.0, 1.0, 0.0])
    front = front - (front @ up) * up
    front /= np.linalg.norm(front)
    dev = None
    if ref is not None and np.linalg.norm(ref - (ref @ up) * up) > 1e-9:
        r = ref - (ref @ up) * up
        dev = round(float(np.degrees(np.arccos(np.clip(front @ r / np.linalg.norm(r), -1, 1)))), 1)
    return {"front": front, "up": up, "center": np.asarray(center, float), "ref_dev_deg": dev}


def head_region(V_piece, F, front: dict, piece_vertices=None) -> np.ndarray:
    """O04 · Máscara de triángulos del FBX (vértices `V_piece`, ya en el marco de la pieza) que corresponden a lo que la pieza tiene: lo que está por
    encima del plano del cuello (`front["center"]`, `front["up"]`, con `NECK_MARGIN_MM` de holgura) y, si se pasa `piece_vertices`, dentro de la caja
    de la pieza agrandada `REGION_BOX_PAD` (el plano está inclinado y por delante de él quedan el pecho y los hombros). Deja afuera hombros y uniforme."""
    cen = np.asarray(V_piece, float)[np.asarray(F, np.int64)].mean(1)
    keep = (cen - front["center"]) @ front["up"] >= -NECK_MARGIN_MM
    if piece_vertices is not None:
        lo, hi = np.min(piece_vertices, axis=0), np.max(piece_vertices, axis=0)
        pad = REGION_BOX_PAD * (hi - lo)
        keep &= np.all((cen >= lo - pad) & (cen <= hi + pad), axis=1)
    return keep


def piece_region(V_piece, F, front: dict, mesh, max_dist_mm: float = REGION_MAX_DIST_MM) -> np.ndarray:
    """O10 · Máscara de triángulos del FBX (vértices `V_piece` en el marco de la pieza) para **agrupar colores**: `head_region` y además a no más de
    `max_dist_mm` de la superficie de la pieza `mesh`. El plano del cuello está inclinado y por delante de él quedan el cuello y el cuello del uniforme (rojo,
    dorado) dentro de la caja: con solo `head_region` entraban 13 % del área del busto que la pieza no tiene. Determinista (muestreo con semilla fija)."""
    import trimesh
    from scipy.spatial import cKDTree

    V = np.asarray(V_piece, float)
    keep = head_region(V, F, front, np.asarray(mesh.vertices, float))
    idx = np.flatnonzero(keep)
    if idx.size == 0:
        return keep
    pts = np.asarray(trimesh.sample.sample_surface(mesh, 30_000, seed=0)[0], float)
    cen = V[np.asarray(F, np.int64)[idx]].mean(1)
    near = cKDTree(pts).query(cen)[0] <= float(max_dist_mm)
    keep[idx[~near]] = False
    return keep


def front_hint(meta, mesh, pkg=None, skin_roles=None) -> FrontHint:
    """`FrontHint` de una cabeza para `align_fbx_to_piece(front=)`. Con el paquete de roles `pkg`, la dirección de la cara del FBX sale de los
    triángulos de piel de la región de la cabeza (`head_region`); sin él, solo se decide por el calce (y puede quedar `ambigua`)."""
    from . import colorroles

    pf = piece_front(meta, mesh)
    pv = None if mesh is None else np.asarray(mesh.vertices, float)
    face_dir = None
    if pkg is not None:
        sk = colorroles.SKIN_ROLES if skin_roles is None else skin_roles

        def face_dir(M):
            M = np.asarray(M, float)
            V = np.asarray(pkg.vertices, float) @ M[:3, :3].T + M[:3, 3]
            vec, conf = colorroles.face_direction(pkg, sk, region=head_region(V, pkg.faces, pf, pv), vertices=V)
            return vec

    return FrontHint(pf["front"], pf["up"], pf["center"], face_dir)


def _check_front(frame: FigureFrame, front: FrontHint | None) -> None:
    """O06 · Rellena `frente`/`frente_ok` en el informe si falta (alineación estricta) y baja `ok` si la cara quedó hacia atrás."""
    if front is None or "frente_ok" in frame.report:
        return
    ang, vec = front.angle(frame.matrix)
    frame.report["frente_ok"] = None if ang is None else bool(ang < FRONT_MAX_DEG)
    frame.report["frente"] = {"fbx": None if vec is None else [round(float(x), 3) for x in vec / np.linalg.norm(vec)],
                              "pieza": [round(float(x), 3) for x in front.front], "angulo_deg": None if ang is None else round(ang, 1)}
    if frame.report["frente_ok"] is False:
        frame.report["ok"] = False


def align_fbx_to_piece(tm: TexturedMesh, piece_stl, *, refine: bool | None = None, ok_mm: float = SIMILARITY_OK_MM,
                       require_ok: bool = True, cut: bool = True, front: FrontHint | None = None) -> FigureFrame:
    """Alinea el FBX `tm` con el STL de la pieza (`piece_stl`, cualquier objeto con `vertices` y `faces`): `FigureFrame` con la 4×4
    FBX → marco del STL y el informe (`media_mm`, `p95_mm`, `max_mm`, `ok`…).

    `ok` es «la media de las distancias de los vértices del STL a la superficie del FBX no pasa de `ok_mm`». Con `require_ok`
    (por defecto) un FBX que no calza levanta `PieceColorError` con las medidas; con `require_ok=False` se devuelve igual y el
    llamador decide con `report["ok"]`.

    `cut` (por defecto): si la alineación estricta no calza, se prueba la de **pieza recortada** (`FigureFrame.from_cut_piece_similarity`):
    una cabeza cortada en la base del cuello (con tapa, pasador, quizá hueca) es un trozo del FBX y sus partes nuevas inflaban la media. En
    ese caso el informe lleva `recortada: True` y `media_mm` es la de la fracción de la pieza que calza (`cubierta` dice cuánta es).

    `front` (`front_hint`, O05/O06): hacia dónde mira la pieza. Con él el calce deja la cara del FBX del lado del frente y se valida el resultado: si
    la cara queda hacia atrás (ángulo ≥ 90°) o no se puede decidir (`ambigua`), `ok` es falso y, con `require_ok`, se levanta `PieceColorError`
    que propone «Girar 180°». El informe lleva `frente`, `frente_ok` y `ambigua`."""
    if "base_color" not in tm.images:
        raise PieceColorError("el FBX no trae textura de color")
    frame = FigureFrame.from_piece_similarity(tm.vertices, piece_stl, refine=refine, source_faces=tm.faces)
    frame.report["ok"] = bool(frame.report["media_mm"] <= ok_mm)
    _check_front(frame, front)
    if not frame.report["ok"] and cut:
        alt = FigureFrame.from_cut_piece_similarity(tm.vertices, piece_stl, source_faces=tm.faces, front_hint=front)
        calza = alt.report["ok"] or (alt.report["media_mm"] <= ok_mm and alt.report.get("cubierta", 0.0) >= CUT_MIN_COVER)
        alt.report["ok"] = bool(calza and alt.report.get("frente_ok") is not False and not alt.report.get("ambigua"))
        alt.report["recortada"] = True
        if alt.report["ok"]:
            return alt
        if require_ok and calza and (alt.report.get("frente_ok") is False or alt.report.get("ambigua")):
            raise PieceFrontError(_front_message(alt.report), alt)
    if require_ok and frame.report.get("frente_ok") is False and frame.report["media_mm"] <= ok_mm:
        raise PieceFrontError(_front_message(frame.report), frame)
    if require_ok and not frame.report["ok"]:
        raise PieceColorError(f"el FBX no coincide con el STL de la pieza: distancia media {frame.report['media_mm']:.3f} mm "
                              f"(máximo aceptado {ok_mm:g} mm)")
    return frame


def front_problem(report: dict) -> bool:
    """O09 · ¿El informe de una alineación dice que la cara quedó hacia atrás o que no se pudo decidir hacia dónde mira?"""
    return report.get("frente_ok") is False or bool(report.get("ambigua"))


def align_roles_package(meta, piece_stl, pkg, palette, *, require_ok: bool = True) -> FigureFrame:
    """Alinea el paquete de roles `pkg` (con `palette`) con el STL de la pieza `piece_stl`. Para una pieza `cabeza` (`meta.tipo`) pasa la pista de frente
    (O05): la cara del FBX tiene que quedar del lado del frente. Es lo que hace `Catalog` al guardar y lo que muestra el asistente de roles antes de guardar."""
    front = front_hint(meta, piece_stl, pkg) if getattr(meta, "tipo", None) == "cabeza" else None
    return align_fbx_to_piece(pkg.to_textured(palette), piece_stl, front=front, require_ok=require_ok)


def package_from_fbx(saved, tm):
    """O08 · El paquete de roles `saved` con los vértices ORIGINALES del FBX `tm` (el `.color.npz` guardado trae los vértices ya deformados por el
    ajuste no rígido, así que no sirve para volver a alinear). Los roles, el atlas y los UV son los guardados: no se vuelve a agrupar ni a asignar
    colores. El FBX tiene que ser la misma malla (mismos vértices, caras y UV), si no `PieceColorError`."""
    from .colorroles import RolesPackage

    V = np.asarray(tm.vertices)
    if V.shape != np.asarray(saved.vertices).shape or not np.array_equal(np.asarray(tm.faces), np.asarray(saved.faces)):
        raise PieceColorError(f"el FBX no es el de origen del paquete: tiene {len(V)} vértices y {len(tm.faces)} caras, el paquete {len(saved.vertices)} y "
                              f"{len(saved.faces)}")
    if not np.allclose(np.asarray(tm.corner_uv, float), np.asarray(saved.corner_uv, float), atol=1e-4):
        raise PieceColorError("el FBX no es el de origen del paquete: los UV no coinciden")
    return RolesPackage(V.astype(np.float32), np.asarray(saved.faces), np.asarray(saved.corner_uv), np.asarray(saved.labels, np.uint8),
                        list(saved.roles), dict(saved.origen or {}), saved.shade)


def turn_roles_package(saved, tm, similarity, meta, mesh, degrees: float = 180.0):
    """O08 · «Girar»: `(paquete con los vértices originales del FBX, FigureFrame)` con la pose guardada (`similarity`, 4×4 FBX → pieza) girada
    `degrees` grados (180° por defecto) alrededor del eje del cuello de la pieza y re-afinada (`FigureFrame.from_cut_piece_pose`). Los roles y el atlas
    no cambian. El informe lleva `giro_grados`, `frente`, `frente_ok` y el calce; `ok` es falso si el calce no alcanza o si la cara sigue hacia
    atrás (en ese caso el giro no corrigió nada: el que llama decide si guarda)."""
    from .frame import yaw_matrix

    pkg = package_from_fbx(saved, tm)
    A = _as_4x4(similarity, "similitud FBX → STL")
    pf = piece_front(meta, mesh)
    hint = front_hint(meta, mesh, pkg)
    start = yaw_matrix(pf["center"], pf["up"], degrees) @ A
    frame = FigureFrame.from_cut_piece_pose(pkg.vertices.astype(float), mesh, start, source_faces=pkg.faces, front_hint=hint)
    frame.report["recortada"] = True
    frame.report["giro_grados"] = float(degrees)
    return pkg, frame


def _front_message(rep: dict) -> str:
    fr = rep.get("frente") or {}
    if rep.get("ambigua"):
        return ("no se puede decidir hacia dónde mira la cara del FBX: la pose y su giro de 180° calzan igual de bien "
                f"(diferencia {rep.get('yaw180', {}).get('margen_mm', 0):.2f} mm) y el FBX no tiene rol de piel. Girá el FBX 180° si hace falta")
    return (f"la cara del FBX quedó mirando hacia atrás (ángulo {fr.get('angulo_deg')}° con el frente de la pieza): "
            "el calce es el de la pieza girada 180° sobre el cuello. Probá «Girar 180°»")


def fbx_in_host_frame(tm: TexturedMesh, similarity, to_host=None) -> TexturedMesh:
    """`tm` con sus vértices en el marco del huésped: `S = to_host · similarity` (sin `to_host`, el marco del STL de la pieza)."""
    A = _as_4x4(similarity, "similitud FBX → STL")
    S = A if to_host is None else _as_4x4(to_host, "STL → huésped") @ A
    return tm.transformed(S)


def piece_color_source(tm: TexturedMesh, piece_stl, to_host=None, *, similarity=None, spacing_mm: float = 0.08,
                       refine: bool | None = None, ok_mm: float = SIMILARITY_OK_MM, require_ok: bool = True) -> PieceColor:
    """La fuente de color de una pieza, ya en el marco del huésped, lista para `Colorizer(..., sources={clave: pc.source})`.

    `similarity` (4×4 FBX → STL, la que guardó el catálogo) evita recalcular la alineación; sin ella se calcula con
    `align_fbx_to_piece`. `to_host` es la 4×4 STL de la pieza → huésped (identidad si no se pasa). El informe suma `alineacion`
    (medidas, o `{"origen": "guardada"}` si se reusó `similarity`) y `escala` de `S`."""
    from .colorsrc import ColorSource

    if similarity is None:
        frame = align_fbx_to_piece(tm, piece_stl, refine=refine, ok_mm=ok_mm, require_ok=require_ok)
        A, align = frame.matrix, dict(frame.report)
    else:
        if "base_color" not in tm.images:
            raise PieceColorError("el FBX no trae textura de color")
        A, align = _as_4x4(similarity, "similitud FBX → STL"), {"origen": "guardada"}
    T = np.eye(4) if to_host is None else _as_4x4(to_host, "STL → huésped")
    S = T @ A
    src = ColorSource(tm.transformed(S), spacing_mm=spacing_mm)
    report = {"alineacion": align, "escala": float(np.cbrt(abs(np.linalg.det(S[:3, :3])))),
              "espejo": bool(np.linalg.det(S[:3, :3]) < 0), "spacing_mm": round(float(src.spacing_mm), 4)}
    return PieceColor(src, A, T, S, report)


def head_color_provider(fbx_path: str, stl_path: str, *, spacing_mm: float = 0.08, similarity=None, out: dict | None = None):
    """Proveedor (`callable(res)`) de la fuente de color de la cabeza importada suelta (T34), para `Colorizer(sources={"cabeza": ...})`.

    Se resuelve con la figura ya construida: `S = raw_a_mundo · A`, con `A` la alineación FBX → STL importado (se calcula, o
    `similarity` si ya se tiene) y `raw_a_mundo` la 4×4 STL importado → marco de la figura que dejó `fit_replacement` (T33) en
    `res.report["head"]["replacement"]`. Levanta `PieceColorError` (un `ValueError`) si no hay cabeza importada o el FBX no calza con
    su STL; el `Colorizer` lo informa y deja la cabeza con el color de la figura. `out`, si se pasa, recibe el informe de `piece_color_source`."""
    def provide(res):
        import trimesh

        from .fbxio import load_textured

        rep = (getattr(res, "report", None) or {}).get("head", {}).get("replacement")
        if not rep or "raw_a_mundo" not in rep:
            raise PieceColorError("la figura no tiene una cabeza importada: el FBX de la cabeza no se usa")
        stl = trimesh.load(stl_path, force="mesh", process=False)
        pc = piece_color_source(load_textured(fbx_path), stl, np.asarray(rep["raw_a_mundo"], float), similarity=similarity,
                                spacing_mm=spacing_mm)
        if out is not None:
            out.update(pc.report)
        return pc.source
    return provide


def catalog_color_sources(placed: dict, catalog, scaled=(), *, spacing_mm: float = 0.08, overrides: dict | None = None) -> tuple[dict, dict]:
    """`(sources, info)` para `Colorizer(sources=...)` de una figura armada desde el catálogo (T36, T43).

    `placed`: `BuildResult.placed` (nombre → `PlacedPart`). Para cada pieza cuya ficha trae `color` se arma un proveedor (se resuelve
    al colorear la pieza, no antes) que lleva su color al marco del huésped con `S = T · A`: `T` es `PlacedPart.T` y `A` la similitud
    guardada en el catálogo (T35), así que no se vuelve a alinear. La clave es el nombre sin sufijo `_izq/_der`: con el lado derecho
    espejado la pieza `_der` se registra reflejada contra la misma fuente (como cualquier otra pieza). Un lado `_der` propio
    (figura asimétrica) no se mira: usa el color del izquierdo.

    Dos modos de color de pieza (`PieceMeta.color["modo"]`):

    * `rgb` (T35, el de siempre): el FBX entero de la pieza, con sus colores.
    * `roles` (T42/T43): el paquete `.color.npz` + la **paleta de la línea** (`catalog.palette()`). La paleta se lee una sola vez, aquí,
      y es la misma que va al informe (`info["paleta"]`), así lo que se informa es lo que se usó. El `TexturedMesh` se arma al colorear
      (`RolesPackage.to_textured`), así que un cambio de color en la paleta se toma en el armado siguiente sin tocar la pieza ni su STL.
      Si a la paleta le falta algún rol que usan las piezas elegidas (o no hay paleta, o no se puede leer) se levanta `PaletteRolesError`
      con la lista, **antes** de registrar nada: ese error no se degrada a aviso.

    `scaled`: tipos de pieza que la receta escala (`scale_choice` cambia la malla y `T` no lo recoge): esas piezas quedan sin color
    propio. `info` = `{"piezas": {clave: {"pieza": ref, "fbx": ruta}}, "omitidas": {clave: motivo}}`; las piezas `roles` llevan
    `{"pieza", "modo": "roles", "paquete", "roles"}` en lugar de `fbx`, y si hay alguna `info["paleta"]` = `{"hash", "roles": {id: hex}}`.
    `overrides` (T48): `{rol: "#RRGGBB"}` colores de la paleta que cambian **solo para esta figura** (`Palette.with_overrides`): la paleta de la línea
    no se toca ni se escribe. Un rol que la paleta no tiene es `PaletteRolesError` (no se descarta en silencio). `info["paleta"]` lleva la paleta
    **usada** (con los cambios: `hash` y `roles`) y, si hubo cambios, `cambios` = `{rol: hex}`. Sin piezas `roles` los cambios no se usan.
    Sin piezas `roles` el resultado es exactamente el de T36."""
    from .colorreg import _split_name

    sources: dict = {}
    info: dict = {"piezas": {}, "omitidas": {}}
    pending: dict = {}                                   # clave -> (meta, T, paquete, ruta): piezas por roles, se resuelven con la paleta ya leída
    for name, pp in placed.items():
        meta = pp.choice.meta
        key, side = _split_name(name)
        if not getattr(meta, "color", None) or side == "der" or key in sources or key in pending:
            continue
        if meta.tipo in set(scaled):
            info["omitidas"][key] = f"{meta.ref} está escalada: el color no se lleva"
            continue
        try:
            path = catalog.color_file(meta.ref)         # CatalogError: sin archivo de color o con otro hash
            roles_mode = (meta.color or {}).get("modo", "rgb") == "roles"
            pkg = _load_roles(path) if roles_mode else None
            rt = catalog.retouch(meta.ref) if roles_mode else None      # R04: retoque manual de la pieza (None si no tiene)
        except ValueError as exc:                       # también RolesError: un .npz ilegible, y el retoque ilegible o alterado
            info["omitidas"][key] = str(exc)
            continue
        if roles_mode:
            pending[key] = (meta, np.asarray(pp.T, float), pkg, path, rt)
            continue
        info["piezas"][key] = {"pieza": meta.ref, "fbx": path}

        def provide(res, _fbx=path, _T=np.asarray(pp.T, float), _A=meta.color["similitud"], _sp=spacing_mm):
            from .fbxio import load_textured

            return piece_color_source(load_textured(_fbx), None, _T, similarity=_A, spacing_mm=_sp).source
        sources[key] = provide
    if pending:
        _roles_sources(pending, catalog, spacing_mm, sources, info, overrides)
    return sources, info


def _load_roles(path: str):
    """El paquete de roles de `path`, **restringido a los roles que alguna etiqueta usa**.

    Un paquete puede declarar un rol que ningún píxel usa (el catálogo solo anota en la ficha los usados): si ese rol se borra de la paleta
    no debe frenar el armado, así que las etiquetas se renumeran sobre los roles usados y el resto de la lista se descarta.
    """
    from .colorroles import RolesPackage
    from .palette import NO_ROLE

    pkg = RolesPackage.load(path)
    used_idx = [int(i) for i in np.unique(pkg.labels) if int(i) != NO_ROLE]
    remap = np.full(256, NO_ROLE, np.uint8)
    for new, old in enumerate(used_idx):
        remap[old] = new
    return RolesPackage(pkg.vertices, pkg.faces, pkg.corner_uv, remap[np.asarray(pkg.labels, np.uint8)],
                        [pkg.roles[i] for i in used_idx], dict(pkg.origen or {}), pkg.shade)


def roles_color_source(pkg, palette, to_host, similarity, spacing_mm: float = 0.08, retouch=None):
    """`ColorSource` en el marco del huésped de una pieza por roles, con su retoque manual (R04) si lo tiene.

    Con retoque, los roles que pinta y el paquete no trae se agregan al final de sus roles (`RolesPackage.with_roles`: las etiquetas existentes no
    cambian) y las pinceladas, que viven en el marco del STL de la pieza, se llevan al huésped con la misma `to_host` (`T`, o `M·T` en el lado
    derecho) que el color. Sin retoque el resultado es exactamente el de antes."""
    has = retouch is not None and len(retouch) > 0
    if has:
        pkg = pkg.with_roles(retouch.roles_used())
    src = piece_color_source(pkg.to_textured(palette), None, to_host, similarity=similarity, spacing_mm=spacing_mm).source
    if has:
        from .retouch import transform_strokes

        src.paint(transform_strokes(retouch.strokes, to_host))
    return src


def _roles_sources(pending: dict, catalog, spacing_mm: float, sources: dict, info: dict, overrides: dict | None = None) -> None:
    """Completa `sources`/`info` con las piezas por roles de `pending` (ver `catalog_color_sources`). Levanta `PaletteRolesError` si la
    paleta de la línea no se puede usar para alguna de ellas."""
    refs: dict = {}                                      # rol -> piezas que lo usan
    for meta, _T, pkg, _path, rt in pending.values():
        for r in list(pkg.roles) + (rt.roles_used() if rt is not None else []):      # el retoque también pinta con roles de la paleta
            refs.setdefault(r, []).append(meta.ref)
    try:
        palette = catalog.palette()
    except ValueError as exc:                            # CatalogError: paleta.json ilegible
        raise PaletteRolesError(str(exc)) from exc
    if palette is None:
        raise PaletteRolesError(f"la línea «{catalog.name}» no tiene paleta (falta paleta.json) y las piezas {', '.join(sorted({v[0].ref for v in pending.values()}))} "
                                "llevan color por roles: creá los roles con `afm catalogo paleta agregar`")
    have = set(palette.ids())
    missing = {r: sorted(set(p)) for r, p in refs.items() if r not in have}
    if missing:
        raise PaletteRolesError(f"la paleta de la línea «{catalog.name}» no tiene los roles que usan las piezas elegidas: "
                                + "; ".join(f"{r} (en {', '.join(p)})" for r, p in sorted(missing.items()))
                                + ". Agregalos con `afm catalogo paleta agregar` o elegí otras piezas")
    if overrides:                                        # T48: cambios de esta figura sobre la paleta de la línea (la de la línea queda como está)
        from .palette import PaletteError, normalize_hex

        try:
            overrides = {str(r): normalize_hex(h, f"cambio de color del rol {r}") for r, h in overrides.items()}
            palette = palette.with_overrides(overrides)
        except PaletteError as exc:
            raise PaletteRolesError(f"{exc} (paleta de la línea «{catalog.name}»)") from exc
    for key, (meta, T, pkg, path, rt) in pending.items():
        info["piezas"][key] = {"pieza": meta.ref, "modo": "roles", "paquete": path, "roles": list(pkg.roles)}
        if rt is not None and len(rt):
            info["piezas"][key]["retoque"] = len(rt)

        def provide(res, _pkg=pkg, _pal=palette, _T=T, _A=meta.color["similitud"], _sp=spacing_mm, _rt=rt):
            return roles_color_source(_pkg, _pal, _T, _A, _sp, _rt)
        sources[key] = provide
        if getattr(meta, "lado", "centro") != "centro":
            # El lado derecho es `mirror_x` del izquierdo, y `register_piece` lleva la pieza IMPRESA al marco de su fuente: contra una fuente que
            # es solo el miembro izquierdo (x > 0) la `_der` (x < 0) no solapa y queda `sin_origen` (medido: 0 %). Con la fuente reflejada,
            # `M · T · A` (M = diag(−1, 1, 1)), registra igual que la izquierda. `Colorizer.source_for` mira primero el nombre completo.
            def provide_right(res, _pkg=pkg, _pal=palette, _T=_MIRROR_X @ T, _A=meta.color["similitud"], _sp=spacing_mm, _rt=rt):
                return roles_color_source(_pkg, _pal, _T, _A, _sp, _rt)
            sources[f"{key}_der"] = provide_right
    info["paleta"] = {"hash": palette.digest(), "roles": {r.id: r.hex for r in palette.roles}}
    if overrides:
        info["paleta"]["cambios"] = {r: palette.hex_of(r) for r in sorted(overrides)}


# ------------------------------------------------------------------ figura base con roles (T44)
def line_palette(catalog_root: str | None):
    """`(paleta, nombre de la línea)` del catálogo abierto en `catalog_root`: la paleta con la que se colorea una figura por roles.

    Una figura que no sale solo de catálogo no tiene «su» línea (plan de paleta, §12.2): se usa la línea que está abierta (`--catalogo`,
    `$AFM_CATALOGO` o la de la sesión). Sin catálogo, sin `paleta.json` o con uno ilegible → `PaletteRolesError` (frena antes de armar)."""
    from .catalog import Catalog

    if not catalog_root:
        raise PaletteRolesError("la figura lleva color por roles y no hay una línea abierta de la que sacar la paleta: pasá --catalogo RUTA "
                                "(o definí AFM_CATALOGO, o abrí el catálogo en la ventana)")
    try:
        cat = Catalog.open(catalog_root)
        palette = cat.palette()
    except (ValueError, OSError) as exc:                 # CatalogError / paleta.json ilegible / carpeta que no existe
        raise PaletteRolesError(f"no se pudo leer la paleta de la línea en «{catalog_root}»: {exc}") from exc
    if palette is None:
        raise PaletteRolesError(f"la línea «{cat.name}» no tiene paleta (falta paleta.json) y la figura lleva color por roles: "
                                "creá los roles con `afm catalogo paleta agregar`")
    return palette, cat.name


def figure_roles_textured(path: str, palette, overrides: dict | None = None, *, line: str = ""):
    """`(TexturedMesh, informe)` de la figura base con el paquete de roles `path` y la `palette` de la línea con los cambios de la figura
    (`overrides` = `{rol: "#RRGGBB"}`, solo para esta figura: la paleta que se pasa no se modifica).

    Falla con `PaletteRolesError` —antes de registrar nada— si el paquete no se puede leer, si a la paleta le faltan roles que usa la
    figura (los lista) o si un cambio nombra un rol que la paleta no tiene. Como en las piezas (T43), un rol que el paquete declara pero
    que ningún píxel usa no frena nada. `informe` (serializable, R5): `{"modo", "paquete", "roles", "origen", "paleta": {hash, roles},
    "cambios"}`; `hash` es el de la paleta **con** los cambios: es la que se usó."""
    from .colorroles import RolesError
    from .palette import PaletteError

    try:
        pkg = _load_roles(path)
    except (RolesError, OSError) as exc:
        raise PaletteRolesError(f"no se pudo leer el paquete de roles de la figura «{path}»: {exc}") from exc
    where = f" de la línea «{line}»" if line else ""
    missing = [r for r in pkg.roles if r not in set(palette.ids())]
    if missing:
        raise PaletteRolesError(f"la paleta{where} no tiene los roles que usa la figura: {', '.join(missing)}. "
                                "Agregalos con `afm catalogo paleta agregar` o abrí otra línea")
    try:
        used = palette.with_overrides(overrides or {})
    except PaletteError as exc:
        raise PaletteRolesError(f"los cambios de color de la figura no valen para la paleta{where}: {exc}") from exc
    tm = pkg.to_textured(used)
    report: dict = {"modo": "roles", "paquete": os.path.abspath(path), "roles": list(pkg.roles), "origen": dict(pkg.origen or {}),
                    "paleta": {"hash": used.digest(), "roles": {r.id: r.hex for r in used.roles}}}
    if overrides:
        report["cambios"] = {str(k): used.hex_of(k) for k in sorted(overrides)}     # el hex ya normalizado (mayúscula), el que se usó
    return tm, report
