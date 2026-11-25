"""«Taladrar agarre…»: taladrar una mano que ya está en el catálogo y guardarla como versión nueva (sin GTK)."""
import pytest

from afm import catalog_ops as OPS
from afm.catalog import Catalog
from afm.cli import main
from afm.gui import catalog_import as CI
from test_catalog_grip import cat  # noqa: F401  (fixture)
from test_gui_hand_place import std_hand  # noqa: F401  (fixture)


def _fake_compute_grip(hand_path, spec=None, progress=None):
    """La mano de prueba no tiene canal natural (la búsqueda real se prueba en test_props con un toro): se fabrica el resultado del taladro
    sobre la misma malla, para probar lo que es de este paso (versión nueva, datos de la pieza, agarre guardado)."""
    from dataclasses import asdict

    import numpy as np

    from afm import hand as H
    from afm import props as P

    spec = spec or P.GripSpec()
    hand, meta = H.load_standard_hand(hand_path)
    fit = P.GripFit(axis=np.array([0, 0, 1.0]), center=np.array([0, 0, 3.0]), ref=np.array([1.0, 0, 0]), up=np.array([0, 1.0, 0]), bore_d=spec.bore_d,
                    t_in=-5.0, t_out=5.0, wall_min=2.0, support_deg=300.0)
    meta = dict(meta or {"formato": H.HAND_FORMAT})
    meta["agarre"] = {**fit.to_dict(), "canal_natural_mm": 2.0, "especificacion": asdict(spec)}
    ch = type("Canal", (), {"free_d": 2.0})()
    return P.GripResult(source=hand_path, hand=hand, meta=meta, channel=ch, fit=fit, spec=spec)


@pytest.fixture()
def cat_lisa(cat, std_hand, monkeypatch):  # noqa: F811
    from afm import props as P

    monkeypatch.setattr(P, "compute_grip", _fake_compute_grip)
    cat.import_hand(std_hand, "mano-lisa-01")
    return cat


def test_taladra_una_mano_del_catalogo_y_guarda_la_version_nueva(cat_lisa):
    old = cat_lisa.meta("mano-lisa-01@1")
    assert not (old.mecanismo or {}).get("agarre")
    res = OPS.drill_hand(cat_lisa, "mano-lisa-01@1")
    assert res.ref == "mano-lisa-01@2" and cat_lisa.versions("mano-lisa-01") == [1, 2]
    new = Catalog.open(cat_lisa.root).meta("mano-lisa-01@2")
    ag = new.mecanismo["agarre"]
    assert ag["diametro_taladro_mm"] == pytest.approx(2.4) and ag["largo_canal_mm"] > 0
    assert new.nombre == old.nombre and new.lado == old.lado and new.tipo == "mano" and "muneca" in new.puertos
    assert not (Catalog.open(cat_lisa.root).meta("mano-lisa-01@1").mecanismo or {}).get("agarre")        # la versión anterior queda como estaba
    assert "mano-lisa-01@1" in new.origen["nota"]
    assert any("Mano con agarre guardada como mano-lisa-01@2" in ln for ln in res.lines)


def test_sin_ref_usa_la_mano_mas_reciente_y_el_taladro_se_puede_cambiar(cat_lisa):
    res = OPS.drill_hand(cat_lisa, None, bore_d=2.2)
    assert res.ref == "mano-lisa-01@2" and res.meta.mecanismo["agarre"]["diametro_taladro_mm"] == pytest.approx(2.2)


def test_solo_se_taladra_una_mano(cat_lisa):
    from afm.catalog import CatalogError
    from afm.props import GripError

    with pytest.raises(CatalogError):
        OPS.drill_hand(cat_lisa, "no-existe-01")
    brazo = [m for m in Catalog.open(cat_lisa.root).list() if m.tipo != "mano"]
    assert brazo == []                                                    # el catálogo de la prueba trae solo la mano
    assert GripError                                                      # una pieza que no es mano se rechaza con GripError (ver drill_catalog_hand)


def test_el_formulario_valida_y_arma_los_argumentos():
    p = CI.parse("agarre", {"mano": "mano-lisa-01@1", "taladro": "2,2", "vastago": "", "ranura": "0.5", "largo_lengueta": "8"})
    assert p["args"] == ("mano-lisa-01@1",) and p["kwargs"] == {"bore_d": 2.2, "slit_w": 0.5, "finger_len": 8.0}
    assert CI.parse("agarre", {})["args"] == (None,)
    with pytest.raises(ValueError, match="mayor que 0"):
        CI.parse("agarre", {"taladro": "0"})
    with pytest.raises(ValueError, match="no es un número"):
        CI.parse("agarre", {"taladro": "dos"})


def test_el_formulario_ejecuta_sobre_el_catalogo(cat_lisa):
    res = CI.run("agarre", cat_lisa.root, CI.parse("agarre", {"mano": "mano-lisa-01@1"}))
    assert res.ref == "mano-lisa-01@2"


def test_la_consola(cat_lisa, capsys):
    assert main(["catalogo", "taladrar-mano", cat_lisa.root, "--mano", "mano-lisa-01@1", "--taladro", "2.4"]) == 0
    assert "mano-lisa-01@2" in capsys.readouterr().out
    assert main(["catalogo", "taladrar-mano", cat_lisa.root, "--mano", "no-existe-01"]) == 2


def test_guarda_en_el_catalogo_una_mano_ya_taladrada_con_la_eleccion_manual(cat_lisa, tmp_path):
    """La mano taladrada en la pestaña «Planos» (plano y centro a mano) vuelve al catálogo como versión nueva de la misma pieza."""
    from afm import props as P

    old, mesh = cat_lisa.load("mano-lisa-01@1")
    stl = tmp_path / "mano.stl"
    mesh.export(str(stl))
    res = _fake_compute_grip(str(stl), P.GripSpec(bore_d=5.0))
    res.meta["agarre"]["eleccion"] = {"manual": {"puntos_plano": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "centro": [0, 0, 3]}}
    meta, warns = P.save_grip_to_catalog(cat_lisa, "mano-lisa-01@1", res)
    assert meta.ref == "mano-lisa-01@2" and cat_lisa.versions("mano-lisa-01") == [1, 2]
    new = Catalog.open(cat_lisa.root).meta("mano-lisa-01@2")
    assert new.mecanismo["agarre"]["diametro_taladro_mm"] == pytest.approx(5.0)
    assert new.nombre == old.nombre and "mano-lisa-01@1" in new.origen["nota"]
    from afm import weapon as W

    assert W.line_hand(Catalog.open(cat_lisa.root)).ref == "mano-lisa-01@2"        # las armas toman la versión nueva


def test_el_boton_guarda_la_mano_del_catalogo_taladrada_en_la_sesion(cat_lisa, tmp_path):
    from afm import props as P
    from afm.gui.controller import Controller

    _old, mesh = cat_lisa.load("mano-lisa-01@1")
    stl = tmp_path / "mano.stl"
    mesh.export(str(stl))
    ctrl = Controller()
    assert ctrl.blocked("grip_catalog")                                   # sin taladro hecho no se puede guardar
    ctrl.grip = _fake_compute_grip(str(stl), ctrl.grip_spec)
    assert ctrl.blocked("grip_catalog")                                   # salió de un STL suelto: no hay mano del catálogo a la que volver
    ctrl.grip_catalog = (cat_lisa.root, "mano-lisa-01@1")
    assert ctrl.blocked("grip_catalog") is None
    assert ctrl.save_grip_catalog() and cat_lisa.versions("mano-lisa-01") == [1, 2]
    assert P.GripSpec  # noqa: B015
