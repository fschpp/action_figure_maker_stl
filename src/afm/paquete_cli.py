"""Subcomandos de paquetes de `afm catalogo …` (plan de catálogos en línea, §5.6): `exportar` (D03), `paquete validar` (D04), `instalar` (D06)
y las herramientas de autor `paquete init|github|empaquetar` (D09).

Se enchufa desde `catalog_cli.add_parsers` / `_run` para no engordar ese archivo; los siguientes (`paquete …`, `remoto …`)
cuelgan de acá. La lógica vive en `exportar.py`: este módulo solo lee argumentos e imprime.
"""
from __future__ import annotations

import os
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
    i.add_argument("--licencia", default="", metavar="SPDX", help="solo zips heredados (sin paquete.json): licencia del contenido, p. ej. CC-BY-4.0")
    i.add_argument("--origen", default="", choices=["", *Q.ORIGINS], help="solo zips heredados: origen del contenido")
    i.add_argument("--autor", action="append", default=[], metavar="NOMBRE[:github]", help="solo zips heredados (repetible)")
    i.add_argument("--id", default="", help="solo zips heredados: id del paquete (por defecto el nombre de la línea)")
    i.add_argument("--version-paquete", default="", metavar="X.Y.Z", help="solo zips heredados (por defecto 1.0.0)")
    pq = cs.add_parser("paquete", help="herramientas de paquetes .zip (validar, fichas) y de autor (init, github, empaquetar)")
    ps = pq.add_subparsers(dest="paqcmd", required=True)
    v = ps.add_parser("validar", help="revisar un paquete .zip (lint CK101–CK403): manifiesto, hashes, archivos, color, línea y `verify` del contenido")
    v.add_argument("zip", metavar="paquete.zip")
    v.add_argument("--estricto", action="store_true", help="los avisos cuentan como errores (así lo revisa el índice antes de adoptar un release)")
    v.add_argument("--permitir-fbx", action="store_true", help="aceptar .fbx dentro del paquete (los que se exportaron con --incluir-fbx)")
    v.add_argument("--sin-malla", action="store_true", help="no abrir el contenido como catálogo (omite CK203: rápido, pero no revisa las mallas)")
    f = ps.add_parser("fichas", help="juntar las .pieza.json de un paquete en un solo archivo afm-fichas-1 (sin mallas): lo usa el índice y «compatibilidad antes de descargar»")
    f.add_argument("zip", metavar="paquete.zip")
    f.add_argument("-o", "--salida", default="", metavar="FICHAS.json", help="archivo de salida (sin -o se imprime en la consola)")
    f.add_argument("--sobrescribir", action="store_true")
    _add_author_parsers(ps)


