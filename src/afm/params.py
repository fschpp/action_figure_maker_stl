"""Parámetros y perfil de línea (plan §5.2 y §8.12). Todo en mm finales (figura de 100 mm)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict


@dataclass
class PinSpec:
    shaft_d: float
    shaft_len: float
    head_d: float
    head_h: float


# Pasador de cadera de la figura de referencia del cliente (plan de articulaciones §1/§3): cabeza/vástago ≈ 1,9.
# mm absolutos de una figura de 129 mm; NO es el valor por defecto de `Params.hip_pin` (decisión de escala pendiente).
HIP_PIN_REFERENCE = PinSpec(4.8, 2.3, 9.25, 2.44)
REFERENCE_FIGURE_HEIGHT = 129.0   # 13 cm reales (decisión del cliente, 2026-10-17): pies → punta del sombrero
HIP_WALL_REFERENCE = 1.30   # pared medida entre la cabeza y el cuerpo del muslo (= shaft_len − 2·holgura con 0,5 mm)


@dataclass
class HingeSpec:
    """Bisagra de lengüeta en ranura (rodilla, codo): dos bulones cortos, uno a cada lado de la lengüeta.

    Medidas en mm absolutos de impresión (no se escalan con la figura). Valores iniciales de la figura de
    referencia (plan de articulaciones §3); `end_r` sube a 3,6 mm para dejar pared alrededor del bulón."""
    pin_d: float = 2.4        # diámetro de cada bulón
    pin_len: float = 1.2      # cuánto sale del lado de la lengüeta
    tongue_t: float = 2.4     # espesor de la lengüeta (pieza distal)
    end_r: float = 3.6        # radio del disco de la lengüeta alrededor del eje
    # Bastago pasante: en vez de dos bulones cortos, un solo bastago soldado a la lengüeta del antebrazo que
    # atraviesa un orificio del brazo (de lado a lado). rod=False vuelve a los dos bulones.
    neck: bool = True         # lengüeta con cuello recto (lados rectos hasta el cuerpo, punta redonda) en vez de un disco suelto
    rod: bool = True
    rod_wall: float = 1.2     # pared de brazo que queda tras cada punta del bastago (0 = orificio pasante abierto)


@dataclass
class DetentSpec:
    """Mecanismo de clic print-in-place (plan de clic §3.5): disco con muescas en el miembro + 2 lengüetas en el padre.

    Medidas en mm absolutos de impresión (no se escalan con la figura). Valores iniciales de una figura de 13 cm
    para boquilla 0,4; son estimaciones a calibrar con las probetas de clic (`afm probe --tipo clic`)."""
    n_notches: int = 4            # muescas del disco (cada 360/n °); hombro 4, cadera 4 (usa 2 por el tope)
    disc_d: float = 8.0           # Ø del disco de clic
    disc_h: float = 2.5           # alto del disco (el plan decía 2,0: con 2,5 la punta tiene 1,4 mm de apoyo)
    notch_depth: float = 0.8      # profundidad de la muesca; flexión de la punta δ = notch_depth − detent_gap
    flank_deg: float = 30.0       # α del flanco: más empinado = clic más firme y más esfuerzo
    tip_r: float = 0.7            # radio de la punta de la lengüeta (redondeada: sin aristas que se desgasten)
    detent_gap: float = 0.30      # holgura radial punta/muesca al imprimir; define el juego angular
    slit_w: float = 0.50          # ranura lateral, inferior y de punta (= holgura radial)
    flex_gap: float = 1.0         # ranura superior = δ + slit_w
    finger_t: float = 1.2         # espesor de la lengüeta (boquilla 0,4; con 0,8 usar 1,6)
    finger_len: float = 10.0      # largo de la lengüeta, de la raíz a la punta (0,8 → 12)
    finger_w: float = 4.0         # ancho de la lengüeta
    max_strain: float = 0.01      # deformación máxima admitida (validación)
    print_angle: float = 10.0     # hombro: ángulo del brazo al imprimir = fase de los clics (opción A); 0 = colgando (B)
    hold_torque_min: float = 10.0     # N·mm: par de retención mínimo que sostiene la pose [estimación]
    move_torque_max: float = 60.0     # N·mm: par máximo para moverlo con los dedos [estimación]
    elastic_modulus: float = 2500.0   # MPa: PLA impreso [estimación]


@dataclass
class Params:
    ref_height: float = 100.0
    clearance: float = 0.50          # holgura cavidad vs. miembro (0,495 medido en el STL de Mickey)
    burr: float = 0.66               # rebaba a rebajar en la cara de contacto
    shoulder_pin: PinSpec = field(default_factory=lambda: PinSpec(4.1, 2.5, 6.6, 1.65))
    hip_pin: PinSpec = field(default_factory=lambda: PinSpec(5.0, 4.1, 6.6, 2.5))
    neck_pin: PinSpec = field(default_factory=lambda: PinSpec(4.1, 2.5, 6.6, 1.65))   # cabeza: misma tecnología que el hombro
    # Cintura (waist.py): mismo hongo que el hombro pero más robusto (vástago Ø6, cabeza Ø10 × 3, pared ≥ 2 mm tras la cabeza)
    waist_pin: PinSpec = field(default_factory=lambda: PinSpec(6.0, 3.0, 10.0, 3.0))
    waist_twist: float = 45.0        # giro a cada lado de la cintura que se valida contra brazos/piernas (°)
    sit_angle: float = 90.0          # pose sentada (hacia adelante)
    hip_margin: float = 5.0          # recorte del límite de cadera hasta sit_angle + margen
    sweep_step: float = 5.0          # paso angular del volumen barrido
    min_wall: float = 1.2            # espesor mínimo de pared (validación)
    min_pin_d: float = 3.0           # diámetro mínimo de pasador (validación)
    nozzle: float = 0.4
    # --- rodilla y codo (print-in-place); ver joints.py. Rangos en FLEXIÓN positiva (rodilla: pantorrilla hacia
    # atrás; codo: antebrazo hacia adelante); hiperextensión = grados de giro en sentido contrario.
    hinge_pin: HingeSpec = field(default_factory=HingeSpec)
    axial_clearance: float = 0.35    # holgura a lo largo del eje (lengüeta vs. paredes de la ranura)
    knee_range: tuple = (0.0, 90.0)
    knee_hyperext: float = 0.0
    elbow_range: tuple = (0.0, 90.0)
    elbow_hyperext: float = 10.0
    stop_margin: float = 5.0         # tolerancia (°) del tope: debe chocar a ≤ stop_margin + 5° del límite
    knee_print_angle: float = 85.0   # flexión de la rodilla en la pose sentada impresa (90° cae justo en el tope)
    elbow_print_angle: float = 0.0
    min_tongue: float = 2.0          # espesor mínimo de lengüeta (validación)
    min_hinge_pin_d: float = 2.0     # diámetro mínimo del bulón de bisagra (validación; el de hongo usa min_pin_d)
    decimate: float = 0.6            # reducción de triángulos de las piezas al exportar (0 = desactivar)
    # --- mecanismo de clic (PLAN_mecanismo_clic_hombro_cadera.md): opción por articulación; False = pasador de hongo solo
    detent: DetentSpec = field(default_factory=DetentSpec)
    shoulder_detent: bool = False
    hip_detent: bool = False
    flat_depth: float = 1.0          # «imprimir sin soportes»: altura del corte plano sobre el punto más bajo de la cola (mm)

    def with_hip_reference(self) -> "Params":
        """Copia con el pasador de cadera de la figura de referencia (cabeza/vástago ≈ 1,9, mm absolutos)."""
        import dataclasses
        return dataclasses.replace(self, hip_pin=dataclasses.replace(HIP_PIN_REFERENCE))

    @classmethod
    def reference_figure(cls) -> "Params":
        """Línea de figuras de 13 cm reales (Link): 129 mm de los pies al punto más alto (o a la coronilla marcada) y
        pasador de cadera de la figura de referencia. Las medidas de articulación son mm absolutos: no se escalan."""
        return cls(ref_height=REFERENCE_FIGURE_HEIGHT).with_hip_reference()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Params":
        d = dict(d)
        for k in ("shoulder_pin", "hip_pin", "neck_pin", "waist_pin"):
            if k in d and isinstance(d[k], dict):
                d[k] = PinSpec(**d[k])
        if isinstance(d.get("detent"), dict):
            d["detent"] = DetentSpec(**d["detent"])
        if isinstance(d.get("hinge_pin"), dict):
            d["hinge_pin"] = HingeSpec(**d["hinge_pin"])
        for k in ("knee_range", "elbow_range"):
            if k in d and d[k] is not None:
                d[k] = tuple(d[k])
        return cls(**d)

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Params":
        with open(path) as f:
            return cls.from_dict(json.load(f))
