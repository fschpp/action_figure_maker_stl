"""Compatibilidad antes de descargar (plan de catálogos en línea, §5.5 «Fichas» y D15).

Con las **fichas** de un paquete (`afm-fichas-1`: sus `.pieza.json` sin mallas, D11) se mide, contra el catálogo local, cuántas
piezas del paquete encajan y por qué no, **sin bajar un solo STL**. Es la misma cuenta que `afm catalogo cobertura` hace sobre
el catálogo ya instalado (`guide.coverage_of_port`), con las piezas del paquete sumadas a las locales: el resultado coincide con
el de `cobertura` después de instalar, salvo una cosa que necesita las mallas y no se puede medir acá: el **choque en la pose
sentada** de un brazo o una pierna contra el torso de otra figura (`guide.SeatedClash`). Los puertos `hombro` y `cadera` lo
marcan en `PortCoverage.sin_choque`.

También avisa de lo que el instalador (`instalar.py`) va a decidir: la línea que no coincide en altura (§5.3 punto 2), las piezas
que ya están instaladas (mismo `sha256`) o que chocan (otro `sha256`, §5.3 punto 5) y los roles de color que la paleta local no tiene.

Motor puro. Lo único que toca la red es `fetch_fichas`, con la misma política de hosts y reintentos que `remoto.py`.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field

from . import indice as X
from . import parts as P
from . import remoto as R
from .catalog import LINE_HEIGHT_TOL_MM, Catalog, CatalogError
from .guide import LEVEL_RANK, coverage_of_port, load_calibration

MAX_FICHAS_BYTES = 16 * 1024 * 1024       # [P] el archivo entero (hasta `indice.MAX_FICHAS` fichas)
_SINCHOQUE = ("hombro", "cadera")         # puertos cuya cobertura real suma el choque sentado, que necesita las mallas


class FichasError(CatalogError):
    """Las fichas no se pueden leer o no son de este paquete."""


# ---------------------------------------------------------------------------------------------------------------- lectura

@dataclass
class Fichas:
    paquete: dict                                   # {id, version, linea?}
    metas: list = field(default_factory=list)       # `PieceMeta`
    avisos: list = field(default_factory=list)      # fichas que no se pudieron leer (la comparación queda incompleta)

    @property
    def linea(self) -> dict | None:
        return self.paquete.get("linea") if isinstance(self.paquete.get("linea"), dict) else None


def parse_fichas(data) -> Fichas:
    """`afm-fichas-1` (texto, bytes o dict) → `Fichas`. Estricto con el formato, tolerante con cada ficha rota (queda en `avisos`)."""
    try:
        d = json.loads(data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data) if not isinstance(data, dict) else data
    except (ValueError, UnicodeDecodeError) as exc:
        raise FichasError(f"las fichas no son un JSON legible: {exc}") from None
    if not isinstance(d, dict) or d.get("formato") != X.FICHAS_FORMAT:
        raise FichasError(f"no es un archivo {X.FICHAS_FORMAT} (formato {d.get('formato')!r})" if isinstance(d, dict) else f"no es un archivo {X.FICHAS_FORMAT}")
    head = d.get("paquete") if isinstance(d.get("paquete"), dict) else {}
    items = d.get("fichas")
    if not isinstance(items, list):
        raise FichasError("a las fichas les falta la lista «fichas»")
    out = Fichas(paquete=head)
    for n, item in enumerate(items, 1):
        try:
            m = P.PieceMeta.from_dict(item)
            if not P.ID_RE.match(m.id) or m.version < 1:
                raise P.PieceError(f"referencia «{m.id}@{m.version}» inválida")
            out.metas.append(m)
        except (ValueError, KeyError, TypeError, P.PieceError) as exc:
            out.avisos.append(f"ficha {n}: no se puede leer ({exc}); esa pieza no se midió")
    return out


def fichas_url(entry: R.Entry, version: str | None = None) -> str:
    """Dónde está el archivo de fichas de esa versión: el `fichas` del índice o, si falta, `fichas/<Autor>@<id>-<ver>.json` junto al `index.json`."""
    rel = entry.release(version)
    if rel is None:
        raise R.RemoteError(f"«{entry.id}» no tiene {'la versión ' + version if version else 'una versión instalable'} en el índice")
    if rel.fichas:
        return rel.fichas
    import urllib.parse
    return urllib.parse.urljoin(entry.indice, f"fichas/{X.ficha_name(entry.folder, rel.version)}")


def _cache_path(cache: str, url: str) -> str:
    return os.path.join(cache, "fichas", hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".json")


def fetch_fichas(entry: R.Entry, *, version: str | None = None, policy: R.Policy | None = None, cache: str | None = None,
                 offline: bool = False, timeout: float = R.TIMEOUT_S, retries: int = R.RETRIES, sleep=time.sleep) -> Fichas:
    """Baja las fichas de `entry` (una sola petición chica, sin STL) y las guarda en la caché; sin red usa la copia guardada.

    Las fichas son datos del índice, no del servidor del autor: se piden al mismo host que `index.json` (Pages) y pasan por la misma
    política de hosts. `OfflineError` si no hay red ni copia."""
    url = fichas_url(entry, version)
    policy = policy or R.policy_for(entry.indice or url)
    policy.check(url)
    path = _cache_path(cache or R.cache_dir(), url)

    def saved() -> bytes | None:
        try:
            with open(path, "rb") as f:
                return f.read()
        except OSError:
            return None

    if not offline:
        def get():
            with R._request(url, policy, timeout=timeout) as resp:
                return R._read_capped(resp, MAX_FICHAS_BYTES, "el archivo de fichas")
        try:
            body = R._with_retries(get, url, retries=retries, sleep=sleep)
            fichas = parse_fichas(body)                  # no se guarda lo que no se puede leer
            os.makedirs(os.path.dirname(path), exist_ok=True)
            R._atomic_write(path, body)
            return fichas
        except R.NetworkError as exc:
            body = saved()
            if body is None:
                raise
            fichas = parse_fichas(body)
            fichas.avisos.insert(0, f"sin conexión ({exc}); se usan las fichas guardadas")
            return fichas
    body = saved()
    if body is None:
        raise R.OfflineError("modo sin red y no hay fichas guardadas de este paquete")
    return parse_fichas(body)


# ---------------------------------------------------------------------------------------------------------------- medición

class _View:
    """Catálogo virtual: las piezas locales más las del paquete que se instalarían. Lo único que `coverage_of_port` le pide a un catálogo es `list()`."""

    def __init__(self, cat: Catalog, extra: list):
        self.params, self.root, self.name = cat.params, cat.root, cat.name
        self._all = list(cat.list(all_versions=True)) + list(extra)

    def list(self, tipo=None, tag=None, state=None, all_versions: bool = False):
        latest: dict = {}
        for m in self._all:
            if m.id not in latest or m.version > latest[m.id].version:
                latest[m.id] = m
        return [m for m in latest.values() if (tipo is None or m.tipo == tipo) and (tag is None or tag in m.etiquetas) and (state is None or m.estado == state)]


@dataclass
class PortCoverage:
    clase: str
    rol: str
    universo: int                  # piezas del catálogo con el puerto opuesto
    ok: int
    aviso: int
    no: int
    resumen: str                   # la misma línea que imprime `afm catalogo cobertura`
    mejor: str = "sin_datos"       # el mejor nivel entre las piezas del universo: ok | aviso | no | sin_datos
    motivos: list = field(default_factory=list)      # por qué «no» (de la pieza que más se acerca) o avisos del mejor ajuste
    sin_choque: bool = False       # el choque en pose sentada no se midió (pide las mallas)

    def to_dict(self) -> dict:
        return {"clase": self.clase, "rol": self.rol, "universo": self.universo, "ok": self.ok, "aviso": self.aviso, "no": self.no,
                "mejor": self.mejor, "resumen": self.resumen, "motivos": list(self.motivos), "sin_choque": self.sin_choque}


@dataclass
class PieceCompat:
    ref: str
    tipo: str
    estado: str                    # nueva | instalada (mismo sha256) | conflicto (otro sha256 con el mismo id@n)
    nivel: str                     # el peor de los mejores ajustes de sus puertos: ok | aviso | no | sin_datos (ningún puerto tiene con qué compararse)
    puertos: dict = field(default_factory=dict)
    avisos: list = field(default_factory=list)

    @property
    def encaja(self) -> bool:
        return self.nivel in ("ok", "aviso")

    def to_dict(self) -> dict:
        return {"ref": self.ref, "tipo": self.tipo, "estado": self.estado, "nivel": self.nivel, "avisos": list(self.avisos),
                "puertos": {k: v.to_dict() for k, v in self.puertos.items()}}


@dataclass
class PackageCompat:
    linea_ok: bool = True
    linea_motivo: str = ""
    piezas: list = field(default_factory=list)
    avisos: list = field(default_factory=list)

    @property
    def encajan(self) -> int:
        return sum(1 for p in self.piezas if p.encaja)

    @property
    def sin_datos(self) -> int:
        return sum(1 for p in self.piezas if p.nivel == "sin_datos")

    def resumen(self) -> str:
        n = len(self.piezas)
        if not self.linea_ok:
            return f"no entra en este catálogo: {self.linea_motivo}"
        if not n:
            return "el paquete no trae piezas (no hay nada que medir)"
        txt = f"{self.encajan} de {n} pieza{'s' if n != 1 else ''} encajan con tu catálogo"
        sd = self.sin_datos
        if sd:
            txt += f" · {sd} sin piezas con qué compararse"
        return txt

    def to_dict(self) -> dict:
        return {"linea_ok": self.linea_ok, "linea_motivo": self.linea_motivo, "resumen": self.resumen(),
                "piezas": [p.to_dict() for p in self.piezas], "avisos": list(self.avisos)}


def _best_fit(cov) -> tuple[str, list]:
    if not cov.fits:
        return "sin_datos", []
    best = min(cov.fits, key=lambda f: (LEVEL_RANK[f.nivel], -(f.compat.iou or 0.0), f.ref))
    return best.nivel, (list(best.compat.motivos) if best.nivel == "no" else list(best.compat.avisos))


def _ports_of(meta: P.PieceMeta, view: _View, calib) -> dict:
    """Cobertura de cada puerto de `meta`: la misma regla de `guide.piece_coverage` (sin el choque sentado)."""
    fig = meta.origen.get("figura") or None
    out = {}
    for name, port in meta.puertos.items():
        if name.endswith("_der") and meta.espejable:
            continue
        cov = coverage_of_port(port, meta.altura_figura_mm, view, view.params, exclude_ids=(meta.id,), exclude_figure=fig,
                               mirrorable=meta.espejable, calib=calib, port_name=name)
        if name.endswith("_der") and not cov.universo and name[:-4] + "_izq" in meta.puertos:
            continue
        mejor, motivos = _best_fit(cov)
        out[name] = PortCoverage(port.clase, port.rol, cov.universo, cov.ok, cov.aviso, cov.no, cov.summary(), mejor, motivos,
                                 sin_choque=port.clase in _SINCHOQUE)
    return out


def _piece_level(ports: dict) -> str:
    levels = [p.mejor for p in ports.values() if p.mejor != "sin_datos"]
    if not levels:
        return "sin_datos"
    return max(levels, key=lambda lv: LEVEL_RANK[lv])        # el peor de los mejores ajustes


def check(cat: Catalog, fichas: Fichas) -> PackageCompat:
    """Compatibilidad de las piezas de `fichas` con el catálogo `cat`, sin STL. No modifica nada."""
    res = PackageCompat(avisos=list(fichas.avisos))
    ln = fichas.linea
    if ln and isinstance(ln.get("altura_mm"), (int, float)) and abs(float(ln["altura_mm"]) - cat.height_mm) > LINE_HEIGHT_TOL_MM:
        res.linea_ok = False
        res.linea_motivo = (f"la línea del paquete mide {float(ln['altura_mm']):g} mm y «{cat.name}» {cat.height_mm:g} mm "
                            f"(tolerancia {LINE_HEIGHT_TOL_MM:g} mm); se puede instalar como línea nueva")
    local = {m.ref: m for m in cat.list(all_versions=True)}
    state, new = {}, []
    for m in fichas.metas:
        have = local.get(m.ref)
        state[m.ref] = "nueva" if have is None else ("instalada" if have.sha256 and have.sha256 == m.sha256 else "conflicto")
        if state[m.ref] == "nueva":
            new.append(m)
    view = _View(cat, new)
    calib = load_calibration(cat.root)
    try:
        palette_ids = set(cat.palette().ids()) if cat.palette() else set()
    except CatalogError:
        palette_ids = set()
    for m in sorted(fichas.metas, key=lambda x: (x.id, x.version)):
        ports = _ports_of(m, view, calib)
        pc = PieceCompat(m.ref, m.tipo, state[m.ref], _piece_level(ports), ports)
        if pc.estado == "conflicto":
            pc.avisos.append("ya hay una pieza con esa referencia y otro contenido: el instalador se detiene (se ofrece instalar como línea nueva)")
        used = list(dict.fromkeys(((m.color or {}).get("roles") or []) + (((m.color or {}).get("retoque") or {}).get("roles") or [])))
        miss = [r for r in used if r not in palette_ids]
        if miss:
            pc.avisos.append(f"usa roles de color que tu paleta no tiene ({', '.join(miss)}): el instalador los agrega desde la paleta del paquete")
        if any(p.sin_choque and p.universo for p in ports.values()):
            pc.avisos.append("el choque en la pose sentada no se midió (hace falta bajar las mallas); `cobertura` lo suma después de instalar")
        res.piezas.append(pc)
    return res


def report_lines(rep: PackageCompat) -> list[str]:
    """Texto para la consola y la ventana: una línea por pieza y, debajo, la cobertura de cada puerto y los motivos."""
    out = [f"Compatibilidad con tu catálogo: {rep.resumen()}"]
    for w in rep.avisos:
        out.append(f"  AVISO: {w}")
    if not rep.linea_ok:
        return out
    mark = {"ok": "ok", "aviso": "aviso", "no": "NO", "sin_datos": "—"}
    for p in rep.piezas:
        extra = "" if p.estado == "nueva" else f" [{p.estado}]"
        out.append(f"  {p.ref:<34} {p.tipo:<10} {mark[p.nivel]:<6}{extra}")
        for name, pc in p.puertos.items():
            out.append(f"      {name}: {pc.resumen}")
            for why in pc.motivos[:2]:
                out.append(f"          · {why}")
        for w in p.avisos:
            out.append(f"      AVISO: {w}")
    return out


__all__ = ["Fichas", "FichasError", "PortCoverage", "PieceCompat", "PackageCompat", "parse_fichas", "fichas_url", "fetch_fichas", "check", "report_lines"]
