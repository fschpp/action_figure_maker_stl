"""D21 · «Publicar…» sin GTK (PLAN_catalogos_online.md §5.8, D21): del catálogo a un paquete validado, un .zip, la carpeta del repo y el alta en el índice."""
import json
import os
import py_compile
import re

import pytest

from afm import autor as A
from afm import i18n
from afm import instalar as I
from afm import paquete as Q
from afm import validar as V
from afm.gui import publish as PB
from test_exportar import add_arm, add_colored, add_recipe, cat  # noqa: F401  (cat es una fixture)
from test_instalar import dest  # noqa: F401  (dest es una fixture)

OWNER = ["Ana Pérez:owner"]


def filled(flow, **over):
    f = flow.form
    f.piezas, f.nombre, f.autores, f.licencia, f.origen, f.github = ["brazo-test-01"], "Brazo de prueba", list(OWNER), "CC-BY-4.0", "propia", "owner/brazo-de-prueba"
    f.descripcion, f.etiquetas, f.categorias = "Un brazo para probar.", ["brazo"], ["manos"]
    for k, v in over.items():
        setattr(f, k, v)
    return flow


@pytest.fixture
def flow(cat):
    add_arm(cat)
    fl = PB.PublishFlow(cat.root)
    yield filled(fl)
    fl.close()


def campos(flow):
    return {p.campo for p in flow.problems()}


# ---------------------------------------------------------------------------------------------------------------- ayudas

def test_sugerir_id_y_autores():
    assert PB.suggest_id("Brazo de Prueba ¡Ñandú!") == "brazo-de-prueba-nandu" and PB.suggest_id("   ") == ""
    assert PB.suggest_id("x" * 200) == "x" * Q.ID_MAX
    assert PB.parse_authors("Ana Pérez:ana, Luis\n  \nMaría:maria") == ["Ana Pérez:ana", "Luis", "María:maria"]


def test_cada_licencia_y_origen_tiene_explicacion():
    for lic in Q.LICENSES:
        text = PB.license_help(lic)
        assert text and not text.startswith("publish."), lic
    assert "nombrarte" in PB.license_help("CC-BY-4.0") and "vender" in PB.license_help("CC-BY-NC-4.0") and "misma licencia" in PB.license_help("CC-BY-SA-4.0")
    assert "modificar" in PB.license_help("CC-BY-ND-4.0") and "Dominio público" in PB.license_help("CC0-1.0") and "reservados" in PB.license_help(Q.ALL_RIGHTS_RESERVED)
    assert "programas" in PB.license_help("MIT") and PB.license_help("") == i18n.tr("publish.lic_elegi")
    for o in Q.ORIGINS:
        assert PB.origin_help(o) and not PB.origin_help(o).startswith("publish."), o
    assert PB.origin_help("inventado") == "inventado"


# ---------------------------------------------------------------------------------------------------------------- lo que falta

def test_problemas_en_espanol_antes_de_armar_nada(cat):
    add_arm(cat)
    fl = PB.PublishFlow(cat.root)
    assert campos(fl) == {"contenido", "nombre", "id", "autores", "licencia", "origen"}
    f = fl.form
    f.nombre, f.piezas = "Brazo", ["brazo-test-01"]
    assert "id" not in campos(fl)                                                                  # el id sale del nombre
    f.id, f.version, f.licencia, f.origen, f.github, f.autores = "Mal Id", "1.0", "GPL", "robada", "sin-barra", ["Ana"]
    assert campos(fl) == {"id", "version", "licencia", "origen", "github"}
    f.linea_completa = True
    assert "contenido" in campos(fl)                                                               # no se mezcla con piezas sueltas
    f.linea_completa, f.portada, f.readme, f.descripcion = False, "/no/existe.png", "/no/existe.md", "x" * 400
    assert {"portada", "readme", "descripcion"} <= campos(fl)
    assert all(p.texto and not p.texto.startswith("publish.") for p in fl.problems())


def test_avisos_de_criterio(flow):
    flow.form.licencia = "CC-BY-NC-4.0"
    assert any("no comercial" in w.lower() for w in flow.warnings_before())
    flow.form.licencia, flow.form.origen = "MIT", "derivada"
    assert any("deriva" in w for w in flow.warnings_before())
    flow.form.licencia, flow.form.origen = "CC-BY-4.0", "propia"
    assert flow.warnings_before() == []


