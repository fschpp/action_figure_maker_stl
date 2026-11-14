"""Constantes de la interfaz (rótulos y rangos de los controles) compartidas por la ventana y los pasos."""
from __future__ import annotations

from ..head import REPLACEMENT_SCALE_RANGE

TRIM_OPS = ("subtract", "union")
SCALE_SPIN_MIN = 1e-6         # el control no admite 0 (la escala debe ser > 0); 1e-6 es el menor valor que muestra con 6 decimales
# Controles de la cabeza de reemplazo: (clave de Controller.set_head_adjust, rótulo, mín, máx, paso, decimales)
HEAD_ADJUST_FIELDS = (
    ("rx", "Cabeza de reemplazo: giro X (°)", -180.0, 180.0, 5.0, 1),
    ("ry", "Cabeza de reemplazo: giro Y (°)", -180.0, 180.0, 5.0, 1),
    ("yaw", "Cabeza de reemplazo: giro Z, sobre el cuello (°)", -360.0, 360.0, 15.0, 1),
    ("scale", "Cabeza de reemplazo: escala (× base del cuello)", SCALE_SPIN_MIN, REPLACEMENT_SCALE_RANGE[1], 0.05, 6),
)
HEAD_ADJUST_ATTR = {"rx": "replacement_rx", "ry": "replacement_ry", "yaw": "replacement_yaw",
                    "scale": "replacement_scale"}
HEAD_ADJUST_DEFAULTS = {"rx": 0.0, "ry": 0.0, "yaw": 0.0, "scale": 1.0}
