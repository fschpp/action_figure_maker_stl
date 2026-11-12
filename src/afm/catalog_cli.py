"""Subcomando `afm catalogo …` (plan de piezas intercambiables, §5). La lógica vive en `catalog.py`; acá solo argumentos y texto."""
from __future__ import annotations

import os
import re
import sys

from . import catalog_ops as OPS
from . import parts as P
from .catalog import Catalog, CatalogError, default_root
from .certify import PLACE_OF_TIPO
from .interface import ContractError
from .ports import PortError, port_class, port_from_points
from .recipe import RecipeError


def add_parsers(sub) -> None:
    c = sub.add_parser("catalogo", help="catálogo de piezas de una línea (crear, listar, verificar, importar-mano, agregar, agregar-stl, cortar-muneca, usos, eliminar, estado, compat, certificar, matriz, cobertura, contrato, capacidad)")
    cs = c.add_subparsers(dest="catcmd", required=True)

    def root(p):
        p.add_argument("ruta", nargs="?", default=None,
                       help="directorio del catálogo de la línea (por defecto $AFM_CATALOGO o ~/.local/share/afm/catalogos/<línea>)")

    a = cs.add_parser("crear", help="crear el catálogo de una línea (linea.json con el perfil de parámetros)")
    root(a)
    a.add_argument("--linea", required=True, help="nombre de la línea (p. ej. proceres-130)")
    a.add_argument("--altura", type=float, required=True, help="altura de figura de la línea (mm, pies → coronilla)")
    a.add_argument("--boquilla", type=float, default=0.4)
    a.add_argument("--perfil", default=None, help="nombre de un perfil guardado (~/.config/afm/perfiles) como parámetros de la línea")
    a = cs.add_parser("listar", help="piezas del catálogo (la última versión de cada id)")
    root(a)
    a.add_argument("--tipo", default=None, choices=P.TYPES)
    a.add_argument("--etiqueta", default=None, choices=P.TAGS)
    a.add_argument("--estado", default=None, choices=P.STATES)
    a.add_argument("--todas-las-versiones", action="store_true")
    a.add_argument("--compatible-con", default=None, metavar="REF", help="solo piezas que se acoplan con REF (compatibilidad barata, por metadatos)")
    a = cs.add_parser("explicar", help="por qué una pieza se acopla (o no) con cada otra: puertos, radios y, por cada par, nivel, IoU, escalón y motivos")
    root(a)
    a.add_argument("ref", metavar="REF", help="pieza a explicar (id@versión)")
    a.add_argument("--con", default=None, metavar="TIPO", help="solo contra piezas de este tipo (p. ej. torso_sup)")
    a = cs.add_parser("verificar", help="formato, hash, mallas cerradas, puertos, caras y altura de cada pieza")
    root(a)
    a = cs.add_parser("usos", help="recetas (y .proyecto) que usan una pieza, para saber qué figuras regenerar al cambiarla")
    root(a)
    a.add_argument("ref", metavar="ID")
    a.add_argument("--buscar", action="append", default=[], metavar="DIR", help="carpeta con .proyecto donde buscar también (repetible)")
    a = cs.add_parser("eliminar", help="eliminar del catálogo una o más versiones de pieza (<id>@<n>); van a la papelera y `restaurar` las recupera")
    root(a)
    a.add_argument("refs", nargs="+", metavar="ID@N")
    a.add_argument("--buscar", action="append", default=[], metavar="DIR",
                   help="carpeta con .proyecto donde buscar también quién la usa (repetible; no recursiva)")
    a.add_argument("--si", action="store_true", help="no pedir confirmación")
    a = cs.add_parser("restaurar", help="recuperar de la papelera lo último que se eliminó (o el lote --lote)")
    root(a)
    a.add_argument("--lote", default=None, metavar="ID", help="lote de la papelera (por defecto el último)")
    a = cs.add_parser("importar-mano", help="importar una mano estándar afm-mano-1 (la de `afm extraer-mano`) como pieza")
    root(a)
    a.add_argument("stl")
    a.add_argument("--id", default=None)
    a.add_argument("--nueva-version", action="store_true", help="si el id ya existe, guardar la versión siguiente")
    a = cs.add_parser("importar-arma", help="importar un STL cualquiera como arma de la línea: lo escala, le corta el mango por un plano y le pone la empuñadura dentada que encaja en la mano con agarre del catálogo")
    root(a)
    a.add_argument("stl", help="STL del arma, en cualquier orientación y unidad")
    a.add_argument("--corte", metavar="X,Y,Z;X,Y,Z;X,Y,Z", required=True,
                   help="3 puntos del plano de corte, en coordenadas del STL original (sin escalar), separados por «;» y con el signo igual "
                        "(--corte=-3,0,1;...) para que un primer número negativo no se tome por opción; el lado hacia el que apunta la normal "
                        "(1→2 × 1→3) se descarta: es el mango")
    g = a.add_mutually_exclusive_group()
    g.add_argument("--escala", type=float, help="factor sobre las unidades del STL")
    g.add_argument("--largo", type=float, help="largo (mm) de la dimensión mayor del arma; por defecto 0,40 × la altura de la línea")
    a.add_argument("--invertir", action="store_true", help="descartar el otro lado del plano")
    a.add_argument("--giro", type=float, default=None, help="giro (°) sobre el eje de la empuñadura; por defecto el que deja el arma más baja")
    a.add_argument("--mano", default=None, help="mano con agarre para la que se arma (por defecto, la más reciente con agarre)")
    a.add_argument("--id", default=None)
    a.add_argument("--descripcion", default="")
    a.add_argument("--nueva-version", action="store_true")
    a = cs.add_parser("importar-accesorio", help="importar un accesorio (arma, herramienta…) con empuñadura dentada como pieza «accesorio»: STL en el marco del accesorio + los datos de su agarre")
    root(a)
    a.add_argument("stl", help="STL cerrado: eje de la empuñadura = +X, origen en la cara de la guarda que mira a la mano (ver props.py)")
    a.add_argument("--id", default=None)
    a.add_argument("--largo-canal", type=float, required=True, help="largo del canal de la mano para el que se diseñó la empuñadura (mm; está en el .mano.json de la mano con agarre)")
    a.add_argument("--taladro", type=float, default=2.4, help="Ø del taladro de la mano (mm)")
    a.add_argument("--vastago", type=float, default=2.0)
    a.add_argument("--ranura", type=float, default=0.5)
    a.add_argument("--largo-lengueta", type=float, default=8.0)
    a.add_argument("--dentado", type=float, default=2.9)
    a.add_argument("--juego", type=float, default=0.4, help="juego axial entre el hombro del dentado y la cara de salida (mm)")
    a.add_argument("--descripcion", default="")
    a.add_argument("--nueva-version", action="store_true")
    a = cs.add_parser("agregar-stl", help="agregar una pieza suelta: STL cerrado + 3 puntos por puerto sobre su cara de unión")
    root(a)
    a.add_argument("stl")
    a.add_argument("--tipo", required=True, choices=P.TYPES)
    a.add_argument("--puerto", nargs=10, action="append", required=True, metavar=("NOMBRE", "x1", "y1", "z1", "x2", "y2", "z2", "x3", "y3", "z3"),
                   help="NOMBRE x1 y1 z1 x2 y2 z2 x3 y3 z3 (repetible). El punto 1 fija el giro; el rol sale del tipo de pieza")
    a.add_argument("--id", default=None)
    a.add_argument("--altura", type=float, default=None, help="altura de la figura de origen (mm); por defecto la de la línea")
    a.add_argument("--lado", default="izq", choices=P.SIDES)
    a.add_argument("--no-espejable", action="store_true")
    a.add_argument("--descripcion", default="")
    a.add_argument("--figura", default="", help="figura de origen (texto, para el índice)")
    a.add_argument("--etiqueta", action="append", default=[], choices=P.TAGS)
    a.add_argument("--nueva-version", action="store_true")
    a = cs.add_parser("agregar", help="extraer las piezas crudas de una figura ya configurada (STL + .proyecto) y guardarlas con sus puertos")
    root(a)
    a.add_argument("stl")
    a.add_argument("proyecto")
    a.add_argument("--lugares", default="todos", help="todos o una lista separada por comas de: cabeza, torso, brazo, pierna, mano")
    a.add_argument("--prefijo", default=None, help="nombre del personaje para los ids (p. ej. guemes → pierna-guemes-01); por defecto el del STL")
    a.add_argument("--muneca", type=float, nargs=9, default=None, metavar="N",
                   help="x1 y1 z1 x2 y2 z2 x3 y3 z3 de la muñeca izquierda (marco de trabajo): el brazo sale sin mano y la mano como pieza aparte")
    a.add_argument("--muneca-invertir", action="store_true", help="la mano es el lado grande del corte de muñeca")
    a.add_argument("--etiqueta", action="append", default=[], choices=P.TAGS, help="estandar / propia (se aplica a todas las piezas)")
    a.add_argument("--nueva-version", action="store_true", help="si un id ya existe, guardar la versión siguiente")
    a = cs.add_parser("cortar-muneca", help="cortarle la mano a un brazo que ya está en el catálogo: guarda una versión nueva del brazo sin mano (puerto «muneca») y la mano como pieza aparte")
    root(a)
    a.add_argument("ref", metavar="BRAZO", help="id o id@n del brazo con la mano pegada (el original no se toca)")
    a.add_argument("--muneca", type=float, nargs=9, required=True, metavar="N",
                   help="x1 y1 z1 x2 y2 z2 x3 y3 z3 de la muñeca, en el marco de la pieza (el mismo de `agregar --muneca`); el punto 1 fija el giro: "
                        "marcalo siempre en el mismo lugar (p. ej. el lado del pulgar). Con signo igual (--muneca=-3 …) si el primer número es negativo")
    a.add_argument("--muneca-invertir", action="store_true", help="la mano es el lado grande del corte")
    a.add_argument("--mano-id", default=None, help="id de la mano (por defecto mano-<resto del id del brazo>)")
    a.add_argument("--mano-nueva-version", action="store_true", help="si el id de la mano ya existe, guardar la versión siguiente")
    a = cs.add_parser("compat", help="compatibilidad completa (build + validate) de las recetas del catálogo; el resultado queda en compat.json")
    root(a)
    a.add_argument("--receta", action="append", default=[], metavar="ARCHIVO",
                   help="receta afm-receta-1 a validar (repetible); sin esta opción, las de <catálogo>/figuras y <catálogo>/recetas")
    a.add_argument("--forzar", action="store_true", help="volver a armar aunque compat.json ya tenga el resultado de esta combinación")
    a = cs.add_parser("certificar", help="certifica piezas (brazo, pierna, cabeza, pelvis, mano): las arma de verdad en un panel de torsos del catálogo (propio + menor/mediana/mayor capacidad)")
    root(a)
    a.add_argument("refs", nargs="*", metavar="ID", help="piezas a certificar (por defecto, todas las certificables)")
    a.add_argument("--estado", action="store_true", help="solo mostrar el estado de la certificación guardada, sin armar nada")
    a.add_argument("--forzar", action="store_true", help="volver a armar aunque compat.json ya tenga el resultado")
    a = cs.add_parser("matriz", help="informe COMPATIBILIDAD.md: matriz barata del catálogo y resultados completos guardados")
    root(a)
    a.add_argument("--out", default=None, metavar="ARCHIVO", help="dónde escribirlo (por defecto <catálogo>/COMPATIBILIDAD.md)")
    a.add_argument("--completa", action="store_true", help="antes de escribirlo, validar (con caché) todas las recetas del catálogo")
    a = cs.add_parser("cobertura", help="cuántas piezas del catálogo aceptan cada puerto de una pieza (compatibilidad barata, por metadatos)")
    root(a)
    a.add_argument("ref", metavar="ID")
    a.add_argument("--todas", action="store_true", help="listar también las piezas con resultado ok")
    a = cs.add_parser("contrato", help="contrato de interfaz de la línea (talles de cara y posición por clase de puerto): ver, calibrar desde el catálogo o validar")
    root(a)
    g = a.add_mutually_exclusive_group()
    g.add_argument("--ver", action="store_true", help="mostrar el contrato guardado y el talle de cada pieza (por defecto)")
    g.add_argument("--calibrar", action="store_true", help="proponer el contrato agrupando los radios de los enchufes del catálogo")
    g.add_argument("--validar", action="store_true", help="comprobar la coherencia del contrato y etiquetar todas las piezas")
    g.add_argument("--quitar", action="store_true", help="borrar el contrato de linea.json")
    a.add_argument("--guardar", action="store_true", help="con --calibrar: escribir la propuesta en linea.json (sin esto solo se muestra)")
    a.add_argument("--tolerancia", type=float, default=None, metavar="MM", help="con --calibrar: tolerancia del radio de un talle (por defecto 0,4 mm)")
    a.add_argument("--metrica", choices=("equivalente", "inscrito"), default="equivalente",
                   help="con --calibrar: con qué radio de la cara se agrupan los talles (equivalente = el de la misma área, por defecto; inscrito = el mayor círculo que entra)")
    a = cs.add_parser("capacidad", help="capacidad del anfitrión (profundidad detrás de la cara y cabezas enfrentadas) de las tomas con pasador: medirla sobre el STL de piezas ya catalogadas")
    root(a)
    a.add_argument("refs", nargs="*", metavar="ID", help="piezas (id o id@n); por defecto las tomas de todo el catálogo")
    a.add_argument("--escribir", action="store_true", help="guardar lo medido en el .pieza.json (no toca el STL, el hash ni la versión); sin esto solo se muestra")
    a = cs.add_parser("estado", help="cambiar el estado de una pieza (borrador, validada, impresa)")
    root(a)
    a.add_argument("ref", metavar="ID")
    a.add_argument("nuevo", choices=P.STATES)


