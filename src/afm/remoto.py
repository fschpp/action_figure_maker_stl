"""Cliente de catálogos en línea: leer un índice por HTTPS y bajar un paquete verificado (plan de catálogos en línea, §5.5, §5.7 y D13).

Es la **única** parte del motor que toca la red, y lo hace con `urllib` (sin dependencias nuevas). Lo que garantiza:

* **Solo HTTPS** y solo a hosts permitidos: el host del propio índice y los de GitHub (`github.com`, `raw.githubusercontent.com`,
  `objects.githubusercontent.com`, `release-assets.githubusercontent.com`). Cada redirección se revisa contra la misma lista **antes**
  de seguirla; una que sale de la lista corta la descarga (`HostNotAllowed`). `Policy(allow_loopback_http=True)` existe solo para las pruebas.
* **El `sha256` lo manda el índice, no el servidor del autor.** Una entrada sin `sha256` no se puede instalar; si lo descargado no
  coincide (o el tamaño difiere) el archivo se borra y se dice «el paquete cambió desde que se indexó; probá más tarde» (`HashMismatch`).
* **Nada fuera de la caché.** Descargar escribe solo en `~/.cache/afm/` (o `$AFM_CACHE`), con un archivo `.part` que se mueve a su lugar
  al terminar; instalar sigue siendo trabajo de `instalar.plan_install`/`apply` (el `.zip` bajado entra por el **mismo camino** que uno local).
* **Offline primero.** Un fallo de red con un índice ya guardado devuelve el guardado (`Index.origen == "guardado"`); sin guardado, `OfflineError`.
  El índice se revalida con `ETag` / `Last-Modified` (304 ⇒ se usa la copia).
* **Lectura tolerante** (§5.1 punto 8): solo importan `schema_version` y la lista de entradas. Una clave desconocida se ignora; una entrada
  mal formada se salta con un aviso sin tirar el resto; solo una `schema_version` mayor que la conocida se rechaza (`IndexTooNew`).
* **Varios índices** (`~/.config/afm/remoto.json`, `$AFM_REMOTO`), cada uno con su propio host permitido.

Módulo de motor puro: sin GTK ni `manifold3d`. La CLI (`afm catalogo remoto …`) es D14; el alta/baja de índices por `owner/repo`, D33.

Formato del índice que se lee (lo fija formalmente D11; los nombres de acá siguen el §5.5): `{"schema_version": 1, "generado", "cantidad", "categorias",
"paquetes": [{folder, id, nombre, autor, version, …, portada, descripcion_url, latest: {version, tag, prerelease, publicado, zip: {nombre, url,
tamano, sha256}}, versiones: [<como latest>], retiradas: [versión], update_check, descargas}]}`. `portada` y `descripcion_url` son rutas relativas
a la carpeta del `index.json` (o una URL completa).
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import __version__
from . import instalar as I
from . import paquete as Q
from . import ziplector as Z
from .catalog import CatalogError

INDEX_SCHEMA_SUPPORTED = 1                # la `schema_version` más alta que esta versión de afm sabe leer
INDEX_FILE = "data/index.json"            # donde Pages publica el feed (se agrega si la URL dada no termina en .json)
CONFIG_FORMAT = "afm-remoto-1"
BASE_ALLOWED_HOSTS = ("github.com", "raw.githubusercontent.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
MAX_INDEX_BYTES = 16 * 1024 * 1024        # [P] un índice de miles de paquetes pesa unos pocos MB
MAX_ZIP_BYTES = Z.MAX_TOTAL_BYTES         # [P] un zip no puede pesar más que el tope descomprimido (D01 fija el definitivo)
TIMEOUT_S = 20.0
RETRIES = 2                               # reintentos tras el primer intento (red caída, 5xx, corte a mitad de la descarga)
BACKOFF_S = 0.5
MAX_REDIRECTS = 5
CHUNK = 1 << 16
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*@[a-z0-9]+(?:-[a-z0-9]+)*$")
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CHANGED_MSG = "el paquete cambió desde que se indexó; probá más tarde"


class RemoteError(CatalogError):
    """Algo falló al hablar con un índice o al bajar un paquete; no se escribió nada fuera de la caché."""


class NetworkError(RemoteError):
    """No hubo conexión (o se agotaron los reintentos)."""


class OfflineError(RemoteError):
    """Modo sin red (o sin conexión) y no hay un índice guardado."""


class HostNotAllowed(RemoteError):
    """La URL no es HTTPS o su host no está en la lista permitida (también para redirecciones)."""


class IndexTooNew(RemoteError):
    """El índice usa una `schema_version` mayor que la que esta versión de afm entiende."""


class IndexInvalid(RemoteError):
    """El índice no es un JSON con la forma mínima (`schema_version` y lista de entradas)."""


class HashMismatch(RemoteError):
    """Lo descargado no coincide con el `sha256` o el tamaño que dijo el índice."""


# ------------------------------------------------------------------------------------------------------------------------ política

@dataclass(frozen=True)
class Policy:
    """Qué URLs se pueden pedir. `hosts` = los permitidos además de los de GitHub (normalmente el host del índice)."""

    hosts: tuple = ()
    allow_loopback_http: bool = False        # SOLO pruebas: http://127.0.0.1 / localhost

    def check(self, url: str) -> None:
        u = urllib.parse.urlsplit(url)
        host = (u.hostname or "").lower()
        if not host:
            raise HostNotAllowed(f"«{url}» no es una URL válida")
        loop = self.allow_loopback_http and host in LOOPBACK
        if u.scheme != "https" and not (loop and u.scheme == "http"):
            raise HostNotAllowed(f"«{url}» no es HTTPS: solo se descarga por conexiones cifradas")
        if host not in {h.lower() for h in (*BASE_ALLOWED_HOSTS, *self.hosts)}:
            raise HostNotAllowed(f"el host «{host}» no está en la lista permitida ({', '.join(sorted({*BASE_ALLOWED_HOSTS, *self.hosts}))})")


def policy_for(index_url: str, *, allow_loopback_http: bool = False) -> Policy:
    """La política de un índice: su propio host + los de GitHub."""
    return Policy(hosts=((urllib.parse.urlsplit(index_url).hostname or "").lower(),), allow_loopback_http=allow_loopback_http)


def index_url(base: str) -> str:
    """Una URL de Pages (`https://x.github.io/repo/`) o la del `index.json` → la del `index.json`."""
    base = base.strip()
    u = urllib.parse.urlsplit(base)
    if u.path.endswith(".json"):
        return base
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path.rstrip("/") + "/" + INDEX_FILE, "", ""))


class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def __init__(self, policy: Policy):
        self.policy = policy

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.policy.check(urllib.parse.urljoin(req.full_url, newurl))      # antes de seguirla: nunca se llega a pedir un host no permitido
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _NotModified(Exception):
    pass


def _request(url: str, policy: Policy, *, headers=None, timeout=TIMEOUT_S):
    """Un intento: devuelve la respuesta abierta. `_NotModified` ante 304; `RemoteError`/`OSError` ante lo demás."""
    policy.check(url)
    opener = urllib.request.build_opener(_Redirects(policy))
    req = urllib.request.Request(url, headers={"User-Agent": f"afm/{__version__}", **(headers or {})})
    try:
        return opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        if code == 304:
            raise _NotModified() from None
        if code >= 500:
            raise OSError(f"HTTP {code}") from None
        raise RemoteError(f"«{url}» respondió HTTP {code}") from None


def _read_capped(resp, cap: int, what: str) -> bytes:
    try:
        declared = int(resp.headers.get("Content-Length") or 0)
    except ValueError:
        declared = 0
    if declared > cap:
        raise RemoteError(f"{what} pesa {declared} bytes y el tope es {cap}")
    out, total = [], 0
    while True:
        chunk = resp.read(CHUNK)
        if not chunk:
            return b"".join(out)
        total += len(chunk)
        if total > cap:
            raise RemoteError(f"{what} supera el tope de {cap} bytes")
        out.append(chunk)


def _with_retries(fn, url: str, *, retries: int, sleep):
    last = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except (OSError, http.client.HTTPException) as exc:      # red caída, timeout, 5xx, corte a mitad de la respuesta
            last = exc
            if attempt < retries:
                sleep(BACKOFF_S * 2 ** attempt)
    host = urllib.parse.urlsplit(url).hostname or url
    reason = getattr(last, "reason", None) or last
    raise NetworkError(f"no se pudo conectar con {host}: {reason}") from None


# ------------------------------------------------------------------------------------------------------------------------ caché y config

def cache_dir() -> str:
    if os.environ.get("AFM_CACHE"):
        return os.environ["AFM_CACHE"]
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "afm")


def config_path() -> str:
    if os.environ.get("AFM_REMOTO"):
        return os.environ["AFM_REMOTO"]
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "afm", "remoto.json")


def load_config(path: str | None = None) -> list[str]:
    """Las URL de los índices configurados (en orden). Sin archivo (o ilegible) ⇒ lista vacía: todavía no hay un índice oficial (D00)."""
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        items = d.get("indices", []) if isinstance(d, dict) else []
    except (OSError, ValueError):
        return []
    urls = []
    for it in items:
        url = it.get("url") if isinstance(it, dict) else it
        if isinstance(url, str) and url.strip() and url.strip() not in urls:
            urls.append(url.strip())
    return urls


def save_config(urls: list[str], path: str | None = None) -> str:
    path = path or config_path()
    _atomic_write(path, json.dumps({"formato": CONFIG_FORMAT, "indices": [{"url": u} for u in urls]}, ensure_ascii=False, indent=2).encode("utf-8"))
    return path


def _atomic_write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _index_cache_paths(url: str, cache: str) -> tuple[str, str]:
    d = os.path.join(cache, "indices", hashlib.sha256(url.encode("utf-8")).hexdigest()[:16])
    return os.path.join(d, "index.json"), os.path.join(d, "meta.json")


# ------------------------------------------------------------------------------------------------------------------------ el índice

@dataclass(frozen=True)
class Block:
    """Una línea de `bloqueados` del índice (§5.5, D16/D24): lo que el índice se niega a listar, con motivo y fecha.

    Bloquea por **`id`**, por **autor** o por **`sha256`** (exactamente uno de los tres): un reclamo no se esquiva volviendo a subir lo
    mismo con otro nombre. El que bloquea por `sha256` afecta a esa versión (el del zip) o a esa pieza (el de su STL, `bloqueos.piezas_bloqueadas`);
    por `id` o autor, a todo el paquete.
    """

    id: str = ""
    autor: str = ""
    sha256: str = ""
    motivo: str = ""
    fecha: str = ""

    def matches(self, entry: "Entry", release: "Release | None" = None) -> bool:
        if self.id:
            return entry.id == self.id
        if self.autor:
            return bool(entry.autor) and entry.autor.casefold() == self.autor.casefold()
        if self.sha256:
            rels = [release] if release is not None else ([entry.latest] if entry.latest else []) + list(entry.versiones)
            return any(r.sha256 == self.sha256 for r in rels)
        return False

    def describe(self) -> str:
        what = f"id «{self.id}»" if self.id else f"autor «{self.autor}»" if self.autor else f"sha256 {self.sha256[:12]}…"
        return f"bloqueado por {what}" + (f" ({self.motivo})" if self.motivo else "") + (f", {self.fecha}" if self.fecha else "")


@dataclass(frozen=True)
class Release:
    """Un zip publicado: lo que el índice sabe de él (el `sha256` es el que manda)."""

    version: str
    tag: str
    prerelease: bool
    publicado: str
    nombre: str
    url: str
    tamano: int
    sha256: str
    fichas: str = ""                      # URL del archivo `afm-fichas-1` de esta versión (D11/D15); "" = usar la convención `fichas/<carpeta>-<ver>.json`


@dataclass
class Entry:
    folder: str
    id: str
    nombre: str = ""
    autor: str = ""
    version: str = ""
    descripcion: str = ""
    tipo: str = ""
    licencia: str = ""
    origen: str = ""
    github: str = ""
    categorias: list = field(default_factory=list)
    etiquetas: list = field(default_factory=list)
    linea: dict = field(default_factory=dict)
    motor: str = ""
    requiere: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    experimental: bool = False
    portada: str = ""                     # URL completa (resuelta contra el índice) o ""
    descripcion_url: str = ""
    latest: Release | None = None
    versiones: list = field(default_factory=list)
    retiradas: list = field(default_factory=list)
    update_check: str = ""
    descargas: dict | None = None         # `null` (no hay contador) ≠ {"total": 0}
    indice: str = ""                      # URL del índice de donde sale
    bloqueos: tuple = ()                  # los `Block` del índice de donde sale (D16)
    raw: dict = field(default_factory=dict)

    @property
    def bloqueado(self) -> str:
        """El motivo si el índice bloquea el paquete entero (por `id` o autor); "" si no. Un bloqueo por `sha256` es de una versión: `block_of`."""
        for b in self.bloqueos:
            if (b.id or b.autor) and b.matches(self):
                return b.describe()
        return ""

    def block_of(self, release: "Release | None" = None) -> str:
        """Por qué no se puede instalar `release` (o la última): paquete bloqueado, o su `sha256` bloqueado; "" si se puede."""
        rel = release or self.latest
        for b in self.bloqueos:
            if b.matches(self, rel):
                return b.describe()
        return ""

    @property
    def visible(self) -> bool:
        """`inaccesible` o bloqueado se oculta en «Explorar» (no se borra del índice: lo primero vuelve solo si el repo se recupera)."""
        return self.update_check != "inaccesible" and not self.bloqueado

    @property
    def instalable(self) -> bool:
        return self.latest is not None

    def release(self, version: str | None = None) -> Release | None:
        """La versión pedida (o la última); busca también entre `versiones`."""
        if not version:
            return self.latest
        for r in ([self.latest] if self.latest else []) + self.versiones:
            if r.version == version:
                return r
        return None


@dataclass
class Index:
    url: str
    schema_version: int
    generado: str = ""
    categorias: list = field(default_factory=list)
    entries: list = field(default_factory=list)
    bloqueados: list = field(default_factory=list)  # `Block` (clave aditiva `bloqueados` del índice, D16)
    avisos: list = field(default_factory=list)      # entradas saltadas, claves raras…
    origen: str = "red"                              # red (200) | validado (304) | guardado (sin red)
    error_red: str = ""                              # por qué se usó el guardado
    obtenido: str = ""

    def find(self, ident: str) -> Entry | None:
        """Por `id`, o por carpeta `Autor@id`."""
        for e in self.entries:
            if ident in (e.id, e.folder):
                return e
        return None

    def visibles(self) -> list:
        return [e for e in self.entries if e.visible]


def _str(d: dict, key: str, default="") -> str:
    v = d.get(key, default)
    return v if isinstance(v, str) else default


def _str_list(d: dict, key: str) -> list:
    v = d.get(key, [])
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _release(d, base_url: str) -> Release:
    """`ValueError` con la causa si no sirve para instalar."""
    if not isinstance(d, dict) or not isinstance(d.get("zip"), dict):
        raise ValueError("falta el bloque «zip»")
    z = d["zip"]
    version = d.get("version")
    if not Q.is_semver(version):
        raise ValueError(f"versión «{version}» no es semver")
    sha = z.get("sha256")
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        raise ValueError("falta el sha256 del zip (el índice es quien lo garantiza)")
    url = z.get("url")
    if not isinstance(url, str) or not url:
        raise ValueError("falta la URL del zip")
    size = z.get("tamano", 0)
    size = size if isinstance(size, int) and not isinstance(size, bool) and size > 0 else 0
    return Release(version=version, tag=_str(d, "tag"), prerelease=bool(d.get("prerelease", False)), publicado=_str(d, "publicado"),
                   nombre=_str(z, "nombre"), url=urllib.parse.urljoin(base_url, url), tamano=size, sha256=sha,
                   fichas=urllib.parse.urljoin(base_url, d["fichas"]) if isinstance(d.get("fichas"), str) and d["fichas"] else "")


def _entry(d: dict, base_url: str, avisos: list, n: int) -> Entry | None:
    pid = d.get("id")
    if not isinstance(pid, str) or not _ID_RE.match(pid) or len(pid) > Q.ID_MAX:
        avisos.append(f"entrada {n}: «id» ausente o inválido; se salta")
        return None
    folder = d.get("folder")
    autor = _str(d, "autor")
    if not isinstance(folder, str):
        folder = f"{autor}@{pid}" if autor else ""
    if not _FOLDER_RE.match(folder) or not folder.endswith("@" + pid):
        avisos.append(f"entrada «{pid}»: la carpeta «{folder}» no es «Autor@{pid}»; se salta")
        return None
    e = Entry(folder=folder, id=pid, nombre=_str(d, "nombre") or pid, autor=autor or folder.split("@")[0], version=_str(d, "version"),
              descripcion=_str(d, "descripcion"), tipo=_str(d, "tipo"), licencia=_str(d, "licencia"), origen=_str(d, "origen"),
              github=_str(d, "github"), categorias=_str_list(d, "categorias"), etiquetas=_str_list(d, "etiquetas"),
              linea=d["linea"] if isinstance(d.get("linea"), dict) else {}, motor=_str(d, "motor"), requiere=_str_list(d, "requiere"),
              conflicts=_str_list(d, "conflicts"), experimental=bool(d.get("experimental", False)), retiradas=_str_list(d, "retiradas"),
              update_check=_str(d, "update_check"), indice=base_url, raw=d)
    for key, attr in (("portada", "portada"), ("descripcion_url", "descripcion_url")):
        if isinstance(d.get(key), str) and d[key]:
            setattr(e, attr, urllib.parse.urljoin(base_url, d[key]))
    desc = d.get("descargas")
    e.descargas = desc if isinstance(desc, dict) else None
    if d.get("latest") is not None:
        try:
            e.latest = _release(d["latest"], base_url)
        except ValueError as exc:
            avisos.append(f"«{pid}»: la última versión no se puede instalar ({exc})")
    for r in d.get("versiones") if isinstance(d.get("versiones"), list) else []:
        try:
            e.versiones.append(_release(r, base_url))
        except ValueError as exc:
            avisos.append(f"«{pid}»: una versión de la lista no sirve ({exc})")
    return e


def _blocks(items, avisos: list) -> list:
    """`bloqueados` del índice → `Block`. Tolerante: una línea rara se salta con aviso (nunca invalida el índice)."""
    if items is None:
        return []
    if not isinstance(items, list):
        avisos.append("«bloqueados» no es una lista; se ignora")
        return []
    out = []
    for n, b in enumerate(items, 1):
        if not isinstance(b, dict):
            avisos.append(f"bloqueo {n}: no es un objeto; se salta")
            continue
        keys = [k for k in ("id", "autor", "sha256") if isinstance(b.get(k), str) and b[k]]
        if len(keys) != 1:
            avisos.append(f"bloqueo {n}: tiene que tener exactamente uno de id, autor o sha256; se salta")
            continue
        if keys[0] == "sha256" and not _SHA_RE.match(b["sha256"]):
            avisos.append(f"bloqueo {n}: el sha256 no es válido; se salta")
            continue
        out.append(Block(**{keys[0]: b[keys[0]]}, motivo=_str(b, "motivo"), fecha=_str(b, "fecha")))
    return out


def parse_index(data, *, url: str = "") -> Index:
    """`index.json` → `Index`. Tolerante con claves y entradas raras; estricto solo con la forma mínima y la `schema_version`."""
    try:
        d = json.loads(data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise IndexInvalid(f"el índice no es un JSON legible: {exc}") from None
    if not isinstance(d, dict):
        raise IndexInvalid("el índice no es un objeto JSON")
    sv = d.get("schema_version")
    if not isinstance(sv, int) or isinstance(sv, bool) or sv < 1:
        raise IndexInvalid("al índice le falta «schema_version» (un entero ≥ 1)")
    if sv > INDEX_SCHEMA_SUPPORTED:
        raise IndexTooNew(f"el índice usa el formato {sv} y esta versión de afm entiende hasta el {INDEX_SCHEMA_SUPPORTED}: actualizá afm para explorarlo")
    items = d.get("paquetes")
    if not isinstance(items, list):
        raise IndexInvalid("al índice le falta la lista «paquetes»")
    idx = Index(url=url, schema_version=sv, generado=_str(d, "generado"), categorias=_str_list(d, "categorias"))
    idx.bloqueados = _blocks(d.get("bloqueados"), idx.avisos)
    seen = set()
    for n, item in enumerate(items, 1):
        if not isinstance(item, dict):
            idx.avisos.append(f"entrada {n}: no es un objeto; se salta")
            continue
        e = _entry(item, url, idx.avisos, n)
        if e is None:
            continue
        e.bloqueos = tuple(idx.bloqueados)
        if e.folder in seen:
            idx.avisos.append(f"entrada «{e.folder}» repetida; se queda la primera")
            continue
        seen.add(e.folder)
        idx.entries.append(e)
    return idx


def fetch_index(url: str, *, policy: Policy | None = None, cache: str | None = None, offline: bool = False,
                timeout: float = TIMEOUT_S, retries: int = RETRIES, sleep=time.sleep) -> Index:
    """Lee un índice (`url`: la de Pages o la del `index.json`). Revalida con la caché; sin red usa el guardado."""
    url = index_url(url)
    policy = policy or policy_for(url)
    policy.check(url)
    cache = cache or cache_dir()
    data_path, meta_path = _index_cache_paths(url, cache)
    saved, meta = None, {}
    try:
        with open(data_path, "rb") as f:
            saved = f.read()
    except OSError:
        saved = None
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}

    def from_saved(why: str) -> Index:
        if saved is None:
            if offline:
                raise OfflineError(f"modo sin red y no hay un índice guardado de {url}")
            raise NetworkError(why)
        idx = parse_index(saved, url=url)
        idx.origen, idx.error_red, idx.obtenido = "guardado", ("" if offline else why), _str(meta, "obtenido")
        return idx

    if offline:
        return from_saved("")
    headers = {}
    if saved is not None and meta.get("etag"):
        headers["If-None-Match"] = meta["etag"]
    if saved is not None and meta.get("last_modified"):
        headers["If-Modified-Since"] = meta["last_modified"]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def get():
        resp = _request(url, policy, headers=headers, timeout=timeout)
        with resp:
            return _read_capped(resp, MAX_INDEX_BYTES, "el índice"), resp.headers.get("ETag") or "", resp.headers.get("Last-Modified") or ""

    try:
        body, etag, last = _with_retries(get, url, retries=retries, sleep=sleep)
    except _NotModified:
        idx = parse_index(saved, url=url)
        idx.origen, idx.obtenido = "validado", now
        _atomic_write(meta_path, json.dumps({**meta, "obtenido": now}).encode("utf-8"))
        return idx
    except NetworkError as exc:
        return from_saved(str(exc))
    idx = parse_index(body, url=url)                  # un índice inválido o demasiado nuevo NO pisa la copia buena
    idx.origen, idx.obtenido = "red", now
    _atomic_write(data_path, body)
    _atomic_write(meta_path, json.dumps({"url": url, "etag": etag, "last_modified": last, "obtenido": now}).encode("utf-8"))
    return idx


@dataclass
class IndexResult:
    url: str
    index: Index | None = None
    error: str = ""


def fetch_indexes(urls: list[str] | None = None, *, offline: bool = False, **kw) -> list[IndexResult]:
    """Todos los índices configurados: uno caído no impide ver los demás (queda con su `error`)."""
    out = []
    for url in (urls if urls is not None else load_config()):
        try:
            out.append(IndexResult(url=url, index=fetch_index(url, offline=offline, **kw)))
        except RemoteError as exc:
            out.append(IndexResult(url=url, error=str(exc)))
    return out


# ------------------------------------------------------------------------------------------------------------------------ descargar

def download_package(entry: Entry, *, version: str | None = None, policy: Policy | None = None, cache: str | None = None,
                     allow_retirada: bool = False, progress=None, timeout: float = TIMEOUT_S, retries: int = RETRIES,
                     sleep=time.sleep) -> str:
    """Baja el zip de `entry` a la caché, verifica su `sha256` (el del índice) y devuelve la ruta. Si ya está en la caché y coincide, no usa la red.

    `progress(hecho, total)` se llama a medida que baja (`total` = 0 si no se sabe). Solo escribe bajo `<caché>/zips/`.
    """
    rel = entry.release(version)
    if rel is None:
        raise RemoteError(f"«{entry.id}» no tiene {'la versión ' + version if version else 'una versión instalable'} en el índice")
    why = entry.block_of(rel)
    if why:
        raise RemoteError(f"«{entry.id}» {rel.version} no se instala: {why}")          # un bloqueo no se salta con ninguna opción
    if rel.version in entry.retiradas and not allow_retirada:
        raise RemoteError(f"la versión {rel.version} de «{entry.id}» fue retirada por sus responsables; no se instala")
    policy = policy or policy_for(entry.indice or rel.url)
    policy.check(rel.url)
    zdir = os.path.join(cache or cache_dir(), "zips")
    final = os.path.join(zdir, rel.sha256 + ".zip")
    if os.path.isfile(final) and _file_sha(final) == rel.sha256:
        if progress:
            progress(rel.tamano, rel.tamano)
        return final
    os.makedirs(zdir, exist_ok=True)
    cap = min(MAX_ZIP_BYTES, rel.tamano) if rel.tamano else MAX_ZIP_BYTES
    part = final + ".part"

    def get():
        h, done = hashlib.sha256(), 0
        resp = _request(rel.url, policy, timeout=timeout)
        with resp, open(part, "wb") as out:
            try:
                total = int(resp.headers.get("Content-Length") or 0) or rel.tamano
            except ValueError:
                total = rel.tamano
            if total > cap:
                raise HashMismatch(f"{CHANGED_MSG} (pesa {total} bytes y el índice dijo {rel.tamano})")
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                done += len(chunk)
                if done > cap:
                    raise HashMismatch(f"{CHANGED_MSG} (supera los {cap} bytes que dijo el índice)")
                h.update(chunk)
                out.write(chunk)
                if progress:
                    progress(done, total)
            if total and done < total:                      # corte a mitad: se reintenta
                raise http.client.IncompleteRead(b"", total - done)
        return h.hexdigest(), done

    try:
        digest, done = _with_retries(get, rel.url, retries=retries, sleep=sleep)
        if digest != rel.sha256 or (rel.tamano and done != rel.tamano):
            raise HashMismatch(f"{CHANGED_MSG} (sha256 {digest[:12]}… ≠ {rel.sha256[:12]}…)")
        os.replace(part, final)
    except BaseException:
        try:
            os.unlink(part)
        except OSError:
            pass
        raise
    return final


def _file_sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def default_root(entry: Entry) -> str:
    """Destino por defecto: la carpeta de la línea del paquete (sin mirar `$AFM_CATALOGO`, D-4)."""
    name = (entry.linea or {}).get("nombre")
    if not isinstance(name, str) or not name:
        raise RemoteError(f"«{entry.id}» no dice su línea: indicá la carpeta de destino")
    return I.explicit_root(name)


def plan_entry(entry: Entry, root: str | None = None, *, version: str | None = None, como_linea: bool = False, allow_fbx: bool = False,
               allow_retirada: bool = False, **net) -> I.InstallPlan:
    """Baja el paquete (verificado) y arma el plan con `instalar.plan_install`: **el mismo camino que un `.zip` local**. No escribe en el catálogo.

    Agrega al plan lo que solo se sabe acá: que el zip diga lo mismo que el índice (id y versión ⇒ error; repo ⇒ aviso) y el índice de origen.
    """
    path = download_package(entry, version=version, allow_retirada=allow_retirada, **net)
    plan = I.plan_install(path, root or default_root(entry), como_linea=como_linea, allow_fbx=allow_fbx)
    rel = entry.release(version)
    pkg = plan.paquete
    if pkg is not None:
        if pkg.id != entry.id:
            plan.issues.append(I.Issue(I.ERROR, entry.id, f"el zip trae el paquete «{pkg.id}» y el índice lo lista como «{entry.id}»", "no se instala: avisá a quien mantiene el índice"))
        if rel is not None and pkg.version != rel.version:
            plan.issues.append(I.Issue(I.ERROR, entry.id, f"el zip es la versión {pkg.version} y el índice dice {rel.version}", "no se instala: probá más tarde"))
        if entry.github and pkg.github and pkg.github != entry.github:
            plan.issues.append(I.Issue(I.WARNING, entry.id, f"el índice liga «{entry.id}» a «{entry.github}» y el paquete dice «{pkg.github}»"))
    plan.indice = entry.indice
    return plan


# ------------------------------------------------------------------------------------------------------------------------ buscar, resolver, actualizar (lo que usan la CLI de D14 y la GUI de D17)

SORTS = ("descargas", "reciente", "nombre")


def parse_ref(text: str) -> tuple[str, str | None]:
    """`id`, `Autor@id`, `id@1.2.0` o `Autor@id@1.2.0` → (identificador, versión | None). El último tramo es versión solo si es semver."""
    text = text.strip()
    parts = text.split("@")
    if len(parts) > 1 and Q.is_semver(parts[-1]):
        return "@".join(parts[:-1]), parts[-1]
    return text, None


def _total(e: Entry) -> int:
    t = (e.descargas or {}).get("total")
    return t if isinstance(t, int) and not isinstance(t, bool) else -1          # sin contador (`null`) va al final, no como «poco popular»


def search(entries, text: str = "", *, tipo: str = "", linea: str = "", licencia: str = "", categoria: str = "", orden: str = "descargas",
           incluir_ocultas: bool = False) -> list[Entry]:
    """Filtra y ordena entradas (todas las palabras de `text` tienen que aparecer en id, nombre, descripción, autor, etiquetas o categorías)."""
    if orden not in SORTS:
        raise RemoteError(f"orden «{orden}» desconocido (usá: {', '.join(SORTS)})")
    words = [w for w in text.casefold().split() if w]
    out = []
    for e in entries:
        if not e.visible and not incluir_ocultas:
            continue
        if tipo and e.tipo != tipo or licencia and e.licencia.casefold() != licencia.casefold() or categoria and categoria not in e.categorias:
            continue
        if linea and (e.linea or {}).get("nombre") != linea:
            continue
        hay = " ".join([e.id, e.nombre, e.descripcion, e.autor, *e.etiquetas, *e.categorias]).casefold()
        if all(w in hay for w in words):
            out.append(e)
    if orden == "descargas":
        out.sort(key=lambda e: (-_total(e), e.nombre.casefold()))
    elif orden == "reciente":
        out.sort(key=lambda e: (e.latest.publicado if e.latest else "") or "", reverse=True)
    else:
        out.sort(key=lambda e: e.nombre.casefold())
    return out


def resolve(indexes, ident: str, *, instalable: bool = False) -> Entry:
    """La entrada que `ident` (`id` o `Autor@id`) nombra en los índices dados; error claro si no está o si es ambigua."""
    found = [e for idx in indexes for e in idx.entries if ident in (e.id, e.folder)]
    if not found:
        raise RemoteError(f"«{ident}» no está en los índices consultados")
    if len(found) > 1:
        raise RemoteError(f"«{ident}» está en más de un lugar ({', '.join(f'{e.folder} de {e.indice}' for e in found)}): usá Autor@id o elegí un índice con --indice")
    e = found[0]
    if instalable and e.bloqueado:
        raise RemoteError(f"«{e.folder}» no se puede instalar: {e.bloqueado}")
    if instalable and not e.visible:
        raise RemoteError(f"«{e.folder}» figura como inaccesible (su repo o su Release no responden); probá más tarde")
    return e


def installed_version(entry: Entry, root: str | None = None) -> str | None:
    """La versión instalada de `entry` en `root` (o en la carpeta de su línea), según `paquetes.json`; `None` si no está o no se puede leer."""
    try:
        root = root or default_root(entry)
        if not os.path.isdir(root):
            return None
        got = I.load_registry(root)["paquetes"].get(entry.id)
    except (RemoteError, I.InstallError):
        return None
    return got.get("version") if isinstance(got, dict) else None


def state(entry: Entry, root: str | None = None) -> str:
    """`no instalado` · `instalado X` · `actualización X` (instalado una versión más vieja que `latest`) · `inaccesible` · `sin versión instalable`."""
    if entry.bloqueado:
        return "bloqueado"
    if not entry.visible:
        return "inaccesible"
    have = installed_version(entry, root)
    if entry.latest is None:
        return "sin versión instalable" if have is None else f"instalado {have}"
    if have is None:
        return "no instalado"
    try:
        if Q.parse_semver(have) < Q.parse_semver(entry.latest.version) and entry.latest.version not in entry.retiradas:
            return f"actualización {entry.latest.version}"
    except Q.PaqueteError:
        pass
    return f"instalado {have}"


def catalog_roots() -> list[str]:
    """Los catálogos en `~/.local/share/afm/catalogos/*` que tienen `paquetes.json` (para «actualizar todo»)."""
    base = os.path.dirname(I.explicit_root("x"))
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return []
    return [os.path.join(base, n) for n in names if os.path.isfile(os.path.join(base, n, I.REGISTRY_FILE))]


def pending_updates(indexes, root: str) -> tuple[list, list]:
    """(actualizaciones, avisos) para los paquetes de `root`: `[(Entry, versión instalada)]` de los que el índice trae más nuevos.

    Se salta (con aviso) el que está instalado desde otro repo que el que dice el índice (suplantación, §5.7) y el que el índice no lista.
    """
    reg = I.load_registry(root)["paquetes"]
    todo, avisos = [], []
    for pid, got in sorted(reg.items()):
        cands = [e for idx in indexes for e in idx.entries if e.id == pid]
        if not cands:
            continue
        if len(cands) > 1:
            avisos.append(f"«{pid}» está en más de un índice; actualizalo a mano con `remoto instalar Autor@{pid} --indice URL`")
            continue
        e = cands[0]
        if got.get("github", "") != e.github:
            avisos.append(f"«{pid}» se instaló desde «{got.get('github') or 'un zip local'}» y el índice lo liga a «{e.github or 'ningún repo'}»: no se actualiza solo")
            continue
        if state(e, root).startswith("actualización"):
            todo.append((e, got.get("version", "")))
    return todo, avisos
