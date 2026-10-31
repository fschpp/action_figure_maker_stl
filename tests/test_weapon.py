"""Armas importadas: escala, plano de corte, mango nuevo con la empuñadura de la línea (`afm.weapon`)."""
import numpy as np
import pytest
import trimesh

from afm import props as P
from afm import solids as S
from afm import weapon as W

GRIP = P.GripSpec()
HOLE = 10.0


def _sword_manifold(handle_d=4.0):
    """Espada plana a lo largo de +X: hoja x∈[0,40] (ancho 4, grosor 1,2), guarda x∈[40,42] (10 × 1,2) y mango x∈[42,54] redondo."""
    blade = S.Manifold.cube((40, 4, 1.2)).translate((0, -2, -0.6))
    guard = S.Manifold.cube((2, 10, 1.2)).translate((40, -5, -0.6))
    handle = S.Manifold.cylinder(12, handle_d / 2, handle_d / 2, 48).rotate((0, 90, 0)).translate((42, 0, 0))
    return S.union(blade, guard, handle)


def _rotation():
    return trimesh.transformations.random_rotation_matrix(np.random.default_rng(7).random(3))


@pytest.fixture()
def sword_stl(tmp_path):
    """La espada, girada al azar y en centímetros (unidades del STL ≠ mm), más los 3 puntos del plano a x=44 (en el mango)."""
    tm = S.to_trimesh(_sword_manifold())
    R = _rotation()
    tm.apply_transform(R)
    tm.apply_scale(0.1)                                            # cm
    path = str(tmp_path / "excalibur.stl")
    tm.export(path)
    pts = np.array([[44, 0, 0], [44, 1, 0], [44, 0, 1]], float)    # normal +X (hacia el extremo del mango)
    pts = (R[:3, :3] @ pts.T).T * 0.1
    return path, tuple(map(tuple, pts))


def test_scale_helpers():
    m = S.to_trimesh(_sword_manifold())
    assert W.scale_for_length(m, 51.0) == pytest.approx(51.0 / 54.0)
    assert W.suggest_length(127.0) == pytest.approx(50.8)
    with pytest.raises(W.WeaponError):
        W.scale_for_length(m, 0)


def test_load_applies_the_scale_and_repairs(sword_stl):
    path, _ = sword_stl
    m = W.load_weapon_mesh(path, scale=10.0)
    assert m.is_watertight and W.longest_dimension(m) == pytest.approx(54.0, abs=0.5)
    assert W.scale_for_length(m, 27.0) == pytest.approx(0.5, abs=0.01)         # el STL viene girado: el largo se mide en sus ejes principales


def test_build_replaces_the_handle_with_the_lines_grip(sword_stl):
    path, pts = sword_stl
    spec = W.WeaponSpec(path, scale=10.0, cut_points=pts)
    b = W.build_weapon(W.load_weapon_mesh(path, 10.0), spec, GRIP, HOLE)
    bb = np.asarray(b.mesh.bounding_box()).reshape(2, 3)
    x_end = HOLE + GRIP.play + GRIP.back_len
    assert bb[0, 0] == pytest.approx(-44.0, abs=0.2) and bb[1, 0] == pytest.approx(x_end, abs=0.05)   # arma hasta el corte; el vástago sale hacia +X
    assert b.info["volumen_mantenido_pct"] > 60
    # el resto del mango es redondo de Ø4 y el vástago de Ø2: queda 1 mm sobre la cama al imprimir acostada → aviso de soporte (y nada más)
    assert b.info["vastago_sobre_cama_mm"] == pytest.approx(1.0, abs=0.05)
    assert len(b.info["avisos"]) == 1 and "por encima de la cama" in b.info["avisos"][0]
    # el vástago está centrado en el eje: sección del vástago a la mitad del canal
    tm = S.to_trimesh(b.mesh)
    shaft = tm.vertices[(tm.vertices[:, 0] > 2.0) & (tm.vertices[:, 0] < 6.0)]
    assert np.hypot(shaft[:, 1], shaft[:, 2]).max() == pytest.approx(GRIP.shaft_d / 2, abs=0.02)
    assert tm.is_watertight and tm.volume > 0
    # el mango original (x > 44) ya no está: nada del arma más allá de la cara de corte salvo la empuñadura (radio ≤ dentado/2)
    beyond = tm.vertices[tm.vertices[:, 0] > 0.05]
    assert np.hypot(beyond[:, 1], beyond[:, 2]).max() <= GRIP.barb_d / 2 + 1e-3


