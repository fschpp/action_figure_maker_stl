"""D17 · lógica de «Explorar catálogos en línea…» sin GTK (`afm.gui.catalog_online`).

Se conduce todo el flujo sin ventana: leer índices (también sin red), filtrar y ordenar tarjetas, ficha, compatibilidad, plan de instalación con
confirmación obligatoria, y las tareas en hilo con progreso y cancelación. Los zips y el índice salen de un `http.server` local."""
import json
import os
import threading
import time

import pytest

from afm import dependencias as D
from afm import instalar as I
from afm import remoto as R
from afm.catalog import Catalog
from afm.gui import catalog_online as C
from afm.i18n import set_language, tr
from test_dependencias import BASE, ent, entorno, publish_world, set_registry  # noqa: F401  (entorno es autouse)
from test_exportar import add_arm, cat  # noqa: F401
from test_instalar import dest  # noqa: F401
from test_remoto import POLICY, entry_dict, index_body, release, srv  # noqa: F401


@pytest.fixture(autouse=True)
def _idioma():
    set_language("es")
    yield
    set_language("es")


def net(tmp_path):
    return dict(policy=POLICY, cache=str(tmp_path / "cache"), retries=1, sleep=lambda s: None)


def serve(srv, *entries, **over):
    srv.routes["/data/index.json"] = (200, {}, index_body(*entries, **over))


def state(srv, tmp_path, root="", **kw) -> C.OnlineState:
    st = C.OnlineState([srv.base], root, **net(tmp_path), **kw)
    st.refresh()
    return st


def lista(*specs):
    """Entradas de índice sin zips reales (solo para listar): `(id, over)`."""
    return [ent(pid, **over) for pid, over in specs]


def folders(cards):
    return [c.entry.id for c in cards]


# ------------------------------------------------------------------------------------------------------------------------ lista

def test_lista_ordena_por_descargas_y_deja_sin_contador_al_final(srv, tmp_path):
    serve(srv, ent("a", descargas=None, nombre="A"), ent("b", descargas={"total": 5}, nombre="B"), ent("c", descargas={"total": 40}, nombre="C"))
    st = state(srv, tmp_path)
    assert folders(st.cards()) == ["c", "b", "a"]
    st.filters.orden = "nombre"
    assert folders(st.cards()) == ["a", "b", "c"]
    assert C.downloads_text(st.indexes[0].find("a")) == "sin contador" and "40" in C.downloads_text(st.indexes[0].find("c"))


def test_filtros_texto_tipo_linea_licencia_y_categoria(srv, tmp_path):
    serve(srv,
          ent("guemes", nombre="Güemes", tipo="heroe", licencia="CC-BY-4.0", categorias=["heroes"], linea="proceres-130"),
          ent("espada", nombre="Espada de época", tipo="piezas", licencia="CC0-1.0", categorias=["armas"], linea="proceres-130"),
          ent("otra", nombre="Otra", tipo="piezas", licencia="CC-BY-4.0", categorias=["armas"], linea="chicos-90"))
    st = state(srv, tmp_path)
    assert set(st.options()["tipo"]) == {"heroe", "piezas"} and set(st.options()["linea"]) == {"proceres-130", "chicos-90"}
    st.filters.text = "espada"
    assert folders(st.cards()) == ["espada"]
    st.filters.text, st.filters.tipo = "", "piezas"
    assert set(folders(st.cards())) == {"espada", "otra"}
    st.filters.linea = "chicos-90"
    assert folders(st.cards()) == ["otra"]
    st.filters.linea, st.filters.tipo, st.filters.licencia = "", "", "cc0-1.0"
    assert folders(st.cards()) == ["espada"]
    st.filters.licencia, st.filters.categoria = "", "heroes"
    assert folders(st.cards()) == ["guemes"]


