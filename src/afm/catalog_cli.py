"""Subcomando `afm catalogo …` (plan de piezas intercambiables, §5). La lógica vive en `catalog.py`; acá solo argumentos y texto."""
from __future__ import annotations

import os
import re
import sys

from . import parts as P
from .catalog import Catalog, CatalogError, default_root
from .ports import PortError, port_class, port_from_points


def add_parsers(sub) -> None:
    c = sub.add_parser("catalogo", help="catálogo de piezas de una línea (crear, listar, verificar, importar-mano, agregar, agregar-stl, usos, estado)")
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
    a = cs.add_parser("verificar", help="formato, hash, mallas cerradas, puertos, caras y altura de cada pieza")
    root(a)
    a = cs.add_parser("usos", help="recetas (y .proyecto) que usan una pieza, para saber qué figuras regenerar al cambiarla")
    root(a)
    a.add_argument("ref", metavar="ID")
    a.add_argument("--buscar", action="append", default=[], metavar="DIR", help="carpeta con .proyecto donde buscar también (repetible)")
    a = cs.add_parser("importar-mano", help="importar una mano estándar afm-mano-1 (la de `afm extraer-mano`) como pieza")
    root(a)
    a.add_argument("stl")
    a.add_argument("--id", default=None)
    a.add_argument("--nueva-version", action="store_true", help="si el id ya existe, guardar la versión siguiente")
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


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "pieza"


def run_catalogo(args) -> int:
    try:
        return _run(args)
    except (CatalogError, P.PieceError, PortError) as exc:
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
    if cmd == "listar":
        metas = cat.list(args.tipo, args.etiqueta, args.estado, args.todas_las_versiones)
        if args.compatible_con:
            keep = {}
            for other, na, nb, c in cat.compatible_with(args.compatible_con):
                keep.setdefault(other.id, []).append((na, nb, c))
            metas = [m for m in metas if m.id in keep]
        if not metas:
            print("(sin piezas)")
        for m in metas:
            print(f"{m.ref:<28} {m.tipo:<10} {m.estado:<9} {','.join(m.etiquetas) or '-':<8} puertos: {', '.join(m.puertos)}")
            if args.compatible_con:
                for na, nb, c in keep[m.id]:
                    det = f"IoU {c.iou:.2f}, escalón {c.escalon_mm:.2f} mm" if c.iou is not None else "sin cara"
                    print(f"    {args.compatible_con}:{na} ↔ {nb}: {c.nivel} ({det})" + "".join(f"\n      aviso: {x}" for x in c.avisos))
        return 0
    if cmd == "verificar":
        rep = cat.verify()
        for w in rep.warnings:
            print("AVISO:", w)
        for p in rep.problems:
            print("PROBLEMA:", p)
        print(f"Catálogo «{cat.name}»: {len(cat.list(all_versions=True))} pieza(s); "
              + ("limpio" if rep.ok else f"{len(rep.problems)} problema(s)"))
        return 0 if rep.ok else 1
    if cmd == "usos":
        uses = cat.usages(args.ref, args.buscar)
        if not uses:
            print(f"«{args.ref}» no la usa ninguna figura")
        for u in uses:
            print(f"{u['archivo']}: lugar «{u['lugar']}» → {u['pieza']}")
        return 0
    if cmd == "importar-mano":
        meta, warns = cat.import_hand(args.stl, args.id, args.nueva_version)
        print(f"Mano guardada como {meta.ref} (muñeca Ø{2 * meta.puertos['muneca'].face.radius_eq:.2f} mm, {meta.volumen_mm3:.0f} mm³)")
        for w in warns:
            print("AVISO:", w)
        return 0
    if cmd == "agregar-stl":
        mesh = P.load_mesh(args.stl)
        if not mesh.is_watertight:
            raise P.PieceError(f"«{args.stl}» no es estanco: repararlo antes (la GUI o `afm build` lo reparan al cargar)")
        spec = P.PORT_SPEC[args.tipo]
        ports = {}
        for item in args.puerto:
            name, nums = item[0], [float(x) for x in item[1:]]
            if name not in spec:
                raise P.PieceError(f"puerto «{name}» no previsto en una pieza «{args.tipo}» (válidos: {', '.join(spec) or 'ninguno'})")
            ports[name] = port_from_points(mesh, [nums[0:3], nums[3:6], nums[6:9]], port_class(name), spec[name][0])
        meta = P.PieceMeta(id=args.id or f"{args.tipo}-{_slug(os.path.splitext(os.path.basename(args.stl))[0])}-01", version=1,
                           tipo=args.tipo, puertos=ports, descripcion=args.descripcion,
                           origen=P.make_origin(args.figura or os.path.basename(args.stl)),
                           altura_figura_mm=args.altura if args.altura is not None else cat.height_mm,
                           lado=args.lado, espejable=not args.no_espejable, etiquetas=list(args.etiqueta))
        meta, warns = cat.add_piece(mesh, meta, args.nueva_version)
        print(f"Pieza guardada como {meta.ref} ({meta.volumen_mm3:.0f} mm³; puertos: {', '.join(meta.puertos)})")
        for w in warns:
            print("AVISO:", w)
        return 0
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
        return 0
    if cmd == "estado":
        m = cat.set_state(args.ref, args.nuevo)
        print(f"{m.ref}: {m.estado}")
        return 0
    return 2
