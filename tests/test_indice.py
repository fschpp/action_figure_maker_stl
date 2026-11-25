"""D11 · fichas, `meta.json` y entrada de índice de un paquete (PLAN_catalogos_online.md §5.5): sin abrir ningún STL."""
import json
import os
import shutil

import pytest

from afm import indice as X
from afm import paquete as Q
from afm import parts as P
from afm import remoto as R
from afm.catalog import Catalog
from afm.cli import main
from test_exportar import add_arm, cat  # noqa: F401  (cat es una fixture)
from test_instalar import export

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


def validator(which):
    js = pytest.importorskip("jsonschema")
    return js.Draft202012Validator(X.schema(which))


@pytest.fixture
def zip_brazo(cat, tmp_path):
    add_arm(cat)
    return export(cat, tmp_path, github="owner/repo", version="1.2.0")


@pytest.fixture
def zip_ejemplo(tmp_path):
    root = tmp_path / "ejemplo"
    shutil.copytree(EJEMPLO, root)
    return export(Catalog.open(str(root)), tmp_path, "ej.zip", linea_completa=True, github="owner/repo", id="ejemplo-100")


def no_meshes(monkeypatch):
    """Lo que se hace con el zip no puede leer una malla: cualquier intento falla la prueba."""
    def boom(*a, **k):
        raise AssertionError("se intentó leer una malla")
    monkeypatch.setattr(P, "load_mesh", boom)
    monkeypatch.setattr(P, "read_piece", boom)
    import trimesh
    monkeypatch.setattr(trimesh, "load", boom)


def test_los_esquemas_cargan_y_son_validos():
    js = pytest.importorskip("jsonschema")
    for k in X.SCHEMAS:
        js.Draft202012Validator.check_schema(X.schema(k))
    with pytest.raises(KeyError, match="hay meta"):
        X.schema("otro")


def test_fichas_de_un_zip_sin_leer_mallas(zip_brazo, monkeypatch):
    no_meshes(monkeypatch)
    f = X.fichas_from_zip(zip_brazo)
    assert f["formato"] == "afm-fichas-1" and f["paquete"]["version"] == "1.2.0" and f["paquete"]["linea"]["nombre"] == "proceres-130"
    assert [d["id"] for d in f["fichas"]] == ["brazo-test-01"] and f["fichas"][0]["formato"] == "afm-pieza-1"
    validator("fichas").validate(f)
    assert X.dump_json(f) == X.dump_json(X.fichas_from_zip(zip_brazo))          # determinista


def test_fichas_y_entrada_de_catalogo_ejemplo_validan(zip_ejemplo, monkeypatch):
    no_meshes(monkeypatch)
    f = X.fichas_from_zip(zip_ejemplo)
    assert len(f["fichas"]) == 5
    validator("fichas").validate(f)
    e = X.entry_from_zip(zip_ejemplo, url="https://github.com/owner/repo/releases/download/v1.0.0/ejemplo-100-1.0.0.zip")
    validator("indice").validate(X.build_index([e]))
    assert {p["tipo"] for p in e["piezas"]} == {"cabeza", "torso", "brazo", "mano", "pierna"}
    assert e["piezas"][0]["puertos"] and e["folder"] == "owner@ejemplo-100"


def test_meta_json_valida_y_copia_el_manifiesto(zip_brazo):
    pkg = X.fichas_from_zip(zip_brazo) and X._read_package(zip_brazo)[0]
    meta = X.meta_from_paquete(pkg)
    validator("meta").validate(meta)
    assert (meta["id"], meta["autor"], meta["version"], meta["licencia"], meta["origen"], meta["github"]) == \
        (pkg.id, "owner", "1.2.0", "CC-BY-4.0", "propia", "owner/repo")
    assert "seguimiento" not in meta
    fijo = X.meta_from_paquete(pkg, seguimiento="fijo", release_fijo="v1.2.0")
    validator("meta").validate(fijo)
    assert fijo["seguimiento"] == "fijo" and fijo["release_fijo"] == "v1.2.0"


def test_meta_errores_claros(cat, tmp_path, zip_brazo):
    pkg = X._read_package(zip_brazo)[0]
    with pytest.raises(X.IndiceError, match="release_fijo"):
        X.meta_from_paquete(pkg, seguimiento="fijo")
    with pytest.raises(X.IndiceError, match="desconocido"):
        X.meta_from_paquete(pkg, seguimiento="siempre")
    sin_repo = export(cat, tmp_path, "sr.zip")
    with pytest.raises(X.IndiceError, match="no tiene `github`"):
        X.meta_from_paquete(X._read_package(sin_repo)[0])