def test_estados_de_las_tarjetas(srv, tmp_path, dest):
    serve(srv, ent("nuevo"), ent("al-dia"), ent("viejo", "1.2.0"), ent("mala", "1.0.0", versions=[("0.9.0", False)], retiradas=["0.9.0"]),
          ent("caido", update_check="inaccesible"), ent("vetado"), ent("exp", experimental=True),
          bloqueados=[{"id": "vetado", "motivo": "reclamo", "fecha": "2026-11-20"}])
    set_registry(dest.root, **{"al-dia": ("1.0.0", {"github": "owner/al-dia"}), "viejo": ("1.0.0", {"github": "owner/viejo"}),
                               "mala": ("0.9.0", {"github": "owner/mala"})})
    st = state(srv, tmp_path, dest.root)
    got = {c.entry.id: c for c in st.cards()}
    assert set(got) == {"nuevo", "al-dia", "viejo", "mala", "exp"}                 # inaccesible y bloqueado se ocultan
    assert got["nuevo"].estado == C.NO_INSTALADO and got["nuevo"].instalable
    assert got["al-dia"].estado == C.INSTALADO and got["al-dia"].texto_estado == "Instalado 1.0.0" and got["al-dia"].instalada == "1.0.0"
    assert got["viejo"].estado == C.ACTUALIZACION and got["viejo"].texto_estado == "Actualización 1.2.0"
    assert got["mala"].estado == C.RETIRADA and "retirada" in got["mala"].texto_estado
    assert got["exp"].experimental
    st.filters.incluir_ocultas = True
    ocultos = {c.entry.id: c for c in st.cards()}
    assert ocultos["caido"].estado == C.INACCESIBLE and not ocultos["caido"].instalable
    assert ocultos["vetado"].estado == C.BLOQUEADO and not ocultos["vetado"].instalable


def test_solo_compatibles_mira_el_motor_y_la_altura_de_la_linea(srv, tmp_path, dest):
    big = ent("grande", linea="gigantes")
    big["linea"] = {"nombre": "gigantes", "altura_mm": 250.0}
    serve(srv, ent("ok"), ent("motor-nuevo", motor=">=99.0.0"), big)
    st = state(srv, tmp_path, dest.root)
    got = {c.entry.id: c for c in st.cards()}
    assert got["ok"].incompatible == ""
    assert "motor" in got["motor-nuevo"].incompatible and ">=99.0.0" in got["motor-nuevo"].incompatible
    assert "250 mm" in got["grande"].incompatible and "130 mm" in got["grande"].incompatible and "línea nueva" in got["grande"].incompatible
    st.filters.solo_compatibles = True
    assert folders(st.cards()) == ["ok"]
    nuevo = state(srv, tmp_path, dest.root, engine_version="99.1.0+local")
    nuevo.filters.solo_compatibles = True
    assert "motor-nuevo" in folders(nuevo.cards())                                  # con un motor que lo cumple, el filtro lo deja


def test_sin_catalogo_de_destino_la_altura_no_se_compara(srv, tmp_path):
    big = ent("grande")
    big["linea"] = {"nombre": "gigantes", "altura_mm": 250.0}
    serve(srv, big)
    st = state(srv, tmp_path, str(tmp_path / "no-existe"))
    assert st.cards()[0].incompatible == ""


# ------------------------------------------------------------------------------------------------------------------------ índices: sin red, caídos, avisos

def test_sin_red_usa_el_indice_guardado_y_lo_avisa(srv, tmp_path):
    serve(srv, ent("a"))
    online = state(srv, tmp_path)
    assert not online.sin_red and online.errors == []
    off = C.OnlineState([srv.base], "", offline=True, **net(tmp_path))
    off.refresh()
    assert off.sin_red and folders(off.cards()) == ["a"]
    notes = off.notices()
    assert any(n.startswith("Sin red: se usa el índice guardado") for n in notes) and notes[-1].startswith("Listado no verificado")
    assert all("Sin red" not in n for n in online.notices())


def test_sin_red_y_sin_copia_guardada_es_un_error_legible_no_una_excepcion(srv, tmp_path):
    st = C.OnlineState([srv.base], "", offline=True, **net(tmp_path))
    st.refresh()
    assert st.cards() == [] and len(st.errors) == 1 and srv.base in st.errors[0]
    assert st.notices()[-1].startswith("Listado no verificado")


