"""Importar al catálogo desde la ventana «Administrar catálogo» (`afm catalogo importar-mano / -accesorio / -arma / agregar-stl`).

Sin GTK: `FORMS` describe los campos de cada formulario, `parse` valida lo que escribió la persona y lo convierte en los argumentos
de `catalog_ops`, y `run` abre el catálogo y ejecuta la importación (se llama desde un hilo). La ventana solo dibuja los campos.

Los 3 puntos del plano de corte del arma y los 3 puntos de cada puerto de una pieza suelta se **escriben** (X,Y,Z sobre el STL,
como en la consola); elegirlos con clics sobre una vista previa del STL es el paso siguiente."""
from __future__ import annotations
from ..i18n import tr, lz

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


_ID = Field("id", lz("catalog_import.id_de_la_pieza"), TEXT, "", False, (), lz("catalog_import.vacio_se_arma_con_el_nombre"))
_DESC = Field("descripcion", lz("catalog_import.descripcion"), TEXT)
_NEW = Field("nueva_version", lz("catalog_import.si_el_id_ya_existe_guardar"), BOOL, False)

FORMS: dict[str, dict] = {
    "mano": {
        "titulo": lz("catalog_import.importar_una_mano_estandar"), "comando": "importar-mano",
        "ayuda": lz("catalog_import.stl_de_una_mano_afm_mano"),
        "campos": [Field("stl", lz("catalog_import.stl_de_la_mano"), FILE, required=True), _ID, _NEW],
    },
    "accesorio": {
        "titulo": lz("catalog_import.importar_un_accesorio"), "comando": "importar-accesorio",
        "ayuda": lz("catalog_import.stl_cerrado_en_el_marco_del"),
        "campos": [
            Field("stl", lz("catalog_import.stl_del_accesorio"), FILE, required=True),
            Field("largo_canal", lz("catalog_import.largo_del_canal_de_la_mano"), FLOAT, "", True, (), lz("catalog_import.esta_en_el_mano_json_de")),
            Field("taladro", lz("catalog_import.del_taladro_de_la_mano_mm"), FLOAT, 2.4), Field("vastago", lz("catalog_import.del_vastago_mm"), FLOAT, 2.0),
            Field("ranura", lz("catalog_import.ancho_de_la_ranura_mm"), FLOAT, 0.5), Field("largo_lengueta", lz("common.largo_de_la_lengueta_mm"), FLOAT, 8.0),
            Field("dentado", lz("catalog_import.del_dentado_mm"), FLOAT, 2.9), Field("juego", lz("catalog_import.juego_axial_mm"), FLOAT, 0.4),
            _ID, _DESC, _NEW],
    },
    "arma": {
        "titulo": lz("catalog_import.importar_un_arma"), "comando": "importar-arma",
        "ayuda": lz("catalog_import.cualquier_stl_se_escala_se_le"),
        "campos": [
            Field("stl", lz("catalog_import.stl_del_arma"), FILE, required=True),
            Field("corte", lz("catalog_import.plano_de_corte_3_puntos_x"), TEXT, "", True, (),
                  lz("catalog_import.en_coordenadas_del_stl_original_sin")),
            Field("largo", lz("catalog_import.largo_de_la_dimension_mayor_mm"), FLOAT, "", False, (), lz("catalog_import.vacio_0_40_la_altura_de")),
            Field("escala", lz("catalog_import.o_factor_sobre_las_unidades_del"), FLOAT, ""),
            Field("invertir", lz("catalog_import.descartar_el_otro_lado_del_plano"), BOOL, False),
            Field("giro", lz("catalog_import.giro_sobre_el_eje_de_la"), FLOAT, "", False, (), lz("catalog_import.vacio_el_que_deja_el_arma")),
            Field("mano", lz("catalog_import.mano_con_agarre"), TEXT, "", False, (), lz("catalog_import.vacio_la_mas_reciente_con_agarre")),
            _ID, _DESC, _NEW],
    },
    "pieza": {
        "titulo": lz("catalog_import.agregar_una_pieza_suelta"), "comando": "agregar-stl",
        "ayuda": lz("catalog_import.stl_cerrado_3_puntos_por_puerto"),
        "campos": [
            Field("stl", lz("catalog_import.stl_de_la_pieza"), FILE, required=True),
            Field("tipo", lz("catalog_import.tipo_de_pieza"), CHOICE, "brazo", True, tuple(P.TYPES)),
            Field("puertos", lz("catalog_import.puertos_uno_por_linea_nombre_x1"), MULTILINE, "", True, (),
                  lz("catalog_import.los_nombres_validos_dependen_del_tipo")),
            Field("lado", lz("catalog_import.lado"), CHOICE, "izq", False, tuple(P.SIDES)),
            Field("espejable", lz("catalog_import.espejable"), BOOL, True),
            Field("altura", lz("catalog_import.altura_de_la_figura_de_origen"), FLOAT, "", False, (), lz("catalog_import.vacio_la_de_la_linea")),
            Field("figura", lz("catalog_import.figura_de_origen_texto_para_el"), TEXT),
            Field("etiquetas", lz("catalog_import.etiquetas_estandar_propia_separadas_por_co"), TEXT),
            _ID, _DESC, _NEW],
    },
}

