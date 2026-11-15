"""EngineSession: la API de bytes que usa el navegador (Pyodide). Corre con la figura sintética."""
import io
import json
import zipfile

import pytest

from afm.testfigure import make_doll
from afm.webapi import EngineSession


def test_load_stl_reports_the_mesh_and_rejects_garbage():
    s = EngineSession()
    buf = io.BytesIO()
    make_doll().export(buf, file_type="stl")
    r = s.load_stl(buf.getvalue())
    assert r["triangulos"] > 100 and 90 < r["alto_mm"] < 110
    with pytest.raises(ValueError):
        s.load_stl(b"esto no es un stl")
    s.close()


def test_build_requires_a_loaded_stl():
    with pytest.raises(RuntimeError):
        EngineSession().build("{}")


def test_demo_returns_a_zip_with_the_parts_and_the_report():
    s = EngineSession()
    msgs = []
    r = s.demo(progress_cb=msgs.append)
    assert msgs, "el progreso debería informar etapas"
    z = zipfile.ZipFile(io.BytesIO(r["zip"]))
    assert sorted(z.namelist()) == r["archivos"]
    assert any(n.endswith(".stl") for n in r["archivos"]) and "muneco_informe.json" in r["archivos"]
    assert json.loads(z.read("muneco_informe.json"))["validacion"]["ok"] is True
    assert r["informe"]["validacion"]["ok"] is True
    s.close()