def _open(args) -> Catalog:
    if args.ruta:
        return Catalog.open(args.ruta)
    env = os.environ.get("AFM_CATALOGO")
    if not env:
        raise CatalogError("falta la ruta del catálogo (o definí AFM_CATALOGO)")
    return Catalog.open(env)


def _print_coverage(cat: Catalog, ref: str, all_fits: bool = False) -> None:
    """Cobertura de cada puerto de `ref` contra el resto del catálogo (plan de compatibilidad, N0)."""
    for line in OPS.coverage_lines(cat, ref, all_fits):
        print(line)


def _print_import(res) -> int:
    """Imprime lo de una importación como siempre: la primera línea, los avisos y el resto (en el arma y el accesorio, los avisos van
    antes de «se sujeta / sirve en …»)."""
    head, rest = res.lines[:1], res.lines[1:]
    if res.meta is not None and "arma" in (res.meta.mecanismo or {}):    # el arma imprime sus medidas antes de los avisos
        head, rest = res.lines[:2], res.lines[2:]
    for ln in head:
        print(ln)
    for w in res.warnings:
        print("AVISO:", w)
    for ln in rest:
        print(ln)
    return 0


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "pieza"


def _run_capacity(cat, args) -> int:
    """`afm catalogo capacidad RUTA [ID …] [--escribir]`: ver `catalog_ops.measure_capacity`."""
    OPS.measure_capacity(cat.root, args.refs or None, args.escribir, emit=print)
    return 0


