"""`afm catalogo remoto …` (plan de catálogos en línea, §5.6 y D14): buscar, ver, versiones, instalar y actualizar desde un índice.

Solo lee argumentos e imprime; la lógica está en `remoto.py` (red, búsqueda, resolución) y `instalar.py` (plan y aplicación atómica). Instalar y
actualizar pasan por el **mismo** `paquete_cli._install_plan` que `afm catalogo instalar`: se muestra el plan, se pide confirmación y todo o nada.
Los índices salen de `--indice URL` (repetible) o de `~/.config/afm/remoto.json`; el alta por `owner/repo` es D33.

Códigos de salida: 0 ok · 1 el plan tiene errores o se canceló · 2 no se pudo hablar con el índice, no existe lo pedido o el zip no sirve.
"""
from __future__ import annotations

import os
import sys

from . import __version__
from . import instalar as I
from . import paquete as Q
from . import remoto as R
from .paquete_cli import _install_plan
from .ziplector import ZipError


def add_parsers(cs) -> None:
    """`cs`: los subparsers de `afm catalogo`."""
    r = cs.add_parser("remoto", help="explorar e instalar paquetes de un índice en línea (buscar, ver, versiones, instalar, actualizar); "
                                     "los índices salen de --indice o de ~/.config/afm/remoto.json")
    rs = r.add_subparsers(dest="remcmd", required=True)

    def common(p):
        p.add_argument("--indice", action="append", default=[], metavar="URL", help="índice a consultar (repetible; por defecto los de remoto.json)")
        p.add_argument("--sin-red", action="store_true", help="usar solo el índice guardado")

    b = rs.add_parser("buscar", help="buscar paquetes (sin texto: todos)")
    b.add_argument("texto", nargs="?", default="")
    b.add_argument("--tipo", default="", choices=["", *Q.TYPES])
    b.add_argument("--linea", default="", help="nombre de la línea (p. ej. proceres-127)")
    b.add_argument("--licencia", default="", metavar="SPDX")
    b.add_argument("--categoria", default="")
    b.add_argument("--orden", default="descargas", choices=R.SORTS, help="descargas (por defecto; los que no tienen contador van al final), reciente o nombre")
    b.add_argument("--ocultos", action="store_true", help="incluir los que figuran como inaccesibles")
    common(b)
    v = rs.add_parser("ver", help="ficha de un paquete: autor, licencia, línea, tamaño, sha256, versiones retiradas…")
    v.add_argument("ref", metavar="ID")
    common(v)
    vs = rs.add_parser("versiones", help="versiones que el índice conoce de un paquete")
    vs.add_argument("ref", metavar="ID")
    common(vs)
    i = rs.add_parser("instalar", help="bajar un paquete (verificado con el sha256 del índice) e instalarlo: plan, confirmación y todo o nada")
    i.add_argument("ref", metavar="ID[@versión]", help="id, Autor@id o con versión (p. ej. proceres-127-guemes@1.2.0)")
    g = i.add_mutually_exclusive_group()
    g.add_argument("--en", default=None, metavar="RUTA", help="catálogo de destino (por defecto ~/.local/share/afm/catalogos/<línea>; no mira $AFM_CATALOGO)")
    g.add_argument("--como-linea", action="store_true")
    i.add_argument("--permitir-retirada", action="store_true", help="instalar aunque la versión figure como retirada")
    _install_flags(i)
    common(i)
    a = rs.add_parser("actualizar", help="instalar la versión nueva de lo ya instalado (aditivo: no toca lo que usan tus recetas)")
    a.add_argument("ref", nargs="?", default="", metavar="ID", help="solo este paquete (por defecto, todos los instalados que el índice conoce)")
    a.add_argument("--en", default=None, metavar="RUTA", help="catálogo (por defecto, todos los de ~/.local/share/afm/catalogos con paquetes)")
    _install_flags(a)
    a.set_defaults(como_linea=False)
    common(a)


