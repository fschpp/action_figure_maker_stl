"""Armar una figura desde cero, solo con piezas del catálogo (sin STL ni proyecto abiertos). Lógica sin GTK.

Es la versión de ventana de `afm armar`: se elige una pieza por lugar (torso, pelvis, cabeza, brazo, pierna, mano), se
genera, se valida y se exporta con el mismo motor (`cli.run_armar` → `recipe.assemble`). La receta (`afm-receta-1`) se puede
guardar en `<catálogo>/figuras/`, que es donde `Catalog.usages` y `afm catalogo compat` la buscan."""
from __future__ import annotations
from ..i18n import tr, lz, loc

import dataclasses
import os
import re

import numpy as np

from ..catalog import Catalog, CatalogError
from ..customize import is_identity, normalize_scale, scale_vector
from ..hand import DEFAULT_OVERLAP_MM
from ..mount import DEFAULT_CLEARANCE, DEFAULT_STATIONS, MOUNT_PLACES, MountError, MountSpec
from ..recipe import PLACES, SCALE_PLACES, Recipe, RecipeError, accessory_fit

# lugar -> tipos de pieza que acepta (el torso puede ser entero o solo la parte de arriba)
PLACE_TYPES = {"torso": ("torso", "torso_sup"), "pelvis": ("pelvis",), "cabeza": ("cabeza",), "brazo": ("brazo",),
               "pierna": ("pierna",), "mano": ("mano",)}
PLACE_ORDER = ("torso", "pelvis", "cabeza", "brazo", "pierna", "mano")
PLACE_LABELS = {"torso": lz("figure_builder.torso"), "pelvis": lz("figure_builder.pelvis"), "cabeza": lz("common.cabeza"), "brazo": lz("figure_builder.brazo"), "pierna": lz("figure_builder.pierna"), "mano": lz("figure_builder.mano")}
NONE_LABEL = lz("common.ninguna")
HAND_JOINS = ("solape", "giro")
MOUNT_DEFAULT_DIRECTION = (0.0, 0.0, -1.0)      # empuñadura arriba y punta hacia abajo: la gravedad apoya la guarda en el puente (M07)
MOUNT_FIELDS_EDITABLE = ("arma", "lugar", "lado", "tipo", "ancla", "direccion", "holgura", "pared", "estaciones")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_RANK = {"ok": 0, "aviso": 1, "no": 2}
FIT_TEXT = {"ok": "ok", "aviso": lz("figure_builder.aviso"), "no": lz("figure_builder.no_se_sujeta"), "sin_datos": lz("figure_builder.sin_datos_de_agarre")}   # accesorio vs. mano elegida


