"""Línea de comandos del motor (sin interfaz)."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from . import __version__, meshio, pipeline as P, solids as S
from .project import Project, demo_project
from .testfigure import make_doll


def run(stl_path: str, project: Project, outdir: str, name: str, progress=print, orient: bool | str = False,
        max_angle: float = 45.0, flat: float | None = None) -> dict:
    mesh, rep = meshio.load_figure(stl_path, project.params.ref_height, project.crown_z)
    for w in rep.warnings:
        progress(f"AVISO: {w}")
    if project.front == "-Y":      # girar 180° sobre Z para que el frente quede en +Y
        mesh.apply_transform([[-1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    mesh = P.align_to_symmetry(mesh, project.symmetry)
    body = S.to_manifold(mesh)
    res = P.build(body, project.limbs, project.params, project.symmetry, progress=progress,
                  trims=project.trims, head=project.head, joints=project.joints)
    val = P.validate(res, project.params, project.limbs, joints=project.joints)
    files = P.export(res, outdir, name, orient=orient, max_angle=max_angle, flat=flat, params=project.params)
    info = {"carga": rep.__dict__, "construccion": res.report, "validacion": val, "archivos": files}
    with open(os.path.join(outdir, f"{name}_informe.json"), "w") as f:
        json.dump(info, f, indent=2, default=float)
    project.save(os.path.join(outdir, f"{name}.proyecto"))
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="afm", description=__doc__)
    ap.add_argument("--version", action="version", version=f"afm {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="figura sintética de ejemplo")
    d.add_argument("--out", default="out/demo")
    d.add_argument("--orient", action="store_true", help="exportar apoyado en la cama en la pose de la vista previa (sentada, cabeza arriba)")
    d.add_argument("--orient-auto", action="store_true", help="exportar en la orientación de menor voladizo (puede dar vuelta la figura)")
    b = sub.add_parser("build", help="articular un STL con un proyecto JSON")
    b.add_argument("stl")
    b.add_argument("proyecto")
    b.add_argument("--out", default="out/figura")
    b.add_argument("--name", default=None)
    b.add_argument("--orient", action="store_true", help="exportar apoyado en la cama en la pose de la vista previa (sentada, cabeza arriba)")
    b.add_argument("--sin-soportes", nargs="?", type=float, const=-1.0, default=None, metavar="MM",
                   help="aplanar piernas y cola con un corte horizontal a MM sobre la cola (por defecto Params.flat_depth) y "
                        "girar los brazos hacia adelante hasta apoyar en ese plano")
    b.add_argument("--orient-auto", action="store_true", help="exportar en la orientación de menor voladizo (puede dar vuelta la figura)")
    b.add_argument("--cabeza", default=None, metavar="STL",
                   help="usar esta cabeza en lugar de la original (el proyecto debe tener el plano del cuello)")
    b.add_argument("--cabeza-giro", type=float, default=None, metavar="GRADOS", help="girar la cabeza importada sobre su eje vertical")
    b.add_argument("--cabeza-arriba", default=None, choices=("+Z", "-Z", "+Y", "-Y", "+X", "-X"),
                   help="eje de la cabeza importada que apunta hacia arriba (por defecto +Z)")
    b.add_argument("--cadera-referencia", action="store_true",
                   help="usar el pasador de cadera de la figura de referencia (Ø4,8 / cabeza Ø9,25 × 2,44, mm absolutos)")
    b.add_argument("--max-angle", type=float, default=45.0, help="ángulo de voladizo tolerado (grados desde la vertical)")
    pr = sub.add_parser("probe", help="generar placa de probetas de articulación (§8.11)")
    pr.add_argument("--out", default="out/probetas")
    pr.add_argument("--clearances", default=None, help="holguras radiales a probar (mm)")
    pr.add_argument("--deltas", default=None, help="variación del Ø de vástago (hongo) o de bulón (bisagra), en mm")
    pr.add_argument("--tipo", default="hongo", choices=("hongo", "bisagra"),
                    help="hongo = cadera/hombro (§8.11); bisagra = rodilla/codo (plan de articulaciones, Fase 6)")
    pr.add_argument("--axiales", default=None, help="solo bisagra: holguras axiales a probar, p. ej. 0.25,0.35,0.45")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    os.makedirs(args.out, exist_ok=True)
    if args.cmd == "probe":
        from . import probes
        from .params import Params
        p = Params()
        fl = lambda t: [float(x) for x in t.split(",")]
        if args.tipo == "bisagra":
            kw = {"clearances": fl(args.clearances)} if args.clearances else {}
            if args.deltas:
                kw["pin_deltas"] = fl(args.deltas)
            if args.axiales:
                kw["axial_clearances"] = fl(args.axiales)
            cells = probes.hinge_probe_cells(p, **kw)
            mesh, text = probes.build_hinge_probe(p, cells), probes.hinge_labels_text(cells)
        else:
            cells = probes.probe_cells(p, clearances=fl(args.clearances or "0.3,0.4,0.5,0.6"),
                                       shaft_deltas=fl(args.deltas or "-0.2,0,0.2"))
            mesh, text = probes.build_probe(p, cells), probes.labels_text(cells)
        S.to_trimesh(mesh).export(os.path.join(args.out, "probetas.stl"))
        with open(os.path.join(args.out, "probetas.txt"), "w") as f:
            f.write(text + "\n")
        print(text)
        return 0
    if args.cmd == "demo":
        stl = os.path.join(args.out, "muneco.stl")
        make_doll().export(stl)
        info = run(stl, demo_project(), args.out, "muneco", orient="auto" if args.orient_auto else args.orient)
    else:
        name = args.name or os.path.splitext(os.path.basename(args.stl))[0]
        project = Project.load(args.proyecto)
        if args.cabeza or args.cabeza_giro is not None or args.cabeza_arriba:
            if project.head is None:
                ap.error("el proyecto no tiene cabeza (plano del cuello): --cabeza necesita definirla primero")
            import dataclasses
            project.head = dataclasses.replace(
                project.head, replacement_path=args.cabeza or project.head.replacement_path,
                replacement_yaw=project.head.replacement_yaw if args.cabeza_giro is None else args.cabeza_giro,
                replacement_up=args.cabeza_arriba or project.head.replacement_up)
        if args.cadera_referencia:
            project.params = project.params.with_hip_reference()
        flat = args.sin_soportes
        if flat is not None and flat < 0:
            flat = project.params.flat_depth
        info = run(args.stl, project, args.out, name, orient="auto" if args.orient_auto else args.orient,
                   max_angle=args.max_angle, flat=flat)
    val = info["validacion"]
    print("Validación:", "OK" if val["ok"] else "CON PROBLEMAS")
    for pr in val["problems"]:
        print(" -", pr)
    for av in val.get("avisos", []) + val.get("avisos_pasador", []):
        print(" AVISO:", av)
    print("Holguras mínimas (mm):", val["gaps"])
    ori = info["archivos"].get("orientacion")
    if ori:
        print(f"Orientación de impresión: {ori['etiqueta']} — voladizo {ori['voladizo_mm2']} mm², "
              f"base {ori['base_mm2']} mm²")
    ss = info["archivos"].get("sin_soportes")
    if ss:
        giro = ", ".join(f"{k} {v:g}°" for k, v in ss["giro_brazos_grados"].items())
        print(f"Sin soportes: corte {ss['corte_sobre_cola_mm']:g} mm sobre la cola (z = {ss['altura_corte_z']}); "
              f"brazos girados hacia adelante: {giro or '0°'}")
        for av in ss["avisos"]:
            print(" AVISO:", av)
    return 0 if val["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
