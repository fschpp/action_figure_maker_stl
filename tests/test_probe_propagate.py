"""Llevar la celda elegida de las probetas a otros lados: perfil, línea del catálogo y valores por defecto de la aplicación."""
import json
import os

import pytest

from afm import profile
from afm.catalog import Catalog
from afm.cli import main
from afm.gui.controller import Controller
from afm.params import DetentSpec, Params

HONGO = {"formato": "afm-probetas-1", "tipo": "hongo", "pasador": "hombro",
         "celdas": [{"n": 1, "holgura": 0.3, "vastago": 3.9}, {"n": 2, "holgura": 0.4, "vastago": 4.3}]}
BISAGRA = {"formato": "afm-probetas-1", "tipo": "bisagra",
           "celdas": [{"n": 1, "holgura": 0.35, "holgura_axial": 0.3, "bulon": 2.6}]}
CLIC = {"formato": "afm-probetas-1", "tipo": "clic",
        "celdas": [{"n": 4, "archivo": "a.stl", "detent": {"notch_depth": 1.0, "finger_t": 1.4, "detent_gap": 0.2}}]}


def _catalog(tmp_path, name="linea"):
    return Catalog.create(str(tmp_path / name), name, 100.0, 0.4)


# --- qué valores fija cada tipo de tirada --------------------------------------------------------------------
def test_probe_cell_changes_per_type():
    assert profile.probe_cell_changes(HONGO, 2) == {"clearance": 0.4, "shoulder_pin.shaft_d": 4.3}
    assert profile.probe_cell_changes({**HONGO, "pasador": "cadera"}, 1) == {"clearance": 0.3, "hip_pin.shaft_d": 3.9}
    assert profile.probe_cell_changes(BISAGRA, 1) == {"clearance": 0.35, "axial_clearance": 0.3, "hinge_pin.pin_d": 2.6}
    ch = profile.probe_cell_changes(CLIC, 4, "a.stl")
    assert ch["detent"]["notch_depth"] == 1.0 and ch["detent"]["n_notches"] == DetentSpec().n_notches
    with pytest.raises(ValueError):
        profile.probe_cell_changes({**HONGO, "tipo": "montura"}, 1)


def test_apply_changes_is_partial_and_does_not_mutate():
    base = Params()
    q = profile.apply_changes(base, {"hip_pin.shaft_d": 4.5})
    assert q.hip_pin.shaft_d == 4.5 and q.hip_pin.head_d == base.hip_pin.head_d and base.hip_pin.shaft_d == 5.0
    with pytest.raises(KeyError):
        profile.apply_changes(base, {"hip_pin.no_existe": 1})


def test_apply_probe_cell_matches_changes():
    q, resumen = profile.apply_probe_cell(Params(), HONGO, 2)
    assert q.clearance == 0.4 and q.shoulder_pin.shaft_d == 4.3 and "4.3" in resumen
    q, _ = profile.apply_probe_cell(Params(), CLIC, 4, "a.stl")
    assert isinstance(q.detent, DetentSpec) and q.detent.finger_t == 1.4


# --- perfil de línea -----------------------------------------------------------------------------------------
def test_update_profile_keeps_what_the_profile_already_had(tmp_path):
    d = str(tmp_path)
    profile.save_profile("mia", Params(min_wall=1.6, clearance=0.9), d)
    path, existed = profile.update_profile("mia", {"clearance": 0.4}, Params(), d)
    assert existed and os.path.isfile(path)
    q = profile.load_profile("mia", d)
    assert q.clearance == 0.4 and q.min_wall == 1.6            # solo cambió la holgura
    path, existed = profile.update_profile("nueva", {"clearance": 0.4}, Params(min_wall=2.0, clearance=0.4), d)
    assert not existed and profile.load_profile("nueva", d).min_wall == 2.0


# --- valores por defecto de la aplicación ---------------------------------------------------------------------
def test_app_defaults_roundtrip_and_clear():
    assert profile.app_defaults() == Params() and profile.app_defaults_changes() == {}
    path = profile.save_app_defaults({"clearance": 0.4, "hip_pin.shaft_d": 4.6})
    assert os.path.isfile(path)
    p = profile.app_defaults()
    assert p.clearance == 0.4 and p.hip_pin.shaft_d == 4.6 and p.shoulder_pin == Params().shoulder_pin
    profile.save_app_defaults({"axial_clearance": 0.3})                     # suma: lo anterior se conserva
    assert profile.app_defaults().clearance == 0.4 and profile.app_defaults().axial_clearance == 0.3
    assert profile.clear_app_defaults() is True and profile.app_defaults() == Params()
    assert profile.clear_app_defaults() is False


def test_app_defaults_never_fail_on_a_bad_file(tmp_path):
    with open(profile.app_defaults_path(), "w") as fh:
        fh.write("{no es json")
    assert profile.app_defaults() == Params()
    with open(profile.app_defaults_path(), "w") as fh:
        json.dump({"formato": profile.DEFAULTS_FORMAT, "cambios": {"ya_no_existe": 1}}, fh)
    assert profile.app_defaults() == Params()                                # otra versión: vuelve a fábrica
    with pytest.raises(KeyError):
        profile.save_app_defaults({"ya_no_existe": 1})                       # y no escribe basura


