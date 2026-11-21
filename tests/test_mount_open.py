"""M16: tipos abiertos de montura (`clip` y `cuna`) sobre piezas sintéticas: caja como flanco, `make_prop` como espada."""
import numpy as np
import pytest

from afm import props as P
from afm import solids as S
from afm.mount import (CLIP_SNAP, EXTRACT_MARGIN, MountError, MountSpec, apply_mounts, attach_lazo, build_lazo, build_mount,
                       extraction_scan, extraction_vector, mirrored_for_side, mount_solid)

MIN_WALL = 1.2


def _sword(**kw):
    return P.make_prop(P.GripSpec(), 10.0, **kw)


def _flank():
    """Caja de 20 × 20 × 60 con la cara del flanco en x = 10 (normal +X)."""
    return S.Manifold.cube((20.0, 20.0, 60.0)).translate((-10.0, -10.0, 0.0))


def _spec(**over):
    d = dict(arma="espada@1", lugar="pelvis", ancla=(10.0, 0.0, 40.0), direccion=(0.0, 0.0, -1.0))
    d.update(over)
    return MountSpec(**d)


def _vol(a, b):
    return float((a ^ b).volume())


def _lift(lz, mm):
    e, _d, _m = extraction_vector(lz)
    return lz.weapon.translate(tuple(float(x) for x in e * mm))


# ---------------------------------------------------------------- clip
def test_clip_tiene_ranura_mas_angosta_que_el_hueco_y_retiene_a_presion():
    lz = build_mount(_spec(tipo="clip"), _sword(), MIN_WALL, normal=(1, 0, 0))
    i = lz.info
    assert lz.kind == "clip" and i["tipo"] == "clip"
    hole_w = i["abertura_mm"][0]
    assert i["ranura_mm"] == pytest.approx(hole_w - 2 * CLIP_SNAP, abs=1e-3)
    assert i["interferencia_mm"] == pytest.approx(CLIP_SNAP - 0.25, abs=1e-3)       # 0,2 mm por lado
    assert i["retiene"] and not i["avisos"]


def test_clip_el_arma_enfundada_no_toca_el_puente_y_los_labios_la_frenan_al_levantarla():
    lz = build_mount(_spec(tipo="clip"), _sword(), MIN_WALL, normal=(1, 0, 0))
    bridge = mount_solid(lz)
    assert _vol(lz.weapon, bridge) < 1e-3                       # enfundada: sin contacto
    assert _vol(_lift(lz, 0.8), bridge) > 1e-3                  # al levantarla pasa por los labios (encaje a presión)
    assert _vol(_lift(lz, lz.info["levantar_mm"] + 0.1), bridge) < 1e-3     # librado el techo, queda suelta


def test_clip_se_une_a_la_pieza_en_un_solo_cuerpo_y_tiene_ranura_abierta():
    lz = build_mount(_spec(tipo="clip"), _sword(), MIN_WALL, normal=(1, 0, 0))
    out = attach_lazo(_flank(), lz)
    assert len(out.decompose()) == 1
    # sonda en el medio del techo (sobre el eje de la hoja): el clip tiene la ranura abierta, el lazo tiene techo
    x_roof = float(mount_solid(lz).bounding_box()[3]) - MIN_WALL / 2
    probe = S.Manifold.cube((0.1, 0.1, 0.1)).translate((x_roof - 0.05, -0.05, 39.95))
    assert _vol(probe, out) < 1e-9
    lazo = build_mount(_spec(), _sword(), MIN_WALL, normal=(1, 0, 0))
    assert _vol(probe, attach_lazo(_flank(), lazo)) > 1e-4


def test_clip_marca_sus_labios_y_el_lazo_y_la_cuna_no():
    clip = build_mount(_spec(tipo="clip"), _sword(), MIN_WALL, normal=(1, 0, 0))
    assert clip.lips is not None and 0 < clip.lips.volume() < mount_solid(clip).volume()
    assert _vol(clip.lips, mount_solid(clip)) == pytest.approx(float(clip.lips.volume()), rel=1e-3)     # son parte del puente
    der_spec = _spec(tipo="clip", lado="der", ancla=(-10.0, 0.0, 40.0))
    der = mirrored_for_side(build_mount(der_spec, _sword(), MIN_WALL, normal=(1, 0, 0)), der_spec)
    assert float(der.lips.bounding_box()[0]) < 0 and _vol(der.lips, mount_solid(der)) == pytest.approx(float(der.lips.volume()), rel=1e-3)
    assert build_mount(_spec(), _sword(), MIN_WALL, normal=(1, 0, 0)).lips is None
    assert build_mount(_spec(tipo="cuna"), _sword(), MIN_WALL, normal=(1, 0, 0)).lips is None