def test_un_indice_caido_no_tapa_a_los_demas_y_sin_indices_lo_dice(srv, tmp_path):
    serve(srv, ent("a"))
    st = C.OnlineState([srv.base + "/nada-aca", srv.base], "", **net(tmp_path))
    st.refresh()
    assert folders(st.cards()) == ["a"] and len(st.errors) == 1
    vacio = C.OnlineState([], "", **net(tmp_path))
    vacio.refresh()
    assert vacio.errors == ["No hay índices configurados."]


def test_avisos_del_indice_se_muestran_y_las_entradas_rotas_se_saltan(srv, tmp_path):
    serve(srv, ent("bien"), {"id": 3})
    st = state(srv, tmp_path)
    assert folders(st.cards()) == ["bien"] and any("entrada" in n.lower() or "salt" in n.lower() for n in st.notices()[:-1])


def test_mismo_paquete_en_dos_indices_se_selecciona_por_indice_y_carpeta(srv, tmp_path):
    serve(srv, ent("a", nombre="A uno"))
    otro = type(srv)()
    try:
        otro.routes["/data/index.json"] = (200, {}, index_body(ent("a", nombre="A dos")))
        st = C.OnlineState([srv.base, otro.base], "", policy=R.Policy(hosts=("127.0.0.1",), allow_loopback_http=True), cache=str(tmp_path / "cache"), retries=1, sleep=lambda s: None)
        st.refresh()
        a1, a2 = st.entries()
        assert a1.indice != a2.indice
        st.select(a2)
        assert st.selected.nombre == "A dos"
        st.select(a1)
        assert st.selected.nombre == "A uno"
        st.select(None)
        assert st.selected is None and st.detail() is None
    finally:
        otro.close()


# ------------------------------------------------------------------------------------------------------------------------ ficha

def test_ficha_con_filas_avisos_y_razon(srv, tmp_path, dest):
    e = ent("guemes", nombre="Güemes", tipo="heroe", requiere=["base@^1"], conflicts=["otro"], experimental=True, retiradas=["0.9.0"], descargas={"total": 12},
            descripcion="Héroe de prueba")
    e["motor"] = ">=99.0.0"
    serve(srv, e, ent("vetado"), bloqueados=[{"id": "vetado", "motivo": "reclamo"}])
    st = state(srv, tmp_path, dest.root)
    d = st.detail(st.indexes[0].find("guemes"))
    rows = dict(d.rows)
    assert d.titulo == "Güemes" and d.version == "1.0.0" and d.estado == "No instalado" and d.descripcion == "Héroe de prueba"
    assert rows["Autor"] == "owner" and rows["Licencia"] == "CC-BY-4.0" and rows["Línea"] == "proceres-130 (130 mm)"
    assert rows["Requiere"] == "base@^1" and rows["No convive con"] == "otro" and rows["Descargas"] == "12 descargas" and rows["Retiradas"] == "0.9.0"
    assert any("motor" in a for a in d.avisos) and any("experimental" in a for a in d.avisos) and d.avisos[-1].startswith("Listado no verificado")
    assert d.puede_instalar and d.razon == ""
    st.filters.incluir_ocultas = True
    v = st.detail(st.indexes[0].find("vetado"))
    assert not v.puede_instalar and "no se puede instalar" in v.razon and "reclamo" in v.razon


def test_los_textos_salen_en_el_idioma_pedido(srv, tmp_path):
    serve(srv, ent("a"))
    st = state(srv, tmp_path)
    set_language("en")
    assert st.cards()[0].texto_estado == "Not installed" and st.notices()[-1].startswith("Unverified listing")
    set_language("pt")
    assert st.cards()[0].texto_estado == "Não instalado"
    assert C.fmt_size(0) == tr("catalog_online.tam_desconocido") == "tamanho desconhecido"


def test_fmt_size():
    set_language("es")
    assert C.fmt_size(512) == "512 B" and C.fmt_size(2048) == "2,0 KB" and C.fmt_size(19_000_000) == "18,1 MB" and C.fmt_size(0) == "tamaño desconocido"


# ------------------------------------------------------------------------------------------------------------------------ compatibilidad (fichas, sin bajar STL)

