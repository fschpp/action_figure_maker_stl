"""E15: asiento por clic y arrastre, escala desde una medida y vista conjunta con colores de diagnóstico (lógica sin GTK, `gui/ride_pick.py`).

La ventana (`figure_window.py`, sección «Caballo») no se puede ejecutar en este entorno (sin GTK4 ni EGL): `test_la_ventana_*` solo comprueba
sobre su código que cada nombre que usa existe. Que se vea y responda al ratón queda sin probar.
"""
import ast
import os
import shutil

import numpy as np
import pytest
import trimesh

from afm import solids as S
from afm.equestrian import (FitResult, HorseSpec, RidingAssembly, carve_horse, load_horse_mesh, sha256_file)
from afm.gui import figure_builder as FB
from afm.gui import mount_pick as MP
from afm.gui import ride_pick as RP

HERE = os.path.dirname(__file__)
EJEMPLO = os.path.join(HERE, "..", "catalogo", "ejemplo")
HORSE = os.path.join(HERE, "..", "examples", "referencia", "caballo", "horse_baseless.stl")
SRC = os.path.join(HERE, "..", "src", "afm")
IMG = (840, 630)
WIDGET = (420, 400)
PIECES = (("cabeza", "cabeza-muneco-01@1"), ("torso", "torso-muneco-01@1"), ("brazo", "brazo-muneco-01@1"),
          ("pierna", "pierna-muneco-01@1"), ("mano", "mano-muneco-01@1"))


def _box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    m = trimesh.creation.box(extents=hi - lo)
    m.apply_translation((lo + hi) / 2)
    return m


def horse_from(tmp_path, mesh, name="h.stl"):
    p = tmp_path / name
    mesh.export(p)
    return load_horse_mesh(HorseSpec(archivo=p.name, sha256=sha256_file(str(p))), base_dir=str(tmp_path))


def leg_box(x, y, zlo, zhi, w=6.0, d=8.0):
    return S.to_manifold(_box((x - w / 2, y - d / 2, zlo), (x + w / 2, y + d / 2, zhi)))


@pytest.fixture()
def parts():
    """El caballo (una caja de 14 × 50 × 44 con el lomo en z = 10) como lo dibuja la vista del asiento."""
    return [("caballo", _box((-7, -25, -34), (7, 25, 10)), RP.HORSE_COLOR)]


# ------------------------------------------------------------------------------------------------------------------ clic y arrastre
def test_el_clic_da_un_punto_del_caballo(parts):
    cam = MP.group_camera(parts, *IMG)
    sp = RP.pick_seat(cam, IMG[0] / 2, IMG[1] / 2, *IMG, parts)
    assert sp.parte == "caballo" and abs(sp.x) <= 7.0 + 1e-6 and abs(sp.y) <= 25.0 + 1e-6 and -34.0 <= sp.z <= 10.0 + 1e-6
    # el mismo clic hecho en el widget (la imagen se ve achicada, con margen arriba y abajo) cae en el mismo punto
    w = RP.pick_seat_from_widget(WIDGET[0] / 2, WIDGET[1] / 2, WIDGET, IMG, parts)
    assert (w.x, w.y, w.z) == pytest.approx((sp.x, sp.y, sp.z), abs=0.05)
    # y con la vista girada el rayo sigue la cámara que se dibuja
    cam2 = MP.group_camera(parts, *IMG, 40.0, -10.0)
    sp2 = RP.pick_seat(cam2, IMG[0] / 2, IMG[1] / 2, *IMG, parts)
    assert RP.pick_seat_from_widget(WIDGET[0] / 2, WIDGET[1] / 2, WIDGET, IMG, parts, 40.0, -10.0).x == pytest.approx(sp2.x, abs=0.05)


def test_un_clic_fuera_del_caballo_o_sobre_otra_pieza_se_avisa(parts):
    cam = MP.group_camera(parts, *IMG)
    with pytest.raises(RP.RidePickError, match="fuera del caballo"):
        RP.pick_seat(cam, 2.0, 2.0, *IMG, parts)
    with pytest.raises(RP.RidePickError, match="fuera del caballo"):
        RP.pick_seat_from_widget(1.0, 1.0, WIDGET, IMG, parts)             # el margen de la imagen no es la figura
    sentado = parts + [("torso", _box((-90, -90, 14), (90, 90, 18)), (1, 1, 1, 1))]      # el jinete (una losa ancha sobre el lomo) lo tapa
    cam = MP.group_camera(sentado, *IMG)
    with pytest.raises(RP.RidePickError, match="«torso»"):
        RP.pick_seat(cam, IMG[0] / 2, IMG[1] / 2, *IMG, sentado)
    cut = parts + [("caballo_tallado", _box((-7, -25, 10), (7, 25, 11)), RP.CUT_COLOR)]    # las caras del tallado cuentan como caballo
    assert RP.pick_seat(MP.group_camera(cut, *IMG), IMG[0] / 2, IMG[1] / 2, *IMG, cut).parte.startswith("caballo")


