"""Bloqueo y retiro (plan de catálogos en línea, §5.8, IX106 y D24).

`bloqueados.json` es la lista de lo que el índice **se niega a listar**: cada línea bloquea por `id`, por `autor` o por `sha256` (exactamente uno de
los tres), con `motivo` y `fecha`. Tres usos, los tres sin red y sin tocar nada:

* **CI del índice** (el repo del índice reutiliza esto, D12): `leer_bloqueados` valida el archivo con rigor (el cliente, en cambio, lee con
  tolerancia: `remoto._blocks`); `comprobar_bloqueados(entradas, bloqueados, modo="alta")` rechaza una alta que coincida (**IX106**) y, con
  `modo="listado"`, avisa de lo ya listado; `retirar` saca del `index.json` lo bloqueado y deja la clave `bloqueados` para que los clientes avisen.
* **La app, en local**: `piezas_bloqueadas(raiz, bloqueados)` compara el `sha256` de **cada pieza instalada** con la lista (nada se envía a ningún lado),
  así avisa aunque el paquete se haya importado a mano o con otro nombre. `dependencias.audit` lo suma a sus alertas.
* **Qué significa cada `sha256`:** el de un `.zip` publicado (versión puntual) o el de un STL de pieza. Quien reclama una pieza concreta bloquea su
  STL; quien reclama un paquete entero bloquea su `id` o su autor.

Un bloqueo **nunca borra** lo instalado: avisa, y la persona decide (`docs/RECLAMOS.md`).
"""
from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass
from datetime import date

from .catalog import Catalog, CatalogError
from .remoto import Block

BLOCK_FILE = "bloqueados.json"
CODE = "IX106"
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class BloqueosError(CatalogError):
    """`bloqueados.json` ilegible o que no es una lista."""


@dataclass(frozen=True)
class Hallazgo:
    """Un resultado de `comprobar_bloqueados`, con el formato de los códigos del índice (`IX…`)."""

    codigo: str
    severidad: str                      # error | aviso
    folder: str
    mensaje: str

    def __str__(self) -> str:
        return f"{self.codigo} {self.severidad}: {self.folder}: {self.mensaje}"


# ------------------------------------------------------------------------------------------------------------ leer y validar

