"""Subcomandos de paquetes de `afm catalogo …` (plan de catálogos en línea, §5.6): `exportar` (D03), `paquete validar` (D04) e `instalar` (D06).

Se enchufa desde `catalog_cli.add_parsers` / `_run` para no engordar ese archivo; los siguientes (`paquete …`, `remoto …`)
cuelgan de acá. La lógica vive en `exportar.py`: este módulo solo lee argumentos e imprime.
"""
from __future__ import annotations

import sys

from . import paquete as Q
from .catalog import Catalog
from .exportar import ExportError, export_package


def _csv(values) -> list[str]:
    return [v.strip() for chunk in (values or []) for v in chunk.split(",") if v.strip()]


def add_parsers(cs, root) -> None:
    """`cs`: los subparsers de `afm catalogo`; `root(p)`: agrega el argumento `ruta` del catálogo."""
    a = cs.add_parser("exportar", help="armar un paquete .zip (afm-paquete-1) con piezas, recetas o la línea completa para compartirlo; "
                                       "no incluye el FBX de color salvo --incluir-fbx ni lo derivado (compat, papelera, caballos)")
    root(a)
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--piezas", action="append", metavar="A,B", help="piezas a exportar (id = la última versión, o id@n); un brazo con muñeca lleva su mano si existe")
    g.add_argument("--receta", action="append", metavar="NOMBRE", help="receta(s) de figuras/ y todas las piezas que usa (héroe completo)")
    g.add_argument("--linea-completa", action="store_true", help="toda la línea: todas las piezas (todas las versiones), recetas y paleta")
    a.add_argument("-o", "--salida", required=True, metavar="paquete.zip")
    a.add_argument("--sobrescribir", action="store_true", help="reemplazar la salida si ya existe")
    a.add_argument("--incluir-fbx", action="store_true", help="incluir el FBX de color de las piezas en modo rgb (pesa 26–36 MB cada uno); sin esto salen sin color")
    a.add_argument("--manifiesto", default=None, metavar="paquete.json", help="paquete.json ya escrito: sus campos son los valores por defecto de los de abajo")
    a.add_argument("--id", default="", help="id del paquete (por defecto sale de la línea y la receta)")
    a.add_argument("--nombre", default="")
    a.add_argument("--version-paquete", default="", metavar="X.Y.Z", help="versión semver del paquete (por defecto 1.0.0)")
    a.add_argument("--descripcion", default="", help="una o dos frases (el texto largo va en el README)")
    a.add_argument("--tipo", default="", choices=["", *Q.TYPES])
    a.add_argument("--autor", action="append", default=[], metavar="NOMBRE[:github]", help="repetible; obligatorio salvo que lo traiga --manifiesto")
    a.add_argument("--licencia", default="", metavar="SPDX", help="obligatoria (p. ej. CC-BY-4.0): cada persona responde por su contenido")
    a.add_argument("--origen", default="", choices=["", *Q.ORIGINS], help="obligatorio salvo que lo traiga --manifiesto")
    a.add_argument("--github", default="", metavar="owner/repo")
    a.add_argument("--categoria", action="append", default=[], metavar="CAT", help=f"1 a {Q.CATEGORIES_MAX} de: {', '.join(Q.CATEGORIES)}")
    a.add_argument("--etiqueta", action="append", default=[])
    a.add_argument("--readme", default="", metavar="ARCHIVO.md", help="README propio (si no, se genera uno)")
    a.add_argument("--licencia-archivo", default="", metavar="ARCHIVO", help="texto de la licencia (si no, LICENSE nombra la licencia y su enlace SPDX)")
    a.add_argument("--creditos", default="", metavar="ARCHIVO.md")
    a.add_argument("--portada", default="", metavar="IMAGEN", help="portada de 1280×720; se guarda como previews/portada.png")
    i = cs.add_parser("instalar", help="instalar un paquete .zip en un catálogo: valida, muestra el plan y recién entonces escribe (todo o nada); "
                                       "sin --en usa ~/.local/share/afm/catalogos/<línea> (no mira $AFM_CATALOGO)")
    i.add_argument("zip", metavar="paquete.zip")
    g = i.add_mutually_exclusive_group()
    g.add_argument("--en", default=None, metavar="RUTA", help="catálogo de destino (si no existe se crea como línea nueva con la línea del paquete)")
    g.add_argument("--como-linea", action="store_true", help="instalar como línea nueva (la carpeta no puede ser un catálogo que ya existe)")
    i.add_argument("--simular", action="store_true", help="solo imprimir el plan; no escribe nada")
    i.add_argument("--si", action="store_true", help="no pedir confirmación")
    i.add_argument("--aceptar-cambio-origen", action="store_true", help="confirmar que un paquete ya instalado cambia de repo de origen")
    i.add_argument("--permitir-fbx", action="store_true", help="aceptar .fbx dentro del paquete")
    pq = cs.add_parser("paquete", help="herramientas de paquetes .zip (por ahora: validar)")
    ps = pq.add_subparsers(dest="paqcmd", required=True)
    v = ps.add_parser("validar", help="revisar un paquete .zip (lint CK101–CK403): manifiesto, hashes, archivos, color, línea y `verify` del contenido")
    v.add_argument("zip", metavar="paquete.zip")
    v.add_argument("--estricto", action="store_true", help="los avisos cuentan como errores (así lo revisa el índice antes de adoptar un release)")
    v.add_argument("--permitir-fbx", action="store_true", help="aceptar .fbx dentro del paquete (los que se exportaron con --incluir-fbx)")
    v.add_argument("--sin-malla", action="store_true", help="no abrir el contenido como catálogo (omite CK203: rápido, pero no revisa las mallas)")


