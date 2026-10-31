"""Accesorios (armas) que viajan con la figura: referencia en el proyecto, carga desde el catálogo y exportación.

El accesorio no va en el puño: sale como STL aparte en `piezas/`, apoyado en la cama y en mm reales, y además se suma al
STL ensamblado al lado de la figura (a su derecha, con una separación), para tener la figura completa con su arma en un archivo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import trimesh


class AccessoryError(ValueError):
    """Un accesorio del proyecto no se puede cargar (catálogo o pieza inexistentes, pieza de otro tipo…)."""


@dataclass
class AccessoryRef:
    """Accesorio elegido en un proyecto: pieza de un catálogo. El `.proyecto` guarda la referencia, no la malla."""
    catalogo: str                   # carpeta del catálogo (ruta absoluta)
    pieza: str                      # «id@versión»
    sha256: str = ""                # hash de la pieza al elegirla: si cambia en el catálogo, se avisa

    def to_dict(self) -> dict:
        return {"catalogo": self.catalogo, "pieza": self.pieza, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, d: dict) -> "AccessoryRef":
        return cls(catalogo=str(d["catalogo"]), pieza=str(d["pieza"]), sha256=str(d.get("sha256", "")))


def print_pose(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Copia apoyada en la cama (z mínimo = 0). El marco del accesorio ya trae el arma acostada con el fondo plano."""
    m = mesh.copy()
    m.apply_translation([0.0, 0.0, -float(m.bounds[0][2])])
    return m


def load_ref(ref: AccessoryRef) -> tuple:
    """(meta, malla en el marco del accesorio, avisos) de la pieza `accesorio` que nombra `ref`."""
    from .catalog import Catalog, CatalogError

    try:
        cat = Catalog.open(ref.catalogo)
        meta, mesh = cat.load(ref.pieza)
    except (CatalogError, OSError) as exc:
        raise AccessoryError(f"accesorio «{ref.pieza}» en «{ref.catalogo}»: {exc}") from exc
    if meta.tipo != "accesorio":
        raise AccessoryError(f"«{meta.ref}» es una pieza «{meta.tipo}», no un accesorio")
    warns = []
    if ref.sha256 and meta.sha256 and ref.sha256 != meta.sha256:
        warns.append(f"«{meta.ref}» cambió en el catálogo desde que se eligió para este proyecto")
    return meta, mesh, warns


def project_accessory_fit(project, meta) -> dict:
    """Cómo se lleva el accesorio con la mano pegada del proyecto (la clave «agarre» del `.mano.json` de su STL)."""
    from .recipe import accessory_fit_grip

    ag = None
    if project.hand is not None and project.hand.path and os.path.exists(project.hand.path):
        from .hand import load_standard_hand

        try:
            ag = (load_standard_hand(project.hand.path)[1] or {}).get("agarre")
        except Exception:                          # noqa: BLE001 — una mano ilegible no tiene agarre
            ag = None
        return accessory_fit_grip(ag, meta, label=os.path.basename(project.hand.path))
    return accessory_fit_grip(None, meta, label=None)


def place_beside(asm: trimesh.Trimesh, extras: list[trimesh.Trimesh], gap: float = 10.0) -> list[trimesh.Trimesh]:
    """Los `extras` (en pose de impresión) puestos a la derecha de la figura ensamblada, a la altura de su cama, uno tras otro."""
    lo, hi = asm.bounds
    x = float(hi[0]) + gap
    yc = float((lo[1] + hi[1]) / 2)
    out = []
    for m in extras:
        m = m.copy()
        b = m.bounds
        m.apply_translation([x - b[0][0], yc - (b[0][1] + b[1][1]) / 2, float(lo[2]) - b[0][2]])
        out.append(m)
        x = float(m.bounds[1][0]) + gap
    return out


def write_files(items: list[tuple[str, trimesh.Trimesh]], outdir: str, name: str) -> list[str]:
    """Escribe `piezas/<name>_<id>.stl` (pose de impresión) de cada (id, malla); devuelve las rutas relativas."""
    folder = os.path.join(outdir, "piezas")
    os.makedirs(folder, exist_ok=True)
    rel = []
    for pid, mesh in items:
        fname = f"{name}_{pid}.stl"
        mesh.export(os.path.join(folder, fname))
        rel.append(f"piezas/{fname}")
    return rel


def load_project_items(project, progress=None) -> list[dict]:
    """Fichas de los accesorios del proyecto: {meta, mesh (pose de impresión), ficha (sin `archivo`)}; avisa lo que cambió."""
    say = progress or (lambda text: None)
    out = []
    for ref in project.accesorios:
        meta, mesh, warns = load_ref(ref)
        fit = project_accessory_fit(project, meta)
        for w in warns:
            say(f"AVISO: {w}")
        out.append({"meta": meta, "mesh": print_pose(mesh),
                    "ficha": {"pieza": meta.ref, "id": meta.id, "version": meta.version, "sha256": meta.sha256,
                              "origen": meta.origen.get("figura") or meta.origen.get("nota") or "", "compat": fit["nivel"],
                              "motivos": fit["motivos"], "avisos": fit["avisos"] + warns}})
    return out


def finish_export(items: list[dict], outdir: str, name: str, progress=None) -> list[dict]:
    """Escribe los STL de los accesorios, completa cada ficha con su archivo, avisa y los suma a la lista de piezas si hay."""
    from .pipeline import attach_accessories_to_parts_list

    say = progress or (lambda text: None)
    if not items:
        return []
    say("Exportando los accesorios…")
    rel = write_files([(i["ficha"]["id"], i["mesh"]) for i in items], outdir, name)
    fichas = []
    for i, path in zip(items, rel):
        f = {**i["ficha"], "archivo": path}
        fichas.append(f)
        for w in list(f["motivos"]) + list(f["avisos"]):
            say(f"AVISO ({f['pieza']}): {w}")
    attach_accessories_to_parts_list(outdir, name, fichas)
    return fichas
