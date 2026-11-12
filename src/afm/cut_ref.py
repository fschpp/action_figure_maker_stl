"""Referencia del corte de brazos y piernas: a cuántos mm del plano de simetría corta una figura YA cortada del catálogo (p. ej.
Güemes), para que el corte de una figura nueva quede parecido sin tener que mirarlo a ojo.

La distancia de corte de una pieza de catálogo es la x del centro de su puerto de hombro / cadera (los puertos son laterales y las
piezas conservan el marco de su figura de origen, con la simetría en x = 0): lo mismo que usa `Session.line_cut_x`, pero leído de
cualquier figura del catálogo y sin haber elegido antes una pieza en «Armar desde catálogo».

* `reference_figures(cat)`: figuras del catálogo con al menos un corte lateral de brazo o pierna que sirva de referencia.
* `reference_cuts(cat, figura)`: `{"arm"|"leg": RefCut}` de esa figura.
* `default_reference(figuras)`: la que se propone (Güemes si está).
* `reading(...)` / `readout_text(...)`: la lectura para la ventana, con la diferencia respecto del corte actual.

Las medidas están en los mm de la figura (las de «Corte a»). Si la figura abierta no mide lo mismo que la de referencia se agrega el
valor proporcional a la altura, solo como dato: lo que acopla piezas entre figuras de una línea es la distancia absoluta. No importa GTK."""
from __future__ import annotations

from dataclasses import dataclass

from .assembly import LIMB_PART
from .proportions import catalog_figures

ALIGNED_TOL_MM = 0.05          # menos que esto cuenta como «igual» (lo mismo que «Alinear» de la lista de planos)
LATERAL_MIN = 0.95             # |componente x del eje| mínima para que el puerto cuente como lateral
KINDS = ("arm", "leg")
KIND_LABEL = {"arm": "brazo", "leg": "pierna"}


@dataclass(frozen=True)
class RefCut:
    figura: str
    kind: str                      # "arm" | "leg"
    ref: str                       # la pieza de catálogo de donde sale la medida
    mm: float                      # distancia del corte al plano de simetría
    altura_mm: float | None        # altura de la figura de origen (para el valor proporcional)


def reference_cuts(cat, figura: str) -> dict[str, RefCut]:
    """Los cortes de brazo / pierna de `figura` que se pueden usar de referencia: `{kind: RefCut}` (puerto lateral)."""
    out: dict[str, RefCut] = {}
    refs = catalog_figures(cat).get(figura) or {}
    for kind in KINDS:
        tipo, port_name = LIMB_PART[kind]
        ref = refs.get(tipo)
        if ref is None:
            continue
        try:
            meta = cat.meta(ref)
        except Exception:                                      # noqa: BLE001 — la consulta es informativa
            continue
        port = meta.puertos.get(port_name)
        if port is None or abs(float(port.frame.axis[0])) < LATERAL_MIN:
            continue
        out[kind] = RefCut(figura, kind, meta.ref, abs(float(port.frame.center[0])), meta.altura_figura_mm)
    return out


def reference_figures(cat) -> list[str]:
    """Figuras del catálogo con al menos un corte de referencia, por orden alfabético."""
    return sorted(f for f in catalog_figures(cat) if reference_cuts(cat, f))


def default_reference(figuras: list[str]) -> str | None:
    """La referencia que se propone: Güemes si está en el catálogo (sin mirar mayúsculas ni tildes); si no, la primera."""
    for f in figuras:
        if f.lower().replace("ü", "u").startswith("guemes"):
            return f
    return figuras[0] if figuras else None


def reading(current_mm: float, ref: RefCut, height_mm: float | None = None) -> dict:
    """Compara el corte actual con el de la referencia: `ref_mm`, `dif_mm` (referencia − actual: lo que hay que correr), `igual`,
    y, si se conocen las dos alturas y difieren, `prop_mm` (la medida de la referencia llevada a la altura de la figura abierta)."""
    dif = ref.mm - current_mm
    out = {"ref_mm": ref.mm, "dif_mm": dif, "igual": abs(dif) < ALIGNED_TOL_MM, "prop_mm": None}
    if height_mm and ref.altura_mm and abs(height_mm - ref.altura_mm) > 0.5:
        out["prop_mm"] = ref.mm * height_mm / ref.altura_mm
    return out


def _fmt(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def readout_text(label: str, current_mm: float, ref: RefCut, height_mm: float | None = None) -> str:
    """«Brazo: tu corte a 6,20 mm · Güemes corta a 7,70 mm → falta +1,50 mm»  (o «= igual» cuando coinciden)."""
    r = reading(current_mm, ref, height_mm)
    txt = f"{label}: tu corte a {_fmt(current_mm)} mm · {ref.figura} corta a {_fmt(ref.mm)} mm"
    txt += " → igual" if r["igual"] else f" → mové {r['dif_mm']:+.2f} mm".replace(".", ",")
    if r["prop_mm"] is not None:
        txt += f" (a tu altura, proporcional: {_fmt(r['prop_mm'])} mm)"
    return txt
