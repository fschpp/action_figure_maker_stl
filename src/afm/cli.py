"""Línea de comandos del motor (sin interfaz)."""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
import time

from . import __version__, meshio, pipeline as P, solids as S
from .i18n import tr
from .project import Project, demo_project
from .testfigure import make_doll


def head_color_sources(head, base_dir: str | None = None, progress=print) -> tuple[dict, dict]:
    """(`sources`, info) con el proveedor de color de la cabeza importada (T34): `HeadSpec.replacement_color_path` (su FBX) contra
    `replacement_path` (su STL). `({}, {})` si la cabeza no trae color propio. Un FBX perdido, de otro hash o sin textura de color se
    avisa y la cabeza sale con el color de la figura (no se corta la exportación)."""
    if head is None or not head.replacement_path or not head.replacement_color_path:
        return {}, {}
    from .colorpiece import head_color_provider
    from .project import sha256_of

    path = head.replacement_color_path
    if not os.path.isabs(path) and base_dir and not os.path.isfile(path):
        path = os.path.join(base_dir, path)
    info: dict = {"fbx": os.path.abspath(path)}
    if not os.path.isfile(path):
        progress(tr("color.aviso_cabeza_sin_fbx", ruta=head.replacement_color_path))
        info["omitido"] = "no_existe"
        return {}, info
    if head.replacement_color_sha256 and sha256_of(path) != head.replacement_color_sha256:
        progress(tr("color.aviso_cabeza_hash", ruta=path))
        info["omitido"] = "hash"
        return {}, info
    return {"cabeza": head_color_provider(path, head.replacement_path, out=info)}, info


def make_colorizer(color: dict, mesh, params, progress=print, post=None, frame=None, sources: dict | None = None):
    """(Colorizer | None, info) para `run`: carga el FBX de `color["fbx"]`, lo alinea con `mesh` (la figura en el marco del motor, modo B)
    y arma la fuente de color. `mesh` es la figura TAL COMO SE CARGÓ (antes del giro de `front="-Y"` y de la simetría) y `post` la 4×4 de
    esos dos pasos (`FigureFrame.engine_post`): el FBX del cliente mira a −Y como el STL, así que se alinea antes de girar.
    Modo A (T21): `frame` (`FigureFrame.from_load`, archivo → marco del motor) reemplaza a la alineación por similitud: el FBX ES la entrada.
    Si el FBX no es la misma figura o no trae textura de color, avisa y devuelve `None` (la figura sale sin color).
    `color`: `fbx`, `texel_mm`, `eps`, `fill`, `symmetric`. `sources` (T34): fuentes propias por pieza (ver `Colorizer`). El `info` es serializable (R13)."""
    from .colorexport import Colorizer
    from .colorsrc import ColorSource
    from .fbxio import load_textured
    from .frame import SIMILARITY_OK_MM, FigureFrame

    path = color["fbx"]
    tm = load_textured(path)
    info: dict = {"fuente": os.path.abspath(path)}
    for w in tm.info.get("avisos", []):
        progress(f"AVISO: {w}")
    if "base_color" not in tm.images:
        progress(tr("color.aviso_sin_textura", ruta=os.path.basename(path)))
        info["omitido"] = "sin_textura"
        return None, info
    if frame is None:
        frame = FigureFrame.from_similarity(tm, mesh)
        if post is not None:
            frame = frame.then(post)
        info["alineacion"] = dict(frame.report)
    else:
        info["alineacion"] = {"modo": "A", "ok": True}
    if not frame.report.get("ok", True):
        progress(tr("color.aviso_otra_figura", ruta=os.path.basename(path), media=f"{frame.report['media_mm']:.2f}",
                    max=f"{SIMILARITY_OK_MM:g}"))
        info["omitido"] = "otra_figura"
        return None, info
    progress(tr("color.cargando_fuente"))
    src = ColorSource(tm, frame)
    cz = Colorizer(src, params, texel_mm=color.get("texel_mm", 0.2), eps=color.get("eps", 0.15),
                   fill=color.get("fill", "extend"), symmetric=bool(color.get("symmetric", False)), sources=sources)
    return cz, info


