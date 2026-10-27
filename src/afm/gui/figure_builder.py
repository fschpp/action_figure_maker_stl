"""Armar una figura desde cero, solo con piezas del catálogo (sin STL ni proyecto abiertos). Lógica sin GTK.

Es la versión de ventana de `afm armar`: se elige una pieza por lugar (torso, pelvis, cabeza, brazo, pierna, mano), se
genera, se valida y se exporta con el mismo motor (`cli.run_armar` → `recipe.assemble`). La receta (`afm-receta-1`) se puede
guardar en `<catálogo>/figuras/`, que es donde `Catalog.usages` y `afm catalogo compat` la buscan."""
from __future__ import annotations

import os
import re

import numpy as np

from ..catalog import Catalog, CatalogError
from ..customize import is_identity, normalize_scale, scale_vector
from ..hand import DEFAULT_OVERLAP_MM
from ..recipe import PLACES, SCALE_PLACES, Recipe, RecipeError

# lugar -> tipos de pieza que acepta (el torso puede ser entero o solo la parte de arriba)
PLACE_TYPES = {"torso": ("torso", "torso_sup"), "pelvis": ("pelvis",), "cabeza": ("cabeza",), "brazo": ("brazo",),
               "pierna": ("pierna",), "mano": ("mano",)}
PLACE_ORDER = ("torso", "pelvis", "cabeza", "brazo", "pierna", "mano")
PLACE_LABELS = {"torso": "Torso", "pelvis": "Pelvis", "cabeza": "Cabeza", "brazo": "Brazo", "pierna": "Pierna", "mano": "Mano"}
NONE_LABEL = "(ninguna)"
HAND_JOINS = ("solape", "giro")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_RANK = {"ok": 0, "aviso": 1, "no": 2}