class FigureBuilder:
    """Elecciones de una figura nueva sobre un catálogo abierto."""

    def __init__(self, catalog_root: str, name: str = "figura-nueva"):
        self.root = os.path.abspath(catalog_root)
        self.name = name
        self.choices: dict[str, str | None] = {p: None for p in PLACE_ORDER}      # lugar -> "id@versión"
        self.options = {"clic_hombro": False, "clic_cadera": False, "torso_mirror": True}
        self.hand = {"yaw": 0.0, "union": "solape", "solape": DEFAULT_OVERLAP_MM}     # ajustes de la mano (grados, unión, mm)
        self.scales: dict[str, list[float]] = {}                                        # lugar -> [sx, sy, sz] (solo los ≠ ×1)
        self.accessories: list[str] = []                                                # «id@versión» de los accesorios que acompañan a la figura
        self.mounts: list[MountSpec] = []                                               # M13: armas enganchadas al cuerpo (`Recipe.ajustes["monturas"]`)
        self._mount_report: tuple | None = None                                         # (monturas armadas, informe `monturas`) del último armado
        self.palette_override: dict[str, str] = {}                                      # T48: {rol: "#RRGGBB"} colores que cambian SOLO en esta figura
        self._color_tm_cache: dict = {}                                                 # ref -> (clave, fuente de color de la vista previa)
        self.flat = False                                                               # «imprimir sin soportes»: aplana piernas y cola, los brazos giran hacia adelante
        self.flat_depth: float | None = None                                            # altura del corte sobre la cola (mm); None = la de la línea (`Params.flat_depth`)
        self.fill_hip = False                                                           # con `flat`: el muslo se queda con la loncha de pelvis que saca el corte
        self._built: dict | None = None                                                 # T48: último armado (Colorizer, carpeta, nombre, geometría) para recolorear

    # ------------------------------------------------------------------ catálogo
    def catalog(self) -> Catalog:
        return Catalog.open(self.root)

    def pieces_for(self, place: str, cat: Catalog | None = None) -> list:
        """Todas las versiones de las piezas que sirven en ese lugar, por id y versión."""
        cat = cat or self.catalog()
        return sorted((m for m in cat.list(all_versions=True) if m.tipo in PLACE_TYPES[place]), key=lambda m: (m.id, m.version))

    def torso_type(self, cat: Catalog | None = None) -> str | None:
        ref = self.choices.get("torso")
        if not ref:
            return None
        try:
            return (cat or self.catalog()).meta(ref).tipo
        except CatalogError:
            return None

    @property
    def waist(self) -> bool:
        """Con un torso `torso_sup` la figura lleva cintura (y pelvis aparte); con un torso entero no."""
        return self.torso_type() == "torso_sup"

    def anchor_for(self, place: str) -> str | None:
        """Pieza ya elegida con la que `place` se acopla (para marcar cuáles de la lista encajan)."""
        c = self.choices
        if place in ("cabeza", "brazo", "pelvis"):
            return c.get("torso")
        if place == "pierna":
            return c.get("pelvis") if self.waist and c.get("pelvis") else c.get("torso")
        if place == "mano":
            return c.get("brazo")
        return None

    def fit_labels(self, place: str, cat: Catalog | None = None) -> dict[str, str]:
        """{ref: «ok» | «aviso» | «no acopla»} de las piezas de `place` respecto de la pieza elegida con la que se acopla
        (compatibilidad barata por puertos, sin generar). Vacío si todavía no hay con qué comparar."""
        cat = cat or self.catalog()
        anchor = self.anchor_for(place)
        if not anchor:
            return {}
        try:
            found = cat.compatible_with(anchor)
        except CatalogError:
            return {}
        best: dict[str, str] = {}
        for other, _na, _nb, comp in found:
            if best.get(other.ref) is None or _RANK[comp.nivel] < _RANK[best[other.ref]]:
                best[other.ref] = comp.nivel
        return {m.ref: best.get(m.ref, "no acopla") for m in self.pieces_for(place, cat)}

    def label(self, meta, fit: dict[str, str]) -> str:
        """Texto de una pieza en la lista: «brazo-a-01@2 — ok» (o sin marca si no hay con qué comparar)."""
        mark = fit.get(meta.ref)
        return meta.ref if not mark else f"{meta.ref} — {mark}"

    # ------------------------------------------------------------------ elecciones
    def set_choice(self, place: str, ref: str | None) -> None:
        if place not in PLACE_TYPES:
            raise ValueError(tr("figure_builder.place_no_es_un_lugar_hay", place=place, join=', '.join(PLACE_ORDER)))
        if ref:
            meta = self.catalog().meta(ref)           # CatalogError (ValueError) si no existe
            if meta.tipo not in PLACE_TYPES[place]:
                raise ValueError(tr("common.ref_es_una_pieza_tipo_y", ref=meta.ref, tipo=meta.tipo, place=place, join=' o '.join(PLACE_TYPES[place])))
            ref = meta.ref                            # siempre fija la versión
        self.choices[place] = ref or None
        if place == "torso" and not self.waist:
            self.choices["pelvis"] = None             # un torso entero no lleva pelvis aparte

    # ------------------------------------------------------------------ accesorios (acompañan a la figura; no van en el puño)
    def accessory_pieces(self, cat: Catalog | None = None) -> list:
        """Todas las versiones de las piezas «accesorio» del catálogo, por id y versión."""
        cat = cat or self.catalog()
        return sorted((m for m in cat.list(all_versions=True) if m.tipo == "accesorio"), key=lambda m: (m.id, m.version))

    def accessory_fits(self, cat: Catalog | None = None) -> dict[str, str]:
        """{ref: nivel} de cada accesorio respecto de la mano elegida (ok | aviso | no | sin_datos). Sirve para marcarlos en la
        lista: un accesorio que «no se sujeta» igual puede acompañar a la figura."""
        cat = cat or self.catalog()
        hand = cat.meta(self.choices["mano"]) if self.choices.get("mano") else None
        return {m.ref: accessory_fit(hand, m)["nivel"] for m in self.accessory_pieces(cat)}

    def accessory_label(self, meta, fits: dict[str, str]) -> str:
        return f"{meta.ref} — {loc(FIT_TEXT.get(fits.get(meta.ref), ''))}".rstrip(" —")

    def set_accessory(self, ref: str, on: bool = True) -> None:
        """Agrega (`on`) o quita un accesorio. Una sola versión por id: elegir otra versión reemplaza la anterior."""
        if on:
            meta = self.catalog().meta(ref)                          # CatalogError (ValueError) si no existe
            if meta.tipo != "accesorio":
                raise ValueError(tr("figure_builder.ref_es_una_pieza_tipo_no", ref=meta.ref, tipo=meta.tipo))
            self.accessories = [r for r in self.accessories if r.split("@")[0] != meta.id] + [meta.ref]
            self.mounts = [dataclasses.replace(m, arma=meta.ref) if m.arma.split("@")[0] == meta.id else m for m in self.mounts]
        else:
            pid = ref.split("@")[0]
            self.accessories = [r for r in self.accessories if r.split("@")[0] != pid]
            self.mounts = [m for m in self.mounts if m.arma.split("@")[0] != pid]          # sin el accesorio no hay arma que enganchar

    # ------------------------------------------------------------------ armas en el cuerpo (M13)
    def mount_places(self) -> tuple:
        """Lugares donde se puede enganchar un arma. `pelvis` sin cintura cae en el torso (`mount.resolve_host`)."""
        return tuple(MOUNT_PLACES[:2])

    def mount_weapons(self) -> list[str]:
        """Armas que se pueden enganchar: los accesorios marcados para la figura."""
        return list(self.accessories)

    def _mount_arma(self, arma: str) -> str:
        """Referencia completa del accesorio marcado que corresponde a `arma` (con o sin versión)."""
        pid = str(arma).split("@")[0]
        for ref in self.accessories:
            if ref.split("@")[0] == pid and ("@" not in str(arma) or ref == arma):
                return ref
        raise ValueError(tr("figure_builder.montura_arma_no_esta_entre_accesorios", arma=arma))

    def _make_mount(self, base: dict) -> MountSpec:
        d = dict(base)
        d["arma"] = self._mount_arma(d["arma"])
        if d.get("lugar") not in self.mount_places():
            raise ValueError(tr("figure_builder.montura_lugar_no_disponible", lugar=d.get("lugar"), hay=", ".join(self.mount_places())))
        try:
            return MountSpec.from_dict(d)
        except MountError as exc:
            raise ValueError(str(exc)) from exc

    def add_mount(self, arma: str, lugar: str = "pelvis", lado: str = "izq", ancla=(0.0, 0.0, 0.0), direccion=MOUNT_DEFAULT_DIRECTION,
                  holgura: float = DEFAULT_CLEARANCE, pared: float | None = None, estaciones: int = DEFAULT_STATIONS,
                  tipo: str = "lazo") -> int:
        """Engancha `arma` (un accesorio marcado) al `lugar` y `lado` del cuerpo, con un enganche `tipo` («lazo», «clip» o «cuna»). `ancla` es un punto de la superficie de la pieza (marco
        de la pieza) y `direccion` el eje empuñadura → punta; la punta hacia abajo es la que retiene. Devuelve el índice de la montura."""
        spec = self._make_mount({"arma": arma, "lugar": lugar, "lado": lado, "ancla": ancla, "direccion": direccion,
                                 "holgura": holgura, "pared": pared, "estaciones": estaciones, "tipo": tipo})
        self.mounts.append(spec)
        return len(self.mounts) - 1

    def set_mount(self, index: int, **campos) -> None:
        """Cambia campos de una montura (`MOUNT_FIELDS_EDITABLE`). No cambia nada si algo viene mal formado."""
        spec = self._mount_at(index)
        bad = sorted(k for k in campos if k not in MOUNT_FIELDS_EDITABLE)
        if bad:
            raise ValueError(tr("figure_builder.montura_campo_desconocido", campo=", ".join(bad), hay=", ".join(MOUNT_FIELDS_EDITABLE)))
        self.mounts[index] = self._make_mount({**spec.to_dict(), **campos})

    def remove_mount(self, index: int) -> None:
        self._mount_at(index)
        del self.mounts[index]

    def bake_mount(self, index: int, piece_id: str | None = None, new_version: bool = False):
        """M17 «guardar como pieza»: hornea la montura `index` en la malla cruda de la pieza que la lleva (`mount_bake.bake_mount`) y la
        guarda como pieza nueva del catálogo. La figura pasa a usar esa pieza en su lugar, la montura sale de la lista y «torso
        espejado» se apaga (el puente es de un solo costado). Devuelve el `BakeResult`. Si algo falla no cambia nada: ni el catálogo ni
        las elecciones."""
        from .. import mount_bake as MB

        spec = self._mount_at(index)
        slot = MB.host_slot(spec, self.choices)
        ref = self.choices.get(slot)
        if not ref:
            raise ValueError(tr("figure_builder.montura_sin_pieza_base", lugar=slot))
        try:
            res = MB.bake_mount(self.catalog(), ref, spec, piece_id=piece_id, new_version=new_version)
        except (MountError, CatalogError) as exc:
            raise ValueError(str(exc)) from exc
        self.choices[slot] = res.ref
        del self.mounts[index]
        self.options["torso_mirror"] = False
        self._mount_report = None
        return res

    def _mount_at(self, index: int) -> MountSpec:
        if not isinstance(index, int) or not 0 <= index < len(self.mounts):
            raise ValueError(tr("figure_builder.no_hay_una_montura_numero", n=index))
        return self.mounts[index]

    def mount_label(self, index: int) -> str:
        """Texto de una montura en la lista: «espada@1 — pelvis izq (23, 0, 38)», más el estado del último armado si lo hay."""
        m = self._mount_at(index)
        txt = f"{m.arma} — {m.lugar} {m.lado} ({', '.join(f'{c:g}' for c in m.ancla)})" + ("" if m.tipo == "lazo" else f" [{m.tipo}]")
        st = self.mount_status(index)
        return txt if st is None else f"{txt} — {st['nivel']}"

    def mount_status(self, index: int) -> dict | None:
        """Lo que dijo el último armado de la montura `index`: `{nivel, problemas, avisos}` (nivel «ok» | «aviso» | «no»). None si todavía no
        se armó con estas monturas: cambiar cualquiera borra el estado, porque ya no es de lo que se armó."""
        if self._mount_report is None or self._mount_report[0] != [m.to_dict() for m in self.mounts]:
            return None
        self._mount_at(index)
        for name, rec in self._mount_report[1].items():
            if name.startswith(f"montura_{index + 1}_"):
                return {"nivel": rec.get("nivel") or "ok", "problemas": list(rec.get("problemas") or []),
                        "avisos": list(rec.get("avisos") or rec.get("avisos_construccion") or [])}
        return None

    # ------------------------------------------------------------------ ajustes de mano y escalas
    def set_hand(self, yaw: float | None = None, union: str | None = None, solape: float | None = None) -> None:
        """Giro extra de la mano (°) alrededor de la muñeca, tipo de unión («solape»: mano soldada al brazo; «giro»: mano
        aparte con pasador, gira libre) y cuánto se hunde la base de la mano en el brazo (mm, solo con «solape»)."""
        if yaw is not None:
            if float(yaw) < -360.0:
                raise ValueError(tr("figure_builder.el_giro_de_la_mano_no"))
            self.hand["yaw"] = float(yaw)
        if union is not None:
            if union not in HAND_JOINS:
                raise ValueError(tr("figure_builder.union_de_la_mano_union_hay", union=union, join=', '.join(HAND_JOINS)))
            self.hand["union"] = union
        if solape is not None:
            if not 0.0 <= float(solape) <= 5.0:
                raise ValueError(tr("figure_builder.el_solape_de_la_mano_va"))
            self.hand["solape"] = float(solape)

    def set_scale(self, place: str, factor) -> None:
        """Escala de la pieza de un lugar: un número (uniforme) o (sx, sy, sz) en X lateral, Y adelante-atrás, Z alto.
        ×1 en los tres ejes la quita. La pieza se escala alrededor de su unión principal y el resto se vuelve a acoplar."""
        if place not in SCALE_PLACES:
            raise ValueError(tr("figure_builder.place_no_se_escala_hay_join", place=place, join=', '.join(SCALE_PLACES)))
        try:
            k = scale_vector(factor)
        except Exception as exc:                                                  # AssemblyError: se muestra tal cual
            raise ValueError(str(exc)) from exc
        if is_identity(k):
            self.scales.pop(place, None)
        else:
            self.scales[place] = [float(x) for x in k]

    def scale_of(self, place: str) -> list[float]:
        return list(self.scales.get(place, (1.0, 1.0, 1.0)))

    # ------------------------------------------------------------------ receta y armado
    def recipe(self) -> Recipe:
        cat = self.catalog()
        lugares = {p: self.choices.get(p) for p in PLACES}
        opciones = {"cintura": self.waist, **self.options}
        ajustes: dict = {}
        if lugares.get("mano"):
            h = {"yaw": self.hand["yaw"], "union": self.hand["union"]}
            if self.hand["union"] == "solape":
                h["solape"] = self.hand["solape"]
            ajustes["mano"] = h
        esc = {p: normalize_scale(k) for p, k in self.scales.items() if lugares.get(p)}
        if esc:
            ajustes["escalas"] = esc
        if self.mounts:
            ajustes["monturas"] = [m.to_dict() for m in self.mounts]
        return Recipe(self.name, cat.name, lugares, opciones, ajustes, list(self.accessories), self.effective_overrides(cat))

    def load_recipe(self, path: str) -> list[str]:
        """Carga una receta (`afm-receta-1`) para retocarla: piezas, opciones, mano y escalas pasan al constructor. Es de
        la línea de este catálogo y sus piezas tienen que existir. Devuelve avisos (cosas que no se pudieron cargar)."""
        try:
            r = Recipe.load(path)
        except (OSError, ValueError) as exc:                  # JSON roto, formato desconocido, lugares mal formados…
            raise ValueError(tr("figure_builder.no_se_pudo_abrir_la_receta", exc=exc)) from exc
        cat = self.catalog()
        if r.linea != cat.name:
            raise ValueError(tr("figure_builder.la_receta_es_de_la_linea", linea=r.linea, name=cat.name))
        warns: list[str] = []
        choices: dict[str, str | None] = {p: None for p in PLACE_ORDER}
        for place in PLACE_ORDER:
            ref = r.lugares.get(place)
            if not ref:
                continue
            try:
                meta = cat.meta(ref)
            except CatalogError as exc:
                warns.append(tr("figure_builder.place_exc_queda_sin_pieza", place=place, exc=exc))
                continue
            if meta.tipo not in PLACE_TYPES[place]:
                warns.append(tr("figure_builder.place_ref_es_una_pieza_tipo", place=place, ref=meta.ref, tipo=meta.tipo))
                continue
            choices[place] = meta.ref
        self.accessories = []
        for ref in r.accesorios:
            try:
                self.set_accessory(ref, True)
            except ValueError as exc:
                warns.append(tr("figure_builder.accesorio_exc_se_descarto", exc=exc))
        self.mounts = []
        self._mount_report = None
        for i, raw in enumerate(r.ajustes.get("monturas") or [], start=1):
            try:
                self.mounts.append(self._make_mount(raw))
            except (ValueError, MountError, TypeError, AttributeError) as exc:
                warns.append(tr("figure_builder.montura_n_se_descarto", n=i, exc=exc))
        self.name = r.personaje
        self.choices = choices
        if not self.waist:
            self.choices["pelvis"] = None
        o = r.options()
        self.options = {k: bool(o[k]) for k in ("clic_hombro", "clic_cadera", "torso_mirror")}
        self.hand = {"yaw": 0.0, "union": "solape", "solape": DEFAULT_OVERLAP_MM}
        self.set_hand(**{k: v for k, v in (r.ajustes.get("mano") or {}).items() if k in self.hand})
        self.scales = {}
        for place, k in (r.ajustes.get("escalas") or {}).items():
            if place in SCALE_PLACES:
                self.set_scale(place, k)
        for place in list(self.scales):
            if not choices.get(place):
                warns.append(tr("figure_builder.la_escala_de_place_se_descarto", place=place))
                del self.scales[place]
        self.palette_override = {}
        for role, hx in (r.paleta_override or {}).items():                   # T48: cambios de color de esta figura
            try:
                self.set_override(role, hx, cat)
            except ValueError as exc:
                warns.append(tr("figure_builder.cambio_de_color_descartado", role=role, exc=exc))
        return warns

    # ------------------------------------------------------------------ color: paleta de la línea y cambios de esta figura (T48)
    def _chosen_metas(self, cat: Catalog | None = None) -> list:
        """Fichas de las piezas elegidas (lugares y accesorios no: el accesorio sale sin color) que existen en el catálogo."""
        cat = cat or self.catalog()
        out = []
        for place in PLACE_ORDER:
            ref = self.choices.get(place)
            if ref:
                try:
                    out.append(cat.meta(ref))
                except CatalogError:
                    pass
        return out

    def palette_roles_used(self, cat: Catalog | None = None) -> dict[str, list[str]]:
        """{rol: [piezas elegidas que lo usan]} según la ficha (`color.roles`), sin abrir ningún paquete."""
        used: dict[str, list[str]] = {}
        for meta in self._chosen_metas(cat):
            color = getattr(meta, "color", None) or {}
            if color.get("modo", "rgb") == "roles":
                for role in color.get("roles") or []:
                    used.setdefault(role, []).append(meta.ref)
        return used

    def effective_overrides(self, cat: Catalog | None = None) -> dict[str, str]:
        """Los cambios de color de la figura que de verdad cambian algo: roles que la paleta tiene y con otro color que el de la línea."""
        try:
            pal = (cat or self.catalog()).palette()
        except CatalogError:                                         # paleta.json ilegible: la receta sale sin cambios (el armado lo avisa)
            return {}
        if pal is None:
            return {}
        return {r: h for r, h in sorted(self.palette_override.items()) if r in pal.ids() and h != pal.hex_of(r)}

    def palette_rows(self, cat: Catalog | None = None) -> list[dict]:
        """Una fila por rol de la paleta de la línea para el bloque «Colores»: `{id, nombre, hex_linea, hex, cambiado, piezas}`.
        `hex` es el que va a tener en la figura (el cambio, si hay); `piezas`, las piezas elegidas que lo usan. Vacío si la línea no tiene paleta."""
        cat = cat or self.catalog()
        pal = cat.palette()
        if pal is None:
            return []
        used = self.palette_roles_used(cat)
        rows = []
        for r in pal.roles:
            hx = self.palette_override.get(r.id, r.hex)
            rows.append({"id": r.id, "nombre": r.nombre, "hex_linea": r.hex, "hex": hx, "cambiado": hx != r.hex, "piezas": list(used.get(r.id, []))})
        return rows

    def set_override(self, role: str, hex_color: str, cat: Catalog | None = None) -> None:
        """Cambia el color de `role` solo para esta figura (la paleta de la línea no se toca). Un rol que la paleta no tiene o un color que no es
        `#RRGGBB` es `ValueError`. Volver al color de la línea quita el cambio."""
        from ..palette import normalize_hex

        pal = (cat or self.catalog()).palette()
        if pal is None or role not in pal.ids():
            raise ValueError(tr("figure_builder.la_paleta_no_tiene_el_rol", role=role))
        hx = normalize_hex(hex_color, tr("figure_builder.color_del_rol", role=role))        # PaletteError (ValueError)
        if hx == pal.hex_of(role):
            self.palette_override.pop(role, None)
        else:
            self.palette_override[role] = hx

    def clear_override(self, role: str | None = None) -> None:
        """Quita el cambio de un rol (o todos) y vuelve al color de la línea."""
        if role is None:
            self.palette_override.clear()
        else:
            self.palette_override.pop(role, None)

    def color_missing(self, cat: Catalog | None = None) -> dict[str, list[str]]:
        """{rol: piezas} de los roles que usan las piezas elegidas y la paleta de la línea no tiene (o {rol: piezas} de todos si no hay
        paleta). Con algo acá el armado se frena antes de empezar: no se inventa un color."""
        cat = cat or self.catalog()
        try:
            pal = cat.palette()
        except CatalogError:
            pal = None
        have = set(pal.ids()) if pal is not None else set()
        return {r: p for r, p in sorted(self.palette_roles_used(cat).items()) if r not in have}

    def color_report(self, cat: Catalog | None = None) -> dict:
        """Informe previo del color de lo elegido, sin generar nada: `{faltan: {rol: piezas}, sin_color: [refs], rgb: [refs], escaladas: [refs],
        con_roles: [refs]}`. `sin_color`: sin FBX ni roles (salen en color plano); `rgb`: con su FBX propio (T35, no se recolorean);
        `escaladas`: con color pero la receta las escala, así que el color no se lleva (como `catalog_color_sources`)."""
        cat = cat or self.catalog()
        scaled = set(self.scales)
        rep: dict = {"faltan": self.color_missing(cat), "sin_color": [], "rgb": [], "escaladas": [], "con_roles": []}
        for meta in self._chosen_metas(cat):
            color = getattr(meta, "color", None)
            if not color:
                rep["sin_color"].append(meta.ref)
            elif meta.tipo in scaled:
                rep["escaladas"].append(meta.ref)
            elif color.get("modo", "rgb") == "roles":
                rep["con_roles"].append(meta.ref)
            else:
                rep["rgb"].append(meta.ref)
        return rep

    def color_report_lines(self, cat: Catalog | None = None) -> list[str]:
        """El informe previo en frases (para la ventana)."""
        rep = self.color_report(cat)
        out = []
        for role, refs in rep["faltan"].items():
            out.append("✗ " + tr("figure_builder.falta_el_rol_en_la_paleta", role=role, refs=", ".join(refs)))
        if rep["sin_color"]:
            out.append("• " + tr("figure_builder.sin_color_propio", refs=", ".join(rep["sin_color"])))
        if rep["rgb"]:
            out.append("• " + tr("figure_builder.color_rgb_no_se_recolorea", refs=", ".join(rep["rgb"])))
        if rep["escaladas"]:
            out.append("• " + tr("figure_builder.color_escalada", refs=", ".join(rep["escaladas"])))
        return out

    def can_recolor(self) -> bool:
        """Hay una figura armada con color por roles y lo elegido sigue siendo lo que se armó (mismas piezas, opciones, mano y escalas)."""
        b = self._built
        return bool(b and b["colorizer"] is not None and b["geom"] == self._geometry_key())

    def overrides_pending(self) -> bool:
        """Los cambios de color de ahora difieren de los que tiene la figura armada."""
        b = self._built
        return bool(b) and self.effective_overrides() != b["overrides"]

    def _geometry_key(self) -> str:
        """Todo lo que define la geometría de lo armado (la receta sin el nombre ni los cambios de color)."""
        import json

        d = self.recipe().to_dict()
        d.pop("personaje", None)
        d.pop("paleta_override", None)
        d["_sin_soportes"] = [bool(self.flat), self.flat_depth, bool(self.fill_hip)] if self.flat else None      # el corte plano cambia las piezas
        return json.dumps(d, sort_keys=True, default=str)

    def recolor(self) -> dict:
        """Aplica los cambios de color a la figura ya armada **sin volver a armar**: `Colorizer.recolor` rehace los atlas (PNG, GLB e informe de
        color) y los STL no se tocan. Usa la paleta de la línea tal como está ahora en disco más los cambios de esta figura. Si lo elegido ya no
        es lo armado, `ValueError` (hay que armar de nuevo). Devuelve lo de `Colorizer.recolor` más `glb` (ruta del GLB ensamblado)."""
        from ..palette import PaletteError

        b = self._built
        if not b or b["colorizer"] is None:
            raise ValueError(tr("figure_builder.no_hay_una_figura_armada_con_color"))
        if not self.can_recolor():
            raise ValueError(tr("figure_builder.lo_elegido_cambio_desde_el_armado"))
        cat = self.catalog()
        pal = cat.palette()
        if pal is None:
            raise ValueError(tr("figure_builder.la_linea_no_tiene_paleta"))
        over = self.effective_overrides(cat)
        try:
            out = b["colorizer"].recolor(pal, over, rewrite=True)
        except PaletteError as exc:                                              # a la paleta le falta un rol: no cambió nada
            raise ValueError(str(exc)) from exc
        b["overrides"] = dict(over)
        self._rewrite_reports(b, over)
        glb = (out.get("salida") or {}).get("glb")
        out["glb"] = os.path.join(b["outdir"], glb) if glb else None
        return out

    def _rewrite_reports(self, built: dict, over: dict) -> None:
        """Deja al día la paleta usada en `<nombre>_informe.json` y los cambios en `<nombre>.receta.json` de la carpeta de salida."""
        import json

        rep_path = os.path.join(built["outdir"], f"{built['name']}_informe.json")
        try:
            with open(rep_path, encoding="utf-8") as fh:
                rep = json.load(fh)
            if isinstance(rep.get("color"), dict):
                rep["color"]["paleta"] = dict(built["colorizer"].palette_report or {})
                with open(rep_path, "w", encoding="utf-8") as fh:
                    json.dump(rep, fh, indent=2, default=float)
            rec_path = os.path.join(built["outdir"], f"{built['name']}.receta.json")
            if os.path.isfile(rec_path):
                self.recipe().save(rec_path)
        except (OSError, ValueError):                                            # los informes son accesorios: el recoloreo ya quedó hecho
            pass

    def problems(self) -> list[str]:
        """Lo que falta o sobra antes de poder armar (vacío = listo)."""
        out = []
        if not NAME_RE.match(self.name or ""):
            out.append(tr("figure_builder.el_nombre_de_la_figura_admite"))
        if not self.choices.get("torso"):
            return out + [tr("figure_builder.elegi_un_torso_es_la_pieza")]
        if self.waist and not self.choices.get("pelvis"):
            out.append(tr("figure_builder.el_torso_elegido_es_solo_la"))
        if self.choices.get("mano") and not self.choices.get("brazo"):
            out.append(tr("figure_builder.la_mano_se_acopla_a_la"))
        out += self.recipe().problems()
        return list(dict.fromkeys(out))

    def save_recipe(self) -> str:
        """Guarda la receta en `<catálogo>/figuras/<nombre>.receta.json` (así los usos de cada pieza la incluyen)."""
        bad = self.problems()
        if bad:
            raise ValueError("; ".join(bad))
        folder = os.path.join(self.root, "figuras")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{self.name}.receta.json")
        self.recipe().save(path)
        return path

    def _color_tm(self, cat: Catalog, meta):
        """Fuente de color de la pieza (en el marco de su STL) con los cambios de paleta de esta figura; se recuerda por pieza, color, paleta y cambios."""
        from ..colorpreview import piece_color_source_tm

        over = self.effective_overrides(cat)
        pal = cat.palette()
        key = (meta.ref, str((meta.color or {}).get("sha256", "")), pal.digest() if pal is not None else "", tuple(sorted(over.items())),
               str(((meta.color or {}).get("retoque") or {}).get("sha256", "")))
        hit = self._color_tm_cache.get(meta.ref)
        if hit is not None and hit[0] == key:
            return hit[1]
        tm = piece_color_source_tm(cat, meta, over)
        if len(self._color_tm_cache) >= 12:
            self._color_tm_cache.pop(next(iter(self._color_tm_cache)))
        self._color_tm_cache[meta.ref] = (key, tm)
        return tm

    def _saved_retouch(self, cat: Catalog, meta):
        """Retoque manual guardado de la pieza (R05) o `None`. Un retoque ilegible no frena la vista previa: la pieza se ve sin él."""
        if not (meta.color or {}).get("retoque"):
            return None
        try:
            return cat.retouch(meta.ref)
        except ValueError:
            return None

    def piece_color_parts(self, meta, mesh):
        """`[(nombre, malla, rgba)]` de la pieza `meta` sola (en su marco), con el color de la figura; `None` si no lleva color."""
        from ..colorpreview import color_parts

        cat = self.catalog()
        tm = self._color_tm(cat, meta) if getattr(meta, "color", None) else None
        return None if tm is None else color_parts(tm, mesh, prefix=f"{meta.id}_c", retouch=self._saved_retouch(cat, meta))

    def placed_color_parts(self, meta, sides):
        """Partes de color de la pieza `meta` ya puesta en la figura: `sides` = [(nombre, malla puesta)] (el lado izquierdo y, si hay, su espejo).
        Cada malla puesta es la del STL de la pieza movida por una transformación afín (giro, traslado, escala, espejo): se recupera esa matriz
        comparando vértices y la misma se aplica a la fuente de color. `None` si no se pudo (otra cantidad de vértices): la pieza va de color plano."""
        from ..colorpreview import color_parts, fit_affine
        from ..retouch import Retouch, transform_strokes

        cat = self.catalog()
        tm = self._color_tm(cat, meta)
        if tm is None:
            return None
        rt = self._saved_retouch(cat, meta)
        _m, orig = cat.load(meta.ref, check_hash=False)
        out: list = []
        for name, placed in sides:
            M = fit_affine(orig.vertices, placed.vertices)
            if M is None:
                return None
            out.extend(color_parts(tm.transformed(M), placed, prefix=f"{name}_c",
                                   retouch=None if rt is None else Retouch(transform_strokes(rt.strokes, M))))
        return out

    def preview_color(self) -> tuple[list, list[str]]:
        """Vista previa conjunta con el color de cada pieza (ver `preview_color_parts`). Puede tardar unos segundos."""
        return preview_color_parts(self)

    def has_colored_choice(self) -> bool:
        """¿Alguna pieza elegida lleva color? (para decidir si vale la pena calcular la vista previa a color)."""
        return any(getattr(m, "color", None) for m in self._chosen_metas())

    def preview(self) -> tuple[list, list[str]]:
        """Vista previa conjunta de las piezas elegidas (ver `preview_parts`)."""
        return preview_parts(self)

    def progress_stages(self) -> list:
        """Pasos esperados de «Armar y exportar» con las piezas elegidas (para la barra de progreso)."""
        from ..recipe import progress_stages

        return progress_stages(self.recipe(), self.root)

    def generate(self, outdir: str, progress=print, orient: bool | str = False) -> dict:
        """Arma, valida y exporta en `outdir` (lo mismo que `afm armar`). Devuelve el informe: {"receta", "construccion",
        "validacion", "archivos"} más `ensamblada` (ruta del STL de la figura entera, para la vista previa). Tarda minutos."""
        from ..cli import run_armar

        bad = self.problems()
        if bad:
            raise ValueError("; ".join(bad))
        missing = self.color_missing()
        if missing:                                       # T48: antes de gastar minutos armando (run_armar también lo frena, pero después de armar)
            raise ValueError(tr("figure_builder.a_la_paleta_le_faltan_roles", roles="; ".join(f"{r} ({', '.join(p)})" for r, p in missing.items())))
        os.makedirs(outdir, exist_ok=True)
        recipe_path = os.path.join(outdir, f"{self.name}.receta.json")
        self.recipe().save(recipe_path)
        keep: dict = {}
        self._built = None
        flat = None if not self.flat else (-1.0 if self.flat_depth is None else float(self.flat_depth))      # -1: la altura de la línea
        info = run_armar(recipe_path, outdir, self.name, self.root, progress=progress, orient=orient, keep=keep, flat=flat,
                         fill_hip=bool(self.flat and self.fill_hip))
        info["ensamblada"] = os.path.join(outdir, info["archivos"]["ensamblada"])
        self._mount_report = ([m.to_dict() for m in self.mounts], info["monturas"]) if info.get("monturas") else None
        self._built = {"colorizer": keep.get("colorizer"), "outdir": outdir, "name": self.name, "geom": self._geometry_key(),
                       "overrides": self.effective_overrides()}
        return info


