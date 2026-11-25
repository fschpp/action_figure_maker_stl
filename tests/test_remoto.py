"""D13 · `remoto.py`: cliente de catálogos en línea (PLAN_catalogos_online.md §5.5, §5.7).

Todo corre contra un servidor `http.server` local (127.0.0.1) con `Policy(allow_loopback_http=True)`: sin red real.
"""
import hashlib
import http.server
import json
import os
import socket
import threading
import time

import pytest

from afm import instalar as I
from afm import remoto as R
from afm.catalog import Catalog
from test_exportar import OWNER, add_arm, cat  # noqa: F401  (cat es una fixture)
from test_instalar import dest, export  # noqa: F401  (dest es una fixture)

POLICY = R.Policy(hosts=("127.0.0.1",), allow_loopback_http=True)
FAST = dict(retries=2, sleep=lambda s: None)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Server:
    """Servidor de pruebas: `routes[path] = callable(handler) | (status, headers, body)`; `log` guarda lo pedido."""

    def __init__(self):
        self.routes, self.log = {}, []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.log.append((self.path, dict(self.headers)))
                r = outer.routes.get(self.path)
                if r is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                if callable(r):
                    r = r(self)
                    if r is None:
                        return
                status, headers, body = r
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    s = Server()
    yield s
    s.close()


@pytest.fixture
def cache(tmp_path):
    return str(tmp_path / "cache")


def release(base, body: bytes, version="1.0.0", path="/zips/p.zip", **over):
    z = {"nombre": "p.zip", "url": base + path, "tamano": len(body), "sha256": sha(body)}
    return {"version": version, "tag": "v" + version, "prerelease": False, "publicado": "2026-11-24T10:00:00Z", "zip": {**z, **over}}


def entry_dict(base, body=b"zip", pid="brazo-test", **over):
    d = {"folder": f"owner@{pid}", "id": pid, "nombre": "Brazo de prueba", "autor": "owner", "version": "1.0.0", "licencia": "CC-BY-4.0",
         "origen": "propia", "tipo": "piezas", "github": "owner/repo", "linea": {"nombre": "proceres-130", "altura_mm": 130.0},
         "categorias": ["manos"], "portada": f"paquetes/owner@{pid}/portada.png", "update_check": "ok", "descargas": None,
         "latest": release(base, body)}
    d.update(over)
    return d


def index_body(*entries, **over) -> bytes:
    return json.dumps({"schema_version": 1, "generado": "2026-11-24T12:00:00Z", "cantidad": len(entries), "categorias": ["manos"],
                       "paquetes": list(entries), **over}).encode("utf-8")


def serve_index(srv, *entries, etag='"v1"', **over):
    body = index_body(*entries, **over)

    def handler(h):
        if h.headers.get("If-None-Match") == etag:
            return 304, {"ETag": etag}, b""
        return 200, {"ETag": etag, "Content-Type": "application/json"}, body

    srv.routes["/data/index.json"] = handler
    return body


def fetch(srv, cache, **kw):
    return R.fetch_index(srv.base, policy=POLICY, cache=cache, **{**FAST, **kw})


# ------------------------------------------------------------------------------------------------------------------------ índice

def test_lee_el_indice_y_resuelve_rutas(srv, cache):
    serve_index(srv, entry_dict(srv.base))
    idx = fetch(srv, cache)
    e = idx.find("brazo-test")
    assert idx.origen == "red" and idx.schema_version == 1 and idx.find("owner@brazo-test") is e
    assert e.folder == "owner@brazo-test" and e.linea["nombre"] == "proceres-130" and e.descargas is None and e.visible and e.instalable
    assert e.portada == f"{srv.base}/data/paquetes/owner@brazo-test/portada.png"      # relativa a la carpeta del index.json
    assert e.latest.sha256 == sha(b"zip") and e.latest.tamano == 3 and not e.latest.prerelease
    assert idx.url == srv.base + "/data/index.json"                                  # una URL base se completa con data/index.json


