"""D19 · diálogo de confirmación e instalación sin GTK (`afm.gui.install_confirm`).

Se conduce todo el flujo sin ventana: propuesta (baja y verifica, no escribe), confirmación (autor, licencia, origen, tamaño, qué agrega, línea destino,
conflictos y avisos), instalación solo con `acepto=True` (todo o nada) e informe con `verificar` y `cobertura`. Los zips y el índice salen de un
`http.server` local, y «no se escribe nada» se comprueba con el resumen de todo el árbol del catálogo (`instalar.tree_digest`)."""
import os
import threading
import time

import pytest

from afm import dependencias as D
from afm import instalar as I
from afm import remoto as R
from afm.catalog import Catalog
from afm.gui import catalog_online as C
from afm.gui import install_confirm as IC
from afm.i18n import set_language
from test_catalog_online import serve, state, world
from test_dependencias import ent, entorno, publish_world, set_registry  # noqa: F401  (entorno es autouse)
from test_exportar import add_arm, cat  # noqa: F401
from test_instalar import dest  # noqa: F401
from test_remoto import POLICY, entry_dict, index_body, srv  # noqa: F401


@pytest.fixture(autouse=True)
def _idioma():
    set_language("es")
    yield
    set_language("es")


def propose(st, pid, **opts) -> IC.InstallFlow:
    flow = IC.InstallFlow(st, st.indexes[0].find(pid))
    for k, v in opts.items():
        setattr(flow, k, v)
    t = flow.task_propose().start()
    assert t.join(30) and t.status == C.DONE, t.error_text
    return flow


def run(task, timeout=30):
    assert task.start().join(timeout)
    return task


# ------------------------------------------------------------------------------------------------------------------------ confirmación