PREVIEW_COLORS = {"torso": (0.62, 0.70, 0.84, 1.0), "pelvis": (0.50, 0.62, 0.78, 1.0), "cabeza": (0.93, 0.78, 0.62, 1.0),
                  "brazo": (0.55, 0.78, 0.60, 1.0), "pierna": (0.84, 0.62, 0.62, 1.0), "mano": (0.95, 0.88, 0.45, 1.0)}


def _mirrored(mesh):
    """Copia de `mesh` espejada en X (el otro lado de la figura), con las caras bien orientadas."""
    m = mesh.copy()
    m.vertices = m.vertices * np.array([-1.0, 1.0, 1.0])
    m.invert()
    return m


def _place_parts(builder: "FigureBuilder") -> tuple[list, list[str]]:
    """Todas las piezas elegidas puestas juntas, sin generar nada (solo transformaciones rígidas, instantáneo).

    Devuelve ([(nombre, malla, color)], avisos). Cada pieza va en su puerto —la pelvis en la cintura del torso, la cabeza en
    el cuello, el brazo y la pierna en las tomas `hombro_izq` / `cadera_izq`, la mano en la muñeca del brazo— con sus escalas
    y el giro / solape de la mano; el otro lado sale por espejo. No incluye pasadores, holguras ni cavidades (eso lo hace
    «Armar y exportar»): sirve para ver proporciones, uniones y escalas antes de esperar minutos."""
    from ..assembly import AssemblyError, transform_choice
    from ..hand import WristFrame, place_hand
    from ..limbs import ContactFace
    from ..ports import coupling_transform
    from .. import solids as S
    from ..assembly import host_frame
    from ..recipe import load_choices

    if not builder.choices.get("torso"):
        raise ValueError(tr("figure_builder.elegi_un_torso_para_ver_la"))
    cat, ch = load_choices(builder.recipe(), builder.root)
    notes: list[str] = []
    out: list = []
    upper = ch.get("torso_sup") or ch["torso"]
    lower = ch.get("pelvis") if upper.meta.tipo == "torso_sup" else None
    out.append(("torso", upper.mesh.copy(), "torso", False, upper.meta))
    if lower is not None:
        cp, lp = upper.meta.puertos.get("cintura"), lower.meta.puertos.get("cintura")
        if cp is None or lp is None:
            notes.append(tr("figure_builder.falta_el_puerto_cintura_la_pelvis"))
        else:
            out.append(("pelvis", transform_choice(lower, coupling_transform(lp.frame, cp.frame)).mesh, "pelvis", False, lower.meta))
    elif builder.choices.get("pelvis"):
        notes.append(tr("figure_builder.el_torso_elegido_es_entero_la"))
    if "cabeza" in ch:
        neck = upper.meta.puertos.get("cuello")
        if neck is None:
            notes.append(tr("figure_builder.ref_no_tiene_puerto_cuello_la", ref=upper.meta.ref))
        else:
            hc = ch["cabeza"]
            m = hc.mesh.copy()
            m.apply_transform(coupling_transform(hc.port("head").frame, neck.frame))
            out.append(("cabeza", m, "cabeza", False, hc.meta))
    for place, kind, owner, toma in (("brazo", "arm", upper, "hombro_izq"), ("pierna", "leg", lower or upper, "cadera_izq")):
        if place not in ch:
            continue
        port = owner.meta.puertos.get(toma)
        if port is None:
            notes.append(tr("figure_builder.ref_no_tiene_el_puerto_toma", ref=owner.meta.ref, toma=toma, place=place))
            continue
        face = ContactFace(np.asarray(port.frame.center, float).copy(), -np.asarray(port.frame.axis, float), 0.0, None)
        try:
            T = coupling_transform(ch[place].port(kind).frame, host_frame(face))
        except AssemblyError as exc:
            notes.append(str(exc))
            continue
        m = ch[place].mesh.copy()
        m.apply_transform(T)
        out.append((place, m, place, True, ch[place].meta))
        wrist = ch[place].meta.puertos.get("muneca") if place == "brazo" else None
        if place == "brazo" and "mano" in ch:
            if wrist is None:
                notes.append(tr("figure_builder.el_brazo_no_trae_puerto_muneca"))
                continue
            wp = wrist.transformed(T)
            fr = WristFrame(wp.frame.center.copy(), wp.frame.axis.copy(), wp.frame.ref.copy()).twisted(builder.hand["yaw"])
            hand = S.to_printable_trimesh(place_hand(S.to_manifold(ch["mano"].mesh), fr))
            if builder.hand["union"] == "solape":
                hand.apply_translation(-fr.axis * builder.hand["solape"])
            out.append(("mano", hand, "mano", True, ch["mano"].meta))
    return out, notes


