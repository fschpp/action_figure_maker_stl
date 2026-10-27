"""Progreso de las tareas largas (armar, generar, revisar, exportar): porcentaje, tareas pendientes y tiempo estimado.

Las funciones pesadas (`pipeline.build`, `preflight.preview_parts`, `cli.run_armar`) siguen informando con texto
(`progress("Separando brazo…")`). Acá se describe de antemano la lista de pasos que se esperan (`build_plan`, `preview_plan`,
`armar_plan`) y `ProgressTracker` los va marcando a medida que llegan los mensajes. No depende de GTK: la barra de la ventana
solo pide `snapshot()`.

El porcentaje sale del peso relativo de cada paso (`Stage.weight`); dentro del paso en curso avanza con el tiempo, calibrado con lo
que tardaron los pasos ya terminados, así la barra no salta. El tiempo restante se estima con esa misma calibración: es una
aproximación (los pasos pesan distinto según la figura) y no se muestra hasta tener algún paso terminado.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Stage:
    key: str                 # texto con el que lo anuncia el motor (sin «…»); se compara sin mayúsculas ni puntos suspensivos
    weight: float = 1.0      # costo relativo
    label: str = ""          # cómo se muestra (por defecto, el texto)

    @property
    def text(self) -> str:
        return self.label or self.key


@dataclass
class Snapshot:
    fraction: float                      # 0..1
    current: str                         # lo que se está haciendo (el último mensaje recibido)
    pending: list[str] = field(default_factory=list)      # pasos que faltan, en orden
    done: int = 0
    total: int = 0
    elapsed: float = 0.0
    eta: float | None = None             # segundos que faltan (None = todavía no se puede estimar)
    finished: bool = False

    @property
    def percent(self) -> int:
        return int(round(100 * self.fraction))


def _norm(text: str) -> str:
    return str(text).strip().rstrip("….").strip().lower()


def fmt_duration(sec: float) -> str:
    sec = max(0, int(round(sec)))
    if sec < 60:
        return f"{sec} s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} min {s:02d} s"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def fmt_eta(sec: float | None) -> str:
    """«unos 2 min», «unos 40 s»; vacío si aún no se puede estimar."""
    if sec is None:
        return ""
    if sec < 8:
        return "casi listo"
    if sec < 60:
        return f"unos {int(round(sec / 5.0)) * 5} s"
    return f"unos {int(round(sec / 60.0))} min"


class ProgressTracker:
    """Marca los pasos de una tarea larga. Seguro entre hilos (el motor informa desde el hilo de trabajo)."""

    MAX_INNER = 0.95          # un paso en curso nunca llega al 100 % por tiempo: eso lo decide el mensaje siguiente

    def __init__(self, stages: list[Stage], clock=time.monotonic):
        self.stages = list(stages)
        self._clock = clock
        self._lock = threading.Lock()
        self._t0 = clock()
        self._idx = -1                       # paso en curso (-1 = todavía ninguno)
        self._t_stage = self._t0
        self._starts: list[float] = [self._t0] * len(self.stages)
        self._text = "Preparando…"
        self._t_first: float | None = None       # cuándo empezó el primer paso (el arranque previo no cuenta para calibrar)
        self._finished = False

    # ---------------------------------------------------------------- entrada
    def feed(self, text: str) -> bool:
        """Recibe un mensaje del motor. Devuelve True si coincide con un paso esperado (los anteriores quedan hechos)."""
        key = _norm(text)
        with self._lock:
            self._text = str(text).strip() or self._text
            for i in range(max(self._idx, 0), len(self.stages)):
                s = _norm(self.stages[i].key)
                if key == s or key.startswith(s):
                    if i != self._idx:
                        self._idx, self._t_stage = i, self._clock()
                        if self._t_first is None:
                            self._t_first = self._t_stage
                        self._starts[i] = self._t_stage
                    return True
        return False

    def finish(self, text: str | None = None) -> None:
        with self._lock:
            self._finished = True
            self._idx = len(self.stages)
            if text:
                self._text = text

    # ---------------------------------------------------------------- salida
    def snapshot(self) -> Snapshot:
        with self._lock:
            now = self._clock()
            total_w = sum(s.weight for s in self.stages) or 1.0
            elapsed = now - self._t0
            n = len(self.stages)
            if self._finished:
                return Snapshot(1.0, self._text, [], n, n, elapsed, 0.0, True)
            idx = self._idx
            w_before = sum(s.weight for s in self.stages[:max(idx, 0)])
            unit = None                                        # segundos por unidad de peso, según lo ya terminado
            if idx > 0 and w_before > 0:
                unit = max(self._t_stage - (self._t_first if self._t_first is not None else self._t0), 1e-6) / w_before
            inner = 0.0
            if idx >= 0 and idx < n:
                w_cur = self.stages[idx].weight
                if unit:
                    inner = min(self.MAX_INNER, (now - self._t_stage) / max(unit * w_cur, 1e-6))
                done_w = w_before + inner * w_cur
            else:
                done_w = 0.0
            frac = min(0.99, done_w / total_w)
            eta = None if unit is None else max(0.0, unit * (total_w - done_w))
            pending = [s.text for s in self.stages[idx + 1:]] if idx >= 0 else [s.text for s in self.stages]
            return Snapshot(frac, self._text, pending, max(idx, 0), n, elapsed, eta, False)


MAX_PENDING_SHOWN = 6


def describe(snap: Snapshot) -> tuple[str, str, str]:
    """(texto de la barra, tarea en curso, pendientes) para un `Snapshot`."""
    bits = [f"{snap.percent} %"]
    if snap.finished:
        bits.append(f"terminado en {fmt_duration(snap.elapsed)}")
    else:
        eta = fmt_eta(snap.eta)
        bits.append(f"faltan {eta}" if eta and eta != "casi listo" else (eta or "calculando el tiempo…"))
        bits.append(f"{fmt_duration(snap.elapsed)} transcurridos")
    now = snap.current if snap.finished else f"Ahora: {snap.current}"
    pend = ""
    if snap.pending:
        shown = " · ".join(snap.pending[:MAX_PENDING_SHOWN])
        more = len(snap.pending) - MAX_PENDING_SHOWN
        pend = f"Pendiente ({len(snap.pending)}): {shown}" + (f" · … y {more} más" if more > 0 else "")
    return " · ".join(bits), now, pend


# ------------------------------------------------------------------------------------------------ planes
def build_plan(limbs, p, *, parts=None, head=None, waist=None, hand=None, joints=(), host_faces=None,
               prologue: list[Stage] | None = None, epilogue: list[Stage] | None = None) -> list[Stage]:
    """Pasos que `pipeline.build` va a anunciar con estos datos, en su orden (ver los `say(...)` de ese módulo)."""
    from . import detent as DT

    parts = dict(parts or {})
    out: list[Stage] = list(prologue or [])
    if "pelvis" in parts and not host_faces:
        out.append(Stage("Cambiando la pelvis", 3))
    if head is not None:
        out.append(Stage("Separando la cabeza", 4))
    wrist_arms = {s.name for s in limbs if s.kind == "arm" and s.name in parts and "muneca" in parts[s.name].meta.puertos}
    if hand is not None and not wrist_arms:
        out.append(Stage("Pegando la mano estándar", 2))
    by_limb = {j.limb: j for j in joints}
    for spec in limbs:
        out.append(Stage(f"Separando {spec.name}", 3))
        if spec.name in wrist_arms and (hand is not None or "mano" in parts):
            out.append(Stage(f"Mano estándar en {spec.name}", 2))
        if DT.detent_spec_for(spec, p) is not None:
            out.append(Stage(f"Clic de {spec.name}", 5))
        else:
            out.append(Stage(f"Cavidad de {spec.name}", 6))
    for lname, j in by_limb.items():
        out.append(Stage(f"Articulación {j.name}", 3))
    if head is not None:
        out.append(Stage("Cavidad del cuello", 3))
    out.append(Stage("Pose sentada y espejado", 8))
    if not p.torso_mirror:
        out.append(Stage("Torso sin espejar", 4))
    if p.decimate > 0:
        out.append(Stage("Reduciendo triángulos", 4))
    if any(DT.detent_spec_for(s, p) is not None for s in limbs):
        out.append(Stage("Lengüetas del clic", 3))
    if waist is not None:
        out.append(Stage("Cintura", 4))
    out.extend(epilogue or [])
    return out


@dataclass(frozen=True)
class LimbLite:
    """Lo mínimo de un miembro para planear los pasos sin sintetizar el cuerpo (`name`, `kind`, mecanismo por defecto)."""
    name: str
    kind: str
    mechanism: str | None = None
    detent: object | None = None


BASE_BODY = Stage("Armando el cuerpo base", 3)
VALIDATE = Stage("Validando la figura", 6)
EXPORT = Stage("Exportando las piezas", 6)
SAVE_PROJECT = Stage("Guardando el proyecto", 1)


def preview_plan(places) -> list[Stage]:
    """`preflight.preview_parts`: un paso por lugar elegido."""
    return [Stage(f"Revisando «{pl}»", 3) for pl in places]


def catalog_plan(choices: dict, p, hand=None, *, epilogue: list[Stage] | None = None) -> list[Stage]:
    """Pasos de armar una figura toda de catálogo. `choices` = {lugar: PartChoice} con las claves del huésped
    (`torso` | `torso_sup`, `pelvis`, `brazo`, `pierna`, `cabeza`, `mano`); no sintetiza nada (es barato, se llama antes de empezar)."""
    limbs = [LimbLite(n, k) for n, k in (("brazo", "arm"), ("pierna", "leg")) if n in choices]
    parts = {k: v for k, v in choices.items() if k in ("brazo", "pierna", "cabeza", "mano")}
    return build_plan(limbs, p, parts=parts, head=object() if "cabeza" in choices else None,
                      waist=object() if "torso_sup" in choices else None,
                      hand=hand if hand is not None else (object() if "mano" in choices else None),
                      host_faces={"x": 1} if "torso_sup" in choices or "torso" in choices else None,
                      prologue=[BASE_BODY], epilogue=epilogue)