class FigureBuilder:
    """Elecciones de una figura nueva sobre un catálogo abierto."""

    def __init__(self, catalog_root: str, name: str = "figura-nueva"):
        self.root = os.path.abspath(catalog_root)
        self.name = name
        self.choices: dict[str, str | None] = {p: None for p in PLACE_ORDER}      # lugar -> "id@versión"
        self.options = {"clic_hombro": False, "clic_cadera": False, "torso_mirror": True}
        self.hand = {"yaw": 0.0, "union": "solape", "solape": DEFAULT_OVERLAP_MM}     # ajustes de la mano (grados, unión, mm)
        self.scales: dict[str, list[float]] = {}                                        # lugar -> [sx, sy, sz] (solo los ≠ ×1)

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
            raise ValueError(f"«{place}» no es un lugar (hay {', '.join(PLACE_ORDER)})")
        if ref:
            meta = self.catalog().meta(ref)           # CatalogError (ValueError) si no existe
            if meta.tipo not in PLACE_TYPES[place]:
                raise ValueError(f"«{meta.ref}» es una pieza «{meta.tipo}» y «{place}» pide «{' o '.join(PLACE_TYPES[place])}»")
            ref = meta.ref                            # siempre fija la versión
        self.choices[place] = ref or None
        if place == "torso" and not self.waist:
            self.choices["pelvis"] = None             # un torso entero no lleva pelvis aparte

    # ------------------------------------------------------------------ ajustes de mano y escalas
    def set_hand(self, yaw: float | None = None, union: str | None = None, solape: float | None = None) -> None:
        """Giro extra de la mano (°) alrededor de la muñeca, tipo de unión («solape»: mano soldada al brazo; «giro»: mano
        aparte con pasador, gira libre) y cuánto se hunde la base de la mano en el brazo (mm, solo con «solape»)."""
        if yaw is not None:
            if not -360.0 <= float(yaw) <= 360.0:
                raise ValueError("el giro de la mano va de −360° a 360°")
            self.hand["yaw"] = float(yaw)
        if union is not None:
            if union not in HAND_JOINS:
                raise ValueError(f"unión de la mano «{union}» (hay {', '.join(HAND_JOINS)})")
            self.hand["union"] = union
        if solape is not None:
            if not 0.0 <= float(solape) <= 5.0:
                raise ValueError("el solape de la mano va de 0 a 5 mm")
            self.hand["solape"] = float(solape)

    def set_scale(self, place: str, factor) -> None:
        """Escala de la pieza de un lugar: un número (uniforme) o (sx, sy, sz) en X lateral, Y adelante-atrás, Z alto.
        ×1 en los tres ejes la quita. La pieza se escala alrededor de su unión principal y el resto se vuelve a acoplar."""
        if place not in SCALE_PLACES:
            raise ValueError(f"«{place}» no se escala (hay {', '.join(SCALE_PLACES)}; la mano va en mm reales)")
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
        return Recipe(self.name, cat.name, lugares, opciones, ajustes)

    def load_recipe(self, path: str) -> list[str]:
        """Carga una receta (`afm-receta-1`) para retocarla: piezas, opciones, mano y escalas pasan al constructor. Es de
        la línea de este catálogo y sus piezas tienen que existir. Devuelve avisos (cosas que no se pudieron cargar)."""
        try:
            r = Recipe.load(path)
        except (OSError, ValueError) as exc:                  # JSON roto, formato desconocido, lugares mal formados…
            raise ValueError(f"no se pudo abrir la receta: {exc}") from exc
        cat = self.catalog()
        if r.linea != cat.name:
            raise ValueError(f"la receta es de la línea «{r.linea}» y este catálogo es el de «{cat.name}»")
        warns: list[str] = []
        choices: dict[str, str | None] = {p: None for p in PLACE_ORDER}
        for place in PLACE_ORDER:
            ref = r.lugares.get(place)
            if not ref:
                continue
            try:
                meta = cat.meta(ref)
            except CatalogError as exc:
                warns.append(f"{place}: {exc} (queda sin pieza)")
                continue
            if meta.tipo not in PLACE_TYPES[place]:
                warns.append(f"{place}: «{meta.ref}» es una pieza «{meta.tipo}» (queda sin pieza)")
                continue
            choices[place] = meta.ref
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
                warns.append(f"la escala de «{place}» se descartó: ese lugar quedó sin pieza")
                del self.scales[place]
        return warns

    def problems(self) -> list[str]:
        """Lo que falta o sobra antes de poder armar (vacío = listo)."""
        out = []
        if not NAME_RE.match(self.name or ""):
            out.append("el nombre de la figura admite letras, números, guiones y puntos (sin espacios)")
        if not self.choices.get("torso"):
            return out + ["elegí un torso: es la pieza que arma el cuerpo"]
        if self.waist and not self.choices.get("pelvis"):
            out.append("el torso elegido es solo la parte de arriba: elegí también una pelvis")
        if self.choices.get("mano") and not self.choices.get("brazo"):
            out.append("la mano se acopla a la muñeca de un brazo: elegí también un brazo")
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
        os.makedirs(outdir, exist_ok=True)
        recipe_path = os.path.join(outdir, f"{self.name}.receta.json")
        self.recipe().save(recipe_path)
        info = run_armar(recipe_path, outdir, self.name, self.root, progress=progress, orient=orient)
        info["ensamblada"] = os.path.join(outdir, info["archivos"]["ensamblada"])
        return info


PREVIEW_COLORS = {"torso": (0.62, 0.70, 0.84, 1.0), "pelvis": (0.50, 0.62, 0.78, 1.0), "cabeza": (0.93, 0.78, 0.62, 1.0),
                  "brazo": (0.55, 0.78, 0.60, 1.0), "pierna": (0.84, 0.62, 0.62, 1.0), "mano": (0.95, 0.88, 0.45, 1.0)}


def _mirrored(mesh):
    """Copia de `mesh` espejada en X (el otro lado de la figura), con las caras bien orientadas."""
    m = mesh.copy()
    m.vertices = m.vertices * np.array([-1.0, 1.0, 1.0])
    m.invert()
    return m