def preview_parts(builder: "FigureBuilder") -> tuple[list, list[str]]:
    """Todas las piezas elegidas puestas juntas, sin generar nada (solo transformaciones rígidas, instantáneo).

    Devuelve ([(nombre, malla, color)], avisos). Cada pieza va en su puerto —la pelvis en la cintura del torso, la cabeza en
    el cuello, el brazo y la pierna en las tomas `hombro_izq` / `cadera_izq`, la mano en la muñeca del brazo— con sus escalas
    y el giro / solape de la mano; el otro lado sale por espejo. No incluye pasadores, holguras ni cavidades (eso lo hace
    «Armar y exportar»): sirve para ver proporciones, uniones y escalas antes de esperar minutos. Cada tipo de pieza va de un color
    plano propio; para ver el color que lleva cada pieza, `preview_color_parts`."""
    out, notes = _place_parts(builder)
    parts = []
    for name, mesh, key, both, _meta in out:
        parts.append((name, mesh, PREVIEW_COLORS[key]))
        if both:
            parts.append((f"{name}_der", _mirrored(mesh), PREVIEW_COLORS[key]))
    return parts, notes


def preview_color_parts(builder: "FigureBuilder") -> tuple[list, list[str]]:
    """Como `preview_parts`, pero las piezas que llevan color (`PieceMeta.color`) salen partidas por color, con el de la figura (incluidos los
    cambios de la paleta de esta figura). Las que no llevan color, o cuyo color no se pudo llevar a su lugar, van del color plano de su tipo.
    Tarda algo más que `preview_parts` (se llama desde un hilo). Devuelve ([(nombre, malla, rgba)], avisos)."""
    out, notes = _place_parts(builder)
    parts: list = []
    for name, mesh, key, both, meta in out:
        sides = [(name, mesh)] + ([(f"{name}_der", _mirrored(mesh))] if both else [])
        colored = None
        if getattr(meta, "color", None):
            try:
                colored = builder.placed_color_parts(meta, sides)
            except (ValueError, OSError) as exc:                 # sin archivo de color, paleta sin roles…: se ve de color plano y se avisa
                notes.append(tr("figure_builder.color_no_disponible", ref=meta.ref, exc=exc))
        if colored is None:
            parts.extend((n, m, PREVIEW_COLORS[key]) for n, m in sides)
        else:
            parts.extend(colored)
    return parts, notes