def test_la_direccion_sale_sobre_el_plano_horizontal(parts):
    cam = MP.group_camera(parts, *IMG)
    anchor = (0.0, 0.0, 10.0)
    c = (IMG[0] / 2, IMG[1] / 2)
    d = RP.direction_on_ground(cam, anchor, (c[0] - 60, c[1]), (c[0] + 60, c[1]), *IMG)
    assert d[2] == 0.0 and np.hypot(d[0], d[1]) == pytest.approx(1.0, abs=0.01)
    fwd = np.asarray(cam.target, float) - np.asarray(cam.eye(), float)
    right = np.cross(fwd, (0.0, 0.0, 1.0))
    right = right[:2] / np.linalg.norm(right[:2])
    assert float(np.dot(d[:2], right)) > 0.99                             # un arrastre a la derecha de la pantalla va a la derecha de la cámara
    back = RP.direction_on_ground(cam, anchor, (c[0] + 60, c[1]), (c[0] - 60, c[1]), *IMG)
    assert back[0] == pytest.approx(-d[0], abs=0.02) and back[1] == pytest.approx(-d[1], abs=0.02)
    wd = RP.direction_from_widget(WIDGET[0] / 2 - 40, WIDGET[1] / 2, WIDGET[0] / 2 + 40, WIDGET[1] / 2, WIDGET, IMG, parts, anchor)
    assert wd[2] == 0.0 and np.dot(wd[:2], right) > 0.99


def test_un_arrastre_corto_o_una_vista_rasante_no_dan_direccion(parts):
    cam = MP.group_camera(parts, *IMG)
    with pytest.raises(RP.RidePickError, match="muy corto"):
        RP.direction_on_ground(cam, (0, 0, 10), (100, 100), (102, 101), *IMG)
    with pytest.raises(RP.RidePickError, match="muy corto"):
        RP.direction_from_widget(10, 10, 11, 10, WIDGET, IMG, parts, (0, 0, 10))
    with pytest.raises(RP.RidePickError, match="muy corto"):
        RP.direction_from_widget(10, 10, 80, 10, (0, 0), IMG, parts, (0, 0, 10))

    class Rasante:                                                         # cámara a ras del plano: los rayos no lo cortan
        def ray(self, px, py, w, h):
            return np.array([0.0, 0.0, 10.0]), np.array([0.0, 1.0, 0.0])

    with pytest.raises(RP.RidePickError, match="a ras"):
        RP.direction_on_ground(Rasante(), (0, 0, 10), (100, 100), (200, 100), *IMG)


def test_las_marcas_del_asiento():
    assert RP.seat_marks(None) is None
    m = RP.seat_marks((1.0, 2.0, 3.0), (3.0, 4.0, 5.0))                    # la parte vertical de la dirección no cuenta
    assert m.shape == (1 + RP.MARK_COUNT, 3) and tuple(m[0]) == (1.0, 2.0, 3.0)
    assert tuple(m[1]) == pytest.approx((1.0 + 0.6 * RP.MARK_STEP_MM, 2.0 + 0.8 * RP.MARK_STEP_MM, 3.0))
    assert RP.seat_marks((0, 0, 0), (0, 0, 1)).shape == (1, 3)             # sin parte horizontal: solo el punto


# ------------------------------------------------------------------------------------------------------------------ importar
def test_id_y_escala_desde_la_medida(tmp_path):
    assert RP.suggest_horse_id("/x/Caballo Pardo (v2).STL") == "caballo-pardo-v2" and RP.suggest_horse_id("/x/___.stl") == "caballo"
    stl = tmp_path / "c.stl"
    _box((0, 0, 0), (10, 50, 20)).export(stl)
    assert RP.suggest_scale(str(stl), "largo", 150.0) == pytest.approx(3.0)
    assert RP.suggest_scale(str(stl), "alto_total", 60.0) == pytest.approx(3.0)
    assert RP.suggest_scale(str(stl), "largo", 30.0, yaw=90.0) == pytest.approx(3.0)     # girado 90°, el largo es el ancho de antes (10)
    assert RP.suggest_scale(str(stl), "alto_dorso", 60.0) == pytest.approx(3.0)
    for bad in (0, -5, "x", None):
        with pytest.raises(RP.RidePickError, match="mayor que 0"):
            RP.suggest_scale(str(stl), "largo", bad)
    with pytest.raises(RP.RidePickError, match="desconocida"):
        RP.suggest_scale(str(stl), "ancho", 10.0)
    with pytest.raises(RP.RidePickError, match="No se pudo leer"):
        RP.suggest_scale(str(tmp_path / "no-esta.stl"), "largo", 10.0)


