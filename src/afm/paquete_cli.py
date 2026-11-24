"""Subcomandos de paquetes de `afm catalogo …` (plan de catálogos en línea, §5.6): por ahora `exportar` (D03).

Se enchufa desde `catalog_cli.add_parsers` / `_run` para no engordar ese archivo; los siguientes (`instalar`, `paquete …`, `remoto …`)
cuelgan de acá. La lógica vive en `exportar.py`: este módulo solo lee argumentos e imprime.
"""
from __future__ import annotations

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


__all__ = ["ExportError", "add_parsers", "run_exportar"]
