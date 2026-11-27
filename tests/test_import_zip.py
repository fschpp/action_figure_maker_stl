"""D22 · importar un paquete `.zip` desde un archivo (`afm.gui.import_zip`): el mismo flujo de confirmación que «Instalar» de «Explorar» (D19), sin GTK.

Se conduce todo sin ventana: se mira el archivo (paquete o zip heredado), se arma el plan sin escribir, la confirmación dice lo mismo que dice D19 (autor,
licencia, origen, destino, qué agrega, conflictos y avisos) y recién `task_install(acepto=True)` escribe, todo o nada. «No se escribe nada» se comprueba con
el resumen de todo el árbol del catálogo (`instalar.tree_digest`). Lo que se instala es **exactamente** lo que instala `afm catalogo instalar`."""
import os
import zipfile

import pytest

from afm import instalar as I
from afm.catalog import Catalog
from afm.gui import catalog_online as C
from afm.gui import import_zip as IZ
from afm.gui import install_confirm as IC
from afm.i18n import set_language
from test_dependencias import entorno  # noqa: F401  (autouse: aísla ~/.local/share, la caché y la configuración)
from test_exportar import add_arm, add_colored, add_recipe, cat  # noqa: F401  (cat es una fixture)
from test_instalar import dest, export  # noqa: F401
from test_instalar_heredado import EJEMPLO, INFO, JUNK, files_of, zip_dir

LEGACY = dict(autores="Ana Pérez:owner", licencia="CC-BY-4.0", origen="propia")


@pytest.fixture(autouse=True)
def _idioma():
    set_language("es")
    yield
    set_language("es")


def run(task, timeout=60):
    assert task.start().join(timeout)
    return task


def flow_for(path, root="") -> IZ.LocalZipFlow:
    return IZ.LocalZipFlow(C.OnlineState(urls=[], root=root), str(path))


def propose(flow) -> IZ.LocalZipFlow:
    t = run(flow.task_propose())
    assert t.status == C.DONE, t.error_text
    return flow


def fill(flow, **kw):
    for k, v in {**LEGACY, **kw}.items():
        setattr(flow.datos, k, v)
    assert flow.confirmar_datos() == []


# ------------------------------------------------------------------------------------------------------------------------ qué es el archivo

def test_split_dropped_acepta_solo_zip_sin_mirar_mayusculas():
    assert IZ.split_dropped(["/a/p.zip", "/a/figura.stl", "/b/Q.ZIP", "/c/x.proyecto"]) == (["/a/p.zip", "/b/Q.ZIP"], ["/a/figura.stl", "/c/x.proyecto"])
    assert IZ.split_dropped([]) == ([], []) and IZ.split_dropped(None) == ([], [])