@pytest.mark.skipif(not os.path.exists(HORSE), reason="falta examples/referencia/caballo/horse_baseless.stl")
def test_la_escala_del_caballo_de_referencia():
    assert RP.suggest_scale(HORSE, "alto_total", 132.7, 180.0) == pytest.approx(3.0, abs=0.02)      # plano §1.4: 3× da ≈133 mm hasta la cabeza
    assert RP.suggest_scale(HORSE, "largo", 152.4, 180.0) == pytest.approx(3.0, abs=0.02)
    assert RP.suggest_scale(HORSE, "alto_dorso", 97.0, 180.0) == pytest.approx(3.0, abs=0.15)       # ≈97 mm de dorso al suelo a 3×


# ------------------------------------------------------------------------------------------------------------------ colores de diagnóstico
def test_los_nombres_que_senalan_los_mensajes_del_motor():
    f = RP.flagged(["pierna_izq ↔ caballo: 3.00 mm³ del jinete siguen dentro de la caballo", "caballo tallado: la pared mide 1 mm",
                    "despeje: no entra", "pelvis ↔ silla: la pelvis no apoya (2 mm)", "algo sin pareja ni prefijo"])
    assert f == {"pierna_izq", "caballo", "silla", "pelvis"}
    assert RP.flagged(["tallado: se pasa", "silla: flota"]) == {"caballo", "silla"} and RP.flagged([]) == set()


def _assembly(tmp_path, carve=True):
    horse = horse_from(tmp_path, _box((-20, -40, -40), (20, 40, 0)))
    legs = {"pierna_izq": leg_box(8, 0, -5.0, 30.0), "pierna_der": leg_box(-8, 0, -5.0, 30.0)}
    rider = {**legs, "torso": leg_box(0, 0, 30.0, 60.0, w=20.0)}
    c = carve_horse(horse, legs, clearance=0.5) if carve else None
    assert c is None or (c.ok and c.carved is not None)
    return RidingAssembly(horse=horse, rider=rider, lower=legs, carve=c), horse


def _fit(ra, **kw):
    base = dict(ok=True, mode="tallado", seat_origin=np.array([0.0, 0.0, 0.0]), direction=np.array([0.0, 1.0, 0.0]), pitch_deg=0.0, assembly=ra,
                validation={"ok": True, "problems": [], "avisos": [], "gaps": {"pierna_izq ↔ caballo": 0.52, "pierna_der ↔ caballo": 0.61},
                            "info": {"tallado": {"retirado_mm3": 1234.0, "profundidad_mm": 5.5, "pared_mm": 8.0, "fraccion": 0.004}}},
                problems=[], warnings=[], stage="ok")
    base.update(kw)
    return FitResult(**base)


def test_la_escena_pinta_el_tallado_y_deja_las_piezas_en_su_color(tmp_path):
    ra, horse = _assembly(tmp_path)
    sc = RP.diagnostic_scene(_fit(ra))
    col = {n: c for n, _m, c in sc.parts}
    assert list(col) == ["caballo", "caballo_tallado", "pierna_izq", "pierna_der", "torso"]
    assert col["caballo"] == RP.HORSE_COLOR and col["caballo_tallado"] == RP.CUT_COLOR
    assert col["pierna_izq"] == col["pierna_der"] == RP.LEG_COLOR and col["torso"] == RP.RIDER_COLOR
    assert sc.level == "ok" and sc.bounds.shape == (2, 3) and sc.marks.shape == (1 + RP.MARK_COUNT, 3)
    mesh = {n: m for n, m, _c in sc.parts}
    cut = mesh["caballo_tallado"]
    assert len(cut.faces) > 0
    total = len(mesh["caballo"].faces) + len(cut.faces)
    assert total == len(S.to_trimesh(ra.carve.carved).faces)              # se reparte entre las dos partes, nada se pierde ni se repite
    lo, hi = np.asarray(cut.bounds, float)                                # la pared del corte rodea a las piernas (con la holgura), no al caballo entero
    assert lo[0] >= -11.7 and hi[0] <= 11.7 and lo[1] >= -5.0 and hi[1] <= 5.0
    assert float(cut.area) < 0.5 * float(S.to_trimesh(ra.carve.carved).area)
    assert RP.diagnostic_scene(_fit(ra)).parts[0][1] is not None


