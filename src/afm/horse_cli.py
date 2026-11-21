"""E13: la línea de comandos de los caballos (`afm caballo …`) y el armado ecuestre de `afm armar`.

* `afm caballo agregar | listar | verificar`: la biblioteca `<catálogo>/caballos/` (E11) desde la terminal.
* `armar_ecuestre`: lo que hace `afm armar` cuando la receta trae `cabalgadura` (E12): lee el caballo `id@versión` de la
  biblioteca, ajusta el asiento con `RidingFitter` (E09), valida (E08) y exporta el conjunto con `export_riding` (E10).

El flujo de una receta sin `cabalgadura` no pasa por acá. Este módulo no importa `manifold3d` (solo `solids` a través de
`equestrian`). Los avisos y errores se dicen en español, como el resto de la CLI.
"""
from __future__ import annotations

import dataclasses
import json
import os
import time

from .catalog import Catalog, CatalogError
from .equestrian import (REF_KINDS, HorseError, RiderError, RidingFitter, SaddleError, export_riding)
from .horse_library import HorseLibrary, parse_horse_ref
from .ride import RideSpec, geometry_hash, geometry_warnings, resolve_horse

CABALLO_ERRORS = (HorseError, SaddleError, RiderError)       # los que `afm` rotula como «ERROR (caballo)»


# ---------------------------------------------------------------------------------------------------------------- biblioteca
def library_for(catalog_root: str | None) -> HorseLibrary:
    """La biblioteca de caballos del catálogo `catalog_root` (o `$AFM_CATALOGO`). Pide que sea un catálogo de línea: así un error de
    tipeo en la ruta no deja una carpeta `caballos/` suelta."""
    root = catalog_root or os.environ.get("AFM_CATALOGO")
    if not root:
        raise HorseError("falta el catálogo de la línea: pasá --catalogo RUTA (o definí AFM_CATALOGO)")
    try:
        cat = Catalog.open(root)
    except CatalogError as exc:
        raise HorseError(str(exc)) from None
    return HorseLibrary(cat.root)


def _add_catalog(p) -> None:
    p.add_argument("--catalogo", default=None, metavar="RUTA", help="catálogo de piezas de la línea; el caballo vive en <RUTA>/caballos (por defecto $AFM_CATALOGO)")


def add_parsers(sub) -> None:
    c = sub.add_parser("caballo", help="biblioteca de caballos de la línea (agregar, listar, verificar)")
    cs = c.add_subparsers(dest="horsecmd", required=True)
    a = cs.add_parser("agregar", help="agregar un caballo STL a la biblioteca (copia administrada; las versiones no se sobrescriben)")
    _add_catalog(a)
    a.add_argument("stl", help="STL del caballo (una sola malla estanca)")
    a.add_argument("--id", required=True, help="id del caballo: minúsculas, dígitos y guiones (p. ej. caballo-pardo-01)")
    a.add_argument("--nombre", default="")
    a.add_argument("--escala", type=float, default=1.0, help="factor que lleva el STL a milímetros (por defecto 1)")
    a.add_argument("--yaw", type=float, default=0.0, help="giro en grados sobre Z que deja la cabeza hacia +Y (horse_baseless.stl: 180)")
    a.add_argument("--ref-tipo", default=None, choices=REF_KINDS, help="medida física de referencia que fijó la escala (va con --ref-mm)")
    a.add_argument("--ref-mm", type=float, default=None, help="esa medida en mm, ya escalada")
    a.add_argument("--nueva-version", action="store_true", help="si el id ya existe, guardar la versión siguiente")
    a.add_argument("--sin-reparar", action="store_true", help="no intentar reparar un STL que no sea estanco (falla en vez de reparar)")
    ls = cs.add_parser("listar", help="caballos de la biblioteca (la última versión de cada id)")
    _add_catalog(ls)
    ls.add_argument("--todas-las-versiones", action="store_true")
    v = cs.add_parser("verificar", help="ficha, SHA-256 y presencia del STL de cada caballo (o de uno)")
    _add_catalog(v)
    v.add_argument("ref", nargs="?", default=None, metavar="REF", help="id o id@versión (por defecto toda la biblioteca)")
    v.add_argument("--profundo", action="store_true", help="además cargar cada STL (estanco, escala y guiño)")


def _num(v: float) -> str:
    return f"{v:.1f}".replace(".", ",")


