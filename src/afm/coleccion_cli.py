"""`afm catalogo coleccion comprobar` (plan de catálogos en línea, §5.12 y D34): lo que el CI del índice llama para CK402 contra el índice.
La lógica está en `coleccion.py`; acá solo se leen argumentos y se imprime. No necesita catálogo ni red.

Códigos de salida: 0 todos los pines están bien · 1 hay pines que impiden instalar la colección · 2 un archivo no se puede leer o no es válido."""
from __future__ import annotations

import sys

from . import coleccion as C
from . import instalar as I
from . import remoto as R
from .catalog import CatalogError
from .ziplector import ZipError


def add_parsers(cs) -> None:
    c = cs.add_parser("coleccion", help="colecciones de paquetes fijados (D34): comprobar que cada pin existe y su sha256 coincide con el índice")
    cc = c.add_subparsers(dest="colcmd", required=True)
    k = cc.add_parser("comprobar", help="CK402: ¿cada pin de la colección existe en el índice con ese sha256 y no está retirado ni bloqueado? sale 1 si no")
    k.add_argument("zip", metavar="COLECCION.zip")
    k.add_argument("--indice", required=True, metavar="index.json", help="el índice ya construido (el mismo formato que sirve Pages)")
    k.add_argument("--permitir-retirada", action="store_true")


def run_coleccion(args) -> int:
    try:
        pkg = C.cargar(args.zip)
        try:
            with open(args.indice, encoding="utf-8") as fh:
                data = fh.read()
        except (OSError, ValueError) as exc:
            print(f"ERROR: no se pudo leer {args.indice}: {exc}", file=sys.stderr)
            return 2
        index = R.parse_index(data, url="https://example.invalid/")
    except (I.InstallError, R.RemoteError, CatalogError, ZipError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    issues = C.comprobar_pines([index], pkg, allow_retirada=args.permitir_retirada)
    for i in issues:
        print(f"CK402 {i.subject}: {i.message}")
    if issues:
        print(f"FALLA: «{pkg.id}» {pkg.version}: {len(issues)} pin(es) con problemas; la colección queda fuera.")
        return 1
    print(f"«{pkg.id}» {pkg.version}: los {len(pkg.contenido.pines)} pin(es) existen con el sha256 que fija la colección.")
    return 0