def test_lo_que_el_motor_nombra_sale_en_rojo_o_ambar(tmp_path):
    ra, _horse = _assembly(tmp_path)
    fit = _fit(ra, ok=False, stage="validación", problems=["pierna_izq ↔ caballo: 3.00 mm³ del jinete siguen dentro de la caballo"],
               warnings=["torso ↔ silla: no apoya", "pierna_izq ↔ silla: aviso repetido"])
    sc = RP.diagnostic_scene(fit)
    col = {n: c for n, _m, c in sc.parts}
    assert col["pierna_izq"] == RP.BAD_COLOR                               # problema y aviso a la vez: gana el problema
    assert col["torso"] == RP.WARN_COLOR and col["pierna_der"] == RP.LEG_COLOR
    assert col["caballo"][0] > RP.HORSE_COLOR[0] and col["caballo"][1] < RP.HORSE_COLOR[1]       # el caballo se tiñe de rojo, no se pinta entero
    assert col["caballo"] != RP.BAD_COLOR and sc.level == "no"
    aviso = RP.diagnostic_scene(_fit(ra, warnings=["torso ↔ silla: no apoya"]))
    assert aviso.level == "aviso"


def test_sin_jinete_colocado_se_dibuja_el_caballo(tmp_path):
    ra, horse = _assembly(tmp_path, carve=False)
    fit = _fit(None, ok=False, stage="silla", problems=["silla: no se pudo generar"], validation=None)
    sc = RP.diagnostic_scene(fit, horse)
    assert [n for n, _m, _c in sc.parts] == ["caballo"] and sc.level == "no" and "silla" in sc.text
    with pytest.raises(RP.RidePickError, match="Falta el caballo"):
        RP.diagnostic_scene(fit)
    sin_corte = RP.diagnostic_scene(_fit(ra))                              # sin tallado el caballo se dibuja tal cual está
    assert [n for n, _m, _c in sin_corte.parts] == ["caballo", "pierna_izq", "pierna_der", "torso"]


def test_el_texto_del_resultado(tmp_path):
    ra, _h = _assembly(tmp_path, carve=False)
    ok = RP.fit_text(_fit(ra))
    assert ok.startswith("Monta bien") and "1.23 cm³" in ok and "0.40 %" in ok and "5.50 mm" in ok
    assert "pierna_izq ↔ caballo, 0.52 mm" in ok and "Colores:" in ok
    no = RP.fit_text(_fit(ra, ok=False, stage="tallado", problems=["tallado: el rebaje entra 9 mm"], validation=None))
    assert no.startswith("No monta") and "- tallado: el rebaje entra 9 mm" in no and "«tallado»" in no
    av = RP.fit_text(_fit(ra, warnings=["pelvis ↔ silla: la pelvis no apoya"]))
    assert av.startswith("Monta en este asiento, con avisos") and "- pelvis ↔ silla" in av