def _fecha_ok(f) -> bool:
    if not isinstance(f, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", f):
        return False
    try:
        date.fromisoformat(f)
    except ValueError:
        return False
    return True


def _items(obj):
    if isinstance(obj, dict):
        obj = obj.get("bloqueados")
    if not isinstance(obj, list):
        raise BloqueosError("«bloqueados.json» tiene que ser una lista (o un objeto con la clave «bloqueados»)")
    return obj


def validar_bloqueados(obj) -> tuple[list[Block], list[str]]:
    """`(bloqueos, problemas)`. A diferencia del cliente, el CI **no salta** líneas raras: cada problema se informa y el archivo no pasa.

    Cada línea: exactamente uno de `id` (`[a-z0-9]+(-…)*`), `autor` o `sha256` (64 hex en minúscula), más `motivo` (obligatorio, para que el reclamo
    quede explicado) y `fecha` (`AAAA-MM-DD` real). Una línea repetida también es un problema."""
    problemas, out, vistos = [], [], set()
    for n, b in enumerate(_items(obj), 1):
        if not isinstance(b, dict):
            problemas.append(f"línea {n}: no es un objeto")
            continue
        claves = [k for k in ("id", "autor", "sha256") if isinstance(b.get(k), str) and b[k].strip()]
        extra = sorted(set(b) - {"id", "autor", "sha256", "motivo", "fecha"})
        if len(claves) != 1:
            problemas.append(f"línea {n}: tiene que tener exactamente uno de id, autor o sha256")
            continue
        clave = claves[0]
        valor = b[clave].strip()
        if clave == "sha256" and not _SHA_RE.match(valor):
            problemas.append(f"línea {n}: el sha256 tiene que ser de 64 caracteres hexadecimales en minúscula")
            continue
        if clave == "id" and not _ID_RE.match(valor):
            problemas.append(f"línea {n}: el id «{valor}» no cumple el patrón [a-z0-9]+(-[a-z0-9]+)*")
            continue
        motivo = b.get("motivo")
        if not isinstance(motivo, str) or not motivo.strip():
            problemas.append(f"línea {n}: falta el «motivo» (el reclamo tiene que quedar explicado)")
            continue
        fecha = b.get("fecha")
        if not _fecha_ok(fecha):
            problemas.append(f"línea {n}: la «fecha» tiene que ser AAAA-MM-DD y existir")
            continue
        if extra:
            problemas.append(f"línea {n}: claves desconocidas: {', '.join(extra)}")
            continue
        k = (clave, valor.casefold() if clave == "autor" else valor)
        if k in vistos:
            problemas.append(f"línea {n}: repite un bloqueo ya listado ({clave} {valor[:16]})")
            continue
        vistos.add(k)
        out.append(Block(**{clave: valor}, motivo=motivo.strip(), fecha=fecha))
    return out, problemas


def leer_bloqueados(path: str) -> list[Block]:
    """Lee y valida `bloqueados.json`. Un archivo que no existe es **una lista vacía** (el índice todavía no bloqueó nada); uno con problemas
    es `BloqueosError` con todos los problemas juntos."""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BloqueosError(f"no se pudo leer {os.path.basename(path)}: {exc}") from exc
    blocks, problemas = validar_bloqueados(obj)
    if problemas:
        raise BloqueosError(f"{os.path.basename(path)} no es válido:\n  " + "\n  ".join(problemas))
    return blocks


def a_json(blocks) -> list[dict]:
    """`[{id|autor|sha256, motivo, fecha}]` listo para `indice.build_index(bloqueados=…)`."""
    out = []
    for b in blocks:
        clave = "id" if b.id else "autor" if b.autor else "sha256"
        out.append({clave: getattr(b, clave), "motivo": b.motivo, "fecha": b.fecha})
    return out


# ------------------------------------------------------------------------------------------------------------ el CI del índice

def _zips(entrada: dict) -> list[tuple[str, str]]:
    """`[(versión, sha256 del zip)]` de una entrada de `index.json`: la última y las `versiones`."""
    out = []
    for rel in ([entrada.get("latest")] if isinstance(entrada.get("latest"), dict) else []) + [r for r in (entrada.get("versiones") or []) if isinstance(r, dict)]:
        z = rel.get("zip") if isinstance(rel.get("zip"), dict) else {}
        if z.get("sha256"):
            out.append((str(rel.get("version", "")), z["sha256"]))
    return out


def _coincide(b: Block, entrada: dict) -> bool:
    if b.id:
        return entrada.get("id") == b.id
    if b.autor:
        return bool(entrada.get("autor")) and str(entrada["autor"]).casefold() == b.autor.casefold()
    return any(sha == b.sha256 for _, sha in _zips(entrada))


def comprobar_bloqueados(entradas, bloqueados, *, modo: str = "alta") -> list[Hallazgo]:
    """IX106 sobre entradas de índice (dicts de `indice.entry_from_zip`). `modo="alta"`: **error** por cada coincidencia (el alta se rechaza);
    `modo="listado"`: **error** también, pero el mensaje dice que hay que retirarla (`retirar`). Sin coincidencias, lista vacía."""
    if modo not in ("alta", "listado"):
        raise ValueError("modo tiene que ser «alta» o «listado»")
    out = []
    for e in entradas:
        folder = str(e.get("folder") or e.get("id") or "?")
        for b in bloqueados:
            if not _coincide(b, e):
                continue
            que = "se rechaza el alta" if modo == "alta" else "hay que retirarlo del índice"
            out.append(Hallazgo(CODE, "error", folder, f"{b.describe()}: {que}"))
    return out


def retirar(indice: dict, bloqueados) -> tuple[dict, list[Hallazgo]]:
    """`(índice sin lo bloqueado, qué se retiró)`. Un bloqueo por `id` o autor saca la entrada entera; uno por `sha256` saca esa versión de `versiones`
    y, si es la **última**, la entrada entera (hasta que sus responsables publiquen otra versión que no esté bloqueada). No modifica `indice`.
    Conserva la clave `bloqueados` (aditiva) para que los clientes avisen de lo ya instalado y recalcula `cantidad` y `categorias`."""
    nuevo = copy.deepcopy(indice)
    quitados, quedan = [], []
    for e in nuevo.get("paquetes", []):
        folder = str(e.get("folder") or e.get("id") or "?")
        entero = next((b for b in bloqueados if (b.id or b.autor) and _coincide(b, e)), None)
        if entero is not None:
            quitados.append(Hallazgo(CODE, "error", folder, f"retirado: {entero.describe()}"))
            continue
        por_sha = {b.sha256: b for b in bloqueados if b.sha256}
        ult = (e.get("latest") or {}).get("zip", {}).get("sha256") if isinstance(e.get("latest"), dict) else None
        if ult in por_sha:
            quitados.append(Hallazgo(CODE, "error", folder, f"retirado: su última versión está {por_sha[ult].describe()}"))
            continue
        versiones = [r for r in (e.get("versiones") or []) if not (isinstance(r, dict) and (r.get("zip") or {}).get("sha256") in por_sha)]
        if len(versiones) != len(e.get("versiones") or []):
            for r in e["versiones"]:
                s = (r.get("zip") or {}).get("sha256") if isinstance(r, dict) else None
                if s in por_sha:
                    quitados.append(Hallazgo(CODE, "aviso", folder, f"versión {r.get('version', '?')} retirada: {por_sha[s].describe()}"))
            e["versiones"] = versiones
        quedan.append(e)
    nuevo["paquetes"] = quedan
    nuevo["cantidad"] = len(quedan)
    nuevo["categorias"] = sorted({c for e in quedan for c in e.get("categorias", [])})
    if bloqueados:
        nuevo["bloqueados"] = a_json(bloqueados)
    return nuevo, quitados


# ------------------------------------------------------------------------------------------------------------ la app, en local

@dataclass(frozen=True)
class PiezaBloqueada:
    ref: str                            # id@n
    paquete: str                        # id del paquete que la trajo; "" = del propio catálogo (hecha o importada a mano)
    version: str
    bloqueo: Block

    def mensaje(self) -> str:
        return f"la pieza {self.ref} coincide con un archivo {self.bloqueo.describe()}; sigue instalada, quitala o reemplazala si corresponde"


def piezas_bloqueadas(raiz: str, bloqueados) -> list[PiezaBloqueada]:
    """Las piezas instaladas en `raiz` cuyo `sha256` (el del STL, el de su ficha) figura en `bloqueados`. Todo en local: compara con lo que ya hay
    en disco; no abre mallas ni envía nada. Un catálogo que no se puede leer da lista vacía (la auditoría no frena por eso)."""
    por_sha = {b.sha256: b for b in bloqueados if b.sha256}
    if not por_sha:
        return []
    from .instalar import InstallError, load_registry

    try:
        reg = load_registry(raiz).get("paquetes") or {}
        metas = Catalog.open(raiz).list(all_versions=True)
    except (InstallError, CatalogError, OSError):
        return []
    duenos = {}
    for pid in sorted(reg):
        info = reg[pid] if isinstance(reg[pid], dict) else {}
        for ref in ((info.get("refs") or {}).get("piezas") or []):
            duenos.setdefault(ref, (pid, str(info.get("version") or "")))
    out = []
    for m in metas:
        b = por_sha.get(m.sha256 or "")
        if b is not None:
            pid, ver = duenos.get(m.ref, ("", ""))
            out.append(PiezaBloqueada(m.ref, pid, ver, b))
    return sorted(out, key=lambda p: p.ref)


__all__ = ["BLOCK_FILE", "CODE", "BloqueosError", "Hallazgo", "PiezaBloqueada", "a_json", "comprobar_bloqueados", "leer_bloqueados", "piezas_bloqueadas",
           "retirar", "validar_bloqueados"]
