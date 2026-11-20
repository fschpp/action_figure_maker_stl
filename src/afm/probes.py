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


def _cell(cell: ProbeCell, wall: float = 2.0, pitch: float = 16.0) -> S.Manifold:
    """Una articulación: bloque con cavidad + pasador atrapado (que es parte del 'miembro')."""
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
    return S.union(body, limb)


def build_probe(p: Params | None = None, cells: list[ProbeCell] | None = None,
                columns: int = 3, pitch: float = 16.0) -> S.Manifold:
    """Placa de probetas en una grilla. Devuelve el sólido completo (piezas separadas por holgura)."""
    p = p or Params()
    cells = cells or probe_cells(p)
    parts = []
    for i, cell in enumerate(cells):
        r, c = divmod(i, columns)
        parts.append(_cell(cell, pitch=pitch).translate((c * pitch, -r * pitch, 0)))
    return S.union(*parts)


def labels_text(cells: list[ProbeCell]) -> str:
    """Tabla de referencia (holgura/vástago) en el orden de la grilla, para anotar el resultado."""
    return "\n".join(f"#{i + 1}: holgura {c.clearance:.1f} mm, vástago Ø{c.pin.shaft_d:.1f} mm"
                     for i, c in enumerate(cells))


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


def _hinge_cell(cell: HingeProbeCell, wall: float = 1.5, stem_len: float = 6.0) -> S.Manifold:
    """Una bisagra de prueba: yugo (bloque con ranura y agujeros ciegos) + pieza distal (disco, vástago y 2 bulones).

    Eje de la bisagra = +X por el origen. La pieza distal queda retenida: el disco es más ancho que su vástago y
    los bulones están en agujeros cerrados. La ranura se talla con el barrido del distal dilatado (como `joints`)."""
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
    return S.union(S.difference(block, sweep), distal)


def build_hinge_probe(p: Params | None = None, cells: list[HingeProbeCell] | None = None,
                      columns: int = 3, pitch_x: float = 14.0, pitch_y: float = 16.0) -> S.Manifold:
    """Placa de probetas de bisagra en una grilla (piezas distales sueltas dentro de su yugo)."""
    p = p or Params()
    cells = cells or hinge_probe_cells(p)
    parts = []
    for i, cell in enumerate(cells):
        r, c = divmod(i, columns)
        parts.append(_hinge_cell(cell).translate((c * pitch_x, -r * pitch_y, 0)))
    return S.union(*parts)


def hinge_labels_text(cells: list[HingeProbeCell]) -> str:
    """Tabla de referencia en el orden de la grilla, para anotar cuál gira suave y sostiene la pose."""
    return "\n".join(f"#{i + 1}: holgura radial {c.clearance:.2f} mm, axial {c.axial_clearance:.2f} mm, "
                     f"bulón Ø{c.hinge.pin_d:.1f} mm (lengüeta {c.hinge.tongue_t:.1f} mm)" for i, c in enumerate(cells))


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


def _detent_cell(cell: DetentProbeCell, p: Params, wall: float = 2.5, handle_len: float = 12.0) -> S.Manifold:
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
    return S.union(body, limb)


def build_detent_probe(p: Params | None = None, cells: list[DetentProbeCell] | None = None,
                       columns: int = 3, pitch_x: float = 32.0, pitch_y: float = 22.0) -> S.Manifold:
    """Placa de probetas de clic en una grilla (miembros sueltos dentro de su bolsillo)."""
    p = p or Params()
    cells = cells or detent_probe_cells(p)
    parts = []
    for i, cell in enumerate(cells):
        r, c = divmod(i, columns)
        parts.append(_detent_cell(cell, p).translate((c * pitch_x, -r * pitch_y, 0)))
    return S.union(*parts)


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


def probe_files(tipo: str = "hongo", clearances=None, deltas=None, axiales=None, boquilla: float = 0.4,
                gaps=None, espesores=None, hoja=None, pared=None) -> tuple[dict[str, bytes], str]:
    """Genera las probetas de calibración como archivos en memoria: ({nombre: bytes}, texto de la tabla).

    Es la única implementación: la usan `afm probe` (CLI) y la página (Pyodide), así que dan los mismos archivos."""
    import dataclasses

    p = Params()
    stl = lambda mesh: S.to_trimesh(mesh).export(file_type="stl")
    if tipo == "clic":
        from . import detent as DT
        p = dataclasses.replace(p, nozzle=boquilla, detent=DT.spec_for_nozzle(boquilla))
        thick = tuple(espesores) if espesores else ((1.4, 1.6, 1.8) if boquilla >= 0.7 else DEFAULT_DETENT_THICKNESSES)
        files: dict[str, bytes] = {}
        blocks = []
        for g in (gaps or (0.2, 0.3, 0.4)):
            cells = detent_probe_cells(p, thicknesses=thick, gap=g)
            fn = f"probetas_clic_b{boquilla:g}_gap{g:g}"
            files[fn + ".stl"] = stl(build_detent_probe(p, cells))
            blocks.append(f"== {fn}.stl (boquilla {boquilla:g} mm, holgura de punta {g:g} mm) ==\n" + detent_labels_text(cells))
        text = "\n\n".join(blocks)
        files[f"probetas_clic_b{boquilla:g}.txt"] = (text + "\n").encode()
        return files, text
    if tipo == "montura":
        bw, bt = (float(hoja[0]), float(hoja[1])) if hoja else MOUNT_PROBE_BLADE
        wall = float(pared) if pared else p.min_wall
        cells = mount_probe_cells(clearances or DEFAULT_MOUNT_CLEARANCES)
        plate, strip, lazos = build_mount_probe(p, cells, bw, bt, wall)
        text = mount_labels_text(cells, lazos, bw, bt, wall)
        return {"probeta_montura.stl": stl(plate), "tira_hoja.stl": stl(strip), "probeta_montura.txt": (text + "\n").encode()}, text
    if tipo == "bisagra":
        kw = {}
        if clearances:
            kw["clearances"] = list(clearances)
        if deltas:
            kw["pin_deltas"] = list(deltas)
        if axiales:
            kw["axial_clearances"] = list(axiales)
        cells = hinge_probe_cells(p, **kw)
        mesh, text = build_hinge_probe(p, cells), hinge_labels_text(cells)
    elif tipo == "hongo":
        cells = probe_cells(p, clearances=list(clearances or DEFAULT_CLEARANCES),
                            shaft_deltas=list(deltas or DEFAULT_SHAFT_DELTAS))
        mesh, text = build_probe(p, cells), labels_text(cells)
    else:
        raise ValueError(f"tipo de probeta desconocido: {tipo!r}")
    return {"probetas.stl": stl(mesh), "probetas.txt": (text + "\n").encode()}, text