# ------------------------------------------------------------------------------------------------------------------ constructor real
@pytest.fixture(scope="module")
def real(tmp_path_factory):
    """El muñeco de ejemplo sobre el caballo de referencia: un asiento que pasa y otro en el que el tallado retira demasiado."""
    if not os.path.exists(HORSE):
        pytest.skip("falta examples/referencia/caballo/horse_baseless.stl")
    dst = str(tmp_path_factory.mktemp("e15") / "ejemplo")
    shutil.copytree(EJEMPLO, dst, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    fb = FB.FigureBuilder(dst)
    for place, ref in PIECES:
        fb.set_choice(place, ref)
    fb.add_horse(HORSE, "referencia", nombre="Referencia", escala=3.0, yaw=180.0)
    fb.set_horse("referencia")
    assert fb.saddle_suggestion() is None                                  # todavía no se armó la figura
    fb.seat_on_back(0.0, -20.0)
    fb.set_ride_limits(max_depth=10.0)
    ok = fb.check_ride()
    fb.seat_on_back(0.0, 0.0)
    bad = fb.check_ride()
    return fb, ok, bad


def test_la_escena_del_caballo_real_y_el_clic(real):
    fb, _ok, _bad = real
    horse = fb.horse_model()
    sc = RP.horse_scene(horse)
    assert [n for n, _m, _c in sc.parts] == ["caballo"] and sc.parts[0][1] is horse.mesh
    cam = MP.group_camera(sc.parts, *IMG)
    sp = RP.pick_seat(cam, IMG[0] / 2, IMG[1] / 2, *IMG, sc.parts)         # el centro de la imagen cae sobre el caballo
    assert sp.parte == "caballo"
    z = fb.seat_on_back(sp.x, sp.y)[2]                                     # y el constructor lo lleva al lomo
    assert z >= sp.z - 1e-6
    d = RP.direction_on_ground(cam, (sp.x, sp.y, z), (300, 300), (500, 300), *IMG)
    fb.set_seat(direccion=d)
    assert fb.ride["direccion"] == d


def test_el_ajuste_real_que_pasa_se_dibuja_con_su_tallado(real):
    fb, ok, _bad = real
    assert ok.ok
    sc = RP.diagnostic_scene(ok)
    names = [n for n, _m, _c in sc.parts]
    assert names[0] == "caballo" and "caballo_tallado" in names and "pierna_izq" in names or "pierna_izq_0" in " ".join(names) or len(names) > 3
    assert sc.level in ("ok", "aviso") and sc.text.startswith("Monta") and "Tallado:" in sc.text
    assert all(len(m.faces) > 0 for _n, m, _c in sc.parts)
    cut = next(m for n, m, _c in sc.parts if n == "caballo_tallado")
    assert 0.0 < float(cut.area) < float(next(m for n, m, _c in sc.parts if n == "caballo").area)
    sug = fb.saddle_suggestion()                                           # armada la figura, la silla se propone desde los muslos
    assert sug is not None and sug.ancho > 0 and sug.largo > 0


def test_el_ajuste_real_que_no_pasa_marca_el_caballo(real):
    _fb, _ok, bad = real
    assert not bad.ok and bad.stage == "tallado"
    sc = RP.diagnostic_scene(bad)
    assert sc.level == "no" and sc.text.startswith("No monta") and "«tallado»" in sc.text
    col = {n: c for n, _m, c in sc.parts}
    assert col["caballo"][0] > RP.HORSE_COLOR[0]                           # «tallado: …» señala al caballo


# ------------------------------------------------------------------------------------------------------------------ la ventana
def _class(path, name):
    tree = ast.parse(open(os.path.join(SRC, "gui", path), encoding="utf-8").read())
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _self_attrs(cls, load=True):
    out = set()
    for n in ast.walk(cls):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self":
            if isinstance(n.ctx, ast.Load) == load:
                out.add(n.attr)
    return out


def test_la_ventana_solo_usa_nombres_que_existen():
    """Sin GTK no se puede abrir la ventana: al menos, que todo lo `ride` que usa esté definido, y que lo que pide al constructor y a
    `ride_pick` exista."""
    win = _class("figure_window.py", "FigureBuilderWindow")
    methods = {n.name for n in win.body if isinstance(n, ast.FunctionDef)}
    defined = methods | _self_attrs(win, load=False)
    used = {a for a in _self_attrs(win) if "ride" in a}
    assert used - defined == set()
    assert {"_build_ride_section", "_refill_ride", "_on_ride_check", "_apply_ride_seat", "_apply_ride_direction"} <= methods
    # lo que se le pide al constructor: método, propiedad o campo suyo
    fbc = _class("figure_builder.py", "FigureBuilder")
    fb_names = {n.name for n in fbc.body if isinstance(n, ast.FunctionDef)} | _self_attrs(fbc, load=False)
    tree = ast.parse(open(os.path.join(SRC, "gui", "figure_window.py"), encoding="utf-8").read())
    asked = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Attribute)
             and isinstance(n.value.value, ast.Name) and n.value.value.id == "self" and n.value.attr == "builder"}
    assert asked - fb_names == set()
    rp_asked = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "RP"}
    assert rp_asked and all(hasattr(RP, a) for a in rp_asked)


def test_la_ventana_conecta_cada_aviso_a_un_metodo():
    win = _class("figure_window.py", "FigureBuilderWindow")
    methods = {n.name for n in win.body if isinstance(n, ast.FunctionDef)}
    handlers = set()
    for n in ast.walk(win):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "connect" and len(n.args) >= 2:
            h = n.args[1]
            if isinstance(h, ast.Attribute) and isinstance(h.value, ast.Name) and h.value.id == "self" and "ride" in h.attr:
                handlers.add(h.attr)
    assert {"_on_ride_horse", "_on_ride_import", "_on_ride_scale", "_on_ride_add", "_on_ride_field", "_on_ride_pick_toggle",
            "_on_ride_view_toggle", "_on_ride_mode", "_on_ride_check"} <= handlers
    assert handlers <= methods