def test_revalida_con_etag_y_usa_la_copia_en_un_304(srv, cache):
    serve_index(srv, entry_dict(srv.base))
    fetch(srv, cache)
    idx = fetch(srv, cache)
    assert idx.origen == "validado" and idx.find("brazo-test") is not None
    assert srv.log[-1][1].get("If-None-Match") == '"v1"'


def test_sin_red_usa_el_guardado_y_sin_guardado_falla(srv, cache):
    serve_index(srv, entry_dict(srv.base))
    fetch(srv, cache)
    idx = fetch(srv, cache, offline=True)
    assert idx.origen == "guardado" and idx.find("brazo-test") and idx.error_red == ""
    with pytest.raises(R.OfflineError):
        fetch(srv, str(cache) + "-otra", offline=True)


def test_servidor_caido_con_y_sin_guardado(srv, cache):
    serve_index(srv, entry_dict(srv.base))
    fetch(srv, cache)
    srv.close()
    idx = fetch(srv, cache)
    assert idx.origen == "guardado" and "no se pudo conectar" in idx.error_red and idx.find("brazo-test")
    with pytest.raises(R.NetworkError):
        fetch(srv, str(cache) + "-otra")


def test_5xx_se_reintenta(srv, cache):
    body = index_body(entry_dict(srv.base))
    calls = []

    def flaky(h):
        calls.append(1)
        return (503, {}, b"") if len(calls) < 3 else (200, {}, body)

    srv.routes["/data/index.json"] = flaky
    assert fetch(srv, cache).find("brazo-test") and len(calls) == 3


def test_timeout_agota_los_reintentos(srv, cache):
    def slow(h):
        time.sleep(0.6)
        return 200, {}, b"{}"

    srv.routes["/data/index.json"] = slow
    with pytest.raises(R.NetworkError):
        fetch(srv, cache, timeout=0.15, retries=1)


def test_lectura_tolerante_claves_nuevas_y_entradas_rotas(srv, cache):
    ok = entry_dict(srv.base, clave_del_futuro={"x": 1})
    malas = [{"folder": "owner@x"}, "no soy un objeto", entry_dict(srv.base, pid="otro", folder="owner@distinto"),
             entry_dict(srv.base, pid="sin-sha", latest=release(srv.base, b"zz", sha256="nope"))]
    serve_index(srv, ok, *malas, ok, campo_nuevo=[1, 2, 3])
    idx = fetch(srv, cache)
    assert [e.id for e in idx.entries] == ["brazo-test", "sin-sha"]
    assert idx.find("brazo-test").raw["clave_del_futuro"] == {"x": 1}
    assert idx.find("sin-sha").latest is None and not idx.find("sin-sha").instalable      # sin sha256 no se puede instalar
    assert len(idx.avisos) == 5 and any("repetida" in a for a in idx.avisos)


def test_schema_version_mayor_se_rechaza_y_no_pisa_la_copia(srv, cache):
    serve_index(srv, entry_dict(srv.base))
    fetch(srv, cache)
    body = json.dumps({"schema_version": R.INDEX_SCHEMA_SUPPORTED + 1, "paquetes": []}).encode()
    srv.routes["/data/index.json"] = (200, {"ETag": '"v2"'}, body)
    with pytest.raises(R.IndexTooNew, match="actualizá afm"):
        fetch(srv, cache)
    assert fetch(srv, cache, offline=True).find("brazo-test")           # la copia buena sigue ahí


@pytest.mark.parametrize("body", [b"no es json", b"[]", b"{}", b'{"schema_version": 1}', b'{"schema_version": "1", "paquetes": []}',
                                  b'{"schema_version": true, "paquetes": []}'])
def test_indice_invalido(srv, cache, body):
    srv.routes["/data/index.json"] = (200, {}, body)
    with pytest.raises(R.IndexInvalid):
        fetch(srv, cache)


