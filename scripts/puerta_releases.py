#!/usr/bin/env python3
"""La puerta de releases: baja el `.zip` del Release que sigue cada alta y lo valida como lo haría `afm catalogo validar --estricto`.

    python scripts/puerta_releases.py [--raiz .] [--solo CARPETA ...] [--tope-zip-mb 100]

Un paquete pasa si: el Release existe y tiene su `.zip` (IX105); pesa hasta el tope; no trae `.fbx` (`allow_fbx=False`); pasa la validación
estricta de `afm`; y su `paquete.json` coincide con el alta en lo que no puede cambiar (`id`, `github`, `licencia`, `origen`, `linea`:
error; texto de listado: aviso IX104). El zip se baja a una carpeta temporal y se borra; nunca se extrae a disco.
Sale con 1 si algún paquete no pasa. `construir_indice.py` usa `evaluar()` de este módulo.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

from afm import validar as V  # noqa: E402
from afm.ziplector import ZipError  # noqa: E402


@dataclass
class Resultado:
    folder: str
    ok: bool = False
    hallazgos: list = field(default_factory=list)
    zip_path: str = ""                    # el zip descargado (vive en `tmpdir`); solo si pasó
    sha256: str = ""
    tamano: int = 0
    paquete: object = None                # `afm.paquete.Paquete` del zip
    release: dict = field(default_factory=dict)
    asset: dict = field(default_factory=dict)
    inaccesible: bool = False             # True = no se pudo ni preguntar (red/API): no es culpa del paquete


def comparar_con_alta(folder: str, meta: dict, pkg, tag: str) -> list:
    """IX104: lo que dice el zip contra lo que pidió el alta. Lo que identifica al paquete (id, repo) y lo que promete (licencia, origen, línea)
    son errores; el texto de listado, un aviso (el listado se arma desde el zip)."""
    out = []
    ver = C.version_de_tag(tag)
    if pkg.id != meta.get("id"):
        out.append(C.error("IX104", folder, f"el zip es del paquete «{pkg.id}» y el alta dice «{meta.get('id')}»"))
    if (pkg.github or "").casefold() != str(meta.get("github", "")).casefold():
        out.append(C.error("IX104", folder, f"el zip declara github «{pkg.github or '(ninguno)'}» y el alta «{meta.get('github')}»"))
    if ver and pkg.version != ver:
        out.append(C.error("IX104", folder, f"la versión del zip ({pkg.version}) no es la del Release ({tag})"))
    for campo in ("licencia", "origen"):
        if getattr(pkg, campo) != meta.get(campo):
            out.append(C.error("IX104", folder, f"{campo}: el zip dice «{getattr(pkg, campo)}» y el alta «{meta.get(campo)}»"))
    linea_zip = (getattr(pkg, "linea", None) or {})
    if isinstance(meta.get("linea"), dict) and meta["linea"] != linea_zip:
        out.append(C.error("IX104", folder, "linea: el zip y el alta no coinciden"))
    for campo in ("nombre", "descripcion", "tipo"):
        if campo in meta and getattr(pkg, campo, None) != meta[campo]:
            out.append(C.aviso("IX104", folder, f"{campo} del alta difiere del paquete.json del zip (se usa el del zip)"))
    return out


def evaluar(gh: C.GitHub, folder: str, meta: dict, tmpdir: str, *, tope_mb: int = C.TOPE_ZIP_MB) -> Resultado:
    """Resuelve el Release, baja el zip y lo valida. No lanza: todo sale en `Resultado.hallazgos`."""
    res = Resultado(folder)
    github, pid = str(meta.get("github", "")), str(meta.get("id", ""))
    try:
        if not gh.repo_existe(github):
            res.hallazgos.append(C.error("IX105", folder, f"el repo «{github}» no existe o es privado"))
            return res
        release = C.elegir_release(gh.releases(github), meta)
    except C.ApiError as exc:
        res.hallazgos.append(C.error("IX105", folder, f"no pude consultar GitHub: {exc} (reintentá más tarde)"))
        res.inaccesible = True
        return res
    if release is None:
        que = f"el tag «{meta.get('release_fijo')}»" if meta.get("seguimiento") == "fijo" else "un Release estable (no borrador ni prerelease)"
        res.hallazgos.append(C.error("IX105", folder, f"el repo «{github}» no tiene {que}"))
        return res
    res.release = release
    asset = C.elegir_asset(release, pid)
    tag = str(release.get("tag_name", ""))
    if asset is None:
        res.hallazgos.append(C.error("IX105", folder, f"el Release {tag} no tiene {pid}-{C.version_de_tag(tag)}.zip (ni un único .zip)"))
        return res
    res.asset = asset
    tope = tope_mb * 1024 * 1024
    if isinstance(asset.get("size"), int) and asset["size"] > tope:
        res.hallazgos.append(C.error("IX105", folder, f"{asset.get('name')} pesa {asset['size'] / 1e6:.0f} MB y el tope es {tope_mb} MB"))
        return res
    destino = os.path.join(tmpdir, f"{folder}.zip")
    try:
        res.sha256, res.tamano = gh.descargar(str(asset.get("browser_download_url", "")), destino, tope_bytes=tope)
    except C.ApiError as exc:
        res.hallazgos.append(C.error("IX105", folder, f"no pude bajar {asset.get('name')}: {exc}"))
        res.inaccesible = exc.status == 0 and "tope" not in str(exc) and "lista de hosts" not in str(exc)
        return res
    try:
        val = V.validate_package(destino, estricto=True, allow_fbx=False)
    except ZipError as exc:
        res.hallazgos.append(C.error("IX105", folder, f"{asset.get('name')} no es un paquete válido: {exc}"))
        return res
    for f in val.findings:
        res.hallazgos.append(C.Hallazgo("IX105", "error", folder, f"el zip no pasa la validación estricta: {f.code or '----'} {f.path}: {f.message}"))
    if val.paquete is None:
        return res
    res.paquete = val.paquete
    res.hallazgos.extend(comparar_con_alta(folder, meta, val.paquete, tag))
    res.ok = not C.hay_errores(res.hallazgos)
    if res.ok:
        res.zip_path = destino
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--raiz", default=C.ROOT)
    ap.add_argument("--solo", nargs="*", default=None, metavar="CARPETA")
    ap.add_argument("--tope-zip-mb", type=int, default=C.TOPE_ZIP_MB)
    ap.add_argument("--api", default=C.API_DEFAULT, help="solo para pruebas")
    ap.add_argument("--loopback", action="store_true", help="solo para pruebas: permite 127.0.0.1")
    args = ap.parse_args(argv)
    gh = C.GitHub(api=args.api, loopback=args.loopback)
    todos, malos = [], 0
    with tempfile.TemporaryDirectory(prefix="afm-puerta-") as tmp:
        for folder in C.carpetas(args.raiz, args.solo):
            meta, falla = C.leer_meta(args.raiz, folder)
            if falla:
                todos.append(C.error("IX101", folder, falla))
                malos += 1
                continue
            r = evaluar(gh, folder, meta, tmp, tope_mb=args.tope_zip_mb)
            todos.extend(r.hallazgos)
            malos += 0 if r.ok else 1
            if r.ok:
                print(f"{folder}: ok ({r.paquete.version}, {r.tamano / 1e6:.1f} MB, sha256 {r.sha256[:12]}…)")
    C.mostrar(todos)
    print(f"{len(C.carpetas(args.raiz, args.solo))} paquete(s) revisados: {malos} no pasan la puerta")
    return 1 if malos else 0


if __name__ == "__main__":
    sys.exit(main())