def _add_author_parsers(ps) -> None:
    """D09: `paquete init`, `paquete github` y `paquete empaquetar` (trabajan sobre la carpeta del paquete, no sobre un catálogo)."""
    from .autor import PROFILES

    n = ps.add_parser("init", help="crear la carpeta de un paquete nuevo (paquete.json, README, LICENSE, CREDITOS.md y el workflow que publica el Release)")
    n.add_argument("dir", metavar="DIR", help="carpeta nueva (o vacía)")
    n.add_argument("--id", required=True, help="id del paquete (minúsculas, números y guiones)")
    n.add_argument("--perfil", default="heroe", choices=list(PROFILES), help="heroe (por defecto), piezas, linea o coleccion")
    n.add_argument("--nombre", default="")
    n.add_argument("--descripcion", default="", help="una o dos frases (el texto largo va en el README)")
    n.add_argument("--version-paquete", default="0.1.0", metavar="X.Y.Z")
    n.add_argument("--autor", action="append", default=[], metavar="NOMBRE[:github]", help="repetible; obligatorio salvo que se dé --github")
    n.add_argument("--licencia", default="", metavar="SPDX", help="obligatoria (p. ej. CC-BY-4.0): cada persona responde por su contenido")
    n.add_argument("--origen", default="", choices=["", *Q.ORIGINS], help="obligatorio")
    n.add_argument("--github", default="", metavar="owner/repo", help="repo donde se publica (activa el seguimiento del índice)")
    n.add_argument("--linea", default="", help="nombre de la línea (si no, se toma de linea.json al empaquetar)")
    n.add_argument("--altura-mm", type=float, default=0.0, help="altura de la línea en mm")
    n.add_argument("--categoria", action="append", default=[], metavar="CAT", help=f"1 a {Q.CATEGORIES_MAX} de: {', '.join(Q.CATEGORIES)}")
    g = ps.add_parser("github", help="fijar `github` en paquete.json y dejar el workflow de Release (como set-github + add-release-workflow de modkit)")
    g.add_argument("repo", metavar="OWNER/REPO")
    g.add_argument("dir", nargs="?", default=".", metavar="DIR", help="carpeta del paquete (por defecto la actual)")
    g.add_argument("--sobrescribir-workflow", action="store_true", help="reemplazar el workflow aunque tenga cambios propios")
    e = ps.add_parser("empaquetar", help="armar <id>-<version>.zip desde la carpeta del paquete (recalcula archivos y contenido); lo usa el workflow")
    e.add_argument("dir", nargs="?", default=".", metavar="DIR", help="carpeta del paquete (por defecto la actual)")
    e.add_argument("-o", "--salida", default="", metavar="paquete.zip", help="por defecto dist/<id>-<version>.zip dentro de DIR")
    e.add_argument("--version-paquete", default="", metavar="X.Y.Z", help="usar esta versión solo en el zip (el manifiesto del repo no cambia)")
    e.add_argument("--sobrescribir", action="store_true")


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


def run_paquete(args) -> int:
    """`afm catalogo paquete validar|fichas|init|github|empaquetar`."""
    if args.paqcmd in ("init", "github", "empaquetar"):
        return run_autor(args)
    return run_fichas(args) if args.paqcmd == "fichas" else run_validar(args)


def run_autor(args) -> int:
    """`paquete init|github|empaquetar` (D09). 0 = hecho; 2 = falta un dato o la carpeta no sirve (el mensaje dice qué corregir)."""
    from . import autor as A

    try:
        if args.paqcmd == "init":
            res = A.init_package_dir(
                args.dir, id=args.id, perfil=args.perfil, nombre=args.nombre, descripcion=args.descripcion, version=args.version_paquete,
                autores=args.autor, licencia=args.licencia, origen=args.origen, github=args.github, linea=args.linea,
                altura_mm=args.altura_mm, categorias=args.categoria)
            print(f"Paquete «{res.paquete.id}» ({res.paquete.tipo}) creado en {res.dir}:")
            for rel in res.archivos:
                print(f"  {rel}")
            if res.pendientes:
                print("Falta para que `afm catalogo paquete validar --estricto` pase:")
                for t in res.pendientes:
                    print(f"  - {t}")
            if not res.paquete.github:
                print("Cuando tengas el repo: `afm catalogo paquete github OWNER/REPO` fija `github` y deja el workflow.")
            return 0
        if args.paqcmd == "github":
            changed, wrote = A.set_github(args.dir, args.repo, overwrite_workflow=args.sobrescribir_workflow)
            print(f"github = {args.repo}" + ("" if changed else " (ya estaba)"))
            print(f"{A.WORKFLOW_PATH}: " + ("escrito" if wrote else "ya está al día"))
            return 0
        out = args.salida
        if not out:
            _p, raw = A._read_manifest_raw(args.dir)
            ver = args.version_paquete or str(raw.get("version", ""))
            out = os.path.join(args.dir, A.DIST_DIR, f"{raw.get('id', '')}-{ver}.zip")
        res = A.pack_dir(args.dir, out, version=args.version_paquete, overwrite=args.sobrescribir)
    except A.AutorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    p = res.paquete
    print(f"Paquete «{p.id}» {p.version} ({p.tipo}) → {res.path}")
    print(f"  {len(p.contenido.piezas)} pieza(s), {len(p.contenido.recetas)} receta(s), paleta: {'sí' if p.contenido.paleta else 'no'}; "
          f"{len(res.archivos) + 1} archivos, {res.tamano / 1e6:.2f} MB, sha256 {res.sha256}")
    for w in res.avisos:
        print("AVISO:", w)
    return 0


