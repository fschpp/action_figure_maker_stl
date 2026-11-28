"""Color de una pieza extraído de su FBX, **sin el FBX** (`afm-color-rgb-1`, archivo `<id>@<v>.colorrgb.npz`).

El FBX de una pieza `modo: rgb` pesa 26–36 MB (malla, normales, materiales y una textura sin recomprimir). Para colorear solo hace falta lo que
`TexturedMesh.sample` usa: los vértices, las caras, los UV de cada esquina y la textura de color base. Este módulo guarda justo eso:

* geometría en `float32` / `int32` (igual que el paquete de roles), UV en `float32` (con `float16` una textura de 4096 px perdería ~2 px);
* la textura base como **JPEG** (calidad `COLOR_QUALITY`) y, si pasa de `COLOR_MAX_SIDE` por lado, reducida a ese tope;
* nada de normales, rugosidad, metal ni materiales (no entran en el color), y **ninguna referencia al FBX** (ni nombre ni hash).

El resultado vive en el mismo marco que el FBX: la `similitud` de la ficha (FBX → STL) sigue valiendo sin recalcular nada, y
`fbxio.load_textured` lee este archivo igual que un FBX, así que el armado, la vista previa y el horneado no cambian. `PIL` se importa dentro de
las funciones (D10).
"""
from __future__ import annotations

import io
import json
import os

import numpy as np

FORMAT = "afm-color-rgb-1"
COLOR_MAX_SIDE = 4096            # [P] tope por lado de la textura; más grande se reduce (el espaciado de muestreo es 0,08 mm)
COLOR_QUALITY = 90               # [P] calidad JPEG de la textura


class ColorRgbError(ValueError):
    """El archivo de color compacto no se pudo armar o leer."""


def is_compact(source) -> bool:
    """`True` si `source` (ruta o bytes) es un `.npz` (un zip), no un FBX. Un FBX binario empieza con «Kaydara FBX Binary»."""
    if isinstance(source, (bytes, bytearray)):
        return bytes(source[:2]) == b"PK"
    return os.fspath(source).lower().endswith(".npz")


def compact_bytes(tm, *, max_side: int = COLOR_MAX_SIDE, quality: int = COLOR_QUALITY) -> bytes:
    """Los bytes del `.colorrgb.npz` de un `TexturedMesh` leído de un FBX. `ColorRgbError` si no trae textura de color."""
    from PIL import Image

    if getattr(tm, "labels", None) is not None:
        raise ColorRgbError("la malla ya es de color por roles: usá el paquete de roles")
    if "base_color" not in tm.images:
        raise ColorRgbError("el FBX no trae textura de color")
    img = np.asarray(tm.image("base_color"), np.uint8)
    h, w = img.shape[:2]
    pil = Image.fromarray(img)
    if max(h, w) > max_side:
        k = max_side / max(h, w)
        pil = pil.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=int(quality), optimize=True)
    tm.release_image("base_color")
    meta = {"formato": FORMAT, "textura": [int(pil.size[0]), int(pil.size[1])], "calidad": int(quality)}
    out = io.BytesIO()
    np.savez_compressed(out, meta=np.frombuffer(json.dumps(meta).encode("utf-8"), np.uint8),
                        vertices=np.asarray(tm.vertices, np.float32), faces=np.asarray(tm.faces, np.int32),
                        corner_uv=np.asarray(tm.corner_uv, np.float32), textura=np.frombuffer(buf.getvalue(), np.uint8))
    return out.getvalue()


def compact_file(fbx_path: str, **kw) -> bytes:
    """Lee el FBX de `fbx_path` y devuelve los bytes del color compacto. `FbxError` si no es un FBX legible."""
    from .fbxio import load_textured

    return compact_bytes(load_textured(fbx_path), **kw)


def load(source):
    """`TexturedMesh` desde la ruta (o los bytes) de un `.colorrgb.npz`. Errores de formato: `ColorRgbError`."""
    from .texmesh import TexturedMesh

    name = "color compacto" if isinstance(source, (bytes, bytearray)) else os.path.basename(os.fspath(source))
    try:
        with np.load(io.BytesIO(bytes(source)) if isinstance(source, (bytes, bytearray)) else os.fspath(source), allow_pickle=False) as z:
            missing = [k for k in ("meta", "vertices", "faces", "corner_uv", "textura") if k not in z.files]
            if missing:
                raise ColorRgbError(f"{name}: faltan arrays: {', '.join(missing)}")
            meta = json.loads(bytes(np.asarray(z["meta"], np.uint8)).decode("utf-8"))
            v, f, uv, tex = z["vertices"], z["faces"], z["corner_uv"], bytes(np.asarray(z["textura"], np.uint8))
    except ColorRgbError:
        raise
    except (OSError, ValueError, KeyError, UnicodeDecodeError, EOFError) as exc:
        raise ColorRgbError(f"{name}: no es un archivo de color legible ({exc})") from exc
    if not isinstance(meta, dict) or meta.get("formato") != FORMAT:
        raise ColorRgbError(f"{name}: formato desconocido (se esperaba «{FORMAT}»)")
    if v.ndim != 2 or v.shape[1] != 3 or f.ndim != 2 or f.shape[1] != 3 or uv.shape != (len(f), 3, 2) or not len(f):
        raise ColorRgbError(f"{name}: las formas de vértices, caras y UV no cierran")
    if len(f) and (f.min() < 0 or f.max() >= len(v)):
        raise ColorRgbError(f"{name}: hay caras con índices fuera de los vértices")
    return TexturedMesh(np.asarray(v, np.float64), np.asarray(f, np.int32), np.asarray(uv, np.float32), {"base_color": tex},
                        {"avisos": [], "n_materiales": 1, "formato": FORMAT})


__all__ = ["FORMAT", "COLOR_MAX_SIDE", "COLOR_QUALITY", "ColorRgbError", "is_compact", "compact_bytes", "compact_file", "load"]
