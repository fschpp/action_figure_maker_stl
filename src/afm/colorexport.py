"""`Colorizer`: el «gancho» que recibe `pipeline.export(colorize=...)` y escribe los archivos de color (plan §6, §8; T14).

Por cada pieza: registro contra la fuente de color (`colorreg`), despliegue (`unwrap`, una celda por triángulo) y horneado
(`colorbake`). Al final `write` deja, sin tocar los STL:

    piezas_color/<fig>_<pieza>.obj  +  .mtl  +  <fig>_<pieza>_color.png      (un OBJ por pieza, con su textura)
    piezas_color/<fig>_ensamblada.obj + .mtl                              (todas las piezas en un OBJ, para Bambu Studio)
    <fig>_ensamblada.glb                                                   (todas las piezas, cada una con su material)
    <fig>_color_informe.json                                               (estado de registro y % con origen por pieza)

Los OBJ y el GLB se escriben con Y hacia arriba (convención de OBJ y glTF, `frame.Z_UP_TO_Y_UP`); los STL siguen en Z arriba. Así
Bambu Studio, Blender y los visores de glTF dejan la figura parada y mirando hacia donde mira el STL.

`PIL` y `trimesh` se importan dentro de las funciones que los usan (D10). Todo lo que va al informe es número, `str`, `bool`,
lista o diccionario de eso (R13).
"""
from __future__ import annotations

import json
import os
import time

import numpy as np

from .frame import Z_UP_TO_Y_UP

COLOR_DIR = "piezas_color"