def test_compatibilidad_sin_catalogo_o_sin_version_se_explica(srv, tmp_path):
    serve(srv, ent("a"))
    st = state(srv, tmp_path, str(tmp_path / "no-hay"))
    assert "No hay un catálogo" in st.compat_lines(st.indexes[0].find("a"))[0]
    e = st.indexes[0].find("a")
    e.latest = None
    assert "no tiene una versión instalable" in st.compat_lines(e)[0]


def test_compatibilidad_mide_las_fichas_del_paquete(srv, cat, dest, tmp_path):
    from afm import indice as IX
    from test_dependencias import make_pkg

    add_arm(cat)
    z, body = make_pkg(cat, tmp_path, "base-pk", "brazo-test-01")
    fichas = json.dumps(IX.fichas_from_zip(z)).encode()
    e = entry_dict(srv.base, body, pid="base-pk", folder="owner@base-pk", nombre="Base")
    e["latest"] = release(srv.base, body, path="/zips/base.zip")
    srv.routes["/zips/base.zip"] = (200, {}, body)
    srv.routes["/data/fichas/owner@base-pk-1.0.0.json"] = (200, {}, fichas)
    serve(srv, e)
    st = state(srv, tmp_path, dest.root)
    t = st.task_compat(st.indexes[0].find("base-pk")).start()
    assert t.join(20) and t.status == C.DONE and t.error is None
    assert any("brazo-test-01" in l or "pieza" in l.lower() for l in t.result)
    assert [p for p in srv.log if p[0].endswith(".zip")] == []                    # solo se bajaron las fichas, ningún zip


# ------------------------------------------------------------------------------------------------------------------------ plan e instalación

def world(srv, cat, tmp_path, dest, **kw):
    publish_world(srv, cat, tmp_path)
    return state(srv, tmp_path, dest.root, **kw)