def test_que_hay_para_elegir(cat):
    add_arm(cat)
    add_recipe(cat, "mi-figura", {"brazo": "brazo-test-01@1"})
    fl = PB.PublishFlow(cat.root)
    ids = [p[0] for p in fl.available_pieces()]
    assert "brazo-test-01" in ids and fl.available_recipes() == ["mi-figura"] and fl.line() == ("proceres-130", 130.0)
    assert PB.PublishFlow(os.path.join(cat.root, "no-hay")).available_recipes() == []


# ---------------------------------------------------------------------------------------------------------------- armar y validar

def test_validar_arma_en_una_carpeta_temporal_y_no_toca_el_catalogo(cat, flow):
    before = I.tree_digest(cat.root)
    rep = flow.validate()
    assert rep.ok and not rep.errores and rep.paquete.id == "brazo-de-prueba" and rep.tamano > 0 and len(rep.sha256) == 64
    assert "brazo-de-prueba" in rep.resumen and "1 pieza" in rep.resumen
    assert flow.stage == PB.VALIDADO and flow.validated
    assert I.tree_digest(cat.root) == before                                                       # nada se escribió en el catálogo
    tmp = flow._tmp
    assert tmp and os.path.isdir(tmp)
    flow.close()
    assert not os.path.exists(tmp)


def test_la_validacion_corre_en_un_hilo_con_progreso(flow):
    seen = []
    t = flow.task_validate(notify=lambda: seen.append(1))
    t.start().join()
    assert t.status == "done" or flow.report is not None
    assert flow.report.ok and seen


def test_con_datos_incompletos_no_se_arma_nada(cat):
    add_arm(cat)
    fl = PB.PublishFlow(cat.root)
    with pytest.raises(PB.PublishError, match="Falta"):
        fl.validate()
    assert fl.stage == PB.FALLO and fl.report is None and not fl.validated and fl._tmp is None


def test_errores_de_validacion_legibles_y_sin_salidas(flow, monkeypatch, tmp_path):
    real = V.validate_package

    def fake(path, **kw):
        res = real(path, **kw)
        res.findings.append(V.Finding("CK999", V.ERROR, "piezas/x.stl", "la malla no es cerrada", "volvé a exportarla"))
        res.findings.append(V.Finding("CK998", V.WARNING, "README.md", "falta la portada"))
        return res

    monkeypatch.setattr(PB.V, "validate_package", fake)
    rep = flow.validate()
    assert not rep.ok and flow.stage == PB.FALLO and "1 error" in flow.error
    e = rep.errores[0]
    assert (e.severidad, e.codigo, e.donde, e.arreglo) == ("error", "CK999", "piezas/x.stl", "volvé a exportarla") and rep.avisos[0].severidad == "aviso"
    for salida in (lambda: flow.export_zip(str(tmp_path / "x.zip")), lambda: flow.prepare_repo(str(tmp_path / "r")), lambda: flow.prepare_alta(str(tmp_path))):
        with pytest.raises(PB.PublishError, match="revisar"):
            salida()
    assert not (tmp_path / "x.zip").exists()


def test_cambiar_un_dato_despues_de_validar_obliga_a_validar_de_nuevo(flow, tmp_path):
    flow.validate()
    assert flow.validated
    flow.form.licencia = "CC0-1.0"
    assert not flow.validated
    with pytest.raises(PB.PublishError):
        flow.export_zip(str(tmp_path / "p.zip"))
    flow.validate()
    assert flow.validated and flow.export_zip(str(tmp_path / "p.zip")).endswith("p.zip")


# ---------------------------------------------------------------------------------------------------------------- salidas

def test_exportar_el_zip_que_despues_se_instala(flow, dest, tmp_path):
    flow.validate()
    out = flow.export_zip(str(tmp_path / "salida" / "brazo"))
    assert out.endswith("brazo.zip") and os.path.isfile(out)
    plan = I.plan_install(out, dest.root)
    assert plan.ok and plan.paquete.id == "brazo-de-prueba" and plan.paquete.licencia == "CC-BY-4.0" and plan.paquete.autores[0].github == "owner"
    with pytest.raises(PB.PublishError, match="ya existe"):
        flow.export_zip(out)
    assert flow.export_zip(out, overwrite=True) == out


def test_el_paquete_trae_creditos_y_el_origen_declarado(flow, tmp_path):
    flow.form.creditos = "Modelo base de Luis, retocado por Ana."
    flow.validate()
    out = flow.export_zip(str(tmp_path / "p.zip"))
    import zipfile
    with zipfile.ZipFile(out) as zf:
        assert "Modelo base de Luis" in zf.read("CREDITOS.md").decode("utf-8")
        assert json.loads(zf.read("paquete.json"))["origen"] == "propia"