def run_fichas(args) -> int:
    """`afm catalogo paquete fichas ZIP [-o FICHAS.json]`: no abre ningún STL. 0 = escrito; 2 = el zip no sirve."""
    from . import indice as X
    from .ziplector import ZipError

    try:
        text = X.dump_json(X.fichas_from_zip(args.zip))
    except (ZipError, X.IndiceError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not args.salida:
        print(text, end="")
        return 0
    if os.path.exists(args.salida) and not args.sobrescribir:
        print(f"ERROR: «{args.salida}» ya existe (usá --sobrescribir)", file=sys.stderr)
        return 2
    with open(args.salida, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Fichas de «{args.zip}» → {args.salida}")
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


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _legacy_info(args) -> dict | None:
    """Datos que un zip heredado no trae y solo quien instala puede dar (licencia, origen, autor): de las opciones o, si faltan, se preguntan."""
    lic, origen, autores = args.licencia, args.origen, list(args.autor)
    if not lic:
        lic = _ask(f"Licencia del contenido (SPDX, p. ej. CC-BY-4.0; válidas: {', '.join(Q.LICENSES[:4])}…): ")
    if not origen:
        origen = _ask(f"Origen del contenido ({', '.join(Q.ORIGINS)}): ")
    if not autores:
        autores = [a for a in [_ask("Autor (nombre o nombre:usuario-github): ")] if a]
    if not (lic and origen and autores):
        print("ERROR: este zip no trae paquete.json: hace falta indicar --licencia, --origen y --autor (cada persona responde por su contenido)", file=sys.stderr)
        return None
    return {"licencia": lic, "origen": origen, "autores": autores, "id": args.id, "version": args.version_paquete}


def run_instalar(args) -> int:
    """`afm catalogo instalar ZIP`: plan → confirmación → instalación atómica. 0 = instalado (o nada que hacer / simulado sin errores); 1 = el
    plan tiene errores o se canceló; 2 = no es un zip o un paquete legible. Un zip heredado (sin `paquete.json`, D07) se convierte antes."""
    import os
    import tempfile

    from . import instalar as I
    from .ziplector import ZipError

    with tempfile.TemporaryDirectory(prefix="afm-instalar-") as work:
        try:
            legacy = I.detect_legacy(args.zip)
            if legacy is not None:
                info = _legacy_info(args)
                if info is None:
                    return 2
                conv = I.prepare_legacy(args.zip, os.path.join(work, "heredado.zip"), allow_fbx=args.permitir_fbx, **info)
                pkg = conv.paquete
            else:
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
            if legacy is not None:
                plan = I.plan_install(conv.path, root, como_linea=args.como_linea, allow_fbx=args.permitir_fbx)
                plan.descartados, plan.heredado_sha256 = conv.descartados, conv.original_sha256
                plan.issues = [I.Issue(I.WARNING, "zip heredado", a) for a in conv.avisos] + plan.issues
            else:
                plan = I.plan_install(args.zip, root, como_linea=args.como_linea, allow_fbx=args.permitir_fbx)
        except (ZipError, I.InstallError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        return _install_plan(args, plan, I)


def _install_plan(args, plan, I) -> int:
    for line in plan.describe():
        print(line)
    if not plan.ok:
        print(f"FALLA: no se puede instalar: {len(plan.errors)} error(es).")
        if plan.sugerir_linea and not args.como_linea:
            print("Sugerencia: instalarlo como línea nueva con `--como-linea` (o `--en RUTA` con una carpeta nueva).")
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
        if _ask("¿Instalar? [s/N] ").lower() not in ("s", "si", "sí", "y", "yes"):
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


__all__ = ["ExportError", "add_parsers", "run_exportar", "run_validar", "run_paquete", "run_fichas", "run_instalar", "run_autor"]