def test_auto_roll_lays_the_blade_flat(sword_stl):
    path, pts = sword_stl
    mesh = W.load_weapon_mesh(path, 10.0)

    def blade_height(roll):
        b = W.build_weapon(mesh, W.WeaponSpec(path, 10.0, pts, roll=roll), GRIP, HOLE)
        keep = S.intersection(b.mesh, S.Manifold.cube((100, 100, 100)).translate((-100.0, -50, -50)))   # solo x < 0 (sin el vástago)
        bb = np.asarray(keep.bounding_box()).reshape(2, 3)
        return bb[1, 2] - bb[0, 2]
    auto = blade_height(None)
    assert auto <= 4.0 + 0.05                                      # la guarda de 10 mm queda acostada: manda el mango redondo de Ø4
    worst = max(blade_height(float(r)) for r in range(0, 180, 15))
    assert worst > auto + 3.0                                      # con otros giros la guarda de 10 mm queda de canto


def test_flip_discards_the_other_side(sword_stl):
    path, pts = sword_stl
    mesh = W.load_weapon_mesh(path, 10.0)
    b = W.build_weapon(mesh, W.WeaponSpec(path, 10.0, pts, flip=True), GRIP, HOLE)
    assert 30 < b.info["volumen_mantenido_pct"] < 40              # se quedó con el resto del mango (x ∈ [44, 54]) y no con la hoja


def test_thin_section_warns(sword_stl, tmp_path):
    path, _ = sword_stl
    R = _rotation()                                                 # mismos giros que el fixture: mismo seed
    pts = (R[:3, :3] @ np.array([[20, 0, 0], [20, 1, 0], [20, 0, 1]], float).T).T * 0.1     # corte por la hoja (4 × 1,2 mm)
    b = W.build_weapon(W.load_weapon_mesh(path, 10.0), W.WeaponSpec(path, 10.0, tuple(map(tuple, pts))), GRIP, HOLE)
    assert any("no rodea el vástago" in a for a in b.info["avisos"])


def test_bad_inputs(sword_stl):
    path, pts = sword_stl
    mesh = W.load_weapon_mesh(path, 10.0)
    with pytest.raises(W.WeaponError, match="3 puntos"):
        W.build_weapon(mesh, W.WeaponSpec(path, 10.0, ()), GRIP, HOLE)
    with pytest.raises(W.WeaponError, match="colineales"):
        W.build_weapon(mesh, W.WeaponSpec(path, 10.0, ((0, 0, 0), (1, 0, 0), (2, 0, 0))), GRIP, HOLE)
    far = tuple((p[0] + 1000, p[1], p[2]) for p in pts)
    with pytest.raises(W.WeaponError):
        W.build_weapon(mesh, W.WeaponSpec(path, 10.0, far), GRIP, HOLE)       # plano lejos del arma: queda vacía o no corta
    with pytest.raises(W.WeaponError, match="no se pudo leer|no tiene una malla"):
        W.load_weapon_mesh("/no/existe.stl")


def test_spec_round_trip():
    s = W.WeaponSpec("a.stl", 2.5, ((0, 0, 0), (1, 0, 0), (0, 1, 0)), flip=True, roll=30.0)
    assert W.WeaponSpec.from_dict(s.to_dict()) == s


# ---------------------------------------------------------------- catálogo de armas
from afm.catalog import Catalog                                   # noqa: E402
from afm.hand import load_standard_hand, save_standard_hand       # noqa: E402

from test_catalog_grip import HAND_AG                             # noqa: E402,F401
from test_gui_hand_place import std_hand                          # noqa: E402,F401  (fixture)


@pytest.fixture()
def cat_hand(tmp_path, std_hand):                                 # noqa: F811
    cat = Catalog.create(str(tmp_path / "linea"), "linea-prueba", 127.0)
    hand, meta = load_standard_hand(std_hand)
    path = str(tmp_path / "mano-agarre.stl")
    save_standard_hand(path, hand, {**meta, "agarre": HAND_AG})
    cat.import_hand(path, "mano-agarre-01")
    return cat


def test_line_grip_follows_the_hand():
    spec, hole = W.line_grip(HAND_AG)
    assert (spec.bore_d, spec.shaft_d, spec.barb_d, hole) == (2.4, 2.0, 2.9, 10.0)


