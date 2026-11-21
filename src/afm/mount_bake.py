"""M17: «guardar como pieza» (opción B del plan de enganche de armas, §6.5).

Hasta M16 la montura se **genera al armar**: `pipeline.build(mounts=)` la suma a la pelvis o al torso ya armados. Acá se hace
lo contrario: la montura se **hornea** en la malla cruda de una pieza del catálogo y se guarda como pieza nueva (como el
`torso_atras_v4` hecho a mano). Sirve para una combinación arma × torso que se usa siempre, o para llevar la montura a otra
figura sin repetir el ancla.

Lo que cambia respecto de la montura generada, y por qué:

* La pieza guardada sigue siendo **cruda**: el puente va en la malla, pero los pasadores, cavidades, clic y tallados se generan al
  armar como siempre. Por eso el puente pasa por `build` (tallado de barrido, decimación) y **hay que volver a validarlo** al
  armar: `baked_builds` reconstruye su `Lazo` (mismo `build_mount`, misma normal) y lo anota en `BuildResult.mounts` con
  `baked=True`, de modo que `pipeline._validate_mount` lo mide igual que a una montura de la receta.
* El torso se arma por **mitad +X espejada** (`torso_mirror`): un puente de un solo costado saldría en los dos. Una pieza con
  montura exige `torso_mirror = false` (`recipe.assemble` lo comprueba).
* Una pieza con montura es **otra pieza**: id propio (o versión nueva), sin color heredado (la geometría nueva no tiene fuente de
  color) y sin certificaciones heredadas (`estado = borrador`).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np

from . import mount as MT
from . import parts as P
from . import solids as S
from .catalog import Catalog, CatalogError, parse_ref
from .mount import MountError, MountSpec

PORT_MARGIN_MM = 0.5            # el puente no puede quedar a menos de (radio de la cara + esto) del centro de un puerto
BAKED_LOSS_TOL = 0.10           # fracción del puente que el armado puede haber tallado antes de dar error (la decimación quita muy poco)


@dataclass
class BakeResult:
    """Lo que dejó «guardar como pieza»: la ficha guardada, las líneas del informe, los avisos y los datos de la montura."""
    meta: object = None
    lines: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    info: dict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return self.meta.ref if self.meta is not None else ""


def _plain(x):
    """Lo mismo con tipos de Python (JSON no acepta los de numpy)."""
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, np.ndarray):
        return _plain(x.tolist())
    return x


def host_fits(lugar: str, tipo: str) -> bool:
    """¿Una pieza de ese tipo puede llevar una montura de ese `lugar`? (`pelvis`: la pelvis o el torso entero; `torso`: el torso o el pecho)."""
    return (lugar == "pelvis" and tipo in ("pelvis", "torso")) or (lugar == "torso" and tipo in ("torso", "torso_sup"))


def default_id(host_id: str, weapon_ref: str) -> str:
    """Id de la pieza con montura: `<huésped>-con-<arma>` (la versión del arma queda en la ficha, no en el id)."""
    return f"{host_id}-con-{parse_ref(weapon_ref)[0]}"


def _port_clashes(lazo: MT.Lazo, meta) -> list[str]:
    """Puertos cuya cara toca el puente (acoplar el miembro ahí ya no sería posible)."""
    verts = np.asarray(S.to_trimesh(lazo.outer).vertices, float)
    out = []
    for name, port in meta.puertos.items():
        r = float(port.face.radius_eq) if port.face is not None else 0.0
        d = float(np.linalg.norm(verts - np.asarray(port.frame.center, float), axis=1).min())
        if d < r + PORT_MARGIN_MM:
            out.append(f"el puente queda a {d:.2f} mm del centro del puerto «{name}» (radio de la cara {r:.2f} mm): taparía la unión")
    return out


def bake_mount(cat: Catalog, host_ref: str, spec: MountSpec, *, piece_id: str | None = None, new_version: bool = False,
               description: str = "", name: str = "") -> BakeResult:
    """Suma la montura `spec` a la malla cruda de `host_ref` y la guarda como pieza nueva del catálogo.

    `spec.ancla` y `spec.direccion` están en el marco de la pieza (el de la figura de origen, el mismo en el que se arma el torso). El
    arma `spec.arma` es un accesorio del catálogo. Si la base ya trae monturas y no se da `piece_id`, sale como versión nueva de esa pieza. Si algo no sirve (el puente no toca la pieza, la parte en trozos, tapa un puerto…)
    levanta `MountError` y **no se escribe nada**. Con `piece_id` ya existente hace falta `new_version=True`."""
    hmeta, hmesh = cat.load(host_ref)
    if hmeta.tipo not in P.MOUNT_HOST_TYPES:
        raise MountError(f"«{hmeta.ref}» es una pieza «{hmeta.tipo}»: solo {', '.join(P.MOUNT_HOST_TYPES)} pueden llevar una montura")
    if not host_fits(spec.lugar, hmeta.tipo):
        raise MountError(f"una montura en «{spec.lugar}» no va en una pieza «{hmeta.tipo}» "
                         f"({'pelvis o torso' if spec.lugar == 'pelvis' else 'torso o torso_sup'})")
    ameta, amesh = cat.load(spec.arma)
    if ameta.tipo != "accesorio":
        raise MountError(f"el arma «{spec.arma}» es una pieza «{ameta.tipo}», no un accesorio")
    try:
        host, prop = S.to_manifold(hmesh), S.to_manifold(amesh)
    except S.MeshNotManifold as exc:
        raise MountError(str(exc)) from exc
    min_wall = float(cat.params.min_wall)
    normal = MT.surface_normal(host, spec.ancla)
    if spec.lado == "der":                       # la montura se construye en +X: la normal también (como `apply_mounts`)
        normal = (-normal[0], normal[1], normal[2])
    lazo = MT.mirrored_for_side(MT.build_mount(spec, prop, min_wall, normal=normal), spec)
    out = MT.attach_lazo(host, lazo)
    clashes = _port_clashes(lazo, hmeta)
    if clashes:
        raise MountError("; ".join(clashes))

    warnings: list[str] = []
    obstacle = out if lazo.lips is None else S.difference(out, lazo.lips)       # los labios del clip ceden: no cuentan
    hit = float((lazo.weapon ^ obstacle).volume())
    if hit > MT.CONTACT_TOL_MM3:
        warnings.append(f"el arma enfundada se mete {hit:.3f} mm³ en la pieza: revisá el ancla y la dirección")
    warnings += [str(a) for a in lazo.info.get("avisos", [])]

    entry = {"spec": spec.to_dict(), "arma_sha256": ameta.sha256, "min_wall_mm": min_wall,
             "normal_construccion": [round(float(x), 6) for x in normal], "base": hmeta.ref, "info": _plain(lazo.info)}
    old = (hmeta.montura or {}).get("monturas", [])
    block = {"formato": P.MOUNT_BLOCK_FORMAT, "monturas": copy.deepcopy(old) + [entry]}

    nm = copy.deepcopy(hmeta)
    if piece_id is None and hmeta.montura:       # sumarle otra montura a una pieza que ya la trae: es la misma pieza, una versión nueva
        piece_id, new_version = hmeta.id, True
    nm.id = piece_id or default_id(hmeta.id, spec.arma)
    nm.version = 1
    nm.estado = "borrador"                      # una pieza nueva: nada de lo certificado de la base vale
    nm.etiquetas = ["propia"]
    nm.nombre = name
    nm.descripcion = description or f"{hmeta.display_name} con montura «{spec.tipo}» para {spec.arma}"
    nm.origen = P.make_origin(hmeta.origen.get("figura", ""), hmeta.origen.get("proyecto", ""),
                              note=f"{hmeta.ref} + montura {spec.arma} ({spec.lugar}, {spec.lado})")
    if hmeta.color:
        nm.color = None
        warnings.append(f"«{hmeta.ref}» tiene color y la pieza con montura no lo hereda: la geometría del puente no tiene fuente de color "
                        "(volvé a asignárselo a la pieza nueva)")
    nm.montura = block
    saved, w = cat.add_piece(S.to_printable_trimesh(out), nm, new_version)
    warnings += [x for x in w if "no se hereda entre versiones" not in x]       # el color ya se avisó arriba
    added = float(out.volume() - host.volume())
    lines = [f"Pieza con montura guardada como {saved.ref} (base {hmeta.ref} + arma {spec.arma}; el puente suma {added:.1f} mm³)",
             f"  {spec.tipo} en {spec.lugar} {spec.lado}: abertura {lazo.info.get('abertura_mm')} mm, "
             f"alto sobre la superficie {lazo.info.get('alto_sobre_superficie_mm')} mm",
             "  para armarla: «torso espejado» apagado (torso_mirror = false), si no el puente sale en los dos costados"]
    return BakeResult(saved, lines, warnings, {"puente_mm3": round(added, 3), **_plain(lazo.info)})


# ====================================================================== al armar
def _host_of(lazo: MT.Lazo, pieces: dict) -> tuple:
    """Pieza armada que lleva el puente (la que más lo contiene: con cintura puede haber quedado en el pecho o en la pelvis) y
    cuánto del puente falta en ella (mm³)."""
    from .waist import PELVIS_NAME

    cut = S.difference(lazo.outer, lazo.tunnel)           # el puente sin su hueco: lo que tiene que estar en la pieza
    total = float(cut.volume())
    best, best_vol = None, -1.0
    for n in ("torso", PELVIS_NAME):
        if n in pieces:
            v = float((cut ^ pieces[n]).volume())
            if v > best_vol:
                best, best_vol = n, v
    if best is None or best_vol < 1e-3:
        raise MountError("la montura no quedó en ninguna pieza del cuerpo: el armado la talló por completo")
    return best, max(0.0, total - best_vol), total


def baked_builds(cat: Catalog, bodies: dict, pieces: dict) -> dict:
    """`{nombre: MountBuild}` de las monturas horneadas en las piezas del torso (`SyntheticHost.bodies`: «torso»/«pelvis» →
    (pieza de catálogo, 4×4 pieza → huésped)) sobre las piezas ya armadas `pieces`. Vacío si ninguna pieza trae monturas.

    Rehace el `Lazo` con el mismo `build_mount` y la normal guardada (sale idéntico al horneado) y lo lleva al marco del huésped; no
    modifica `pieces`. `MountError` si el arma cambió desde que se guardó la pieza o si el armado le talló el puente."""
    out: dict = {}
    for slot, (choice, T) in bodies.items():
        block = choice.meta.montura
        if not block:
            continue
        for i, entry in enumerate(block["monturas"], start=1):
            spec = MountSpec.from_dict(entry["spec"])
            label = f"montura_pieza_{slot}_{i}_{spec.lugar}_{spec.lado}"
            try:
                ameta, amesh = cat.load(spec.arma)
            except CatalogError as exc:
                raise MountError(f"{label}: el arma «{spec.arma}» de la pieza «{choice.meta.ref}» no se puede cargar: {exc}") from exc
            if ameta.sha256 != entry["arma_sha256"]:
                raise MountError(f"{label}: el arma «{spec.arma}» cambió desde que se guardó «{choice.meta.ref}»: guardá la pieza de nuevo")
            lz = MT.mirrored_for_side(MT.build_mount(spec, S.to_manifold(amesh), float(entry["min_wall_mm"]),
                                                     normal=tuple(entry["normal_construccion"])), spec)
            lz = MT.transformed_lazo(lz, T)
            host, missing, total = _host_of(lz, pieces)
            if total > 0 and missing > BAKED_LOSS_TOL * total:
                raise MountError(f"{label}: el armado talló {missing:.2f} mm³ del puente ({100 * missing / total:.0f} %): "
                                 "lo come el tallado de un brazo o la cintura; mové la montura o cambiá esa parte")
            out[label] = MT.MountBuild(spec=spec, host=host, lazo=lz, info=dict(lz.info, host=host, pieza=choice.meta.ref,
                                                                                puente_faltante_mm3=round(missing, 4)), baked=True)
    return out


# ====================================================================== desde una receta
def host_slot(spec: MountSpec, lugares: dict) -> str:
    """Lugar de la receta (`lugares`) cuya pieza lleva la montura: `pelvis` si la figura tiene pelvis aparte (torso_sup + pelvis), si
    no el torso; una montura en el torso va siempre al lugar «torso»."""
    if spec.lugar == "pelvis" and lugares.get("pelvis"):
        return "pelvis"
    return "torso"


def bake_from_recipe(cat: Catalog, recipe, index: int, *, piece_id: str | None = None, new_version: bool = False,
                     description: str = "", name: str = ""):
    """Hornea la montura número `index` (desde 1) de `recipe.ajustes["monturas"]` en la pieza que la lleva y devuelve
    `(BakeResult, receta nueva)`: la receta nueva usa la pieza guardada en su lugar, ya no lista esa montura y lleva
    `torso_mirror = false`. No modifica `recipe`."""
    from .recipe import Recipe

    specs = recipe.mount_specs()
    if not 1 <= index <= len(specs):
        raise MountError(f"la receta tiene {len(specs)} montura(s): no hay la número {index}")
    spec = specs[index - 1]
    slot = host_slot(spec, recipe.lugares)
    host_ref = recipe.lugares.get(slot)
    if not host_ref:
        raise MountError(f"la receta no tiene pieza en «{slot}»: no hay dónde hornear la montura")
    res = bake_mount(cat, host_ref, spec, piece_id=piece_id, new_version=new_version, description=description, name=name)
    d = copy.deepcopy(recipe.to_dict())
    d["lugares"][slot] = res.ref
    d["opciones"]["torso_mirror"] = False
    rest = [m for i, m in enumerate(d["ajustes"].get("monturas", []), start=1) if i != index]
    if rest:
        d["ajustes"]["monturas"] = rest
    else:
        d["ajustes"].pop("monturas", None)
    return res, Recipe.from_dict(d)