def _run_contract(cat, args) -> int:
    if args.calibrar:
        tol = args.tolerancia if args.tolerancia is not None else 0.4
        prop = OPS.propose_contract(cat, tol, args.metrica)
        for line in prop.lines:
            print(line)
        if not args.guardar:
            print("(no se guardó nada; repetir con --guardar para escribirla en linea.json)")
            return 1 if prop.problems else 0
        if prop.problems:
            print("No se guarda: la propuesta tiene problemas.", file=sys.stderr)
            return 1
        digest = OPS.save_contract(cat, prop.contract)
        print(f"Contrato guardado en {cat.root}/linea.json (huella {digest})")
        return 0
    if args.quitar:
        print("Contrato borrado de linea.json" if OPS.clear_contract(cat) else "La línea no tenía contrato")
        return 0
    try:
        contract = cat.contract()
    except (ContractError, KeyError, ValueError, TypeError) as exc:
        print(f"ERROR: contrato ilegible: {exc}", file=sys.stderr)
        return 2
    if contract is None:
        print("La línea no tiene contrato de interfaz (usar `afm catalogo contrato --calibrar --guardar`)")
        return 1 if args.validar else 0
    lines, probs = OPS.contract_view(cat)
    for line in lines:
        print(line)
    if args.validar:
        print("Contrato " + ("coherente" if not probs else f"con {len(probs)} problema(s)"))
    return 1 if probs else 0