def _install_flags(p) -> None:
    p.add_argument("--simular", action="store_true", help="solo imprimir el plan; no escribe nada")
    p.add_argument("--si", action="store_true", help="no pedir confirmación")
    p.add_argument("--aceptar-cambio-origen", action="store_true")
    p.add_argument("--permitir-fbx", action="store_true")


def _load(args):
    """Los índices legibles (con avisos a stderr), o `None` si no hay ninguno."""
    urls = [u for chunk in args.indice for u in chunk.split(",") if u.strip()] or R.load_config()
    if not urls:
        print(f"ERROR: no hay índices configurados: pasá --indice URL o escribí {R.config_path()}", file=sys.stderr)
        return None
    indexes = []
    for res in R.fetch_indexes(urls, offline=args.sin_red):
        if res.error:
            print(f"AVISO: índice {res.url}: {res.error}", file=sys.stderr)
            continue
        idx = res.index
        if idx.origen == "guardado":
            print(f"AVISO: {res.url}: " + (f"sin conexión ({idx.error_red}); " if idx.error_red else "modo sin red; ") + f"se usa el índice guardado el {idx.obtenido or 'en una fecha desconocida'}", file=sys.stderr)
        indexes.append(idx)
    if not indexes:
        print("ERROR: no se pudo leer ningún índice", file=sys.stderr)
        return None
    return indexes


def _size(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(n, 0) / 1024:.0f} kB"


def _downloads(e) -> str:
    t = (e.descargas or {}).get("total")
    return "—" if not isinstance(t, int) else str(t)