def run(stl_path: str, project: Project, outdir: str, name: str, progress=print, orient: bool | str = False,
        max_angle: float = 45.0, flat: float | None = None, catalog_root: str | None = None,
        keep: dict | None = None, color: dict | None = None, sin_color: bool = False) -> dict:
    """Genera la figura. `keep`, si se pasa, recibe `res` (el `BuildResult`), `params` para la vista previa de la pose y `colorizer`.
    `color` (opcional, ver `make_colorizer`): FBX con textura de la misma figura; además de los STL (que no cambian) deja las
    piezas con color en `piezas_color/` y `<nombre>_ensamblada.glb`. Modo A (T21): si `stl_path` es un `.fbx`, ese FBX es la entrada y la
    fuente de color a la vez (`color` solo ajusta `texel_mm`, `eps`, `fill`, `symmetric`); `sin_color=True` lo usa solo como geometría."""
    from .assembly import resolve_parts

    parts = resolve_parts(project.parts, catalog_root) if project.parts else None
    mesh, rep = meshio.load_figure(stl_path, project.params.ref_height, project.crown_z)
    for w in rep.warnings:
        progress(f"AVISO: {w}")
    mode_a = bool(rep.color_source_path) and not sin_color
    if mode_a:
        color = {**(color or {}), "fbx": rep.color_source_path}
        if rep.remeshed:         # la reparación remalló: la superficie ya no coincide con la fuente a 0,15 mm (§7.2)
            color["eps"] = max(float(color.get("eps", 0.15)), 0.3)
            progress(tr("color.aviso_remallada", eps=f"{color['eps']:g}"))
    elif color is not None and "fbx" not in color:
        color = None
    mesh_loaded = mesh.copy() if (color and not mode_a) else None      # la fuente de color se alinea contra la figura tal como se cargó
    if project.front == "-Y":      # girar 180° sobre Z para que el frente quede en +Y
        mesh.apply_transform([[-1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
    mesh = P.align_to_symmetry(mesh, project.symmetry)
    colorize, color_info = (None, None)
    if color:                         # antes de construir: un FBX ilegible o de otra figura se sabe en segundos, no a los 30 s
        from .frame import FigureFrame
        if mode_a:
            colorize, color_info = make_colorizer(color, None, project.params, progress,
                                                  frame=FigureFrame.from_load(rep, project.front, project.symmetry))
        else:
            colorize, color_info = make_colorizer(color, mesh_loaded, project.params, progress,
                                                  post=FigureFrame.engine_post(project.front, project.symmetry))
    head_src, head_info = head_color_sources(project.head, os.path.dirname(os.path.abspath(project.path)) if getattr(project, "path", None) else None,
                                             progress)
    if head_src:                      # T34: la cabeza importada trae su FBX; con o sin FBX de la figura, la pieza `cabeza` sale con el suyo
        if colorize is None:
            from .colorexport import Colorizer
            c = color or {}
            colorize = Colorizer(None, project.params, texel_mm=c.get("texel_mm", 0.2), eps=c.get("eps", 0.15),
                                 fill=c.get("fill", "extend"), symmetric=bool(c.get("symmetric", False)), sources=head_src)
            color_info = {}
        else:
            colorize.sources.update(head_src)
    if head_info and color_info is not None:
        color_info["cabeza"] = head_info
    body = S.to_manifold(mesh)
    from .customize import needed, build_model
    if needed(project):
        res, limbs = build_model(body, project, parts, progress)
        progress("Validando la figura…")
        val = P.validate(res, project.params, limbs, joints=res.joint_specs)
    else:
        res = P.build(body, project.limbs, project.params, project.symmetry, progress=progress,
                      trims=project.trims, head=project.head, joints=project.joints, waist=project.waist,
                      hand=project.hand, parts=parts, limbs_right=project.limbs_right)
        progress("Validando la figura…")
        val = P.validate(res, project.params, project.limbs, joints=project.joints)
    if keep is not None:
        keep.update(res=res, params=project.params, colorizer=colorize)      # `colorizer`: el `Colorizer` (None sin color); se llena en `export`
    from .accessories import finish_export, load_project_items

    items = load_project_items(project, progress=print)
    progress("Exportando las piezas…")
    files = P.export(res, outdir, name, orient=orient, max_angle=max_angle, flat=flat, params=project.params,
                     extras=[i["mesh"] for i in items], colorize=colorize)
    acc_info = finish_export(items, outdir, name, progress=print)
    if acc_info:
        files["accesorios"] = [a["archivo"] for a in acc_info]
    info = {"carga": rep.__dict__, "construccion": res.report, "validacion": val, "archivos": files}
    if acc_info:
        info["accesorios"] = acc_info
    if color_info is not None:
        info["color"] = {**color_info, **({"salida": files["color"]} if "color" in files else {})}
        if "color" in files:
            progress(tr("color.resumen", n=len(files["color"]["piezas"]), carpeta=files["color"]["carpeta"],
                        s=files["color"]["segundos"]))
    with open(os.path.join(outdir, f"{name}_informe.json"), "w") as f:
        json.dump(info, f, indent=2, default=float)
    project.save(os.path.join(outdir, f"{name}.proyecto"))
    return info


def run_armar(recipe_path: str, outdir: str, name: str | None = None, catalog_root: str | None = None, progress=print,
              orient: bool | str = False, max_angle: float = 45.0, flat: float | None = None) -> dict:
    """`afm armar`: arma la figura de una receta (`afm-receta-1`) solo con piezas de catálogo, valida y exporta."""
    from .recipe import Recipe, assemble

    recipe = Recipe.load(recipe_path)
    name = name or recipe.personaje
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    asm = assemble(recipe, catalog_root, progress=progress)
    if flat is not None and flat < 0:
        flat = asm.params.flat_depth
    progress("Validando la figura…")
    val = P.validate(asm.res, asm.params, asm.limbs, joints=asm.joints)
    try:                          # compat.json: la figura ya está armada y validada, guardar el resultado no cuesta nada
        from .compat import record
        record(asm, val, time.time() - t0)
    except (OSError, ValueError) as exc:
        progress(f"AVISO: no se pudo guardar el resultado en compat.json: {exc}")
    items = []
    if recipe.accesorios:                 # acompañan a la figura: STL aparte en piezas/ y al lado de la figura en el ensamblado, no en el puño
        from .recipe import load_accessories

        items = load_accessories(recipe, asm.catalog)
    progress("Exportando las piezas…")
    files = P.export(asm.res, outdir, name, orient=orient, max_angle=max_angle, flat=flat, params=asm.params,
                     progress=progress, extras=[i["mesh"] for i in items])
    from .accessories import finish_export

    acc_info = finish_export(items, outdir, name, progress)
    if acc_info:
        files["accesorios"] = [a["archivo"] for a in acc_info]
    info = {"receta": recipe.to_dict(), "construccion": asm.res.report, "validacion": val, "archivos": files}
    if acc_info:
        info["accesorios"] = acc_info
    with open(os.path.join(outdir, f"{name}_informe.json"), "w") as f:
        json.dump(info, f, indent=2, default=float)
    recipe.save(os.path.join(outdir, f"{name}.receta.json"))
    from . import catalog_project

    progress("Guardando el proyecto…")
    info["proyecto"] = catalog_project.save(asm, os.path.join(outdir, f"{name}.proyecto"))   # la ventana principal lo abre sin STL
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


def _run_desde_patron(args) -> int:
    from .patron import crear_desde_patron

    try:
        out, img, notes = crear_desde_patron(args.patron, args.stl, args.out, args.corona, args.frente, args.entrepierna,
                                             args.imagen, progress=print)
    except (ValueError, OSError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for n in notes:
        print("AVISO:", n)
    print(f"Proyecto guardado en {out}\nImagen de revisión: {img}")
    return 0


def _run_ajustar_cm(args) -> int:
    from . import ajuste as A
    from .catalog import Catalog, CatalogError
    from .extract import load_body
    from .project import Project

    try:
        project = Project.load(args.proyecto)
        cat = Catalog.open(args.catalogo)
        body, _rep = load_body(args.stl, project, progress=print)
        results = A.ajustar(project, body, cat, args.pelvis, args.mano)
    except (ValueError, OSError, KeyError, CatalogError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for r in results:
        print("\n".join(A.format_ajuste(r)))
    if args.guardar:
        n = A.aplicar(project, results, args.escalar_pelvis)
        project.save(args.out or args.proyecto)
        print(f"Se hicieron {n} cambio(s) (planos movidos / escala de la pelvis); proyecto guardado en {args.out or args.proyecto}")
    elif any(r.cambio for r in results):
        print("(no se guardó nada: agregá --guardar para aplicar los desplazamientos)")
    return 0 if all(r.error is None for r in results) else 1


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
    b.add_argument("--escala-pieza", action="append", default=[], metavar="LUGAR=FACTOR",
                   help="escala de la pieza de un lugar (torso, pelvis, cabeza o nombre del brazo/pierna): FACTOR uniforme o FX,FY,FZ "
                        "por eje (X lateral, Y adelante-atrás, Z alto); repetible")
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
    b.add_argument("--catalogo", default=None, metavar="RUTA",
                   help="catálogo de piezas de la línea (por defecto $AFM_CATALOGO o ~/.local/share/afm/catalogos/<línea>)")
    b.add_argument("--pieza", action="append", default=[], metavar="LUGAR=REF",
                   help="usar la pieza de catálogo REF (id o id@versión) en el miembro LUGAR del proyecto (p. ej. pierna=pierna-guemes-01), en «cabeza», «mano» o «pelvis» "
                        "(la pelvis sola cambia solo la del modelo y necesita su plano de cintura); "
                        "repetible. Queda guardada en el .proyecto de salida")
    b.add_argument("--color-desde", default=None, metavar="FBX", help=tr("color.ayuda_desde"))
    b.add_argument("--sin-color", action="store_true", help=tr("color.ayuda_sin_color"))
    b.add_argument("--textura-mm", type=float, default=0.2, metavar="MM", help=tr("color.ayuda_textura"))
    b.add_argument("--color-simetrico", action="store_true", help=tr("color.ayuda_simetrico"))
    b.add_argument("--color-relleno", default="extend", metavar="#RRGGBB", help=tr("color.ayuda_relleno"))
    b.add_argument("--color-tolerancia", type=float, default=0.15, metavar="MM", help=tr("color.ayuda_tolerancia"))
    b.add_argument("--max-angle", type=float, default=45.0, help="ángulo de voladizo tolerado (grados desde la vertical)")
    a = sub.add_parser("armar", help="armar una figura solo con piezas de catálogo a partir de una receta (afm-receta-1)")
    a.add_argument("catalogo", metavar="RUTA", help="catálogo de piezas de la línea de la receta")
    a.add_argument("receta", help="<personaje>.receta.json")
    a.add_argument("--out", default="out/figura")
    a.add_argument("--name", default=None, help="nombre de los archivos de salida (por defecto el personaje de la receta)")
    a.add_argument("--sin-soportes", nargs="?", type=float, const=-1.0, default=None, metavar="MM",
                   help="aplanar la base y girar los brazos para imprimir sin soportes (sin MM: el de la línea)")
    a.add_argument("--orient", action="store_true", help="exportar apoyado en la cama en la pose de la vista previa")
    a.add_argument("--orient-auto", action="store_true", help="exportar en la orientación de menor voladizo")
    a.add_argument("--max-angle", type=float, default=45.0, help="ángulo de voladizo tolerado (grados desde la vertical)")
    dp = sub.add_parser("desde-patron", help="proyecto de un héroe nuevo a partir de una figura patrón que ya funciona (copia sus planos y vuelve a dibujar las siluetas)")
    dp.add_argument("patron", help="proyecto de la figura patrón (p. ej. Güemes)")
    dp.add_argument("stl", help="STL del héroe nuevo")
    dp.add_argument("--out", default=None, help="dónde guardar el proyecto (por defecto, junto al STL, con la extensión .proyecto)")
    dp.add_argument("--imagen", default=None, help="PNG de revisión (por defecto, junto al proyecto)")
    dp.add_argument("--corona", type=float, default=None, help="cota de la coronilla en unidades del STL (si tiene sombrero o plumas)")
    dp.add_argument("--entrepierna", type=float, default=None, help="altura (mm) de la entrepierna si la figura lleva abrigo o faldón largo")
    dp.add_argument("--frente", choices=["+Y", "-Y"], default=None, help="hacia dónde mira la figura en el STL (por defecto, como el patrón)")
    ac = sub.add_parser("ajustar-cintura-muneca", help="mueve el plano de la cintura y el de la muñeca de un héroe hasta que calcen con la pelvis y la mano del patrón del catálogo")
    ac.add_argument("proyecto", help="proyecto del héroe (p. ej. el que dejó desde-patron)")
    ac.add_argument("stl", help="STL del héroe")
    ac.add_argument("--catalogo", required=True, help="carpeta del catálogo con la pelvis y la mano del patrón")
    ac.add_argument("--pelvis", default=None, help="pieza de pelvis del patrón (si hay más de una)")
    ac.add_argument("--mano", default=None, help="pieza de mano del patrón (si hay más de una)")
    ac.add_argument("--guardar", action="store_true", help="aplicar los desplazamientos al proyecto")
    ac.add_argument("--escalar-pelvis", action="store_true", help="con --guardar, escribe también la escala que adapta la pelvis del patrón a la cintura del héroe (part_scales)")
    ac.add_argument("--out", default=None, help="otro archivo donde guardar (por defecto, el mismo proyecto)")
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
    ag = sub.add_parser("agarre", help="taladrar un agujero de agarre en la mano estándar y generar espadas de prueba con empuñadura dentada (snap-fit)")
    ag.add_argument("mano", help="STL de la mano estándar (`afm extraer-mano`); el .mano.json de al lado es opcional")
    ag.add_argument("--out", default="out/agarre")
    ag.add_argument("--taladro", type=float, default=2.4, help="Ø del agujero en el puño (mm); con la mano de Güemes ≥ 2,6–2,8 abre el anillo")
    ag.add_argument("--vastago", type=float, default=2.0, help="Ø de la empuñadura (mm)")
    ag.add_argument("--ranura", type=float, default=0.5, help="ancho de la ranura entre las dos lengüetas (mm)")
    ag.add_argument("--largo-lengueta", type=float, default=8.0, help="largo de la lengüeta (mm)")
    ag.add_argument("--dentados", default="2.7,2.9,3.1", help="Ø del dentado a probar, uno por espada (mm)")
    ag.add_argument("--catalogo", default=None, help="ruta de un catálogo de línea: guarda además la mano taladrada (pieza «mano» con agarre) "
                                                      "y una espada de prueba (pieza «accesorio») con el dentado del medio de --dentados")
    ag.add_argument("--id-mano", default=None)
    ag.add_argument("--id-accesorio", default=None)
    ag.add_argument("--nueva-version", action="store_true", help="con --catalogo: si el id ya existe, guardar la versión siguiente")
    from .catalog_cli import add_guia_parser, add_parsers
    add_parsers(sub)
    add_guia_parser(sub)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.cmd == "catalogo":
        from .catalog_cli import run_catalogo
        return run_catalogo(args)
    if args.cmd == "guia":
        from .catalog_cli import run_guia
        return run_guia(args)
    if args.cmd == "desde-patron":
        return _run_desde_patron(args)
    if args.cmd == "ajustar-cintura-muneca":
        return _run_ajustar_cm(args)
    os.makedirs(args.out, exist_ok=True)
    if args.cmd == "armar":
        from .recipe import RecipeError
        try:
            info = run_armar(args.receta, args.out, args.name, args.catalogo, orient="auto" if args.orient_auto else args.orient,
                             max_angle=args.max_angle, flat=args.sin_soportes)
        except (RecipeError, OSError) as exc:
            print(f"ERROR (receta): {exc}", file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"ERROR (armado): {exc}", file=sys.stderr)
            return 2
        val = info["validacion"]
        print("Validación:", "OK" if val["ok"] else "CON PROBLEMAS")
        for pr in val["problems"]:
            print(" -", pr)
        for av in val.get("avisos", []) + val.get("avisos_pasador", []):
            print(" AVISO:", av)
        for lugar, r in (val.get("piezas") or {}).items():
            print(f"Pieza de catálogo en {lugar}: {r['pieza']} — compatibilidad {r['nivel']}")
        print("Holguras mínimas (mm):", val["gaps"])
        return 0 if val["ok"] else 1

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
    if args.cmd == "agarre":
        from .hand import HandError
        from .props import GripError, GripSpec, compute_grip, run_grip
        try:
            spec = GripSpec(bore_d=args.taladro, shaft_d=args.vastago, slit_w=args.ranura, finger_len=args.largo_lengueta)
            res = compute_grip(args.mano, spec)
            info = run_grip(args.mano, args.out, spec, tuple(float(x) for x in args.dentados.split(",")), res=res)
        except (HandError, GripError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(f"Mano con agarre: {info['mano']}")
        print(f"  canal natural Ø{info['canal_natural_mm']:.2f} mm → taladro Ø{info['diametro_taladro_mm']:g} mm · largo {info['largo_canal_mm']:.1f} mm · "
              f"pared mín. del anillo {info['pared_min_mm']:.2f} mm · apoyo del dentado ≈ {info['apoyo_dentado_grados']:.0f}°")
        print(f"Placa de espadas de prueba: {info['placa']}")
        for v in info["variantes"]:
            print(f"  dentado Ø{v['dentado_mm']:g}: interferencia {v['interferencia_mm']:.2f} mm/lado · lengüeta ε ≈ {v['deformacion_pct']:.2f} %")
        if info["apoyo_dentado_grados"] < 270:
            print("  AVISO: el hombro apoya solo en parte del contorno del taladro; la orientación de la espada importa y el agarre es más flojo que con un tubo completo")
        if args.catalogo:
            from .catalog import Catalog, CatalogError
            from .props import add_to_catalog
            barbs = tuple(float(x) for x in args.dentados.split(","))
            try:
                cat = Catalog.open(args.catalogo)
                mano, acc, warns = add_to_catalog(res, cat, barbs[len(barbs) // 2], args.id_mano, args.id_accesorio, args.nueva_version, spec)
            except (CatalogError, GripError, HandError, ValueError) as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 2
            print(f"Catálogo «{cat.name}»: mano {mano.ref} y accesorio {acc.ref} (dentado Ø{barbs[len(barbs) // 2]:g} mm)")
            for w in warns:
                print("  AVISO:", w)
        return 0
    if args.cmd == "probe":
        from . import probes
        fl = lambda t: [float(x) for x in t.split(",")] if t else None
        files, text = probes.probe_files(
            args.tipo, clearances=fl(args.clearances), deltas=fl(args.deltas), axiales=fl(args.axiales),
            boquilla=args.boquilla, gaps=fl(args.gaps), espesores=fl(args.espesores))
        for name, data in files.items():
            with open(os.path.join(args.out, name), "wb") as f:
                f.write(data)
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
        if args.escala_pieza:
            from .assembly import AssemblyError
            from .customize import normalize_scale
            allowed = {"torso", *(l.name for l in project.limbs)} | ({"pelvis"} if project.waist else set()) \
                | ({"cabeza"} if project.head is not None else set())
            for item in args.escala_pieza:
                place, sep, value = item.partition("=")
                try:
                    k = normalize_scale([float(x) for x in value.replace(";", ",").split(",")])
                except (ValueError, AssemblyError):
                    ap.error(f"--escala-pieza «{item}»: se espera LUGAR=FACTOR o LUGAR=FX,FY,FZ (números mayores que cero)")
                if not sep or place not in allowed:
                    ap.error(f"--escala-pieza «{item}»: lugar inválido (hay {', '.join(sorted(allowed))}); pelvis requiere cintura y cabeza, cuello")
                project.part_scales[place] = k
        if args.pieza:
            from .catalog import Catalog, CatalogError, parse_ref
            from .project import PartRef
            root = args.catalogo or os.environ.get("AFM_CATALOGO")
            if root is None:
                ap.error("--pieza necesita el catálogo: pasá --catalogo RUTA (o definí AFM_CATALOGO)")
            try:
                cat = Catalog.open(root)
                for item in args.pieza:
                    place, sep, ref = item.partition("=")
                    if not sep or not place or not ref:
                        ap.error(f"--pieza «{item}»: se espera LUGAR=REF (p. ej. pierna=pierna-guemes-01)")
                    if place not in ("mano", "cabeza", "pelvis") and place not in {l.name for l in project.limbs}:
                        ap.error(f"--pieza «{item}»: el proyecto no tiene un miembro «{place}» "
                                 f"(hay: {', '.join(l.name for l in project.limbs) or 'ninguno'})")
                    pid, _ver = parse_ref(ref)
                    project.parts[place] = PartRef(pid, cat.meta(ref).version, cat.name)     # fija la versión elegida
            except CatalogError as exc:
                print(f"ERROR (catálogo): {exc}", file=sys.stderr)
                return 2
        if args.cadera_referencia:
            project.params = project.params.with_hip_reference()
        if args.rellenar_muslos:
            project.params = dataclasses.replace(project.params, flat_fill_hip=True)
        flat = args.sin_soportes
        if flat is not None and flat < 0:
            flat = project.params.flat_depth
        from .assembly import AssemblyError
        from .hand import HandError
        run_kw: dict = {}
        if args.color_desde and not args.sin_color:      # sin el flag `run` no recibe `color`: el flujo de siempre queda igual
            from .colorbake import parse_fill
            try:
                parse_fill(args.color_relleno)
            except ValueError as exc:
                ap.error(f"--color-relleno: {exc}")
            if not os.path.isfile(args.color_desde):
                ap.error(tr("color.error_no_existe", ruta=args.color_desde))
            run_kw["color"] = {"fbx": args.color_desde, "texel_mm": args.textura_mm, "eps": args.color_tolerancia,
                               "fill": args.color_relleno, "symmetric": args.color_simetrico}
        from .fbxio import FbxError
        try:
            info = run(args.stl, project, args.out, name, orient="auto" if args.orient_auto else args.orient,
                       max_angle=args.max_angle, flat=flat, catalog_root=args.catalogo, sin_color=args.sin_color, **run_kw)
        except FbxError as exc:
            print(tr("color.error_fbx", err=str(exc)), file=sys.stderr)
            return 2
        except HandError as exc:
            print(f"ERROR (mano): {exc}", file=sys.stderr)
            return 2
        except AssemblyError as exc:
            print(f"ERROR (pieza de catálogo): {exc}", file=sys.stderr)
            return 2
    val = info["validacion"]
    print("Validación:", "OK" if val["ok"] else "CON PROBLEMAS")
    for pr in val["problems"]:
        print(" -", pr)
    for av in val.get("avisos", []) + val.get("avisos_pasador", []):
        print(" AVISO:", av)
    for lugar, r in (val.get("piezas") or {}).items():
        print(f"Pieza de catálogo en {lugar}: {r['pieza']} — compatibilidad {r['nivel']}"
              + ("" if r["iou"] is None else f" (IoU {r['iou']:.2f}, escalón {r['escalon_mm']:.2f} mm)"))
    print("Holguras mínimas (mm):", val["gaps"])
    ori = info["archivos"].get("orientacion")
    if ori:
        print(f"Orientación de impresión: {ori['etiqueta']} — voladizo {ori['voladizo_mm2']} mm², "
              f"base {ori['base_mm2']} mm²")
    ss = info["archivos"].get("sin_soportes")
    if ss:
        giro = ", ".join(f"{k} {v:g}°" for k, v in ss["giro_brazos_grados"].items())
        print(f"Corte con la cama de impresión: {ss['corte_sobre_cola_mm']:g} mm sobre la cola (z = {ss['altura_corte_z']}); "
              f"brazos girados hacia adelante: {giro or '0°'}")
        from .messages import fill_hip_message
        if args.rellenar_muslos:
            print(fill_hip_message(ss, True).strip())
        for av in ss["avisos"]:
            print(" AVISO:", av)
    return 0 if val["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
