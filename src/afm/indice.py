"""Fichas, `meta.json` y entradas del índice de catálogos en línea (plan §5.5 y D11).

Es el lado **productor** del formato que `remoto.py` lee: dado un paquete `.zip` arma, **sin abrir ningún STL**,

* el archivo de **fichas** (`afm-fichas-1`): las `.pieza.json` del paquete juntas, para que el cliente calcule la compatibilidad con su
  catálogo antes de descargar nada (`compat_remota.py`, D15);
* el **`meta.json`** de la carpeta `paquetes/<Autor>@<id>/` del índice (casi una copia de `paquete.json`; lo usa `paquete enviar`, D10);
* la **entrada de `index.json`** (`latest` con `sha256` y tamaño del zip, resumen de piezas, `update_check`, `descargas`) y el índice entero.

Los esquemas JSON (`src/afm/schemas/afm-{meta,indice,fichas}-1.schema.json`) son la fuente de verdad del CI del índice y del sitio; este
módulo solo los carga (`schema`). **Evolución aditiva** (§5.1 punto 8): solo se agregan claves; `schema_version` sube únicamente si se
rompe algo existente. Módulo de motor puro: sin red, sin GTK, sin `manifold3d` (no importa `parts`: lee las fichas como JSON).
"""
from __future__ import annotations

import json
import os
import posixpath
import re
from datetime import datetime, timezone

from . import paquete as Q
from . import ziplector as Z
from .catalog import CatalogError

INDEX_SCHEMA_VERSION = 1                 # la `schema_version` que este módulo escribe (y que `remoto.INDEX_SCHEMA_SUPPORTED` sabe leer)
FICHAS_FORMAT = "afm-fichas-1"
PIECE_FORMAT = "afm-pieza-1"
PIECE_SUFFIX = ".pieza.json"
MAX_FICHA_BYTES = 4 * 1024 * 1024        # [P] una ficha real (caras con ~100 vértices) pesa decenas de KB
MAX_FICHAS = 500                         # [P] proceres-127 completo: ≈ 60 piezas
FOLLOW = ("ultimo", "fijo")
SCHEMAS = {"meta": "afm-meta-1.schema.json", "indice": "afm-indice-1.schema.json", "fichas": "afm-fichas-1.schema.json"}
_AUTHOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_PIECE_PATH = re.compile(r"^piezas/[^/]+/[^/]+@[1-9]\d*\.pieza\.json$")


class IndiceError(CatalogError):
    """No se pudo armar la ficha, el `meta.json` o la entrada de índice de un paquete."""


# ---------------------------------------------------------------------------------------------------------------- esquemas