def test_inspect_distingue_paquete_de_zip_heredado(cat, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    info = IZ.inspect_zip(z)
    assert info.kind == IZ.PAQUETE and info.paquete.id and info.linea == "proceres-130" and info.tamano == os.path.getsize(z)
    h = zip_dir(EJEMPLO, tmp_path / "heredado.zip", "proceres-127/")
    info = IZ.inspect_zip(h)
    assert info.kind == IZ.HEREDADO and info.paquete is None and info.linea


def test_inspect_rechaza_lo_que_no_es_un_zip_legible(tmp_path):
    for name, body, frag in (("falta.zip", None, "no es un archivo"), ("figura.stl", b"solid", "no es un .zip"), ("roto.zip", b"no soy un zip", "no es un .zip legible")):
        path = tmp_path / name
        if body is not None:
            path.write_bytes(body)
        with pytest.raises(IZ.ZipImportError, match=frag):
            IZ.inspect_zip(str(path))
    vacio = tmp_path / "vacio.zip"
    with zipfile.ZipFile(vacio, "w") as z:
        z.writestr("notas.txt", "hola")
    with pytest.raises(IZ.ZipImportError, match="paquete.json"):                   # ni paquete ni catálogo heredado
        IZ.inspect_zip(str(vacio))


# ------------------------------------------------------------------------------------------------------------------------ confirmación de un paquete

def test_la_confirmacion_de_un_zip_local_dice_lo_mismo_que_la_de_explorar_y_no_escribe(cat, dest, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    before = I.tree_digest(dest.root)
    flow = flow_for(z, dest.root)
    assert not flow.pide_datos and flow.stage == IC.INICIO and flow.confirmation is None
    propose(flow)
    c, rows = flow.confirmation, dict(flow.confirmation.rows)
    assert flow.stage == IC.CONFIRMANDO and c.puede_instalar and c.boton == "Instalar" and c.errores == []
    assert c.titulo.startswith("Instalar «") and flow.title and flow.name == flow.zinfo.paquete.id
    assert rows["Autor"] == "Ana Pérez (@owner)" and rows["Licencia"] == "CC-BY-4.0" and rows["Origen"].startswith("propia")
    assert rows["Línea de destino"] == "«proceres-130» (130 mm)" and rows["Carpeta"] == dest.root
    assert rows["Archivo"] == os.path.abspath(z) and rows["Tamaño"] == C.fmt_size(os.path.getsize(z)) and "Tipo" not in rows
    assert c.trae.startswith("Trae 1 pieza(s), 0 receta(s)") and c.trae == flow.proposal.headline()
    secs = {s.titulo: s.lineas for s in c.secciones}
    assert secs["Paquetes que se instalan (1), en este orden"][0].endswith("Ana Pérez · CC-BY-4.0 · propia")
    assert secs["Piezas que se agregan (1)"] == ["brazo-test-01@1"]
    assert "archivo" in c.no_verificado.lower() and "no los verifica" in c.no_verificado       # no pasó por ningún índice: lo dice
    assert I.tree_digest(dest.root) == before                                                   # proponer no escribe


def test_sin_confirmar_no_se_escribe_y_confirmar_instala_todo_o_nada(cat, dest, tmp_path, monkeypatch):
    add_arm(cat)
    z = export(cat, tmp_path)
    flow = propose(flow_for(z, dest.root))
    before = I.tree_digest(dest.root)
    t = run(flow.task_install())                                                                # sin acepto=True
    assert t.status == C.FAILED and "sin confirmar" in t.error_text.lower() and flow.stage == IC.CONFIRMANDO and flow.report is None
    assert I.tree_digest(dest.root) == before
    real = I.apply
    monkeypatch.setattr(I, "apply", lambda *a, **k: (_ for _ in ()).throw(OSError("disco lleno")))
    t = run(flow.task_install(acepto=True))
    assert t.status == C.FAILED and "disco lleno" in t.error_text and flow.stage == IC.FALLO and I.tree_digest(dest.root) == before
    monkeypatch.setattr(I, "apply", real)
    t = run(flow.task_install(acepto=True))                                                     # el plan sigue a la vista: «Instalar» otra vez
    assert t.status == C.DONE and flow.stage == IC.LISTO and t.result is flow.report
    rep = flow.report
    assert rep.carpeta == dest.root and rep.verificar_ok and rep.resumen[0].startswith("Instalado «") and rep.cobertura
    c = Catalog.open(dest.root)
    assert [m.ref for m in c.list(all_versions=True)] == ["brazo-test-01@1"] and c.verify().ok
    reg = I.load_registry(dest.root)["paquetes"][flow.name]
    assert reg["licencia"] == "CC-BY-4.0" and reg["sha256"] and not reg.get("indice")           # un zip local no viene de ningún índice


def test_instala_lo_mismo_que_la_consola(cat, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    a, b = Catalog.create(str(tmp_path / "a"), "proceres-130", 130.0, 0.4), Catalog.create(str(tmp_path / "b"), "proceres-130", 130.0, 0.4)
    flow = propose(flow_for(z, a.root))
    assert run(flow.task_install(acepto=True)).status == C.DONE
    I.apply(I.plan_install(z, b.root))
    assert files_of(a.root) == files_of(b.root)                                                  # mismo árbol, byte por byte


def test_si_el_catalogo_cambia_mientras_se_mira_la_confirmacion_se_vuelve_a_planificar(cat, dest, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    flow = propose(flow_for(z, dest.root))
    assert flow.confirmation.puede_instalar
    I.apply(I.plan_install(z, dest.root))                                                        # alguien instaló lo mismo desde otro lado
    t = run(flow.task_install(acepto=True))
    assert t.status == C.DONE and flow.report.resumen == [tr_nada()]                             # no duplica nada
    assert sorted(m.ref for m in Catalog.open(dest.root).list(all_versions=True)) == ["brazo-test-01@1"]


def tr_nada() -> str:
    from afm.i18n import tr
    return tr("catalog_online.nada_que_instalar")


def test_ya_instalado_dice_que_no_hay_nada_que_agregar(cat, dest, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    I.apply(I.plan_install(z, dest.root))
    c = propose(flow_for(z, dest.root)).confirmation
    assert c.nada_que_hacer and not c.puede_instalar


# ------------------------------------------------------------------------------------------------------------------------ destino y conflictos

def test_sin_catalogo_abierto_el_destino_es_la_carpeta_de_su_linea_y_se_crea(cat, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    flow = propose(flow_for(z))                                                                  # sin catálogo abierto
    want = I.explicit_root("proceres-130")
    c = flow.confirmation
    assert flow.proposal.root == want and c.crea_linea and dict(c.rows)["Línea de destino"] == "línea nueva «proceres-130» (130 mm)" and c.puede_instalar
    assert not os.path.exists(want)                                                              # proponer no crea nada
    assert run(flow.task_install(acepto=True)).status == C.DONE
    assert Catalog.open(want).verify().ok


def test_un_catalogo_de_otra_altura_ofrece_instalarlo_como_linea_nueva(cat, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    otro = Catalog.create(str(tmp_path / "otro"), "proceres-200", 200.0, 0.4)
    flow = propose(flow_for(z, otro.root))
    c = flow.confirmation
    assert not c.puede_instalar and c.errores and c.ofrece_su_linea
    before = I.tree_digest(otro.root)
    flow.como_linea = True                                                                       # la persona tilda «Instalar como línea nueva»: hay que proponer de nuevo
    propose(flow)
    c = flow.confirmation
    assert c.puede_instalar and c.crea_linea and flow.proposal.root == I.explicit_root("proceres-130")
    assert I.tree_digest(otro.root) == before and not os.path.exists(flow.proposal.root)


def test_un_cambio_de_origen_pide_confirmacion_explicita(cat, dest, tmp_path):
    add_arm(cat)
    z1 = export(cat, tmp_path, "uno.zip", github="owner/uno")
    z2 = export(cat, tmp_path, "dos.zip", github="otro/dos", version="1.0.1")
    I.apply(I.plan_install(z1, dest.root))
    flow = propose(flow_for(z2, dest.root))
    c = flow.confirmation
    assert c.pide_origen and not c.puede_instalar and any("origen" in a.lower() for a in c.avisos)
    assert run(flow.task_install(acepto=True)).status == C.FAILED
    flow.confirmar_origen = True
    assert flow.confirmation.puede_instalar and run(flow.task_install(acepto=True)).status == C.DONE


def test_un_zip_roto_o_que_no_es_un_paquete_no_llega_al_dialogo(tmp_path):
    with pytest.raises(IZ.ZipImportError):
        flow_for(tmp_path / "no-existe.zip")


# ------------------------------------------------------------------------------------------------------------------------ zip heredado (D07)

def test_un_zip_heredado_pide_autor_licencia_y_origen_antes_de_proponer(tmp_path):
    h = zip_dir(EJEMPLO, tmp_path / "heredado.zip", "", extra={**JUNK})
    flow = flow_for(h)
    assert flow.heredado and flow.pide_datos and flow.title == os.path.basename(h)
    t = run(flow.task_propose())
    assert t.status == C.FAILED and "faltan" in t.error_text.lower() and flow.proposal is None          # sin los datos no se propone
    flow.datos.autores, flow.datos.id, flow.datos.version = "", "Mal Id", "1.0"
    problems = flow.confirmar_datos()
    assert flow.pide_datos and len(problems) == 5 and any("autor" in p.lower() for p in problems) and any("id" in p.lower() for p in problems)
    fill(flow)
    assert not flow.pide_datos


def test_la_confirmacion_de_un_zip_heredado_lo_avisa_y_muestra_lo_que_se_descarta(tmp_path):
    h = zip_dir(EJEMPLO, tmp_path / "heredado.zip", "proceres-127/", extra={**JUNK})
    flow = flow_for(h, str(tmp_path / "nuevo"))
    fill(flow, autores="Ana Pérez:owner, Beto")
    propose(flow)
    c, rows = flow.confirmation, dict(flow.confirmation.rows)
    assert c.puede_instalar and c.crea_linea and rows["Tipo"].startswith("catálogo comprimido a mano")
    assert rows["Autor"] == "Ana Pérez (@owner), Beto" and rows["Licencia"] == "CC-BY-4.0"
    assert any("no trae ficha de paquete" in a for a in c.avisos)
    secs = {s.titulo: s.lineas for s in c.secciones}
    assert set(secs["Archivos del zip heredado que no se instalan (%d)" % len(set(JUNK) | {"CATALOGO.md"})]) == set(JUNK) | {"CATALOGO.md"}
    assert flow.title == flow.zinfo.linea or flow.title                                          # ya hay paquete: el título sale de él


def test_un_zip_heredado_se_instala_sin_perder_nada_y_deja_la_carpeta_de_trabajo_limpia(tmp_path):
    h = zip_dir(EJEMPLO, tmp_path / "heredado.zip", "proceres-127/", extra={**JUNK})
    dest = tmp_path / "destino"
    flow = flow_for(h, str(dest))
    fill(flow)
    propose(flow)
    work = flow._work
    assert os.path.isdir(work)
    assert run(flow.task_install(acepto=True)).status == C.DONE and flow.stage == IC.LISTO
    c = Catalog.open(str(dest))
    assert c.verify().ok and files_of(str(dest)) == files_of(EJEMPLO)                            # cada STL y cada ficha, byte por byte
    reg = I.load_registry(str(dest))["paquetes"][flow.name]
    assert reg["licencia"] == "CC-BY-4.0" and reg["heredado_sha256"]                             # lo propio de D07 llega al registro
    flow.close()
    assert not os.path.exists(work) and flow._work == ""
    flow.close()                                                                                  # cerrar dos veces no falla


def test_cambiar_los_datos_vuelve_al_formulario_sin_escribir(tmp_path):
    h = zip_dir(EJEMPLO, tmp_path / "heredado.zip", "", extra={**JUNK})
    dest = tmp_path / "destino"
    flow = flow_for(h, str(dest))
    fill(flow)
    propose(flow)
    assert flow.stage == IC.CONFIRMANDO and not flow.pide_datos
    flow.volver_a_datos()
    assert flow.pide_datos and flow.stage == IC.INICIO and flow.proposal is None and flow.confirmation is None
    fill(flow, licencia="CC0-1.0")
    propose(flow)
    assert dict(flow.confirmation.rows)["Licencia"] == "CC0-1.0" and not os.path.exists(dest)
    flow.close()


def test_un_paquete_normal_no_pide_datos_ni_vuelve_al_formulario(cat, dest, tmp_path):
    add_arm(cat)
    flow = propose(flow_for(export(cat, tmp_path), dest.root))
    flow.volver_a_datos()                                                                        # no es un zip heredado: no hay formulario
    assert not flow.pide_datos and flow.stage == IC.CONFIRMANDO and not flow.heredado
    flow.close()


def test_cancelar_mientras_se_lee_no_deja_nada(cat, dest, tmp_path):
    add_arm(cat)
    flow = flow_for(export(cat, tmp_path), dest.root)
    before = I.tree_digest(dest.root)
    t = flow.task_propose()
    t.cancel()
    run(t)
    assert t.status == C.CANCELLED and flow.stage == IC.CANCELADO and I.tree_digest(dest.root) == before


def test_las_claves_de_idioma_existen_en_los_tres_idiomas(cat, dest, tmp_path):
    from afm.i18n import tr
    add_arm(cat)
    flow = propose(flow_for(export(cat, tmp_path), dest.root))
    for lang in ("es", "en", "pt"):
        set_language(lang)
        c = flow.confirmation
        assert "%{" not in c.no_verificado and c.titulo and "import_zip." not in c.no_verificado
        assert all("import_zip." not in k and "install_confirm." not in k for k, _v in c.rows)
        assert tr("import_zip_window.menu_importar") != "import_zip_window.menu_importar"


# ------------------------------------------------------------------------------------------------------------------------ lo que agrega el cierre de D22

def test_las_claves_de_d22_estan_en_los_tres_idiomas_y_sin_marcadores_sueltos():
    """Toda clave `import_zip.*` / `import_zip_window.*` que usa el código existe en es, en y pt, y ninguna deja un `%{…}` sin reemplazar al pedirla con sus datos."""
    import json
    import re

    root = os.path.join(os.path.dirname(__file__), "..", "src", "afm")
    src = "".join(open(os.path.join(root, "gui", f), encoding="utf-8").read() for f in ("import_zip.py", "import_zip_window.py", "install_confirm_window.py"))
    keys = sorted({(g, k) for g, k in re.findall(r'tr\("(import_zip(?:_window)?)\.([a-z_0-9]+)"', src)})
    assert len(keys) >= 20
    for lang in ("es", "en", "pt"):
        d = json.load(open(os.path.join(root, "locales", f"{lang}.json"), encoding="utf-8"))
        assert [f"{g}.{k}" for g, k in keys if not d.get(g, {}).get(k)] == [], lang
    params = {"nombre": "x.zip", "ruta": "/r", "max": 64, "n": 2, "archivos": "a, b"}
    from afm.i18n import tr
    for lang in ("es", "en", "pt"):
        set_language(lang)
        for g, k in keys:
            assert "%{" not in tr(f"{g}.{k}", **params), (lang, g, k)


def test_los_opcionales_invalidos_se_vacian_y_los_validos_se_conservan():
    d = IZ.LegacyData(autores="Ana", licencia="MIT", origen="propia", id="Mal Id", version="1.0")
    assert len(d.problems()) == 2
    d.clear_invalid_optionals()
    assert (d.id, d.version) == ("", "") and d.problems() == []
    ok = IZ.LegacyData(autores="Ana", licencia="MIT", origen="propia", id="mi-linea", version="2.1.0")
    ok.clear_invalid_optionals()
    assert (ok.id, ok.version) == ("mi-linea", "2.1.0")


def test_el_flujo_del_indice_y_el_local_comparten_los_mismos_ganchos(cat, dest, tmp_path):
    """El diálogo no distingue de dónde viene el paquete: `LocalZipFlow` solo redefine los ganchos de `InstallFlow` y la propuesta local se declara planificada."""
    add_arm(cat)
    flow = propose(flow_for(export(cat, tmp_path), dest.root))
    assert isinstance(flow, IC.InstallFlow) and flow.proposal.planificado and not flow.pide_datos
    for hook in ("_make_proposal", "_write", "_fallback_rows", "_source_rows", "_package_lines", "_entry_warnings", "_confirmation_title", "_unverified_text"):
        assert getattr(IZ.LocalZipFlow, hook) is not getattr(IC.InstallFlow, hook), hook
    assert IZ.LocalZipFlow.title is not IC.InstallFlow.title and flow.preparing_text and "import_zip." not in flow.preparing_text