def _explicar(cat, ref: str, tipo: str | None = None) -> int:
    """`afm catalogo explicar REF`: ver `catalog_ops.explain_lines`."""
    try:
        lines = OPS.explain_lines(cat, ref, tipo)
    except CatalogError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 0


def run_catalogo(args) -> int:
    try:
        return _run(args)
    except (CatalogError, P.PieceError, PortError, RecipeError, ContractError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _run(args) -> int:
    cmd = args.catcmd
    if cmd == "crear":
        from .profile import default_for_nozzle, load_profile
        params = load_profile(args.perfil) if args.perfil else default_for_nozzle(args.boquilla)
        path = args.ruta or default_root(args.linea)
        cat = Catalog.create(path, args.linea, args.altura, args.boquilla, params)
        print(f"Catálogo «{cat.name}» creado en {cat.root} (altura {cat.height_mm:g} mm, holgura {cat.params.clearance:g} mm)")
        return 0
    cat = _open(args)
    if cmd == "explicar":
        return _explicar(cat, args.ref, args.con)
    if cmd == "listar":
        metas = cat.list(args.tipo, args.etiqueta, args.estado, args.todas_las_versiones)
        if args.compatible_con:
            keep = {}
            for other, na, nb, c in cat.compatible_with(args.compatible_con):
                keep.setdefault(other.id, []).append((na, nb, c))
            for other, c in cat.compatible_grips(args.compatible_con):
                keep.setdefault(other.id, []).append(("agarre", "agarre", c))
            metas = [m for m in metas if m.id in keep]
        if not metas:
            print("(sin piezas)")
        from .guide import contract_of
        from .interface import label_piece

        contract = contract_of(cat)
        for m in metas:
            print(f"{m.ref:<28} {m.tipo:<10} {m.estado:<9} {','.join(m.etiquetas) or '-':<8} puertos: {', '.join(m.puertos)}")
            if contract is not None:                       # talle de cada puerto de clase dura (o «fuera» si no cae en ninguno)
                labs = label_piece(contract, m)
                if labs:
                    print("    talle: " + ", ".join(f"{n} {lab.talle or 'fuera'}" + ("" if lab.x_ok is not False and lab.eje_ok is not False else "*")
                                                  for n, lab in labs.items()) + ("   (* fuera de la posición o del eje del contrato)" if any(
                                                      l.x_ok is False or l.eje_ok is False for l in labs.values()) else ""))
            if m.tipo in PLACE_OF_TIPO:             # brazo, pierna, cabeza, pelvis y mano (antes faltaban las dos últimas)
                from .certify import status
                print("    " + status(cat, m.ref)["texto"])
            if args.compatible_con:
                for na, nb, c in keep[m.id]:
                    det = (f"IoU {c.iou:.2f}, escalón {c.escalon_mm:.2f} mm" if c.iou is not None else
                           f"holgura {c.margenes_mm['holgura_radial']:.2f} mm/lado, interferencia {c.margenes_mm['interferencia']:.2f} mm/lado"
                           if na == "agarre" else "sin cara")
                    print(f"    {args.compatible_con}:{na} ↔ {nb}: {c.nivel} ({det})" + "".join(f"\n      aviso: {x}" for x in c.avisos))
        return 0
    if cmd == "verificar":
        res = OPS.verify(cat)
        for line in res.lines:
            print(line)
        return 0 if res.ok else 1
    if cmd == "contrato":
        return _run_contract(cat, args)
    if cmd == "capacidad":
        return _run_capacity(cat, args)
    if cmd == "usos":
        uses = cat.usages(args.ref, args.buscar)
        if not uses:
            print(f"«{args.ref}» no la usa ninguna figura")
        for u in uses:
            print(f"{u['archivo']}: lugar «{u['lugar']}» → {u['pieza']}")
        return 0
    if cmd == "eliminar":
        metas = [cat.meta(r) for r in args.refs]            # falla antes de borrar nada si alguna no existe
        for r in args.refs:
            if "@" not in r:
                raise CatalogError(f"«{r}»: para eliminar hay que indicar la versión (<id>@<n>)")
        for m in metas:
            uses = cat.usages(m.ref, args.buscar, recursive=False)
            print(f"{m.ref:<28} {m.tipo:<10} origen: {m.origen.get('figura') or m.origen.get('nota') or '—'}")
            for u in uses:
                print(f"    la usa: {u['archivo']} (lugar «{u['lugar']}»)")
        if not args.si:
            if not sys.stdin.isatty():
                raise CatalogError("falta confirmar: agregá --si para eliminar sin preguntar")
            if input(f"¿Eliminar {len(metas)} pieza(s) del catálogo «{cat.name}»? Quedan en la papelera del catálogo y se recuperan con `restaurar` [s/N] ").strip().lower() not in ("s", "si", "sí", "y", "yes"):
                print("No se eliminó nada.")
                return 1
        res = cat.delete_pieces(args.refs)
        for ref in res.refs:
            print(f"Eliminada {ref}")
        for e in res.errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 0 if not res.errors else 2
    if cmd == "restaurar":
        restored, errors = cat.restore_deleted(args.lote)
        for m in restored:
            print(f"Recuperada {m.ref}")
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 0 if not errors else 2
    if cmd == "importar-arma":
        from . import weapon as WP
        try:
            res = OPS.import_weapon(cat, args.stl, OPS.parse_cut_points(args.corte), scale=args.escala, length=args.largo, flip=args.invertir,
                                    roll=args.giro, hand=args.mano, piece_id=args.id, description=args.descripcion,
                                    new_version=args.nueva_version)
        except (WP.WeaponError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        return _print_import(res)
    if cmd == "importar-accesorio":
        res = OPS.import_accessory(cat, args.stl, args.largo_canal, bore_d=args.taladro, shaft_d=args.vastago, slit_w=args.ranura,
                                   finger_len=args.largo_lengueta, barb_d=args.dentado, play=args.juego, piece_id=args.id,
                                   description=args.descripcion, new_version=args.nueva_version)
        return _print_import(res)
    if cmd == "importar-mano":
        return _print_import(OPS.import_hand(cat, args.stl, args.id, args.nueva_version))
    if cmd == "agregar-stl":
        ports = {item[0]: (item[1:4], item[4:7], item[7:10]) for item in args.puerto}
        res = OPS.add_loose_piece(cat, args.stl, args.tipo, ports, piece_id=args.id, height=args.altura, side=args.lado,
                                  mirrorable=not args.no_espejable, description=args.descripcion, figure=args.figura,
                                  tags=args.etiqueta, new_version=args.nueva_version)
        return _print_import(res)
    if cmd == "agregar":
        from .extract import ExtractError, extract_all, load_body
        from .project import Project

        project = Project.load(args.proyecto)
        places = None if args.lugares == "todos" else [x.strip() for x in args.lugares.split(",") if x.strip()]
        prefix = _slug(args.prefijo or os.path.splitext(os.path.basename(args.stl))[0])
        body, _rep = load_body(args.stl, project, progress=print)
        try:
            res = extract_all(body, project, prefix, places, args.muneca, args.muneca_invertir,
                              figure=os.path.basename(args.stl), project_name=os.path.basename(args.proyecto))
        except ExtractError as exc:
            raise CatalogError(str(exc)) from exc
        if abs(project.params.clearance - cat.params.clearance) > 1e-9:
            print(f"AVISO: la holgura del proyecto ({project.params.clearance:g} mm) difiere de la de la línea ({cat.params.clearance:g} mm): "
                  "al armar manda la de la línea")
        for w in res.warnings:
            print("AVISO:", w)
        if not res.pieces:
            raise CatalogError("no se extrajo ninguna pieza")
        for e in res.pieces:
            e.meta.etiquetas = list(args.etiqueta)
            meta, warns = cat.add_piece(e.mesh, e.meta, args.nueva_version)
            print(f"{meta.ref:<28} {meta.tipo:<10} {meta.volumen_mm3:>9.0f} mm³  puertos: {', '.join(meta.puertos)}")
            for w in warns:
                print("AVISO:", w)
            _print_coverage(cat, meta.ref)
        return 0
    if cmd == "cortar-muneca":
        res = cat.cut_wrist(args.ref, args.muneca, args.muneca_invertir, args.mano_id, args.mano_nueva_version)
        print(f"Brazo sin mano: {res.arm.ref} (desde {res.from_ref}; {res.arm.volumen_mm3:.0f} mm³; puertos: {', '.join(res.arm.puertos)})")
        print(f"Mano: {res.hand.ref} (muñeca Ø{2 * res.wrist_radius_mm:.2f} mm, {res.hand.volumen_mm3:.0f} mm³)")
        for w in res.warnings:
            print("AVISO:", w)
        print(f"Las recetas que usan {res.from_ref} siguen armando igual; para usar la mano separada, apuntalas a {res.arm.ref} y poné {res.hand.id} en el lugar «mano».")
        return 0
    if cmd == "compat":
        from . import compat
        from .recipe import Recipe

        if args.receta:
            results = []
            for path in args.receta:
                res = compat.check(Recipe.load(path), cat.root, args.forzar, print)
                res["receta"] = path
                results.append(res)
        else:
            results = compat.check_all(cat, args.forzar, print)
        if not results:
            print("(no hay recetas: pasá --receta ARCHIVO o guardalas en <catálogo>/figuras)")
        for line in OPS.format_compat(results):
            print(line)
        return 0 if all(r.get("ok") for r in results) else 1
    if cmd == "certificar":
        return _run_certify(args, cat)
    if cmd == "matriz":
        from . import compat

        results = compat.check_all(cat, False, print) if args.completa else None
        print(f"Escrito {compat.write_matrix(cat, args.out, results)}")
        return 0
    if cmd == "cobertura":
        _print_coverage(cat, args.ref, args.todas)
        return 0
    if cmd == "estado":
        m = cat.set_state(args.ref, args.nuevo)
        print(f"{m.ref}: {m.estado}")
        return 0
    return 2


# ------------------------------------------------------------------ afm guia
def add_guia_parser(sub) -> None:
    g = sub.add_parser("guia", help="medir los cortes de una figura (hombro, cadera, cuello) y cuánto del catálogo los acepta, sin generar nada")
    g.add_argument("stl")
    g.add_argument("proyecto")
    g.add_argument("--catalogo", default=None, metavar="RUTA",
                   help="catálogo de la línea (por defecto $AFM_CATALOGO; sin catálogo solo se miden los cortes)")
    g.add_argument("--excluir-figura", default=None, metavar="NOMBRE",
                   help="ignorar las piezas del catálogo que vienen de esta figura (por defecto el nombre del STL: una figura siempre encaja con sus piezas)")
    g.add_argument("--todas", action="store_true", help="listar también las piezas con resultado ok")
    g.add_argument("--ajustar", action="store_true",
                   help="probar desplazar el plano de corte de cada miembro «outward» y decir cuál da más cobertura del catálogo (pide --catalogo)")
    g.add_argument("--estandar", action="store_true",
                   help="ajustar el radio del recorte de cada miembro al talle más cercano del contrato de la línea (con --guardar, lo deja en el proyecto)")
    g.add_argument("--rango", type=float, default=2.0, metavar="MM", help="con --ajustar: hasta cuántos mm a cada lado (2)")
    g.add_argument("--paso", type=float, default=0.25, metavar="MM", help="con --ajustar: de a cuántos mm (0,25)")
    g.add_argument("--guardar", default=None, metavar="SALIDA.proyecto",
                   help="con --ajustar: guardar el proyecto con los mejores desplazamientos (si alguno mejora)")


def run_guia(args) -> int:
    """`afm guia STL PROYECTO [--catalogo RUTA]`: mide cada corte de la figura y su cobertura del catálogo."""
    from .extract import load_body
    from .guide import format_measure, measure_cuts
    from .project import Project

    try:
        root = args.catalogo or os.environ.get("AFM_CATALOGO")
        cat = Catalog.open(root) if root else None
        project = Project.load(args.proyecto)
        body, _rep = load_body(args.stl, project, progress=print)
        fig = args.excluir_figura if args.excluir_figura is not None else os.path.basename(args.stl)
        cuts = measure_cuts(project, body, cat, exclude_figure=fig)
    except (CatalogError, P.PieceError, PortError, RecipeError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if cat is None:
        print("(sin catálogo: solo se miden los cortes; pasá --catalogo RUTA o definí AFM_CATALOGO para ver la cobertura)")
    if not cuts:
        print("El proyecto no define miembros ni cuello: no hay cortes que medir.")
        return 0
    for m in cuts:
        for line in format_measure(m, args.todas):
            print(line)
    if args.guardar and not (args.ajustar or args.estandar):
        print("ERROR: --guardar va con --ajustar o --estandar", file=sys.stderr)
        return 2
    if args.estandar:
        return _run_estandar(args, project, body, cat)
    if args.ajustar:
        return _run_ajuste(args, project, body, cat, fig)
    return 0


def _run_estandar(args, project, body, cat) -> int:
    """`afm guia … --estandar [--guardar SALIDA]`: lleva la cara de cada miembro a un talle del contrato (`guide.fit_cut`)."""
    from .guide import apply_fit, contract_of, fit_cut, format_fit_cut

    contract = contract_of(cat) if cat is not None else None
    if contract is None:
        print("ERROR: --estandar necesita un catálogo con contrato de interfaz (`afm catalogo contrato … --calibrar --guardar`)", file=sys.stderr)
        return 2
    results = fit_cut(project, body, contract)
    print()
    for r in results:
        for line in format_fit_cut(r):
            print(line)
    if args.guardar:
        if apply_fit(project, results):
            project.save(args.guardar)
            print(f"\nProyecto ajustado guardado en {args.guardar}")
        else:
            print("\nNada que guardar: ningún corte cambió.")
    return 0


def _run_certify(args, cat) -> int:
    """`afm catalogo certificar RUTA [ID…] [--estado] [--forzar]`: certificación de piezas (plan de compatibilidad, Fase 6)."""
    rep = OPS.certify_pieces(cat.root, args.refs or None, args.forzar, args.estado, progress=print, emit=print)
    return 1 if rep.bad else 0


def _run_ajuste(args, project, body, cat, fig) -> int:
    """`afm guia … --ajustar [--guardar SALIDA]`: barrido del desplazamiento del corte de cada miembro (plan de compatibilidad, Fase 4)."""
    from .guide import apply_cut_offset, format_scan, scan_cut_offset

    if cat is None:
        print("ERROR: --ajustar necesita un catálogo (--catalogo RUTA o $AFM_CATALOGO)", file=sys.stderr)
        return 2
    changed = 0
    for lugar in ("brazo", "pierna"):
        scan = scan_cut_offset(project, body, cat, lugar, args.rango, args.paso, exclude_figure=fig)
        print()
        for line in format_scan(scan):
            print(line)
        changed += apply_cut_offset(project, scan) if args.guardar else 0
    if args.guardar:
        if changed:
            project.save(args.guardar)
            print(f"\nProyecto con el ajuste guardado en {args.guardar}")
        else:
            print("\nNada que guardar: ningún desplazamiento mejora el corte actual.")
    return 0
