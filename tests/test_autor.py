"""D09 · `afm catalogo paquete init|github|empaquetar`: la carpeta del autor (PLAN_catalogos_online.md)."""
import json
import os
import zipfile

import pytest
from PIL import Image

from afm import autor as A
from afm import exportar as X
from afm import paquete as Q
from afm import parts as P
from afm.catalog import Catalog
from afm.cli import main
from afm.validar import validate_package
from test_exportar import OWNER, add_arm, read, unzip

DATOS = dict(autores=["Ana Pérez:ana"], licencia="CC-BY-4.0", origen="propia", github="ana/mi-paquete", linea="proceres-130", altura_mm=130.0)


@pytest.fixture
def cat(tmp_path):
    return Catalog.create(str(tmp_path / "linea"), "proceres-130", 130.0, 0.4)


@pytest.fixture
def carpeta(cat, tmp_path):
    """Carpeta de autor completa: `init` + contenido exportado de un catálogo + portada + descripción."""
    add_arm(cat)
    exp = X.export_package(cat, str(tmp_path / "e.zip"), piezas=["brazo-test-01"], **OWNER)
    d = str(tmp_path / "mi-paquete")
    A.init_package_dir(d, id="mi-paquete", perfil="piezas", descripcion="Un brazo de prueba.", **DATOS)
    unzip(exp.path, str(tmp_path / "x"))
    for rel in exp.archivos:
        if rel in ("README.md", "LICENSE"):
            continue
        dst = os.path.join(d, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(os.path.join(tmp_path, "x", *rel.split("/")), "rb") as s, open(dst, "wb") as t:
            t.write(s.read())
    os.makedirs(os.path.join(d, "previews"), exist_ok=True)
    Image.new("RGB", (1280, 720), (10, 20, 30)).save(os.path.join(d, "previews", "portada.png"))
    return d


# ---------------------------------------------------------------------------------------------------- init

def test_init_crea_la_carpeta_y_dice_que_falta(tmp_path):
    d = str(tmp_path / "p")
    res = A.init_package_dir(d, id="mi-paquete", **DATOS)
    assert res.archivos == ["paquete.json", "README.md", "LICENSE", "CREDITOS.md", ".gitignore", A.WORKFLOW_PATH]
    assert all(os.path.isfile(os.path.join(d, *r.split("/"))) for r in res.archivos)
    p = Q.Paquete.load(os.path.join(d, "paquete.json"))
    assert (p.id, p.tipo, p.version, p.github, p.categorias, p.linea) == ("mi-paquete", "heroe", "0.1.0", "ana/mi-paquete", ["heroes"],
                                                                         {"nombre": "proceres-130", "altura_mm": 130.0})
    assert p.autores[0].github == "ana" and p.licencia == "CC-BY-4.0" and p.motor.startswith(">=")
    assert any("portada" in t for t in res.pendientes) and any("traé el contenido" in t for t in res.pendientes)
    assert open(os.path.join(d, ".gitignore"), encoding="utf-8").read() == "dist/\n"


def test_init_perfiles(tmp_path):
    for perfil, tipo, cat_ in (("piezas", "piezas", "otros"), ("linea", "linea", "lineas"), ("coleccion", "coleccion", "colecciones")):
        res = A.init_package_dir(str(tmp_path / perfil), id="mi-paquete", perfil=perfil, **{**DATOS, "linea": "", "altura_mm": 0.0})
        assert (res.paquete.tipo, res.paquete.categorias) == (tipo, [cat_])
    assert res.paquete.linea is None and any("pines" in t for t in res.pendientes)


def test_init_exige_lo_que_solo_el_autor_puede_dar(tmp_path):
    with pytest.raises(A.AutorError, match="faltan datos que solo el autor puede dar: --licencia, --origen"):
        A.init_package_dir(str(tmp_path / "a"), id="mi-paquete", autores=["Ana"])
    with pytest.raises(A.AutorError, match="perfil desconocido"):
        A.init_package_dir(str(tmp_path / "b"), id="mi-paquete", perfil="otro", **DATOS)
    with pytest.raises(A.AutorError, match="no son válidos"):
        A.init_package_dir(str(tmp_path / "c"), id="Mi Paquete!", **DATOS)
    assert not os.path.exists(tmp_path / "a") and not os.path.exists(tmp_path / "c")


def test_init_toma_el_autor_del_github_y_no_pisa_una_carpeta_con_cosas(tmp_path):
    d = str(tmp_path / "p")
    res = A.init_package_dir(d, id="mi-paquete", licencia="MIT", origen="propia", github="ana/mi-paquete")
    assert res.paquete.autores[0].github == "ana"
    with pytest.raises(A.AutorError, match="ya existe y no está vacía"):
        A.init_package_dir(d, id="mi-paquete", **DATOS)
    os.makedirs(tmp_path / "vacia")
    A.init_package_dir(str(tmp_path / "vacia"), id="mi-paquete", **DATOS)           # una carpeta vacía sí sirve


# ---------------------------------------------------------------------------------------------------- workflow

def test_workflow_valida_arma_y_publica_sin_interpolar_el_mensaje_del_commit():
    w = A.workflow_text()
    assert w.index("paquete empaquetar") < w.index("paquete validar") < w.index("gh release create")
    assert "--estricto" in w and "dist/$ID-$VER.zip" in w and "pip install" in w and "@${AFM_REF}" in w
    assert "git log -1 --pretty=%B" in w and "github.event.head_commit" not in w     # el mensaje no entra al YAML (inyección)
    assert "[release \\([0-9]" in w and "--prerelease" in w and "contents: write" in w
    assert "{" not in w.replace("${{", "").replace("${", "").replace("}}", "").replace("}", "")      # sin llaves de f-string sin resolver
    import re
    import yaml
    doc = yaml.safe_load(w)
    assert doc["jobs"]["publicar"]["steps"][-1]["name"] == "Publicar el Release" and doc[True]["push"]["branches"] == ["main"]


# ---------------------------------------------------------------------------------------------------- github

def test_github_fija_el_repo_y_conserva_claves_desconocidas(tmp_path):
    d = str(tmp_path / "p")
    A.init_package_dir(d, id="mi-paquete", **{**DATOS, "github": ""}, categorias=["heroes"])
    path = os.path.join(d, "paquete.json")
    raw = json.load(open(path, encoding="utf-8"))
    raw["clave_futura"] = {"x": 1}
    json.dump(raw, open(path, "w", encoding="utf-8"), indent=2)
    assert A.set_github(d, "ana/otro-repo") == (True, False)                          # el workflow de init ya estaba al día
    out = json.load(open(path, encoding="utf-8"))
    assert out["github"] == "ana/otro-repo" and out["clave_futura"] == {"x": 1}
    keys = list(out)
    assert keys.index("github") == keys.index("origen") + 1
    assert A.set_github(d, "ana/otro-repo") == (False, False)                          # idempotente
    with pytest.raises(A.AutorError, match="no es «owner/repo»"):
        A.set_github(d, "sin-barra")


def test_github_agrega_el_workflow_y_no_pisa_uno_modificado(tmp_path):
    d = str(tmp_path / "p")
    A.init_package_dir(d, id="mi-paquete", **DATOS)
    wf = os.path.join(d, *A.WORKFLOW_PATH.split("/"))
    os.remove(wf)
    assert A.set_github(d, "ana/mi-paquete") == (False, True) and os.path.isfile(wf)
    with open(wf, "a", encoding="utf-8") as f:
        f.write("# mi cambio\n")
    with pytest.raises(A.AutorError, match="--sobrescribir-workflow"):
        A.set_github(d, "ana/mi-paquete")
    assert "# mi cambio" in open(wf, encoding="utf-8").read()
    assert A.set_github(d, "ana/mi-paquete", overwrite_workflow=True) == (False, True)
    assert "# mi cambio" not in open(wf, encoding="utf-8").read()
    with pytest.raises(A.AutorError, match="no tiene paquete.json"):
        A.set_github(str(tmp_path / "vacia"), "ana/x")


# ---------------------------------------------------------------------------------------------------- empaquetar

def test_empaquetar_arma_un_zip_que_pasa_validar_estricto(carpeta, tmp_path):
    out = str(tmp_path / "dist" / "mi-paquete-0.1.0.zip")
    res = A.pack_dir(carpeta, out)
    p = res.paquete
    assert p.contenido.piezas == ["brazo-test-01@1"] and p.contenido.recetas == [] and p.contenido.paleta is False
    assert any("se actualizó según los archivos" in w for w in res.avisos)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert names[0] == "paquete.json" and names[1:] == sorted(names[1:])
    assert not any(n.startswith((".", "dist")) or ".github" in n for n in names)       # lo oculto y dist/ no viajan
    assert set(p.archivos) == set(names) - {"paquete.json"}
    v = validate_package(out, estricto=True)
    assert v.ok, [str(f) for f in v.findings]
    assert res.sha256 == P.sha256_file(out) and res.tamano == os.path.getsize(out)


def test_empaquetar_es_determinista_y_no_cambia_la_carpeta(carpeta, tmp_path):
    antes = open(os.path.join(carpeta, "paquete.json"), "rb").read()
    a = A.pack_dir(carpeta, str(tmp_path / "a.zip"))
    b = A.pack_dir(carpeta, str(tmp_path / "b.zip"))
    assert a.sha256 == b.sha256
    assert open(os.path.join(carpeta, "paquete.json"), "rb").read() == antes
    assert not os.path.exists(os.path.join(carpeta, "dist"))


def test_empaquetar_con_version_solo_la_cambia_en_el_zip(carpeta, tmp_path):
    out = str(tmp_path / "v.zip")
    res = A.pack_dir(carpeta, out, version="1.2.3")
    assert res.paquete.version == "1.2.3" and Q.Paquete.from_json(read(out, "paquete.json").decode("utf-8")).version == "1.2.3"
    assert json.load(open(os.path.join(carpeta, "paquete.json"), encoding="utf-8"))["version"] == "0.1.0"
    with pytest.raises(A.AutorError, match="no es válido"):
        A.pack_dir(carpeta, str(tmp_path / "m.zip"), version="uno")


def test_empaquetar_toma_la_linea_de_linea_json_si_el_manifiesto_no_la_trae(carpeta, tmp_path):
    path = os.path.join(carpeta, "paquete.json")
    raw = json.load(open(path, encoding="utf-8"))
    raw["linea"] = {"nombre": "", "altura_mm": 0.0}
    json.dump(raw, open(path, "w", encoding="utf-8"), indent=2)
    res = A.pack_dir(carpeta, str(tmp_path / "l.zip"))
    assert res.paquete.linea == {"nombre": "proceres-130", "altura_mm": 130.0}


def test_empaquetar_errores(carpeta, tmp_path):
    out = str(tmp_path / "p.zip")
    A.pack_dir(carpeta, out)
    with pytest.raises(A.AutorError, match="ya existe"):
        A.pack_dir(carpeta, out)
    A.pack_dir(carpeta, out, overwrite=True)
    with pytest.raises(A.AutorError, match="tiene que terminar en .zip"):
        A.pack_dir(carpeta, str(tmp_path / "p.tar"))
    with pytest.raises(A.AutorError, match="no es una carpeta"):
        A.pack_dir(str(tmp_path / "nada"), str(tmp_path / "q.zip"))
    os.symlink(os.path.join(carpeta, "LICENSE"), os.path.join(carpeta, "enlace.txt"))
    with pytest.raises(A.AutorError, match="enlace simbólico"):
        A.pack_dir(carpeta, str(tmp_path / "r.zip"))
    os.remove(os.path.join(carpeta, "enlace.txt"))
    with open(os.path.join(carpeta, "paquete.json"), "w", encoding="utf-8") as f:
        f.write("{no es json")
    with pytest.raises(A.AutorError, match="no es JSON válido"):
        A.pack_dir(carpeta, str(tmp_path / "s.zip"))


def test_empaquetar_un_paquete_sin_portada_valida_con_aviso_y_falla_estricto(carpeta, tmp_path):
    os.remove(os.path.join(carpeta, "previews", "portada.png"))
    out = str(tmp_path / "sp.zip")
    A.pack_dir(carpeta, out)
    assert validate_package(out).ok
    v = validate_package(out, estricto=True)
    assert not v.ok and any(f.code == "CK105" for f in v.findings)


def test_empaquetar_una_coleccion_no_toca_el_contenido(tmp_path):
    d = str(tmp_path / "c")
    A.init_package_dir(d, id="mi-coleccion", perfil="coleccion", **{**DATOS, "linea": "", "altura_mm": 0.0})
    raw = json.load(open(os.path.join(d, "paquete.json"), encoding="utf-8"))
    raw["contenido"] = {"pines": []}
    json.dump(raw, open(os.path.join(d, "paquete.json"), "w", encoding="utf-8"))
    res = A.pack_dir(d, str(tmp_path / "c.zip"), check_manifest=False)
    assert res.paquete.tipo == "coleccion" and res.paquete.linea is None


def test_lo_empaquetado_se_instala(carpeta, tmp_path):
    out = str(tmp_path / "i.zip")
    A.pack_dir(carpeta, out)
    dest = str(tmp_path / "destino")
    assert main(["catalogo", "instalar", out, "--en", dest, "--si"]) == 0
    assert any(n.endswith(".pieza.json") for _b, _d, fs in os.walk(dest) for n in fs)


# ---------------------------------------------------------------------------------------------------- CLI

def test_cli_init_github_empaquetar(carpeta, tmp_path, capsys):
    d = str(tmp_path / "cli")
    ok = ["--autor", "Ana:ana", "--licencia", "CC-BY-4.0", "--origen", "propia", "--linea", "proceres-130", "--altura-mm", "130"]
    assert main(["catalogo", "paquete", "init", d, "--id", "mi-paquete", "--perfil", "piezas", *ok]) == 0
    out = capsys.readouterr().out
    assert "creado en" in out and "Falta para que" in out and "paquete github OWNER/REPO" in out
    assert main(["catalogo", "paquete", "init", d, "--id", "mi-paquete", *ok]) == 2
    assert "ya existe y no está vacía" in capsys.readouterr().err
    assert main(["catalogo", "paquete", "init", str(tmp_path / "nuevo"), "--id", "mi-paquete"]) == 2
    assert "faltan datos" in capsys.readouterr().err
    assert main(["catalogo", "paquete", "github", "ana/mi-paquete", d]) == 0
    assert "github = ana/mi-paquete" in capsys.readouterr().out
    assert main(["catalogo", "paquete", "github", "ana/mi-paquete", d]) == 0
    out = capsys.readouterr().out
    assert "(ya estaba)" in out and "ya está al día" in out
    assert main(["catalogo", "paquete", "github", "mal", d]) == 2
    capsys.readouterr()
    # empaquetar: salida por defecto dist/<id>-<version>.zip, y --version-paquete como el workflow
    assert main(["catalogo", "paquete", "empaquetar", carpeta]) == 0
    out = capsys.readouterr().out
    assert os.path.isfile(os.path.join(carpeta, "dist", "mi-paquete-0.1.0.zip")) and "1 pieza(s)" in out
    assert main(["catalogo", "paquete", "empaquetar", carpeta]) == 2
    assert "ya existe" in capsys.readouterr().err
    assert main(["catalogo", "paquete", "empaquetar", carpeta, "--version-paquete", "0.2.0", "-o", str(tmp_path / "w.zip")]) == 0
    assert main(["catalogo", "paquete", "validar", str(tmp_path / "w.zip"), "--estricto"]) == 0
    capsys.readouterr()
    assert main(["catalogo", "paquete", "empaquetar", str(tmp_path / "nada")]) == 2
    assert "no tiene paquete.json" in capsys.readouterr().err
