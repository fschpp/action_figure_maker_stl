"""Piezas por referencia (plan de catálogos en línea, §5.11 y D26).

Un paquete `origen: referencia` trae solo la ficha de la pieza (planos, puertos, pasadores, roles) y **no** su STL: el archivo lo aporta la
persona. `paquete.json` lo declara en `referencias_requeridas` (`id`, nombre, `sha256` aceptados, `tamano_max`) y el `sha256` de la ficha de
la pieza es el del archivo que la completa. Este módulo hace el resto:

* `importar(cat, ruta)` verifica el tamaño y el `sha256` del archivo contra lo que piden los paquetes instalados y lo guarda en la carpeta de
  estado local `<catálogo>/referencias/<sha256>.stl` (**nunca** dentro de un paquete: `exportar` no la mira y el lint rechaza el archivo
  dentro de un zip, CK304). Si el archivo no es ninguno de los pedidos no se guarda nada.
* `estado(cat)` dice, por cada archivo requerido, si ya está y qué piezas completa o le faltan (`piezas_incompletas`).
* Mientras falte, la pieza está «incompleta»: `Catalog.load` lanza `PieceIncompleteError` (no se arma) y `Catalog.verify` lo avisa sin romper.
* `quitar(cat, id)` borra el archivo importado (las piezas vuelven a quedar incompletas).

Módulo de motor puro: sin GTK, sin red, sin `manifold3d`.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field

from . import parts as P
from .catalog import REFERENCES_DIR, Catalog, CatalogError


class ReferenceError(CatalogError):
    """El archivo no sirve como archivo requerido (no es el pedido, pesa de más, no se puede leer…); el mensaje dice por qué."""


@dataclass
class Required:
    """Un archivo que piden los paquetes instalados (un `referencias_requeridas` ya registrado)."""

    id: str
    nombre: str
    paquete: str
    sha256: list
    tamano_max: int
    piezas: list = field(default_factory=list)          # `id@n` de las piezas que este archivo completa
    importado: bool = False                             # ¿está en la carpeta de estado local?

    @property
    def pendientes(self) -> list:
        return [] if self.importado else list(self.piezas)

    def describe(self) -> str:
        used = ", ".join(self.piezas) if self.piezas else "ninguna pieza instalada todavía"
        mark = "importado" if self.importado else "FALTA"
        return f"{self.id} — {self.nombre} [{mark}] (paquete {self.paquete}; completa: {used}; hasta {self.tamano_max / 1e6:.1f} MB)"


@dataclass
class ImportResult:
    requerido: Required
    sha256: str
    ruta: str                                           # dónde quedó, dentro de `referencias/`
    completadas: list = field(default_factory=list)     # piezas que pasaron a estar completas con este archivo
    ya_estaba: bool = False
    avisos: list = field(default_factory=list)


def _registry_entries(cat: Catalog) -> list:
    """`(id del paquete, referencias)` de `paquetes.json`, en orden estable."""
    import json

    try:
        with open(os.path.join(cat.root, "paquetes.json"), encoding="utf-8") as f:
            reg = json.load(f)
        items = reg.get("paquetes", {}).items()
    except (OSError, ValueError, AttributeError):
        return []
    return [(pid, e.get("referencias") or []) for pid, e in sorted(items) if isinstance(e, dict)]


def estado(cat: Catalog) -> list[Required]:
    """Todos los archivos requeridos por los paquetes instalados, con su estado. Vacío si ningún paquete es «por referencia»."""
    metas = cat._metas()
    out: list[Required] = []
    for pid, refs in _registry_entries(cat):
        for r in refs:
            if not (isinstance(r, dict) and isinstance(r.get("sha256"), list)):
                continue
            hashes = [h for h in r["sha256"] if isinstance(h, str)]
            req = Required(id=str(r.get("id", "")), nombre=str(r.get("nombre", "")), paquete=pid, sha256=hashes,
                           tamano_max=int(r.get("tamano_max") or 0))
            req.piezas = sorted(m.ref for m in metas if m.sha256 in hashes)
            req.importado = any(os.path.isfile(cat.reference_state_path(h)) for h in hashes)
            out.append(req)
    return out


def piezas_incompletas(cat: Catalog) -> list[tuple[str, Required]]:
    """`(id@n, archivo requerido)` de cada pieza a la que todavía le falta su archivo."""
    out = []
    declared = cat.declared_references()
    by_hash = {h: r for r in estado(cat) for h in r.sha256}
    for m in cat._metas():
        if cat.incomplete_info(m, declared) is not None and m.sha256 in by_hash:
            out.append((m.ref, by_hash[m.sha256]))
    return out


def _find(reqs: list[Required], ref_id: str) -> Required:
    hits = [r for r in reqs if r.id == ref_id]
    if not hits:
        known = ", ".join(sorted({r.id for r in reqs})) or "ninguno"
        raise ReferenceError(f"ningún paquete instalado pide un archivo «{ref_id}» (se piden: {known})")
    return hits[0]


def importar(cat: Catalog, ruta: str, ref_id: str = "") -> ImportResult:
    """Verifica `ruta` y la guarda como archivo requerido. `ref_id` (opcional) obliga a que sea ese archivo y no otro.

    Orden de las comprobaciones (la primera que falla corta, y **no se escribe nada**): existe y es un archivo → pesa a lo sumo lo que pide algún
    archivo pendiente → su `sha256` es uno de los aceptados. Un archivo que ya estaba importado no se reescribe (`ya_estaba`)."""
    reqs = estado(cat)
    if not reqs:
        raise ReferenceError("ningún paquete instalado pide archivos requeridos")
    wanted = [_find(reqs, ref_id)] if ref_id else reqs
    if not os.path.isfile(ruta):
        raise ReferenceError(f"«{ruta}» no es un archivo")
    size = os.path.getsize(ruta)
    cap = max(r.tamano_max for r in wanted)
    if size > cap:
        raise ReferenceError(f"«{os.path.basename(ruta)}» pesa {size / 1e6:.1f} MB y lo más que se acepta es {cap / 1e6:.1f} MB: no es el archivo pedido")
    digest = P.sha256_file(ruta)
    match = next((r for r in wanted if digest in r.sha256), None)
    if match is None:
        names = ", ".join(f"«{r.nombre}»" for r in wanted)
        raise ReferenceError(f"«{os.path.basename(ruta)}» no es ninguno de los archivos que se piden ({names}): el sha256 {digest[:12]}… no coincide con "
                             "el que declara el paquete (¿otra versión del modelo, o un archivo modificado?)")
    if size > match.tamano_max:
        raise ReferenceError(f"«{os.path.basename(ruta)}» pesa {size / 1e6:.1f} MB y «{match.nombre}» admite hasta {match.tamano_max / 1e6:.1f} MB")
    before = {ref for ref, _r in piezas_incompletas(cat)}
    dest = cat.reference_state_path(digest)
    res = ImportResult(requerido=match, sha256=digest, ruta=dest)
    if os.path.isfile(dest) and P.sha256_file(dest) == digest:
        res.ya_estaba = True
    else:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".importando-", dir=os.path.dirname(dest))
        try:
            with os.fdopen(fd, "wb") as out, open(ruta, "rb") as src:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    out.write(chunk)
            if P.sha256_file(tmp) != digest:                                  # el archivo cambió mientras se copiaba
                raise ReferenceError(f"«{os.path.basename(ruta)}» cambió mientras se importaba; volvé a intentarlo")
            os.replace(tmp, dest)
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
    res.completadas = sorted(before - {ref for ref, _r in piezas_incompletas(cat)})
    if not any(m.sha256 == digest for m in cat._metas()):                     # el archivo es de los aceptados pero ninguna ficha instalada lo usa
        res.avisos.append(f"«{match.nombre}» no completa ninguna pieza instalada (ninguna ficha tiene el sha256 {digest[:12]}…)")
    return res


def quitar(cat: Catalog, ref_id: str) -> list[str]:
    """Borra el archivo importado de `ref_id`; devuelve las piezas que vuelven a quedar incompletas. `ReferenceError` si no estaba importado."""
    req = _find(estado(cat), ref_id)
    gone = [h for h in req.sha256 if os.path.isfile(cat.reference_state_path(h))]
    if not gone:
        raise ReferenceError(f"«{ref_id}» no está importado")
    for h in gone:
        os.remove(cat.reference_state_path(h))
    try:
        os.rmdir(os.path.join(cat.root, REFERENCES_DIR))                       # solo si quedó vacía
    except OSError:
        pass
    return sorted(req.piezas)


def lines(cat: Catalog) -> list[str]:
    """El estado en líneas de texto (`afm catalogo referencia listar` y el panel de la ventana)."""
    reqs = estado(cat)
    if not reqs:
        return ["Ningún paquete instalado pide archivos requeridos."]
    out = [r.describe() for r in reqs]
    missing = [r for r in reqs if not r.importado]
    out.append("Todo completo." if not missing else f"Faltan {len(missing)} archivo(s): las piezas incompletas no se arman hasta importarlos.")
    return out


__all__ = ["ReferenceError", "Required", "ImportResult", "estado", "piezas_incompletas", "importar", "quitar", "lines"]
