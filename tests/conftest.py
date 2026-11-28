"""Fixtures de las pruebas del índice: paquetes .zip reales (armados con afm), carpetas de alta y un GitHub de mentira en 127.0.0.1."""
import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import manifold3d as M
import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import common as C  # noqa: E402

from afm import exportar as X  # noqa: E402
from afm import indice as IX  # noqa: E402
from afm import parts as P  # noqa: E402
from afm import solids as S  # noqa: E402
from afm.catalog import Catalog  # noqa: E402
from afm.ports import port_from_points  # noqa: E402

BASE = [(3.0, 0.0, 0.0), (-1.5, -2.6, 0.0), (-1.5, 2.6, 0.0)]


def png(path, size=(1280, 720), color=(40, 90, 160)):
    Image.new("RGB", size, color).save(path)
    return str(path)


@pytest.fixture(scope="session")
def catalogo(tmp_path_factory):
    """Un catálogo con un brazo (cilindro con sus dos puertos) para exportar paquetes de prueba."""
    cat = Catalog.create(str(tmp_path_factory.mktemp("cat") / "linea"), "proceres-130", 130.0, 0.4)
    mesh = S.to_trimesh(M.Manifold.cylinder(30.0, 5.0, 5.0, 96))
    ports = {"hombro": port_from_points(mesh, BASE, "hombro", "enchufe"),
             "muneca": port_from_points(mesh, [(a, b, 30.0) for a, b, _ in BASE], "muneca", "toma")}
    cat.add_piece(mesh, P.PieceMeta(id="brazo-test-01", version=1, tipo="brazo", puertos=ports, altura_figura_mm=130.0))
    return cat


@pytest.fixture(scope="session")
def hacer_zip(catalogo, tmp_path_factory):
    """`hacer_zip(id, version, github, ...) -> ruta` de un paquete válido (cacheado por argumentos)."""
    d = tmp_path_factory.mktemp("zips")
    cache = {}

    def make(pid="mi-paquete", version="1.0.0", github="ana/mi-paquete", licencia="CC-BY-4.0", origen="propia", con_portada=True, nombre="Mi paquete"):
        key = (pid, version, github, licencia, origen, con_portada, nombre)
        if key not in cache:
            out = str(d / f"{pid}-{version}-{len(cache)}.zip")
            kw = dict(piezas=["brazo-test-01"], autores=["Ana Pérez:ana"], licencia=licencia, origen=origen, github=github, id=pid, nombre=nombre,
                      descripcion="Un brazo de prueba.", categorias=["manos"], version=version)
            if con_portada:
                kw["portada"] = png(d / f"c{len(cache)}.png")
            cache[key] = X.export_package(catalogo, out, **kw).path
        return cache[key]
    return make


@pytest.fixture
def raiz(tmp_path):
    """Una copia vacía del repo del índice (esquemas, bloqueados, paquetes/)."""
    r = tmp_path / "indice"
    (r / "paquetes").mkdir(parents=True)
    (r / "bloqueados.json").write_text("[]", encoding="utf-8")
    return str(r)


def escribir_alta(raiz, zip_path, *, autor="ana", portada=True, descripcion="# Mi paquete\n\nUn brazo.\n", **meta_over):
    """Escribe `paquetes/<Autor>@<id>/` como lo haría `afm catalogo paquete enviar` y devuelve el nombre de la carpeta."""
    pkg, _ = IX._read_package(zip_path)
    meta = IX.meta_from_paquete(pkg, autor=autor)
    meta.update(meta_over)
    for k in [k for k, v in meta.items() if v is None]:
        del meta[k]
    folder = f"{autor}@{meta['id']}" if "folder" not in meta_over else meta.pop("folder")
    base = os.path.join(raiz, "paquetes", folder)
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    with open(os.path.join(base, "descripcion.md"), "w", encoding="utf-8") as f:
        f.write(descripcion)
    if portada:
        png(os.path.join(base, "portada.png"))
    return folder


class FakeGitHub:
    """La API de GitHub y las descargas de Releases, en un servidor local. `repos[github] = [release, ...]`; un release es
    `{"tag", "prerelease", "draft", "published_at", "assets": {nombre: bytes | ruta}}`."""

    def __init__(self):
        self.repos, self.caidos = {}, set()
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0]
                parts = path.strip("/").split("/")
                if parts[0] == "repos" and len(parts) >= 3:
                    gh = f"{parts[1]}/{parts[2]}"
                    if gh not in fake.repos:
                        return self._send(404, b'{"message":"Not Found"}')
                    if gh in fake.caidos:
                        return self._send(500, b"{}")
                    if len(parts) == 3:
                        return self._send(200, b"{}")
                    if parts[3] == "releases":
                        return self._send(200, json.dumps([fake._release(gh, r) for r in fake.repos[gh]]).encode())
                if parts[0] == "dl" and len(parts) == 5:
                    gh, tag, name = f"{parts[1]}/{parts[2]}", parts[3], parts[4]
                    for r in fake.repos.get(gh, []):
                        if r["tag"] == tag and name in r["assets"]:
                            a = r["assets"][name]
                            data = open(a, "rb").read() if isinstance(a, str) else a
                            return self._send(200, data, "application/zip")
                return self._send(404, b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _bytes(self, a):
        return open(a, "rb").read() if isinstance(a, str) else a

    def _release(self, gh, r):
        return {"tag_name": r["tag"], "draft": r.get("draft", False), "prerelease": r.get("prerelease", False),
                "published_at": r.get("published_at", "2026-11-28T12:00:00Z"),
                "assets": [{"name": n, "size": len(self._bytes(a)), "browser_download_url": f"{self.url}/dl/{gh}/{r['tag']}/{n}"}
                           for n, a in r["assets"].items()]}

    def publicar(self, github, zip_path, tag="v1.0.0", name=None, **extra):
        name = name or os.path.basename(zip_path)
        self.repos.setdefault(github, []).append({"tag": tag, "assets": {name: zip_path}, **extra})

    def cliente(self):
        return C.GitHub(api=self.url, loopback=True, timeout=10)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def gh():
    fake = FakeGitHub()
    yield fake
    fake.close()


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()
