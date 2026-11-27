"""Generador de probetas de articulación (plan §8.11).

Produce un STL de prueba con varias articulaciones (pasador hongo + cavidad con holgura),
cada una con una combinación distinta de holgura y de diámetro de vástago/cabeza, para que
el cliente elija con su impresora los valores de mínima fricción.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from . import solids as S
from .params import Params, PinSpec
from .pins import mushroom_pin

DEFAULT_CLEARANCES = (0.3, 0.4, 0.5, 0.6)
DEFAULT_SHAFT_DELTAS = (-0.2, 0.0, 0.2)

# --------------------------------------------------------------------------------------------------
# Rótulos y mangos comunes a todas las probetas.
#   · Cada celda lleva su NÚMERO en relieve sobre una plaqueta pegada al bloque (se lee desde arriba, con +X a la
#     derecha y +Y hacia arriba, como en la cama de Bambu Studio). El mismo número encabeza su línea en la tabla
#     (`probetas.txt`), en `probetas.json` (lo que lee la GUI para «usar esta celda») y en el nombre del objeto de
#     `probetas.3mf` (la lista de objetos del laminador).
#   · La pieza que hay que mover a mano termina en un mango que se puede agarrar con dos dedos.
# --------------------------------------------------------------------------------------------------
PLAQUE_LEN = 8.0            # mm de plaqueta delante de cada bloque (hacia −Y)
PLAQUE_T = 1.2              # espesor de la plaqueta
LABEL_H = 0.6               # relieve del número (3 capas de 0,2 mm)
LABEL_PX = 0.9              # lado de cada «punto» de la fuente 5×7 (≥ 2 líneas de una boquilla de 0,4)
HANDLE_POST_R = 1.6         # poste del mango en T (cadera / hombro)
HANDLE_POST_H = 5.0
HANDLE_BAR = (13.0, 3.0, 3.0)   # barra en T: largo × ancho × alto; con 13 mm de largo gira sin tocar a la vecina (paso 16)
HINGE_GRIP_LEN = 10.0       # cuánto más largo es el vástago de la bisagra (palanca)
HINGE_GRIP_BAR = (10.0, 3.0, 3.0)

_FONT = {
    "0": (" ### ", "#   #", "#  ##", "# # #", "##  #", "#   #", " ### "),
    "1": ("  #  ", " ##  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "),
    "2": (" ### ", "#   #", "    #", "   # ", "  #  ", " #   ", "#####"),
    "3": (" ### ", "#   #", "    #", "  ## ", "    #", "#   #", " ### "),
    "4": ("   # ", "  ## ", " # # ", "#  # ", "#####", "   # ", "   # "),
    "5": ("#####", "#    ", "#### ", "    #", "    #", "#   #", " ### "),
    "6": ("  ## ", " #   ", "#    ", "#### ", "#   #", "#   #", " ### "),
    "7": ("#####", "    #", "   # ", "  #  ", " #   ", " #   ", " #   "),
    "8": (" ### ", "#   #", "#   #", " ### ", "#   #", "#   #", " ### "),
    "9": (" ### ", "#   #", "#   #", " ####", "    #", "   # ", " ##  "),
}


def text_width(text: str, px: float = LABEL_PX) -> float:
    """Ancho (mm) de un texto de dígitos en la fuente 5×7 con un punto de separación."""
    return (6 * len(text) - 1) * px


def text_solid(text: str, px: float = LABEL_PX, h: float = LABEL_H) -> S.Manifold:
    """Dígitos en relieve (fuente de puntos 5×7): origen en la esquina inferior izquierda, +X a la derecha, +Y arriba,
    z de 0 a `h`. Solo admite dígitos."""
    if not text or any(ch not in _FONT for ch in text):
        raise ValueError(f"rótulo no admitido: {text!r} (solo dígitos)")
    boxes, x0 = [], 0.0
    for ch in text:
        for row, line in enumerate(_FONT[ch]):
            col = 0
            while col < 5:
                if line[col] != "#":
                    col += 1
                    continue
                end = col
                while end < 5 and line[end] == "#":
                    end += 1
                boxes.append(S.Manifold.cube(((end - col) * px + 0.02, px + 0.02, h), center=False).translate(
                    (x0 + col * px - 0.01, (6 - row) * px - 0.01, 0.0)))
                col = end
        x0 += 6 * px
    return S.union(*boxes)


def number_plaque(number: int, cx: float, y_edge: float, z0: float, width: float, max_width: float | None = None,
                  length: float = PLAQUE_LEN) -> S.Manifold:
    """Plaqueta con el número de la celda en relieve, pegada (0,5 mm de solape) al borde −Y de un bloque.

    `cx`: centro en X; `y_edge`: cara del bloque (la plaqueta sale hacia −Y); `z0`: cara inferior del bloque. El ancho
    crece si el número no cabe (hasta `max_width`); si aun así no cabe se achican los puntos."""
    txt = str(int(number))
    w = max(width, text_width(txt) + 2.0)
    if max_width is not None:
        w = min(w, max_width)
    px = min(LABEL_PX, (w - 1.6) / (6 * len(txt) - 1))
    slab = S.Manifold.cube((w, length + 0.5, PLAQUE_T), center=False).translate((cx - w / 2, y_edge - length, z0))
    tw, th = text_width(txt, px), 7 * px
    label = text_solid(txt, px).translate((cx - tw / 2, y_edge - length / 2 - th / 2, z0 + PLAQUE_T - 0.01))
    return S.union(slab, label)


def write_3mf(objects: list[tuple[str, "trimesh.Trimesh"]], title: str = "Probetas") -> bytes:
    """3MF mínimo (ZIP con el modelo en milímetros): un objeto con NOMBRE por celda, en la posición en que está en la grilla.

    Bambu Studio / Orca muestran esos nombres en la lista de objetos, así que se sabe qué celda es cuál sin mirar la pieza."""
    import io
    import zipfile
    from xml.sax.saxutils import quoteattr

    objs, items = [], []
    for i, (name, mesh) in enumerate(objects, start=1):
        v = "".join(f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in mesh.vertices)
        t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces)
        objs.append(f'<object id="{i}" type="model" name={quoteattr(name)}><mesh><vertices>{v}</vertices>'
                    f'<triangles>{t}</triangles></mesh></object>')
        items.append(f'<item objectid="{i}"/>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="es-AR" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<metadata name="Title">{quoteattr(title)[1:-1]}</metadata>'
             f'<resources>{"".join(objs)}</resources><build>{"".join(items)}</build></model>')
    types = ('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
    return buf.getvalue()


@dataclass
class ProbeCell:
    label: str
    clearance: float
    pin: PinSpec


def probe_cells(p: Params, base: PinSpec | None = None,
                clearances=DEFAULT_CLEARANCES, shaft_deltas=DEFAULT_SHAFT_DELTAS) -> list[ProbeCell]:
    """Todas las combinaciones holgura x diámetro de vástago (la cabeza se mantiene proporcional)."""
    base = base or p.shoulder_pin
    cells = []
    for c in clearances:
        for dd in shaft_deltas:
            pin = replace(base, shaft_d=round(base.shaft_d + dd, 3))
            cells.append(ProbeCell(f"{c:.1f}/{pin.shaft_d:.1f}", c, pin))
    return cells


def _cell(cell: ProbeCell, wall: float = 2.0, pitch: float = 16.0, number: int | None = None) -> S.Manifold:
    """Una articulación: bloque con cavidad + pasador atrapado (que es parte del 'miembro').

    El miembro termina en un mango en T (poste + barra) para poder agarrarlo y girarlo con los dedos; con `number` el
    bloque lleva delante una plaqueta con el número en relieve."""
    pin = cell.pin
    z0 = 0.0
    block_w = pitch - 2.0
    depth = pin.shaft_len + pin.head_h + 2 * wall
    block = S.Manifold.cube((block_w, block_w, depth), center=False).translate((-block_w / 2, -block_w / 2, 0))
    # miembro de prueba: un disco con su pasador que sale hacia +Z dentro del bloque
    disc = S.cylinder_along((0, 0, depth), (0, 0, 1), pin.head_d / 2 + 1.5, wall, 48, start=0)
    pn = mushroom_pin((0, 0, depth), (0, 0, -1), pin)
    limb = S.union(disc, pn)
    cavity = S.dilate(limb, cell.clearance)
    body = S.difference(block, cavity)
    top = depth + wall                                  # cara superior del disco (el mango se agrega después de la cavidad)
    post = S.cylinder_along((0, 0, top - 0.01), (0, 0, 1), HANDLE_POST_R, HANDLE_POST_H + 0.01, 24, start=0)
    bl, bw, bh = HANDLE_BAR
    bar = S.Manifold.cube((bl, bw, bh), center=True).translate((0, 0, top + HANDLE_POST_H + bh / 2 - 0.01))
    limb = S.union(limb, post, bar)
    parts = [body, limb]
    if number is not None:
        parts.append(number_plaque(number, 0.0, -block_w / 2, z0, block_w))
    return S.union(*parts)


def probe_pitch_y(pitch: float = 16.0) -> float:
    """Paso entre filas: el bloque más la plaqueta del número."""
    return pitch + PLAQUE_LEN


def build_probe(p: Params | None = None, cells: list[ProbeCell] | None = None,
                columns: int = 3, pitch: float = 16.0, numbered: bool = True, pitch_y: float | None = None) -> S.Manifold:
    """Placa de probetas en una grilla. Devuelve el sólido completo (piezas separadas por holgura).

    Con `numbered` cada celda lleva su número (el de la tabla) en relieve delante del bloque."""
    p = p or Params()
    cells = cells or probe_cells(p)
    if not numbered:
        return S.union(*[_cell(cell, pitch=pitch).translate(((i % columns) * pitch, -(i // columns) * (pitch_y or pitch), 0))
                         for i, cell in enumerate(cells)])
    return S.union(*build_probe_cells(p, cells, columns, pitch, pitch_y))


def build_probe_cells(p: Params, cells: list[ProbeCell], columns: int = 3, pitch: float = 16.0,
                      pitch_y: float | None = None) -> list[S.Manifold]:
    """Una malla por celda, ya en su lugar de la grilla (para el 3MF con un objeto con nombre por celda)."""
    py = pitch_y or probe_pitch_y(pitch)
    return [_cell(cell, pitch=pitch, number=i + 1).translate(((i % columns) * pitch, -(i // columns) * py, 0))
            for i, cell in enumerate(cells)]


def cell_text(i: int, c: ProbeCell) -> str:
    return f"#{i + 1}: holgura {c.clearance:.1f} mm, vástago Ø{c.pin.shaft_d:.1f} mm"


def labels_text(cells: list[ProbeCell]) -> str:
    """Tabla de referencia (holgura/vástago) en el orden de la grilla, para anotar el resultado."""
    return "\n".join(cell_text(i, c) for i, c in enumerate(cells))


# --------------------------------------------------------------------------------------------------
# Probeta de bisagra (rodilla / codo): plan de articulaciones §4, Fase 6.
# --------------------------------------------------------------------------------------------------
DEFAULT_HINGE_CLEARANCES = (0.3, 0.4, 0.5)
DEFAULT_HINGE_PIN_DELTAS = (-0.2, 0.0, 0.2)
HINGE_PROBE_RANGE = (0.0, 90.0)     # barrido tallado en la ranura de cada celda (grados, alrededor de +X)
HINGE_PROBE_STEP = 15.0


@dataclass
class HingeProbeCell:
    label: str
    clearance: float          # holgura radial
    axial_clearance: float    # holgura a lo largo del eje (lengüeta vs. paredes de la ranura)
    hinge: object             # HingeSpec


def hinge_probe_cells(p: Params, base=None, clearances=DEFAULT_HINGE_CLEARANCES,
                      pin_deltas=DEFAULT_HINGE_PIN_DELTAS, axial_clearances=None) -> list[HingeProbeCell]:
    """Combinaciones holgura radial × Ø de bulón × holgura axial (por defecto solo la de `Params`)."""
    base = base or p.hinge_pin
    axials = tuple(axial_clearances) if axial_clearances else (p.axial_clearance,)
    cells = []
    for c in clearances:
        for dd in pin_deltas:
            hs = replace(base, pin_d=round(base.pin_d + dd, 3))
            for ax in axials:
                cells.append(HingeProbeCell(f"r{c:.2f}/d{hs.pin_d:.1f}/a{ax:.2f}", float(c), float(ax), hs))
    return cells


def _hinge_cell(cell: HingeProbeCell, wall: float = 1.5, stem_len: float = 6.0, for_print: bool = False,
                number: int | None = None, max_w: float | None = None) -> S.Manifold:
    """Una bisagra de prueba: yugo (bloque con ranura y agujeros ciegos) + pieza distal (disco, vástago y 2 bulones).

    Eje de la bisagra = +X por el origen. La pieza distal queda retenida: el disco es más ancho que su vástago y
    los bulones están en agujeros cerrados. La ranura se talla con el barrido del distal dilatado (como `joints`).

    El vástago se alarga con una palanca terminada en una barra en T (se agrega DESPUÉS de tallar la ranura: sale del
    yugo y no la toca en todo el barrido). Con `for_print` la celda se da vuelta (el yugo apoya sobre la cama y la
    palanca sale hacia arriba, sin soportes; el eje queda a `z = top`; la palanca gira hacia el frente, −Y, donde está el número)
    y, con `number`, lleva delante la plaqueta del número.
    Sin `for_print` queda en el marco del eje (el de las pruebas)."""
    from .joints import _bolt, dilate_anisotropic
    h = cell.hinge
    axis = np.array([1.0, 0.0, 0.0])
    r = h.end_r
    disc = S.cylinder_along((0, 0, 0), axis, r, h.tongue_t, 64, start=-h.tongue_t / 2)
    stem_w = 2 * max(r - 1.0, 1.0)
    stem = S.Manifold.cube((h.tongue_t, stem_w, stem_len + 0.5), center=False).translate(
        (-h.tongue_t / 2, -stem_w / 2, -stem_len))
    bolts = [_bolt((0, 0, 0), axis * s, h, 32) for s in (1.0, -1.0)]
    distal = S.largest_component(S.union(disc, stem, *bolts))
    half_x = h.tongue_t / 2 + h.pin_len + cell.axial_clearance + wall
    half_y = stem_len
    top = r + cell.clearance + wall
    block = S.Manifold.cube((2 * half_x, 2 * half_y, top + 2.0), center=False).translate((-half_x, -half_y, -2.0))
    grown = dilate_anisotropic(distal, cell.clearance, cell.axial_clearance, axis)
    lo, hi = HINGE_PROBE_RANGE
    angles = list(np.arange(lo, hi + 1e-9, HINGE_PROBE_STEP))
    sweep = S.union(*[S.rotate_about_axis(grown, (0, 0, 0), axis, a) for a in angles])
    # palanca: continúa el vástago hacia abajo (−Z) más allá del yugo y termina en una barra en T
    lever = S.Manifold.cube((h.tongue_t, stem_w, HINGE_GRIP_LEN + 0.02), center=False).translate(
        (-h.tongue_t / 2, -stem_w / 2, -stem_len - HINGE_GRIP_LEN))
    gl, gw, gh = HINGE_GRIP_BAR
    tbar = S.Manifold.cube((gl, gw, gh), center=True).translate((0, 0, -stem_len - HINGE_GRIP_LEN - gh / 2 + 0.01))
    cellm = S.union(S.difference(block, sweep), distal, lever, tbar)
    if not for_print:
        return cellm
    flipped = cellm.transform(np.array([[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, -1.0, top]]))   # z -> top − z: el yugo queda en z ≥ 0
    if number is None:
        return flipped
    return S.union(flipped, number_plaque(number, 0.0, -half_y, 0.0, 2 * half_x, max_width=max_w))


def hinge_pitch_y(stem_len: float = 6.0) -> float:
    return 2 * stem_len + 4.0 + PLAQUE_LEN


def build_hinge_probe(p: Params | None = None, cells: list[HingeProbeCell] | None = None,
                      columns: int = 3, pitch_x: float = 14.0, pitch_y: float | None = None,
                      for_print: bool = True) -> S.Manifold:
    """Placa de probetas de bisagra en una grilla (piezas distales sueltas dentro de su yugo).

    `for_print` (por defecto): cada celda se imprime con el yugo sobre la cama, la palanca hacia arriba y su número en
    relieve delante. Con `for_print=False` las celdas quedan en el marco del eje (eje +X por el origen, sin número)."""
    p = p or Params()
    cells = cells or hinge_probe_cells(p)
    py = pitch_y or (hinge_pitch_y() if for_print else 16.0)
    return S.union(*[_hinge_cell(cell, for_print=for_print, number=i + 1 if for_print else None,
                                 max_w=pitch_x - 2.0).translate(((i % columns) * pitch_x, -(i // columns) * py, 0))
                     for i, cell in enumerate(cells)])


def build_hinge_probe_cells(p: Params, cells: list[HingeProbeCell], columns: int = 3, pitch_x: float = 14.0,
                            pitch_y: float | None = None) -> list[S.Manifold]:
    py = pitch_y or hinge_pitch_y()
    return [_hinge_cell(cell, for_print=True, number=i + 1, max_w=pitch_x - 2.0).translate(((i % columns) * pitch_x, -(i // columns) * py, 0))
            for i, cell in enumerate(cells)]


def hinge_cell_text(i: int, c: HingeProbeCell) -> str:
    return (f"#{i + 1}: holgura radial {c.clearance:.2f} mm, axial {c.axial_clearance:.2f} mm, "
            f"bulón Ø{c.hinge.pin_d:.1f} mm (lengüeta {c.hinge.tongue_t:.1f} mm)")


def hinge_labels_text(cells: list[HingeProbeCell]) -> str:
    """Tabla de referencia en el orden de la grilla, para anotar cuál gira suave y sostiene la pose."""
    return "\n".join(hinge_cell_text(i, c) for i, c in enumerate(cells))


# --------------------------------------------------------------------------------------------------
# Probeta de giro (swivel: bíceps / muslo / canilla): PLAN_plugins_articulacion.md §4.1, hilo físico H1.
# --------------------------------------------------------------------------------------------------
SWIVEL_DIST_LEN = 5.0          # mm de la mitad distal (la que lleva el pasador y el mango)


@dataclass
class SwivelProbeCell:
    label: str
    clearance: float
    pin: PinSpec


def swivel_probe_cells(p: Params, base: PinSpec | None = None, clearances=DEFAULT_CLEARANCES,
                       shaft_deltas=DEFAULT_SHAFT_DELTAS) -> list[SwivelProbeCell]:
    """Todas las combinaciones holgura × Ø de vástago del tipo `swivel` (la cabeza se mantiene). Parte del pasador por defecto
    del tipo (`joint_types.swivel.DEFAULT_PIN`), que es el de una figura de ~130 mm."""
    from .joint_types.swivel import DEFAULT_PIN
    base = base or DEFAULT_PIN
    cells = []
    for c in clearances:
        for dd in shaft_deltas:
            pin = replace(base, shaft_d=round(base.shaft_d + dd, 3))
            cells.append(SwivelProbeCell(f"{c:.1f}/{pin.shaft_d:.1f}", float(c), pin))
    return cells


def _swivel_cell(cell: SwivelProbeCell, p: Params, pitch: float = 16.0, number: int | None = None) -> S.Manifold:
    """Un giro de prueba: se arma con el MISMO código del tipo (`Swivel.build`) sobre un bloque cuadrado partido por un plano, así lo que se
    imprime es lo que haría la figura. Se imprime con el eje en Z, la mitad proximal (con la cavidad y el collar) abajo y la distal (con el
    pasador) arriba, con un mango en T para girarla con los dedos."""
    from .joint_types.base import JointSite
    from .joint_types.swivel import Swivel
    from .limbs import ContactFace
    from .planes import Plane

    t = Swivel()
    q = replace(p, clearance=cell.clearance)
    w = pitch - 2.0
    prox_len = t.requires(cell.pin, q).profundidad_mm + 2.0 * q.min_wall + q.clearance      # cuerpo para la cavidad + piel
    total = prox_len + SWIVEL_DIST_LEN
    child = S.Manifold.cube((w, w, total), center=False).translate((-w / 2, -w / 2, -total))
    face = ContactFace(center=np.array([0.0, 0.0, 0.0]), outward=np.array([0.0, 0.0, 1.0]), area=w * w)
    plane = Plane.from_normal((0.0, 0.0, -prox_len), (0.0, 0.0, 1.0), "giro")
    res = t.build(JointSite(slot="canilla", side="izq", child=child, face=face, params=q, jp=cell.pin, plane=plane, flex=(0.0, 360.0),
                            segments=24))
    flip = lambda m: S.rotate_about_axis(m, (0, 0, 0), (1, 0, 0), 180.0)             # noqa: E731  la proximal queda abajo, apoyada en la cama
    prox, dist = flip(res.pieces["prox"]), flip(res.pieces["dist"])
    post = S.cylinder_along((0, 0, total - 0.01), (0, 0, 1), HANDLE_POST_R, HANDLE_POST_H + 0.01, 24, start=0)
    bl, bw, bh = HANDLE_BAR
    bar = S.Manifold.cube((bl, bw, bh), center=True).translate((0, 0, total + HANDLE_POST_H + bh / 2 - 0.01))
    parts = [prox, S.union(dist, post, bar)]
    if number is not None:
        parts.append(number_plaque(number, 0.0, -w / 2, 0.0, w))
    return S.union(*parts)


def build_swivel_probe_cells(p: Params, cells: list[SwivelProbeCell], columns: int = 3, pitch: float = 16.0) -> list[S.Manifold]:
    """Una malla por celda, ya en su lugar de la grilla (para el 3MF con un objeto con nombre por celda)."""
    py = probe_pitch_y(pitch)
    return [_swivel_cell(c, p, pitch=pitch, number=i + 1).translate(((i % columns) * pitch, -(i // columns) * py, 0))
            for i, c in enumerate(cells)]


def build_swivel_probe(p: Params | None = None, cells: list[SwivelProbeCell] | None = None, columns: int = 3,
                       pitch: float = 16.0) -> S.Manifold:
    p = p or Params()
    return S.union(*build_swivel_probe_cells(p, cells or swivel_probe_cells(p), columns, pitch))


def swivel_cell_text(i: int, c: SwivelProbeCell) -> str:
    return f"#{i + 1}: holgura {c.clearance:.1f} mm, vástago Ø{c.pin.shaft_d:.1f} mm"


def swivel_labels_text(cells: list[SwivelProbeCell]) -> str:
    """Tabla en el orden de la grilla, para anotar cuál gira libre, sin juego ni roce y no se sale al tirar."""
    return "\n".join(swivel_cell_text(i, c) for i, c in enumerate(cells))


# --------------------------------------------------------------------------------------------------
# Probeta de clic (hombro / cadera): PLAN_mecanismo_clic_hombro_cadera.md §5, Fase 0.
# --------------------------------------------------------------------------------------------------
DEFAULT_DETENT_DEPTH_DELTAS = (-0.2, 0.0, 0.2)       # sobre `DetentSpec.notch_depth`
DEFAULT_DETENT_THICKNESSES = (1.0, 1.2, 1.4)         # `finger_t` (mm)
DEFAULT_DETENT_GAPS = (0.2, 0.3, 0.4)                # `detent_gap`: una placa por valor


@dataclass
class DetentProbeCell:
    label: str
    spec: object              # DetentSpec


def detent_probe_cells(p: Params, base=None, depth_deltas=DEFAULT_DETENT_DEPTH_DELTAS,
                       thicknesses=DEFAULT_DETENT_THICKNESSES, gap: float | None = None) -> list[DetentProbeCell]:
    """Grilla 3×3 profundidad de muesca × espesor de lengüeta, con una holgura de punta fija (una placa por holgura).
    El flex_gap sigue a la flexión de la punta (δ + ranura) para que la lengüeta no choque al flexar."""
    base = base or p.detent
    g = base.detent_gap if gap is None else gap
    cells = []
    for dd in depth_deltas:
        for t in thicknesses:
            nd = round(base.notch_depth + dd, 3)
            sp = replace(base, notch_depth=nd, finger_t=float(t), detent_gap=float(g),
                         flex_gap=round(max(nd - g, 0.1) + base.slit_w, 3))
            cells.append(DetentProbeCell(f"n{nd:.1f}/t{t:.1f}/g{g:.2f}", sp))
    return cells


def _detent_cell(cell: DetentProbeCell, p: Params, wall: float = 2.5, handle_len: float = 12.0,
                 number: int | None = None) -> S.Manifold:
    """Una articulación de clic de prueba: bloque con bolsillo, 2 lengüetas y puntas + miembro suelto (disco con
    muescas, hongo y palanca). Eje = +X por el origen; la cara de contacto es x = 0 y el torso queda hacia −X.
    Se imprime con X horizontal: las lengüetas (arriba/abajo, largas en X) quedan horizontales, como en la figura."""
    from . import detent as DT
    sp = cell.spec
    parts = DT.make_parts((0.0, 0.0, 0.0), (-1.0, 0.0, 0.0), sp)
    pin = mushroom_pin((-sp.disc_h, 0.0, 0.0), (-1.0, 0.0, 0.0), p.shoulder_pin)
    handle = S.cylinder_along((0.3, 0, 0), (1, 0, 0), 3.0, handle_len, 48)
    bar = S.Manifold.cube((3.0, 2 * (sp.disc_d / 2 + 4.0), 3.0), center=True).translate((0.3 + handle_len, 0, 0))
    limb = S.union(pin, parts.disc_notch, handle, bar)
    root = parts.root_depth
    half_y = sp.finger_w / 2 + sp.slit_w + wall
    top = sp.disc_d / 2 + sp.slit_w + sp.finger_t + sp.flex_gap + wall
    block = S.Manifold.cube((root + 2.0 + wall, 2 * half_y, 2 * top), center=False).translate((-(root + 2.0 + wall), -half_y, -top))
    pocket = S.dilate(S.union(pin, parts.disc_plain), p.clearance)
    body = S.difference(block, pocket)
    slots = [f.slot for f in parts.fingers]
    body = S.union(S.difference(body, *slots), *[f.beam for f in parts.fingers], *[f.ridge for f in parts.fingers])
    out = S.union(body, limb)
    if number is not None:      # plaqueta con el número delante del bloque, apoyada en la cama (cara inferior z = −top)
        blk = root + 2.0 + wall
        out = S.union(out, number_plaque(number, -blk / 2, -half_y, -top, blk))
    return out


def detent_pitch_y(pitch_y: float = 22.0) -> float:
    return pitch_y + PLAQUE_LEN


def build_detent_probe(p: Params | None = None, cells: list[DetentProbeCell] | None = None,
                       columns: int = 3, pitch_x: float = 32.0, pitch_y: float | None = None,
                       numbered: bool = True) -> S.Manifold:
    """Placa de probetas de clic en una grilla (miembros sueltos dentro de su bolsillo), cada una con su número."""
    p = p or Params()
    cells = cells or detent_probe_cells(p)
    py = pitch_y or (detent_pitch_y() if numbered else 22.0)
    return S.union(*[_detent_cell(cell, p, number=i + 1 if numbered else None).translate(
        ((i % columns) * pitch_x, -(i // columns) * py, 0)) for i, cell in enumerate(cells)])


def build_detent_probe_cells(p: Params, cells: list[DetentProbeCell], columns: int = 3, pitch_x: float = 32.0) -> list[S.Manifold]:
    py = detent_pitch_y()
    return [_detent_cell(cell, p, number=i + 1).translate(((i % columns) * pitch_x, -(i // columns) * py, 0))
            for i, cell in enumerate(cells)]


def detent_labels_text(cells: list[DetentProbeCell]) -> str:
    """Tabla en el orden de la grilla, con la deformación, la fuerza y el par que estima el modelo, para anotar
    cuál se mueve, hace clic y no se funde (¿cuánto juego angular queda? ¿cuánto par cuesta moverlo?)."""
    from . import detent as DT
    rows = []
    for i, c in enumerate(cells):
        s = c.spec
        rows.append(f"#{i + 1}: muesca {s.notch_depth:.2f} mm, lengüeta {s.finger_t:.1f} × {s.finger_len:g} mm, holgura de punta "
                    f"{s.detent_gap:.2f} mm | ε {100 * DT.tongue_strain(s):.2f} %, F {DT.tongue_force(s):.2f} N, "
                    f"par est. {DT.hold_torque(s):.1f} N·mm, juego ±{DT.angular_play_deg(s):.1f}°")
    return "\n".join(rows)


# --------------------------------------------------------------------------------------------------
# Probeta de la montura de armas (puente «lazo»): PLAN_enganche_armas_cuerpo.md, M09.
# --------------------------------------------------------------------------------------------------
DEFAULT_MOUNT_CLEARANCES = (0.15, 0.25, 0.35, 0.45)   # alrededor de `mount.DEFAULT_CLEARANCE` (0,25)
MOUNT_PROBE_BLADE = (4.0, 0.8)                        # ancho × espesor de la hoja de prueba (mm): el de `props.make_prop`
MOUNT_PROBE_BLADE_LEN = 20.0                          # largo de la hoja de la tira (mm)
MOUNT_PROBE_PLATE_T = 2.0                             # espesor de la placa base (mm); el pie del puente se hunde 1 mm
MOUNT_PROBE_PLATE_LEN = 20.0                          # largo de la placa a lo largo de la hoja (mm)
MOUNT_PROBE_MARGIN = 3.0                              # mm de placa a cada lado del puente, a lo ancho
MOUNT_PROBE_DOT = 1.0                                 # lado (mm) de los puntos que numeran la celda
MOUNT_PROBE_DOT_PITCH = 1.4                           # mm entre puntos; empiezan a 2,5 mm del puente, del lado de la punta
MOUNT_PROBE_MAX_CELLS = 5                             # con más celdas los puntos no caben en la placa


@dataclass
class MountProbeCell:
    label: str
    clearance: float


def mount_probe_cells(clearances=DEFAULT_MOUNT_CLEARANCES) -> list[MountProbeCell]:
    """Una celda por holgura de hoja (juego entre la hoja y el hueco, por lado)."""
    cs = [float(c) for c in clearances]
    if not cs or any(not (0.0 < c <= 2.0) for c in cs):
        raise ValueError("las holguras de la probeta de montura tienen que estar entre 0 y 2 mm")
    if len(cs) > MOUNT_PROBE_MAX_CELLS:
        raise ValueError(f"la probeta de montura admite hasta {MOUNT_PROBE_MAX_CELLS} holguras (pidió {len(cs)})")
    return [MountProbeCell(f"{c:.2f}", c) for c in cs]


def mount_probe_blade(blade_w: float = MOUNT_PROBE_BLADE[0], blade_t: float = MOUNT_PROBE_BLADE[1],
                      blade_len: float = MOUNT_PROBE_BLADE_LEN, guard_w: float = 10.0, guard_t: float = 2.0,
                      handle_len: float = 6.0) -> S.Manifold:
    """Tira de prueba en el marco del accesorio (hoja hacia −X, guarda en x ∈ [−guard_t, 0], empuñadura plana hacia +X).
    Imprimirla acostada con las mismas capas que la espada: si pasa por el puente de la placa, pasa la espada."""
    if min(blade_w, blade_t, blade_len, guard_w, guard_t, handle_len) <= 0:
        raise ValueError("las medidas de la tira de prueba tienen que ser mayores que 0")

    def box(xa, xb, wy, zt):
        return S.Manifold.cube((xb - xa, wy, zt)).translate((xa, -wy / 2, 0.0))

    return S.union(box(-guard_t - blade_len, -guard_t, blade_w, blade_t), box(-guard_t, 0.0, guard_w, max(guard_t, blade_t)),
                   box(0.0, handle_len, min(blade_w, 3.0), blade_t))


def build_mount_probe(p: Params | None = None, cells: list[MountProbeCell] | None = None, blade_w: float = MOUNT_PROBE_BLADE[0],
                      blade_t: float = MOUNT_PROBE_BLADE[1], wall: float | None = None) -> tuple:
    """Placa plana con un puente `lazo` por celda (uno al lado del otro, de más a menos juego según el orden de `cells`).
    Cada puente sale de `mount.build_lazo` y se une con `mount.attach_lazo`: es el mismo que se pone en la figura. La celda
    `i` lleva `i` puntos en relieve al costado. Devuelve `(placa, tira, [Lazo])`."""
    from . import mount as MT

    p = p or Params()
    cells = cells or mount_probe_cells()
    wall = float(p.min_wall if wall is None else wall)
    strip = mount_probe_blade(blade_w, blade_t)
    # ancho de cada celda: el del puente más margen (las celdas tienen el mismo, salvo por la holgura)
    cw = lambda c: blade_w + 2 * c + 2 * wall + 2 * MOUNT_PROBE_MARGIN          # noqa: E731
    total_w = sum(cw(c.clearance) for c in cells)
    plate = S.Manifold.cube((MOUNT_PROBE_PLATE_LEN, total_w, MOUNT_PROBE_PLATE_T)).translate((-MOUNT_PROBE_PLATE_LEN / 2, 0.0, 0.0))
    lazos, y = [], 0.0
    for i, cell in enumerate(cells, start=1):
        yc = y + cw(cell.clearance) / 2
        spec = MT.MountSpec(arma="probeta@1", lugar="pelvis", ancla=(0.0, yc, MOUNT_PROBE_PLATE_T), direccion=(1.0, 0.0, 0.0),
                            holgura=cell.clearance, pared=wall)
        lz = MT.build_lazo(spec, strip, p.min_wall, normal=(0.0, 0.0, 1.0))
        plate = MT.attach_lazo(plate, lz)
        lazos.append(lz)
        # los puntos van del lado de la punta (x > 0) y fuera del ancho de la hoja: la guarda, que sale hacia −X, nunca pasa por ahí
        dots = [S.Manifold.cube((MOUNT_PROBE_DOT, MOUNT_PROBE_DOT, MOUNT_PROBE_DOT * 0.6)).translate(
            (2.5 + MOUNT_PROBE_DOT_PITCH * k, y + 1.0, MOUNT_PROBE_PLATE_T - 0.05)) for k in range(i)]
        plate = S.union(plate, *dots)
        y += cw(cell.clearance)
    return plate, strip, lazos


def mount_labels_text(cells: list[MountProbeCell], lazos: list, blade_w: float, blade_t: float, wall: float) -> str:
    """Tabla de referencia: número de la celda (puntos en la placa), holgura y abertura resultante."""
    rows = [f"#{i + 1} ({i + 1} punto{'s' if i else ''}): holgura de hoja {c.clearance:.2f} mm por lado, "
            f"abertura {lz.hole_w:.2f} × {lz.hole_t:.2f} mm (ancho × alto)" for i, (c, lz) in enumerate(zip(cells, lazos))]
    head = (f"Hoja de prueba {blade_w:g} × {blade_t:g} mm, pared del puente {wall:g} mm. Imprimir la placa y la tira acostadas, con las mismas capas que la espada (el techo del puente cruza ~4,5 mm: anotar si hizo falta soporte).\n"
            "Pasar la tira (hoja primero) por cada puente; la guarda tiene que apoyar en la cara del puente. Elegir la celda donde entra\n"
            "sin forzar y no queda floja, y pasar su valor como `holgura` de la montura.")
    return head + "\n" + "\n".join(rows)


def _stl_bytes(mesh) -> bytes:
    return S.to_trimesh(mesh).export(file_type="stl")


def _objects(names: list[str], meshes: list) -> list:
    return [(n, S.to_trimesh(m)) for n, m in zip(names, meshes)]


def _manifest(tipo: str, cells: list[dict], **extra) -> bytes:
    import json
    data = {"formato": "afm-probetas-1", "tipo": tipo, **extra, "celdas": cells}
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()


def probe_files(tipo: str = "hongo", clearances=None, deltas=None, axiales=None, boquilla: float = 0.4,
                gaps=None, espesores=None, hoja=None, pared=None, pasador: str = "hombro",
                params: Params | None = None) -> tuple[dict[str, bytes], str]:
    """Genera las probetas de calibración como archivos en memoria: ({nombre: bytes}, texto de la tabla).

    Es la única implementación: la usan `afm probe` (CLI), la ventana y la página (Pyodide), así que dan los mismos archivos:
    el `.stl` (la placa), el `.3mf` (la misma placa con un objeto con nombre por celda, para la lista de objetos del
    laminador), la tabla `.txt` y el `.json` con los valores de cada celda (lo que lee «usar esta celda»).

    `pasador`: «hombro» o «cadera» (solo tipo hongo: de cuál se parte). `params`: parámetros de partida (los del proyecto abierto
    en la ventana; por defecto los de fábrica)."""
    import dataclasses

    p = params or Params()
    if tipo == "clic":
        from . import detent as DT
        p = dataclasses.replace(p, nozzle=boquilla, detent=DT.spec_for_nozzle(boquilla))
        thick = tuple(espesores) if espesores else ((1.4, 1.6, 1.8) if boquilla >= 0.7 else DEFAULT_DETENT_THICKNESSES)
        files: dict[str, bytes] = {}
        blocks, jcells = [], []
        for g in (gaps or (0.2, 0.3, 0.4)):
            cells = detent_probe_cells(p, thicknesses=thick, gap=g)
            fn = f"probetas_clic_b{boquilla:g}_gap{g:g}"
            ms = build_detent_probe_cells(p, cells)
            files[fn + ".stl"] = _stl_bytes(S.union(*ms))
            files[fn + ".3mf"] = write_3mf(_objects([f"{fn} #{i + 1}" for i in range(len(cells))], ms), fn)
            blocks.append(f"== {fn}.stl (boquilla {boquilla:g} mm, holgura de punta {g:g} mm) ==\n" + detent_labels_text(cells))
            jcells += [{"n": i + 1, "archivo": fn + ".stl", "detent": dataclasses.asdict(c.spec)} for i, c in enumerate(cells)]
        text = "\n\n".join(blocks)
        files[f"probetas_clic_b{boquilla:g}.txt"] = (text + "\n").encode()
        files["probetas.json"] = _manifest("clic", jcells, boquilla=boquilla)
        return files, text
    if tipo == "montura":
        bw, bt = (float(hoja[0]), float(hoja[1])) if hoja else MOUNT_PROBE_BLADE
        wall = float(pared) if pared else p.min_wall
        cells = mount_probe_cells(clearances or DEFAULT_MOUNT_CLEARANCES)
        plate, strip, lazos = build_mount_probe(p, cells, bw, bt, wall)
        text = mount_labels_text(cells, lazos, bw, bt, wall)
        return {"probeta_montura.stl": _stl_bytes(plate), "tira_hoja.stl": _stl_bytes(strip),
                "probeta_montura.txt": (text + "\n").encode()}, text      # sin .json: la holgura va en cada `MountSpec`, no en el perfil
    if tipo == "bisagra":
        kw = {}
        if clearances:
            kw["clearances"] = list(clearances)
        if deltas:
            kw["pin_deltas"] = list(deltas)
        if axiales:
            kw["axial_clearances"] = list(axiales)
        cells = hinge_probe_cells(p, **kw)
        ms = build_hinge_probe_cells(p, cells)
        mesh, text = S.union(*ms), hinge_labels_text(cells)
        objs = _objects([hinge_cell_text(i, c) for i, c in enumerate(cells)], ms)
        manifest = _manifest("bisagra", [{"n": i + 1, "holgura": c.clearance, "holgura_axial": c.axial_clearance,
                                          "bulon": c.hinge.pin_d} for i, c in enumerate(cells)])
    elif tipo == "giro":
        cells = swivel_probe_cells(p, clearances=list(clearances or DEFAULT_CLEARANCES), shaft_deltas=list(deltas or DEFAULT_SHAFT_DELTAS))
        ms = build_swivel_probe_cells(p, cells)
        mesh, text = S.union(*ms), swivel_labels_text(cells)
        objs = _objects([swivel_cell_text(i, c) for i, c in enumerate(cells)], ms)
        manifest = _manifest("giro", [{"n": i + 1, "holgura": c.clearance, "vastago": c.pin.shaft_d} for i, c in enumerate(cells)])
    elif tipo == "hongo":
        if pasador not in ("hombro", "cadera"):
            raise ValueError(f"pasador desconocido: {pasador!r} (hombro o cadera)")
        base = p.hip_pin if pasador == "cadera" else p.shoulder_pin
        cells = probe_cells(p, base=base, clearances=list(clearances or DEFAULT_CLEARANCES),
                            shaft_deltas=list(deltas or DEFAULT_SHAFT_DELTAS))
        ms = build_probe_cells(p, cells)
        mesh, text = S.union(*ms), labels_text(cells)
        objs = _objects([cell_text(i, c) for i, c in enumerate(cells)], ms)
        manifest = _manifest("hongo", [{"n": i + 1, "holgura": c.clearance, "vastago": c.pin.shaft_d}
                                       for i, c in enumerate(cells)], pasador=pasador)
    else:
        raise ValueError(f"tipo de probeta desconocido: {tipo!r}")
    return {"probetas.stl": _stl_bytes(mesh), "probetas.3mf": write_3mf(objs, "Probetas " + tipo),
            "probetas.txt": (text + "\n").encode(), "probetas.json": manifest}, text
