#!/usr/bin/env python3
"""IX106: `bloqueados.json` es válido y no hay nada bloqueado entre las altas ni en el índice publicado.

    python scripts/comprobar_bloqueados.py [--raiz .] [--bloqueados bloqueados.json] [--indice data/index.json]

Sin `--indice` revisa las carpetas de `paquetes/` (modo alta: se rechaza). Con `--indice` revisa además las entradas de ese `index.json`
(modo listado: hay que retirarlas). Sale con 1 si `bloqueados.json` no es válido o si algo está bloqueado. Es un envoltorio de `afm.bloqueos`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

from afm import bloqueos as B  # noqa: E402


def entradas_de_altas(raiz: str, solo=None) -> list:
    out = []
    for folder in C.carpetas(raiz, solo):
        m = C.FOLDER_RE.match(folder)
        meta, _ = C.leer_meta(raiz, folder)
        out.append({"folder": folder, "id": m.group(2) if m else (meta or {}).get("id", ""), "autor": (meta or {}).get("autor") or (m.group(1) if m else "")})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--raiz", default=C.ROOT)
    ap.add_argument("--bloqueados", default=None, metavar="ARCHIVO")
    ap.add_argument("--indice", default=None, metavar="index.json")
    args = ap.parse_args(argv)
    try:
        bloqueados = B.leer_bloqueados(args.bloqueados or os.path.join(args.raiz, "bloqueados.json"))
    except B.BloqueosError as exc:
        print(f"IX106 error: {exc}", file=sys.stderr)
        return 1
    hallazgos = [C.Hallazgo(h.codigo, h.severidad, h.folder, h.mensaje) for h in B.comprobar_bloqueados(entradas_de_altas(args.raiz), bloqueados, modo="alta")]
    if args.indice:
        try:
            with open(args.indice, encoding="utf-8") as f:
                indice = json.load(f)
        except (OSError, ValueError) as exc:
            print(f"IX106 error: no pude leer {args.indice}: {exc}", file=sys.stderr)
            return 1
        hallazgos += [C.Hallazgo(h.codigo, h.severidad, h.folder, h.mensaje) for h in B.comprobar_bloqueados(indice.get("paquetes", []), bloqueados, modo="listado")]
    C.mostrar(hallazgos)
    print(f"{len(bloqueados)} bloqueo(s) en vigor: {len(hallazgos)} coincidencia(s)")
    return 1 if C.hay_errores(hallazgos) else 0


if __name__ == "__main__":
    sys.exit(main())