def run_remoto(args) -> int:
    try:
        return _run(args)
    except (R.RemoteError, I.InstallError, ZipError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _run(args) -> int:
    indexes = _load(args)
    if indexes is None:
        return 2
    cmd = args.remcmd
    if cmd == "buscar":
        found = R.search([e for idx in indexes for e in idx.entries], args.texto, tipo=args.tipo, linea=args.linea, licencia=args.licencia,
                         categoria=args.categoria, orden=args.orden, incluir_ocultas=args.ocultos)
        if not found:
            print("No hay paquetes que coincidan.")
            return 0
        for e in found:
            print(f"{e.folder:<34} {e.latest.version if e.latest else '—':<9} {e.tipo or '—':<9} {(e.linea or {}).get('nombre', '—'):<16} "
                  f"{e.licencia or '—':<12} {_downloads(e):>6}  {R.state(e)}" + ("  [experimental]" if e.experimental else ""))
        print(f"{len(found)} paquete(s). Listar no es verificar: la licencia y el origen los declara quien lo publicó.")
        return 0
    if cmd in ("ver", "versiones"):
        ident, _ = R.parse_ref(args.ref)
        e = R.resolve(indexes, ident)
        return _ver(e) if cmd == "ver" else _versiones(e)
    if cmd == "instalar":
        return _instalar(args, indexes)
    return _actualizar(args, indexes)


def _ver(e) -> int:
    rel = e.latest
    print(f"{e.nombre}  ({e.folder})")
    for label, value in (("Última versión", rel.version + (" (prerelease)" if rel.prerelease else "") if rel else "ninguna instalable"),
                         ("Estado", R.state(e)), ("Autor", e.autor), ("Licencia", e.licencia or "—"), ("Origen", e.origen or "—"),
                         ("Tipo", e.tipo or "—"), ("Línea", (e.linea or {}).get("nombre", "—") + (f" ({e.linea['altura_mm']:g} mm)" if isinstance((e.linea or {}).get("altura_mm"), (int, float)) else "")),
                         ("Motor", e.motor or "—"), ("Categorías", ", ".join(e.categorias) or "—"), ("Repo", e.github or "—"),
                         ("Requiere", ", ".join(e.requiere) or "—"), ("No convive con", ", ".join(e.conflicts) or "—"),
                         ("Descargas", _downloads(e)), ("Indice", e.indice)):
        print(f"  {label + ':':<16}{value}")
    if rel:
        print(f"  {'Zip:':<16}{rel.nombre or '—'}, {_size(rel.tamano) if rel.tamano else 'tamaño desconocido'}, sha256 {rel.sha256}")
    if e.retiradas:
        print(f"  {'Retiradas:':<16}{', '.join(e.retiradas)}")
    if e.descripcion:
        print(f"\n{e.descripcion}")
    if e.motor:
        try:
            if not Q.parse_range(e.motor).satisfies(__version__.split("+")[0]):
                print(f"\nAVISO: pide el motor {e.motor} y esta versión de afm es la {__version__}.")
        except Q.PaqueteError:
            pass
    if e.experimental:
        print("\nAVISO: el paquete se marcó como experimental.")
    if not e.visible:
        print("\nAVISO: el repo o el Release de este paquete no responden ahora; no se puede instalar.")
    print("\nListar no es verificar: el índice no revisa el contenido a mano; la licencia y el origen los declara quien lo publicó.")
    return 0


def _versiones(e) -> int:
    rels = ([e.latest] if e.latest else []) + [r for r in e.versiones if not e.latest or r.version != e.latest.version]
    if not rels:
        print(f"«{e.folder}» no tiene versiones instalables en el índice.")
        return 0
    have = R.installed_version(e)
    for r in rels:
        marks = [m for m, on in (("última", e.latest is not None and r.version == e.latest.version), ("prerelease", r.prerelease),
                                 ("RETIRADA", r.version in e.retiradas), ("instalada", r.version == have)) if on]
        print(f"{r.version:<12} {r.publicado[:10] or '—':<11} {_size(r.tamano) if r.tamano else '—':>9}  {' · '.join(marks)}")
    return 0


def _instalar(args, indexes) -> int:
    ident, version = R.parse_ref(args.ref)
    e = R.resolve(indexes, ident, instalable=True)
    rel = e.release(version)
    if rel is None:
        print(f"ERROR: «{e.folder}» no tiene {'la versión ' + version if version else 'una versión instalable'} en el índice", file=sys.stderr)
        return 2
    if args.en:
        root = args.en
    else:
        root = R.default_root(e)
        if os.environ.get("AFM_CATALOGO"):
            print(f"AVISO: $AFM_CATALOGO está definida ({os.environ['AFM_CATALOGO']}) y se ignora al instalar; se usa {root}. Elegí otra ruta con --en.")
    if e.experimental:
        print("AVISO: el paquete se marcó como experimental.")
    print(f"Descargando {e.folder} {rel.version} ({_size(rel.tamano) if rel.tamano else 'tamaño desconocido'})…", file=sys.stderr)
    plan = R.plan_entry(e, root, version=version, como_linea=args.como_linea, allow_fbx=args.permitir_fbx, allow_retirada=args.permitir_retirada)
    return _install_plan(args, plan, I)


def _actualizar(args, indexes) -> int:
    roots = [args.en] if args.en else R.catalog_roots()
    if not roots:
        print("No hay catálogos con paquetes instalados.")
        return 0
    ident = R.parse_ref(args.ref)[0] if args.ref else ""
    rc, hubo = 0, False
    for root in roots:
        todo, avisos = R.pending_updates(indexes, root)
        for a in avisos:
            print(f"AVISO: {a}")
        for e, have in todo:
            if ident and ident not in (e.id, e.folder):
                continue
            hubo = True
            print(f"\n{e.id}: {have} → {e.latest.version} (en {root})")
            plan = R.plan_entry(e, root, allow_fbx=args.permitir_fbx)
            rc = max(rc, _install_plan(args, plan, I))
    if not hubo:
        print("Todo está al día." if not ident else f"«{ident}» no tiene actualizaciones (o no está instalado).")
    return rc