LABELS = {"mano": lz("catalog_import.mano_estandar"), "accesorio": lz("catalog_import.accesorio"), "arma": lz("catalog_import.arma"), "pieza": lz("catalog_import.pieza_suelta")}


def _num(text, label: str) -> float | None:
    text = str(text).strip().replace(",", ".") if text is not None else ""
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(tr("catalog_import.label_text_no_es_un_numero", label=label, text=text)) from exc


def parse_ports(text: str) -> dict:
    """Líneas «NOMBRE x1 y1 z1 x2 y2 z2 x3 y3 z3» → {nombre: [[x, y, z] × 3]} (se aceptan espacios, comas y «;»)."""
    out: dict = {}
    for n, line in enumerate((ln for ln in text.splitlines() if ln.strip()), 1):
        toks = [t for t in re.split(r"[\s,;]+", line.strip()) if t]
        name, nums = toks[0], toks[1:]
        if len(nums) != 9:
            raise ValueError(tr("catalog_import.puerto_n_name_hacen_falta_9", n=n, name=name, n2=len(nums)))
        try:
            vals = [float(x.replace(",", ".")) for x in nums]
        except ValueError as exc:
            raise ValueError(tr("catalog_import.puerto_n_name_hay_un_valor", n=n, name=name)) from exc
        if name in out:
            raise ValueError(tr("catalog_import.el_puerto_name_esta_repetido", name=name))
        out[name] = [vals[0:3], vals[3:6], vals[6:9]]
    if not out:
        raise ValueError(tr("catalog_import.falta_al_menos_un_puerto_3"))
    return out