def schema(which: str) -> dict:
    """JSON Schema de `meta` (meta.json), `indice` (index.json) o `fichas`. `KeyError` con las opciones si no existe."""
    from importlib import resources

    if which not in SCHEMAS:
        raise KeyError(f"esquema «{which}» desconocido (hay {', '.join(SCHEMAS)})")
    return json.loads(resources.files("afm").joinpath("schemas").joinpath(SCHEMAS[which]).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------------------------- nombres

def author_of(pkg: Q.Paquete, autor: str = "") -> str:
    """Quien figura como `<Autor>` de la carpeta del índice: el dueño de `github` (o el primer autor con usuario de GitHub). `IndiceError` si no hay."""
    cand = autor or (pkg.github.split("/")[0] if pkg.github else "") or next((a.github for a in pkg.autores if a.github), "")
    if not cand or not _AUTHOR_RE.match(cand):
        raise IndiceError(f"«{pkg.id}» no dice de quién es: poné `github` («owner/repo») en paquete.json o indicá el autor del índice")
    return cand


def folder_name(pkg: Q.Paquete, autor: str = "") -> str:
    """`<Autor>@<id>`: lo listado y lo instalado se llaman igual (§5.5)."""
    return f"{author_of(pkg, autor)}@{pkg.id}"


def ficha_name(folder: str, version: str) -> str:
    """`<Autor>@<id>-<ver>.json`: el archivo de fichas de esa versión (va en `data/fichas/`)."""
    return f"{folder}-{version}.json"


# ---------------------------------------------------------------------------------------------------------------- fichas

def _read_package(zip_path: str) -> tuple[Q.Paquete, dict]:
    """(manifiesto, `{ruta: bytes}` de las fichas), tras la inspección endurecida del zip. No lee ningún STL."""
    with Z.open_zip(zip_path) as zf:
        insp = Z.inspect(zf, allow_fbx=True)         # el FBX no se lee acá; qué paquetes lo admiten lo decide `validar`
        if insp.findings:
            raise IndiceError("el zip no pasó la inspección: " + "; ".join(str(f) for f in insp.findings[:3]))
        names = {e.name for e in insp.entries}
        if Q.MANIFEST_FILE not in names:
            raise IndiceError(f"«{zip_path}» no trae {Q.MANIFEST_FILE}: no es un paquete afm-paquete-1")
        try:
            pkg = Q.Paquete.from_json(zf.read(Q.MANIFEST_FILE).decode("utf-8"))
        except (Q.PaqueteError, UnicodeDecodeError) as exc:
            raise IndiceError(f"{Q.MANIFEST_FILE} no se puede leer: {exc}") from None
        paths = sorted(n for n in names if _PIECE_PATH.match(n))
        if len(paths) > MAX_FICHAS:
            raise IndiceError(f"trae {len(paths)} fichas y el tope es {MAX_FICHAS}")
        raw = {}
        for n in paths:
            if zf.getinfo(n).file_size > MAX_FICHA_BYTES:
                raise IndiceError(f"{n}: la ficha pesa más de {MAX_FICHA_BYTES // 1024} KB")
            raw[n] = zf.read(n)
    return pkg, raw


def fichas_from_zip(zip_path: str) -> dict:
    """El archivo `afm-fichas-1` de un paquete: sus `.pieza.json` (sin mallas) en orden estable. Una colección (sin piezas) da `fichas: []`."""
    pkg, raw = _read_package(zip_path)
    fichas = []
    for name, data in raw.items():
        try:
            d = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise IndiceError(f"{name}: no es un JSON legible ({exc})") from None
        if not isinstance(d, dict) or d.get("formato") != PIECE_FORMAT:
            raise IndiceError(f"{name}: no es una ficha {PIECE_FORMAT} (formato {d.get('formato')!r} si es un objeto)" if isinstance(d, dict)
                              else f"{name}: no es una ficha {PIECE_FORMAT}")
        fichas.append(d)
    head = {"id": pkg.id, "version": pkg.version}
    if pkg.linea is not None:
        head["linea"] = dict(pkg.linea)
    return {"formato": FICHAS_FORMAT, "paquete": head, "fichas": fichas}


def dump_json(obj) -> str:
    """Texto estable (UTF-8 sin escapar, claves en el orden en que se armaron, salto final): el mismo dato da el mismo archivo."""
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def pieces_summary(fichas: dict) -> list:
    """Resumen para el índice: `[{ref, tipo, puertos: {nombre: {clase, rol, radio_eq_mm}}}]` (lo que «Explorar» muestra antes de bajar nada)."""
    out = []
    for d in fichas.get("fichas", []):
        puertos = {}
        for name, p in (d.get("puertos") or {}).items():
            if not isinstance(p, dict):
                continue
            cara = p.get("cara") if isinstance(p.get("cara"), dict) else {}
            r = cara.get("radio_eq_mm")
            puertos[name] = {"clase": p.get("clase", ""), "rol": p.get("rol", ""),
                             "radio_eq_mm": round(float(r), 3) if isinstance(r, (int, float)) and not isinstance(r, bool) else None}
        out.append({"ref": f"{d['id']}@{d['version']}", "tipo": d.get("tipo", ""), "puertos": puertos})
    return out


# ---------------------------------------------------------------------------------------------------------------- meta.json

def meta_from_paquete(pkg: Q.Paquete, *, autor: str = "", seguimiento: str = "ultimo", release_fijo: str = "") -> dict:
    """`meta.json` de `paquetes/<Autor>@<id>/` (§5.5): los campos de listado y los que avisan **antes** de bajar nada, copiados de `paquete.json`.

    Pide `github` (es lo que activa el seguimiento de versiones); sin él use `fuente` a mano en el `meta.json`. `seguimiento = "fijo"`
    exige `release_fijo` (el tag que se sigue).
    """
    if seguimiento not in FOLLOW:
        raise IndiceError(f"seguimiento «{seguimiento}» desconocido (hay {', '.join(FOLLOW)})")
    if seguimiento == "fijo" and not release_fijo:
        raise IndiceError("seguimiento «fijo» necesita release_fijo (el tag que se sigue)")
    if not pkg.github:
        raise IndiceError(f"«{pkg.id}» no tiene `github` en paquete.json: sin repo no hay seguimiento de versiones (`afm catalogo paquete github OWNER/REPO`)")
    d = {"id": pkg.id, "nombre": pkg.nombre, "autor": author_of(pkg, autor), "version": pkg.version, "categorias": list(pkg.categorias),
         "github": pkg.github, "descripcion": pkg.descripcion, "etiquetas": list(pkg.etiquetas), "licencia": pkg.licencia, "origen": pkg.origen,
         "tipo": pkg.tipo}
    if pkg.linea is not None:
        d["linea"] = dict(pkg.linea)
    if pkg.motor:
        d["motor"] = pkg.motor
    d["requiere"] = list(pkg.requiere)
    d["conflicts"] = list(pkg.conflicts)
    d["experimental"] = bool(pkg.experimental)
    if seguimiento == "fijo":
        d["seguimiento"], d["release_fijo"] = "fijo", release_fijo
    return d


# ---------------------------------------------------------------------------------------------------------------- entrada del índice

def release_block(zip_path: str, *, version: str, url: str, tag: str = "", prerelease: bool = False, publicado: str = "",
                  nombre: str = "", fichas: str = "") -> dict:
    """El bloque `latest` / un elemento de `versiones`: versión, tag y dónde está el zip con su `sha256` y tamaño reales."""
    from .parts import sha256_file       # import local: `parts` arrastra el motor y este módulo no lo necesita para el resto

    d = {"version": version, "tag": tag or f"v{version}", "prerelease": bool(prerelease), "publicado": publicado,
         "zip": {"nombre": nombre or posixpath.basename(url.split("?")[0]) or os.path.basename(zip_path), "url": url,
                 "tamano": os.path.getsize(zip_path), "sha256": sha256_file(zip_path)}}
    if fichas:
        d["fichas"] = fichas
    return d


def entry_from_zip(zip_path: str, *, url: str, autor: str = "", tag: str = "", prerelease: bool = False, publicado: str = "",
                   zip_nombre: str = "", portada: str = "", descripcion_url: str = "", fichas: str | None = None,
                   seguimiento: str = "ultimo", release_fijo: str = "", update_check: str = "ok", descargas: dict | None = None,
                   retiradas=(), versiones=()) -> dict:
    """La entrada de `index.json` de un paquete: `meta.json` + `portada`, `descripcion_url`, `latest`, `versiones`, `retiradas`, `update_check`,
    `descargas` y el resumen de piezas. No lee ningún STL. `fichas` = ruta (relativa al `index.json`) del archivo de fichas; por defecto
    `fichas/<Autor>@<id>-<ver>.json`, la convención que el cliente usa si falta.
    """
    pkg, _raw = _read_package(zip_path)
    meta = meta_from_paquete(pkg, autor=autor, seguimiento=seguimiento, release_fijo=release_fijo)
    folder = f"{meta['autor']}@{pkg.id}"
    ficha_path = fichas if fichas is not None else f"fichas/{ficha_name(folder, pkg.version)}"
    entry = {"folder": folder, **meta}
    if portada:
        entry["portada"] = portada
    if descripcion_url:
        entry["descripcion_url"] = descripcion_url
    entry["latest"] = release_block(zip_path, version=pkg.version, url=url, tag=tag, prerelease=prerelease, publicado=publicado,
                                    nombre=zip_nombre, fichas=ficha_path)
    entry["versiones"] = list(versiones)
    entry["retiradas"] = list(retiradas)
    entry["update_check"] = update_check
    entry["descargas"] = descargas              # `None` (no hay contador) y no 0 (§5.5)
    entry["piezas"] = pieces_summary(fichas_from_zip(zip_path))
    return entry


def build_index(entries, *, generado: str | None = None, bloqueados=None) -> dict:
    """`index.json` entero: encabezado (`schema_version`, `generado`, `cantidad`, `categorias` usadas) y las entradas en orden de carpeta.

    `bloqueados` (D16/D24, clave **aditiva**): lo que el índice se niega a listar, `[{id | autor | sha256, motivo, fecha}]`; el cliente lo usa para no
    instalar, ocultar y avisar de lo ya instalado. Sin lista, la clave no se escribe (un índice viejo y uno nuevo sin bloqueos son idénticos)."""
    items = sorted(entries, key=lambda e: e["folder"])
    cats = sorted({c for e in items for c in e.get("categorias", [])})
    out = {"schema_version": INDEX_SCHEMA_VERSION, "generado": generado or datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "cantidad": len(items), "categorias": cats, "paquetes": items}
    if bloqueados:
        out["bloqueados"] = [dict(b) for b in bloqueados]
    return out
