"""M09: probeta de la montura (`probes.probe_files(tipo="montura")`, `afm probe --tipo montura`)."""
import io

import pytest
import trimesh

from afm import probes
from afm import solids as S
from afm.cli import main
from afm.mount import CONTACT_TOL_MM3, extraction_scan, place_accessory
from afm.params import Params


@pytest.fixture(scope="module")
def probe():
    cells = probes.mount_probe_cells()
    plate, strip, lazos = probes.build_mount_probe(Params(), cells)
    return cells, plate, strip, lazos


def test_celdas_por_defecto_y_validacion():
    cells = probes.mount_probe_cells()
    assert [c.clearance for c in cells] == [0.15, 0.25, 0.35, 0.45]
    assert [c.label for c in cells] == ["0.15", "0.25", "0.35", "0.45"]
    for bad in ((), (0.0,), (-0.1,), (2.5,)):
        with pytest.raises(ValueError, match="entre 0 y 2"):
            probes.mount_probe_cells(bad)
    with pytest.raises(ValueError, match="hasta 5"):
        probes.mount_probe_cells([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    assert len(probes.mount_probe_cells([0.1, 0.2, 0.3, 0.4, 0.5])) == 5


def test_la_placa_es_una_pieza_estanca_con_una_abertura_por_celda(probe):
    cells, plate, _strip, lazos = probe
    assert len(plate.decompose()) == 1 and plate.status() == S.m3.Error.NoError
    assert [round(lz.hole_w, 3) for lz in lazos] == [4.3, 4.5, 4.7, 4.9]            # hoja 4,0 + 2 × holgura
    assert [round(lz.hole_t, 3) for lz in lazos] == [1.1, 1.3, 1.5, 1.7]            # hoja 0,8 + 2 × holgura
    assert all(lz.info["retiene"] for lz in lazos)                                  # la guarda de 10 × 2 apoya en todos


def test_la_tira_pasa_por_cada_puente_sin_tocar_la_placa(probe):
    _cells, plate, _strip, lazos = probe
    for lz in lazos:
        assert (lz.weapon ^ plate).volume() < CONTACT_TOL_MM3
        assert extraction_scan(lz, {"placa": plate})["choques"] == {}


def test_una_tira_mas_gruesa_que_la_abertura_no_pasa(probe):
    _cells, plate, _strip, lazos = probe
    gruesa = probes.mount_probe_blade(4.0, 1.4).translate((0.0, 0.0, -0.3))          # centrada en la hoja fina; la celda de 0,15 abre 1,1 mm
    assert (place_accessory(gruesa, lazos[0].matrix) ^ plate).volume() > 0.1
    assert (place_accessory(gruesa, lazos[-1].matrix) ^ plate).volume() < CONTACT_TOL_MM3   # la de 0,45 abre 1,7 mm: pasa


def test_los_puntos_numeran_las_celdas_y_caben_en_la_placa():
    cells = probes.mount_probe_cells([0.1, 0.2, 0.3, 0.4, 0.5])                      # el máximo: 15 puntos
    plate, _strip, lazos = probes.build_mount_probe(Params(), cells)
    assert len(plate.decompose()) == 1                                              # ningún punto quedó suelto fuera de la placa
    assert plate.bounding_box()[3] <= probes.MOUNT_PROBE_PLATE_LEN / 2 + 1e-6
    assert all(extraction_scan(lz, {"placa": plate})["choques"] == {} for lz in lazos)   # y no estorban a la guarda


def test_probe_files_devuelve_placa_tira_y_tabla():
    files, text = probes.probe_files("montura")
    assert set(files) == {"probeta_montura.stl", "tira_hoja.stl", "probeta_montura.txt"}
    for fn in ("probeta_montura.stl", "tira_hoja.stl"):
        m = trimesh.load(io.BytesIO(files[fn]), file_type="stl")
        assert m.is_watertight and m.volume > 1.0
    assert "#4 (4 puntos)" in text and "#1 (1 punto)" in text and "0.25 mm por lado" in text
    assert files["probeta_montura.txt"].decode().strip() == text.strip()


def test_hoja_y_pared_a_medida():
    files, text = probes.probe_files("montura", clearances=[0.25], hoja=(6.0, 1.2), pared=1.6)
    assert "6 × 1.2 mm" in text and "pared del puente 1.6 mm" in text
    assert "abertura 6.50 × 1.70 mm" in text
    tira = trimesh.load(io.BytesIO(files["tira_hoja.stl"]), file_type="stl")
    assert tira.bounds[1][1] - tira.bounds[0][1] == pytest.approx(10.0, abs=0.01)    # la guarda (10 mm) es lo más ancho


def test_tipo_desconocido_sigue_fallando():
    with pytest.raises(ValueError, match="desconocido"):
        probes.probe_files("monturaa")


def test_cli_afm_probe_tipo_montura(tmp_path, capsys):
    assert main(["probe", "--tipo", "montura", "--clearances", "0.2,0.3", "--hoja", "5,1", "--out", str(tmp_path)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["probeta_montura.stl", "probeta_montura.txt", "tira_hoja.stl"]
    out = capsys.readouterr().out
    assert "#2 (2 puntos)" in out and "5 × 1 mm" in out