def test_update_check_inaccesible_se_oculta_pero_no_se_borra(srv, cache):
    serve_index(srv, entry_dict(srv.base, update_check="inaccesible"), entry_dict(srv.base, pid="otro", folder="owner@otro"))
    idx = fetch(srv, cache)
    assert [e.id for e in idx.entries] == ["brazo-test", "otro"] and [e.id for e in idx.visibles()] == ["otro"]


def test_descargas_null_no_es_cero(srv, cache):
    serve_index(srv, entry_dict(srv.base, descargas=None), entry_dict(srv.base, pid="otro", folder="owner@otro", descargas={"total": 0}))
    idx = fetch(srv, cache)
    assert idx.find("brazo-test").descargas is None and idx.find("otro").descargas == {"total": 0}


def test_un_indice_caido_no_tapa_a_los_demas(srv, cache):
    serve_index(srv, entry_dict(srv.base))
    res = R.fetch_indexes([srv.base, "http://127.0.0.1:9/"], policy=POLICY, cache=cache, **FAST)
    assert res[0].index and not res[0].error and res[1].index is None and res[1].error


# ------------------------------------------------------------------------------------------------------------------------ política de hosts

def test_solo_https_y_hosts_permitidos():
    p = R.policy_for("https://x.github.io/repo/")
    for ok in ("https://x.github.io/a", "https://github.com/o/r/releases/download/v1/p.zip", "https://objects.githubusercontent.com/x"):
        p.check(ok)
    for bad in ("http://x.github.io/a", "https://evil.example/p.zip", "ftp://github.com/x", "file:///etc/passwd", "https:///sin-host"):
        with pytest.raises(R.HostNotAllowed):
            p.check(bad)
    with pytest.raises(R.HostNotAllowed):
        R.policy_for("https://x.github.io").check("http://127.0.0.1/x")          # loopback http solo con la bandera de pruebas


def test_index_url_acepta_pages_y_json():
    assert R.index_url("https://x.github.io/repo/") == "https://x.github.io/repo/data/index.json"
    assert R.index_url("https://x.github.io/repo") == "https://x.github.io/repo/data/index.json"
    assert R.index_url("https://x.github.io/repo/data/index.json") == "https://x.github.io/repo/data/index.json"


def test_redireccion_fuera_de_la_lista_se_corta(srv, cache):
    srv.routes["/data/index.json"] = (302, {"Location": "https://evil.example/index.json"}, b"")
    with pytest.raises(R.HostNotAllowed, match="evil.example"):
        fetch(srv, cache)
    srv.routes["/data/index.json"] = (302, {"Location": "http://127.0.0.1:1/otra"}, b"")      # http a otro puerto de loopback: permitido por la política de pruebas
    with pytest.raises(R.NetworkError):
        fetch(srv, cache, retries=0)


def test_redireccion_dentro_de_la_lista_se_sigue(srv, cache):
    body = index_body(entry_dict(srv.base))
    srv.routes["/data/index.json"] = (302, {"Location": "/real.json"}, b"")
    srv.routes["/real.json"] = (200, {}, body)
    assert fetch(srv, cache).find("brazo-test")


# ------------------------------------------------------------------------------------------------------------------------ descarga

def entry_for(srv, body, **over):
    serve_index(srv, entry_dict(srv.base, body, **over))
    return R.parse_index(index_body(entry_dict(srv.base, body, **over)), url=srv.base + "/data/index.json").entries[0]


def zips_en(cache):
    d = os.path.join(cache, "zips")
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


def test_descarga_verifica_el_sha_y_solo_escribe_en_la_cache(srv, cache):
    body = os.urandom(300_000)
    srv.routes["/zips/p.zip"] = (200, {}, body)
    e = entry_for(srv, body)
    seen = []
    path = R.download_package(e, policy=POLICY, cache=cache, progress=lambda d, t: seen.append((d, t)), **FAST)
    assert path == os.path.join(cache, "zips", sha(body) + ".zip") and open(path, "rb").read() == body
    assert zips_en(cache) == [sha(body) + ".zip"] and seen[-1] == (len(body), len(body))


