"""D26 · piezas por referencia (PLAN_catalogos_online.md §5.11): el paquete no trae el STL, la persona lo aporta y se verifica por sha256."""
import json
import os
import shutil
import zipfile

import pytest

from afm import exportar as X
from afm import instalar as I
from afm import paquete as Q
from afm import referencias as R
from afm import validar as V
from afm.assembly import PartChoice
from afm.catalog import Catalog, PieceIncompleteError
from afm.cli import main
from test_exportar import add_arm, cat  # noqa: F401  (cat es una fixture)

REF_ID = "cuerpo-original"


@pytest.fixture
def dest(tmp_path):
    return Catalog.create(str(tmp_path / "destino"), "proceres-130", 130.0, 0.4)


@pytest.fixture
def original(cat, tmp_path):
    """El STL «de terceros» del autor: copia del STL de la pieza, fuera del catálogo (es lo que la persona tiene en su disco)."""
    m = add_arm(cat)
    path = tmp_path / "modelo_original.stl"
    shutil.copy(os.path.join(cat._dir(m.tipo), m.stl), path)
    return m, str(path)


def base_referencia(sha: str, tamano_max: int = 5_000_000) -> Q.Paquete:
    return Q.Paquete(id="brazo-ref", nombre="Brazo por referencia", version="1.0.0", descripcion="Solo la articulación; el STL lo aporta cada persona.",
                     tipo="piezas", autores=[Q.Author("Ana Pérez", "owner")], licencia="CC-BY-4.0", origen="referencia", categorias=["cuerpos"],
                     referencias_requeridas=[Q.RequiredReference(REF_ID, "Cuerpo original", [sha], tamano_max)])


@pytest.fixture
def zip_ref(cat, original, tmp_path):
    m, _ = original
    res = X.export_package(cat, str(tmp_path / "ref.zip"), piezas=[m.ref], base=base_referencia(m.sha256))
    return res


def instalar(zip_path, root):
    plan = I.plan_install(zip_path, str(root))
    assert plan.ok, [str(i) for i in plan.errors]
    return plan, I.apply(plan)


# ---------------------------------------------------------------------------------------------------- el autor
def test_el_paquete_no_trae_el_stl_y_pasa_el_lint(zip_ref, original):
    m, _ = original
    names = zipfile.ZipFile(zip_ref.path).namelist()
    assert f"piezas/brazo/{m.id}@1.pieza.json" in names and not any(n.endswith(".stl") for n in names)
    assert any("no viaja en el paquete" in a for a in zip_ref.avisos)
    pkg = Q.Paquete.from_json(zipfile.ZipFile(zip_ref.path).read("paquete.json").decode())
    assert pkg.origen == "referencia" and pkg.referencias_requeridas[0].sha256 == [m.sha256]
    res = V.validate_package(zip_ref.path)
    assert res.ok and [f.code for f in res.findings] == ["CK105"], [str(f) for f in res.findings]       # solo falta la portada: nada de CK203 por el STL ausente


def test_ck304_si_alguien_empaqueta_el_archivo(cat, original, tmp_path):
    m, _ = original
    plain = X.export_package(cat, str(tmp_path / "plano.zip"), piezas=[m.ref], autores=["Ana Pérez:owner"], licencia="CC-BY-4.0", origen="propia").path
    with zipfile.ZipFile(plain) as zi:
        pkg = Q.Paquete.from_json(zi.read("paquete.json").decode())
        pkg.origen = "referencia"
        pkg.referencias_requeridas = [Q.RequiredReference(REF_ID, "Cuerpo original", [m.sha256], 5_000_000)]
        out = str(tmp_path / "con_stl.zip")
        with zipfile.ZipFile(out, "w") as zo:
            for n in zi.namelist():
                zo.writestr(n, pkg.to_json() if n == "paquete.json" else zi.read(n))
    res = V.validate_package(out)
    assert not res.ok and [f.path for f in res.findings if f.code == "CK304"] == [f"piezas/brazo/{m.id}@1.stl"]


def test_aviso_si_ninguna_ficha_tiene_el_hash(cat, original, tmp_path):
    m, _ = original
    z = X.export_package(cat, str(tmp_path / "ref.zip"), piezas=[m.ref], base=base_referencia("a" * 64)).path
    res = V.validate_package(z)
    assert any("ninguna pieza del paquete tiene el sha256" in f.message for f in res.warnings)
    assert res.ok and any(n.endswith(".stl") for n in zipfile.ZipFile(z).namelist())        # el hash no coincide: la pieza no es la referencia y su STL viaja