def test_clip_sin_resalte_avisa_que_no_retiene_a_presion():
    lz = build_mount(_spec(tipo="clip", holgura=0.5), _sword(), MIN_WALL, normal=(1, 0, 0))
    assert lz.info["interferencia_mm"] == 0.0
    assert any("no retiene a presión" in a for a in lz.info["avisos"])
    assert lz.info["retiene"]                                   # igual la sostiene la guarda apoyada


def test_clip_hoja_muy_angosta_falla():
    blade = S.Manifold.cube((30.0, 1.0, 0.8)).translate((-30.0, -0.5, -0.4))
    with pytest.raises(MountError, match="angosta"):
        build_mount(_spec(tipo="clip"), blade, MIN_WALL, normal=(1, 0, 0))


# ---------------------------------------------------------------- cuna
def test_cuna_no_tiene_techo_y_es_mas_baja_que_el_lazo():
    lazo = build_mount(_spec(), _sword(), MIN_WALL, normal=(1, 0, 0))
    cuna = build_mount(_spec(tipo="cuna"), _sword(), MIN_WALL, normal=(1, 0, 0))
    assert cuna.kind == "cuna" and cuna.info["tipo"] == "cuna"
    top = lambda lz: float(mount_solid(lz).bounding_box()[3])            # noqa: E731  (en +X: hacia afuera del flanco)
    assert top(lazo) - top(cuna) == pytest.approx(MIN_WALL, abs=1e-3)    # falta justo el techo
    assert cuna.info["alto_sobre_superficie_mm"] == pytest.approx(cuna.info["abertura_mm"][1], abs=1e-3)
    assert cuna.info["notas"] and not cuna.info["avisos"]                # la nota informa; no degrada el nivel


def test_cuna_el_arma_apoya_enfundada_y_sale_levantandola():
    lz = build_mount(_spec(tipo="cuna"), _sword(), MIN_WALL, normal=(1, 0, 0))
    bridge = mount_solid(lz)
    assert _vol(lz.weapon, bridge) < 1e-3
    assert _vol(_lift(lz, 0.5), bridge) < 1e-3                  # sin labios: nada la frena al levantarla
    assert lz.info["levantar_mm"] == pytest.approx(0.8 + 0.25, abs=1e-3)  # espesor de la hoja + holgura


def test_cuna_con_arma_sin_guarda_avisa():
    blade = S.Manifold.cube((30.0, 4.0, 0.8)).translate((-30.0, -2.0, -0.4))
    lz = build_mount(_spec(tipo="cuna"), blade, MIN_WALL, normal=(1, 0, 0), station=5.0)
    assert any("no tiene guarda" in a for a in lz.info["avisos"])
    assert not lz.info["retiene"]


# ---------------------------------------------------------------- extracción, espejo, integración
@pytest.mark.parametrize("tipo", ["clip", "cuna"])
def test_extraccion_levanta_a_lo_largo_de_la_normal_y_detecta_obstaculos(tipo):
    lz = build_mount(_spec(tipo=tipo), _sword(), MIN_WALL, normal=(1, 0, 0))
    e, dist, mode = extraction_vector(lz)
    assert mode == "levantar" and np.allclose(e, (1, 0, 0), atol=1e-6)
    assert dist == pytest.approx(lz.info["levantar_mm"] + EXTRACT_MARGIN, abs=1e-3)
    libre = extraction_scan(lz, {"flanco": _flank()})
    assert libre["modo"] == "levantar" and libre["choques"] == {}
    brazo = S.Manifold.cube((5.0, 30.0, 30.0)).translate((11.5, -15.0, 25.0))     # pared a 1,5 mm del flanco: el arma levantada choca
    assert "brazo" in extraction_scan(lz, {"flanco": _flank(), "brazo": brazo})["choques"]


