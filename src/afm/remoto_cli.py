"""`afm catalogo remoto …` (plan de catálogos en línea, §5.6, D14 y D16): buscar, ver, versiones, instalar, actualizar y auditar desde un índice.

Solo lee argumentos e imprime; la lógica está en `remoto.py` (red, búsqueda, resolución) y `instalar.py` (plan y aplicación atómica). Instalar y
actualizar pasan por el **mismo** `paquete_cli._install_plan` que `afm catalogo instalar`: se muestra el plan, se pide confirmación y todo o nada.
Los índices salen de `--indice URL` (repetible) o de `~/.config/afm/remoto.json`; el alta por `owner/repo` es D33.

D16: `instalar` y `actualizar` resuelven `requiere` por el índice (`dependencias.resolve`) y, si hay que instalar más de un paquete, muestran **un único plan** y
lo aplican todo o nada (`dependencias.install_chain`); `--sin-dependencias` instala solo el pedido (queda el aviso de lo que falta). `auditar` avisa de lo ya
instalado que el índice retiró o bloqueó, sin borrar nada.

Códigos de salida: 0 ok · 1 el plan tiene errores o se canceló (en `auditar`: hay alertas) · 2 no se pudo hablar con el índice, no existe lo pedido o el zip no sirve.
"""
from __future__ import annotations

import os
import sys

from . import __version__
from . import dependencias as D
from . import instalar as I
from . import paquete as Q
from . import remoto as R
from .catalog import CatalogError
from .paquete_cli import _ask, _install_plan
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
    v.add_argument("--compat", action="store_true", help="medir cuántas piezas del paquete encajan con tu catálogo, sin bajar los STL (baja solo sus fichas)")
    v.add_argument("--en", default=None, metavar="RUTA", help="catálogo contra el que medir con --compat (por defecto ~/.local/share/afm/catalogos/<línea>; no mira $AFM_CATALOGO)")
    common(v)
    vs = rs.add_parser("versiones", help="versiones que el índice conoce de un paquete")
    vs.add_argument("ref", metavar="ID")
    common(vs)
    i = rs.add_parser("instalar", help="bajar un paquete (verificado con el sha256 del índice) e instalarlo: plan, confirmación y todo o nada")
    i.add_argument("ref", metavar="ID[@versión]", help="id, Autor@id o con versión (p. ej. proceres-127-guemes@1.2.0)")
    g = i.add_mutually_exclusive_group()
    g.add_argument("--en", default=None, metavar="RUTA", help="catálogo de destino (por defecto ~/.local/share/afm/catalogos/<línea>; no mira $AFM_CATALOGO)")
    g.add_argument("--como-linea", action="store_true")
    i.add_argument("--permitir-retirada", action="store_true", help="instalar aunque la versión figure como retirada (un bloqueo no se salta)")
    i.add_argument("--sin-dependencias", action="store_true", help="instalar solo este paquete, sin resolver su `requiere`")
    _install_flags(i)
    common(i)
    a = rs.add_parser("actualizar", help="instalar la versión nueva de lo ya instalado (aditivo: no toca lo que usan tus recetas)")
    a.add_argument("ref", nargs="?", default="", metavar="ID", help="solo este paquete (por defecto, todos los instalados que el índice conoce)")
    a.add_argument("--en", default=None, metavar="RUTA", help="catálogo (por defecto, todos los de ~/.local/share/afm/catalogos con paquetes)")
    _install_flags(a)
    a.set_defaults(como_linea=False)
    common(a)
    au = rs.add_parser("auditar", help="avisar de lo instalado que el índice retiró o bloqueó (no borra nada)")
    au.add_argument("--en", default=None, metavar="RUTA", help="catálogo (por defecto, todos los de ~/.local/share/afm/catalogos con paquetes)")
    common(au)


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
    except (R.RemoteError, I.InstallError, CatalogError, ZipError) as exc:
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
        if cmd == "versiones":
            return _versiones(e)
        code = _ver(e)
        return _compat(args, e) if args.compat and code == 0 else code
    if cmd == "instalar":
        return _instalar(args, indexes)
    if cmd == "auditar":
        return _auditar(args, indexes)
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
    if e.bloqueado:
        print(f"\nAVISO: este paquete está {e.bloqueado}; no se puede instalar.")
    elif not e.visible:
        print("\nAVISO: el repo o el Release de este paquete no responden ahora; no se puede instalar.")
    print("\nListar no es verificar: el índice no revisa el contenido a mano; la licencia y el origen los declara quien lo publicó.")
    return 0