def test_el_esquema_de_meta_rechaza_lo_que_corresponde(zip_brazo):
    v = validator("meta")
    good = X.meta_from_paquete(X._read_package(zip_brazo)[0])
    sin = {k: x for k, x in good.items() if k != "github"}
    assert list(v.iter_errors(sin))                                              # ni github ni fuente
    assert not list(v.iter_errors({**sin, "fuente": {"tipo": "url", "url": "https://x.example/p.zip", "sha256": "a" * 64}}))
    assert list(v.iter_errors({**sin, "fuente": {"tipo": "url", "url": "http://x.example/p.zip", "sha256": "a" * 64}}))   # solo HTTPS
    assert list(v.iter_errors({**good, "seguimiento": "fijo"}))                  # fijo sin release_fijo
    assert list(v.iter_errors({**good, "categorias": []}))
    assert not list(v.iter_errors({**good, "clave_futura": 1}))                  # evolución aditiva


def test_entrada_de_indice_la_lee_el_cliente(zip_brazo, monkeypatch):
    no_meshes(monkeypatch)
    url = "https://github.com/owner/repo/releases/download/v1.2.0/p.zip"
    e = X.entry_from_zip(zip_brazo, url=url, publicado="2026-11-24T10:00:00Z", portada="paquetes/owner@x/portada.png",
                         update_check="ok", descargas={"total": 7, "reciente": 3, "ventana_dias": 30, "as_of": "2026-11-24"})
    idx = X.build_index([e], generado="2026-11-24T12:00:00+00:00")
    validator("indice").validate(idx)
    assert idx["schema_version"] == R.INDEX_SCHEMA_SUPPORTED and idx["cantidad"] == 1 and idx["categorias"]
    got = R.parse_index(json.dumps(idx), url="https://owner.github.io/idx/data/index.json")
    assert got.avisos == []
    ent = got.entries[0]
    assert ent.folder == e["folder"] and ent.latest.sha256 == e["latest"]["zip"]["sha256"] and ent.latest.tamano == os.path.getsize(zip_brazo)
    assert ent.latest.url == url
    assert ent.latest.fichas == f"https://owner.github.io/idx/data/fichas/{ent.folder}-1.2.0.json"      # relativa a data/
    assert ent.descargas == {"total": 7, "reciente": 3, "ventana_dias": 30, "as_of": "2026-11-24"}


def test_sin_contador_es_null_y_no_cero(zip_brazo):
    e = X.entry_from_zip(zip_brazo, url="https://github.com/o/r/p.zip")
    assert e["descargas"] is None
    validator("indice").validate(X.build_index([e]))
    assert R.parse_index(json.dumps(X.build_index([e]))).entries[0].descargas is None


def test_el_indice_ordena_y_tolera_claves_nuevas(zip_brazo):
    a = X.entry_from_zip(zip_brazo, url="https://github.com/o/r/a.zip", autor="zeta")
    b = X.entry_from_zip(zip_brazo, url="https://github.com/o/r/b.zip", autor="alfa")
    idx = X.build_index([a, b])
    assert [p["folder"] for p in idx["paquetes"]][0].startswith("alfa@")
    idx["clave_futura"] = {"x": 1}
    idx["paquetes"][0]["otra_clave"] = True
    validator("indice").validate(idx)
    assert len(R.parse_index(json.dumps(idx)).entries) == 2
    assert list(validator("indice").iter_errors({**idx, "paquetes": [{**a, "update_check": "quien sabe"}]}))


def test_zip_hostil_o_sin_manifiesto(tmp_path):
    import zipfile
    bad = tmp_path / "x.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("../evil.json", "{}")
    with pytest.raises(X.IndiceError, match="inspección"):
        X.fichas_from_zip(str(bad))
    nom = tmp_path / "n.zip"
    with zipfile.ZipFile(nom, "w") as z:
        z.writestr("README.md", "hola")
    with pytest.raises(X.IndiceError, match="no es un paquete"):
        X.fichas_from_zip(str(nom))


def test_cli_paquete_fichas(zip_brazo, tmp_path, capsys, monkeypatch):
    no_meshes(monkeypatch)
    out = tmp_path / "f.json"
    assert main(["catalogo", "paquete", "fichas", zip_brazo, "-o", str(out)]) == 0
    validator("fichas").validate(json.loads(out.read_text(encoding="utf-8")))
    assert main(["catalogo", "paquete", "fichas", zip_brazo, "-o", str(out)]) == 2
    assert "ya existe" in capsys.readouterr().err
    assert main(["catalogo", "paquete", "fichas", zip_brazo, "-o", str(out), "--sobrescribir"]) == 0
    capsys.readouterr()
    assert main(["catalogo", "paquete", "fichas", zip_brazo]) == 0
    assert json.loads(capsys.readouterr().out)["formato"] == "afm-fichas-1"
    assert main(["catalogo", "paquete", "fichas", str(tmp_path / "no.zip")]) == 2