def test_segunda_descarga_sale_de_la_cache_sin_red(srv, cache):
    body = b"contenido"
    srv.routes["/zips/p.zip"] = (200, {}, body)
    e = entry_for(srv, body)
    R.download_package(e, policy=POLICY, cache=cache, **FAST)
    n = len(srv.log)
    R.download_package(e, policy=POLICY, cache=cache, **FAST)
    assert len(srv.log) == n


def test_hash_distinto_borra_todo_y_dice_que_cambio(srv, cache):
    srv.routes["/zips/p.zip"] = (200, {}, b"otro contenido")
    e = entry_for(srv, b"lo que se indexo")
    with pytest.raises(R.HashMismatch, match="cambió desde que se indexó"):
        R.download_package(e, policy=POLICY, cache=cache, **FAST)
    assert zips_en(cache) == []


def test_tamano_mayor_que_el_indexado_corta_la_descarga(srv, cache):
    body = b"x" * 5000
    srv.routes["/zips/p.zip"] = (200, {}, body)
    e = entry_for(srv, b"x" * 100)
    e.latest = R.Release(**{**e.latest.__dict__, "sha256": sha(body)})
    with pytest.raises(R.HashMismatch):
        R.download_package(e, policy=POLICY, cache=cache, **FAST)
    assert zips_en(cache) == []


def test_corte_a_mitad_de_la_descarga_se_reintenta(srv, cache):
    body = os.urandom(200_000)
    calls = []

    def cut(h):
        calls.append(1)
        h.send_response(200)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        if len(calls) == 1:
            h.wfile.write(body[:50_000])
            h.wfile.flush()
            h.connection.shutdown(socket.SHUT_RDWR)
            h.connection.close()
            return None
        h.wfile.write(body)
        return None

    srv.routes["/zips/p.zip"] = cut
    e = entry_for(srv, body)
    path = R.download_package(e, policy=POLICY, cache=cache, **FAST)
    assert len(calls) == 2 and open(path, "rb").read() == body and zips_en(cache) == [sha(body) + ".zip"]


def test_version_retirada_no_se_instala_salvo_que_se_pida(srv, cache):
    body = b"zip"
    srv.routes["/zips/p.zip"] = (200, {}, body)
    e = entry_for(srv, body, retiradas=["1.0.0"])
    with pytest.raises(R.RemoteError, match="retirada"):
        R.download_package(e, policy=POLICY, cache=cache, **FAST)
    assert R.download_package(e, policy=POLICY, cache=cache, allow_retirada=True, **FAST)


def test_version_que_el_indice_no_tiene(srv, cache):
    e = entry_for(srv, b"zip")
    with pytest.raises(R.RemoteError, match="9.9.9"):
        R.download_package(e, version="9.9.9", policy=POLICY, cache=cache, **FAST)


def test_url_del_zip_fuera_de_la_lista(srv, cache):
    e = entry_for(srv, b"zip")
    e.latest = R.Release(**{**e.latest.__dict__, "url": "https://evil.example/p.zip"})
    with pytest.raises(R.HostNotAllowed):
        R.download_package(e, policy=POLICY, cache=cache, **FAST)
    assert zips_en(cache) == []


def test_404_no_se_reintenta(srv, cache):
    e = entry_for(srv, b"zip")
    with pytest.raises(R.RemoteError, match="404"):
        R.download_package(e, policy=POLICY, cache=cache, **FAST)
    assert [p for p, _ in srv.log].count("/zips/p.zip") == 1


# ------------------------------------------------------------------------------------------------------------------------ plan de instalación