def _compat(args, e) -> int:
    """D15: `ver --compat`: mide las fichas del paquete contra el catálogo local. Sin catálogo no hay con qué comparar."""
    from . import compat_remota as CR
    from .catalog import Catalog, CatalogError

    if not e.instalable:
        print("\nNo se puede medir la compatibilidad: el paquete no tiene una versión instalable.")
        return 0
    root = args.en or R.default_root(e)
    try:
        cat = Catalog.open(root)
    except CatalogError:
        print(f"\nNo hay un catálogo en «{root}» con qué comparar (instalá el paquete como línea nueva o indicá --en RUTA).")
        return 0
    fichas = CR.fetch_fichas(e, offline=args.sin_red)
    print()
    for line in CR.report_lines(CR.check(cat, fichas)):
        print(line)
    return 0


def _versiones(e) -> int:
    rels = ([e.latest] if e.latest else []) + [r for r in e.versiones if not e.latest or r.version != e.latest.version]
    if not rels:
        print(f"«{e.folder}» no tiene versiones instalables en el índice.")
        return 0
    have = R.installed_version(e)
    for r in rels:
        marks = [m for m, on in (("última", e.latest is not None and r.version == e.latest.version), ("prerelease", r.prerelease),
                                 ("RETIRADA", r.version in e.retiradas), ("BLOQUEADA", bool(e.block_of(r))), ("instalada", r.version == have)) if on]
        print(f"{r.version:<12} {r.publicado[:10] or '—':<11} {_size(r.tamano) if r.tamano else '—':>9}  {' · '.join(marks)}")
    return 0


def _instalar(args, indexes) -> int:
    ident, version = R.parse_ref(args.ref)
    e = R.resolve(indexes, ident, instalable=True)
    rel = e.release(version)
    if rel is None:
        print(f"ERROR: «{e.folder}» no tiene {'la versión ' + version if version else 'una versión instalable'} en el índice", file=sys.stderr)
        return 2
    if e.tipo == "coleccion":                                          # D34: no trae piezas; fija otros paquetes
        return _instalar_coleccion(args, indexes, e, version)
    if args.en:
        root = args.en
    else:
        root = R.default_root(e)
        if os.environ.get("AFM_CATALOGO"):
            print(f"AVISO: $AFM_CATALOGO está definida ({os.environ['AFM_CATALOGO']}) y se ignora al instalar; se usa {root}. Elegí otra ruta con --en.")
    if e.experimental:
        print("AVISO: el paquete se marcó como experimental.")
    return _install_with_deps(args, indexes, root, e, version)


def _instalar_coleccion(args, indexes, e, version: str | None) -> int:
    """D34: baja la colección, resuelve cada pin contra el índice (versión y `sha256` exactos) y los instala **todos o ninguno** con una sola confirmación."""
    from . import coleccion as C

    retirada = getattr(args, "permitir_retirada", False)
    print(f"Descargando la colección {e.folder} {(e.release(version) or e.latest).version}…", file=sys.stderr)
    pkg = C.cargar(R.download_package(e, version=version, allow_retirada=retirada))
    dep = C.resolver(indexes, pkg, args.en, allow_retirada=retirada)
    print(f"Colección «{pkg.nombre}» ({pkg.id} {pkg.version}): fija {len(pkg.contenido.pines)} paquete(s); se instalan todos o ninguno:")
    for n, s_ in enumerate(dep.pasos, 1):
        print(f"  {n}. {s_.entry.folder} {s_.release.version}")
    if dep.errors:
        for i in dep.errors:
            print(f"  {i}")
        print(f"FALLA: la colección no se puede instalar: {len(dep.errors)} error(es) en sus pines. No se instaló nada.")
        return 1
    print("Descargando…", file=sys.stderr)
    chain = D.plan_chain(dep, como_linea=args.como_linea, allow_fbx=args.permitir_fbx, allow_retirada=retirada)
    return _install_chain(args, chain, dep)


