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


# ---------------------------------------------------------------------------------------------- T02: malla con textura
_KIND_OF_PROP = {"DiffuseColor": "base_color", "NormalMap": "normal", "ShininessExponent": "roughness",
                 "ReflectionFactor": "metallic"}


def polygons_from_pvi(pvi) -> list[list[int]]:
    """Polígonos de `PolygonVertexIndex`: un índice negativo cierra el polígono y se recupera con `~i`."""
    out: list[list[int]] = []
    cur: list[int] = []
    for i in np.asarray(pvi).tolist():
        if i < 0:
            cur.append(~i)
            out.append(cur)
            cur = []
        else:
            cur.append(i)
    return out


def _props70(node: Node) -> dict:
    out: dict = {}
    p70 = node.first("Properties70")
    if p70 is None:
        return out
    for p in p70.find("P"):
        if len(p.props) >= 5:
            vals = p.props[4:]
            out[p.props[0]] = vals[0] if len(vals) == 1 else [float(v) for v in vals]
    return out


def _triangulate(verts: np.ndarray, polys: list[list[int]]):
    """(triángulos como índices de vértice, posición de esquina 0..n−1 de cada uno en su polígono, índice plano de esquina)."""
    tv, tc = [], []
    pos = 0
    for poly in polys:
        n = len(poly)
        if n == 3:
            tv.append((poly[0], poly[1], poly[2])); tc.append((pos, pos + 1, pos + 2))
        elif n == 4:
            a, b, c, d = poly
            if np.linalg.norm(verts[a] - verts[c]) <= np.linalg.norm(verts[b] - verts[d]):
                tv += [(a, b, c), (a, c, d)]; tc += [(pos, pos + 1, pos + 2), (pos, pos + 2, pos + 3)]
            else:
                tv += [(a, b, d), (b, c, d)]; tc += [(pos, pos + 1, pos + 3), (pos + 1, pos + 2, pos + 3)]
        elif n > 4:
            for k in range(1, n - 1):
                tv.append((poly[0], poly[k], poly[k + 1])); tc.append((pos, pos + k, pos + k + 1))
        pos += n
    return np.array(tv, np.int32).reshape(-1, 3), np.array(tc, np.int64).reshape(-1, 3)


def load_textured(source):
    """`TexturedMesh` desde la ruta de un FBX binario o desde sus bytes. Errores de formato: `FbxError`."""
    from .texmesh import TexturedMesh

    data = source if isinstance(source, (bytes, bytearray)) else open(source, "rb").read()
    data = bytes(data)
    root = parse_fbx(data)
    objects = root.first("Objects")
    if objects is None:
        raise FbxError("el FBX no tiene nodo Objects")
    avisos: list[str] = []
    geoms = [o for o in objects.find("Geometry") if len(o.props) > 2 and o.props[2] == "Mesh"]
    if not geoms:
        raise FbxError("el FBX no trae ninguna malla (Geometry/Mesh)")
    if len(geoms) > 1:
        avisos.append(f"El FBX trae {len(geoms)} mallas: se usa solo la primera.")
    g = geoms[0]
    by_id = {int(o.props[0]): o for o in objects.kids if o.props and isinstance(o.props[0], (int, np.integer))}
    # conexiones, sin repetidos por (hijo, padre, propiedad)
    seen, conns = set(), []
    cn = root.first("Connections")
    for c in (cn.find("C") if cn else []):
        p = c.props
        if len(p) >= 3:
            key = (p[0], int(p[1]), int(p[2]), p[3] if len(p) > 3 else None)
            if key not in seen:
                seen.add(key)
                conns.append(key)

    vn, pn = g.first("Vertices"), g.first("PolygonVertexIndex")
    if vn is None or pn is None:
        raise FbxError("la malla del FBX no trae Vertices o PolygonVertexIndex")
    verts = np.asarray(vn.props[0], np.float64).reshape(-1, 3)
    pvi = np.asarray(pn.props[0], np.int64)
    gid = int(g.props[0])
    model = next((by_id[p] for (t, c, p, _l) in conns if t == "OO" and c == gid and p in by_id and by_id[p].name == "Model"), None)
    scaling = [1.0, 1.0, 1.0]
    if model is not None:
        P = _props70(model)
        if "Lcl Scaling" in P:
            scaling = [float(x) for x in P["Lcl Scaling"]]
        rot = P.get("Lcl Rotation")
        if rot is not None and max(abs(float(x)) for x in rot) > 1e-3:
            avisos.append(f"El Model trae una rotación {list(rot)}° que no se aplica.")
    verts = verts * np.array(scaling)

    polys = polygons_from_pvi(pvi)
    faces, corner_pos = _triangulate(verts, polys)

    uvl = g.first("LayerElementUV")
    if uvl is None:
        raise FbxError("el FBX no trae UV (LayerElementUV)")
    mapping = uvl.first("MappingInformationType").props[0] if uvl.first("MappingInformationType") else ""
    ref = uvl.first("ReferenceInformationType").props[0] if uvl.first("ReferenceInformationType") else ""
    uv_all = np.asarray(uvl.first("UV").props[0], np.float64).reshape(-1, 2)
    if mapping != "ByPolygonVertex" or ref not in ("IndexToDirect", "Direct"):
        raise FbxError(f"UV con mapeo no soportado ({mapping} + {ref}): se espera ByPolygonVertex con IndexToDirect o Direct")
    if ref == "IndexToDirect":
        idx = np.asarray(uvl.first("UVIndex").props[0], np.int64)
        per_corner = uv_all[idx]
    else:
        per_corner = uv_all
    corner_uv = per_corner[corner_pos].astype(np.float32)

    materials = [o for o in objects.find("Material")]
    n_mat = len(materials)
    if n_mat > 1:
        avisos.append(f"El FBX trae {n_mat} materiales: solo se usa el color base del primero.")
    first_mat = int(materials[0].props[0]) if materials else None
    tex_nodes = {i: o for i, o in by_id.items() if o.name == "Texture"}
    vid_nodes = {i: o for i, o in by_id.items() if o.name == "Video"}
    video_of_tex = {p: c for (t, c, p, _l) in conns if t == "OO" and c in vid_nodes and p in tex_nodes}
    images: dict = {}
    for (t, c, p, label) in conns:
        if t != "OP" or c not in tex_nodes or p != first_mat or label not in _KIND_OF_PROP:
            continue
        kind = _KIND_OF_PROP[label]
        if kind in images:
            continue
        vid = vid_nodes.get(video_of_tex.get(c))
        content = vid.first("Content").props[0] if vid is not None and vid.first("Content") is not None and vid.first("Content").props else None
        if isinstance(content, (bytes, bytearray)) and len(content) > 16:
            images[kind] = bytes(content)
        else:
            avisos.append(f"La textura «{kind}» no trae la imagen embebida: se ignora.")
    if "base_color" not in images:
        avisos.append("El FBX no trae textura de color base: la figura saldrá sin color.")
    gs = root.first("GlobalSettings")
    gp = _props70(gs) if gs is not None else {}
    info = {"avisos": avisos, "up_axis": gp.get("UpAxis"), "scaling": scaling, "n_materiales": n_mat,
            "version": fbx_version(data), "n_poligonos": len(polys)}
    return TexturedMesh(verts, faces, corner_uv, images, info)
