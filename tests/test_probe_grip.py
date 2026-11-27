"""Probeta de agarre mano / mango: manos de prueba (taladro) × mangos (dentado) y elección del par."""
import json
import os

import numpy as np
import pytest

from afm import cli, probes, profile, solids as S
from afm.props import GripSpec


def test_cells_defaults_and_validation():
    hands, handles = probes.grip_probe_cells()
    assert [h.bore_d for h in hands] == [2.2, 2.4, 2.6]
    assert [m.spec.barb_d for m in handles] == [2.7, 2.9, 3.1]
    assert all(m.spec.bore_d == 2.4 for m in handles)          # referencia = el taladro del medio
    with pytest.raises(ValueError, match="hasta 6"):
        probes.grip_probe_cells(bores=[2 + 0.1 * i for i in range(7)])
    with pytest.raises(ValueError, match="repetirse"):
        probes.grip_probe_cells(barbs=[2.9, 2.9])
    with pytest.raises(ValueError):
        probes.grip_probe_cells(bores=[0.0])


def test_pairs_use_catalog_compat():
    hands, handles = probes.grip_probe_cells()
    pairs = probes.grip_pairs(hands, handles, probes.GRIP_PROBE_HOLE_LEN)
    assert len(pairs) == 9 and [r["n"] for r in pairs] == list(range(1, 10))
    p = next(r for r in pairs if r["mano"] == 2 and r["mango"] == 3)
    assert p["interferencia"] == pytest.approx(0.35) and p["holgura_radial"] == pytest.approx(0.2)
    assert p["nivel"] == "ok"
    # dentado que no supera el taladro de esa mano: no retiene
    bad = probes.grip_pairs([probes.GripProbeHand(1, 3.0)], handles[:1], 8.0)[0]
    assert bad["nivel"] == "no" and any("no retiene" in m for m in bad["motivos"])


def test_build_separate_valid_parts_without_overlap():
    hands, handles = probes.grip_probe_cells(bores=[2.4], barbs=[2.9, 3.1])
    parts = probes.build_grip_probe(hands, handles)
    assert [n for n, _ in parts] == ["mano 1 taladro 2.4", "mango 1 dentado 2.9", "mango 2 dentado 3.1"]
    boxes = []
    for _n, m in parts:
        assert m.status() == S.m3.Error.NoError and m.volume() > 0 and len(m.decompose()) == 1
        boxes.append(S.to_trimesh(m).bounds)
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            assert not (np.all(boxes[i][0] < boxes[j][1]) and np.all(boxes[j][0] < boxes[i][1]))
    # el taladro de la mano es pasante y del Ø pedido
    hole = S.intersection(S.Manifold.cylinder(20.0, 1.19, 1.19, 48).translate((0, 0, -5)), parts[0][1])
    assert hole.is_empty() or hole.volume() < 1e-6


def test_probe_files_and_manifest():
    files, text = probes.probe_files("agarre", taladros=[2.4, 2.6], dentados=[2.9])
    assert sorted(files) == ["probetas.3mf", "probetas.json", "probetas.stl", "probetas.txt"]
    m = json.loads(files["probetas.json"])
    assert m["formato"] == "afm-probetas-1" and m["tipo"] == "agarre" and len(m["celdas"]) == 2
    assert [c["n"] for c in m["manos"]] == [1, 2] and m["mangos"] == [{"n": 1, "dentado": 2.9}]
    assert m["vastago"] == pytest.approx(2.2)                  # vástago = taladro de referencia (2,6) − 0,4
    import io
    import zipfile
    model = zipfile.ZipFile(io.BytesIO(files["probetas.3mf"])).read("3D/3dmodel.model").decode()
    assert "par 2 (mano 2 + mango 1)" in text and 'name="mano 1 taladro 2.4"' in model and 'name="mango 1 dentado 2.9"' in model


def test_short_channel_is_a_clear_error():
    with pytest.raises(ValueError, match="más largas"):
        probes.probe_files("agarre", largo_canal=4.0)


def test_pair_choice_and_grip_spec():
    files, _ = probes.probe_files("agarre")
    man = json.loads(files["probetas.json"])
    cell = profile.probe_pair(man, 3, 2)
    assert (cell["taladro"], cell["dentado"]) == (2.6, 2.9)
    spec = profile.grip_from_cell(man, cell, GripSpec())
    assert (spec.bore_d, spec.barb_d, spec.shaft_d) == (2.6, 2.9, cell["vastago"])
    assert "--taladro 2.6" in profile.grip_command(cell) and "--dentados 2.9" in profile.grip_command(cell)
    assert "mano 3" in profile.probe_cell_summary(man, cell)
    with pytest.raises(ValueError, match="no tiene el par"):
        profile.probe_pair(man, 9, 1)
    with pytest.raises(ValueError, match="no se guarda en el perfil"):
        profile.apply_probe_cell(__import__("afm.params", fromlist=["Params"]).Params(), man, 1)
    with pytest.raises(ValueError):
        profile.probe_pair({"tipo": "hongo", "celdas": []}, 1, 1)


def test_cli_probe_and_result(tmp_path, capsys):
    assert cli.main(["probe", "--tipo", "agarre", "--out", str(tmp_path), "--taladros", "2.4,2.6", "--dentados", "2.9,3.1"]) == 0
    assert os.path.exists(tmp_path / "probetas.json")
    capsys.readouterr()
    assert cli.main(["probe-resultado", str(tmp_path), "--mano", "2", "--mango", "1"]) == 0
    out = capsys.readouterr().out
    assert "afm agarre MANO.stl --taladro 2.6" in out and "--dentados 2.9" in out
    assert cli.main(["probe-resultado", str(tmp_path)]) == 2                      # falta el par
    assert cli.main(["probe-resultado", str(tmp_path), "--mano", "7", "--mango", "1"]) == 2
    assert cli.main(["probe", "--tipo", "agarre", "--out", str(tmp_path), "--dentados", "2.0"]) == 2


def test_cli_reads_drilled_hand(tmp_path):
    stl = tmp_path / "m.stl"
    stl.write_bytes(b"")
    (tmp_path / "m.mano.json").write_text(json.dumps({"agarre": {"diametro_taladro_mm": 2.5, "largo_canal_mm": 9.0}}))
    out = tmp_path / "o"
    out.mkdir()
    assert cli.main(["probe", "--tipo", "agarre", "--out", str(out), "--mano", str(stl)]) == 0
    m = json.loads((out / "probetas.json").read_text())
    assert [h["taladro"] for h in m["manos"]] == [2.5] and m["largo_canal"] == 9.0
    assert cli.main(["probe", "--tipo", "agarre", "--out", str(out), "--mano", str(tmp_path / "nada.stl")]) == 2