# ---------------------------------------------------------------------------------------------------- quien instala
def test_el_plan_dice_que_hay_que_aportar_el_archivo(zip_ref, dest):
    plan = I.plan_install(zip_ref.path, dest.root)
    assert plan.ok
    text = "\n".join(plan.describe())
    assert "Archivos que tenés que aportar (1): «Cuerpo original»" in text and "brazo-test-01@1 (incompleta)" in text
    assert "incompleta hasta importar «Cuerpo original»" in plan.a_instalar[0].detail


def test_instalada_la_pieza_esta_incompleta_y_no_se_arma(zip_ref, dest, original):
    m, _ = original
    _plan, res = instalar(zip_ref.path, dest.root)
    assert res.faltan_archivos == ["Cuerpo original"]
    cat2 = Catalog.open(dest.root)
    assert not os.path.exists(os.path.join(cat2._dir("brazo"), m.stl))
    assert cat2.is_incomplete(m.ref)
    with pytest.raises(PieceIncompleteError, match="Cuerpo original"):
        cat2.load(m.ref)
    with pytest.raises(PieceIncompleteError):
        PartChoice.from_catalog(cat2, m.ref)                        # el armado pasa por `Catalog.load`
    rep = cat2.verify()
    assert rep.ok and any("incompleta" in w for w in rep.warnings)
    assert [(ref, r.id) for ref, r in R.piezas_incompletas(cat2)] == [(m.ref, REF_ID)]
    assert json.load(open(os.path.join(dest.root, "paquetes.json")))["paquetes"]["brazo-ref"]["referencias"][0]["id"] == REF_ID


def test_importar_un_archivo_que_no_es_no_guarda_nada(zip_ref, dest, original, tmp_path):
    instalar(zip_ref.path, dest.root)
    cat2 = Catalog.open(dest.root)
    other = tmp_path / "otro.stl"
    other.write_bytes(b"solid otro\nendsolid otro\n")
    with pytest.raises(R.ReferenceError, match="no es ninguno de los archivos que se piden"):
        R.importar(cat2, str(other))
    big = tmp_path / "grande.stl"
    big.write_bytes(b"x" * 5_000_001)
    with pytest.raises(R.ReferenceError, match="lo más que se acepta"):
        R.importar(cat2, str(big))
    with pytest.raises(R.ReferenceError, match="no es un archivo"):
        R.importar(cat2, str(tmp_path / "no_existe.stl"))
    with pytest.raises(R.ReferenceError, match="ningún paquete instalado pide un archivo «otro-id»"):
        R.importar(cat2, str(other), "otro-id")
    assert not os.path.exists(os.path.join(dest.root, "referencias")) and cat2.is_incomplete(original[0].ref)


def test_importar_el_archivo_correcto_completa_la_pieza(zip_ref, dest, original):
    m, src = original
    instalar(zip_ref.path, dest.root)
    cat2 = Catalog.open(dest.root)
    res = R.importar(cat2, src, REF_ID)
    assert res.completadas == [m.ref] and not res.ya_estaba and res.avisos == []
    assert os.path.isfile(os.path.join(dest.root, "referencias", f"{m.sha256}.stl"))
    assert not cat2.is_incomplete(m.ref) and R.piezas_incompletas(cat2) == []
    meta, mesh = cat2.load(m.ref)
    assert meta.sha256 == m.sha256 and mesh.is_watertight
    PartChoice.from_catalog(cat2, m.ref)
    rep = cat2.verify()
    assert rep.ok and not any("incompleta" in w for w in rep.warnings)
    assert R.importar(cat2, src).ya_estaba
    assert "Todo completo." in R.lines(cat2)


def test_un_archivo_importado_modificado_se_detecta(zip_ref, dest, original):
    m, src = original
    instalar(zip_ref.path, dest.root)
    cat2 = Catalog.open(dest.root)
    R.importar(cat2, src)
    with open(cat2.reference_state_path(m.sha256), "ab") as f:
        f.write(b"\n")
    assert not cat2.verify().ok
    with pytest.raises(Exception, match="no coincide con el hash"):
        cat2.load(m.ref)


def test_quitar_vuelve_a_dejarla_incompleta(zip_ref, dest, original):
    m, src = original
    instalar(zip_ref.path, dest.root)
    cat2 = Catalog.open(dest.root)
    R.importar(cat2, src)
    assert R.quitar(cat2, REF_ID) == [m.ref]
    assert cat2.is_incomplete(m.ref) and not os.path.exists(os.path.join(dest.root, "referencias"))
    with pytest.raises(R.ReferenceError, match="no está importado"):
        R.quitar(cat2, REF_ID)


def test_ya_importado_antes_de_instalar_la_pieza_queda_completa(zip_ref, dest, original):
    """Otro paquete (o una instalación anterior) ya dejó el archivo: el plan no lo vuelve a pedir."""
    m, src = original
    os.makedirs(os.path.join(dest.root, "referencias"))
    shutil.copy(src, os.path.join(dest.root, "referencias", f"{m.sha256}.stl"))
    plan, res = instalar(zip_ref.path, dest.root)
    assert plan.requeridas[0][1] is True and res.faltan_archivos == []
    assert not Catalog.open(dest.root).is_incomplete(m.ref)