def _describe(meta) -> str:
    s = meta.spec
    ref = f" · referencia {s.ref_tipo} {_num(s.ref_mm)} mm" if s.ref_tipo else ""
    return f"{meta.ref}" + (f" — {meta.nombre}" if meta.nombre else "") + f" · escala ×{s.escala:g} · guiño {s.yaw:g}°{ref} · sha256 {s.sha256[:12]}"


def run_caballo(args) -> int:
    try:
        lib = library_for(args.catalogo)
        if args.horsecmd == "agregar":
            return _agregar(lib, args)
        if args.horsecmd == "listar":
            hs = lib.list(all_versions=args.todas_las_versiones)
            if not hs:
                print(f"No hay caballos en {lib.dir}. Agregá uno con: afm caballo agregar STL --id ID --catalogo {lib.root}")
                return 0
            for m in hs:
                print(_describe(m))
            return 0
        bad = lib.verify(args.ref, deep=args.profundo)
        n = 1 if args.ref else sum(len(lib.versions(h)) for h in _ids(lib))
        if not bad:
            print(f"Biblioteca de caballos OK ({n} versión(es) revisadas{' a fondo' if args.profundo else ''}).")
            return 0
        for ref, probs in bad.items():
            for p in probs:
                print(f"PROBLEMA ({ref}): {p}")
        return 1
    except CABALLO_ERRORS as exc:
        import sys

        print(f"ERROR (caballo): {exc}", file=sys.stderr)
        return 2


def _ids(lib: HorseLibrary) -> list:
    return sorted({m.id for m in lib.list()})


def _agregar(lib: HorseLibrary, args) -> int:
    before = lib.versions(args.id) if isinstance(args.id, str) else []
    try:
        meta = lib.add(args.stl, args.id, nombre=args.nombre, escala=args.escala, yaw=args.yaw, ref_tipo=args.ref_tipo, ref_mm=args.ref_mm,
                       new_version=args.nueva_version, repair=not args.sin_reparar)
    except HorseError as exc:
        if "no se sobrescriben" in str(exc):
            raise HorseError(f"{exc} (con --nueva-version se guarda como versión {before[-1] + 1} y las recetas que fijan la anterior siguen igual)") from None
        raise
    same = bool(before) and meta.version == before[-1]
    print(f"Caballo «{meta.ref}» " + ("ya estaba en la biblioteca con esos datos: no se creó nada." if same else "agregado a la biblioteca."))
    print(f"  {_describe(meta)}")
    print(f"  STL administrado: {lib.path(meta.ref)}")
    model = lib.open(meta.ref)                                     # ya validado al agregar; acá solo se miden las dimensiones
    ex, rep = model.report.extents, model.report
    print(f"  mide {_num(ex[0])} (ancho) × {_num(ex[1])} (largo) × {_num(ex[2])} (alto) mm · {rep.triangles} triángulos · {rep.components} cáscara(s)")
    if rep.repaired:
        print("  AVISO: el STL no era estanco y se reparó al cargarlo (el original no se tocó)")
    for w in rep.warnings:
        print(f"  AVISO: {w}")
    return 0


# ---------------------------------------------------------------------------------------------------------------- armar
def open_ride_horse(ride: RideSpec, catalog_root: str):
    """Abre el caballo `id@versión` de la receta en la biblioteca de `catalog_root` → (HorseLibrary, HorseModel, avisos).

    Los errores dicen cómo seguir: caballo que falta (importarlo), STL que falta o cambió (verificar)."""
    lib = HorseLibrary(catalog_root)
    hid, _v = parse_horse_ref(ride.caballo)
    if not lib.versions(hid):
        raise HorseError(f"el caballo «{hid}» de la receta no está en la biblioteca {lib.dir}; importarlo con: "
                         f"afm caballo agregar STL --id {hid} --catalogo {lib.root}")
    meta, warns = resolve_horse(ride, lib)                          # HorseError si falta la versión
    try:
        model = lib.open(meta.ref)
    except HorseError as exc:
        raise HorseError(f"{exc} (revisar con: afm caballo verificar {meta.ref} --catalogo {lib.root})") from None
    return lib, model, warns


