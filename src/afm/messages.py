"""Mensajes de resultado del motor que no dependen de la GUI (los usan la CLI y la ventana)."""
from __future__ import annotations

from .i18n import tr


def fill_hip_message(ss: dict, wanted: bool) -> str:
    """Resultado del relleno de muslos de la exportación sin soportes (vacío si no se pidió)."""
    if not wanted:
        return ""
    done = {k: r for k, r in (ss.get("relleno_cadera") or {}).items() if "aviso" not in r}
    if not done:
        return (' ' + tr("controller.relleno_de_muslos_no_se_aplico")) if ss.get("relleno_cadera") else \
            (' ' + tr("controller.relleno_de_muslos_no_se_aplico_2"))
    return (' ' + tr("controller.relleno_de_muslos") + ' ') + "; ".join(
        tr("controller.k_volumen_muslo_agregado_mm3_mm3", k=k, volumen_muslo_agregado_mm3=f"{r['volumen_muslo_agregado_mm3']:g}", volumen_pelvis_tallado_mm3=f"{r['volumen_pelvis_tallado_mm3']:g}", holgura_de_pie_mm=f"{r['holgura_de_pie_mm']:g}") for k, r in done.items()) + "."
