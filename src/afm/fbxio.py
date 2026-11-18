"""Lector de FBX binario (v7100–v7700): árbol de nodos, sin interpretar su contenido.

Solo usa `zlib`, `struct` y `numpy`. La interpretación (malla, UV, texturas) vive en `texmesh`/`load_textured`.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

import numpy as np

MAGIC = b"Kaydara FBX Binary  \x00\x1a\x00"
VERSION_MIN, VERSION_MAX = 7100, 7700

_ARR = {b"f": ("<f4", 4), b"d": ("<f8", 8), b"l": ("<i8", 8), b"i": ("<i4", 4), b"b": ("<u1", 1)}
_SCALAR = {b"Y": "<h", b"C": "<?", b"I": "<i", b"F": "<f", b"D": "<d", b"L": "<q"}


class FbxError(ValueError):
    """El archivo no es un FBX binario legible."""


@dataclass
class Node:
    name: str
    props: list = field(default_factory=list)
    kids: list["Node"] = field(default_factory=list)

    def find(self, name: str) -> list["Node"]:
        return [k for k in self.kids if k.name == name]

    def first(self, name: str) -> "Node | None":
        for k in self.kids:
            if k.name == name:
                return k
        return None


def fbx_version(data: bytes) -> int:
    if data[:5] == b"; FBX" or data[:2] == b"; ":
        raise FbxError("el FBX es de texto (ASCII); exportalo en binario")
    if len(data) < 27 or data[:23] != MAGIC:
        raise FbxError("no es un FBX binario (firma inválida)")
    v = struct.unpack_from("<I", data, 23)[0]
    if not VERSION_MIN <= v <= VERSION_MAX:
        raise FbxError(f"versión de FBX no soportada: {v} (se leen {VERSION_MIN}–{VERSION_MAX})")
    return v


def parse_fbx(data: bytes) -> Node:
    """Árbol de nodos; la raíz es sintética (`"root"`). Todo error de formato sale como `FbxError`."""
    ver = fbx_version(data)
    wide = ver >= 7500
    hdr = 25 if wide else 13
    size = len(data)

    def need(pos: int, n: int) -> None:
        if n < 0 or pos + n > size:
            raise FbxError("datos truncados o corruptos")

    def read_prop(pos: int):
        need(pos, 1)
        t = data[pos:pos + 1]
        pos += 1
        if t in _SCALAR:
            fmt = _SCALAR[t]
            n = struct.calcsize(fmt)
            need(pos, n)
            return struct.unpack_from(fmt, data, pos)[0], pos + n
        if t in _ARR:
            need(pos, 12)
            ln, enc, clen = struct.unpack_from("<III", data, pos)
            pos += 12
            dt, w = _ARR[t]
            need(pos, clen)
            raw = data[pos:pos + clen]
            if enc == 1:
                try:
                    raw = zlib.decompress(raw)
                except zlib.error as e:
                    raise FbxError("datos truncados o corruptos") from e
            elif enc != 0:
                raise FbxError("codificación de arreglo desconocida")
            if len(raw) < ln * w:
                raise FbxError("datos truncados o corruptos")
            arr = np.frombuffer(raw, dtype=dt, count=ln)
            return (arr.astype(bool) if t == b"b" else arr.copy()), pos + clen
        if t in (b"S", b"R"):
            need(pos, 4)
            n = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            need(pos, n)
            raw = bytes(data[pos:pos + n])
            return (raw.decode("utf-8", errors="replace") if t == b"S" else raw), pos + n
        raise FbxError(f"tipo de propiedad desconocido: {t!r}")

    def read_node(pos: int):
        need(pos, hdr)
        if wide:
            end, nprop, plen, nlen = struct.unpack_from("<QQQB", data, pos)
        else:
            end, nprop, plen, nlen = struct.unpack_from("<IIIB", data, pos)
        pos += hdr
        if end == 0:
            return None, pos
        if end > size:
            raise FbxError("datos truncados o corruptos")
        need(pos, nlen)
        node = Node(data[pos:pos + nlen].decode("latin-1"))
        pos += nlen
        for _ in range(nprop):
            v, pos = read_prop(pos)
            node.props.append(v)
        while pos < end:
            kid, pos = read_node(pos)
            if kid is None:
                break
            node.kids.append(kid)
        return node, max(pos, end)

    root = Node("root")
    pos = 27
    try:
        while pos + hdr <= size:
            n, pos = read_node(pos)
            if n is None:
                break
            root.kids.append(n)
    except (struct.error, IndexError, RecursionError) as e:
        raise FbxError("datos truncados o corruptos") from e
    return root
