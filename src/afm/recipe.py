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
from .assembly import AssemblyError, PartChoice, synthesize_host
from .catalog import Catalog, CatalogError, default_root, parse_ref
from .customize import scale_choice
from .params import Params

RECIPE_FORMAT = "afm-receta-1"
PLACES = ("cabeza", "torso", "pelvis", "brazo", "pierna", "mano")
OPTIONS = {"cintura": False, "clic_hombro": False, "clic_cadera": False, "torso_mirror": True}
HAND_ADJUSTS = ("yaw", "union", "solape")
AJUSTES = ("mano", "escalas")
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

    # ------------------------------------------------------------------ formato
    def to_dict(self) -> dict:
        d = {"formato": RECIPE_FORMAT, "personaje": self.personaje, "linea": self.linea,
             "lugares": {k: self.lugares.get(k) for k in PLACES}, "opciones": {**OPTIONS, **self.opciones},
             "ajustes": self.ajustes}
        if self.accesorios:                                  # solo si hay: las recetas anteriores no cambian
            d["accesorios"] = list(self.accesorios)
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
        r = cls(d["personaje"], d["linea"], dict(d.get("lugares") or {}), dict(d.get("opciones") or {}), dict(d.get("ajustes") or {}),
                list(acc))
        bad = [k for k in r.problems()]
        if bad:
            raise RecipeError("; ".join(bad))
        return r

    @classmethod
    def load(cls, path: str) -> "Recipe":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)

    def problems(self) -> list[str]:
        out = [f"lugar desconocido «{k}» (hay {', '.join(PLACES)})" for k in self.lugares if k not in PLACES]
        out += [f"opción desconocida «{k}» (hay {', '.join(OPTIONS)})" for k in self.opciones if k not in OPTIONS]
        out += [f"ajuste desconocido «{k}» (hay {', '.join(AJUSTES)})" for k in self.ajustes if k not in AJUSTES]
        out += self.scale_problems()
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

    def scales(self) -> dict:
        """Lugar -> escala (número o [sx, sy, sz]) sin las que son ×1."""
        from .customize import is_identity, normalize_scale

        return {k: normalize_scale(v) for k, v in (self.ajustes.get("escalas") or {}).items() if not is_identity(v)}

    def options(self) -> dict:
        return {**OPTIONS, **self.opciones}

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
    return cat, choices


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


def assemble(recipe: Recipe, catalog_root: str | None = None, progress=None, sit: bool = True) -> Assembled:
    """Arma la figura de la receta solo con piezas del catálogo de su línea."""
    cat, choices = load_choices(recipe, catalog_root)
    p = recipe_params(recipe, cat)
    try:
        if progress:
            progress("Armando el cuerpo base…")
        host = synthesize_host(choices, p)
        res = P.build(host.body, host.limbs, p, None, sit, progress, None, host.head, None, host.waist, hand_spec(recipe),
                      host.parts, host_faces=host.faces)
    except AssemblyError as exc:
        raise RecipeError(str(exc)) from exc
    return Assembled(res, p, host.limbs, res.joint_specs, recipe, cat, host)
