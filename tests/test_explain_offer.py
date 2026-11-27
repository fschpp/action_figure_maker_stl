"""`afm catalogo explicar` también dice qué tipos de articulación admite la cara de cada puerto (plan de plugins §3.5, Fase 2)."""
import os
import shutil

import pytest

from afm import catalog_ops as OPS
from afm import joint_types as JT
from afm.catalog import Catalog
from afm.cli import main
from afm.joint_types.mushroom import Mushroom

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


@pytest.fixture()
def cat_dir(tmp_path):
    dst = str(tmp_path / "linea")
    shutil.copytree(EJEMPLO, dst, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    return dst


def test_the_arm_lists_the_types_for_its_shoulder_plug_without_depth(cat_dir):
    cat = Catalog.open(cat_dir)
    lines = OPS.explain_lines(cat, "brazo-muneco-01@1")
    text = "\n".join(lines)
    assert lines[0].startswith("brazo-muneco-01@1") and "tipos de articulación que admite este corte:" in text
    assert "hombro/enchufe" in text and "mushroom" in text and "Hongo" in text and "detent" in text
    assert "cuerpo detrás de la cara" not in text.split("(hombro/enchufe)")[1].split("\n")[1]   # un enchufe no trae capacidad del anfitrión


def test_a_socket_with_stored_capacity_shows_the_depth(cat_dir):
    cat = Catalog.open(cat_dir)
    cat.measure_capacity("torso-muneco-01", write=True)
    text = "\n".join(OPS.explain_lines(cat, "torso-muneco-01@1"))
    assert "(hombro/toma)" in text and "cuerpo detrás de la cara:" in text and "profundidad" in text


def test_ports_of_classes_without_types_print_nothing(cat_dir):
    cat = Catalog.open(cat_dir)
    text = "\n".join(OPS.port_offer_lines(cat.meta("cabeza-muneco-01@1"), cat.params))
    assert text == ""                                      # el cuello es hongo fijo: no hay nada que elegir


def test_the_offer_follows_the_registry_and_comes_before_the_pairs(cat_dir):
    class Extra(Mushroom):
        id = "extra_t"
        labels = {"es": "Extra", "en": "Extra", "pt": "Extra"}

    JT.REGISTRY["extra_t"] = Extra()
    try:
        out = OPS.explain_lines(Catalog.open(cat_dir), "brazo-muneco-01@1")
    finally:
        del JT.REGISTRY["extra_t"]
    text = "\n".join(out)
    assert "extra_t" in text
    first_pair = next(i for i, x in enumerate(out) if x.startswith("torso-muneco-01@1 ("))
    assert max(i for i, x in enumerate(out) if "tipos de articulación" in x) < first_pair


def test_cli_explicar_prints_the_offer(cat_dir, capsys):
    assert main(["catalogo", "explicar", cat_dir, "brazo-muneco-01@1"]) == 0
    out = capsys.readouterr().out
    assert "tipos de articulación que admite este corte:" in out and "IoU" in out