def test_el_lazo_sigue_deslizandose_por_su_eje():
    lz = build_mount(_spec(), _sword(), MIN_WALL, normal=(1, 0, 0))
    assert lz.kind == "lazo"
    assert extraction_scan(lz, {"flanco": _flank()})["modo"] == "deslizar"
    assert "levantar_mm" not in lz.info and "ranura_mm" not in lz.info


def test_build_lazo_sigue_aceptando_solo_lazo():
    with pytest.raises(MountError, match="lazo"):
        build_lazo(_spec(tipo="clip"), _sword(), MIN_WALL)


@pytest.mark.parametrize("tipo", ["clip", "cuna"])
def test_lado_der_espeja_el_tipo_abierto_y_levanta_hacia_afuera(tipo):
    izq = build_mount(_spec(tipo=tipo), _sword(), MIN_WALL, normal=(1, 0, 0))
    der_spec = _spec(tipo=tipo, lado="der", ancla=(-10.0, 0.0, 40.0))
    der = mirrored_for_side(build_mount(der_spec, _sword(), MIN_WALL, normal=(1, 0, 0)), der_spec)
    assert der.kind == tipo
    e, _d, _m = extraction_vector(der)
    assert np.allclose(e, (-1, 0, 0), atol=1e-6)                # el flanco derecho mira a −X
    assert _vol(mount_solid(der), S.mirror_x(mount_solid(izq))) == pytest.approx(float(mount_solid(izq).volume()), rel=1e-3)


@pytest.mark.parametrize("tipo", ["clip", "cuna"])
def test_apply_mounts_acepta_los_tipos_abiertos(tipo):
    pieces = {"torso": _flank()}
    out, built = apply_mounts(pieces, [(_spec(tipo=tipo), _sword())], MIN_WALL)
    (name, mb), = built.items()
    assert mb.lazo.kind == tipo and mb.info["tipo"] == tipo and mb.info["host"] == "torso"
    assert out["torso"].volume() > pieces["torso"].volume() - 1e-6 and len(out["torso"].decompose()) == 1


# ---------------------------------------------------------------- figura demo (pipeline._validate_mount)
@pytest.fixture(scope="module")
def demo():
    import dataclasses

    from afm.project import demo_project
    from afm.testfigure import make_doll

    proj = demo_project()
    return proj, dataclasses.replace(proj.params, decimate=0), S.to_manifold(make_doll())


def _demo_check(demo, **over):
    from afm import pipeline as PL

    proj, p, body = demo
    d = dict(arma="espada@1", lugar="pelvis", ancla=(23.0, 0.0, 38.0), direccion=(0.0, 0.0, -1.0))
    d.update(over)
    res = PL.build(body, proj.limbs, p, proj.symmetry, mounts=[(MountSpec(**d), _sword())])
    out = {"problems": [], "avisos": []}
    return out, PL._validate_mount(res, p, out, proj.limbs)


@pytest.mark.parametrize("tipo", ["clip", "cuna"])
def test_figura_demo_con_tipo_abierto_se_valida_levantando_el_arma(demo, tipo):
    out, info = _demo_check(demo, tipo=tipo)
    (_name, rec), = info.items()
    assert rec["extraccion"]["modo"] == "levantar"
    assert rec["extraccion"]["direccion"][0] > 0.9                      # hacia afuera del flanco izquierdo (+X)
    assert rec["extraccion"]["distancia_mm"] < 4.0                      # se levanta unos mm, no se desliza 34 mm
    assert rec["mano_libre"] is True and rec["componentes_huesped"] == 1
    assert not any("el arma no" in t for t in out["problems"])


def test_tipo_abierto_con_un_brazo_a_pocos_mm_no_se_puede_levantar(demo):
    from afm import pipeline as PL

    proj, p, body = demo
    spec = MountSpec(arma="espada@1", lugar="pelvis", ancla=(23.0, 0.0, 38.0), direccion=(0.0, 0.0, -1.0), tipo="cuna")
    res = PL.build(body, proj.limbs, p, proj.symmetry, mounts=[(spec, _sword())])
    lz = next(iter(res.mounts.values())).lazo
    e, dist, _m = extraction_vector(lz)
    top = lz.weapon.bounding_box()
    obstacle = S.Manifold.cube((3.0, 40.0, 40.0)).translate((float(top[3]) + 0.3 * float(e[0]), -20.0, 20.0))
    scan = extraction_scan(lz, {"obstaculo": obstacle})
    assert "obstaculo" in scan["choques"] and scan["modo"] == "levantar"
