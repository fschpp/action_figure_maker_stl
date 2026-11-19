"""Convierte un FBX texturizado en un GLB con color, de pie y a la escala de la figura, para verlo (plan T05).

Uso: python tools/fbx_a_glb.py entrada.fbx salida.glb [--front -Y] [--crown-z 101.769] [--altura 100]

Marco de salida: el mismo que usa el motor para la geometría (Z arriba, pies en z = 0, centrada en X e Y). La escala fija
`--altura` mm (por defecto 100) entre los pies y la coronilla: si se pasa `--crown-z` (cota ABSOLUTA de la coronilla en unidades
del FBX ya orientado a Z arriba, p. ej. 101,769 en Güemes) la figura queda con la misma escala que su STL (114 mm con sombrero);
si no, `--altura` mm van hasta el punto más alto. `--front -Y` (frente hacia −Y en el archivo) gira 180° sobre Z para que el frente
quede en +Y, igual que `Project.front`. Sin ICP, sin reparar la malla y sin tocar `src/afm/`.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np


def convert(source, glb_path: str, front: str = "+Y", crown_z: float | None = None, ref_height: float = 100.0) -> dict:
    """`source`: ruta de un FBX o un `TexturedMesh`. Escribe `glb_path` y devuelve `{escala, alto_mm, bounds, triangulos, ...}`."""
    import trimesh
    from PIL import Image

    from afm.fbxio import load_textured
    from afm.meshio import orient_z_up_matrix

    tm = load_textured(source) if isinstance(source, (str, bytes, bytearray)) else source
    if "base_color" not in tm.images:
        raise ValueError("el FBX no trae la textura de color base: no hay nada que mostrar")
    V = np.asarray(tm.vertices, float)
    R = orient_z_up_matrix(trimesh.PointCloud(V))
    V = V @ R.T
    zmin = float(V[:, 2].min())
    top = float(V[:, 2].max()) if crown_z is None else float(crown_z)
    if top <= zmin:
        raise ValueError("la coronilla debe estar por encima de los pies")
    scale = ref_height / (top - zmin)
    V = V * scale
    lo, hi = V.min(0), V.max(0)
    V = V - [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]]
    if front == "-Y":
        V = V * [-1.0, -1.0, 1.0]
    elif front != "+Y":
        raise ValueError("front debe ser '+Y' o '-Y'")
    # UV por vértice exige desdoblar por esquina: 3 vértices por triángulo, sin indexar
    F = np.asarray(tm.faces, np.int64)
    verts = V[F].reshape(-1, 3)
    uv = np.asarray(tm.corner_uv, np.float64).reshape(-1, 2)
    import io

    with Image.open(io.BytesIO(tm.images["base_color"])) as im:
        img = im.convert("RGB")
        img.load()
    material = trimesh.visual.material.PBRMaterial(baseColorTexture=img, metallicFactor=0.0, roughnessFactor=1.0)
    mesh = trimesh.Trimesh(verts, np.arange(len(verts)).reshape(-1, 3), process=False,
                           visual=trimesh.visual.TextureVisuals(uv=uv, material=material))
    trimesh.Scene([mesh]).export(glb_path)
    b = mesh.bounds
    return {"escala": float(scale), "alto_mm": float(b[1, 2] - b[0, 2]), "bounds": b.round(4).tolist(),
            "triangulos": int(len(F)), "vertices_glb": int(len(verts)), "front": front}


def _join_front(argv: list[str]) -> list[str]:
    """argparse toma «-Y» por una opción: `--front -Y` se vuelve `--front=-Y` (la forma que pide el plan)."""
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--front" and i + 1 < len(argv) and argv[i + 1] in ("-Y", "+Y"):
            out.append(f"--front={argv[i + 1]}")
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out


def main(argv=None) -> int:
    argv = _join_front(list(sys.argv[1:] if argv is None else argv))
    ap = argparse.ArgumentParser(description="FBX texturizado → GLB con color, de pie y a escala de la figura")
    ap.add_argument("fbx")
    ap.add_argument("glb")
    ap.add_argument("--front", choices=["+Y", "-Y"], default="+Y", help="hacia dónde mira la figura en el archivo (Güemes: -Y)")
    ap.add_argument("--crown-z", type=float, default=None, help="cota absoluta de la coronilla (unidades del FBX, Z arriba); sin ella, la altura va hasta el punto más alto")
    ap.add_argument("--altura", type=float, default=100.0, help="mm entre los pies y la coronilla (o el punto más alto)")
    a = ap.parse_args(argv)
    try:
        info = convert(a.fbx, a.glb, a.front, a.crown_z, a.altura)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{a.glb}: {info['triangulos']} triángulos, escala {info['escala']:.6f}, alto {info['alto_mm']:.2f} mm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