def test_exportar_el_catalogo_destino_no_lleva_el_archivo_importado(zip_ref, dest, original, tmp_path):
    m, src = original
    instalar(zip_ref.path, dest.root)
    cat2 = Catalog.open(dest.root)
    R.importar(cat2, src)
    z = X.export_package(cat2, str(tmp_path / "re.zip"), piezas=[m.ref], base=base_referencia(m.sha256)).path
    assert not any(n.endswith(".stl") for n in zipfile.ZipFile(z).namelist())
    with pytest.raises(X.ExportError, match="falta el STL"):                       # sin declarar la referencia, el STL tendría que viajar y no está
        X.export_package(cat2, str(tmp_path / "mal.zip"), piezas=[m.ref], autores=["Ana:o"], licencia="CC0-1.0", origen="propia")


def test_un_catalogo_sin_referencias_no_cambia(cat):
    m = add_arm(cat)
    assert R.estado(cat) == [] and R.piezas_incompletas(cat) == [] and not cat.is_incomplete(m.ref)
    assert R.lines(cat) == ["Ningún paquete instalado pide archivos requeridos."]
    os.remove(os.path.join(cat._dir("brazo"), m.stl))
    assert not cat.verify().ok                                                      # una pieza común sin STL sigue siendo un problema


# ---------------------------------------------------------------------------------------------------- consola
def test_consola_listar_importar_quitar(zip_ref, dest, original, capsys):
    m, src = original
    instalar(zip_ref.path, dest.root)
    assert main(["catalogo", "referencia", "listar", dest.root]) == 0
    out = capsys.readouterr().out
    assert f"{REF_ID} — Cuerpo original [FALTA]" in out and m.ref in out and "Faltan 1 archivo(s)" in out
    assert main(["catalogo", "referencia", "importar", dest.root, src, "--id", REF_ID]) == 0
    assert f"Piezas que ahora están completas: {m.ref}" in capsys.readouterr().out
    assert main(["catalogo", "referencia", "listar", dest.root]) == 0 and "[importado]" in capsys.readouterr().out
    assert main(["catalogo", "referencia", "quitar", dest.root, REF_ID]) == 0
    assert main(["catalogo", "referencia", "importar", dest.root, __file__]) == 2
    assert "no es ninguno de los archivos que se piden" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------------- ventana «Paquetes instalados» (sin GTK)
def test_paquetes_instalados_ofrece_importar_el_archivo_requerido(zip_ref, dest, original):
    from afm.gui import catalog_online as CO
    from afm.gui import installed_packages as IP
    from afm.i18n import set_language

    set_language("es")
    m, src = original
    instalar(zip_ref.path, dest.root)
    st = CO.OnlineState([], dest.root)
    st.indexes = []
    ip = IP.InstalledState(st)
    (row,) = ip.rows()
    (req,) = ip.required_files(row)
    assert (req.id, req.importado, req.piezas) == (REF_ID, False, [m.ref]) and "FALTA" in req.texto
    assert "FALTA" in dict(ip.detail_rows(row))["Archivos requeridos"]
    with pytest.raises(R.ReferenceError):
        ip.import_required(row, "otro-id", src)
    with pytest.raises(R.ReferenceError, match="no es ninguno de los archivos"):
        ip.import_required(row, REF_ID, __file__)
    lines = ip.import_required(row, REF_ID, src)
    assert lines == ["Importado: «Cuerpo original».", f"Piezas que ahora están completas: {m.ref}"]
    (req,) = ip.required_files(row)
    assert req.importado and "importado" in dict(ip.detail_rows(row))["Archivos requeridos"]
    assert ip.import_required(row, REF_ID, src) == ["Ese archivo ya estaba importado."]


def test_un_hash_aceptado_que_ninguna_ficha_usa_se_guarda_con_aviso(cat, original, dest, tmp_path):
    """`sha256` es una lista de aceptados: una variante válida se guarda, pero solo completa lo que una ficha instalada pide con ese hash."""
    m, src = original
    variante = tmp_path / "variante.stl"
    variante.write_bytes(open(src, "rb").read() + b"\n")
    base = base_referencia(m.sha256)
    base.referencias_requeridas[0].sha256.append(R.P.sha256_file(str(variante)))
    z = X.export_package(cat, str(tmp_path / "v.zip"), piezas=[m.ref], base=base).path
    instalar(z, dest.root)
    cat2 = Catalog.open(dest.root)
    res = R.importar(cat2, str(variante))
    assert res.completadas == [] and "no completa ninguna pieza instalada" in res.avisos[0]
    assert cat2.is_incomplete(m.ref)
