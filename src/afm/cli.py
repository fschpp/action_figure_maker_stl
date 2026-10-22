"""Línea de comandos del motor (sin interfaz)."""
from __future__ import annotations

import argparse
import dataclasses
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
                  trims=project.trims, head=project.head, joints=project.joints, waist=project.waist,
                  hand=project.hand)
    val = P.validate(res, project.params, project.limbs, joints=project.joints)
    files = P.export(res, outdir, name, orient=orient, max_angle=max_angle, flat=flat, params=project.params)
    info = {"carga": rep.__dict__, "construccion": res.report, "validacion": val, "archivos": files}
    with open(os.path.join(outdir, f"{name}_informe.json"), "w") as f:
        json.dump(info, f, indent=2, default=float)
    project.save(os.path.join(outdir, f"{name}.proyecto"))
    return info


def run_extract_hand(stl_path: str, project: Project, points, out_dir: str, name: str = "mano",
                     invert: bool = False, progress=print) -> dict:
    """Corta la mano de la figura por el plano de muñeca (3 puntos en el marco de trabajo de la GUI) y la guarda
    como mano estándar (`<out>/<name>.stl` + `<name>.mano.json`). Usa la misma carga que `run`: altura, frente y
    simetría del proyecto, así los puntos elegidos en la GUI valen tal cual."""
    from . import hand as H

    mesh, rep = meshio.load_figure(stl_path, project.params.ref_height, project.crown_z)
    for w in rep.warnings:
        progress(f"AVISO: {w}")
    if project.front == "-Y":
        mesh.apply_transform([[-1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    mesh = P.align_to_symmetry(mesh, project.symmetry)
    hand, split = H.extract_standard_hand(S.to_manifold(mesh), points, invert=invert)
    meta = H.hand_meta(split, height_mm=project.params.ref_height, source=os.path.basename(stl_path))
    path = os.path.join(out_dir, f"{name}.stl")
    H.save_standard_hand(path, hand, meta)
    meta["archivo"] = path
    if abs(project.params.ref_height - H.LINE_HEIGHT_MM) > 0.5:
        meta["aviso_altura"] = (f"la figura está a {project.params.ref_height:g} mm y la línea usa {H.LINE_HEIGHT_MM:g} mm: "
                                "la mano estándar quedó medida a otra escala")
    return meta


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
    b.add_argument("--rellenar-muslos", action="store_true",
                   help="con --sin-soportes: el muslo se queda con la loncha de pelvis que saca el corte plano y la pelvis "
                        "se talla de nuevo alrededor (Params.flat_fill_hip)")
    b.add_argument("--orient-auto", action="store_true", help="exportar en la orientación de menor voladizo (puede dar vuelta la figura)")
    b.add_argument("--cabeza", default=None, metavar="STL",
                   help="usar esta cabeza en lugar de la original (el proyecto debe tener el plano del cuello)")
    b.add_argument("--cabeza-giro", type=float, default=None, metavar="GRADOS", help="girar la cabeza importada sobre su eje vertical")
    b.add_argument("--cabeza-arriba", default=None, choices=("+Z", "-Z", "+Y", "-Y", "+X", "-X"),
                   help="eje de la cabeza importada que apunta hacia arriba (por defecto +Z)")
    b.add_argument("--cabeza-rx", type=float, default=None, metavar="GRADOS", help="girar la cabeza importada sobre X (tras --cabeza-arriba)")
    b.add_argument("--cabeza-ry", type=float, default=None, metavar="GRADOS", help="girar la cabeza importada sobre Y")
    b.add_argument("--cabeza-escala", type=float, default=None, metavar="FACTOR",
                   help="factor manual sobre la escala automática (1 = base igual a la del cuello; 0,2–5)")
    b.add_argument("--cabeza-pasador", type=float, nargs=2, default=None, metavar=("Y", "Z"),
                   help="centro del pasador del cuello visto de costado (y, z en el marco de trabajo, x = 0); "
                        "se proyecta sobre el plano del cuello")
    b.add_argument("--cabeza-corte", type=float, nargs=9, default=None, metavar="N",
                   help="plano de 3 puntos que corta la base de la cabeza importada: x1 y1 z1 x2 y2 z2 x3 y3 z3, "
                        "en coordenadas del STL de la cabeza (lo de abajo del plano se descarta)")
    b.add_argument("--mano", default=None, metavar="STL",
                   help="mano estándar (guardada con `afm extraer-mano` o con la GUI) que reemplaza a la mano del modelo; "
                        "usa los puntos de --mano-puntos o, si no se dan, los que ya tenga el proyecto")
    b.add_argument("--mano-puntos", type=float, nargs=9, default=None, metavar="N",
                   help="plano de muñeca del modelo: x1 y1 z1 x2 y2 z2 x3 y3 z3 (marco de la GUI, cualquiera de los dos brazos). "
                        "El punto 1 va en el mismo lugar anatómico que al extraer la mano (p. ej. el lado del pulgar)")
    b.add_argument("--mano-giro", type=float, default=None, metavar="GRADOS", help="giro extra de la mano alrededor de la muñeca")
    b.add_argument("--mano-invertir", action="store_true", help="la mano del modelo es el lado grande del corte")
    b.add_argument("--mano-union", default=None, choices=("solape", "giro"),
                   help="solape = la mano queda soldada al brazo (por defecto); giro = pieza aparte con pasador que gira libre dentro del brazo")
    b.add_argument("--mano-solape", type=float, default=None, metavar="MM",
                   help="cuánto se hunde la base de la mano en el brazo (por defecto 0,5 mm)")
    b.add_argument("--cadera-referencia", action="store_true",
                   help="usar el pasador de cadera de la figura de referencia (Ø4,8 / cabeza Ø9,25 × 2,44, mm absolutos)")
    b.add_argument("--max-angle", type=float, default=45.0, help="ángulo de voladizo tolerado (grados desde la vertical)")
    e = sub.add_parser("extraer-mano", help="cortar la mano de una figura por un plano de muñeca de 3 puntos y guardarla como mano estándar")
    e.add_argument("stl")
    e.add_argument("proyecto", help="proyecto de la figura (altura, frente y simetría: los puntos se miden en ese marco)")
    e.add_argument("--puntos", type=float, nargs=9, required=True, metavar="N",
                   help="x1 y1 z1 x2 y2 z2 x3 y3 z3 de la muñeca izquierda (+X). El punto 1 es la referencia de giro: "
                        "marcalo siempre en el mismo lugar (p. ej. lado del pulgar)")
    e.add_argument("--out", default="out/mano")
    e.add_argument("--name", default="mano")
    e.add_argument("--invertir", action="store_true", help="la mano es el lado grande del corte (por defecto es el chico)")
    pr = sub.add_parser("probe", help="generar placa de probetas de articulación (§8.11)")
    pr.add_argument("--out", default="out/probetas")
    pr.add_argument("--clearances", default=None, help="holguras radiales a probar (mm)")
    pr.add_argument("--deltas", default=None, help="variación del Ø de vástago (hongo) o de bulón (bisagra), en mm")
    pr.add_argument("--tipo", default="hongo", choices=("hongo", "bisagra", "clic"),
                    help="hongo = cadera/hombro (§8.11); bisagra = rodilla/codo (plan de articulaciones, Fase 6); "
                         "clic = disco con muescas + lengüetas (PLAN_mecanismo_clic_hombro_cadera.md, Fase 0)")
    pr.add_argument("--boquilla", type=float, default=0.4, help="solo clic: boquilla (mm); fija el espesor y largo de la lengüeta")
    pr.add_argument("--gaps", default=None, help="solo clic: holguras punta/muesca; una placa por valor (por defecto 0.2,0.3,0.4)")
    pr.add_argument("--espesores", default=None, help="solo clic: espesores de lengüeta a probar (mm)")
    pr.add_argument("--axiales", default=None, help="solo bisagra: holguras axiales a probar, p. ej. 0.25,0.35,0.45")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    os.makedirs(args.out, exist_ok=True)
    if args.cmd == "extraer-mano":
        from .hand import HandError
        try:
            meta = run_extract_hand(args.stl, Project.load(args.proyecto), tuple(tuple(args.puntos[i:i + 3]) for i in (0, 3, 6)),
                                    args.out, args.name, invert=args.invertir)
        except HandError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"Mano estándar guardada en {meta['archivo']}")
        print(f"  lado de origen: {meta['lado_origen']} · muñeca Ø{2 * meta['radio_muneca_mm']:.2f} mm · "
              f"largo {meta['largo_mm']:.1f} mm · figura a {meta['altura_figura_mm']:g} mm")
        if meta.get("aviso_altura"):
            print("  AVISO:", meta["aviso_altura"])
        return 0
    if args.cmd == "probe":
        from . import probes
        from .params import Params
        p = Params()
        fl = lambda t: [float(x) for x in t.split(",")]
        if args.tipo == "clic":
            from . import detent as DT
            import dataclasses
            p = dataclasses.replace(p, nozzle=args.boquilla, detent=DT.spec_for_nozzle(args.boquilla))
            thick = fl(args.espesores) if args.espesores else (
                (1.4, 1.6, 1.8) if args.boquilla >= 0.7 else probes.DEFAULT_DETENT_THICKNESSES)
            lines = []
            for g in fl(args.gaps or "0.2,0.3,0.4"):
                cells = probes.detent_probe_cells(p, thicknesses=thick, gap=g)
                fn = f"probetas_clic_b{args.boquilla:g}_gap{g:g}"
                S.to_trimesh(probes.build_detent_probe(p, cells)).export(os.path.join(args.out, fn + ".stl"))
                lines.append(f"== {fn}.stl (boquilla {args.boquilla:g} mm, holgura de punta {g:g} mm) ==\n"
                             + probes.detent_labels_text(cells))
            text = "\n\n".join(lines)
            with open(os.path.join(args.out, f"probetas_clic_b{args.boquilla:g}.txt"), "w") as f:
                f.write(text + "\n")
            print(text)
            return 0
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
        head_opts = (args.cabeza, args.cabeza_giro, args.cabeza_arriba, args.cabeza_rx, args.cabeza_ry,
                     args.cabeza_escala, args.cabeza_corte, args.cabeza_pasador)
        if any(o is not None and o != "" for o in head_opts):
            if project.head is None:
                ap.error("el proyecto no tiene cabeza (plano del cuello): --cabeza necesita definirla primero")
            import dataclasses
            project.head = dataclasses.replace(
                project.head, replacement_path=args.cabeza or project.head.replacement_path,
                replacement_yaw=project.head.replacement_yaw if args.cabeza_giro is None else args.cabeza_giro,
                replacement_up=args.cabeza_arriba or project.head.replacement_up,
                replacement_rx=project.head.replacement_rx if args.cabeza_rx is None else args.cabeza_rx,
                replacement_ry=project.head.replacement_ry if args.cabeza_ry is None else args.cabeza_ry,
                replacement_scale=project.head.replacement_scale if args.cabeza_escala is None else args.cabeza_escala,
                replacement_cut_points=project.head.replacement_cut_points if args.cabeza_corte is None
                else tuple(tuple(args.cabeza_corte[i:i + 3]) for i in (0, 3, 6)))
            if args.cabeza_pasador is not None:
                import numpy as np

                from .head import neck_plane
                n, o = neck_plane(project.head).normal, neck_plane(project.head).origin
                c = np.array([0.0, *args.cabeza_pasador])
                project.head = dataclasses.replace(project.head, pin_center=tuple(c - ((c - o) @ n) * n))
        hand_opts = (args.mano, args.mano_puntos, args.mano_giro, args.mano_solape, args.mano_union)
        if any(o is not None for o in hand_opts) or args.mano_invertir:
            from .hand import DEFAULT_OVERLAP_MM, HandSpec
            cur = project.hand
            path = os.path.abspath(args.mano) if args.mano else (cur.path if cur else None)
            pts = (tuple(tuple(args.mano_puntos[i:i + 3]) for i in (0, 3, 6)) if args.mano_puntos is not None
                   else (cur.points if cur else None))
            if path is None:
                ap.error("el proyecto no tiene mano estándar: pasá --mano STL")
            if pts is None:
                ap.error("falta el plano de muñeca del modelo: pasá --mano-puntos x1 y1 z1 x2 y2 z2 x3 y3 z3")
            yaw = args.mano_giro if args.mano_giro is not None else (cur.yaw if cur else 0.0)
            overlap = args.mano_solape if args.mano_solape is not None else (cur.overlap if cur else DEFAULT_OVERLAP_MM)
            project.hand = HandSpec(path=path, points=pts, yaw=yaw, overlap=overlap,
                                    invert=bool(args.mano_invertir or (cur.invert if cur else False)),
                                    join=args.mano_union or (cur.join if cur else "solape"))
        if args.cadera_referencia:
            project.params = project.params.with_hip_reference()
        if args.rellenar_muslos:
            project.params = dataclasses.replace(project.params, flat_fill_hip=True)
        flat = args.sin_soportes
        if flat is not None and flat < 0:
            flat = project.params.flat_depth
        from .hand import HandError
        try:
            info = run(args.stl, project, args.out, name, orient="auto" if args.orient_auto else args.orient,
                       max_angle=args.max_angle, flat=flat)
        except HandError as exc:
            print(f"ERROR (mano): {exc}", file=sys.stderr)
            return 2
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
        from .gui.controller import fill_hip_message
        if args.rellenar_muslos:
            print(fill_hip_message(ss, True).strip())
        for av in ss["avisos"]:
            print(" AVISO:", av)
    return 0 if val["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
