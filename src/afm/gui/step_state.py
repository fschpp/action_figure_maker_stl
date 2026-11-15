"""Estado de cada paso del riel lateral (UX etapa 5b): ○ pendiente · ✓ listo · ⚠ falta algo · ⟳ desactualizado · ▶ listo para generar · · opcional.

Todo sale de lo que el controlador ya sabe (`problems()`, `outcome_stale()`, el proyecto y los parámetros): acá no hay lógica nueva de la
figura, solo se resume en una palabra y una línea por paso. No importa GTK: se prueba sin ventana. Ver `gui/steps/rail.py`."""
from __future__ import annotations
from ..i18n import tr, lz, loc

from dataclasses import dataclass

FLOW = ("figura", "planos", "miembros", "parametros", "recortes", "generar")      # el orden del trabajo
OPTIONAL = ("catalogo",)                                                           # modo aparte: la figura sale (en parte) del catálogo
ORDER = FLOW + OPTIONAL
TITLES = {"figura": lz("common.figura"), "planos": lz("common.planos"), "miembros": lz("common.miembros"), "parametros": lz("common.parametros"), "recortes": lz("common.recortes"),
          "generar": lz("common.generar"), "catalogo": lz("common.catalogo_2")}
GLYPHS = {"todo": "○", "ok": "✓", "warn": "⚠", "stale": "⟳", "ready": "▶", "info": "·"}
ATTENTION = ("todo", "warn", "stale", "ready")      # estados que piden que el usuario haga algo (los salta «Siguiente pendiente»)


@dataclass(frozen=True)
class StepState:
    key: str
    state: str            # clave de GLYPHS
    detail: str           # una línea corta bajo el título

    @property
    def title(self) -> str:
        return loc(TITLES[self.key])

    @property
    def glyph(self) -> str:
        return GLYPHS[self.state]


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _short(names: list[str], limit: int = 2) -> str:
    return ", ".join(names[:limit]) + ((' ' + tr("step_state.y_v_mas", v=len(names) - limit)) if len(names) > limit else "")


def step_states(ctrl) -> dict[str, StepState]:
    """`{paso: StepState}` de los siete pasos para el estado actual de `ctrl` (un `Controller`)."""
    out: dict[str, StepState] = {}
    loaded = ctrl.loaded
    proj = ctrl.session.project
    catalog_only = loaded and ctrl.session.catalog_only()

    def put(key: str, state: str, detail: str = "") -> None:
        out[key] = StepState(key, state, detail)

    # --- Figura
    put("figura", "ok" if loaded else "todo", tr("step_state.frente_front", front=proj.front) if loaded else tr("common.abri_un_stl"))

    # --- Planos
    n_planes = len(proj.limbs) + (proj.head is not None) + (proj.waist is not None)
    if not loaded:
        put("planos", "todo", "")
    elif catalog_only:
        put("planos", "info", tr("step_state.no_hacen_falta_la_figura_sale"))
    elif proj.symmetry is None:
        put("planos", "todo", tr("common.falta_el_plano_de_simetria"))
    elif n_planes == 0:
        put("planos", "warn", tr("step_state.falta_un_miembro_la_cabeza_o"))
    else:
        put("planos", "ok", (tr("step_state.simetria") + ' ') + _plural(n_planes, "plano", "planos"))

    # --- Miembros (siluetas y pasadores)
    open_limbs = [l.name for l in proj.limbs if len(l.polygon) < 3]
    if not loaded or catalog_only:
        put("miembros", "info" if catalog_only else "todo", "")
    elif proj.symmetry is None or not proj.limbs:
        put("miembros", "info" if (proj.head is not None or proj.waist is not None) else "todo",
            tr("step_state.sin_miembros_solo_cabeza_cintura") if (proj.head is not None or proj.waist is not None) else tr("step_state.agrega_un_brazo_o_una_pierna"))
    elif open_limbs:
        put("miembros", "warn", (tr("step_state.falta_la_silueta") + ' ') + _short(open_limbs))
    else:
        put("miembros", "ok", _plural(len(proj.limbs), "miembro", "miembros") + (' ' + tr("step_state.con_silueta")))

    # --- Parámetros
    n_mod = len(ctrl.modified_params()) if loaded else 0
    put("parametros", "info", tr("step_state.n_mod_modificados", n_mod=n_mod) if n_mod else tr("step_state.valores_por_defecto"))

    # --- Recortes
    n_trims = len(proj.trims)
    put("recortes", "info", _plural(n_trims, "recorte", "recortes") if n_trims else "ninguno")

    # --- Generar
    problems = ctrl.problems()
    if not loaded:
        put("generar", "todo", "")
    elif problems:
        put("generar", "todo", (tr("common.falta") + ' ') + problems[0] + (f" (+{len(problems) - 1})" if len(problems) > 1 else ""))
    elif ctrl.outcome_stale:
        put("generar", "stale", tr("step_state.desactualizada_genera_de_nuevo"))
    elif ctrl.session.outcome is None:
        put("generar", "ready", tr("step_state.lista_para_generar"))
    else:
        put("generar", "ok", tr("common.figura_generada"))

    # --- Catálogo (opcional)
    n_parts = len(proj.parts)
    if not ctrl.session.catalog_root:
        put("catalogo", "info", tr("step_state.sin_catalogo_abierto"))
    else:
        put("catalogo", "info", _plural(n_parts, tr("step_state.pieza_elegida"), tr("step_state.piezas_elegidas")) if n_parts else tr("step_state.catalogo_abierto"))
    return out


def next_pending(states: dict[str, StepState], current: str | None = None) -> str | None:
    """El primer paso del flujo que pide atención, empezando después de `current` y dando la vuelta; None si no hay ninguno."""
    keys = [k for k in FLOW if states[k].state in ATTENTION]
    if not keys:
        return None
    if current in FLOW:
        after = [k for k in keys if FLOW.index(k) > FLOW.index(current)]
        if after:
            return after[0]
    return keys[0]
