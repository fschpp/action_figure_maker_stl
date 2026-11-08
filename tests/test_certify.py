"""Certificación de piezas (plan de compatibilidad, Fase 6)."""
import os
import shutil

import pytest

from afm import certify, compat
from afm.catalog import Catalog
from afm.cli import main

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


@pytest.fixture()
def cat_dir(tmp_path):
    dst = str(tmp_path / "linea")
    shutil.copytree(EJEMPLO, dst, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    return dst


def test_panel_of_the_example_is_its_own_torso(cat_dir):
    cat = Catalog.open(cat_dir)
    panel = certify.host_panel(cat, "brazo-muneco-01")
    assert [h.id for h in panel] == ["torso-muneco-01"]
    h, r, key = certify.panel_keys(cat, "brazo-muneco-01")[0]
    assert r.lugares["brazo"].startswith("brazo-muneco-01@") and r.lugares["torso"] == h.ref and len(key) == 64
    with pytest.raises(certify.CertifyError):
        certify.host_panel(cat, "torso-muneco-01")


def test_panel_adds_low_median_high_capacity_hosts(cat_dir):
    import dataclasses
    from afm import parts as P
    cat = Catalog.open(cat_dir)
    meta, mesh = cat.load("torso-muneco-01")
    for i, depth in enumerate((15.0, 20.0, 60.0)):
        m = P.PieceMeta.from_dict(meta.to_dict())
        m.id, m.version, m.sha256 = f"torso-x{i}", 1, ""
        m.origen = {**(m.origen or {}), "figura": f"otra{i}.stl"}
        for p in m.puertos.values():
            if p.rol == "toma" and p.clase in ("hombro", "cadera"):
                p.capacity = {"profundidad_mm": depth, "huella_radio_mm": 3.0}
        cat.add_piece(mesh, m)
    cat = Catalog.open(cat_dir)
    ids = [h.id for h in certify.host_panel(cat, "brazo-muneco-01")]
    assert ids[0] == "torso-muneco-01" and len(ids) <= certify.MAX_HOSTS
    assert {"torso-x0", "torso-x2"} <= set(ids)                      # los extremos de capacidad entran (15 y 60 mm)


def test_status_flow_certify_then_expires(cat_dir):
    cat = Catalog.open(cat_dir)
    assert certify.status(cat, "brazo-muneco-01")["estado"] == "sin certificar"
    entry = certify.certify(cat, "brazo-muneco-01")
    assert entry["n"] == 1 and "clave" in entry and entry["anfitriones"][0]["torso"].startswith("torso-muneco-01")
    st = certify.status(cat, "brazo-muneco-01")
    assert st["estado"] in ("certificada", "parcial") and st["texto"].startswith("certificada 1/1" if entry["ok"] else "certificada 0/1")
    # cualquier cambio de la clave (aquí, otra pieza del panel con otro hash) vence la certificación
    saved = compat.CompatCache(cat.root).get_cert("brazo-muneco-01")
    assert saved["clave"] == entry["clave"]
    meta, mesh = cat.load("torso-muneco-01")
    mesh.apply_scale(1.01)
    meta.sha256 = ""
    cat.add_piece(mesh, meta, new_version=True)
    assert certify.status(Catalog.open(cat_dir), "brazo-muneco-01")["estado"] == "vencida"


def test_cli_certificar_and_listar_badge(cat_dir, capsys):
    assert main(["catalogo", "certificar", cat_dir, "--estado"]) == 0
    assert "sin certificar" in capsys.readouterr().out
    rc = main(["catalogo", "certificar", cat_dir, "brazo-muneco-01"])
    out = capsys.readouterr().out
    assert "brazo-muneco-01" in out and ("certificada 1/1" in out) == (rc == 0)
    assert main(["catalogo", "listar", cat_dir]) == 0
    assert "certificada" in capsys.readouterr().out


# ---------------------------------------------------------------- pelvis (torsos con cintura)
@pytest.fixture(scope="module")
def waist_cat(tmp_path_factory):
    """Catálogo de dos figuras con cintura: el muñeco y el muñeco más ancho (otra `origen.figura`)."""
    import copy
    from afm import solids as S
    from afm.extract import extract_all
    from afm.planes import Plane
    from afm.project import demo_project
    from afm.testfigure import make_doll
    from afm.waist import WaistSpec

    root = str(tmp_path_factory.mktemp("cintura") / "linea")
    cat = Catalog.create(root, "prueba", 100.0)
    for tag, sx in (("muneco", 1.0), ("ancho", 1.12)):
        doll = make_doll()
        doll.apply_scale([sx, 1.0, 1.0])
        pr = demo_project()
        pr.waist = WaistSpec(Plane.from_points((-5, -5, 55), (5, -5, 55), (0, 5, 55), "cintura"))
        for e in extract_all(S.to_manifold(doll), copy.deepcopy(pr), tag, figure=f"{tag}.stl", project_name=tag).pieces:
            cat.add_piece(e.mesh, e.meta)
    return Catalog.open(root)


def test_pelvis_is_certifiable_and_its_panel_are_torsos_with_waist(waist_cat):
    cat = waist_cat
    pelvis = next(m for m in cat.list("pelvis") if m.id == "pelvis-muneco-01")
    assert certify.CLASS_OF_TIPO["pelvis"] == "cintura"
    panel = certify.host_panel(cat, pelvis.ref)
    assert panel and panel[0].id == "torso-sup-muneco-01" and all(certify.has_waist(h) for h in panel)
    assert len(panel) <= certify.MAX_HOSTS


def test_recipe_on_a_waist_torso_carries_the_pelvis_and_the_waist_option(waist_cat):
    cat = waist_cat
    host = next(h for h in cat.list("torso_sup") if h.id == "torso-sup-muneco-01")
    r = certify.recipe_for(cat, "pelvis-ancho-01", host)
    assert r.lugares["pelvis"].startswith("pelvis-ancho-01") and r.options()["cintura"] is True and r.problems() == []
    arm = certify.recipe_for(cat, "brazo-ancho-01", host)                  # un brazo sobre ese torso: la pelvis es la de su figura
    assert arm.lugares["pelvis"].startswith("pelvis-muneco-01") and arm.options()["cintura"] is True
    assert arm.problems() == []


def test_certify_a_pelvis_arms_it_on_every_host(waist_cat):
    cat = waist_cat
    entry = certify.certify(cat, "pelvis-muneco-01")
    assert entry["n"] == len(certify.host_panel(cat, "pelvis-muneco-01")) >= 1
    assert entry["anfitriones"][0]["torso"].startswith("torso-sup-muneco-01") and entry["anfitriones"][0]["ok"]
    assert certify.status(cat, "pelvis-muneco-01")["estado"] in ("certificada", "parcial")


def test_leg_panel_hosts_are_pelvises_when_the_catalog_has_a_waist(waist_cat):
    cat = waist_cat
    panel = certify.host_panel(cat, "pierna-muneco-01")
    assert panel and panel[0].tipo == "pelvis" and panel[0].id == "pelvis-muneco-01"
    r = certify.recipe_for(cat, "pierna-ancho-01", panel[0])
    assert r.lugares["pelvis"] == panel[0].ref and r.lugares["pierna"].startswith("pierna-ancho-01")
    assert r.lugares["torso"].startswith("torso-sup-muneco-01") and r.options()["cintura"] is True and r.problems() == []


def test_certify_a_leg_arms_it_on_the_pelvis(waist_cat):
    entry = certify.certify(waist_cat, "pierna-muneco-01")
    assert entry["n"] >= 1 and entry["anfitriones"][0]["torso"].startswith("pelvis-muneco-01")
    assert entry["anfitriones"][0]["ok"], entry["anfitriones"][0]["problemas"]


def test_a_hand_is_certified_on_the_arms_of_the_catalog(cat_dir):
    """La mano se acopla a un brazo (toma de muñeca), no a un torso: el panel son brazos y la receta lleva el torso de su figura."""
    cat = Catalog.open(cat_dir)
    panel = certify.host_panel(cat, "mano-muneco-01")
    assert [h.id for h in panel] == ["brazo-muneco-01"]
    r = certify.recipe_for(cat, "mano-muneco-01", panel[0])
    assert r.lugares["mano"].startswith("mano-muneco-01@") and r.lugares["brazo"] == panel[0].ref
    assert r.lugares["torso"].startswith("torso-muneco-01@") and r.problems() == []
    entry = certify.certify(cat, "mano-muneco-01")
    assert entry["n"] == 1 and entry["anfitriones"][0]["torso"] == panel[0].ref and entry["anfitriones"][0]["ok"], entry
    assert certify.status(cat, "mano-muneco-01")["estado"] == "certificada"
