"""«Explorar catálogos en línea…» sin GTK (D17): estado de la ventana, filtros, ficha, compatibilidad, plan de instalación y tareas en hilo.

La ventana (D18) y el diálogo de confirmación (D19) solo dibujan lo que sale de acá; todo el flujo se puede conducir sin ventana:

    st = OnlineState(root=ruta_del_catalogo)           # `urls` salen de ~/.config/afm/remoto.json si no se pasan
    st.refresh()                                        # lee los índices (sin red: usa el guardado)
    st.filters.text = "guemes"; cards = st.cards()      # lista filtrada y ordenada, con estado y motivo de incompatibilidad
    st.select(cards[0].entry); st.detail()              # ficha de la tarjeta elegida
    prop = st.propose(st.selected)                      # resuelve dependencias, baja y verifica, arma el plan (NO escribe)
    st.install(prop, acepto=True)                       # solo con la confirmación explícita; todo o nada (D16)

Lo que tarda (leer índices, medir compatibilidad, bajar y planificar, instalar) tiene su versión en hilo: `task_refresh`, `task_compat`,
`task_propose`, `task_install` devuelven una `Task` con progreso, cancelación cooperativa y un aviso `notify` que la ventana convierte en
`GLib.idle_add`. Instalar **no se puede cancelar una vez que empezó a escribir** (el catálogo queda entero o como estaba).
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import urllib.parse
from dataclasses import dataclass, field

from .. import __version__
from .. import dependencias as D
from .. import instalar as I
from .. import paquete as Q
from .. import remoto as R
from ..catalog import LINE_HEIGHT_TOL_MM, Catalog, CatalogError
from ..i18n import lz, tr
from ..progress import CancelToken, TaskCancelled

ALL = lz("catalog_online.todos")

MAX_COVER_BYTES = 2 * 1024 * 1024         # el índice guarda la portada reencodada (2 MB máx., plan §5.5); más que eso no es una portada
_OWNER_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")


def is_image(data: bytes) -> bool:
    """PNG, JPEG o WebP por sus primeros bytes (no se confía en la extensión ni en el tipo que diga el servidor)."""
    return data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")) or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")

# estados de una tarjeta, de más a menos importante: lo bloqueado o inaccesible no se instala; el resto cambia qué botón se ofrece
BLOQUEADO, INACCESIBLE, SIN_VERSION, RETIRADA, ACTUALIZACION, INSTALADO, NO_INSTALADO = (
    "bloqueado", "inaccesible", "sin_version", "retirada", "actualizacion", "instalado", "no_instalado")
STATES = (BLOQUEADO, INACCESIBLE, SIN_VERSION, RETIRADA, ACTUALIZACION, INSTALADO, NO_INSTALADO)

SORT_LABELS = {"descargas": lz("catalog_online.orden_descargas"), "reciente": lz("catalog_online.orden_reciente"), "nombre": lz("catalog_online.orden_nombre")}


def fmt_size(n: int) -> str:
    """«18,2 MB» · «512 KB» · «tamaño desconocido» (0)."""
    if not n:
        return tr("catalog_online.tam_desconocido")
    for unit, k in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= k:
            return f"{n / k:.1f} {unit}".replace(".", ",")
    return f"{n} B"


def downloads_text(e: R.Entry) -> str:
    """El contador de descargas o «sin contador» (`null` ≠ 0: una fuente fija no tiene contador)."""
    total = (e.descargas or {}).get("total")
    if isinstance(total, int) and not isinstance(total, bool):
        return tr("catalog_online.descargas_n", n=total)
    return tr("catalog_online.sin_contador")


# ------------------------------------------------------------------------------------------------------------------------ tarjetas

@dataclass
class Card:
    """Una fila de la lista de «Explorar»: la entrada más lo que hace falta para dibujarla sin volver a mirar el índice."""

    entry: R.Entry
    estado: str                         # uno de STATES
    texto_estado: str                   # «Instalado 1.2.0», «Actualización 1.3.0»…
    instalada: str = ""                 # versión instalada en el destino ("" si no está)
    incompatible: str = ""              # por qué no encaja con el motor o la línea del destino ("" = encaja)
    experimental: bool = False

    @property
    def instalable(self) -> bool:
        """Se puede ofrecer «Instalar» (o «Actualizar»): hay versión, no está bloqueada ni inaccesible."""
        return self.estado not in (BLOQUEADO, INACCESIBLE, SIN_VERSION)

    @property
    def titulo(self) -> str:
        return self.entry.nombre or self.entry.id

    @property
    def subtitulo(self) -> str:
        e, rel = self.entry, self.entry.latest
        bits = [(e.linea or {}).get("nombre", ""), e.licencia, fmt_size(rel.tamano) if rel and rel.tamano else ""]
        return " · ".join(b for b in bits if b)


@dataclass
class Filters:
    text: str = ""
    tipo: str = ""
    linea: str = ""
    licencia: str = ""
    categoria: str = ""
    solo_compatibles: bool = False
    orden: str = "descargas"
    incluir_ocultas: bool = False


@dataclass
class Detail:
    """La ficha del panel derecho."""

    titulo: str
    version: str
    estado: str
    rows: list = field(default_factory=list)          # [(etiqueta, valor)]
    descripcion: str = ""
    avisos: list = field(default_factory=list)
    puede_instalar: bool = False
    razon: str = ""                                    # por qué no (bloqueado, inaccesible…)
    portada: str = ""                                  # URL de la portada que el bot ya reencodó (D12); "" si no hay


@dataclass
class Proposal:
    """Lo que se mostrará en el diálogo de confirmación (D19). Existir **no** escribió nada en el catálogo."""

    entry: R.Entry
    root: str
    dep: D.DepPlan
    chain: D.Chain | None = None                       # None si las dependencias tienen errores (no se bajó nada)

    @property
    def planificado(self) -> bool:
        """¿Hay plan de instalación armado? (False si las dependencias tienen errores y no se bajó nada.) D22: un zip local lo redefine."""
        return self.chain is not None

    @property
    def errors(self) -> list:
        return list(self.dep.errors) + (self.chain.errors if self.chain else [])

    @property
    def warnings(self) -> list:
        extra = [w for p in self.chain.plans for w in p.warnings] if self.chain else []
        return list(self.dep.warnings) + extra

    @property
    def ok(self) -> bool:
        return self.chain is not None and self.chain.ok

    @property
    def nada_que_hacer(self) -> bool:
        return self.ok and self.chain.nada_que_hacer

    @property
    def cambia_origen(self) -> bool:
        return bool(self.chain and self.chain.cambia_origen)

    @property
    def pasos(self) -> list:
        return self.dep.pasos

    @property
    def tamano_total(self) -> int:
        return sum(s.release.tamano or 0 for s in self.dep.pasos)

    @property
    def piezas(self) -> list:
        return [i.name for p in self._plans() for i in p.a_instalar if i.kind == "pieza"]

    @property
    def recetas(self) -> list:
        return [i.name for p in self._plans() for i in p.a_instalar if i.kind == "receta"]

    @property
    def roles(self) -> list:
        return [r.id for p in self._plans() for r in p.roles_nuevos]

    def _plans(self) -> list:
        return self.chain.plans if self.chain else []

    def headline(self) -> str:
        """Una línea: «Trae 6 piezas, 1 receta y 5 roles de paleta nuevos (18,2 MB)»."""
        return tr("catalog_online.trae", piezas=len(self.piezas), recetas=len(self.recetas), roles=len(self.roles), tam=fmt_size(self.tamano_total))

    def lines(self) -> list[str]:
        """El plan completo en texto (lo mismo que `--simular`): paquetes en orden, qué instala cada uno y los avisos."""
        if self.chain is not None:
            return self.chain.describe() + [str(i) for i in self.dep.warnings]
        return self.dep.describe()


# ------------------------------------------------------------------------------------------------------------------------ tareas en hilo

PENDING, RUNNING, DONE, FAILED, CANCELLED = "pendiente", "en_curso", "listo", "error", "cancelado"


class Task:
    """Un trabajo en un hilo con progreso y cancelación cooperativa. `fn(task)` hace el trabajo; llama a `task.progress(hecho, total)`
    (que lanza `TaskCancelled` si se pidió cancelar) y puede poner `task.text`. `notify()` se llama desde el hilo en cada cambio: la
    ventana lo envuelve en `GLib.idle_add` (acá no se toca GTK)."""

    def __init__(self, fn, *, notify=None, name: str = "afm-catalogo-en-linea"):
        self._fn, self._notify, self._name = fn, notify, name
        self._token = CancelToken()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancelable = True
        self.status = PENDING
        self.text = ""
        self.done_bytes = 0
        self.total_bytes = 0
        self.result = None
        self.error: BaseException | None = None

    # ---- desde el hilo
    def progress(self, done: int = 0, total: int = 0, text: str | None = None) -> None:
        if self._token.cancelled and self._cancelable:
            raise TaskCancelled()
        with self._lock:
            self.done_bytes, self.total_bytes = done, total
            if text is not None:
                self.text = text
        self._emit()

    def set_text(self, text: str) -> None:
        with self._lock:
            self.text = text
        self._emit()

    def lock_cancel(self) -> None:
        """Desde acá ya no se cancela (se está escribiendo el catálogo)."""
        self._cancelable = False

    def _emit(self) -> None:
        if self._notify is not None:
            try:
                self._notify()
            except Exception:                         # noqa: BLE001 — la ventana no puede romper el trabajo
                pass

    def _run(self) -> None:
        try:
            result = self._fn(self)
            with self._lock:
                self.result, self.status = result, DONE
        except TaskCancelled:
            with self._lock:
                self.status = CANCELLED
        except BaseException as exc:                  # noqa: BLE001 — se guarda y la ventana lo muestra
            with self._lock:
                self.error, self.status = exc, FAILED
        self._emit()

    # ---- desde la ventana
    def start(self) -> "Task":
        with self._lock:
            self.status = RUNNING
        self._thread = threading.Thread(target=self._run, daemon=True, name=self._name)
        self._thread.start()
        return self

    def cancel(self) -> bool:
        """Pide cancelar; `False` si ya no se puede (terminó o está escribiendo)."""
        if self.finished or not self._cancelable:
            return False
        self._token.cancel()
        return True

    def join(self, timeout: float | None = None) -> bool:
        if self._thread is not None:
            self._thread.join(timeout)
        return self.finished

    @property
    def finished(self) -> bool:
        return self.status in (DONE, FAILED, CANCELLED)

    @property
    def cancelable(self) -> bool:
        return self._cancelable and not self.finished

    @property
    def fraction(self) -> float | None:
        """0..1 mientras se sabe el total; `None` si no (la barra «late»)."""
        return min(1.0, self.done_bytes / self.total_bytes) if self.total_bytes else None

    @property
    def error_text(self) -> str:
        return str(self.error) if self.error is not None else ""


# ------------------------------------------------------------------------------------------------------------------------ estado de la ventana

class OnlineState:
    """Todo lo que la ventana «Explorar catálogos en línea…» necesita saber, sin GTK.

    `root` es el catálogo destino (el que está abierto en la app); vacío = la carpeta de la línea de cada paquete (`remoto.default_root`).
    `net` son opciones de red que se pasan tal cual a `remoto` (`policy=`, `cache=`; las pruebas usan un servidor local)."""

    def __init__(self, urls: list[str] | None = None, root: str = "", *, offline: bool = False, engine_version: str = __version__, **net):
        self.urls = list(urls) if urls is not None else R.load_config()
        self.root = root
        self.offline = offline
        self.engine_version = engine_version.split("+")[0]
        self.net = net
        self.filters = Filters()
        self.indexes: list[R.Index] = []
        self.errors: list[str] = []                   # índices que no se pudieron leer (ni de la copia guardada)
        self._selected: tuple[str, str] | None = None
        self._height_cache: dict[str, tuple[str, float] | None] = {}

    # ---- índices
    def refresh(self, *, offline: bool | None = None) -> None:
        """Lee todos los índices (uno caído no tapa a los demás; sin red se usa la copia guardada)."""
        if offline is not None:
            self.offline = offline
        self.indexes, self.errors = [], []
        if not self.urls:
            self.errors.append(tr("catalog_online.no_hay_indices"))
            return
        for res in R.fetch_indexes(self.urls, offline=self.offline, **self.net):
            if res.error:
                self.errors.append(tr("catalog_online.indice_error", url=res.url, error=res.error))
            else:
                self.indexes.append(res.index)

    @property
    def sin_red(self) -> bool:
        """Algún índice sale de la copia guardada (no hay conexión o se pidió el modo sin red)."""
        return any(i.origen == "guardado" for i in self.indexes)

    def notices(self) -> list[str]:
        """Los avisos de arriba de la lista: sin red, índices caídos, entradas saltadas y «listado no verificado»."""
        out = list(self.errors)
        for idx in self.indexes:
            if idx.origen == "guardado":
                out.append(tr("catalog_online.sin_red_guardado", fecha=idx.obtenido or tr("catalog_online.fecha_desconocida"), url=idx.url))
            out += [f"{idx.url}: {a}" for a in idx.avisos]
        out.append(tr("catalog_online.aviso_no_verificado"))
        return out

    def entries(self) -> list[R.Entry]:
        return [e for idx in self.indexes for e in idx.entries]

    # ---- lista
    def options(self) -> dict:
        """Los valores que ofrecen los desplegables (solo lo que hay en la lista visible)."""
        vis = [e for e in self.entries() if e.visible]
        return {"tipo": sorted({e.tipo for e in vis if e.tipo}), "linea": sorted({(e.linea or {}).get("nombre", "") for e in vis} - {""}),
                "licencia": sorted({e.licencia for e in vis if e.licencia}), "categoria": sorted({c for e in vis for c in e.categorias}),
                "orden": list(R.SORTS)}

    def _local_line(self, root: str) -> tuple[str, float] | None:
        """(nombre, altura) de la línea del catálogo `root`, o `None` si no hay catálogo ahí. Se recuerda por ruta durante una lista."""
        if root not in self._height_cache:
            try:
                cat = Catalog.open(root)
                self._height_cache[root] = (cat.name, cat.height_mm)
            except (CatalogError, OSError, KeyError, ValueError):
                self._height_cache[root] = None
        return self._height_cache[root]

    def _dest(self, e: R.Entry) -> str:
        if self.root:
            return self.root
        try:
            return R.default_root(e)
        except R.RemoteError:
            return ""

    def incompatible(self, e: R.Entry) -> str:
        """Por qué el paquete no encaja con este motor o con la línea del destino ("" si encaja). Mira solo el índice: no baja nada."""
        if e.motor:
            try:
                if not Q.parse_range(e.motor).satisfies(self.engine_version):
                    return tr("catalog_online.motivo_motor", motor=e.motor, version=self.engine_version)
            except Q.PaqueteError:
                pass
        height = (e.linea or {}).get("altura_mm")
        dest = self._dest(e)
        local = self._local_line(dest) if dest else None
        if local and isinstance(height, (int, float)) and not isinstance(height, bool) and abs(float(height) - local[1]) > LINE_HEIGHT_TOL_MM:
            return tr("catalog_online.motivo_linea", altura=f"{float(height):g}", nombre=local[0], local=f"{local[1]:g}")
        return ""

    def card(self, e: R.Entry) -> Card:
        root = self.root or None
        raw = R.state(e, root)
        have = R.installed_version(e, root) or ""
        if raw == "bloqueado":
            estado, text = BLOQUEADO, tr("catalog_online.estado_bloqueado")
        elif raw == "inaccesible":
            estado, text = INACCESIBLE, tr("catalog_online.estado_inaccesible")
        elif raw == "sin versión instalable":
            estado, text = SIN_VERSION, tr("catalog_online.estado_sin_version")
        elif have and have in e.retiradas:
            estado, text = RETIRADA, tr("catalog_online.estado_retirada", version=have)
        elif raw.startswith("actualización"):
            estado, text = ACTUALIZACION, tr("catalog_online.estado_actualizacion", version=raw.split(" ", 1)[1])
        elif raw.startswith("instalado"):
            estado, text = INSTALADO, tr("catalog_online.estado_instalado", version=have)
        else:
            estado, text = NO_INSTALADO, tr("catalog_online.estado_no_instalado")
        return Card(e, estado, text, have, self.incompatible(e) if estado not in (BLOQUEADO, INACCESIBLE, SIN_VERSION) else "", bool(e.experimental))

    def cards(self) -> list[Card]:
        """La lista de «Explorar» con los filtros y el orden de `self.filters`. Las entradas sin contador de descargas van al final."""
        f = self.filters
        self._height_cache.clear()
        found = R.search(self.entries(), f.text, tipo=f.tipo, linea=f.linea, licencia=f.licencia, categoria=f.categoria, orden=f.orden,
                         incluir_ocultas=f.incluir_ocultas)
        cards = [self.card(e) for e in found]
        return [c for c in cards if not c.incompatible] if f.solo_compatibles else cards

    # ---- selección y ficha
    def select(self, entry: R.Entry | None) -> None:
        self._selected = (entry.indice, entry.folder) if entry is not None else None

    @property
    def selected(self) -> R.Entry | None:
        if self._selected is None:
            return None
        for e in self.entries():
            if (e.indice, e.folder) == self._selected:
                return e
        return None

    def detail(self, entry: R.Entry | None = None) -> Detail | None:
        e = entry or self.selected
        if e is None:
            return None
        card = self.card(e)
        rel = e.latest
        line = e.linea or {}
        alt = line.get("altura_mm")
        dash = "—"
        rows = [(tr("catalog_online.l_autor"), e.autor or dash), (tr("catalog_online.l_licencia"), e.licencia or dash),
                (tr("catalog_online.l_origen"), e.origen or dash), (tr("catalog_online.l_tipo"), e.tipo or dash),
                (tr("catalog_online.l_linea"), (line.get("nombre", dash)) + (f" ({alt:g} mm)" if isinstance(alt, (int, float)) and not isinstance(alt, bool) else "")),
                (tr("catalog_online.l_motor"), e.motor or dash), (tr("catalog_online.l_categorias"), ", ".join(e.categorias) or dash),
                (tr("catalog_online.l_repo"), e.github or dash), (tr("catalog_online.l_requiere"), ", ".join(e.requiere) or dash),
                (tr("catalog_online.l_no_convive"), ", ".join(e.conflicts) or dash), (tr("catalog_online.l_descargas"), downloads_text(e))]
        if rel:
            rows.append((tr("catalog_online.l_zip"), f"{rel.nombre or dash}, {fmt_size(rel.tamano)}"))
        if e.retiradas:
            rows.append((tr("catalog_online.l_retiradas"), ", ".join(e.retiradas)))
        avisos = []
        if card.incompatible:
            avisos.append(card.incompatible)
        if e.experimental:
            avisos.append(tr("catalog_online.aviso_experimental"))
        if rel is not None and rel.prerelease:
            avisos.append(tr("catalog_online.aviso_prerelease"))
        razon = ""
        if card.estado == BLOQUEADO:
            razon = tr("catalog_online.bloqueado_error", folder=e.folder, motivo=e.bloqueado)
        elif card.estado == INACCESIBLE:
            razon = tr("catalog_online.inaccesible_error", folder=e.folder)
        elif card.estado == SIN_VERSION:
            razon = tr("catalog_online.sin_version", id=e.id)
        avisos.append(tr("catalog_online.aviso_no_verificado"))
        return Detail(card.titulo, rel.version if rel else "", card.texto_estado, rows, e.descripcion, avisos, card.instalable, razon, e.portada)

    def compat_lines(self, e: R.Entry, *, progress=None) -> list[str]:
        """D15: cuántas piezas del paquete encajan en el catálogo destino, sin bajar ningún STL (baja solo las fichas). Es red: usar `task_compat`."""
        from .. import compat_remota as CR

        if not e.instalable:
            return [tr("catalog_online.compat_sin_version")]
        root = self._dest(e)
        try:
            cat = Catalog.open(root)
        except (CatalogError, OSError, KeyError, ValueError):
            return [tr("catalog_online.compat_sin_catalogo", root=root or "—")]
        net = {k: v for k, v in self.net.items() if k in ("policy", "cache")}
        return CR.report_lines(CR.check(cat, CR.fetch_fichas(e, offline=self.offline, **net)))

    def cover(self, e: R.Entry) -> bytes | None:
        """La portada de la ficha (bytes de una imagen) o `None`: no tiene, no se pudo bajar o no es una imagen. Es opcional, así que **nunca**
        falla: sin red sale de la caché y, si no hay, la ficha se muestra sin imagen. Es red: usar `task_cover`."""
        if not e.portada:
            return None
        path = os.path.join(self.net.get("cache") or R.cache_dir(), "portadas", hashlib.sha256(e.portada.encode("utf-8")).hexdigest()[:24])
        data = None
        try:
            with open(path, "rb") as f:
                data = f.read(MAX_COVER_BYTES + 1)
        except OSError:
            pass
        if data is None and not self.offline:
            try:
                policy = self.net.get("policy") or R.policy_for(e.indice or e.portada)
                with R._request(e.portada, policy, timeout=R.TIMEOUT_S) as resp:
                    data = R._read_capped(resp, MAX_COVER_BYTES, "la portada")
            except (R.RemoteError, OSError):
                return None
            if is_image(data):                              # lo que no es una imagen no se guarda
                try:
                    R._atomic_write(path, data)
                except OSError:
                    pass
        return data if data and len(data) <= MAX_COVER_BYTES and is_image(data) else None

    # ---- índices propios
    def add_index(self, text: str) -> str:
        """«Agregar índice…»: valida la URL (HTTPS; `owner/repo` y la baja de índices son de D33), la suma a la configuración y a esta lista.

        Devuelve la URL del `index.json`. Agregar un índice no instala nada ni baja ningún paquete; la advertencia de confianza la muestra la ventana."""
        raw = text.strip()
        if not raw:
            raise R.RemoteError(tr("catalog_online.indice_vacio"))
        if _OWNER_REPO.match(raw):
            raise R.RemoteError(tr("catalog_online.indice_owner_repo"))
        u = urllib.parse.urlsplit(raw)
        loopback = (u.hostname or "") in ("127.0.0.1", "localhost", "::1")
        if not u.hostname or not (u.scheme == "https" or (u.scheme == "http" and loopback)):
            raise R.RemoteError(tr("catalog_online.indice_https"))
        url = R.index_url(raw)
        known = {R.index_url(u) for u in [*self.urls, *R.load_config()]}      # la lista puede traer la base de Pages y no la URL del index.json
        if url in known:
            raise R.RemoteError(tr("catalog_online.indice_repetido", url=url))
        R.save_config(R.load_config() + [url])                # a la configuración se suma sobre lo que ya había (no se pisa con una lista de prueba)
        self.urls.append(url)
        return url

    # ---- instalar
    def propose(self, e: R.Entry, version: str | None = None, *, como_linea: bool = False, allow_fbx: bool = False, allow_retirada: bool = False,
                progress=None, root: str = "") -> Proposal:
        """Resuelve lo que el paquete `requiere`, baja y verifica cada zip (`sha256` del índice) y arma el plan. **No escribe en el catálogo.**

        Errores de uso como excepción (`remoto.RemoteError`): versión inexistente, retirada, bloqueado o inaccesible. Errores del plan (línea
        de otra altura, colisión…) quedan en `Proposal.errors` para mostrarlos en el diálogo. `root` elige otra carpeta de destino (D19: la de su línea
        cuando se instala «como línea nueva»); vacío = el catálogo abierto o la carpeta de la línea del paquete."""
        rel = e.release(version)
        if rel is None:
            raise R.RemoteError(tr("catalog_online.sin_version_pedida", id=e.id, version=version) if version else tr("catalog_online.sin_version", id=e.id))
        if e.bloqueado or e.block_of(rel):
            raise R.RemoteError(tr("catalog_online.bloqueado_error", folder=e.folder, motivo=e.bloqueado or e.block_of(rel)))
        if not e.visible:
            raise R.RemoteError(tr("catalog_online.inaccesible_error", folder=e.folder))
        if rel.version in e.retiradas and not allow_retirada:
            raise R.RemoteError(tr("catalog_online.retirada_error", id=e.id, version=rel.version))
        root = root or self._dest(e)
        if not root:
            raise R.RemoteError(tr("catalog_online.sin_destino", id=e.id))
        dep = D.resolve(self.indexes, root, [(e, version)], allow_retirada=allow_retirada)
        if dep.errors:
            return Proposal(e, root, dep)
        net = {k: v for k, v in self.net.items() if k in ("policy", "cache")}
        chain = D.plan_chain(dep, como_linea=como_linea, allow_fbx=allow_fbx, allow_retirada=allow_retirada,
                             progress=self._wrap_progress(progress, dep), **net)
        return Proposal(e, root, dep, chain)

    @staticmethod
    def _wrap_progress(progress, dep: D.DepPlan):
        """`plan_chain` baja varios zips con el mismo aviso `(hecho, total)`: se suman en una sola barra. Un zip terminó cuando `hecho >= total`;
        el aviso que sigue ya es del siguiente."""
        if progress is None:
            return None
        whole = sum(s.release.tamano or 0 for s in dep.pasos)
        st = {"base": 0, "total": 0, "complete": False}

        def inner(done: int, total: int) -> None:
            if st["complete"]:
                st["base"] += st["total"]
                st["complete"] = False
            st["total"] = total or st["total"]
            if total and done >= total:
                st["complete"] = True
            progress(st["base"] + done, whole or st["base"] + total)
        return inner

    def install(self, prop: Proposal, *, acepto: bool = False, confirmar_origen: bool = False) -> list:
        """Escribe el catálogo. **Sin `acepto=True` no escribe nada** (D19: el diálogo lo pasa recién tras «Instalar»). Todo o nada (D16)."""
        if not acepto:
            raise I.InstallError(tr("catalog_online.sin_confirmar"))
        if not prop.ok:
            raise I.InstallError(tr("catalog_online.plan_con_errores", errores="; ".join(str(i) for i in prop.errors[:3])))
        if prop.cambia_origen and not confirmar_origen:
            raise I.InstallError(tr("catalog_online.cambia_origen"))
        return D.install_chain(prop.chain, confirmar_origen=confirmar_origen)

    @staticmethod
    def result_lines(results: list) -> list[str]:
        """El informe de una instalación (uno por paquete) con sus avisos."""
        if not results:
            return [tr("catalog_online.nada_que_instalar")]
        out = []
        for r in results:
            line = tr("catalog_online.instalado_ok", id=r.paquete.id, version=r.paquete.version, piezas=len(r.piezas), recetas=len(r.recetas), roles=len(r.roles))
            out.append(line + (tr("catalog_online.ya_estaban", n=len(r.omitidas)) if r.omitidas else ""))
            out += [str(w) for w in r.avisos]                   # ya vienen con su «AVISO …»
        return out

    # ---- actualizaciones y alertas (D20 las muestra en «Paquetes instalados»)
    def roots(self) -> list[str]:
        return [self.root] if self.root else R.catalog_roots()

    def updates(self) -> tuple[list, list[str]]:
        """([(Entry, versión instalada, destino)], avisos) de los paquetes instalados que el índice trae más nuevos."""
        todo, avisos = [], []
        for root in self.roots():
            try:
                found, warn = R.pending_updates(self.indexes, root)
            except (I.InstallError, OSError) as exc:
                avisos.append(f"{root}: {exc}")
                continue
            todo += [(e, have, root) for e, have in found]
            avisos += warn
        return todo, avisos

    def alerts(self) -> list:
        """Retiradas y bloqueados que afectan a lo instalado (`dependencias.audit`). No borra nada."""
        out = []
        for root in self.roots():
            try:
                out += D.audit(self.indexes, root)
            except (I.InstallError, OSError):
                continue
        return out

    # ---- las mismas operaciones, en un hilo
    def task_refresh(self, *, offline: bool | None = None, notify=None) -> Task:
        def work(task: Task):
            task.set_text(tr("catalog_online.leyendo_indices"))
            self.refresh(offline=offline)
        return Task(work, notify=notify, name="afm-indices")

    def task_compat(self, e: R.Entry, *, notify=None) -> Task:
        def work(task: Task):
            task.set_text(tr("catalog_online.midiendo"))
            return self.compat_lines(e)
        return Task(work, notify=notify, name="afm-compat")

    def task_cover(self, e: R.Entry, *, notify=None) -> Task:
        return Task(lambda task: self.cover(e), notify=notify, name="afm-portada")

    def task_propose(self, e: R.Entry, version: str | None = None, *, notify=None, **kw) -> Task:
        def work(task: Task):
            def progress(done: int, total: int) -> None:
                task.progress(done, total, tr("catalog_online.descargando", nombre=e.folder, hecho=fmt_size(done), total=fmt_size(total)))
            return self.propose(e, version, progress=progress, **kw)
        return Task(work, notify=notify, name="afm-descarga")

    def task_install(self, prop: Proposal, *, acepto: bool = False, confirmar_origen: bool = False, notify=None) -> Task:
        def work(task: Task):
            if not acepto:                                # sin confirmar no se llega a escribir (ni se bloquea la cancelación)
                raise I.InstallError(tr("catalog_online.sin_confirmar"))
            task.progress(0, 0, tr("catalog_online.instalando"))
            task.lock_cancel()
            return self.install(prop, acepto=True, confirmar_origen=confirmar_origen)
        return Task(work, notify=notify, name="afm-instalar")