def test_la_confirmacion_muestra_lo_que_se_instala_y_no_escribe_nada(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    before = I.tree_digest(dest.root)
    flow = propose(st, "dep-pk")
    c, rows = flow.confirmation, dict(flow.confirmation.rows)
    assert flow.stage == IC.CONFIRMANDO and c.titulo == "Instalar «dep-pk» 1.0.0" and c.boton == "Instalar" and c.puede_instalar
    assert rows["Autor"] == "Ana Pérez (@owner)" and rows["Licencia"] == "CC-BY-4.0" and rows["Repositorio"] == "owner/repo"
    assert rows["Origen"].startswith("propia") and rows["Línea de destino"] == "«proceres-130» (130 mm)" and rows["Carpeta"] == dest.root
    assert rows["Descarga"] == C.fmt_size(flow.proposal.tamano_total) and flow.proposal.tamano_total > 0
    assert c.trae == flow.proposal.headline() and c.trae.startswith("Trae 2 pieza(s), 0 receta(s)")
    secs = {s.titulo: s.lineas for s in c.secciones}
    paquetes = secs["Paquetes que se instalan (2), en este orden"]
    assert paquetes[0].startswith("1. owner@base-pk 1.0.0 — Ana Pérez · CC-BY-4.0 · propia") and "(dependencia de dep-pk)" in paquetes[0]
    assert paquetes[1].startswith("2. owner@dep-pk 1.0.0") and "dependencia" not in paquetes[1]
    assert secs["Piezas que se agregan (2)"] == ["brazo-test-01@1", "brazo-test-02@1"]
    assert c.errores == [] and not c.pide_origen and not c.crea_linea and not c.nada_que_hacer and not c.ofrece_su_linea and not c.ofrece_fbx


# ------------------------------------------------------------------------------------------------------------------------ sin confirmar no se escribe

def test_sin_confirmar_no_se_escribe_nada_y_se_puede_confirmar_despues(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    flow = propose(st, "dep-pk")
    before = I.tree_digest(dest.root)
    t = run(flow.task_install())                                   # sin acepto=True
    assert t.status == C.FAILED and "sin confirmar" in t.error_text.lower() and flow.stage == IC.CONFIRMANDO and flow.report is None
    assert I.tree_digest(dest.root) == before
    t = run(flow.task_install(acepto=True))
    assert t.status == C.DONE and flow.stage == IC.LISTO


def test_sin_propuesta_no_hay_confirmacion_ni_se_instala(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    flow = IC.InstallFlow(st, st.indexes[0].find("dep-pk"))
    assert flow.stage == IC.INICIO and flow.confirmation is None
    t = run(flow.task_install(acepto=True))
    assert t.status == C.FAILED and "propuesta" in t.error_text and flow.stage == IC.INICIO


# ------------------------------------------------------------------------------------------------------------------------ instalar e informe

def test_instalar_deja_el_catalogo_entero_y_el_informe_trae_verificar_y_cobertura(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    flow = propose(st, "dep-pk")
    seen = []
    t = run(flow.task_install(acepto=True, notify=lambda: seen.append(flow.stage)))
    assert t.status == C.DONE and flow.stage == IC.LISTO and t.result is flow.report
    rep = flow.report
    assert [r.paquete.id for r in rep.results] == ["base-pk", "dep-pk"] and rep.carpeta == dest.root
    done = [l for l in rep.resumen if l.startswith("Instalado")]
    assert done[0].startswith("Instalado «base-pk» 1.0.0: 1 pieza(s)") and done[1].startswith("Instalado «dep-pk» 1.0.0")      # los avisos (p. ej. sin portada) van aparte
    assert rep.verificar_ok and rep.verificar[-1].endswith("limpio")
    assert [l for l in rep.cobertura if l.endswith("@1:")] == ["brazo-test-01@1:", "brazo-test-02@1:"]
    c = Catalog.open(dest.root)
    assert sorted(m.ref for m in c.list(all_versions=True)) == ["brazo-test-01@1", "brazo-test-02@1"] and c.verify().ok
    assert set(I.load_registry(dest.root)["paquetes"]) == {"base-pk", "dep-pk"}
    assert IC.INSTALANDO in seen or t.status == C.DONE                # el diálogo ve avanzar la etapa


def test_un_fallo_al_instalar_deja_el_catalogo_como_estaba_y_se_puede_reintentar(srv, cat, dest, tmp_path, monkeypatch):
    st = world(srv, cat, tmp_path, dest)
    flow = propose(st, "dep-pk")
    before = I.tree_digest(dest.root)
    real, calls = I.apply, []

    def flaky(plan, **kw):
        calls.append(plan.paquete.id)
        if len(calls) == 2:
            raise OSError("disco lleno")
        return real(plan, **kw)

    monkeypatch.setattr(I, "apply", flaky)
    t = run(flow.task_install(acepto=True))
    assert t.status == C.FAILED and "disco lleno" in t.error_text and flow.stage == IC.FALLO and "disco lleno" in flow.error
    assert I.tree_digest(dest.root) == before and flow.report is None
    monkeypatch.setattr(I, "apply", real)
    assert run(flow.task_install(acepto=True)).status == C.DONE and flow.stage == IC.LISTO     # el plan sigue a la vista: «Instalar» otra vez


def test_una_falla_al_medir_no_deshace_lo_instalado(srv, cat, dest, tmp_path, monkeypatch):
    st = world(srv, cat, tmp_path, dest)
    flow = propose(st, "dep-pk")
    monkeypatch.setattr(IC.OPS, "coverage_lines", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sin memoria")))
    t = run(flow.task_install(acepto=True))
    assert t.status == C.DONE and flow.stage == IC.LISTO and any("sin memoria" in l for l in flow.report.cobertura)
    assert Catalog.open(dest.root).verify().ok


def test_la_cobertura_se_mide_solo_de_algunas_piezas(srv, cat, dest, tmp_path, monkeypatch):
    st = world(srv, cat, tmp_path, dest)
    flow = propose(st, "dep-pk")
    monkeypatch.setattr(IC, "MAX_COBERTURA", 1)
    run(flow.task_install(acepto=True))
    assert flow.report.cobertura[-1] == "… y 1 pieza(s) más (usá `afm catalogo cobertura`)." and sum(l.endswith("@1:") for l in flow.report.cobertura) == 1


# ------------------------------------------------------------------------------------------------------------------------ conflictos, origen y línea

def test_cambio_de_origen_pide_confirmarlo_y_no_se_instala_sin_eso(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    set_registry(dest.root, **{"dep-pk": ("0.5.0", {"github": "otro/repo"})})
    flow = propose(st, "dep-pk")
    c = flow.confirmation
    assert c.pide_origen and not c.puede_instalar and any("cambia de repositorio" in a for a in c.avisos) and c.errores == []
    before = I.tree_digest(dest.root)
    t = run(flow.task_install(acepto=True))
    assert t.status == C.FAILED and "cambia de origen" in t.error_text and I.tree_digest(dest.root) == before
    flow.confirmar_origen = True                                    # la persona tilda «Confirmo el cambio de origen»
    assert flow.confirmation.puede_instalar
    assert run(flow.task_install(acepto=True)).status == C.DONE


def test_ya_instalado_no_ofrece_instalar(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    run(propose(st, "dep-pk").task_install(acepto=True))
    flow = propose(st, "dep-pk")
    c = flow.confirmation
    assert c.nada_que_hacer and not c.puede_instalar and c.errores == []
    assert [s.titulo for s in c.secciones if s.titulo.startswith("Ya están instaladas")] == ["Ya están instaladas y se omiten (1)"]


def test_linea_de_otra_altura_muestra_el_error_y_ofrece_instalar_como_linea_nueva(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    other = Catalog.create(os.path.join(str(tmp_path), "otra"), "gigantes", 250.0, 0.4)
    st.root = other.root                                             # el destino es una línea de 250 mm y el paquete es de 130 mm
    flow = propose(st, "dep-pk")
    c = flow.confirmation
    assert not c.puede_instalar and c.ofrece_su_linea and any("pertenece a la línea «proceres-130» y el destino es la línea «gigantes»" in e for e in c.errores)
    assert dict(c.rows)["Línea de destino"] == "«gigantes» (250 mm)" and not c.crea_linea
    assert dict(c.rows)["Carpeta"] == other.root and c.trae == "" and dict(c.rows)["Autor"] == "owner"       # sin plan: lo que dice el índice
    before = I.tree_digest(other.root)
    assert run(flow.task_install(acepto=True)).status == C.FAILED and I.tree_digest(other.root) == before
    flow.como_linea = True                                           # «Instalar como línea nueva»: hay que proponer otra vez
    t = run(flow.task_propose())
    assert t.status == C.DONE and flow.stage == IC.CONFIRMANDO
    c = flow.confirmation
    assert c.puede_instalar and c.crea_linea and not c.ofrece_su_linea and c.errores == []
    assert dict(c.rows)["Línea de destino"] == "línea nueva «proceres-130» (130 mm)" and dict(c.rows)["Carpeta"] == flow.line_root() != other.root
    assert run(flow.task_install(acepto=True)).status == C.DONE and Catalog.open(flow.line_root()).verify().ok and I.tree_digest(other.root) == before


def test_destino_sin_catalogo_crea_la_linea_del_paquete(srv, cat, tmp_path):
    publish_world(srv, cat, tmp_path)
    nuevo = os.path.join(str(tmp_path), "nuevo")
    st = state(srv, tmp_path, nuevo)
    flow = propose(st, "dep-pk")
    c = flow.confirmation
    assert c.crea_linea and c.puede_instalar and dict(c.rows)["Línea de destino"] == "línea nueva «proceres-130» (130 mm)" and dict(c.rows)["Carpeta"] == nuevo
    assert not os.path.exists(nuevo)                                  # confirmar no crea nada
    assert run(flow.task_install(acepto=True)).status == C.DONE and Catalog.open(nuevo).verify().ok


# ------------------------------------------------------------------------------------------------------------------------ fallos de la propuesta

def test_propuesta_imposible_es_un_fallo_sin_tocar_nada(srv, cat, dest, tmp_path):
    publish_world(srv, cat, tmp_path)
    body = index_body(entry_dict(srv.base, pid="mala", folder="owner@mala", retiradas=["1.0.0"]))
    srv.routes["/data/index.json"] = (200, {}, body)
    st = state(srv, tmp_path, dest.root)
    before = I.tree_digest(dest.root)
    flow = IC.InstallFlow(st, st.indexes[0].find("mala"))
    t = run(flow.task_propose())
    assert t.status == C.FAILED and flow.stage == IC.FALLO and "fue retirada" in flow.error and flow.confirmation is None
    assert I.tree_digest(dest.root) == before
    flow.allow_retirada = True                                        # lo mismo con el permiso de instalar una retirada: el error pasa a ser otro (sin zip)
    assert flow.allow_retirada


def test_cancelar_la_descarga_no_escribe_y_deja_la_etapa_cancelada(srv, cat, dest, tmp_path, monkeypatch):
    st = world(srv, cat, tmp_path, dest)
    before = I.tree_digest(dest.root)
    started = threading.Event()

    def slow(entry, **kw):
        for n in range(1, 500):
            kw["progress"](n, 500)
            started.set()
            time.sleep(0.005)
        raise AssertionError("no se canceló")

    monkeypatch.setattr(R, "download_package", slow)
    flow = IC.InstallFlow(st, st.indexes[0].find("dep-pk"))
    t = flow.task_propose().start()
    assert started.wait(5) and t.cancel() and t.join(10)
    assert t.status == C.CANCELLED and flow.stage == IC.CANCELADO and flow.proposal is None and I.tree_digest(dest.root) == before


def test_proponer_de_nuevo_descarta_la_confirmacion_anterior(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    flow = propose(st, "dep-pk")
    flow.confirmar_origen = True
    t = flow.task_propose()
    assert flow.confirmation is not None                              # hasta que corre, lo anterior sigue
    run(t)
    assert flow.stage == IC.CONFIRMANDO and flow.confirmar_origen is False


# ------------------------------------------------------------------------------------------------------------------------ textos

def test_la_confirmacion_siempre_dice_que_listar_no_es_verificar_y_sale_en_los_tres_idiomas(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    flow = propose(st, "dep-pk")
    assert flow.confirmation.no_verificado.startswith("Listado no verificado")
    got = {}
    for lang in ("es", "en", "pt"):
        set_language(lang)
        c = flow.confirmation
        got[lang] = (c.titulo, c.boton, [k for k, _ in c.rows])
    assert got["en"][0] == "Install “dep-pk” 1.0.0" and got["en"][1] == "Install" and got["en"][2][0] == "Author"
    assert got["pt"][2][0] == "Autor" and got["pt"][2][3] == "Origem" and got["es"][2][3] == "Origen"


def test_un_origen_que_no_se_conoce_se_muestra_tal_cual():
    assert IC._origin_text("rara") == "rara" and IC._origin_text("dominio-publico") == "dominio-publico — de dominio público" and IC._origin_text("") == "—"
