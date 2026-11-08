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