def test_preparar_la_carpeta_del_repo(flow, tmp_path):
    flow.validate()
    dest_dir = str(tmp_path / "repo")
    steps = flow.prepare_repo(dest_dir)
    assert len(steps) == 3 and "owner/brazo-de-prueba" in steps[0] and "v1.0.0" in steps[1]
    manifest = json.load(open(os.path.join(dest_dir, "paquete.json"), encoding="utf-8"))
    assert manifest["github"] == "owner/brazo-de-prueba" and os.path.isfile(os.path.join(dest_dir, ".github", "workflows", os.path.basename(A.WORKFLOW_PATH)))
    with pytest.raises(PB.PublishError, match="no está vacía"):
        flow.prepare_repo(dest_dir)
    res = A.pack_dir(dest_dir, str(tmp_path / "otra.zip"))                                         # lo que hará el workflow: la carpeta se empaqueta de nuevo
    assert res.paquete.id == "brazo-de-prueba" and V.validate_package(res.path, estricto=True).ok
    flow.form.github = ""
    with pytest.raises(PB.PublishError, match="usuario/repositorio"):
        flow.prepare_repo(str(tmp_path / "repo2"))
    assert not os.path.exists(tmp_path / "repo2" / "paquete.json")


def test_preparar_el_alta_en_el_indice(flow, tmp_path):
    flow.form.portada = str(tmp_path / "foto.png")
    with open(flow.form.portada, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    flow.validate()
    target, steps = flow.prepare_alta(str(tmp_path / "alta"))
    assert os.path.basename(target) == "owner@brazo-de-prueba" and len(steps) == 3
    assert sorted(os.listdir(target)) == ["descripcion.md", "meta.json", "portada.png"]
    meta = json.load(open(os.path.join(target, "meta.json"), encoding="utf-8"))
    assert (meta["id"], meta["autor"], meta["github"], meta["licencia"], meta["origen"], meta["version"]) == ("brazo-de-prueba", "owner", "owner/brazo-de-prueba", "CC-BY-4.0", "propia", "1.0.0")
    assert "Brazo de prueba" in open(os.path.join(target, "descripcion.md"), encoding="utf-8").read()
    jsonschema = pytest.importorskip("jsonschema")
    from afm import indice as IX
    jsonschema.validate(meta, IX.schema("meta"))
    with pytest.raises(PB.PublishError, match="no está vacía"):
        flow.prepare_alta(str(tmp_path / "alta"))


def test_el_alta_pide_repo(flow, tmp_path):
    flow.form.github = ""
    flow.validate()
    with pytest.raises(PB.PublishError, match="GitHub"):
        flow.prepare_alta(str(tmp_path / "alta"))
    assert not (tmp_path / "alta").exists() or not os.listdir(tmp_path / "alta")


def test_publicar_una_receta_con_sus_piezas(cat, tmp_path):
    add_arm(cat)
    add_recipe(cat, "mi-figura", {"brazo": "brazo-test-01@1"})
    fl = filled(PB.PublishFlow(cat.root), piezas=[], recetas=["mi-figura"], tipo="heroe", nombre="Mi figura", github="owner/mi-figura")
    try:
        rep = fl.validate()
        assert rep.ok and rep.paquete.tipo == "heroe" and rep.paquete.contenido.recetas == ["mi-figura"]
    finally:
        fl.close()


# ---------------------------------------------------------------------------------------------------------------- ventana e idiomas

def test_publicar_con_el_fbx_de_color(cat, tmp_path):
    """Sin la casilla el FBX no viaja (y se avisa); con ella viaja y el paquete pasa la validación estricta."""
    from PIL import Image
    add_colored(cat, "brazo-rgb-01", "rgb", b"FBX" * 10)
    portada = str(tmp_path / "portada.png")
    Image.new("RGB", (1280, 720), (200, 200, 200)).save(portada)
    fl = filled(PB.PublishFlow(cat.root), piezas=["brazo-rgb-01"], portada=portada)
    try:
        rep = fl.validate()
        assert rep.ok and any("se exporta sin color" in a and "Incluir el color de las piezas" in a for a in rep.avisos_exportacion)
        assert not any("aviso_fbx" in w or "FBX de color" in w for w in fl.warnings_before())
        fl.form.incluir_fbx = True
        assert not fl.validated                                  # cambiar la casilla obliga a validar de nuevo
        assert any("FBX" in w for w in fl.warnings_before())
        rep = fl.validate()
        assert rep.ok and not rep.errores and not any("se exporta sin color" in a for a in rep.avisos_exportacion)
        out = fl.export_zip(str(tmp_path / "con-fbx.zip"))
        assert V.validate_package(out, estricto=True, allow_fbx=True).ok
        import zipfile
        assert "piezas/brazo/brazo-rgb-01@1.fbx" in zipfile.ZipFile(out).namelist()
        dest_dir = tmp_path / "repo"
        fl.prepare_repo(str(dest_dir), "owner/brazo-rgb")
        assert (dest_dir / "piezas" / "brazo" / "brazo-rgb-01@1.fbx").is_file()
    finally:
        fl.close()


def test_publicar_con_el_color_extraido_sin_el_fbx(cat, tmp_path):
    """La casilla «Incluir el color de las piezas»: el paquete lleva el color extraído (no el FBX), pasa la validación estricta y se desempaqueta."""
    import zipfile

    from color_helpers import write_doll_fbx
    from PIL import Image
    add_colored(cat, "brazo-rgb-01", "rgb", open(write_doll_fbx(tmp_path / "m.fbx"), "rb").read())
    portada = str(tmp_path / "portada.png")
    Image.new("RGB", (1280, 720), (200, 200, 200)).save(portada)
    fl = filled(PB.PublishFlow(cat.root), piezas=["brazo-rgb-01"], portada=portada)
    try:
        assert any("se exporta sin color" in a for a in fl.validate().avisos_exportacion)
        fl.form.incluir_color = True
        assert not fl.validated                                  # cambiar la casilla obliga a validar de nuevo
        rep = fl.validate()
        assert rep.ok and not rep.errores and not any("se exporta sin color" in a for a in rep.avisos_exportacion)
        out = fl.export_zip(str(tmp_path / "con-color.zip"))
        ns = zipfile.ZipFile(out).namelist()
        assert "piezas/brazo/brazo-rgb-01@1.colorrgb.npz" in ns and not any(n.endswith(".fbx") for n in ns)
        assert V.validate_package(out, estricto=True).ok         # sin permitir .fbx
        fl.prepare_repo(str(tmp_path / "repo"), "owner/brazo-rgb")
        assert (tmp_path / "repo" / "piezas" / "brazo" / "brazo-rgb-01@1.colorrgb.npz").is_file()
    finally:
        fl.close()


def test_las_claves_de_publicar_existen_en_los_tres_idiomas():
    src = os.path.join(os.path.dirname(PB.__file__))
    used = set()
    for name in ("publish.py", "publish_window.py"):
        text = open(os.path.join(src, name), encoding="utf-8").read()
        used |= set(re.findall(r'tr\(\s*"([a-z_]+\.[a-z_0-9]+)"', text))
        used |= set(re.findall(r'tr\(f"(publish_window\.paso)_', text))
    used |= {"publish.aviso_fbx", "publish_window.incluir_color", "publish_window.incluir_color_tip"}
    used |= {f"publish_window.paso_{p}" for p in ("contenido", "datos", "licencia", "revisar")}
    used |= {f"publish.origen_{o.replace('-', '_')}" for o in Q.ORIGINS} | {"catalog_window.menu_compartir", "catalog_window.menu_compartir_tip"}
    assert len(used) > 50
    for lang in ("es", "en", "pt"):
        loc = json.load(open(os.path.join(os.path.dirname(i18n.__file__), "locales", f"{lang}.json"), encoding="utf-8"))
        for key in sorted(used):
            grupo, _, nombre = key.partition(".")
            assert nombre in loc.get(grupo, {}), f"{lang}: falta {key}"
    ph = lambda s: sorted(re.findall(r"%\{\w+\}", s))  # noqa: E731
    es = json.load(open(os.path.join(os.path.dirname(i18n.__file__), "locales", "es.json"), encoding="utf-8"))
    for lang in ("en", "pt"):
        loc = json.load(open(os.path.join(os.path.dirname(i18n.__file__), "locales", f"{lang}.json"), encoding="utf-8"))
        for g in ("publish", "publish_window"):
            for k, v in es[g].items():
                assert ph(v) == ph(loc[g][k]), f"{lang}: {g}.{k}"


def test_la_ventana_compila_y_el_menu_esta_conectado():
    here = os.path.dirname(PB.__file__)
    py_compile.compile(os.path.join(here, "publish_window.py"), doraise=True)
    py_compile.compile(os.path.join(here, "catalog_window.py"), doraise=True)
    assert "self._open_publish" in open(os.path.join(here, "catalog_window.py"), encoding="utf-8").read()