def run_exportar(cat: Catalog, args) -> int:
    base = Q.Paquete.load(args.manifiesto) if args.manifiesto else None
    if base is not None:
        for w in base.avisos:
            print(f"AVISO: {args.manifiesto}: {w}")
    res = export_package(
        cat, args.salida, piezas=_csv(args.piezas), recetas=_csv(args.receta), linea_completa=args.linea_completa,
        incluir_fbx=args.incluir_fbx, base=base, id=args.id, nombre=args.nombre, version=args.version_paquete,
        descripcion=args.descripcion, tipo=args.tipo, autores=args.autor, licencia=args.licencia, origen=args.origen,
        github=args.github, categorias=_csv(args.categoria), etiquetas=args.etiqueta, readme=args.readme,
        license_file=args.licencia_archivo, creditos=args.creditos, portada=args.portada, overwrite=args.sobrescribir)
    p = res.paquete
    print(f"Paquete «{p.id}» {p.version} ({p.tipo}) → {res.path}")
    print(f"  {len(p.contenido.piezas)} pieza(s), {len(p.contenido.recetas)} receta(s), paleta: {'sí' if p.contenido.paleta else 'no'}; "
          f"{len(res.archivos) + 1} archivos, {res.tamano / 1e6:.2f} MB, sha256 {res.sha256}")
    for w in res.avisos:
        print("AVISO:", w)
    return 0


def run_validar(args) -> int:
    """`afm catalogo paquete validar ZIP`: imprime cada hallazgo con su archivo y qué corregir. 0 = sin errores; 1 = hay errores."""
    from .validar import validate_package
    from .ziplector import ZipError

    try:
        res = validate_package(args.zip, estricto=args.estricto, allow_fbx=args.permitir_fbx, verify_catalog=not args.sin_malla)
    except ZipError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for f in res.findings:
        print(f)
    p = res.paquete
    head = f"«{p.id}» {p.version}" if p else args.zip
    if res.ok:
        print(f"OK: {head} no tiene errores" + (f" ({len(res.warnings)} aviso(s))" if res.warnings else "") + ".")
        return 0
    print(f"FALLA: {head}: {len(res.errors)} error(es)" + (f", {len(res.warnings)} aviso(s)" if res.warnings else "") + ".")
    return 1


def run_instalar(args) -> int:
    """`afm catalogo instalar ZIP`: plan → confirmación → instalación atómica. 0 = instalado (o nada que hacer / simulado sin errores); 1 = el
    plan tiene errores o se canceló; 2 = no es un zip o un paquete legible."""
    import os

    from . import instalar as I
    from .ziplector import ZipError

    try:
        pkg = I.peek_package(args.zip)
        if args.en:
            root = args.en
        else:
            if not pkg.linea:
                print("ERROR: el paquete no declara una línea: indicá el catálogo con --en", file=sys.stderr)
                return 2
            root = I.explicit_root(pkg.linea["nombre"])
            if os.environ.get("AFM_CATALOGO"):
                print(f"AVISO: $AFM_CATALOGO está definida ({os.environ['AFM_CATALOGO']}) y se ignora al instalar; se usa {root}. Elegí otra ruta con --en.")
        plan = I.plan_install(args.zip, root, como_linea=args.como_linea, allow_fbx=args.permitir_fbx)
    except (ZipError, I.InstallError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for line in plan.describe():
        print(line)
    if not plan.ok:
        print(f"FALLA: no se puede instalar: {len(plan.errors)} error(es).")
        if plan.sugerir_linea and not args.como_linea:
            print(f"Sugerencia: instalarlo como línea nueva con `--como-linea` (o `--en RUTA` con una carpeta nueva).")
        return 1
    if args.simular:
        print("Simulación: no se escribió nada.")
        return 0
    if plan.nada_que_hacer:
        print(f"Nada que instalar: «{plan.paquete.id}» {plan.paquete.version} ya está en {plan.root}.")
        return 0
    print("Aviso: la licencia y el origen los declara quien hizo el paquete; la app no los verifica (listar no es verificar).")
    if plan.cambia_origen and not args.aceptar_cambio_origen:
        print("ERROR: el paquete cambia de origen respecto de lo instalado; confirmalo con --aceptar-cambio-origen.", file=sys.stderr)
        return 1
    if not args.si:
        try:
            ans = input("¿Instalar? [s/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("s", "si", "sí", "y", "yes"):
            print("Cancelado: no se escribió nada.")
            return 1
    try:
        res = I.apply(plan, confirmar_origen=args.aceptar_cambio_origen)
    except (I.InstallError, OSError) as exc:
        print(f"ERROR: no se instaló nada: {exc}", file=sys.stderr)
        return 1
    print(f"Instalado «{res.paquete.id}» {res.paquete.version} en {res.root}: {len(res.piezas)} pieza(s), {len(res.recetas)} receta(s), {len(res.roles)} rol(es) de paleta"
          + (f"; {len(res.omitidas)} ya estaban" if res.omitidas else "") + ".")
    for w in res.avisos:
        print("AVISO:", w)
    return 0


__all__ = ["ExportError", "add_parsers", "run_exportar", "run_validar", "run_instalar"]
