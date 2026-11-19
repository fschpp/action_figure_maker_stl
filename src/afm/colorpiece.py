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

from dataclasses import dataclass, field

import numpy as np

from .frame import SIMILARITY_OK_MM, FigureFrame
from .texmesh import TexturedMesh


class PieceColorError(ValueError):
    """El FBX de la pieza no se puede usar como su fuente de color (no coincide con el STL, o no trae textura de color)."""


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


def align_fbx_to_piece(tm: TexturedMesh, piece_stl, *, refine: bool | None = None, ok_mm: float = SIMILARITY_OK_MM,
                       require_ok: bool = True) -> FigureFrame:
    """Alinea el FBX `tm` con el STL de la pieza (`piece_stl`, cualquier objeto con `vertices` y `faces`): `FigureFrame` con la 4×4
    FBX → marco del STL y el informe (`media_mm`, `p95_mm`, `max_mm`, `ok`…).

    `ok` es «la media de las distancias de los vértices del STL a la superficie del FBX no pasa de `ok_mm`». Con `require_ok`
    (por defecto) un FBX que no calza levanta `PieceColorError` con las medidas; con `require_ok=False` se devuelve igual y el
    llamador decide con `report["ok"]`."""
    if "base_color" not in tm.images:
        raise PieceColorError("el FBX no trae textura de color")
    frame = FigureFrame.from_piece_similarity(tm.vertices, piece_stl, refine=refine, source_faces=tm.faces)
    frame.report["ok"] = bool(frame.report["media_mm"] <= ok_mm)
    if require_ok and not frame.report["ok"]:
        raise PieceColorError(f"el FBX no coincide con el STL de la pieza: distancia media {frame.report['media_mm']:.3f} mm "
                              f"(máximo aceptado {ok_mm:g} mm)")
    return frame


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


def catalog_color_sources(placed: dict, catalog, scaled=(), *, spacing_mm: float = 0.08) -> tuple[dict, dict]:
    """`(sources, info)` para `Colorizer(sources=...)` de una figura armada desde el catálogo (T36).

    `placed`: `BuildResult.placed` (nombre → `PlacedPart`). Para cada pieza cuya ficha trae `color` se arma un proveedor (se resuelve
    al colorear la pieza, no antes) que lleva su FBX al marco del huésped con `S = T · A`: `T` es `PlacedPart.T` y `A` la similitud
    guardada en el catálogo (T35), así que no se vuelve a alinear. La clave es el nombre sin sufijo `_izq/_der`: con el lado derecho
    espejado la pieza `_der` se registra reflejada contra la misma fuente (como cualquier otra pieza). Un lado `_der` propio
    (figura asimétrica) no se mira: usa el color del izquierdo.

    `scaled`: tipos de pieza que la receta escala (`scale_choice` cambia la malla y `T` no lo recoge): esas piezas quedan sin color
    propio. `info` = `{clave: {"pieza": ref, "fbx": ruta}}` más `omitidas` `{clave: motivo}`."""
    from .colorreg import _split_name

    sources: dict = {}
    info: dict = {"piezas": {}, "omitidas": {}}
    for name, pp in placed.items():
        meta = pp.choice.meta
        key, side = _split_name(name)
        if not getattr(meta, "color", None) or side == "der" or key in sources:
            continue
        if meta.tipo in set(scaled):
            info["omitidas"][key] = f"{meta.ref} está escalada: el color no se lleva"
            continue
        try:
            fbx = catalog.color_file(meta.ref)
        except ValueError as exc:                       # CatalogError: sin FBX o con otro hash
            info["omitidas"][key] = str(exc)
            continue
        info["piezas"][key] = {"pieza": meta.ref, "fbx": fbx}

        def provide(res, _fbx=fbx, _T=np.asarray(pp.T, float), _A=meta.color["similitud"], _sp=spacing_mm):
            from .fbxio import load_textured

            return piece_color_source(load_textured(_fbx), None, _T, similarity=_A, spacing_mm=_sp).source
        sources[key] = provide
    return sources, info
