"""Línea de comandos del motor (sin interfaz)."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from . import meshio, pipeline as P, solids as S
from .project import Project, demo_project
from .testfigure import make_doll


def run(stl_path: str, project: Project, outdir: str, name: str, progress=print) -> dict:
    mesh, rep = meshio.load_figure(stl_path, project.params.ref_height, project.crown_z)
    for w in rep.warnings:
        progress(f"AVISO: {w}")
    if project.front == "-Y":      # girar 180° sobre Z para que el frente quede en +Y
        mesh.apply_transform([[-1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    mesh = P.align_to_symmetry(mesh, project.symmetry)
    body = S.to_manifold(mesh)
    res = P.build(body, project.limbs, project.params, project.symmetry, progress=progress,
                  trims=project.trims)
    val = P.validate(res, project.params, project.limbs)
    files = P.export(res, outdir, name)
    info = {"carga": rep.__dict__, "construccion": res.report, "validacion": val, "archivos": files}
    with open(os.path.join(outdir, f"{name}_informe.json"), "w") as f:
        json.dump(info, f, indent=2, default=float)
    project.save(os.path.join(outdir, f"{name}.proyecto"))
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="afm", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="figura sintética de ejemplo")
    d.add_argument("--out", default="out/demo")
    b = sub.add_parser("build", help="articular un STL con un proyecto JSON")
    b.add_argument("stl")
    b.add_argument("proyecto")
    b.add_argument("--out", default="out/figura")
    b.add_argument("--name", default=None)
    pr = sub.add_parser("probe", help="generar placa de probetas de articulación (§8.11)")
    pr.add_argument("--out", default="out/probetas")
    pr.add_argument("--clearances", default="0.3,0.4,0.5,0.6")
    pr.add_argument("--deltas", default="-0.2,0,0.2", help="variación del Ø de vástago (mm)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    os.makedirs(args.out, exist_ok=True)
    if args.cmd == "probe":
        from . import probes
        from .params import Params
        p = Params()
        cells = probes.probe_cells(p, clearances=[float(x) for x in args.clearances.split(",")],
                                   shaft_deltas=[float(x) for x in args.deltas.split(",")])
        S.to_trimesh(probes.build_probe(p, cells)).export(os.path.join(args.out, "probetas.stl"))
        with open(os.path.join(args.out, "probetas.txt"), "w") as f:
            f.write(probes.labels_text(cells) + "\n")
        print(probes.labels_text(cells))
        return 0
    if args.cmd == "demo":
        stl = os.path.join(args.out, "muneco.stl")
        make_doll().export(stl)
        info = run(stl, demo_project(), args.out, "muneco")
    else:
        name = args.name or os.path.splitext(os.path.basename(args.stl))[0]
        info = run(args.stl, Project.load(args.proyecto), args.out, name)
    val = info["validacion"]
    print("Validación:", "OK" if val["ok"] else "CON PROBLEMAS")
    for pr in val["problems"]:
        print(" -", pr)
    for av in val.get("avisos", []):
        print(" AVISO:", av)
    print("Holguras mínimas (mm):", val["gaps"])
    return 0 if val["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
