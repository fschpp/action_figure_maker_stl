"""Recetas de armado (`afm-receta-1`) y armado de una figura solo con piezas de catálogo (Fase 6 del plan de piezas
intercambiables, §5 y Apéndice A).

Una receta dice, para un personaje de una línea, qué pieza va en cada lugar (`id@versión`), qué opciones de armado se usan
(cintura, clic de hombro/cadera, espejado del torso) y los ajustes de la mano. `assemble` abre el catálogo de la línea,
sintetiza el huésped con el torso y acopla el resto; el perfil de la línea (`linea.json`) da los `Params`.
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field

from . import pipeline as P
from .assembly import AssemblyError, PartChoice, rigid_choice, synthesize_host
from . import solids as S
from .catalog import Catalog, CatalogError, default_root, parse_ref
from .customize import scale_choice
from .params import Params

RECIPE_FORMAT = "afm-receta-1"
PLACES = ("cabeza", "torso", "pelvis", "brazo", "pierna", "mano")
OPTIONS = {"cintura": False, "clic_hombro": False, "clic_cadera": False, "torso_mirror": True}
# Codo y rodilla: True (defecto) = el miembro usa la articulación que trae su pieza de catálogo (si la trae); False = el
# miembro se arma rígido. Aparte de `OPTIONS` a propósito: `options()` solo las incluye cuando valen False, así las recetas
# anteriores (todas «con») conservan su archivo y su clave de compatibilidad (`compat.recipe_key`).
JOINT_OPTIONS = {"codo": True, "rodilla": True}
JOINT_OF_PLACE = {"brazo": "codo", "pierna": "rodilla"}
HAND_ADJUSTS = ("yaw", "union", "solape")
AJUSTES = ("mano", "escalas", "monturas")
SCALE_PLACES = ("torso", "pelvis", "cabeza", "brazo", "pierna")     # la mano no se reescala (mm reales)


class RecipeError(ValueError):
    """La receta está mal formada o pide algo que no se puede armar."""


@dataclass
class Recipe:
    personaje: str
    linea: str
    lugares: dict = field(default_factory=dict)          # lugar -> "id" | "id@versión" | None
    opciones: dict = field(default_factory=dict)
    ajustes: dict = field(default_factory=dict)
    accesorios: list = field(default_factory=list)       # ["id@versión", …]: piezas «accesorio» que acompañan a la figura (no van en el puño)
    paleta_override: dict = field(default_factory=dict)  # T48: {rol: "#RRGGBB"} colores de la paleta de la línea que cambian SOLO en esta figura
    cabalgadura: dict = field(default_factory=dict)      # E12: caballo y asiento (`ride.RideSpec.to_dict()`); vacío = sin caballo. Fuera de `ajustes`: nunca entra en `compat.recipe_key`

    # ------------------------------------------------------------------ formato
    def to_dict(self) -> dict:
        d = {"formato": RECIPE_FORMAT, "personaje": self.personaje, "linea": self.linea,
             "lugares": {k: self.lugares.get(k) for k in PLACES}, "opciones": self.options(),
             "ajustes": self.ajustes}
        if self.accesorios:                                  # solo si hay: las recetas anteriores no cambian
            d["accesorios"] = list(self.accesorios)
        if self.paleta_override:                             # T48: solo si hay (las recetas anteriores no cambian). No entra en `ajustes`: el color no cambia los STL ni el hash de compat.json
            d["paleta_override"] = dict(self.paleta_override)
        if self.cabalgadura:                                 # E12: solo si hay (las recetas anteriores no cambian)
            d["cabalgadura"] = dict(self.cabalgadura)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Recipe":
        if d.get("formato") != RECIPE_FORMAT:
            raise RecipeError(f"formato de receta desconocido: {d.get('formato')!r} (se espera {RECIPE_FORMAT})")
        for k in ("personaje", "linea"):
            if not d.get(k):
                raise RecipeError(f"a la receta le falta «{k}»")
        acc = d.get("accesorios") or []
        if not isinstance(acc, list):
            raise RecipeError("«accesorios» tiene que ser una lista de id@versión")
        ov = d.get("paleta_override") or {}
        if not isinstance(ov, dict):
            raise RecipeError("«paleta_override» tiene que ser un diccionario {rol: \"#RRGGBB\"}")
        cab = d.get("cabalgadura") or {}
        if not isinstance(cab, dict):
            raise RecipeError("«cabalgadura» tiene que ser un diccionario")
        r = cls(d["personaje"], d["linea"], dict(d.get("lugares") or {}), dict(d.get("opciones") or {}), dict(d.get("ajustes") or {}),
                list(acc), dict(ov), dict(cab))
        bad = [k for k in r.problems()]
        if bad:
            raise RecipeError("; ".join(bad))
        r.ajustes = r.key_ajustes()          # monturas completas y sin lista vacía: la misma montura da siempre la misma clave
        if r.cabalgadura:
            r.cabalgadura = r.ride_spec().to_dict()      # forma canónica (campos por defecto escritos)
        return r

    @classmethod
    def load(cls, path: str) -> "Recipe":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)

    def override_problems(self) -> list[str]:
        """Los cambios de color de la figura (T48): cada rol con un `#RRGGBB` válido. Que el rol exista en la paleta se mira al armar."""
        from .palette import PaletteError, normalize_hex

        out: list[str] = []
        for rol, hx in (self.paleta_override or {}).items():
            try:
                normalize_hex(hx, f"paleta_override «{rol}»")
            except PaletteError as exc:
                out.append(str(exc))
        return out

    def ride_spec(self):
        """La cabalgadura (`ride.RideSpec`) o None si la receta no lleva caballo. Levanta `HorseError` si está mal formada."""
        if not self.cabalgadura:
            return None
        from .ride import RideSpec

        return RideSpec.from_dict(self.cabalgadura)

    def ride_problems(self) -> list[str]:
        """La cabalgadura bien formada (E12). Que el caballo esté en la biblioteca y tenga ese hash se mira al armar (`ride.resolve_horse`)."""
        if not self.cabalgadura:
            return []
        try:
            self.ride_spec()
        except ValueError as exc:                 # HorseError y SaddleError son ValueError
            return [f"cabalgadura: {exc}"]
        return []

    def problems(self) -> list[str]:
        out = [f"lugar desconocido «{k}» (hay {', '.join(PLACES)})" for k in self.lugares if k not in PLACES]
        out += [f"opción desconocida «{k}» (hay {', '.join([*OPTIONS, *JOINT_OPTIONS])})" for k in self.opciones
                if k not in OPTIONS and k not in JOINT_OPTIONS]
        out += [f"opción «{k}»: tiene que ser true o false" for k in JOINT_OPTIONS
                if k in self.opciones and not isinstance(self.opciones[k], bool)]
        out += [f"ajuste desconocido «{k}» (hay {', '.join(AJUSTES)})" for k in self.ajustes if k not in AJUSTES]
        out += self.scale_problems()
        out += self.mount_problems()
        out += self.override_problems()
        out += self.ride_problems()
        out += [f"ajuste de mano desconocido «{k}» (hay {', '.join(HAND_ADJUSTS)})" for k in (self.ajustes.get("mano") or {})
                if k not in HAND_ADJUSTS]
        un = (self.ajustes.get("mano") or {}).get("union")
        if un is not None and un not in ("solape", "giro"):
            out.append(f"ajuste de mano: union «{un}» (hay solape, giro)")
        for k, v in self.lugares.items():
            if v is not None:
                try:
                    parse_ref(v)
                except CatalogError as exc:
                    out.append(f"{k}: {exc}")
        seen: set[str] = set()
        for ref in self.accesorios:
            try:
                pid, _v = parse_ref(ref)
            except (CatalogError, TypeError, AttributeError) as exc:
                out.append(f"accesorio «{ref}»: {exc}")
                continue
            if pid in seen:
                out.append(f"el accesorio «{pid}» está repetido")
            seen.add(pid)
        if not self.lugares.get("torso"):
            out.append("falta el lugar «torso»: sin figura de origen es el que arma el cuerpo")
        if self.options()["cintura"] and not self.lugares.get("pelvis"):
            out.append("con cintura hace falta una pieza en el lugar «pelvis»")
        if not self.options()["cintura"] and self.lugares.get("pelvis"):
            out.append("hay una pieza en «pelvis» pero la opción «cintura» está apagada")
        return out

    def scale_problems(self) -> list[str]:
        """Escalas por pieza mal formadas: lugar que no se escala o sin pieza, y factores que no son 1 o 3 números > 0."""
        from .assembly import AssemblyError
        from .customize import scale_vector

        out = []
        for lugar, k in (self.ajustes.get("escalas") or {}).items():
            if lugar not in SCALE_PLACES:
                out.append(f"escala de «{lugar}»: ese lugar no se escala (hay {', '.join(SCALE_PLACES)})")
                continue
            try:
                scale_vector(k)
            except (AssemblyError, TypeError, ValueError) as exc:
                out.append(f"escala de «{lugar}»: {exc}")
                continue
            if not self.lugares.get(lugar):
                out.append(f"escala de «{lugar}» pero ese lugar no tiene pieza")
        return out

    def mount_problems(self) -> list[str]:
        """Monturas de armas (`ajustes[\"monturas\"]`, M10): cada una bien formada y con su arma entre los `accesorios`."""
        from .mount import MountError, MountSpec

        ms = self.ajustes.get("monturas")
        if ms is None:
            return []
        if not isinstance(ms, list):
            return ["«monturas» tiene que ser una lista de monturas"]
        acc: dict = {}
        for ref in self.accesorios:
            try:
                pid, ver = parse_ref(ref)
            except (CatalogError, TypeError, AttributeError):
                continue                                  # ya lo informa `problems`
            acc[pid] = ver
        out = []
        for i, m in enumerate(ms, start=1):
            try:
                spec = MountSpec.from_dict(m)
                pid, ver = parse_ref(spec.arma)
            except (MountError, CatalogError) as exc:
                out.append(f"montura {i}: {exc}")
                continue
            if pid not in acc:
                out.append(f"montura {i}: el arma «{spec.arma}» no está entre los accesorios de la receta")
            elif ver is not None and acc[pid] is not None and ver != acc[pid]:
                out.append(f"montura {i}: el arma es «{spec.arma}» pero el accesorio de la receta es «{pid}@{acc[pid]}»")
        return out

    def mount_specs(self) -> list:
        """Las monturas de la receta como `mount.MountSpec` (lista vacía si no hay)."""
        from .mount import MountSpec

        return [MountSpec.from_dict(m) for m in (self.ajustes.get("monturas") or [])]

    def key_ajustes(self) -> dict:
        """`ajustes` en forma canónica para la clave de compatibilidad: sin `monturas` vacías y con cada montura completa
        (campos por defecto escritos). Una receta sin monturas da exactamente sus `ajustes`: su clave no cambia (plan §6.3)."""
        a = dict(self.ajustes)
        ms = a.get("monturas")
        if ms is None:
            return a
        if not ms:
            a.pop("monturas")
            return a
        try:
            a["monturas"] = [m.to_dict() for m in self.mount_specs()]
        except Exception:                                 # receta armada a mano con una montura inválida: la clave no es lo que falla
            pass
        return a

    def scales(self) -> dict:
        """Lugar -> escala (número o [sx, sy, sz]) sin las que son ×1."""
        from .customize import is_identity, normalize_scale

        return {k: normalize_scale(v) for k, v in (self.ajustes.get("escalas") or {}).items() if not is_identity(v)}

    def options(self) -> dict:
        o = {**OPTIONS, **self.opciones}
        for k, default in JOINT_OPTIONS.items():          # solo las que se apartan del defecto (ver JOINT_OPTIONS)
            if o.get(k, default) == default:
                o.pop(k, None)
        return o

    def joint_on(self, lugar: str) -> bool:
        """¿El miembro del `lugar` («brazo» | «pierna») usa la articulación (codo | rodilla) de su pieza? Sí por defecto."""
        k = JOINT_OF_PLACE[lugar]
        return bool(self.opciones.get(k, JOINT_OPTIONS[k]))

    def apply_joint_options(self, choices: dict) -> dict:
        """`choices` (clave de `build(parts=)` -> `PartChoice`) con los brazos / piernas pedidos SIN articulación armados
        rígidos (`assembly.rigid_choice`). Devuelve un diccionario nuevo; las piezas del catálogo no se modifican."""
        out = dict(choices)
        for lugar in JOINT_OF_PLACE:
            if lugar in out and not self.joint_on(lugar):
                out[lugar] = rigid_choice(out[lugar])
        return out

    def parts_keys(self) -> dict:
        """Lugar de la receta -> clave de `build(parts=)`: con cintura el torso es `torso_sup`."""
        keys = {}
        for lugar, ref in self.lugares.items():
            if ref:
                keys[lugar] = "torso_sup" if (lugar == "torso" and self.options()["cintura"]) else lugar
        return keys


@dataclass
class Assembled:
    res: P.BuildResult
    params: Params
    limbs: list
    joints: list
    recipe: Recipe
    catalog: Catalog | None = None
    host: object | None = None          # `assembly.SyntheticHost` (cuerpo, miembros, cabeza y cintura del armado)


def recipe_params(recipe: Recipe, cat: Catalog) -> Params:
    """`Params` de la línea más las opciones de la receta."""
    o = recipe.options()
    return dataclasses.replace(cat.params, shoulder_detent=bool(o["clic_hombro"]), hip_detent=bool(o["clic_cadera"]),
                               torso_mirror=bool(o["torso_mirror"]))


def articulation_points(recipe: Recipe, cat: Catalog) -> int:
    """Puntos de articulación de la figura de la receta: cabeza, cintura (si lleva), hombros y caderas (de a dos) y, si la pieza
    del brazo / la pierna trae codo / rodilla y la opción está activa, codos / rodillas (de a dos). Juguete simple = 6
    (cabeza, cintura, hombros, caderas); completo = 10 (suma codos y rodillas)."""
    n = 0
    if recipe.lugares.get("cabeza"):
        n += 1
    if recipe.options()["cintura"]:
        n += 1
    for lugar in JOINT_OF_PLACE:
        ref = recipe.lugares.get(lugar)
        if not ref:
            continue
        n += 2
        if recipe.joint_on(lugar) and (cat.meta(ref).articulaciones or []):
            n += 2
    return n


def hand_spec(recipe: Recipe):
    from .hand import DEFAULT_OVERLAP_MM, HandSpec
    a = recipe.ajustes.get("mano")
    if not a or not recipe.lugares.get("mano"):
        return None
    return HandSpec(path="", yaw=float(a.get("yaw", 0.0)), join=a.get("union", "solape"),
                    overlap=float(a.get("solape", DEFAULT_OVERLAP_MM)))


def progress_stages(recipe: Recipe, catalog_root: str | None = None) -> list:
    """Pasos de «Armar y exportar» para esta receta (porcentaje y pendientes de la barra de progreso)."""
    from .progress import EXPORT, SAVE_PROJECT, VALIDATE, catalog_plan

    cat, choices = load_choices(recipe, catalog_root)
    return catalog_plan(choices, recipe_params(recipe, cat), hand_spec(recipe), epilogue=[VALIDATE, EXPORT, SAVE_PROJECT])


def load_choices(recipe: Recipe, catalog_root: str | None = None) -> tuple[Catalog, dict]:
    root = catalog_root or default_root(recipe.linea)
    try:
        cat = Catalog.open(root)
        if cat.name != recipe.linea:
            raise RecipeError(f"la receta es de la línea «{recipe.linea}» y el catálogo es el de «{cat.name}»")
        choices = {key: PartChoice.from_catalog(cat, recipe.lugares[lugar]) for lugar, key in recipe.parts_keys().items()}
    except CatalogError as exc:
        raise RecipeError(str(exc)) from exc
    keys = recipe.parts_keys()
    for lugar, k in recipe.scales().items():              # cada pieza se escala por separado, alrededor de su unión principal
        choices[keys[lugar]] = scale_choice(choices[keys[lugar]], k)
    return cat, recipe.apply_joint_options(choices)


def accessory_fit_grip(hand_agarre: dict | None, acc_meta, label: str | None) -> dict:
    """Cómo se lleva el accesorio con una mano (su clave «agarre»; `label` la nombra en los avisos; None = la figura no tiene
    mano): {nivel: ok | aviso | no | sin_datos, motivos, avisos}. «no» no impide acompañar a la figura (puede ser un accesorio
    que no se empuña): solo avisa que no se sujeta en esa mano."""
    from .props import grip_compat

    acc = (acc_meta.mecanismo or {}).get("agarre")
    if acc is None:
        return {"nivel": "sin_datos", "motivos": [], "avisos": ["el accesorio no tiene datos de agarre: no se sabe si se sujeta en la mano"]}
    if label is None:
        return {"nivel": "sin_datos", "motivos": [], "avisos": ["la figura no tiene una mano donde sujetarlo"]}
    if hand_agarre is None:
        return {"nivel": "no", "motivos": [f"la mano «{label}» no tiene agarre (taladro): el accesorio no se sujeta"], "avisos": []}
    c = grip_compat(hand_agarre, acc)
    return {"nivel": c.nivel, "motivos": list(c.motivos), "avisos": list(c.avisos)}


def accessory_fit(hand_meta, acc_meta) -> dict:
    """`accessory_fit_grip` con la pieza `mano` del catálogo (None = la figura no tiene mano de catálogo)."""
    if hand_meta is None:
        return accessory_fit_grip(None, acc_meta, None)
    return accessory_fit_grip((hand_meta.mecanismo or {}).get("agarre"), acc_meta, hand_meta.ref)


def load_accessories(recipe: Recipe, cat: Catalog) -> list[dict]:
    """Fichas de los accesorios de la receta: {meta, mesh (pose de impresión), ficha sin `archivo`}."""
    from .accessories import print_pose

    hand = cat.meta(recipe.lugares["mano"]) if recipe.lugares.get("mano") else None
    out = []
    for ref in recipe.accesorios:
        try:
            meta, mesh = cat.load(ref)
        except CatalogError as exc:
            raise RecipeError(f"accesorio «{ref}»: {exc}") from exc
        if meta.tipo != "accesorio":
            raise RecipeError(f"accesorio «{ref}»: es una pieza «{meta.tipo}», no un accesorio")
        fit = accessory_fit(hand, meta)
        out.append({"meta": meta, "mesh": print_pose(mesh),
                    "ficha": {"pieza": meta.ref, "id": meta.id, "version": meta.version, "sha256": meta.sha256,
                              "origen": meta.origen.get("figura") or meta.origen.get("nota") or "", "compat": fit["nivel"],
                              "motivos": fit["motivos"], "avisos": fit["avisos"]}})
    return out


def export_accessories(recipe: Recipe, cat: Catalog, outdir: str, name: str) -> list[dict]:
    """Escribe en `<outdir>/piezas/` el STL de cada accesorio de la receta (pose de impresión, mm reales) y devuelve una ficha por
    accesorio (pieza, sha256, archivo, compatibilidad con la mano de la figura). No toca la lista de piezas ni el ensamblado."""
    from .accessories import write_files

    items = load_accessories(recipe, cat)
    rel = write_files([(i["ficha"]["id"], i["mesh"]) for i in items], outdir, name)
    return [{**i["ficha"], "archivo": r} for i, r in zip(items, rel)]


def load_mounts(recipe: Recipe, cat: Catalog) -> list:
    """`[(MountSpec, arma Manifold)]` de las monturas de la receta, para `pipeline.build(mounts=)`.

    El accesorio del catálogo ya está en mm reales y en su marco de accesorio (empuñadura en +X): no se reescala."""
    out = []
    for spec in recipe.mount_specs():
        try:
            meta, mesh = cat.load(spec.arma)
        except CatalogError as exc:
            raise RecipeError(f"montura con el arma «{spec.arma}»: {exc}") from exc
        if meta.tipo != "accesorio":
            raise RecipeError(f"montura con el arma «{spec.arma}»: es una pieza «{meta.tipo}», no un accesorio")
        try:
            out.append((spec, S.to_manifold(mesh)))
        except S.MeshNotManifold as exc:
            raise RecipeError(f"montura con el arma «{spec.arma}»: {exc}") from exc
    return out


def assemble(recipe: Recipe, catalog_root: str | None = None, progress=None, sit: bool = True) -> Assembled:
    """Arma la figura de la receta solo con piezas del catálogo de su línea."""
    cat, choices = load_choices(recipe, catalog_root)
    p = recipe_params(recipe, cat)
    try:
        if progress:
            progress("Armando el cuerpo base…")
        host = synthesize_host(choices, p)
        baked = [ch.meta.ref for ch, _T in host.bodies.values() if ch.meta.montura]
        if baked and p.torso_mirror:                      # M17: el puente horneado es de un solo costado y el torso se arma espejado
            raise RecipeError(f"«{', '.join(baked)}» trae una montura de un solo costado: con «torso espejado» saldría en los dos; "
                              "armá con torso_mirror = false")
        mounts = load_mounts(recipe, cat) or None
        res = P.build(host.body, host.limbs, p, None, sit, progress, None, host.head, None, host.waist, hand_spec(recipe),
                      host.parts, host_faces=host.faces, mounts=mounts)
        if baked:
            res = _with_baked_mounts(res, cat, host, baked)
    except AssemblyError as exc:
        raise RecipeError(str(exc)) from exc
    except ValueError as exc:                             # `build` rotula los errores de monturas con «montura: …»
        if str(exc).startswith("montura:"):
            raise RecipeError(str(exc)) from exc
        raise
    return Assembled(res, p, host.limbs, res.joint_specs, recipe, cat, host)


def _with_baked_mounts(res, cat: Catalog, host, baked: list):
    """M17: anota en `res.mounts` las monturas que ya vienen horneadas en las piezas del torso, para que `validate` y el informe las
    traten como a las de la receta. Si el armado espejó el torso igual (no encontró el otro lado), el puente quedó duplicado: error."""
    from .mount import MountError
    from .mount_bake import baked_builds

    if (res.report.get("torso") or {}).get("espejado"):
        raise RecipeError(f"«{', '.join(baked)}» trae una montura de un solo costado y el armado espejó el torso igual "
                          f"({res.report['torso'].get('aviso', 'sin lado −X propio')}): el puente saldría en los dos costados")
    try:
        built = baked_builds(cat, host.bodies, res.pieces)
    except MountError as exc:
        raise RecipeError(f"montura: {exc}") from exc
    res.mounts = {**res.mounts, **built}
    res.report["monturas"] = {**res.report.get("monturas", {}), **{n: mb.info for n, mb in built.items()}}
    return res