def _real_entry(srv, cache, cat, tmp_path, **over):
    add_arm(cat)
    z = export(cat, tmp_path, github="owner/repo")
    body = open(z, "rb").read()
    srv.routes["/zips/p.zip"] = (200, {}, body)
    pkg = I.peek_package(z)
    d = entry_dict(srv.base, body, pid=pkg.id, folder=f"owner@{pkg.id}", **over)
    d["latest"] = release(srv.base, body, version=pkg.version)
    return R.parse_index(index_body(d), url=srv.base + "/data/index.json").entries[0], body


def test_plan_entry_usa_el_mismo_camino_que_un_zip_local(srv, cache, cat, dest, tmp_path):
    e, _ = _real_entry(srv, cache, cat, tmp_path)
    before = I.tree_digest(dest.root)
    plan = R.plan_entry(e, dest.root, policy=POLICY, cache=cache, **FAST)
    assert plan.ok and [i.name for i in plan.a_instalar] == ["brazo-test-01@1"] and plan.indice == e.indice
    assert I.tree_digest(dest.root) == before                                  # bajar y planear no toca el catálogo
    res = I.apply(plan)
    assert res.piezas == ["brazo-test-01@1"] and Catalog.open(dest.root).verify().ok
    assert I.load_registry(dest.root)["paquetes"][res.paquete.id]["indice"] == e.indice


def test_plan_entry_detecta_que_el_zip_no_es_lo_que_el_indice_dice(srv, cache, cat, dest, tmp_path):
    e, _ = _real_entry(srv, cache, cat, tmp_path)
    e.latest = R.Release(**{**e.latest.__dict__, "version": "9.0.0"})
    plan = R.plan_entry(e, dest.root, policy=POLICY, cache=cache, **FAST)
    assert not plan.ok and any("9.0.0" in i.message for i in plan.errors)


def test_plan_entry_con_otro_id_o_repo(srv, cache, cat, dest, tmp_path):
    e, _ = _real_entry(srv, cache, cat, tmp_path)
    e.id = "otro-id"
    plan = R.plan_entry(e, dest.root, policy=POLICY, cache=cache, **FAST)
    assert not plan.ok and any("otro-id" in i.message for i in plan.errors)
    e.id = I.peek_package(os.path.join(cache, "zips", e.latest.sha256 + ".zip")).id
    e.github = "otro/repo"
    plan = R.plan_entry(e, dest.root, policy=POLICY, cache=cache, **FAST)
    assert plan.ok and any("otro/repo" in i.message for i in plan.warnings)


def test_default_root_y_linea_ausente(srv, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("AFM_CATALOGO", str(tmp_path / "otro"))                  # no se mira (D-4)
    e = entry_for(srv, b"zip")
    assert R.default_root(e) == os.path.join(str(tmp_path), "afm", "catalogos", "proceres-130")
    e.linea = {}
    with pytest.raises(R.RemoteError):
        R.default_root(e)


# ------------------------------------------------------------------------------------------------------------------------ configuración

def test_config_de_indices(tmp_path):
    path = str(tmp_path / "cfg" / "remoto.json")
    assert R.load_config(path) == []                                            # sin archivo: todavía no hay un índice oficial (D00)
    R.save_config(["https://a.github.io/x/", "https://b.github.io/y/"], path)
    assert R.load_config(path) == ["https://a.github.io/x/", "https://b.github.io/y/"]
    open(path, "w").write("{roto")
    assert R.load_config(path) == []
    open(path, "w").write(json.dumps({"indices": ["https://a.github.io/x/", {"url": "https://a.github.io/x/"}, {"nada": 1}, 7]}))
    assert R.load_config(path) == ["https://a.github.io/x/"]


def test_el_motor_remoto_no_importa_gtk_ni_manifold():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(R))
    mods = {n.module.split(".")[0] if isinstance(n, ast.ImportFrom) and n.module else a.name.split(".")[0]
            for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in getattr(n, "names", [])}
    assert not mods & {"gi", "manifold3d", "moderngl"}
