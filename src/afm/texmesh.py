"""Malla triangulada con UV por esquina y texturas embebidas (la «fuente de color»; plan §6, T02).

`PIL` y `trimesh` se importan dentro de las funciones que los usan (D10): importar este módulo es barato.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np

IMAGE_KINDS = ("base_color", "normal", "roughness", "metallic")

# Sombreado por rol (`PLAN_color_sombreado.md`): un atlas uint8 con el factor de brillo de cada píxel respecto del color medio de su rol.
# Código 128 = ×1,0; el rango guardado es SHADE_MIN…SHADE_MAX (acotado a propósito: un sombreado agresivo recoloreado daría colores raros).
SHADE_ONE = 128
SHADE_MIN = 0.5
SHADE_MAX = 1.5


@dataclass
class TexturedMesh:
    vertices: np.ndarray                  # (V, 3) float64, ya multiplicada por `Lcl Scaling`, unidades del FBX
    faces: np.ndarray                     # (F, 3) int32 (cuadriláteros partidos por la diagonal más corta)
    corner_uv: np.ndarray                 # (F, 3, 2) float32: UV de cada esquina; v hacia arriba (convención FBX)
    images: dict = field(default_factory=dict)      # "base_color" | "normal" | "roughness" | "metallic" -> bytes PNG
    info: dict = field(default_factory=dict)        # {"avisos": [...], "up_axis": int, "scaling": [3], "n_materiales": int, ...}
    _cache: dict = field(default_factory=dict, repr=False, compare=False)
    labels: np.ndarray | None = field(default=None, repr=False, compare=False)   # (H, W) uint8: rol de cada píxel (255 = sin rol), T41
    lut: np.ndarray | None = field(default=None, repr=False, compare=False)      # (256, 3) uint8: color de cada etiqueta
    shade: np.ndarray | None = field(default=None, repr=False, compare=False)    # (H, W) uint8: sombreado de cada píxel (128 = ×1,0); solo con `labels`

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

    def _uv(self, face: np.ndarray, bary: np.ndarray) -> np.ndarray:
        face = np.asarray(face, np.int64)
        bary = np.asarray(bary, np.float64)
        return np.einsum("nk,nkc->nc", bary, self.corner_uv[face].astype(np.float64))

    def sample_labels(self, face: np.ndarray, bary: np.ndarray) -> np.ndarray:
        """Etiqueta de rol `(n,) uint8` en el punto de baricéntricas `bary` del triángulo `face`. **Vecino más cercano** (nunca se
        interpola: mezclar índices inventa roles en los bordes). Misma convención de píxel que `sample`: el texel que contiene el
        punto (columna = ⌊u·ancho⌋, fila = ⌊(1 − v)·alto⌋, con tope en los bordes)."""
        if self.labels is None:
            raise ValueError("la malla no tiene etiquetas de rol")
        return nearest_label(self.labels, self._uv(face, bary))

    def sample_shade(self, face: np.ndarray, bary: np.ndarray) -> np.ndarray | None:
        """Factor de sombreado `(n,) float32` en el punto `bary` del triángulo `face` (bilineal: es una magnitud continua), o `None` si la malla no
        trae atlas de sombreado."""
        if self.shade is None:
            return None
        return sample_plane(shade_factor(self.shade), self._uv(face, bary))

    def sample(self, face: np.ndarray, bary: np.ndarray, kind: str = "base_color") -> np.ndarray:
        """Color `(n, 3) uint8` en el punto de baricéntricas `bary` del triángulo `face`, con los UV de las esquinas de ESE triángulo.

        Convención de píxel [M3]: fila = (1 − v)·alto − 0,5; columna = u·ancho − 0,5; bilineal con borde «clamp».
        Con `labels` (T41) el color de base sale de `lut[etiqueta]` por vecino más cercano: los únicos colores posibles son los de la LUT; con `shade`
        ese color se modula por el factor de brillo del píxel (mismo matiz, ver `apply_shade`).
        """
        if kind == "base_color" and self.labels is not None:
            if self.lut is None:
                raise ValueError("la malla tiene etiquetas de rol pero no la tabla de colores (lut)")
            rgb = np.asarray(self.lut)[self.sample_labels(face, bary)]
            f = self.sample_shade(face, bary)
            return rgb if f is None else apply_shade(rgb, f)
        return sample_image(self.image(kind), self._uv(face, bary))

    def transformed(self, M) -> "TexturedMesh":
        """Copia con los vértices llevados por la 4×4 `M` (similitud, con o sin espejo). Los UV y las texturas no se tocan: se comparten
        con la original (también la imagen ya decodificada). Con det < 0 se invierte el orden de cada triángulo (y el de sus UV) para
        que las normales sigan hacia afuera."""
        M = np.asarray(M, float)
        V = np.asarray(self.vertices, float) @ M[:3, :3].T + M[:3, 3]
        F, UV = self.faces, self.corner_uv
        if np.linalg.det(M[:3, :3]) < 0:
            F, UV = F[:, ::-1].copy(), UV[:, ::-1].copy()
        return TexturedMesh(V, F, UV, self.images, dict(self.info), self._cache, labels=self.labels, lut=self.lut, shade=self.shade)

    def to_trimesh(self):
        """Solo la geometría (sin UV ni color): lo que entra al motor."""
        import trimesh

        return trimesh.Trimesh(np.array(self.vertices, float), np.array(self.faces), process=False)


def nearest_label(labels: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Etiqueta (uint8) del texel que contiene `uv` (n, 2) en el atlas `labels` (H, W): sin interpolar."""
    h, w = labels.shape[:2]
    c = np.clip(np.floor(uv[:, 0] * w).astype(np.int64), 0, w - 1)
    r = np.clip(np.floor((1.0 - uv[:, 1]) * h).astype(np.int64), 0, h - 1)
    return labels[r, c]