def _install_with_deps(args, indexes, root: str, e, version: str | None) -> int:
    """Resuelve `requiere`; sin nada más que instalar sigue el camino de siempre (`plan_entry` + `_install_plan`); con dependencias, un solo plan todo o nada."""
    retirada = getattr(args, "permitir_retirada", False)
    want = e.release(version) or e.latest
    if want is not None and want.version in e.retiradas and not retirada:                # lo pedido directo: error de uso, como antes de D16
        raise R.RemoteError(f"la versión {want.version} de «{e.id}» fue retirada por sus responsables; no se instala")
    if getattr(args, "sin_dependencias", False):
        dep = None
    else:
        dep = D.resolve(indexes, root, [(e, version)], allow_retirada=retirada)
        if dep.errors:
            for line in dep.describe():
                print(line)
            print(f"FALLA: no se puede instalar «{e.folder}»: {len(dep.errors)} error(es) en sus dependencias. No se instaló nada.")
            return 1
    if dep is None or len(dep.pasos) <= 1:
        for w in (dep.warnings if dep else []):
            print(w)
        print(f"Descargando {e.folder} {(e.release(version) or e.latest).version}…", file=sys.stderr)
        plan = R.plan_entry(e, root, version=version, como_linea=args.como_linea, allow_fbx=args.permitir_fbx, allow_retirada=retirada)
        return _install_plan(args, plan, I)
    print(f"«{e.folder}» necesita {len(dep.pasos) - 1} paquete(s) más; se instalan todos o ninguno:")
    for n, s_ in enumerate(dep.pasos, 1):
        print(f"  {n}. {s_.entry.folder} {s_.release.version}" + ("" if s_.por == "pedido" else f"  ({s_.por})"))
    print("Descargando…", file=sys.stderr)
    chain = D.plan_chain(dep, como_linea=args.como_linea, allow_fbx=args.permitir_fbx, allow_retirada=retirada)
    return _install_chain(args, chain, dep)


def _install_chain(args, chain, dep) -> int:
    for line in chain.describe():
        print(line)
    for i in dep.warnings:
        print(i)
    if not chain.ok:
        print(f"FALLA: no se puede instalar: {len(chain.errors)} error(es). No se instaló nada.")
        return 1
    if args.simular:
        print("Simulación: no se escribió nada.")
        return 0
    if chain.nada_que_hacer:
        print(f"Nada que instalar: todo ya está en {chain.raiz}.")
        return 0
    print("Aviso: la licencia y el origen los declara quien hizo cada paquete; la app no los verifica (listar no es verificar).")
    if chain.cambia_origen and not args.aceptar_cambio_origen:
        print("ERROR: algún paquete cambia de origen respecto de lo instalado; confirmalo con --aceptar-cambio-origen.", file=sys.stderr)
        return 1
    if not args.si and _ask(f"¿Instalar los {len(chain.pasos)} paquetes? [s/N] ").lower() not in ("s", "si", "sí", "y", "yes"):
        print("Cancelado: no se escribió nada.")
        return 1
    try:
        results = D.install_chain(chain, confirmar_origen=args.aceptar_cambio_origen)
    except (I.InstallError, CatalogError, OSError) as exc:
        print(f"ERROR: no se instaló nada: {exc}", file=sys.stderr)
        return 1
    for res in results:
        print(f"Instalado «{res.paquete.id}» {res.paquete.version}: {len(res.piezas)} pieza(s), {len(res.recetas)} receta(s), {len(res.roles)} rol(es) de paleta"
              + (f"; {len(res.omitidas)} ya estaban" if res.omitidas else "") + ".")
        for w in res.avisos:
            print("AVISO:", w)
    return 0


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
            rc = max(rc, _install_with_deps(args, indexes, root, e, None))
        for alert in D.audit(indexes, root):
            print(f"AVISO: {alert}")
    if not hubo:
        print("Todo está al día." if not ident else f"«{ident}» no tiene actualizaciones (o no está instalado).")
    return rc


def _auditar(args, indexes) -> int:
    roots = [args.en] if args.en else R.catalog_roots()
    if not roots:
        print("No hay catálogos con paquetes instalados.")
        return 0
    found = 0
    for root in roots:
        alerts = D.audit(indexes, root)
        found += len(alerts)
        for a in alerts:
            print(f"{a}   [{root}]")
    if not found:
        print("Nada que avisar: ningún paquete instalado figura como retirado ni bloqueado.")
        return 0
    print(f"{found} alerta(s). No se borró nada: decidí vos qué hacer con cada paquete.")
    return 1
