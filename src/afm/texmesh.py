"""Malla triangulada con UV por esquina y texturas embebidas (la «fuente de color»; plan §6, T02).

`PIL` y `trimesh` se importan dentro de las funciones que los usan (D10): importar este módulo es barato.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np

IMAGE_KINDS = ("base_color", "normal", "roughness", "metallic")


@dataclass
class TexturedMesh:
    vertices: np.ndarray                  # (V, 3) float64, ya multiplicada por `Lcl Scaling`, unidades del FBX
    faces: np.ndarray                     # (F, 3) int32 (cuadriláteros partidos por la diagonal más corta)
    corner_uv: np.ndarray                 # (F, 3, 2) float32: UV de cada esquina; v hacia arriba (convención FBX)
    images: dict = field(default_factory=dict)      # "base_color" | "normal" | "roughness" | "metallic" -> bytes PNG
    info: dict = field(default_factory=dict)        # {"avisos": [...], "up_axis": int, "scaling": [3], "n_materiales": int, ...}
    _cache: dict = field(default_factory=dict, repr=False, compare=False)

    def image(self, kind: str = "base_color") -> np.ndarray:
        """Píxeles como `uint8 (H, W, 3)`. Se decodifica la primera vez y se cachea (un canal se replica; RGBA/paleta/16 bits pasan por `convert("RGB")`)."""
        if kind in self._cache:
            return self._cache[kind]
        if kind not in self.images:
            raise KeyError(f"la malla no tiene imagen «{kind}»")
        from PIL import Image

        with Image.open(io.BytesIO(self.images[kind])) as im:
            arr = np.asarray(im.convert("RGB"), dtype=np.uint8)
        self._cache[kind] = arr
        return arr

    def release_image(self, kind: str = "base_color") -> None:
        """Libera la imagen decodificada (RG5: 4096² RGB pesa ≈ 50 MB)."""
        self._cache.pop(kind, None)

    def sample(self, face: np.ndarray, bary: np.ndarray, kind: str = "base_color") -> np.ndarray:
        """Color `(n, 3) uint8` en el punto de baricéntricas `bary` del triángulo `face`, con los UV de las esquinas de ESE triángulo.

        Convención de píxel [M3]: fila = (1 − v)·alto − 0,5; columna = u·ancho − 0,5; bilineal con borde «clamp».
        """
        face = np.asarray(face, np.int64)
        bary = np.asarray(bary, np.float64)
        uv = np.einsum("nk,nkc->nc", bary, self.corner_uv[face].astype(np.float64))
        return sample_image(self.image(kind), uv)

    def to_trimesh(self):
        """Solo la geometría (sin UV ni color): lo que entra al motor."""
        import trimesh

        return trimesh.Trimesh(np.array(self.vertices, float), np.array(self.faces), process=False)


def sample_image(img: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Muestreo bilineal de `img` (H, W, 3) en `uv` (n, 2), con la convención de píxel del plan."""
    h, w = img.shape[:2]
    c = np.clip(uv[:, 0] * w - 0.5, 0, w - 1)
    r = np.clip((1.0 - uv[:, 1]) * h - 0.5, 0, h - 1)
    c0, r0 = np.floor(c).astype(np.int64), np.floor(r).astype(np.int64)
    c1, r1 = np.minimum(c0 + 1, w - 1), np.minimum(r0 + 1, h - 1)
    fc, fr = (c - c0)[:, None], (r - r0)[:, None]
    out = (img[r0, c0] * (1 - fc) * (1 - fr) + img[r0, c1] * fc * (1 - fr)
           + img[r1, c0] * (1 - fc) * fr + img[r1, c1] * fc * fr)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)
