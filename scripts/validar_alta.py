#!/usr/bin/env python3
"""Revisa las carpetas `paquetes/<Autor>@<id>/` **sin red** (IX101, IX102, IX103, IX106, IX107, IX108). Sale con 1 si hay algún error.

    python scripts/validar_alta.py [--raiz .] [--solo CARPETA ...] [--bloqueados bloqueados.json]

Lo corre el CI de cada Pull Request (`validate-pr.yml`) sobre las carpetas que cambió y el bot de `build-index.yml` sobre todas.
Un archivo que no es de la lista (`meta.json`, `descripcion.md`, `portada.png|jpg`) es un error: el índice nunca copia nada más.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

from afm import bloqueos as B  # noqa: E402
from afm import indice as IX  # noqa: E402
from afm import paquete as Q  # noqa: E402

DESCRIPCION_MAX = 20_000                       # caracteres de `descripcion.md`


def _esquema():
    import jsonschema
    return jsonschema.Draft202012Validator(IX.schema("meta"))


def _portada(path: str, folder: str) -> list:
    out = []
    size = os.path.getsize(path)
    if size > C.TOPE_PORTADA_BYTES:
        return [C.error("IX107", folder, f"la portada pesa {size / 1e6:.1f} MB y el tope es {C.TOPE_PORTADA_BYTES // (1024 * 1024)} MB")]
    with open(path, "rb") as f:
        cabecera = f.read(8)
    ext = os.path.splitext(path)[1].lower()
    if (ext == ".png" and cabecera != b"\x89PNG\r\n\x1a\n") or (ext == ".jpg" and cabecera[:3] != b"\xff\xd8\xff"):
        return [C.error("IX107", folder, f"{os.path.basename(path)} no es una imagen {ext[1:].upper()} (la cabecera no coincide con la extensión)")]
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = C.TOPE_PORTADA_PX * C.TOPE_PORTADA_PX
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            ancho, alto = im.size
    except Exception as exc:                    # Pillow lanza de todo con un archivo roto o con una bomba de descompresión
        return [C.error("IX107", folder, f"la portada no es una imagen legible: {exc}")]
    if max(ancho, alto) > C.TOPE_PORTADA_PX:
        out.append(C.error("IX107", folder, f"la portada mide {ancho}×{alto}; el lado máximo es {C.TOPE_PORTADA_PX} px"))
    elif (ancho, alto) != (1280, 720):
        out.append(C.aviso("IX107", folder, f"la portada mide {ancho}×{alto}; se espera 1280×720 (se publica como miniatura igual)"))
    return out


def revisar_carpeta(raiz: str, folder: str, bloqueados=(), validador=None) -> list:
    """Todos los hallazgos de una carpeta del alta. No toca el disco ni la red."""
    base = os.path.join(raiz, C.PAQUETES, folder)
    out: list = []
    # IX103: solo los cuatro archivos, sin subcarpetas ni enlaces simbólicos
    nombres = sorted(os.listdir(base))
    for n in nombres:
        p = os.path.join(base, n)
        if os.path.islink(p):
            out.append(C.error("IX103", folder, f"«{n}» es un enlace simbólico"))
        elif os.path.isdir(p) or n not in C.ARCHIVOS_ALTA:
            out.append(C.error("IX103", folder, f"«{n}» no va en el alta (solo meta.json, descripcion.md y portada.png|jpg)"))
    if "portada.png" in nombres and "portada.jpg" in nombres:
        out.append(C.error("IX103", folder, "hay portada.png y portada.jpg: dejá una sola"))
    m = C.FOLDER_RE.match(folder)
    if not m:
        out.append(C.error("IX102", folder, "el nombre de la carpeta tiene que ser <Autor>@<id> (id en minúsculas, números y guiones)"))
    # IX101 / IX108: meta.json
    meta, falla = C.leer_meta(raiz, folder)
    if falla:
        out.append(C.error("IX101", folder, falla))
    else:
        if "fuente" in meta:
            out.append(C.error("IX108", folder, "este índice solo lista paquetes de GitHub Releases: sacá `fuente` y usá `github`"))
        validador = validador or _esquema()
        for e in sorted(validador.iter_errors(meta), key=lambda e: list(e.path))[:6]:
            donde = "/".join(str(p) for p in e.path) or "(raíz)"
            if "fuente" in meta and "'github' is a required property" in e.message:
                continue                                                # ya salió como IX108
            out.append(C.error("IX101", folder, f"meta.json {donde}: {e.message[:160]}"))
        cats = meta.get("categorias")
        if isinstance(cats, list):
            fuera = [c for c in cats if c not in C.CATEGORIAS]
            if fuera:
                out.append(C.error("IX101", folder, f"categorías fuera de la lista: {', '.join(map(str, fuera))} (válidas: {', '.join(C.CATEGORIAS)})"))
        lic = meta.get("licencia")
        if isinstance(lic, str) and lic not in Q.LICENSES and lic != Q.ALL_RIGHTS_RESERVED:
            out.append(C.error("IX101", folder, f"licencia «{lic}» desconocida (SPDX de la lista de afm o «{Q.ALL_RIGHTS_RESERVED}»)"))
        if meta.get("seguimiento") == "fijo" and not meta.get("release_fijo"):
            out.append(C.error("IX101", folder, "seguimiento «fijo» pide `release_fijo` (el tag que se sigue)"))
        # IX102: el nombre de la carpeta es lo que dice el meta
        if m:
            if meta.get("id") != m.group(2):
                out.append(C.error("IX102", folder, f"la carpeta termina en @{m.group(2)} pero meta.json dice id «{meta.get('id')}»"))
            autor = str(meta.get("autor", ""))
            if autor.casefold() != m.group(1).casefold():
                out.append(C.error("IX102", folder, f"la carpeta empieza con «{m.group(1)}» pero meta.json dice autor «{autor}»"))
            gh = str(meta.get("github", ""))
            if gh and C.duenio(gh).casefold() != m.group(1).casefold():
                out.append(C.error("IX102", folder, f"el autor «{m.group(1)}» no es el dueño del repo «{gh}»: el alta la pide quien publica el repo"))
    # IX101: descripcion.md
    desc = os.path.join(base, "descripcion.md")
    if not os.path.isfile(desc):
        out.append(C.error("IX101", folder, "falta descripcion.md"))
    else:
        try:
            texto = open(desc, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError) as exc:
            out.append(C.error("IX101", folder, f"descripcion.md no se puede leer como UTF-8: {exc}"))
        else:
            if not texto.strip():
                out.append(C.error("IX101", folder, "descripcion.md está vacío"))
            elif len(texto) > DESCRIPCION_MAX:
                out.append(C.error("IX101", folder, f"descripcion.md tiene {len(texto)} caracteres y el tope es {DESCRIPCION_MAX}"))
    # IX107: portada (opcional)
    portadas = [n for n in ("portada.png", "portada.jpg") if n in nombres and not os.path.islink(os.path.join(base, n))]
    if portadas:
        out.extend(_portada(os.path.join(base, portadas[0]), folder))
    elif not any(h.codigo == "IX103" and "portada" in h.mensaje for h in out):
        out.append(C.aviso("IX107", folder, "sin portada: el paquete se lista sin imagen"))
    # IX106: bloqueados
    if m and bloqueados:
        pid, autor = m.group(2), (meta or {}).get("autor") or m.group(1)
        for h in B.comprobar_bloqueados([{"folder": folder, "id": pid, "autor": autor}], bloqueados, modo="alta"):
            out.append(C.error("IX106", folder, h.mensaje))
    return out


def revisar(raiz: str, solo=None, bloqueados=()) -> list:
    validador = _esquema()
    out: list = []
    for folder in C.carpetas(raiz, solo):
        out.extend(revisar_carpeta(raiz, folder, bloqueados, validador))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--raiz", default=C.ROOT)
    ap.add_argument("--solo", nargs="*", default=None, metavar="CARPETA", help="solo estas carpetas de paquetes/")
    ap.add_argument("--bloqueados", default=None, metavar="ARCHIVO", help="por defecto <raiz>/bloqueados.json")
    args = ap.parse_args(argv)
    try:
        bloqueados = B.leer_bloqueados(args.bloqueados or os.path.join(args.raiz, "bloqueados.json"))
    except B.BloqueosError as exc:
        print(f"IX106 error: bloqueados.json: {exc}", file=sys.stderr)
        return 1
    hallazgos = revisar(args.raiz, args.solo, bloqueados)
    C.mostrar(hallazgos)
    n = len(C.carpetas(args.raiz, args.solo))
    errores = sum(1 for h in hallazgos if h.severidad == "error")
    print(f"{n} carpeta(s) revisadas: {errores} error(es), {len(hallazgos) - errores} aviso(s)")
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