def test_new_figures_and_catalogs_start_with_the_own_defaults():
    profile.save_app_defaults({"clearance": 0.37})
    assert Controller().get_param("clearance") == 0.37
    assert profile.default_for_nozzle(0.4).clearance == 0.37
    assert profile.default_for_nozzle(0.8).clearance == 0.70                 # otra boquilla: la tabla de siempre


# --- línea del catálogo ----------------------------------------------------------------------------------------
def test_catalog_update_params_touches_only_the_named_values(tmp_path):
    cat = _catalog(tmp_path)
    before = cat.params
    warns = cat.update_params({"clearance": 0.4, "ref_height": 999.0, "shoulder_pin.shaft_d": 4.3})
    assert warns == []
    after = Catalog.open(cat.root).params
    assert after.clearance == 0.4 and after.shoulder_pin.shaft_d == 4.3
    assert after.ref_height == before.ref_height and after.hip_pin == before.hip_pin and after.min_wall == before.min_wall
    assert "holgura **0.4 mm**" in open(os.path.join(cat.root, "CATALOGO.md"), encoding="utf-8").read()
    with pytest.raises(KeyError):
        cat.update_params({"no.existe": 1})


# --- controlador ----------------------------------------------------------------------------------------------
def _use_cell(tmp_path, c=None):
    c = c or Controller()
    man = tmp_path / "probetas.json"
    man.write_text(json.dumps(HONGO), encoding="utf-8")
    assert c.load_probe_manifest(str(man)) is True
    assert c.apply_probe_choice(1) is True
    return c


def test_using_a_cell_remembers_what_it_set(tmp_path):
    c = _use_cell(tmp_path)
    assert c.probe_applied["n"] == 2
    assert c.probe_applied["cambios"] == {"clearance": 0.4, "shoulder_pin.shaft_d": 4.3}


def test_propagate_to_every_destination(tmp_path):
    c = _use_cell(tmp_path)
    cat = _catalog(tmp_path)
    lines = c.propagate_probe(profile_name="mi linea", catalog_roots=[cat.root], as_default=True, profile_dir=str(tmp_path / "perf"))
    assert not any(x.startswith("✗") for x in lines), lines
    q = profile.load_profile("mi linea", str(tmp_path / "perf"))
    assert q.clearance == 0.4 and q.shoulder_pin.shaft_d == 4.3
    assert Catalog.open(cat.root).params.shoulder_pin.shaft_d == 4.3
    assert profile.app_defaults().shoulder_pin.shaft_d == 4.3
    assert c.param_default("shoulder_pin.shaft_d") == 4.3 and not c.param_modified("shoulder_pin.shaft_d")


def test_one_failing_destination_does_not_block_the_others(tmp_path):
    c = _use_cell(tmp_path)
    cat = _catalog(tmp_path)
    lines = c.propagate_probe(catalog_roots=[str(tmp_path / "no_hay_catalogo"), cat.root], as_default=True)
    assert lines[0].startswith("✗")
    assert Catalog.open(cat.root).params.clearance == 0.4 and profile.app_defaults().clearance == 0.4


def test_propagate_without_a_chosen_cell_or_destination(tmp_path):
    c = Controller()
    assert "Usar esta celda" in c.propagate_probe(as_default=True)[0]
    assert profile.app_defaults_changes() == {}
    c = _use_cell(tmp_path)
    assert c.propagate_probe() == [c.message] and profile.app_defaults_changes() == {}


def test_known_catalogs_lists_the_open_one_first(tmp_path):
    c = Controller()
    a, b = _catalog(tmp_path, "a"), _catalog(tmp_path, "b")
    from afm.gui import settings

    settings.remember_catalog("a", a.root)
    settings.remember_catalog("b", b.root)
    c.open_catalog(b.root)
    assert [n for n, _r in c.known_catalogs()] == ["b", "a"]


def test_clear_app_defaults_from_the_controller():
    c = Controller()
    profile.save_app_defaults({"clearance": 0.37})
    assert c.param_default("clearance") == 0.37
    assert c.clear_app_defaults() is True and c.param_default("clearance") == 0.5
    assert c.clear_app_defaults() is False


# --- consola ---------------------------------------------------------------------------------------------------
def test_cli_probe_resultado_to_catalog_and_default(tmp_path, capsys):
    man = tmp_path / "probetas.json"
    man.write_text(json.dumps(HONGO), encoding="utf-8")
    cat = _catalog(tmp_path)
    assert main(["probe-resultado", str(man), "2", "--catalogo", cat.root, "--defecto"]) == 0
    out = capsys.readouterr().out
    assert "actualizada" in out and "por defecto" in out
    assert Catalog.open(cat.root).params.shoulder_pin.shaft_d == 4.3 and profile.app_defaults().clearance == 0.4


def test_cli_probe_resultado_needs_a_destination_and_reports_errors(tmp_path, capsys):
    man = tmp_path / "probetas.json"
    man.write_text(json.dumps(HONGO), encoding="utf-8")
    assert main(["probe-resultado", str(man), "2"]) == 2
    assert "--perfil" in capsys.readouterr().err
    assert main(["probe-resultado", str(man), "2", "--catalogo", str(tmp_path / "nada"), "--defecto"]) == 2
    assert profile.app_defaults().clearance == 0.4               # el destino que sí andaba se hizo igual
