"""Texto de la simulación de tamaño de un arma (`weapon_fit`): pasa los avisos `(código, variables)` a frases en el idioma actual.

Sin GTK. Lo usan la sesión de importación de armas (arma en la mano estándar), la vista previa de la montura (arma enfundada
frente a la figura) y `afm simular-arma`.
"""
from __future__ import annotations

from ..i18n import tr
from ..weapon_fit import LEVEL_OK, BodyFit, HandSim


def note_text(code: str, variables: dict) -> str:
    """Frase de un aviso de `weapon_fit` (cada código tiene su clave `weapon_sim.<código>`)."""
    return tr(f"weapon_sim.{code}", **variables)


def _notes(notes) -> list[str]:
    return [f"- {note_text(c, v)}" for c, v in notes]


def hand_lines(sim: HandSim) -> list[str]:
    """Resumen del arma en el puño de la mano estándar: medidas y avisos (o «tamaño razonable»)."""
    head = tr("weapon_sim.mano_medidas", alcance=f"{sim.reach_mm:.1f}", vs_mano=f"{sim.reach_vs_hand:.1f}",
              guarda=f"{sim.guard_mm:.1f}", puno=f"{sim.fist_mm:.1f}")
    lines = [head]
    if sim.reach_vs_figure is not None:
        lines.append(tr("weapon_sim.mano_figura", pct=f"{100 * sim.reach_vs_figure:.0f}"))
    if sim.gap_mm is not None:
        lines.append(tr("weapon_sim.mano_distancia", gap=f"{sim.gap_mm:.1f}"))
    lines += _notes(sim.notes) if sim.notes else [tr("weapon_sim.razonable")]
    return lines


def body_lines(fit: BodyFit) -> list[str]:
    """Resumen del arma enfundada frente a la figura: fracción de la altura, punta contra el piso, tope contra la cabeza y avisos."""
    head = tr("weapon_sim.cuerpo_medidas", pct=f"{100 * fit.length_vs_figure:.0f}", piso=f"{fit.floor_gap_mm:.1f}",
              cabeza=f"{fit.top_over_head_mm:+.1f}")
    return [head] + (_notes(fit.notes) if fit.notes else [tr("weapon_sim.razonable")])


def has_problems(sim) -> bool:
    """¿Hay algo que mostrar en rojo? (cualquier aviso)."""
    return sim.level != LEVEL_OK