def preview_parts(builder: "FigureBuilder") -> tuple[list, list[str]]:
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
        raise ValueError("elegí un torso para ver la figura")
    cat, ch = load_choices(builder.recipe(), builder.root)
    notes: list[str] = []
    out: list = []
    upper = ch.get("torso_sup") or ch["torso"]
    lower = ch.get("pelvis") if upper.meta.tipo == "torso_sup" else None
    out.append(("torso", upper.mesh.copy(), "torso", False))
    if lower is not None:
        cp, lp = upper.meta.puertos.get("cintura"), lower.meta.puertos.get("cintura")
        if cp is None or lp is None:
            notes.append("falta el puerto «cintura»: la pelvis no se puede colocar")
        else:
            out.append(("pelvis", transform_choice(lower, coupling_transform(lp.frame, cp.frame)).mesh, "pelvis", False))
    elif builder.choices.get("pelvis"):
        notes.append("el torso elegido es entero: la pelvis no se usa")
    if "cabeza" in ch:
        neck = upper.meta.puertos.get("cuello")
        if neck is None:
            notes.append(f"«{upper.meta.ref}» no tiene puerto «cuello»: la cabeza no se puede colocar")
        else:
            hc = ch["cabeza"]
            m = hc.mesh.copy()
            m.apply_transform(coupling_transform(hc.port("head").frame, neck.frame))
            out.append(("cabeza", m, "cabeza", False))
    for place, kind, owner, toma in (("brazo", "arm", upper, "hombro_izq"), ("pierna", "leg", lower or upper, "cadera_izq")):
        if place not in ch:
            continue
        port = owner.meta.puertos.get(toma)
        if port is None:
            notes.append(f"«{owner.meta.ref}» no tiene el puerto «{toma}»: {place} no se puede colocar")
            continue
        face = ContactFace(np.asarray(port.frame.center, float).copy(), -np.asarray(port.frame.axis, float), 0.0, None)
        try:
            T = coupling_transform(ch[place].port(kind).frame, host_frame(face))
        except AssemblyError as exc:
            notes.append(str(exc))
            continue
        m = ch[place].mesh.copy()
        m.apply_transform(T)
        out.append((place, m, place, True))
        wrist = ch[place].meta.puertos.get("muneca") if place == "brazo" else None
        if place == "brazo" and "mano" in ch:
            if wrist is None:
                notes.append("el brazo no trae puerto «muneca»: la mano no se puede colocar")
                continue
            wp = wrist.transformed(T)
            fr = WristFrame(wp.frame.center.copy(), wp.frame.axis.copy(), wp.frame.ref.copy()).twisted(builder.hand["yaw"])
            hand = S.to_printable_trimesh(place_hand(S.to_manifold(ch["mano"].mesh), fr))
            if builder.hand["union"] == "solape":
                hand.apply_translation(-fr.axis * builder.hand["solape"])
            out.append(("mano", hand, "mano", True))
    parts = []
    for name, mesh, key, both in out:
        parts.append((name, mesh, PREVIEW_COLORS[key]))
        if both:
            parts.append((f"{name}_der", _mirrored(mesh), PREVIEW_COLORS[key]))
    return parts, notes


def summary(info: dict) -> str:
    """Resumen legible del resultado de `FigureBuilder.generate` (validación y archivos)."""
    val = info.get("validacion", {})
    lines = ["Figura armada: " + ("sin problemas." if val.get("ok") else "CON PROBLEMAS.")]
    lines += [f"✗ {t}" for t in val.get("problems", [])[:10]]
    lines += [f"• {t}" for t in (val.get("avisos", []) + val.get("avisos_pasador", []))[:10]]
    lines.append(f"Piezas exportadas: {len(info.get('archivos', {}).get('piezas', []))} (más la figura ensamblada y la lista de piezas).")
    if info.get("proyecto"):
        lines.append(f"Proyecto: {os.path.basename(info['proyecto'])} (se abre en la ventana principal sin el STL).")
    return "\n".join(lines)