def test_propuesta_con_dependencias_describe_y_no_escribe(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    before = I.tree_digest(dest.root)
    prop = st.propose(st.indexes[0].find("dep-pk"))
    assert prop.ok and not prop.nada_que_hacer and not prop.cambia_origen and prop.errors == []
    assert [s.entry.id for s in prop.pasos] == ["base-pk", "dep-pk"]
    assert sorted(prop.piezas) == ["brazo-test-01@1", "brazo-test-02@1"] and prop.recetas == []
    assert prop.headline().startswith("Trae 2 pieza(s), 0 receta(s)") and prop.tamano_total > 0
    assert any("owner@base-pk" in l for l in prop.lines()) and any("dependencia de dep-pk" in l or "(dependencia" in l for l in prop.lines())
    assert I.tree_digest(dest.root) == before


def test_sin_confirmar_no_se_instala_nada(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    prop = st.propose(st.indexes[0].find("dep-pk"))
    before = I.tree_digest(dest.root)
    with pytest.raises(I.InstallError, match="sin confirmar"):
        st.install(prop)
    with pytest.raises(I.InstallError, match="sin confirmar"):
        st.install(prop, acepto=False)
    t = st.task_install(prop).start()
    assert t.join(10) and t.status == C.FAILED and "sin confirmar" in t.error_text
    assert I.tree_digest(dest.root) == before


def test_confirmar_instala_todo_o_nada_y_el_informe_lo_cuenta(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    prop = st.propose(st.indexes[0].find("dep-pk"))
    results = st.install(prop, acepto=True)
    assert [r.paquete.id for r in results] == ["base-pk", "dep-pk"]
    c = Catalog.open(dest.root)
    assert sorted(m.ref for m in c.list(all_versions=True)) == ["brazo-test-01@1", "brazo-test-02@1"] and c.verify().ok
    lines = st.result_lines(results)
    assert lines[0].startswith("Instalado «base-pk» 1.0.0: 1 pieza(s)") and sum(l.startswith("Instalado «") for l in lines) == 2
    assert not any(l.startswith("AVISO: AVISO") for l in lines)
    assert st.result_lines([]) == ["Nada que instalar: todo ya está en el catálogo."]
    again = st.propose(st.indexes[0].find("dep-pk"))                                  # volver a pedirlo: no hay nada que hacer
    assert again.ok and again.nada_que_hacer
    assert {c_.entry.id: c_.estado for c_ in st.cards()} == {"base-pk": C.INSTALADO, "dep-pk": C.INSTALADO}


def test_un_fallo_a_mitad_de_la_instalacion_deja_el_catalogo_como_estaba(srv, cat, dest, tmp_path, monkeypatch):
    st = world(srv, cat, tmp_path, dest)
    prop = st.propose(st.indexes[0].find("dep-pk"))
    before = I.tree_digest(dest.root)
    real, calls = I.apply, []

    def flaky(plan, **kw):
        calls.append(plan.paquete.id)
        if len(calls) == 2:
            raise OSError("disco lleno")
        return real(plan, **kw)

    monkeypatch.setattr(I, "apply", flaky)
    t = st.task_install(prop, acepto=True).start()
    assert t.join(20) and t.status == C.FAILED and "disco lleno" in t.error_text
    assert I.tree_digest(dest.root) == before


def test_errores_de_uso_son_excepciones_con_mensaje(srv, cat, dest, tmp_path):
    publish_world(srv, cat, tmp_path)
    body = index_body(entry_dict(srv.base, pid="mala", folder="owner@mala", retiradas=["1.0.0"]),
                      entry_dict(srv.base, pid="vetada", folder="owner@vetada"), entry_dict(srv.base, pid="caida", folder="owner@caida", update_check="inaccesible"),
                      bloqueados=[{"id": "vetada", "motivo": "reclamo"}])
    srv.routes["/data/index.json"] = (200, {}, body)
    st = state(srv, tmp_path, dest.root)
    f = st.indexes[0].find
    with pytest.raises(R.RemoteError, match="fue retirada"):
        st.propose(f("mala"))
    with pytest.raises(R.RemoteError, match="no tiene la versión 9.9.9"):
        st.propose(f("mala"), "9.9.9")
    with pytest.raises(R.RemoteError, match="no se puede instalar: bloqueado por id"):
        st.propose(f("vetada"))
    with pytest.raises(R.RemoteError, match="inaccesible"):
        st.propose(f("caida"))


def test_dependencia_imposible_vuelve_en_la_propuesta_sin_bajar_nada(srv, cat, dest, tmp_path):
    serve(srv, ent("a", requiere=["falta"]))
    st = state(srv, tmp_path, dest.root)
    prop = st.propose(st.indexes[0].find("a"))
    assert not prop.ok and prop.chain is None and any("ningún índice" in str(i) for i in prop.errors)
    assert [p for p in srv.log if p[0].startswith("/zips")] == []
    with pytest.raises(I.InstallError, match="errores"):
        st.install(prop, acepto=True)


def test_cambio_de_origen_pide_confirmacion_explicita(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    set_registry(dest.root, **{"dep-pk": ("0.5.0", {"github": "otro/repo"})})
    prop = st.propose(st.indexes[0].find("dep-pk"))
    assert prop.ok and prop.cambia_origen
    with pytest.raises(I.InstallError, match="cambia de origen"):
        st.install(prop, acepto=True)
    assert st.install(prop, acepto=True, confirmar_origen=True)


# ------------------------------------------------------------------------------------------------------------------------ tareas en hilo

def test_task_progreso_cancelacion_y_error():
    seen = []
    ev = threading.Event()

    def loop(task):
        for n in range(1000):
            task.progress(n, 1000, f"paso {n}")
            ev.set()
            time.sleep(0.005)
        return "terminó"

    t = C.Task(loop, notify=lambda: seen.append(1)).start()
    assert ev.wait(5) and t.cancelable and t.fraction is not None
    assert t.cancel() and t.join(5) and t.status == C.CANCELLED and t.result is None and t.error is None and not t.cancel()
    assert seen
    ok = C.Task(lambda task: 7).start()
    assert ok.join(5) and ok.status == C.DONE and ok.result == 7
    bad = C.Task(lambda task: 1 / 0).start()
    assert bad.join(5) and bad.status == C.FAILED and isinstance(bad.error, ZeroDivisionError) and "division" in bad.error_text


def test_un_notify_que_falla_no_rompe_el_trabajo():
    def boom():
        raise RuntimeError("ventana cerrada")
    t = C.Task(lambda task: task.progress(1, 2, "x") or "ok", notify=boom).start()
    assert t.join(5) and t.status == C.DONE and t.result == "ok"


def test_task_propose_baja_con_progreso_y_devuelve_la_propuesta(srv, cat, dest, tmp_path):
    st = world(srv, cat, tmp_path, dest)
    notes = []
    t = st.task_propose(st.indexes[0].find("dep-pk"), notify=lambda: notes.append((t.status, t.text, t.fraction))).start()
    assert t.join(30) and t.status == C.DONE and t.error is None and t.result.ok
    assert any("Descargando owner@" in n[1] for n in notes) and notes[-1][0] == C.DONE
    fr = [n[2] for n in notes if n[2] is not None]
    assert fr and fr == sorted(fr) and fr[-1] == 1.0                                  # una sola barra para los dos zips, siempre creciente
    assert not os.path.isdir(os.path.join(dest.root, ".instalando"))


def test_cancelar_mientras_baja_no_deja_nada_ni_escribe_el_catalogo(srv, cat, dest, tmp_path, monkeypatch):
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
    t = st.task_propose(st.indexes[0].find("dep-pk")).start()
    assert started.wait(5) and t.cancel() and t.join(10)
    assert t.status == C.CANCELLED and t.result is None
    assert I.tree_digest(dest.root) == before


def test_instalar_ya_no_se_cancela_una_vez_que_empezo_a_escribir(srv, cat, dest, tmp_path, monkeypatch):
    st = world(srv, cat, tmp_path, dest)
    prop = st.propose(st.indexes[0].find("dep-pk"))
    inside, release_it = threading.Event(), threading.Event()
    real = D.install_chain

    def slow_install(chain, **kw):
        inside.set()
        release_it.wait(10)
        return real(chain, **kw)

    monkeypatch.setattr(D, "install_chain", slow_install)
    t = st.task_install(prop, acepto=True).start()
    assert inside.wait(5)
    assert not t.cancelable and t.cancel() is False
    release_it.set()
    assert t.join(20) and t.status == C.DONE and len(t.result) == 2


def test_task_refresh_lee_los_indices_en_un_hilo(srv, tmp_path):
    serve(srv, ent("a"))
    st = C.OnlineState([srv.base], "", **net(tmp_path))
    t = st.task_refresh().start()
    assert t.join(10) and t.status == C.DONE and folders(st.cards()) == ["a"]


# ------------------------------------------------------------------------------------------------------------------------ actualizaciones y alertas

def test_actualizaciones_y_alertas_de_lo_instalado(srv, tmp_path, dest):
    serve(srv, ent("viejo", "1.2.0", github="owner/viejo"), ent("mala", "1.1.0", versions=[("1.0.0", False)], retiradas=["1.0.0"], github="owner/mala"),
          bloqueados=[{"id": "vetado", "motivo": "reclamo", "fecha": "2026-11-20"}])
    set_registry(dest.root, viejo=("1.0.0", {"github": "owner/viejo"}), mala=("1.0.0", {"github": "owner/mala"}), vetado=("1.0.0", {}))
    st = state(srv, tmp_path, dest.root)
    todo, avisos = st.updates()
    assert sorted((e.id, have, root) for e, have, root in todo) == [("mala", "1.0.0", dest.root), ("viejo", "1.0.0", dest.root)] and avisos == []
    kinds = sorted((a.kind, a.paquete) for a in st.alerts())
    assert kinds == [("bloqueado", "vetado"), ("retirada", "mala")]
    assert I.load_registry(dest.root)["paquetes"]["mala"]["version"] == "1.0.0"          # no cambió nada


# ------------------------------------------------------------------------------------------------------------------------ D18: portada e índices propios

def png_bytes(w=8, h=8) -> bytes:
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


COVER = "/data/paquetes/owner@a/portada.png"


def test_is_image_por_los_primeros_bytes():
    assert C.is_image(png_bytes()) and C.is_image(b"\xff\xd8\xff\xe0abc") and C.is_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
    assert not C.is_image(b"<html>") and not C.is_image(b"RIFF\x00\x00\x00\x00WAVEfmt ") and not C.is_image(b"")


def test_la_portada_se_baja_una_vez_y_despues_sale_de_la_cache(srv, tmp_path):
    serve(srv, ent("a"))
    srv.routes[COVER] = (200, {"Content-Type": "image/png"}, png_bytes())
    st = state(srv, tmp_path)
    e = st.entries()[0]
    assert C.is_image(st.cover(e)) and [p for p, _h in srv.log].count(COVER) == 1
    srv.routes[COVER] = (503, {}, b"")                                      # el servidor falla: sale de la caché
    assert C.is_image(st.cover(e)) and [p for p, _h in srv.log].count(COVER) == 1
    off = C.OnlineState([srv.base], "", offline=True, **net(tmp_path))
    off.refresh()
    assert C.is_image(off.cover(off.entries()[0]))                          # sin red también


def test_la_portada_nunca_rompe_la_ficha(srv, tmp_path):
    serve(srv, ent("a"), ent("b", portada=""))
    st = state(srv, tmp_path)
    a, b = st.entries()
    assert st.cover(a) is None                                              # 404
    assert st.cover(b) is None                                              # el paquete no tiene portada
    srv.routes[COVER] = (200, {}, b"<html>no soy una imagen</html>")
    assert st.cover(a) is None
    assert not os.path.exists(os.path.join(str(tmp_path / "cache"), "portadas")) or not os.listdir(str(tmp_path / "cache" / "portadas"))   # lo que no es imagen no se guarda
    srv.routes[COVER] = (200, {}, png_bytes() + b"\0" * C.MAX_COVER_BYTES)  # más de 2 MB
    assert st.cover(a) is None
    off = C.OnlineState([srv.base], "", offline=True, **net(tmp_path))
    off.refresh()
    assert off.cover(off.entries()[0]) is None                              # sin red y sin copia


def test_task_cover_en_un_hilo(srv, tmp_path):
    serve(srv, ent("a"))
    srv.routes[COVER] = (200, {}, png_bytes())
    st = state(srv, tmp_path)
    t = st.task_cover(st.entries()[0]).start()
    assert t.join(10) and C.is_image(t.result)


def test_agregar_indice_valida_guarda_y_no_pisa_la_configuracion(srv, tmp_path):
    R.save_config(["https://uno.github.io/idx/data/index.json"])
    st = C.OnlineState(["https://otro.example/data/index.json"], "", **net(tmp_path))      # la lista de la ventana puede diferir de lo guardado
    url = st.add_index("  https://dos.github.io/idx/  ")
    assert url == "https://dos.github.io/idx/data/index.json" and st.urls[-1] == url
    assert R.load_config() == ["https://uno.github.io/idx/data/index.json", url]            # se sumó sobre lo que había
    for bad, key in (("", "indice_vacio"), ("owner/repo", "indice_owner_repo"), ("http://dos.example/idx/", "indice_https"),
                     ("ftp://x.example/i", "indice_https"), ("https://", "indice_https")):
        with pytest.raises(R.RemoteError) as ei:
            st.add_index(bad)
        assert str(ei.value) == tr(f"catalog_online.{key}"), bad
    for same in ("https://dos.github.io/idx/data/index.json", "https://uno.github.io/idx/", "https://otro.example/"):      # agregada, guardada, o en la lista de la ventana
        with pytest.raises(R.RemoteError, match="ya está"):
            st.add_index(same)
    assert len(R.load_config()) == 2                                                        # lo rechazado no se guardó


def test_agregar_indice_acepta_http_solo_en_loopback_y_se_puede_explorar(srv, tmp_path):
    serve(srv, ent("a"))
    st = C.OnlineState([], "", **net(tmp_path))
    st.add_index(srv.base + "/")
    st.refresh()
    assert folders(st.cards()) == ["a"] and st.errors == []
