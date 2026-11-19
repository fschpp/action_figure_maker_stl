"""`Colorizer`: el «gancho» que recibe `pipeline.export(colorize=...)` y escribe los archivos de color (plan §6, §8; T14).

Por cada pieza: registro contra la fuente de color (`colorreg`), despliegue (`unwrap`, una celda por triángulo) y horneado
(`colorbake`). Al final `write` deja, sin tocar los STL:

    piezas_color/<fig>_<pieza>.obj  +  .mtl  +  <fig>_<pieza>_color.png      (un OBJ por pieza, con su textura)
    piezas_color/<fig>_ensamblada.obj + .mtl                              (todas las piezas en un OBJ, para Bambu Studio)
    <fig>_ensamblada.glb                                                   (todas las piezas, cada una con su material)
    <fig>_color_informe.json                                               (estado de registro y % con origen por pieza)

`PIL` y `trimesh` se importan dentro de las funciones que los usan (D10). Todo lo que va al informe es número, `str`, `bool`,
lista o diccionario de eso (R13).
"""
from __future__ import annotations

import json
import os
import time

import numpy as np

COLOR_DIR = "piezas_color"


class Colorizer:
    def __init__(self, src, params, *, texel_mm: float = 0.2, eps: float = 0.15, fill="extend", symmetric: bool = False,
                 workers: int = 1):
        from .colorbake import parse_fill

        parse_fill(fill)                                   # falla ya, no después de registrar todas las piezas
        self.src, self.params = src, params
        self.texel_mm, self.eps, self.fill, self.symmetric, self.workers = float(texel_mm), float(eps), fill, bool(symmetric), int(workers)
        self.pieces: dict = {}                             # nombre -> (Unwrapped, BakeResult, Registration, info)

    # ------------------------------------------------------------------ una pieza
    def __call__(self, name: str, mesh, res) -> dict:
        """Registra, despliega y hornea `mesh` (la pieza impresa, en su pose, antes de la orientación de impresión).

        `res` es el `BuildResult` del que sale la pieza. Guarda el resultado y devuelve su informe.
        """
        from .colorbake import bake_piece
        from .colorreg import register_piece
        from .unwrap import unwrap_triangles

        t0 = time.perf_counter()
        reg = register_piece(name, mesh, self.src, res, self.params)
        unw = unwrap_triangles(mesh, texel_mm=self.texel_mm)
        bk = bake_piece(mesh, unw, self.src, reg, eps=self.eps, fill=self.fill, symmetric=self.symmetric, workers=self.workers)
        info = {"estado": reg.status, "pct_origen": round(float(bk.stats["pct_origen"]), 2), "angulo": round(float(reg.angle_deg), 3),
                "puntaje_registro": round(float(reg.score), 3), "texel_mm": round(float(unw.texel_mm), 4),
                "atlas": [int(unw.size[0]), int(unw.size[1])], "triangulos": int(len(unw.faces)),
                "area_mm2": round(float(bk.stats["area_mm2"]), 2), "segundos": round(float(time.perf_counter() - t0), 2)}
        self.pieces[name] = (unw, bk, reg, info)
        return dict(info)

    # ------------------------------------------------------------------ archivos
    @staticmethod
    def _corner_arrays(unw, orientation):
        """Un vértice por esquina de cara (3 por triángulo), para que cada `vt` sea el de su esquina."""
        V = np.asarray(unw.vertices, float)[np.asarray(unw.faces, np.int64)].reshape(-1, 3)
        if orientation is not None:
            T = np.asarray(orientation, float)
            V = V @ T[:3, :3].T + T[:3, 3]
        uv = np.asarray(unw.uv, np.float64).reshape(-1, 2)
        F = np.arange(len(V), dtype=np.int64).reshape(-1, 3)
        return V, uv, F

    @staticmethod
    def _write_assembled_obj(cdir: str, fig_name: str, parts: list) -> str:
        """`<fig>_ensamblada.obj` + `.mtl` en la carpeta de color: todas las piezas en un solo archivo (un `o` y un material por pieza, cada
        material con el PNG de su pieza), para importar la figura entera en Bambu Studio. Los índices `v`/`vt` son globales."""
        base = f"{fig_name}_ensamblada"
        with open(os.path.join(cdir, f"{base}.mtl"), "w", encoding="utf-8") as fh:
            for b, *_rest, png in parts:
                fh.write(f"newmtl {b}\nKa 1 1 1\nKd 1 1 1\nKs 0 0 0\nd 1\nillum 1\nmap_Kd {png}\n\n")
        off = 0
        with open(os.path.join(cdir, f"{base}.obj"), "w", encoding="utf-8") as fh:
            fh.write(f"mtllib {base}.mtl\n")
            for b, V, uv, F, _png in parts:
                fh.write(f"o {b}\n")
                fh.write("".join(f"v {x:.6f} {y:.6f} {z:.6f}\n" for x, y, z in V))
                fh.write("".join(f"vt {u:.6f} {v:.6f}\n" for u, v in uv))
                fh.write(f"usemtl {b}\n")
                fh.write("".join(f"f {a + off + 1}/{a + off + 1} {b2 + off + 1}/{b2 + off + 1} {c + off + 1}/{c + off + 1}\n" for a, b2, c in F))
                off += len(V)
        return f"{base}.obj"

    def write(self, outdir: str, fig_name: str, orientation=None, accesorios_sin_color: int = 0) -> dict:
        """Escribe OBJ + MTL + PNG por pieza, el GLB ensamblado y el informe. `orientation` es la 4×4 de la orientación de impresión
        (la `T` de `print_orientation`): se aplica a las mallas de salida igual que a los STL."""
        import trimesh
        from PIL import Image

        cdir = os.path.join(outdir, COLOR_DIR)
        os.makedirs(cdir, exist_ok=True)
        scene = trimesh.Scene()
        piezas: dict = {}
        parts: list = []                                  # (base, V, uv, F, png) de cada pieza, para el OBJ ensamblado
        for name in sorted(self.pieces):
            unw, bk, _reg, info = self.pieces[name]
            V, uv, F = self._corner_arrays(unw, orientation)
            base = f"{fig_name}_{name}"
            png = f"{base}_color.png"
            img = Image.fromarray(np.ascontiguousarray(bk.atlas), "RGB")
            img.save(os.path.join(cdir, png))
            with open(os.path.join(cdir, f"{base}.mtl"), "w", encoding="utf-8") as fh:
                fh.write(f"newmtl {base}\nKa 1 1 1\nKd 1 1 1\nKs 0 0 0\nd 1\nillum 1\nmap_Kd {png}\n")
            with open(os.path.join(cdir, f"{base}.obj"), "w", encoding="utf-8") as fh:
                fh.write(f"mtllib {base}.mtl\no {base}\n")
                fh.write("".join(f"v {x:.6f} {y:.6f} {z:.6f}\n" for x, y, z in V))
                fh.write("".join(f"vt {u:.6f} {v:.6f}\n" for u, v in uv))
                fh.write(f"usemtl {base}\n")
                fh.write("".join(f"f {a + 1}/{a + 1} {b + 1}/{b + 1} {c + 1}/{c + 1}\n" for a, b, c in F))
            parts.append((base, V, uv, F, png))
            mesh = trimesh.Trimesh(V, F, process=False)
            mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=trimesh.visual.material.PBRMaterial(
                baseColorTexture=img, metallicFactor=0.0, roughnessFactor=1.0, name=base))
            scene.add_geometry(mesh, node_name=name, geom_name=name)
            piezas[name] = {**info, "obj": f"{COLOR_DIR}/{base}.obj", "mtl": f"{COLOR_DIR}/{base}.mtl", "png": f"{COLOR_DIR}/{png}"}
        obj_all = self._write_assembled_obj(cdir, fig_name, parts)
        glb = f"{fig_name}_ensamblada.glb"
        scene.export(os.path.join(outdir, glb))
        out = {"carpeta": COLOR_DIR, "glb": glb, "obj_ensamblado": f"{COLOR_DIR}/{obj_all}", "informe": f"{fig_name}_color_informe.json", "piezas": piezas,
               "accesorios_sin_color": int(accesorios_sin_color)}      # los accesorios de `export` no llevan color
        out["config"] = {"texel_mm": self.texel_mm, "eps": self.eps, "fill": self.fill, "simetrico": self.symmetric,
                         "spacing_fuente_mm": round(float(self.src.spacing_mm), 4)}
        out["sin_origen"] = sorted(n for n, (_u, _b, r, _i) in self.pieces.items() if r.status == "sin_origen")
        out["segundos"] = round(float(sum(i["segundos"] for *_x, i in self.pieces.values())), 2)
        with open(os.path.join(outdir, out["informe"]), "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2, default=float)
        return out
