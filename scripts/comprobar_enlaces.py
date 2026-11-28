#!/usr/bin/env python3
"""IX105 barato: ¿el repo existe, tiene el Release que el índice sigue y ese Release trae su `.zip`? No baja nada.

    python scripts/comprobar_enlaces.py [--raiz .] [--solo CARPETA ...] [--tope-zip-mb 100]

Es la comprobación rápida del Pull Request (segundos, sin descargar el zip); `puerta_releases.py` es la completa. Sale con 1 si algo no resuelve.
Si GitHub no responde (red o límite de uso) lo dice como error «reintentá»: no se da por buena una alta que no se pudo comprobar.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402


def comprobar(gh: C.GitHub, folder: str, meta: dict, tope_mb: int = C.TOPE_ZIP_MB) -> list:
    github, pid = str(meta.get("github", "")), str(meta.get("id", ""))
    try:
        if not gh.repo_existe(github):
            return [C.error("IX105", folder, f"el repo «{github}» no existe o es privado")]
        release = C.elegir_release(gh.releases(github), meta)
    except C.ApiError as exc:
        return [C.error("IX105", folder, f"no pude consultar GitHub: {exc} (reintentá más tarde)")]
    if release is None:
        que = f"el tag «{meta.get('release_fijo')}»" if meta.get("seguimiento") == "fijo" else "un Release estable (no borrador ni prerelease)"
        return [C.error("IX105", folder, f"el repo «{github}» no tiene {que}: publicá la versión antes de pedir el alta")]
    tag = str(release.get("tag_name", ""))
    asset = C.elegir_asset(release, pid)
    if asset is None:
        return [C.error("IX105", folder, f"el Release {tag} no tiene {pid}-{C.version_de_tag(tag)}.zip (ni un único .zip)")]
    if isinstance(asset.get("size"), int) and asset["size"] > tope_mb * 1024 * 1024:
        return [C.error("IX105", folder, f"{asset.get('name')} pesa {asset['size'] / 1e6:.0f} MB y el tope es {tope_mb} MB")]
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--raiz", default=C.ROOT)
    ap.add_argument("--solo", nargs="*", default=None, metavar="CARPETA")
    ap.add_argument("--tope-zip-mb", type=int, default=C.TOPE_ZIP_MB)
    ap.add_argument("--api", default=C.API_DEFAULT, help="solo para pruebas")
    ap.add_argument("--loopback", action="store_true", help="solo para pruebas: permite 127.0.0.1")
    args = ap.parse_args(argv)
    gh = C.GitHub(api=args.api, loopback=args.loopback)
    todos = []
    folders = C.carpetas(args.raiz, args.solo)
    for folder in folders:
        meta, falla = C.leer_meta(args.raiz, folder)
        todos.extend([C.error("IX101", folder, falla)] if falla else comprobar(gh, folder, meta, args.tope_zip_mb))
    C.mostrar(todos)
    print(f"{len(folders)} carpeta(s): {sum(1 for h in todos if h.severidad == 'error')} enlace(s) que no resuelven")
    return 1 if C.hay_errores(todos) else 0


if __name__ == "__main__":
    sys.exit(main())