def shade_factor(code: np.ndarray) -> np.ndarray:
    """Código uint8 del atlas de sombreado → factor float32 (128 → 1,0), acotado a `SHADE_MIN`…`SHADE_MAX`."""
    return np.clip(np.asarray(code, np.float32) / SHADE_ONE, SHADE_MIN, SHADE_MAX)


def shade_code(factor: np.ndarray) -> np.ndarray:
    """Factor de sombreado → código uint8 (inverso de `shade_factor`)."""
    return np.rint(np.clip(np.asarray(factor, np.float32), SHADE_MIN, SHADE_MAX) * SHADE_ONE).astype(np.uint8)


def apply_shade(rgb: np.ndarray, factor: np.ndarray) -> np.ndarray:
    """`rgb` (n, 3) uint8 × `factor` (n,) → (n, 3) uint8. Se multiplica **el mismo factor en los tres canales** (el matiz no cambia) y el factor se
    limita a lo que deja el canal más alto sin pasarse de 255 (un tono claro no se «quema» hacia el blanco ni cambia de matiz al recortar)."""
    rgb = np.asarray(rgb)
    f = np.asarray(factor, np.float32)
    f = np.minimum(f, 255.0 / np.maximum(rgb.max(axis=-1), 1).astype(np.float32))
    return np.clip(np.rint(rgb.astype(np.float32) * f[..., None]), 0, 255).astype(np.uint8)


def sample_plane(a: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Muestreo bilineal de un atlas de un canal `a` (H, W) en `uv` (n, 2), con la misma convención de píxel que `sample_image`."""
    h, w = a.shape[:2]
    c = np.clip(uv[:, 0] * w - 0.5, 0, w - 1)
    r = np.clip((1.0 - uv[:, 1]) * h - 0.5, 0, h - 1)
    c0, r0 = np.floor(c).astype(np.int64), np.floor(r).astype(np.int64)
    c1, r1 = np.minimum(c0 + 1, w - 1), np.minimum(r0 + 1, h - 1)
    fc, fr = c - c0, r - r0
    return (a[r0, c0] * (1 - fc) * (1 - fr) + a[r0, c1] * fc * (1 - fr) + a[r1, c0] * (1 - fc) * fr + a[r1, c1] * fc * fr).astype(np.float32)


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
