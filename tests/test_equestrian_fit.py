"""E09: ajuste iterativo del asiento (RidingFitter) con el caballo de referencia y el muñeco de ejemplo."""
import os

import numpy as np
import pytest

from afm import recipe as R
from afm.equestrian import (SAFE_MAX_DEPTH, CarveLimits, HorseError, HorseSpec, RidingFitter, load_horse_mesh, sha256_file)

ROOT = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
HORSE_DIR = os.path.join(os.path.dirname(__file__), "..", "examples", "referencia", "caballo")
HORSE = os.path.join(HORSE_DIR, "horse_baseless.stl")
pytestmark = pytest.mark.skipif(not os.path.exists(HORSE), reason="falta examples/referencia/caballo/horse_baseless.stl")


@pytest.fixture(scope="module")
def parts():
    h = load_horse_mesh(HorseSpec(archivo="horse_baseless.stl", sha256=sha256_file(HORSE), escala=3.0, yaw=180.0), base_dir=HORSE_DIR)
    asm = R.assemble(R.Recipe.load(os.path.join(ROOT, "recetas", "muneco.receta.json")), ROOT, sit=True)
    return h, asm


def test_the_depth_cap_cannot_be_raised(parts):
    h, asm = parts
    with pytest.raises(HorseError, match="máximo seguro"):
        RidingFitter(h, asm, limits=CarveLimits(max_depth=SAFE_MAX_DEPTH + 1))
    assert RidingFitter(h, asm, limits=CarveLimits(max_depth=SAFE_MAX_DEPTH)).limits.max_depth == SAFE_MAX_DEPTH


def test_accepted_and_rejected_attempts(parts):
    h, asm = parts
    vols = {k: float(v.volume()) for k, v in asm.res.pieces.items()}
    f = RidingFitter(h, asm, limits=CarveLimits(max_depth=10.0))
    rp = f.rp
    good = f.sweep([-20.0], mode="tallado")[0]
    assert good.ok and good.stage == "ok" and f.current is good and good.validation["ok"]
    bad = f.sweep([0.0], mode="tallado")[0]                       # ahí el tallado retira más del 3 % del caballo
    assert not bad.ok and bad.stage == "tallado" and any("%" in p for p in bad.problems)
    assert f.current is good and f.history == [good, bad]         # lo rechazado no reemplaza al último aceptado
    assert f.rp is rp                                              # la figura no se vuelve a leer ni a armar
    assert {k: float(v.volume()) for k, v in asm.res.pieces.items()} == vols


def test_a_new_seat_uses_a_new_rigid_matrix_but_the_same_pieces(parts):
    h, asm = parts
    f = RidingFitter(h, asm, limits=CarveLimits(max_depth=10.0))
    a = f.sweep([-20.0], mode="tallado")[0]
    b = f.fit(a.seat_origin, pitch_deg=5.0, mode="tallado")
    ba, bb = (np.asarray(r.assembly.rider["torso"].bounding_box()).reshape(2, 3) for r in (a, b))
    assert not np.allclose(ba, bb)                                 # la inclinación movió al jinete
    for k in a.assembly.rider:                                     # y todas las piezas conservan su volumen
        assert float(a.assembly.rider[k].volume()) == pytest.approx(float(b.assembly.rider[k].volume()), rel=1e-6)


def test_a_bad_saddle_stops_before_carving(parts):
    h, asm = parts
    f = RidingFitter(h, asm, limits=CarveLimits(max_depth=10.0))
    r = f.sweep([0.0], mode="silla")[0]                            # el lomo sobresale de la silla mínima
    assert not r.ok and r.stage == "silla" and r.assembly is None
    assert all(p.startswith("silla:") for p in r.problems)
    assert f.current is None


def test_back_height_sweep_and_bad_mode(parts):
    h, asm = parts
    f = RidingFitter(h, asm)
    assert f.back_z(0.0, 500.0) is None and f.back_z(0.0, 0.0) == pytest.approx(29.1, abs=0.5)
    assert f.sweep([500.0], mode="tallado") == []                  # fuera del caballo: no hay intento
    with pytest.raises(HorseError, match="mode"):
        f.fit((0, 0, 29), mode="otro")