def parse(kind: str, values: dict) -> dict:
    """Valida `values` (clave del campo → texto / número / bool) y devuelve `{"stl": …, "args": (…), "kwargs": {…}}` para `catalog_ops`.
    Levanta `ValueError` con un mensaje para mostrar si falta algo o algo no sirve."""
    form = FORMS[kind]
    for f in form["campos"]:
        v = values.get(f.key, f.default)
        if f.required and (v is None or (isinstance(v, str) and not v.strip())):
            raise ValueError(tr("catalog_import.falta_v", v=f.label.split(':')[0]))
    stl = str(values.get("stl", "")).strip()
    pid = (str(values.get("id", "")).strip() or None)
    new = bool(values.get("nueva_version", False))
    desc = str(values.get("descripcion", "")).strip()
    if kind == "mano":
        return {"stl": stl, "args": (), "kwargs": {"piece_id": pid, "new_version": new}}
    if kind == "accesorio":
        canal = _num(values.get("largo_canal"), tr("catalog_import.largo_del_canal"))
        if canal is None or canal <= 0:
            raise ValueError(tr("catalog_import.largo_del_canal_tiene_que_ser"))
        kw = {}
        for key, name, label in (("taladro", "bore_d", tr("catalog_import.del_taladro")), ("vastago", "shaft_d", tr("catalog_import.del_vastago")), ("ranura", "slit_w", tr("catalog_import.ancho_de_la_ranura")),
                                 ("largo_lengueta", "finger_len", tr("catalog_import.largo_de_la_lengueta")), ("dentado", "barb_d", tr("catalog_import.del_dentado")), ("juego", "play", tr("catalog_import.juego_axial"))):
            v = _num(values.get(key), label)
            if v is not None:
                kw[name] = v
        return {"stl": stl, "args": (canal,), "kwargs": {**kw, "piece_id": pid, "description": desc, "new_version": new}}
    if kind == "arma":
        try:
            pts = OPS.parse_cut_points(str(values.get("corte", "")).strip())
        except ValueError as exc:                    # un número mal escrito: `float()` no explica nada útil
            raise ValueError(tr("catalog_import.el_corte_son_tres_puntos_separados")) from exc
        scale, length = _num(values.get("escala"), tr("catalog_import.factor")), _num(values.get("largo"), tr("catalog_import.largo"))
        if scale is not None and length is not None:
            raise ValueError(tr("catalog_import.pone_el_largo_o_el_factor"))
        for v, label in ((scale, tr("catalog_import.el_factor")), (length, tr("catalog_import.el_largo"))):
            if v is not None and v <= 0:
                raise ValueError(tr("catalog_import.label_tiene_que_ser_mayor_que", label=label))
        return {"stl": stl, "args": (pts,), "kwargs": {"scale": scale, "length": length, "flip": bool(values.get("invertir", False)),
                                                        "roll": _num(values.get("giro"), tr("catalog_import.giro")), "hand": (str(values.get("mano", "")).strip() or None),
                                                        "piece_id": pid, "description": desc, "new_version": new}}
    if kind == "pieza":
        tags = [t.strip() for t in re.split(r"[,;\s]+", str(values.get("etiquetas", ""))) if t.strip()]
        bad = [t for t in tags if t not in P.TAGS]
        if bad:
            raise ValueError(tr("catalog_import.etiqueta_desconocida_join_validas_join2", join=', '.join(bad), join2=', '.join(P.TAGS)))
        tipo = str(values.get("tipo", "")).strip()
        if tipo not in P.TYPES:
            raise ValueError(tr("catalog_import.tipo_de_pieza_desconocido_tipo", tipo=tipo))
        return {"stl": stl, "args": (tipo, parse_ports(str(values.get("puertos", "")))),
                "kwargs": {"piece_id": pid, "height": _num(values.get("altura"), tr("catalog_import.altura")), "side": str(values.get("lado", "izq")) or "izq",
                           "mirrorable": bool(values.get("espejable", True)), "description": desc, "figure": str(values.get("figura", "")).strip(),
                           "tags": tags, "new_version": new}}
    raise ValueError(tr("catalog_import.importacion_desconocida_kind", kind=kind))


def run(kind: str, root: str, parsed: dict) -> OPS.ImportResult:
    """Ejecuta la importación ya validada sobre el catálogo `root` (abre el suyo: se puede llamar desde un hilo)."""
    from ..catalog import Catalog

    cat = Catalog.open(root)
    fn = {"mano": OPS.import_hand, "accesorio": OPS.import_accessory, "arma": OPS.import_weapon, "pieza": OPS.add_loose_piece}[kind]
    return fn(cat, parsed["stl"], *parsed["args"], **parsed["kwargs"])


def result_lines(res: OPS.ImportResult) -> list[str]:
    """Líneas del informe de una importación: lo que cuenta el resultado más los avisos."""
    return [*res.lines, *[tr("common.aviso_w", w=w) for w in res.warnings]]