class Colorizer:
    def __init__(self, src, params, *, texel_mm: float = 0.2, eps: float = 0.15, fill="extend", symmetric: bool = False,
                 workers: int = 1, sources: dict | None = None, palette_report: dict | None = None,
                 piece_roles: dict | None = None):
        from .colorbake import parse_fill

        parse_fill(fill)                                   # falla ya, no después de registrar todas las piezas
        self.src, self.params = src, params
        self.sources: dict = dict(sources or {})           # clave de pieza (sin _izq/_der) -> ColorSource propia (T31)
        self.texel_mm, self.eps, self.fill, self.symmetric, self.workers = float(texel_mm), float(eps), fill, bool(symmetric), int(workers)
        self.pieces: dict = {}                             # nombre -> (Unwrapped, BakeResult, Registration, info)
        self.source_errors: dict = {}                      # clave de pieza -> por qué no se pudo usar su fuente propia (T34)
        # T43: color por roles. `palette_report` = {"hash", "roles": {id: hex}} de la paleta de la línea usada; `piece_roles` = clave -> roles
        # que usa la pieza. Solo van al informe (no cambian el color): sin ellos el informe es el de antes.
        self.palette_report: dict | None = dict(palette_report) if palette_report else None
        self.piece_roles: dict = {k: list(v) for k, v in (piece_roles or {}).items()}
        self._last_write: tuple | None = None              # T45: argumentos del último `write`, para que `recolor` reescriba los mismos archivos

    # ------------------------------------------------------------------ fuente de cada pieza
    def source_for(self, name: str, res=None):
        """`(fuente, origen)` de la pieza `name`: la propia (`sources[clave]`, clave sin sufijo `_izq/_der`) o la de la figura.

        `origen` es `"pieza"`, `"figura"` o `"plano"` (no hay ninguna fuente). Un valor de `sources` puede ser un PROVEEDOR, un
        `callable(res)` que arma la `ColorSource` cuando la figura ya se construyó (la cabeza importada necesita la transformación
        que calcula `fit_replacement`, T34). Se resuelve la primera vez y queda guardado; si falla (el FBX no coincide con el STL)
        la pieza sigue con la fuente de la figura y el motivo queda en `source_errors[clave]`."""
        from .colorreg import _split_name

        key, side = _split_name(name)
        slot = name if name in self.sources else key         # T43: un lado con su propia fuente (la reflejada de `_der`) va antes que la de la clave
        if slot not in self.sources and res is not None:
            # Pieza de una articulación (antebrazo, muslo, pantorrilla…): la fuente es la de su miembro (`brazo`, `pierna`), que es la pieza de catálogo entera.
            from .colorreg import _joint_info, _owner

            ji = _joint_info(_owner(res, side), key)
            if ji is not None:
                limb = ji[1].get("limb")
                slot = next((c for c in ((f"{limb}_{side}" if side else None), limb) if c and c in self.sources), slot)
        own = self.sources.get(slot)
        if own is not None and not hasattr(own, "query"):
            try:
                own = own(res)
            except (ValueError, OSError) as exc:
                self.source_errors[slot] = str(exc)
                own = None
            if own is None:
                self.sources.pop(slot, None)
            else:
                self.sources[slot] = own
        if own is not None:
            return own, "pieza"
        return (self.src, "figura") if self.src is not None else (None, "plano")

    # ------------------------------------------------------------------ una pieza
    def __call__(self, name: str, mesh, res) -> dict:
        """Registra, despliega y hornea `mesh` (la pieza impresa, en su pose, antes de la orientación de impresión).

        `res` es el `BuildResult` del que sale la pieza. Guarda el resultado y devuelve su informe.
        """
        from .colorbake import bake_piece
        from .colorreg import register_piece
        from .unwrap import unwrap_triangles

        t0 = time.perf_counter()
        src, origen = self.source_for(name, res)
        if src is None:                                   # figura sin color y pieza sin fuente propia: todo plano (T34)
            from .colorreg import Registration

            reg = Registration("sin_origen", np.eye(4), 0.0, 0.0, None)
        else:
            reg = register_piece(name, mesh, src, res, self.params)
        unw = unwrap_triangles(mesh, texel_mm=self.texel_mm)
        bk = bake_piece(mesh, unw, src, reg, eps=self.eps, fill=self.fill, symmetric=self.symmetric, workers=self.workers)
        info = {"estado": reg.status, "fuente": "plano" if reg.status == "sin_origen" else origen, "pct_origen": round(float(bk.stats["pct_origen"]), 2), "angulo": round(float(reg.angle_deg), 3),
                "puntaje_registro": round(float(reg.score), 3), "texel_mm": round(float(unw.texel_mm), 4),
                "atlas": [int(unw.size[0]), int(unw.size[1])], "triangulos": int(len(unw.faces)),
                "area_mm2": round(float(bk.stats["area_mm2"]), 2), "segundos": round(float(time.perf_counter() - t0), 2)}
        from .colorreg import _split_name

        ekey = name if name in self.source_errors else _split_name(name)[0]
        if ekey in self.source_errors:
            info["aviso_fuente"] = self.source_errors[ekey]
        if info["fuente"] == "pieza" and self.piece_roles.get(_split_name(name)[0]):
            info["roles"] = list(self.piece_roles[_split_name(name)[0]])
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
        V = V @ Z_UP_TO_Y_UP.T                               # OBJ y GLB van con Y arriba (ver el docstring del módulo)
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

    # ------------------------------------------------------------------ recolor (T45)
    def recolor(self, palette, overrides: dict | None = None, *, rewrite: bool = True) -> dict:
        """Cambia el color de lo ya horneado con otra `palette` (más `overrides` = `{rol: "#RRGGBB"}`, solo para esta corrida) **sin registrar ni
        hornear de nuevo** (plan §5.5, T45): por cada pieza cuya fuente traía etiquetas de rol, `atlas = lut[etiquetas]` en los texeles que
        tienen rol; los de etiqueta 255 (color plano del relleno) y los que ningún triángulo cubre no cambian. El resultado es el mismo,
        píxel por píxel, que el de volver a correr el color con esa paleta. Las piezas sin etiquetas (fuente RGB, plana) quedan como están.

        Todo o nada: si a la paleta le falta un rol que usa alguna pieza, `PaletteError` (lista los que faltan) y no cambia nada. Las fuentes
        ya resueltas también se recolorean, así una pieza que se hornee después sale con la paleta nueva, y el informe pasa a llevar la paleta
        **usada** (`hash` y `roles`; con `overrides`, también `cambios`). Con `rewrite` (por defecto) y un `write` previo, reescribe los mismos
        PNG, el GLB y el informe (los OBJ/MTL no cambian: salen byte a byte iguales y no se vuelven a escribir); los STL no se tocan nunca. Devuelve `{"piezas": [recoloreadas], "sin_etiquetas": [...], "segundos": s}` y,
        si reescribió, `"salida"` con lo que devuelve `write`."""
        import dataclasses

        t0 = time.perf_counter()
        used = palette.with_overrides(overrides or {})
        luts = {}                                          # nombre -> LUT de la pieza (se calculan todas antes de tocar nada)
        for name, (_unw, bk, _reg, _info) in self.pieces.items():
            if bk.labels is not None:
                luts[name] = used.lut(bk.label_roles)
        srcs: dict = {}                                    # fuentes con etiquetas, sin repetir objetos
        for s in [self.src, *self.sources.values()]:
            if s is not None and hasattr(s, "query") and getattr(s, "sample_label", None) is not None:
                srcs[id(s)] = (s, used.lut(s.label_roles))
        for name, lut in luts.items():
            unw, bk, reg, info = self.pieces[name]
            atlas = bk.atlas.copy()
            has_role = bk.mask & (bk.labels != 255)
            atlas[has_role] = lut[bk.labels[has_role]]
            self.pieces[name] = (unw, dataclasses.replace(bk, atlas=atlas), reg, info)
        for s, lut in srcs.values():
            s.recolor(lut)
        self.palette_report = {"hash": used.digest(), "roles": {r.id: r.hex for r in used.roles}}
        if overrides:
            self.palette_report["cambios"] = {str(k): used.hex_of(k) for k in sorted(overrides)}
        out: dict = {"piezas": sorted(luts), "sin_etiquetas": sorted(n for n in self.pieces if n not in luts)}
        if rewrite and self._last_write is not None:
            out["salida"] = self.write(*self._last_write, textures_only=True)
        out["segundos"] = round(float(time.perf_counter() - t0), 3)
        return out

    def write(self, outdir: str, fig_name: str, orientation=None, accesorios_sin_color: int = 0, *, textures_only: bool = False) -> dict:
        """Escribe OBJ + MTL + PNG por pieza, el GLB ensamblado y el informe. `orientation` es la 4×4 de la orientación de impresión
        (la `T` de `print_orientation`): se aplica a las mallas de salida igual que a los STL.

        `textures_only` (lo usa `recolor`, T45): no reescribe los OBJ ni los MTL (por pieza y ensamblado), que solo dependen de la geometría
        y de los nombres de los PNG y por eso salen byte a byte iguales; escribe los PNG, el GLB y el informe. Devuelve lo mismo que sin él."""
        import trimesh
        from PIL import Image

        self._last_write = (outdir, fig_name, orientation, accesorios_sin_color)
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
            if not textures_only:
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
        obj_all = f"{fig_name}_ensamblada.obj" if textures_only else self._write_assembled_obj(cdir, fig_name, parts)
        glb = f"{fig_name}_ensamblada.glb"
        scene.export(os.path.join(outdir, glb))
        out = {"carpeta": COLOR_DIR, "glb": glb, "obj_ensamblado": f"{COLOR_DIR}/{obj_all}", "informe": f"{fig_name}_color_informe.json", "piezas": piezas,
               "accesorios_sin_color": int(accesorios_sin_color)}      # los accesorios de `export` no llevan color
        out["config"] = {"texel_mm": self.texel_mm, "eps": self.eps, "fill": self.fill, "simetrico": self.symmetric,
                         "spacing_fuente_mm": None if self.src is None else round(float(self.src.spacing_mm), 4)}
        out["config"]["fuentes_propias"] = sorted(self.sources)
        if self.source_errors:
            out["config"]["errores_fuentes"] = dict(self.source_errors)
        if self.palette_report:
            out["paleta"] = dict(self.palette_report)
        out["sin_origen"] = sorted(n for n, (_u, _b, r, _i) in self.pieces.items() if r.status == "sin_origen")
        out["segundos"] = round(float(sum(i["segundos"] for *_x, i in self.pieces.values())), 2)
        with open(os.path.join(outdir, out["informe"]), "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2, default=float)
        return out
