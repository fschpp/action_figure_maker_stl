"""Piezas comunes de los scripts del índice (D12): hallazgos `IX…`, lectura de `paquetes/<Autor>@<id>/`, cliente mínimo de la API de GitHub y
descarga de zips con tope. Solo biblioteca estándar + `afm`; nada acá escribe en `paquetes/`.

Códigos (PLAN_catalogos_online.md §5.5):
IX101 `meta.json`/`descripcion.md` ausente o que no cumple el esquema · IX102 la carpeta no es `<Autor>@<id>` de lo que dice el meta ·
IX103 hay archivos de más (solo `meta.json`, `descripcion.md`, `portada.png|jpg`) · IX104 el meta deriva del `paquete.json` del zip ·
IX105 el repo, el Release o el zip no se pueden resolver · IX106 bloqueado (`bloqueados.json`) · IX107 la portada no sirve ·
IX108 `fuente` por URL: este índice solo lista paquetes de GitHub Releases (decisión 5 de `docs/POLITICA_catalogos_online.md`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAQUETES = "paquetes"
TOPE_ZIP_MB = 100                                   # decisión 6 de la política: un solo valor para toda la puerta
TOPE_PORTADA_BYTES = 2 * 1024 * 1024                # IX107
TOPE_PORTADA_PX = 4096                              # lado máximo aceptado antes de reencodar
PORTADA_PUBLICADA_PX = (640, 360)                   # lo que se publica: miniatura, sin metadatos
ARCHIVOS_ALTA = frozenset({"meta.json", "descripcion.md", "portada.png", "portada.jpg"})
CATEGORIAS = ("heroes", "cuerpos", "cabezas", "manos", "armas-accesorios", "ropa", "paletas", "lineas", "colecciones", "otros")   # decisión 4
FOLDER_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)@([a-z0-9]+(?:-[a-z0-9]+)*)$")
GITHUB_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
                "github-releases.githubusercontent.com")
API_DEFAULT = "https://api.github.com"


@dataclass(frozen=True)
class Hallazgo:
    codigo: str
    severidad: str                       # error | aviso
    folder: str
    mensaje: str

    def __str__(self) -> str:
        return f"{self.codigo} {self.severidad}: {self.folder}: {self.mensaje}"


def error(codigo: str, folder: str, mensaje: str) -> Hallazgo:
    return Hallazgo(codigo, "error", folder, mensaje)


def aviso(codigo: str, folder: str, mensaje: str) -> Hallazgo:
    return Hallazgo(codigo, "aviso", folder, mensaje)


def mostrar(hallazgos, *, out=None) -> None:
    """Imprime cada hallazgo; dentro de GitHub Actions, además, como anotación (`::error ::`) para que salga en la pestaña del PR."""
    out = out or sys.stdout
    for h in hallazgos:
        print(h, file=out)
        if os.environ.get("GITHUB_ACTIONS"):
            nivel = "error" if h.severidad == "error" else "warning"
            msg = str(h).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::{nivel} file={PAQUETES}/{h.folder}::{msg}", file=out)


def hay_errores(hallazgos) -> bool:
    return any(h.severidad == "error" for h in hallazgos)


def leer_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(d) -> str:
    return json.dumps(d, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def carpetas(raiz: str, solo=None) -> list[str]:
    """Nombres de las subcarpetas de `<raiz>/paquetes/` (ordenados). `solo` limita a esas (las que no existen se ignoran: un PR que las borra)."""
    base = os.path.join(raiz, PAQUETES)
    if not os.path.isdir(base):
        return []
    todas = sorted(n for n in os.listdir(base) if os.path.isdir(os.path.join(base, n)) and not n.startswith("."))
    if solo:
        pedidas = {os.path.basename(s.rstrip("/")) for s in solo}
        return [n for n in todas if n in pedidas]
    return todas


def leer_meta(raiz: str, folder: str):
    """`(meta | None, error | None)`: el `meta.json` de la carpeta, sin validar contra el esquema (eso lo hace `validar_alta`)."""
    path = os.path.join(raiz, PAQUETES, folder, "meta.json")
    try:
        d = leer_json(path)
    except FileNotFoundError:
        return None, "falta meta.json"
    except (OSError, ValueError) as exc:
        return None, f"meta.json no es un JSON legible: {exc}"
    return (d, None) if isinstance(d, dict) else (None, "meta.json no es un objeto JSON")


def duenio(github: str) -> str:
    return github.split("/")[0] if github and "/" in github else ""


# ------------------------------------------------------------------------------------------------------------ GitHub

class ApiError(Exception):
    """La API (o la descarga) falló. `status` = código HTTP, o 0 si no se pudo ni conectar."""

    def __init__(self, status: int, mensaje: str):
        super().__init__(mensaje)
        self.status = status


class _Redirige(urllib.request.HTTPRedirectHandler):
    """Un redirect solo se sigue si va a un host de GitHub por HTTPS (o, en pruebas, a loopback)."""

    def __init__(self, permitido):
        self.permitido = permitido

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.permitido(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHub:
    """Cliente mínimo de la API REST de GitHub. `api` y `loopback` existen para las pruebas (un servidor local); en producción son los de siempre."""

    def __init__(self, token: str = "", api: str = API_DEFAULT, *, loopback: bool = False, timeout: float = 30.0):
        self.token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        self.api, self.loopback, self.timeout = api.rstrip("/"), loopback, timeout
        self.opener = urllib.request.build_opener(_Redirige(self._permitido))

    def _permitido(self, url: str) -> None:
        u = urllib.parse.urlsplit(url)
        host = (u.hostname or "").lower()
        if self.loopback and host in ("127.0.0.1", "localhost") and u.scheme in ("http", "https"):
            return
        if u.scheme != "https" or host not in GITHUB_HOSTS:
            raise ApiError(0, f"«{url}» no está en la lista de hosts permitidos (solo GitHub por HTTPS)")

    def _req(self, url: str, accept: str) -> urllib.request.Request:
        self._permitido(url)
        h = {"Accept": accept, "User-Agent": "afm-catalogo-indice", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token and urllib.parse.urlsplit(url).hostname in ("api.github.com", "127.0.0.1", "localhost"):
            h["Authorization"] = f"Bearer {self.token}"
        return urllib.request.Request(url, headers=h)

    def get_json(self, path: str):
        try:
            with self.opener.open(self._req(self.api + path, "application/vnd.github+json"), timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, f"GitHub respondió {exc.code} para {path}") from None
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            raise ApiError(0, f"no pude hablar con GitHub ({exc})") from None

    def repo_existe(self, github: str) -> bool:
        try:
            self.get_json(f"/repos/{github}")
            return True
        except ApiError as exc:
            if exc.status == 404:
                return False
            raise

    def releases(self, github: str, limite: int = 30) -> list:
        data = self.get_json(f"/repos/{github}/releases?per_page={int(limite)}")
        if not isinstance(data, list):
            raise ApiError(0, "la lista de Releases no es una lista")
        return [r for r in data if isinstance(r, dict) and not r.get("draft")]

    def descargar(self, url: str, destino: str, *, tope_bytes: int) -> tuple[str, int]:
        """Baja `url` a `destino` cortando al pasar `tope_bytes`. Devuelve `(sha256, bytes)`. `ApiError` si falla o excede el tope."""
        h, n = hashlib.sha256(), 0
        try:
            with self.opener.open(self._req(url, "application/octet-stream"), timeout=self.timeout) as r, open(destino, "wb") as out:
                for chunk in iter(lambda: r.read(1 << 20), b""):
                    n += len(chunk)
                    if n > tope_bytes:
                        raise ApiError(0, f"el zip pesa más de {tope_bytes / 1e6:.0f} MB (tope del índice)")
                    h.update(chunk)
                    out.write(chunk)
        except ApiError:
            self._borrar(destino)
            raise
        except urllib.error.HTTPError as exc:
            self._borrar(destino)
            raise ApiError(exc.code, f"la descarga respondió {exc.code}") from None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            self._borrar(destino)
            raise ApiError(0, f"no pude descargar el zip ({exc})") from None
        return h.hexdigest(), n

    @staticmethod
    def _borrar(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------------------ elegir el Release

def version_de_tag(tag: str) -> str:
    return tag[1:] if tag[:1] in ("v", "V") else tag


def elegir_release(releases: list, meta: dict):
    """El Release que sigue el índice para esta entrada, o `None`. `seguimiento: fijo` ⇒ el del tag `release_fijo` (puede ser un prerelease);
    si no, el más nuevo que **no** sea prerelease (los borradores ya se descartaron en `GitHub.releases`)."""
    if meta.get("seguimiento") == "fijo":
        return next((r for r in releases if r.get("tag_name") == meta.get("release_fijo")), None)
    estables = [r for r in releases if not r.get("prerelease")]
    estables.sort(key=lambda r: r.get("published_at") or r.get("created_at") or "", reverse=True)
    return estables[0] if estables else None


def elegir_asset(release: dict, pid: str):
    """El `.zip` del Release: `<id>-<version>.zip` (lo que sube el workflow de «Publicar…»); si no está, el único `.zip` que haya."""
    assets = [a for a in release.get("assets", []) if isinstance(a, dict) and str(a.get("name", "")).endswith(".zip")]
    esperado = f"{pid}-{version_de_tag(str(release.get('tag_name', '')))}.zip"
    for a in assets:
        if a.get("name") == esperado:
            return a
    return assets[0] if len(assets) == 1 else None
