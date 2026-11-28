"""`afm catalogo bloqueados …` (plan de catálogos en línea, §5.5 y D24): lo que el CI del índice llama para validar `bloqueados.json`, rechazar altas y
retirar lo ya listado. La lógica está en `bloqueos.py`; acá solo se leen argumentos y se imprime.

Códigos de salida: 0 ok · 1 hay hallazgos (alta rechazada, o algo listado que hay que retirar) · 2 un archivo no se puede leer o no es válido."""
from __future__ import annotations

import json
import sys

from . import bloqueos as B
from . import indice as X
from .catalog import CatalogError


def add_parsers(cs) -> None:
    b = cs.add_parser("bloqueados", help="validar bloqueados.json, rechazar altas bloqueadas (IX106) y retirar lo ya listado del índice")
    bs = b.add_subparsers(dest="blkcmd", required=True)
    v = bs.add_parser("validar", help="comprobar que bloqueados.json es válido (un solo criterio por línea, motivo y fecha)")
    v.add_argument("archivo", metavar="bloqueados.json")
    c = bs.add_parser("comprobar", help="IX106: ¿alguna alta (zip) o entrada del índice coincide con lo bloqueado? sale 1 si sí")
    c.add_argument("archivo", metavar="bloqueados.json")
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument("--zip", action="append", default=[], metavar="PAQUETE.zip", help="un alta: el zip candidato (repetible)")
    g.add_argument("--indice", metavar="index.json", help="un índice ya construido: lo que sigue listado y está bloqueado")
    c.add_argument("--autor", default="", help="autor de la alta (para bloqueos por autor; con --zip)")
    r = bs.add_parser("retirar", help="escribir el index.json sin lo bloqueado (no modifica el de entrada)")
    r.add_argument("archivo", metavar="bloqueados.json")
    r.add_argument("--indice", required=True, metavar="index.json")
    r.add_argument("--salida", required=True, metavar="index.json", help="dónde escribir el índice ya filtrado")


def run_bloqueados(args) -> int:
    try:
        return _run(args)
    except (B.BloqueosError, CatalogError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


def _indice(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError) as exc:
        raise B.BloqueosError(f"no se pudo leer {path}: {exc}") from exc
    if not isinstance(d, dict) or not isinstance(d.get("paquetes"), list):
        raise B.BloqueosError(f"{path} no es un index.json (falta «paquetes»)")
    return d


def _run(args) -> int:
    blocks = B.leer_bloqueados(args.archivo)
    cmd = args.blkcmd
    if cmd == "validar":
        print(f"{args.archivo}: válido ({len(blocks)} bloqueo(s))")
        return 0
    if cmd == "comprobar":
        if args.indice:
            hallazgos = B.comprobar_bloqueados(_indice(args.indice)["paquetes"], blocks, modo="listado")
        else:
            entradas = [X.entry_from_zip(z, url="https://example.invalid/" + z.rsplit("/", 1)[-1], autor=args.autor) for z in args.zip]
            hallazgos = B.comprobar_bloqueados(entradas, blocks, modo="alta")
        for h in hallazgos:
            print(h)
        if not hallazgos:
            print("Nada bloqueado.")
            return 0
        return 1
    nuevo, quitados = B.retirar(_indice(args.indice), blocks)       # retirar
    with open(args.salida, "w", encoding="utf-8") as fh:
        fh.write(X.dump_json(nuevo))
    for h in quitados:
        print(h)
    print(f"{args.salida}: {nuevo['cantidad']} paquete(s); se retiraron {len(quitados)}.")
    return 0