def armar_ecuestre(asm, ride: RideSpec, model, outdir: str, name: str, progress=print, items=None, warns=None, fijar_geometria: bool = False) -> dict:
    """Monta la figura armada `asm` en el caballo `model` según `ride`, valida y exporta el conjunto.

    La figura no se vuelve a armar (D4): `RidingFitter` le aplica una matriz rígida a las piezas ya armadas. Si el ajuste no pasa la
    validación ecuestre NO se exporta nada y se devuelve `{"ok": False, …}` con los problemas y la etapa donde se detuvo. Si pasa se
    escriben `<nombre>_ensamblada.stl` y `piezas/<nombre>_<parte>.stl` (jinete, `caballo`, `silla`) con `export_riding`.
    Devuelve el resumen para el informe (`info["cabalgadura"]`) y, si se pidió, la receta con el hash de la geometría validada."""
    say = progress or (lambda *_: None)
    t0 = time.time()
    # la holgura ecuestre (plano 7.2) reemplaza a la de la línea solo en el ajuste (tallado, despeje y validación); el jinete se exporta con la suya
    fparams = asm.params if ride.holgura is None else dataclasses.replace(asm.params, clearance=ride.holgura)
    fitter = RidingFitter(model, asm, params=fparams, limits=ride.carve_limits(asm.params.min_wall))
    say(f"Montando la figura en «{ride.caballo}» (modo {ride.modo})…")
    fit = fitter.fit(**ride.fit_kwargs())
    info: dict = {"ok": fit.ok, "caballo": ride.caballo, "sha256": ride.sha256, "modo": ride.modo, "asiento": list(ride.asiento),
                  "direccion": list(ride.direccion), "inclinacion": ride.inclinacion,
                  "holgura": fparams.clearance, "etapa": fit.stage, "segundos_ajuste": round(fit.seconds, 1),
                  "problemas": list(fit.problems), "avisos": list(warns or []) + list(fit.warnings)}
    if fit.validation is not None:
        info["validacion"] = fit.validation
    if not fit.ok:
        return info
    ra = fit.assembly
    h = geometry_hash(ra)
    info["hash_geometria"] = h
    info["avisos"] += geometry_warnings(ride, ra)
    if ra.carve is not None:
        c = ra.carve
        info["tallado"] = {"volumen_retirado_mm3": round(float(c.removed_volume), 2), "fraccion": round(float(c.removed_frac), 5),
                           "profundidad_max_mm": round(float(c.max_depth), 2), "pared_min_mm": None if c.wall_min is None else round(float(c.wall_min), 2)}
    files = export_riding(ra, outdir, name, params=asm.params, progress=say, extras=[i["mesh"] for i in (items or [])], res=asm.res,
                          validation=fit.validation)
    info["archivos"] = files
    info["avisos"] += files.get("avisos", [])
    info["segundos"] = round(time.time() - t0, 1)
    return info


def fixed_recipe(recipe, ride: RideSpec, info: dict):
    """La receta con el hash de la geometría validada (`--fijar-geometria`): la próxima vez avisa si la geometría derivada cambió."""
    return dataclasses.replace(recipe, cabalgadura=ride.with_geometry_hash(info["hash_geometria"]).to_dict())


def print_summary(info: dict, out=print) -> None:
    """Lo que `afm armar` cuenta del conjunto con caballo."""
    out(f"Cabalgadura: {info['caballo']} · modo {info['modo']} · asiento {tuple(round(v, 2) for v in info['asiento'])}")
    if info["ok"]:
        out(f"Montaje: OK ({info['segundos_ajuste']:g} s de ajuste)")
        t = info.get("tallado")
        if t:
            pared = "" if t["pared_min_mm"] is None else f" · pared mínima {t['pared_min_mm']:g} mm"
            out(f"Caballo tallado: {t['volumen_retirado_mm3']:g} mm³ retirados ({t['fraccion'] * 100:.2f} %) · profundidad máxima {t['profundidad_max_mm']:g} mm{pared}")
        for k, g in (info.get("validacion", {}).get("gaps") or {}).items():
            out(f"  holgura {k}: {g:.2f} mm")
        out(f"Conjunto: {info['archivos']['ensamblada']} + {len(info['archivos']['triangulos'])} STL por componente en piezas/ (jinete, caballo y, si hay, silla)")
    else:
        out(f"Montaje: NO SE EXPORTÓ (se detuvo en «{info['etapa']}»)")
        for p in info["problemas"]:
            out(f" - {p}")
    for a in info["avisos"]:
        out(f" AVISO: {a}")


def write_report(info: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(info, f, indent=2, default=float)