def summary(info: dict) -> str:
    """Resumen legible del resultado de `FigureBuilder.generate` (validación y archivos)."""
    val = info.get("validacion", {})
    lines = [(tr("figure_builder.figura_armada") + ' ') + (tr("figure_builder.sin_problemas") if val.get("ok") else tr("figure_builder.con_problemas"))]
    lines += [f"✗ {t}" for t in val.get("problems", [])[:10]]
    lines += [f"• {t}" for t in (val.get("avisos", []) + val.get("avisos_pasador", []))[:10]]
    lines.append(tr("figure_builder.piezas_exportadas_n_mas_la_figura", n=len(info.get('archivos', {}).get('piezas', []))))
    ss = (info.get("archivos") or {}).get("sin_soportes")
    if ss:
        giro = ", ".join(f"{k} {v:g}°" for k, v in (ss.get("giro_brazos_grados") or {}).items()) or "0°"
        lines.append(tr("figure_builder.sin_soportes_resumen", corte=f"{ss['corte_sobre_cola_mm']:g}", giro=giro))
        lines += [f"• {t}" for t in ss.get("avisos", [])[:5]]
    if info.get("proyecto"):
        lines.append(tr("figure_builder.proyecto_basename_se_abre_en_la", basename=os.path.basename(info['proyecto'])))
    return "\n".join(lines)
