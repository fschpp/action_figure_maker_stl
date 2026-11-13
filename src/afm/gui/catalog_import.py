"""Importar al catálogo desde la ventana «Administrar catálogo» (`afm catalogo importar-mano / -accesorio / -arma / agregar-stl`).

Sin GTK: `FORMS` describe los campos de cada formulario, `parse` valida lo que escribió la persona y lo convierte en los argumentos
de `catalog_ops`, y `run` abre el catálogo y ejecuta la importación (se llama desde un hilo). La ventana solo dibuja los campos.

Los 3 puntos del plano de corte del arma y los 3 puntos de cada puerto de una pieza suelta se **escriben** (X,Y,Z sobre el STL,
como en la consola); elegirlos con clics sobre una vista previa del STL es el paso siguiente."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .. import catalog_ops as OPS
from .. import parts as P

FILE, TEXT, FLOAT, BOOL, CHOICE, MULTILINE = "archivo", "texto", "numero", "casilla", "opcion", "multilinea"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = TEXT
    default: object = ""
    required: bool = False
    choices: tuple = ()
    help: str = ""


_ID = Field("id", "Id de la pieza", TEXT, "", False, (), "Vacío: se arma con el nombre del archivo.")
_DESC = Field("descripcion", "Descripción", TEXT)
_NEW = Field("nueva_version", "Si el id ya existe, guardar la versión siguiente", BOOL, False)

FORMS: dict[str, dict] = {
    "mano": {
        "titulo": "Importar una mano estándar", "comando": "importar-mano",
        "ayuda": "STL de una mano afm-mano-1 (la que da `afm extraer-mano`).",
        "campos": [Field("stl", "STL de la mano", FILE, required=True), _ID, _NEW],
    },
    "accesorio": {
        "titulo": "Importar un accesorio", "comando": "importar-accesorio",
        "ayuda": "STL cerrado en el marco del accesorio: eje de la empuñadura = +X, origen en la cara de la guarda que mira a la mano.",
        "campos": [
            Field("stl", "STL del accesorio", FILE, required=True),
            Field("largo_canal", "Largo del canal de la mano (mm)", FLOAT, "", True, (), "Está en el .mano.json de la mano con agarre."),
            Field("taladro", "Ø del taladro de la mano (mm)", FLOAT, 2.4), Field("vastago", "Ø del vástago (mm)", FLOAT, 2.0),
            Field("ranura", "Ancho de la ranura (mm)", FLOAT, 0.5), Field("largo_lengueta", "Largo de la lengüeta (mm)", FLOAT, 8.0),
            Field("dentado", "Ø del dentado (mm)", FLOAT, 2.9), Field("juego", "Juego axial (mm)", FLOAT, 0.4),
            _ID, _DESC, _NEW],
    },
    "arma": {
        "titulo": "Importar un arma", "comando": "importar-arma",
        "ayuda": "Cualquier STL: se escala, se le corta el mango por un plano y se le pone la empuñadura que encaja en la mano con agarre.",
        "campos": [
            Field("stl", "STL del arma", FILE, required=True),
            Field("corte", "Plano de corte: 3 puntos X,Y,Z;X,Y,Z;X,Y,Z", TEXT, "", True, (),
                  "En coordenadas del STL original (sin escalar). El lado hacia el que apunta la normal (1→2 × 1→3) se descarta: es el mango."),
            Field("largo", "Largo de la dimensión mayor (mm)", FLOAT, "", False, (), "Vacío: 0,40 × la altura de la línea. No combinar con la escala."),
            Field("escala", "o factor sobre las unidades del STL", FLOAT, ""),
            Field("invertir", "Descartar el otro lado del plano", BOOL, False),
            Field("giro", "Giro sobre el eje de la empuñadura (°)", FLOAT, "", False, (), "Vacío: el que deja el arma más baja."),
            Field("mano", "Mano con agarre", TEXT, "", False, (), "Vacío: la más reciente con agarre."),
            _ID, _DESC, _NEW],
    },
    "pieza": {
        "titulo": "Agregar una pieza suelta", "comando": "agregar-stl",
        "ayuda": "STL cerrado + 3 puntos por puerto sobre su cara de unión (el punto 1 fija el giro).",
        "campos": [
            Field("stl", "STL de la pieza", FILE, required=True),
            Field("tipo", "Tipo de pieza", CHOICE, "brazo", True, tuple(P.TYPES)),
            Field("puertos", "Puertos (uno por línea): NOMBRE x1 y1 z1 x2 y2 z2 x3 y3 z3", MULTILINE, "", True, (),
                  "Los nombres válidos dependen del tipo (p. ej. brazo: hombro). El rol sale del tipo de pieza."),
            Field("lado", "Lado", CHOICE, "izq", False, tuple(P.SIDES)),
            Field("espejable", "Espejable", BOOL, True),
            Field("altura", "Altura de la figura de origen (mm)", FLOAT, "", False, (), "Vacío: la de la línea."),
            Field("figura", "Figura de origen (texto, para el índice)", TEXT),
            Field("etiquetas", "Etiquetas (estandar, propia; separadas por coma)", TEXT),
            _ID, _DESC, _NEW],
    },
}

LABELS = {"mano": "Mano estándar…", "accesorio": "Accesorio…", "arma": "Arma…", "pieza": "Pieza suelta…"}


def _num(text, label: str) -> float | None:
    text = str(text).strip().replace(",", ".") if text is not None else ""
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"«{label}»: «{text}» no es un número") from exc


def parse_ports(text: str) -> dict:
    """Líneas «NOMBRE x1 y1 z1 x2 y2 z2 x3 y3 z3» → {nombre: [[x, y, z] × 3]} (se aceptan espacios, comas y «;»)."""
    out: dict = {}
    for n, line in enumerate((ln for ln in text.splitlines() if ln.strip()), 1):
        toks = [t for t in re.split(r"[\s,;]+", line.strip()) if t]
        name, nums = toks[0], toks[1:]
        if len(nums) != 9:
            raise ValueError(f"puerto {n} («{name}»): hacen falta 9 números (3 puntos X,Y,Z), hay {len(nums)}")
        try:
            vals = [float(x.replace(",", ".")) for x in nums]
        except ValueError as exc:
            raise ValueError(f"puerto {n} («{name}»): hay un valor que no es un número") from exc
        if name in out:
            raise ValueError(f"el puerto «{name}» está repetido")
        out[name] = [vals[0:3], vals[3:6], vals[6:9]]
    if not out:
        raise ValueError("falta al menos un puerto (3 puntos sobre su cara de unión)")
    return out


def parse(kind: str, values: dict) -> dict:
    """Valida `values` (clave del campo → texto / número / bool) y devuelve `{"stl": …, "args": (…), "kwargs": {…}}` para `catalog_ops`.
    Levanta `ValueError` con un mensaje para mostrar si falta algo o algo no sirve."""
    form = FORMS[kind]
    for f in form["campos"]:
        v = values.get(f.key, f.default)
        if f.required and (v is None or (isinstance(v, str) and not v.strip())):
            raise ValueError(f"Falta «{f.label.split(':')[0]}».")
    stl = str(values.get("stl", "")).strip()
    pid = (str(values.get("id", "")).strip() or None)
    new = bool(values.get("nueva_version", False))
    desc = str(values.get("descripcion", "")).strip()
    if kind == "mano":
        return {"stl": stl, "args": (), "kwargs": {"piece_id": pid, "new_version": new}}
    if kind == "accesorio":
        canal = _num(values.get("largo_canal"), "Largo del canal")
        if canal is None or canal <= 0:
            raise ValueError("«Largo del canal» tiene que ser un número mayor que 0.")
        kw = {}
        for key, name, label in (("taladro", "bore_d", "Ø del taladro"), ("vastago", "shaft_d", "Ø del vástago"), ("ranura", "slit_w", "Ancho de la ranura"),
                                 ("largo_lengueta", "finger_len", "Largo de la lengüeta"), ("dentado", "barb_d", "Ø del dentado"), ("juego", "play", "Juego axial")):
            v = _num(values.get(key), label)
            if v is not None:
                kw[name] = v
        return {"stl": stl, "args": (canal,), "kwargs": {**kw, "piece_id": pid, "description": desc, "new_version": new}}
    if kind == "arma":
        try:
            pts = OPS.parse_cut_points(str(values.get("corte", "")).strip())
        except ValueError as exc:                    # un número mal escrito: `float()` no explica nada útil
            raise ValueError("el corte son tres puntos separados por «;», cada uno con tres números X,Y,Z (decimales con punto)") from exc
        scale, length = _num(values.get("escala"), "Factor"), _num(values.get("largo"), "Largo")
        if scale is not None and length is not None:
            raise ValueError("Poné el largo o el factor de escala, no los dos.")
        for v, label in ((scale, "El factor"), (length, "El largo")):
            if v is not None and v <= 0:
                raise ValueError(f"{label} tiene que ser mayor que 0.")
        return {"stl": stl, "args": (pts,), "kwargs": {"scale": scale, "length": length, "flip": bool(values.get("invertir", False)),
                                                        "roll": _num(values.get("giro"), "Giro"), "hand": (str(values.get("mano", "")).strip() or None),
                                                        "piece_id": pid, "description": desc, "new_version": new}}
    if kind == "pieza":
        tags = [t.strip() for t in re.split(r"[,;\s]+", str(values.get("etiquetas", ""))) if t.strip()]
        bad = [t for t in tags if t not in P.TAGS]
        if bad:
            raise ValueError(f"Etiqueta desconocida: {', '.join(bad)} (válidas: {', '.join(P.TAGS)}).")
        tipo = str(values.get("tipo", "")).strip()
        if tipo not in P.TYPES:
            raise ValueError(f"Tipo de pieza desconocido: «{tipo}».")
        return {"stl": stl, "args": (tipo, parse_ports(str(values.get("puertos", "")))),
                "kwargs": {"piece_id": pid, "height": _num(values.get("altura"), "Altura"), "side": str(values.get("lado", "izq")) or "izq",
                           "mirrorable": bool(values.get("espejable", True)), "description": desc, "figure": str(values.get("figura", "")).strip(),
                           "tags": tags, "new_version": new}}
    raise ValueError(f"importación desconocida: {kind}")


def run(kind: str, root: str, parsed: dict) -> OPS.ImportResult:
    """Ejecuta la importación ya validada sobre el catálogo `root` (abre el suyo: se puede llamar desde un hilo)."""
    from ..catalog import Catalog

    cat = Catalog.open(root)
    fn = {"mano": OPS.import_hand, "accesorio": OPS.import_accessory, "arma": OPS.import_weapon, "pieza": OPS.add_loose_piece}[kind]
    return fn(cat, parsed["stl"], *parsed["args"], **parsed["kwargs"])


def result_lines(res: OPS.ImportResult) -> list[str]:
    """Líneas del informe de una importación: lo que cuenta el resultado más los avisos."""
    return [*res.lines, *[f"AVISO: {w}" for w in res.warnings]]