def test_catalog_needs_a_hand_with_grip(tmp_path, sword_stl):
    path, pts = sword_stl
    cat = Catalog.create(str(tmp_path / "vacia"), "linea-vacia", 127.0)
    with pytest.raises(W.WeaponError, match="ninguna mano con agarre"):
        W.add_weapon_to_catalog(cat, W.load_weapon_mesh(path, 10.0), W.WeaponSpec(path, 10.0, pts))


def test_weapon_goes_to_the_catalog_and_fits_the_lines_hand(cat_hand, sword_stl):
    path, pts = sword_stl
    spec = W.WeaponSpec(path, 10.0, pts)
    meta, warns = W.add_weapon_to_catalog(cat_hand, W.load_weapon_mesh(path, 10.0), spec, piece_id="excalibur-01")
    assert meta.tipo == "accesorio" and meta.ref == "excalibur-01@1"
    arma = meta.mecanismo["arma"]
    assert arma["mano"] == "mano-agarre-01@1" and arma["preparacion"]["scale"] == 10.0 and arma["preparacion"]["path"] == "excalibur.stl"
    assert any("por encima de la cama" in w for w in warns)             # mango redondo de Ø4: pide soporte bajo el vástago
    assert cat_hand.verify().problems == []
    sirve = cat_hand.compatible_grips("mano-agarre-01")
    assert [(o.ref, c.nivel) for o, c in sirve] == [("excalibur-01@1", "ok")]     # armada con las medidas de esa mano: encaja
    assert cat_hand.meta("excalibur-01").mecanismo["agarre"]["taladro_mm"] == 2.4


def test_default_id_comes_from_the_file_name(cat_hand, sword_stl):
    path, pts = sword_stl
    meta, _ = W.add_weapon_to_catalog(cat_hand, W.load_weapon_mesh(path, 10.0), W.WeaponSpec(path, 10.0, pts))
    assert meta.id == "excalibur-01"


def test_cli_importar_arma(cat_hand, sword_stl, capsys):
    from afm.cli import main

    path, pts = sword_stl
    corte = "--corte=" + ";".join(",".join(f"{c:.6f}" for c in p) for p in pts)
    code = main(["catalogo", "importar-arma", cat_hand.root, path, corte, "--largo", "54", "--id", "excalibur-02"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "Arma guardada como excalibur-02@1 (×10" in out and "se sujeta en mano-agarre-01@1: ok" in out
    assert cat_hand.meta("excalibur-02").mecanismo["arma"]["preparacion"]["scale"] == pytest.approx(10.0, rel=0.02)
    # por defecto el largo sale de la altura de la línea (127 mm → 50,8 mm de arma)
    code = main(["catalogo", "importar-arma", cat_hand.root, path, corte, "--id", "excalibur-03"])
    assert code == 0 and cat_hand.meta("excalibur-03").mecanismo["arma"]["preparacion"]["scale"] == pytest.approx(10.0 * 50.8 / 54.0, rel=0.02)


def test_cli_importar_arma_reports_errors(cat_hand, sword_stl, capsys):
    from afm.cli import main

    path, _ = sword_stl
    assert main(["catalogo", "importar-arma", cat_hand.root, path, "--corte=0,0,0;1,0,0;2,0,0", "--escala", "10"]) == 2
    assert "colineales" in capsys.readouterr().err
    assert main(["catalogo", "importar-arma", cat_hand.root, path, "--corte=0,0;1,0,0;2,0,0", "--escala", "10"]) == 2
    assert "tres números" in capsys.readouterr().err


def test_build_remembers_how_to_go_back_to_the_original_frame(sword_stl):
    path, pts = sword_stl
    mesh = W.load_weapon_mesh(path, 10.0)
    b = W.build_weapon(mesh, W.WeaponSpec(path, 10.0, pts), GRIP, HOLE)
    kept = S.intersection(b.mesh, S.Manifold.cube((100, 100, 100)).translate((-100.0, -50, -50)))     # solo el arma, sin el vástago
    back = kept.transform(b.to_mesh_frame[:3, :].astype(np.float64))
    orig = S.to_manifold(mesh)
    inter = S.intersection(back, orig).volume()
    assert inter == pytest.approx(back.volume(), rel=0.01)                  # lo que se conservó está dentro del arma original
    assert back.volume() > 0.6 * orig.volume()
