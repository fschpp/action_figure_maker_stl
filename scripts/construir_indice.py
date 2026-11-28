#!/usr/bin/env python3
"""Arma `data/` (lo que publica GitHub Pages): `index.json`, `fichas/` y `paquetes/<Autor>@<id>/{portada.png,descripcion.md}`.

    python scripts/construir_indice.py [--releases] [--salida data] [--previo previo.json] [--tope-zip-mb 100] [--api URL --loopback]

Sin `--releases` arma las fichas desde los `meta.json` (sin red; sirve para ver el sitio): las entradas no tienen `latest`, no se pueden instalar.
Con `--releases` (lo que corre el bot) pasa cada alta por la puerta de `puerta_releases.py` y lista su última versión instalable.

Reglas (PLAN_catalogos_online.md §5.5, D12):
- Un alta con errores (IX101–IX103, IX106–IX108) **no se lista**. Lo bloqueado se retira (`bloqueados.json`, conserva la clave `bloqueados`).
- Si la puerta falla, la entrada **conserva su última versión buena** (la de `--previo`) con `update_check: "error: …"`; sin versión previa no se lista.
- `--previo` es el `index.json` ya publicado: de ahí salen esa versión buena y la lista `versiones` (las anteriores, para volver atrás).
- Salida 0 aunque un paquete falle (eso se ve en el log y en `update_check`); salida 2 si la corrida no es confiable: `bloqueados.json` roto,
  el índice resultante no cumple su esquema, o GitHub no respondió y quedaría el índice vacío. Con salida 2 el workflow no despliega nada.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402
import puerta_releases as PR  # noqa: E402
import validar_alta as VA  # noqa: E402

from afm import bloqueos as B  # noqa: E402
from afm import indice as IX  # noqa: E402
from afm import remoto as R  # noqa: E402

VERSIONES_MAX = 10                       # cuántas versiones anteriores se recuerdan en `versiones`


def _portada_publicada(path: str) -> bytes:
    """La portada reencodada: miniatura PNG sin metadatos (EXIF, comentarios, perfiles) — lo único de una portada que se publica."""
    from PIL import Image
    with Image.open(path) as im:
        im.load()
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        im.thumbnail(C.PORTADA_PUBLICADA_PX)
        limpia = Image.frombytes(im.mode, im.size, im.tobytes())          # píxeles nada más: sin EXIF ni comentarios
        buf = io.BytesIO()
        limpia.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def _previas(previo: dict | None) -> dict:
    if not isinstance(previo, dict):
        return {}
    return {e["folder"]: e for e in previo.get("paquetes", []) if isinstance(e, dict) and isinstance(e.get("folder"), str)}


def _versiones(nuevo: dict, prev: dict | None, bloqueados) -> list:
    """`versiones` de la entrada nueva: la última de antes (si ya no es la última) + las que ya recordaba, sin repetir ni pasar del tope."""
    if not prev:
        return []
    candidatas = ([prev["latest"]] if isinstance(prev.get("latest"), dict) else []) + [r for r in (prev.get("versiones") or []) if isinstance(r, dict)]
    vistas, out = {nuevo["latest"]["version"]}, []
    for r in candidatas:
        v = r.get("version")
        if v in vistas or not isinstance(r.get("zip"), dict):
            continue
        vistas.add(v)
        out.append(r)
    return out[:VERSIONES_MAX]


def _entrada_sin_release(folder: str, meta: dict, tiene_portada: bool) -> dict:
    e = {"folder": folder, **meta}
    if tiene_portada:
        e["portada"] = f"paquetes/{folder}/portada.png"
    e["descripcion_url"] = f"paquetes/{folder}/descripcion.md"
    e.update({"versiones": [], "retiradas": [], "update_check": "off", "descargas": None, "piezas": []})
    return e


def construir(raiz: str, salida: str, *, releases: bool, gh=None, previo: dict | None = None, bloqueados=(), tope_mb: int = C.TOPE_ZIP_MB,
              generado: str | None = None):
    """Devuelve `(indice, hallazgos, fatal)`. Escribe `salida` solo si no hay `fatal`."""
    hallazgos: list = []
    previas = _previas(previo)
    entradas, staging, inaccesibles = [], {}, 0
    validador = VA._esquema()
    with tempfile.TemporaryDirectory(prefix="afm-indice-") as tmp:
        for folder in C.carpetas(raiz):
            h = VA.revisar_carpeta(raiz, folder, bloqueados, validador)
            hallazgos.extend(h)
            if C.hay_errores(h):
                continue                                                 # no pasó el alta: no se lista
            meta, _ = C.leer_meta(raiz, folder)
            base = os.path.join(raiz, C.PAQUETES, folder)
            portada = next((os.path.join(base, n) for n in ("portada.png", "portada.jpg") if os.path.isfile(os.path.join(base, n))), "")
            archivos = {"descripcion": open(os.path.join(base, "descripcion.md"), encoding="utf-8").read(),
                        "portada": _portada_publicada(portada) if portada else None, "fichas": None}
            prev = previas.get(folder)
            if not releases:
                entradas.append(_entrada_sin_release(folder, meta, bool(portada)))
                staging[folder] = archivos
                continue
            r = PR.evaluar(gh, folder, meta, tmp, tope_mb=tope_mb)
            hallazgos.extend(r.hallazgos)
            if r.ok:
                tag = str(r.release.get("tag_name", ""))
                try:
                    entrada = IX.entry_from_zip(
                        r.zip_path, url=str(r.asset.get("browser_download_url", "")), autor=str(meta["autor"]), tag=tag,
                        prerelease=bool(r.release.get("prerelease", False)), publicado=str(r.release.get("published_at") or ""),
                        zip_nombre=str(r.asset.get("name", "")), portada=f"paquetes/{folder}/portada.png" if portada else "",
                        descripcion_url=f"paquetes/{folder}/descripcion.md", fichas=f"fichas/{IX.ficha_name(folder, r.paquete.version)}",
                        seguimiento=str(meta.get("seguimiento", "ultimo")), release_fijo=str(meta.get("release_fijo", "")), update_check="ok")
                    if entrada["folder"] != folder:
                        raise IX.IndiceError(f"el zip da la carpeta «{entrada['folder']}» y el alta es «{folder}»")
                    archivos["fichas"] = (IX.ficha_name(folder, r.paquete.version), IX.fichas_from_zip(r.zip_path))
                except (IX.IndiceError, OSError, ValueError) as exc:
                    hallazgos.append(C.error("IX104", folder, f"no se pudo armar la entrada: {exc}"))
                    r.ok = False
                else:
                    entrada["versiones"] = _versiones(entrada, prev, bloqueados)
                    entradas.append(entrada)
                    staging[folder] = archivos
                    continue
            if r.inaccesible:
                inaccesibles += 1
            if prev and isinstance(prev.get("latest"), dict):                 # conserva su última versión buena
                viejo = copy.deepcopy(prev)
                motivo = next((str(x.mensaje) for x in r.hallazgos if x.severidad == "error"), "la puerta no la dejó pasar")
                viejo["update_check"] = "inaccesible" if r.inaccesible else "error: " + motivo[:200]
                entradas.append(viejo)
                staging[folder] = archivos
                hallazgos.append(C.aviso("IX105", folder, f"se conserva la versión {viejo['latest'].get('version', '?')} ({viejo['update_check']})"))
            else:
                hallazgos.append(C.error("IX105", folder, "no tiene una versión que pase la puerta y no hay una anterior: no se lista"))
    indice = IX.build_index(entradas, generado=generado, bloqueados=B.a_json(bloqueados))
    indice, quitados = B.retirar(indice, bloqueados)
    hallazgos.extend(C.Hallazgo(q.codigo, q.severidad, q.folder, q.mensaje) for q in quitados)
    if releases and inaccesibles and not indice["paquetes"]:
        return indice, hallazgos, "GitHub no respondió y el índice quedaría vacío: no se publica"
    import jsonschema
    errores = sorted(jsonschema.Draft202012Validator(IX.schema("indice")).iter_errors(indice), key=lambda e: list(e.path))
    if errores:
        e = errores[0]
        return indice, hallazgos, f"el index.json armado no cumple su esquema: {'/'.join(map(str, e.path)) or '(raíz)'}: {e.message[:200]}"
    prueba = R.parse_index(C.dump_json(indice))
    for a in prueba.avisos:
        hallazgos.append(C.aviso("IX101", "-", f"el cliente de afm avisa al leer el índice: {a}"))
    _escribir(salida, indice, staging)
    return indice, hallazgos, ""


def _escribir(salida: str, indice: dict, staging: dict) -> None:
    if os.path.isdir(salida):
        shutil.rmtree(salida)
    os.makedirs(os.path.join(salida, "fichas"))
    for e in indice["paquetes"]:
        folder, a = e["folder"], staging.get(e["folder"])
        if not a:
            continue
        dest = os.path.join(salida, "paquetes", folder)
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "descripcion.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(a["descripcion"])
        if a["portada"]:
            with open(os.path.join(dest, "portada.png"), "wb") as f:
                f.write(a["portada"])
        if a["fichas"]:
            nombre, fichas = a["fichas"]
            with open(os.path.join(salida, "fichas", nombre), "w", encoding="utf-8", newline="\n") as f:
                f.write(IX.dump_json(fichas))
    with open(os.path.join(salida, "index.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(C.dump_json(indice))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--raiz", default=C.ROOT)
    ap.add_argument("--salida", default=None, metavar="DIR", help="por defecto <raiz>/data")
    ap.add_argument("--releases", action="store_true", help="consultar los Releases y pasar cada paquete por la puerta (necesita red)")
    ap.add_argument("--previo", default=None, metavar="index.json", help="el index.json ya publicado (si no existe, se ignora)")
    ap.add_argument("--bloqueados", default=None, metavar="ARCHIVO")
    ap.add_argument("--tope-zip-mb", type=int, default=C.TOPE_ZIP_MB)
    ap.add_argument("--api", default=C.API_DEFAULT, help="solo para pruebas")
    ap.add_argument("--loopback", action="store_true", help="solo para pruebas: permite 127.0.0.1")
    args = ap.parse_args(argv)
    try:
        bloqueados = B.leer_bloqueados(args.bloqueados or os.path.join(args.raiz, "bloqueados.json"))
    except B.BloqueosError as exc:
        print(f"IX106 error: {exc}", file=sys.stderr)
        return 2
    previo = None
    if args.previo and os.path.isfile(args.previo):
        try:
            previo = C.leer_json(args.previo)
        except (OSError, ValueError) as exc:
            print(f"AVISO: no pude leer --previo ({exc}); se arma sin versión anterior", file=sys.stderr)
    gh = C.GitHub(api=args.api, loopback=args.loopback) if args.releases else None
    generado = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    salida = args.salida or os.path.join(args.raiz, "data")
    indice, hallazgos, fatal = construir(args.raiz, salida, releases=args.releases, gh=gh, previo=previo, bloqueados=bloqueados,
                                         tope_mb=args.tope_zip_mb, generado=generado)
    C.mostrar(hallazgos)
    if fatal:
        print(f"ERROR: {fatal}", file=sys.stderr)
        return 2
    errores = sum(1 for h in hallazgos if h.severidad == "error")
    print(f"index.json: {indice['cantidad']} paquete(s) listado(s) en {salida} ({errores} sin listar o con error, "
          f"{len(hallazgos) - errores} aviso(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
