"""Escritor de FBX binario sintético para las pruebas (Geometry + Model + Material + Texture + Video + Connections).

Reproduce la estructura medida en el archivo del cliente: 1 Geometry, 1 Model (con `Lcl Scaling`), 1 Material, texturas
con su Video embebido, UV por esquina (`ByPolygonVertex` + `IndexToDirect`) y polígonos con el último índice complementado.
"""
from __future__ import annotations

import io
import struct
import zlib

import numpy as np


class N:
    def __init__(self, name, props=(), children=()):
        self.name, self.props, self.children = name, list(props), list(children)


def _prop(v, compress=True) -> bytes:
    if isinstance(v, bool):
        return b"C" + bytes([int(v)])
    if isinstance(v, np.ndarray):
        kind = {"f": "d", "i": "i", "u": "i"}[v.dtype.kind]
        arr = np.ascontiguousarray(v, dtype={"d": "<f8", "i": "<i4"}[kind])
        raw = arr.tobytes()
        if compress:
            z = zlib.compress(raw)
            return kind.encode() + struct.pack("<III", arr.size, 1, len(z)) + z
        return kind.encode() + struct.pack("<III", arr.size, 0, len(raw)) + raw
    if isinstance(v, int):
        if abs(v) > 2**31 - 1:
            return b"L" + struct.pack("<q", v)
        return b"I" + struct.pack("<i", v)
    if isinstance(v, float):
        return b"D" + struct.pack("<d", v)
    if isinstance(v, str):
        b = v.encode()
        return b"S" + struct.pack("<I", len(b)) + b
    if isinstance(v, (bytes, bytearray)):
        return b"R" + struct.pack("<I", len(v)) + bytes(v)
    raise TypeError(type(v))


def _node(n: N, pos: int, wide: bool) -> bytes:
    props = b"".join(_prop(p) for p in n.props)
    name = n.name.encode()
    head = (25 if wide else 13) + len(name)
    body_start = pos + head + len(props)
    kids = b""
    cur = body_start
    for c in n.children:
        kb = _node(c, cur, wide)
        kids += kb
        cur += len(kb)
    if n.children:
        kids += b"\0" * (25 if wide else 13)
    end = body_start + len(kids)
    fmt = "<QQQB" if wide else "<IIIB"
    return struct.pack(fmt, end, len(n.props), len(props), len(name)) + name + props + kids


def p70(*rows) -> N:
    return N("Properties70", children=[N("P", list(r)) for r in rows])


def png_bytes(arr: np.ndarray) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def write_fbx(path, verts, polys, corner_uv_idx, uvs, texture=None, extra_textures=None, scaling=(1, 1, 1),
              rotation=(0, 0, 0), version=7400, up_axis=1, mapping="ByPolygonVertex"):
    """`polys`: lista de listas de índices de vértice (3 o 4). `corner_uv_idx`: un índice de UV por esquina (en orden de polígono).
    `texture`: uint8 (h, w, 3) del color base; `extra_textures`: {propiedad: uint8} (normal, rugosidad…)."""
    wide = version >= 7500
    pvi = []
    for poly in polys:
        pvi += list(poly[:-1]) + [~int(poly[-1])]
    geom = N("Geometry", [1001, "Cube\x00\x01Geometry", "Mesh"], [
        N("Vertices", [np.asarray(verts, float).ravel()]),
        N("PolygonVertexIndex", [np.asarray(pvi, np.int32)]),
        N("LayerElementUV", [0], [
            N("MappingInformationType", [mapping]),
            N("ReferenceInformationType", ["IndexToDirect"]),
            N("UV", [np.asarray(uvs, float).ravel()]),
            N("UVIndex", [np.asarray(corner_uv_idx, np.int32)]),
        ]),
        N("LayerElementMaterial", [0], [N("MappingInformationType", ["AllSame"]), N("ReferenceInformationType", ["IndexToIndex"]),
                                       N("Materials", [np.array([0], np.int32)])]),
    ])
    model = N("Model", [1002, "Figura\x00\x01Model", "Mesh"], [
        p70(("Lcl Scaling", "Lcl Scaling", "", "A", float(scaling[0]), float(scaling[1]), float(scaling[2])),
            ("Lcl Rotation", "Lcl Rotation", "", "A", *[float(x) for x in rotation])),
    ])
    mat = N("Material", [1003, "Mat\x00\x01Material", ""], [N("ShadingModel", ["phong"])])
    objs = [geom, model, mat]
    conns = [N("C", ["OO", 1001, 1002]), N("C", ["OO", 1003, 1002])]
    texs = []
    if texture is not None:
        texs.append(("DiffuseColor", "color.png", texture))
    for prop, arr in (extra_textures or {}).items():
        texs.append((prop, f"{prop}.png", arr))
    for i, (prop, fname, arr) in enumerate(texs):
        tid, vid = 2000 + i, 3000 + i
        objs.append(N("Texture", [tid, f"{fname}\x00\x01Texture", ""], [N("FileName", [fname]), N("RelativeFilename", [fname])]))
        objs.append(N("Video", [vid, f"{fname}\x00\x01Video", "Clip"], [N("FileName", [fname]), N("Content", [png_bytes(arr)])]))
        conns.append(N("C", ["OO", vid, tid]))
        conns.append(N("C", ["OP", tid, 1003, prop]))
    top = [
        N("FBXHeaderExtension", [], [N("FBXHeaderVersion", [1003]), N("FBXVersion", [version])]),
        N("GlobalSettings", [], [p70(("UpAxis", "int", "Integer", "", up_axis), ("UpAxisSign", "int", "Integer", "", 1),
                                   ("FrontAxis", "int", "Integer", "", 2), ("FrontAxisSign", "int", "Integer", "", 1),
                                   ("UnitScaleFactor", "double", "Number", "", 1.0))]),
        N("Objects", [], objs),
        N("Connections", [], conns),
    ]
    out = b"Kaydara FBX Binary  \x00\x1a\x00" + struct.pack("<I", version)
    pos = len(out)
    for n in top:
        nb = _node(n, pos, wide)
        out += nb
        pos += len(nb)
    out += b"\0" * (25 if wide else 13)
    with open(path, "wb") as fh:
        fh.write(out)
