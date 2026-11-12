"""Operaciones de catálogo que comparten la consola (`afm catalogo …`) y la ventana «Administrar catálogo».

Cada función calcula y **devuelve líneas de texto** (o un resultado estructurado): no imprime ni toca GTK. La consola las
imprime; la ventana las muestra en el panel «Informe». Así hay una sola implementación de cada comando.

`COMMANDS` es el registro de la correspondencia consola → ventana: un test comprueba que todo subcomando de
`afm catalogo` figure acá (integrado en la ventana o declarado «solo consola»), para que un comando nuevo no quede sin
ventana sin que nadie lo note.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import certify as CT
from .catalog import Catalog, CatalogError

# subcomando -> (dónde está en la ventana, ¿tarea larga?, ¿escribe en el catálogo?)
GUI, CONSOLE = "ventana", "solo consola"
COMMANDS: dict[str, tuple[str, bool, bool]] = {
    "crear": (GUI, False, True),               # pestaña Catálogo de la ventana principal
    "listar": (GUI, False, False),
    "explicar": (GUI, False, False),
    "verificar": (GUI, False, False),
    "usos": (GUI, False, False),               # «Se usa en» de la ficha
    "eliminar": (GUI, False, True),            # va a la papelera
    "restaurar": (GUI, False, True),           # «Recuperar lo eliminado» (el último grupo; `--lote ID` queda en consola)
    "importar-mano": (CONSOLE, False, True),    # fase 4: pide archivo
    "importar-arma": (CONSOLE, False, True),    # fase 4: pide 3 puntos sobre el STL
    "importar-accesorio": (CONSOLE, False, True),
    "agregar-stl": (CONSOLE, False, True),
    "agregar": (GUI, True, True),              # «Catalogar esta figura…» de la ventana principal
    "cortar-muneca": (GUI, False, True),
    "compat": (GUI, True, False),
    "certificar": (GUI, True, False),
    "matriz": (GUI, True, True),
    "cobertura": (GUI, False, False),
    "contrato": (GUI, False, True),            # ver, calibrar (tolerancia 0,4 mm; otra tolerancia, en consola), guardar la propuesta y borrar
    "capacidad": (GUI, True, True),            # «Capacidad» de la pieza y «Medir y guardar capacidad…» del menú (sin rayo de selección de tolerancia)
    "estado": (GUI, False, True),             # «Estado: … Aplicar a las elegidas» (en lote)
}


class Cancelled(Exception):
    """El usuario canceló la tarea larga (no es un `ValueError`: `certify` no la confunde con una falla de armado)."""


# ---------------------------------------------------------------------------------------------------- lectura
def coverage_lines(cat: Catalog, ref: str, all_fits: bool = False) -> list[str]:
    """Cobertura de cada puerto de `ref` contra el resto del catálogo (`afm catalogo cobertura`)."""
    from .guide import contract_of, format_coverage, piece_coverage
    from .interface import label_piece

    contract = contract_of(cat)
    labels = {} if contract is None else label_piece(contract, cat.meta(ref))
    out: list[str] = []
    for name, cov in piece_coverage(cat, ref).items():
        out.append(f"  {name} ({cov.rol}): {cov.summary()}")
        if name in labels:
            out.append(f"    contrato: {labels[name].resumen()}")
        if cov.universo:
            out += format_coverage(cov, "    ", all_fits)[1:]
    return out


def explain_lines(cat: Catalog, ref: str, tipo: str | None = None) -> list[str]:
    """Por qué `ref` se acopla (o no) con cada otra pieza (`afm catalogo explicar`). Levanta `CatalogError` si no existe."""
    from .ports import port_compat

    try:
        me = cat.meta(ref)
    except (KeyError, ValueError) as exc:
        raise CatalogError(str(exc)) from exc

    def rad(p):
        if p.face is None:
            return "—"
        n, holes = len(p.face.polygons), sum(len(q["interiors"]) for q in p.face.polygons)
        extra = (f" ({n} trozos" if n > 1 else "") + ((", " if n > 1 else " (") + f"{holes} agujero(s)" if holes else "") + (")" if n > 1 or holes else "")
        return f"{float(p.face.radius_eq):.2f}".replace(".", ",") + " mm" + extra

    out = [f"{me.ref} ({me.tipo}, altura de figura {me.altura_figura_mm}): " + ", ".join(f"{n} [{p.clase}/{p.rol}] r={rad(p)}" for n, p in me.puertos.items())]
    for other in cat.list(tipo):
        if other.id == me.id:
            continue
        rows = []
        for na, pa in me.puertos.items():
            for nb, pb in other.puertos.items():
                if pa.clase != pb.clase or (nb.endswith("_der") and me.espejable) or (na.endswith("_der") and other.espejable):
                    continue
                c = port_compat(pa, pb, me.altura_figura_mm, other.altura_figura_mm, cat.params)
                extra = "; ".join([*c.motivos, *c.avisos])
                rows.append(f"    {na} ~ {nb} (r={rad(pb)}): {c.nivel.upper()}  IoU {c.iou:.2f}  escalón {c.escalon_mm:.2f} mm"
                            + (f"  capacidad: {c.margenes_mm}" if c.margenes_mm else "") + (f"  → {extra}" if extra else ""))
        if rows:
            out.append(f"{other.ref} ({other.tipo}, altura {other.altura_figura_mm}):")
            out += rows
    return out


@dataclass
class VerifyResult:
    lines: list[str] = field(default_factory=list)
    ok: bool = True


def verify(cat: Catalog) -> VerifyResult:
    """`afm catalogo verificar`: formato, hash, mallas cerradas, puertos, caras y altura de cada pieza."""
    rep = cat.verify()
    lines = [f"AVISO: {w}" for w in rep.warnings] + [f"PROBLEMA: {p}" for p in rep.problems]
    lines.append(f"Catálogo «{cat.name}»: {len(cat.list(all_versions=True))} pieza(s); "
                 + ("limpio" if rep.ok else f"{len(rep.problems)} problema(s)"))
    return VerifyResult(lines, rep.ok)


def contract_view(cat: Catalog) -> tuple[list[str], list[str]]:
    """Contrato de interfaz de la línea guardado (`afm catalogo contrato --ver`): (líneas, problemas de coherencia)."""
    from .interface import ContractError, format_contract, label_piece

    try:
        contract = cat.contract()
    except (ContractError, KeyError, ValueError, TypeError) as exc:
        raise CatalogError(f"contrato ilegible: {exc}") from exc
    if contract is None:
        return ["La línea no tiene contrato de interfaz (usar `afm catalogo contrato --calibrar --guardar`)"], []
    out = list(format_contract(contract))
    probs = contract.problems(cat.params)
    out += [f"PROBLEMA: {p}" for p in probs]
    piezas = []
    for m in cat.list():
        for name, lab in label_piece(contract, m).items():
            piezas.append(f"  {m.ref:<28} {name:<12} {m.puertos[name].rol:<8} {lab.resumen()}")
            piezas += [f"      aviso: {x}" for x in lab.avisos if "no cae en ningún talle" not in x]
    if piezas:
        out += ["Piezas:", *piezas]
    return out, probs


# ---------------------------------------------------------------------------------------------------- certificación
def certifiable(cat: Catalog) -> list[str]:
    """Refs (última versión de cada id) de las piezas que se certifican: brazo, pierna, cabeza, pelvis y mano."""
    return [m.ref for m in cat.list() if m.tipo in CT.PLACE_OF_TIPO]


def cert_badge(cat: Catalog, meta) -> str:
    """Texto corto del semáforo de certificación de una pieza (vacío si no aplica). No arma nada."""
    if meta.tipo not in CT.PLACE_OF_TIPO:
        return ""
    try:
        return CT.status(cat, meta.ref)["texto"]
    except (CatalogError, CT.CertifyError, ValueError) as exc:
        return f"sin estado ({exc})"


@dataclass
class CertifyReport:
    lines: list[str] = field(default_factory=list)
    bad: int = 0                       # piezas que no quedaron «certificada» (o que no se pudieron armar)
    done: int = 0                      # piezas procesadas
    cancelled: bool = False


def certify_pieces(root: str, refs: list[str] | None = None, force: bool = False, status_only: bool = False,
                   progress=None, cancel=None, emit=None) -> CertifyReport:
    """`afm catalogo certificar`: certifica `refs` (por omisión todas las certificables) o solo informa su estado.

    Abre su propio `Catalog` (se puede llamar desde un hilo). `cancel()` → True corta entre piezas y también dentro de un
    armado, en cuanto el motor informa progreso; lo ya certificado queda guardado (cada pieza escribe su resultado al
    terminar y cada receta armada queda en el caché). `emit(línea)` recibe cada línea apenas se produce (la consola la
    imprime; la ventana la agrega al informe), además de quedar en `lines`."""
    cat = Catalog.open(root)
    refs = refs or certifiable(cat)
    rep = CertifyReport()

    def add(line: str) -> None:
        rep.lines.append(line)
        if emit is not None:
            emit(line)

    if not refs:
        add("(no hay piezas certificables: brazo, pierna, cabeza, pelvis o mano)")
        return rep

    def tick(text):
        if cancel is not None and cancel():
            raise Cancelled()
        if progress is not None:
            progress(text)

    for ref in refs:
        if cancel is not None and cancel():
            rep.cancelled = True
            break
        try:
            if not status_only:
                entry = CT.certify(cat, ref, force, tick)
                for h in entry["anfitriones"]:
                    add(f"  {h['torso']}: {'OK' if h['ok'] else 'CON PROBLEMAS'}")
                    for t in h["problemas"]:
                        add(f"    PROBLEMA: {t}")
            st = CT.status(cat, ref)
        except Cancelled:
            rep.cancelled = True
            add(f"{ref}: cancelado (no se guardó su certificación)")
            break
        except (CT.CertifyError, CatalogError, ValueError) as exc:
            add(f"{ref}: {exc}")
            rep.bad += 1
            rep.done += 1
            continue
        add(f"{ref}: {st['texto']}")
        rep.bad += st["estado"] != "certificada" and not status_only
        rep.done += 1
    return rep


# ---------------------------------------------------------------------------------------------------- compatibilidad de recetas
@dataclass
class CompatReport:
    lines: list[str] = field(default_factory=list)
    ok: bool = True
    n: int = 0


def format_compat(results: list[dict]) -> list[str]:
    out = []
    for r in results:
        origen = "caché" if r.get("cache") else f"armada en {r.get('segundos')} s"
        hm = r.get("holgura_min_mm")
        out.append(f"{r.get('personaje')}: {'OK' if r.get('ok') else 'CON PROBLEMAS'} (acople {r.get('nivel') or '—'}, "
                   f"holgura mín. {'—' if hm is None else f'{hm:.2f} mm'}; {origen})")
        out += [f"  PROBLEMA: {t}" for t in r.get("problemas", [])]
        out += [f"  aviso: {t}" for t in r.get("avisos", [])]
    return out


def compat_all(root: str, force: bool = False, progress=None, cancel=None) -> CompatReport:
    """`afm catalogo compat` sin `--receta`: build + validate de las recetas del catálogo (con caché)."""
    from . import compat

    cat = Catalog.open(root)

    def tick(text):
        if cancel is not None and cancel():
            raise Cancelled()
        if progress is not None:
            progress(text)

    results = compat.check_all(cat, force, tick)
    lines = format_compat(results) or ["(no hay recetas: guardalas en <catálogo>/figuras o <catálogo>/recetas)"]
    return CompatReport(lines, all(r.get("ok") for r in results), len(results))


def write_matrix(root: str, out: str | None = None, complete: bool = False, progress=None, cancel=None) -> str:
    """`afm catalogo matriz`: escribe COMPATIBILIDAD.md y devuelve su ruta (`complete` valida antes todas las recetas)."""
    from . import compat

    cat = Catalog.open(root)
    results = None
    if complete:
        def tick(text):
            if cancel is not None and cancel():
                raise Cancelled()
            if progress is not None:
                progress(text)

        results = compat.check_all(cat, False, tick)
    return compat.write_matrix(cat, out, results)


# ---------------------------------------------------------------------------------------------------- capacidad de las tomas
def measurable(cat: Catalog, meta) -> list[str]:
    """Nombres de las tomas de `meta` con pasador de la línea (las que `capacidad` sabe medir)."""
    from .capacity import pin_of

    return [n for n, p in meta.puertos.items() if p.rol == "toma" and pin_of(p.clase, cat.params) is not None]


@dataclass
class CapacityReport:
    lines: list[str] = field(default_factory=list)
    done: int = 0                      # tomas medidas
    skipped: int = 0                   # tomas sin medida (ningún rayo atraviesa el cuerpo)
    wrote: bool = False
    cancelled: bool = False


def measure_capacity(root: str, refs: list[str] | None = None, write: bool = False, cancel=None, emit=None) -> CapacityReport:
    """`afm catalogo capacidad`: mide (y con `write` guarda en los .pieza.json) la capacidad de las tomas con pasador.
    Abre su propio `Catalog` (se puede llamar desde un hilo); `cancel()` → True corta entre piezas (lo ya guardado queda)."""
    from .capacity import demand, head_gap, pin_of, pin_shift

    cat = Catalog.open(root)
    params = cat.params
    metas = [cat.meta(r) for r in refs] if refs else cat.list()
    rep = CapacityReport()

    def add(line: str) -> None:
        rep.lines.append(line)
        if emit is not None:
            emit(line)

    for m in metas:
        if cancel is not None and cancel():
            rep.cancelled = True
            break
        sockets = measurable(cat, m)
        if not sockets:
            continue
        caps = cat.measure_capacity(m.ref, write=write)
        for name in sockets:
            cap, port = caps.get(name), m.puertos[name]
            dem = demand(port.clase, params)
            if cap is None:
                add(f"  {m.ref:<28} {name:<12} sin medida (ningún rayo atraviesa el cuerpo desde la cara)")
                rep.skipped += 1
                continue
            need = dem["profundidad_mm"] + dem["pared_min_mm"]
            line = f"  {m.ref:<28} {name:<12} profundidad {cap['profundidad_mm']:.2f} mm (pide {need:.2f}, margen {cap['profundidad_mm'] - need:+.2f})"
            gap = None
            if cap.get("x_mm") is not None:
                gap = head_gap(pin_of(port.clase, params), cap["x_mm"], cap["dir_x"], pin_shift(port.clase, params))
                line += f" · cabezas a {gap:.2f} mm (mínimo {dem['separacion_min_mm']:.2f})"
            if cap["profundidad_mm"] < need or (gap is not None and gap < dem["separacion_min_mm"]):
                line += "  ← el pasador de la línea no cabe"
            add(line)
            rep.done += 1
    rep.wrote = bool(write and rep.done)
    add(f"{rep.done} toma(s) medida(s)" + (f", {rep.skipped} sin medida" if rep.skipped else "")
        + (" · guardado en los .pieza.json" if rep.wrote else " · no se guardó nada (usar --escribir)" if rep.done else "")
        + (" · cancelado" if rep.cancelled else ""))
    return rep


# ---------------------------------------------------------------------------------------------------- contrato: calibrar / guardar / borrar
@dataclass
class ContractProposal:
    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    contract: object = None            # `interface.Contract` listo para `save_contract` (None si no hubo propuesta)


def propose_contract(cat: Catalog, tolerance: float = 0.4, metric: str = "equivalente") -> ContractProposal:
    """`afm catalogo contrato --calibrar`: propone un contrato a partir de las piezas del catálogo (no guarda nada)."""
    from .interface import format_contract, propose

    prop = propose(cat, tolerance, metric)
    out = ["Propuesta (a confirmar):", *format_contract(prop.contract)]
    for clase, rows in sorted(prop.muestras.items()):
        out.append(f"  muestras de {clase}: " + ", ".join(f"{ref}:{name} r eq {req:.2f} / inscrito {rins:.2f} x {x:.2f}"
                                                         for ref, name, r, x, req, rins in rows))
    out += [f"AVISO: {n}" for n in prop.notas]
    probs = prop.contract.problems(cat.params)
    out += [f"PROBLEMA: {p}" for p in probs]
    return ContractProposal(out, probs, prop.contract)


def save_contract(cat: Catalog, contract) -> str:
    """Guarda `contract` en linea.json (reemplaza el que hubiera) y devuelve su huella."""
    cat.set_contract(contract)
    return contract.hash()


def clear_contract(cat: Catalog) -> bool:
    """Borra el contrato de linea.json; False si la línea no tenía."""
    return bool(cat.clear_contract())
