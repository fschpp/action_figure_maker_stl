"""Instalar un paquete `.zip` `afm-paquete-1` en un catálogo (plan de catálogos en línea, §5.3, §5.4 y D05).

Dos pasos separados:

* `plan_install(zip, raiz)` **no escribe nada**: valida el paquete (`validar`, D04, sobre el lector endurecido de `ziplector`, D08),
  aplica las reglas del §5.3 contra el catálogo de destino y devuelve un `InstallPlan` con lo que se instalaría, lo que se omite
  (mismo `sha256`), los conflictos y los avisos. Un hallazgo de severidad *error* impide aplicarlo.
* `apply(plan)` instala de forma **atómica**: extrae a una carpeta de *staging* dentro del catálogo (mismo disco ⇒ el movimiento es un
  `os.replace`), comprueba los hashes y corre `Catalog.verify` sobre lo extraído, mueve los archivos, fusiona la paleta, regenera
  `CATALOGO.md` y escribe `paquetes.json` **al final**. Si algo falla, un diario deshace todo (archivos movidos, paleta, índice,
  registro y carpetas creadas): el árbol del catálogo queda idéntico al de antes.

Qué se conserva al instalar: las piezas con su `id@n` **tal cual** (no se renumera: un paquete puede traer `pierna-x-01@3` sin el `@1`; las
recetas fijan `id@versión`), las recetas, y de la paleta solo los roles que el catálogo no tenía (si el id ya existe con otro color,
**gana el local** y se avisa). No se instalan `README.md`, `LICENSE`, `CREDITOS.md`, la portada ni `paquete.json`: de ellos se guarda en
`paquetes.json` lo que hace falta (licencia, autores, origen, qué agregó el paquete).

`paquetes.json` (estado local, **no se comparte**; `Catalog.verify` y los demás comandos lo ignoran: D-9) es el registro del §5.4. Módulo de
motor puro: sin GTK, sin red, sin `manifold3d`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import __version__
from . import parts as P
from . import paquete as Q
from . import validar as V
from . import ziplector as Z
from .catalog import LINE_HEIGHT_TOL_MM, Catalog, CatalogError
from .palette import Palette, PaletteError

REGISTRY_FILE = "paquetes.json"
REGISTRY_FORMAT = "afm-instalados-1"
STAGING_PREFIX = ".instalando-"
ERROR, WARNING = "error", "aviso"
_PIECE_RE = re.compile(r"^piezas/(?P<tipo>[a-z_]+)/(?P<id>[a-z0-9]+(?:-[a-z0-9]+)*)@(?P<v>[1-9]\d*)\.pieza\.json$")
_RECIPE_RE = re.compile(r"^figuras/(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)\.receta\.json$")
_PARAMS_SHOWN = 6                         # cuántas diferencias de perfil se nombran en el aviso


class InstallError(CatalogError):
    """No se puede instalar (el plan tiene errores, el zip cambió, falló la verificación…); el catálogo no se toca."""


@dataclass(frozen=True)
class Issue:
    severity: str                         # ERROR | WARNING
    subject: str
    message: str
    fix: str = ""

    def __str__(self):
        tag = "ERROR" if self.severity == ERROR else "AVISO"
        return f"{tag} {self.subject}: {self.message}" + (f" → {self.fix}" if self.fix else "")


@dataclass
class Item:
    """Un elemento del paquete: `kind` = pieza | receta; `action` = instalar | omitir."""

    kind: str
    name: str                             # `id@n` o nombre de la receta
    action: str
    detail: str = ""
    files: list = field(default_factory=list)       # rutas dentro del zip (con «/»)


@dataclass
class InstallPlan:
    zip_path: str
    zip_sha256: str
    root: str
    paquete: Q.Paquete | None = None
    como_linea: bool = False              # el destino no existe todavía: se crea la línea con el `linea.json` del paquete
    items: list = field(default_factory=list)
    roles_nuevos: list = field(default_factory=list)      # `palette.Role` que se agregan a la paleta del destino
    issues: list = field(default_factory=list)
    cambia_origen: bool = False           # el id ya estaba instalado desde otro repo (§5.7): pide confirmación explícita
    sugerir_linea: bool = False           # no encaja en este catálogo: se puede instalar como línea nueva
    registro_previo: dict | None = None
    allow_fbx: bool = False

    @property
    def errors(self) -> list:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def ok(self) -> bool:
        return self.paquete is not None and not self.errors

    @property
    def a_instalar(self) -> list:
        return [i for i in self.items if i.action == "instalar"]

    @property
    def omitidas(self) -> list:
        return [i for i in self.items if i.action == "omitir"]

    @property
    def nada_que_hacer(self) -> bool:
        return self.ok and not self.a_instalar and not self.roles_nuevos and not self.como_linea

    def describe(self) -> list[str]:
        """El plan en líneas de texto (lo que imprime `--simular`)."""
        p = self.paquete
        out = []
        if p is not None:
            out.append(f"Paquete «{p.id}» {p.version} ({p.tipo}) de {', '.join(a.nombre for a in p.autores) or '—'}; licencia {p.licencia}, origen {p.origen}")
        out.append(("Se crea la línea nueva en " if self.como_linea else "Destino: ") + self.root)
        for kind, label in (("pieza", "Piezas"), ("receta", "Recetas")):
            add = [i for i in self.a_instalar if i.kind == kind]
            skip = [i for i in self.omitidas if i.kind == kind]
            if add:
                out.append(f"{label} a instalar ({len(add)}): " + ", ".join(i.name for i in add))
            if skip:
                out.append(f"{label} que ya están (se omiten, {len(skip)}): " + ", ".join(i.name for i in skip))
        if self.roles_nuevos:
            out.append("Roles de paleta a agregar: " + ", ".join(f"{r.id} ({r.hex})" for r in self.roles_nuevos))
        out += [str(i) for i in self.issues]
        return out


@dataclass
class InstallResult:
    paquete: Q.Paquete
    root: str
    piezas: list                          # `id@n` agregadas
    recetas: list
    roles: list
    omitidas: list
    avisos: list = field(default_factory=list)


# ------------------------------------------------------------------------------------------------------------------------ utilidades

def explicit_root(line: str) -> str:
    """`~/.local/share/afm/catalogos/<linea>` **sin** mirar `$AFM_CATALOGO` (D-4: con esa variable puesta, `default_root` daría la misma ruta
    para todas las líneas, y un paquete se instalaría encima de otra)."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "afm", "catalogos", line)


def peek_package(zip_path: str) -> Q.Paquete:
    """Lee solo el `paquete.json` de un zip (para saber la línea y proponer un destino). `InstallError` si falta o no se entiende."""
    with Z.open_zip(zip_path) as zf:
        try:
            return Q.Paquete.from_json(zf.read(Q.MANIFEST_FILE).decode("utf-8"))
        except KeyError:
            raise InstallError(f"«{zip_path}» no trae {Q.MANIFEST_FILE}: no es un paquete afm-paquete-1") from None
        except (Q.PaqueteError, UnicodeDecodeError, ValueError) as exc:
            raise InstallError(f"{Q.MANIFEST_FILE} no se puede leer: {exc}") from None


def tree_digest(root: str) -> dict:
    """`{ruta relativa: sha256 | None}` de todos los archivos y carpetas (None) bajo `root`: sirve para comprobar que un fallo no dejó nada."""
    out = {}
    for base, dirs, files in os.walk(root):
        for d in dirs:
            out[os.path.relpath(os.path.join(base, d), root)] = None
        for f in files:
            out[os.path.relpath(os.path.join(base, f), root)] = P.sha256_file(os.path.join(base, f))
    return out


def load_registry(root: str) -> dict:
    """`paquetes.json` del catálogo; vacío si no existe. Lectura tolerante: un registro ilegible es `InstallError` (no se pisa a ciegas)."""
    path = os.path.join(root, REGISTRY_FILE)
    if not os.path.isfile(path):
        return {"formato": REGISTRY_FORMAT, "paquetes": {}}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not isinstance(d.get("paquetes", {}), dict):
            raise ValueError("no es un registro de paquetes")
    except (OSError, ValueError) as exc:
        raise InstallError(f"{REGISTRY_FILE} no se puede leer: {exc}") from None
    d.setdefault("formato", REGISTRY_FORMAT)
    d.setdefault("paquetes", {})
    return d


def _read_json(zf: zipfile.ZipFile, name: str):
    return json.loads(zf.read(name).decode("utf-8"))


def _plain(values) -> list:
    return [v for v in values if v]


# ---------------------------------------------------------------------------------------------------------------------------- plan

def plan_install(zip_path: str, root: str, *, como_linea: bool = False, allow_fbx: bool = False) -> InstallPlan:
    """Plan de instalar `zip_path` en el catálogo `root` (reglas del §5.3). **No escribe nada.** `ZipError` si ni siquiera es un zip.

    Si `root` no es un catálogo se instala como **línea nueva** (con el `linea.json` del paquete); con `como_linea=True` el destino no puede
    ser un catálogo que ya existe."""
    root = os.path.abspath(root)
    plan = InstallPlan(zip_path=zip_path, zip_sha256=P.sha256_file(zip_path), root=root, allow_fbx=allow_fbx)

    def add(sev, subject, msg, fix=""):
        plan.issues.append(Issue(sev, subject, msg, fix))

    res = V.validate_package(zip_path, allow_fbx=allow_fbx, verify_catalog=False)
    plan.paquete = res.paquete
    for f in res.findings:
        add(ERROR if f.severity == V.ERROR else WARNING, f"{f.code or 'paquete'} {f.path}".strip(), f.message, f.fix)
    pkg = res.paquete
    if pkg is None or not res.ok:
        return plan
    if pkg.tipo == "coleccion":
        add(ERROR, Q.MANIFEST_FILE, "es una colección (lista de paquetes fijados): se instala con `afm catalogo remoto instalar`, no desde un zip", "")
        return plan

    exists = os.path.isfile(os.path.join(root, "linea.json"))
    if como_linea and exists:
        add(ERROR, root, "ya es un catálogo: --como-linea necesita una carpeta nueva", "elegí otra ruta con --en")
        return plan
    if not exists:
        if os.path.exists(root) and (not os.path.isdir(root) or os.listdir(root)):
            add(ERROR, root, "existe, no es un catálogo y no está vacía: no se instala una línea nueva encima", "elegí una carpeta nueva con --en")
            return plan
        plan.como_linea = True
    cat = Catalog.open(root) if exists else None

    with Z.open_zip(zip_path) as zf:
        insp = Z.inspect(zf, allow_fbx=allow_fbx)
        names = {e.name for e in insp.entries}
        try:
            line_d = _read_json(zf, "linea.json") if "linea.json" in names else None
        except (ValueError, UnicodeDecodeError) as exc:
            add(ERROR, "linea.json", f"no se puede leer: {exc}")
            return plan
        if line_d is None:
            add(ERROR, "linea.json", "el paquete no trae linea.json", "volvé a exportarlo")
            return plan

        # ---- 2 y 3 · línea de destino y perfil (§5.3)
        if cat is not None:
            _check_line(plan, add, cat, line_d)
        else:
            if line_d.get("formato") != "afm-linea-1":
                add(ERROR, "linea.json", f"formato {line_d.get('formato')!r}, se esperaba 'afm-linea-1'", "actualizá afm o volvé a exportar")

        # ---- 8 · motor
        if pkg.motor:
            try:
                if not Q.parse_range(pkg.motor).satisfies(__version__):
                    add(WARNING, Q.MANIFEST_FILE, f"pide el motor {pkg.motor} y este es {__version__}", "actualizá afm")
            except Q.PaqueteError:
                pass

        # ---- piezas y recetas del paquete contra el destino (1, 5, 6)
        _plan_pieces(plan, add, zf, names, cat)
        _plan_recipes(plan, add, zf, names, cat)

        # ---- 4 · paleta
        _plan_palette(plan, add, zf, names, cat, pkg)

    # ---- registro local: reinstalación, origen, dependencias y conflictos (§5.4, §5.7, §5.9)
    if exists:
        try:
            reg = load_registry(root)
        except InstallError as exc:
            add(ERROR, REGISTRY_FILE, str(exc), "arreglalo o movelo antes de instalar")
            return plan
        _plan_registry(plan, add, reg, pkg)
    return plan


def _check_line(plan, add, cat: Catalog, line_d: dict) -> None:
    pkg = plan.paquete
    height = (pkg.linea or {}).get("altura_mm", line_d.get("altura_mm"))
    try:
        height = float(height)
    except (TypeError, ValueError):
        add(ERROR, "linea.json", "no tiene altura_mm")
        return
    ref_pkg = (line_d.get("params") or {}).get("ref_height")
    ref_cat = cat.params.ref_height
    if abs(height - cat.height_mm) > LINE_HEIGHT_TOL_MM or (ref_pkg is not None and abs(float(ref_pkg) - ref_cat) > LINE_HEIGHT_TOL_MM):
        plan.sugerir_linea = True
        add(ERROR, "línea", f"el paquete es de una línea de {height:g} mm y «{cat.name}» mide {cat.height_mm:g} mm (tolerancia {LINE_HEIGHT_TOL_MM} mm): "
                            "las piezas solo se acoplan dentro de una línea de la misma altura", "instalalo como línea nueva (--como-linea)")
        return
    local = cat.params.to_dict()
    diff = sorted(k for k, v in (line_d.get("params") or {}).items() if k in local and local[k] != v and k != "ref_height")
    if diff:
        more = f" y {len(diff) - _PARAMS_SHOWN} más" if len(diff) > _PARAMS_SHOWN else ""
        add(WARNING, "perfil", f"el perfil del paquete difiere del de «{cat.name}» en: {', '.join(diff[:_PARAMS_SHOWN])}{more}; "
                               "las piezas son crudas y gana el perfil local")


def _piece_groups(names: set) -> dict:
    """`ref` → (ruta de la ficha, [archivos de esa pieza]) a partir de las fichas `piezas/<tipo>/<id>@<n>.pieza.json`."""
    out = {}
    for n in sorted(names):
        m = _PIECE_RE.match(n)
        if not m:
            continue
        prefix = f"piezas/{m.group('tipo')}/{m.group('id')}@{m.group('v')}."
        out[f"{m.group('id')}@{m.group('v')}"] = (n, sorted(x for x in names if x.startswith(prefix)))
    return out


def _plan_pieces(plan, add, zf, names: set, cat: Catalog | None) -> None:
    for ref, (ficha, files) in _piece_groups(names).items():
        try:
            meta = P.PieceMeta.from_dict(_read_json(zf, ficha))
        except (ValueError, KeyError, TypeError, UnicodeDecodeError, P.PieceError) as exc:
            add(ERROR, ficha, f"la ficha no se puede leer: {exc}", "actualizá afm o volvé a exportar la pieza")
            continue
        if meta.ref != ref or ficha.split("/")[1] != meta.tipo:
            add(ERROR, ficha, f"la ficha dice {meta.ref} de tipo «{meta.tipo}» y su ruta dice {ref}", "volvé a exportar la pieza")
            continue
        local = None
        if cat is not None:
            try:
                other = cat.meta(meta.id)
                if other.tipo != meta.tipo:
                    add(ERROR, ref, f"«{meta.id}» ya existe en el catálogo como «{other.tipo}» y el paquete lo trae como «{meta.tipo}»", "instalalo como línea nueva (--como-linea)")
                    plan.sugerir_linea = True
                    continue
                local = next((m for m in cat._metas() if m.id == meta.id and m.version == meta.version), None)
            except CatalogError:
                local = None
        if local is None:
            plan.items.append(Item("pieza", ref, "instalar", f"{meta.tipo}", files))
        elif local.sha256 == meta.sha256:
            note = "ya estaba (mismo STL)"
            if (local.color or {}).get("sha256") != (meta.color or {}).get("sha256"):
                note += "; la ficha local tiene otro color y se conserva"
                add(WARNING, ref, "ya está instalada con otro color o retoque; se conserva la local")
            plan.items.append(Item("pieza", ref, "omitir", note, files))
        else:
            plan.sugerir_linea = True
            add(ERROR, ref, f"ya existe en el catálogo con otra geometría (sha256 distinto): las piezas son inmutables y no se pisan",
                "instalalo como línea nueva (--como-linea)")


def _plan_recipes(plan, add, zf, names: set, cat: Catalog | None) -> None:
    for n in sorted(names):
        m = _RECIPE_RE.match(n)
        if not m:
            continue
        data = zf.read(n)
        dest = os.path.join(cat.root, n) if cat is not None else None
        if dest and os.path.isfile(dest):
            with open(dest, "rb") as f:
                same = f.read() == data
            if same:
                plan.items.append(Item("receta", m.group("name"), "omitir", "ya estaba (mismo contenido)", [n]))
            else:
                plan.sugerir_linea = True
                add(ERROR, n, f"ya hay una receta «{m.group('name')}» distinta en el catálogo", "instalalo como línea nueva (--como-linea)")
        else:
            plan.items.append(Item("receta", m.group("name"), "instalar", "", [n]))


def _roles_used(zf, items: list) -> list:
    used = []
    for it in items:
        if it.kind != "pieza":
            continue
        ficha = next(f for f in it.files if f.endswith(".pieza.json"))
        c = (_read_json(zf, ficha).get("color") or {})
        used += list(c.get("roles") or []) + list((c.get("retoque") or {}).get("roles") or [])
    return list(dict.fromkeys(used))


def _plan_palette(plan, add, zf, names: set, cat: Catalog | None, pkg: Q.Paquete) -> None:
    pkg_pal = None
    if "paleta.json" in names:
        try:
            pkg_pal = Palette.from_dict(_read_json(zf, "paleta.json"))
        except (ValueError, PaletteError, UnicodeDecodeError) as exc:
            add(ERROR, "paleta.json", f"no se puede leer: {exc}", "volvé a exportar la paleta")
            return
    try:
        local = cat.palette() if cat is not None else None
    except CatalogError as exc:
        add(ERROR, "paleta.json", f"la paleta del catálogo no se puede leer: {exc}", "arreglala antes de instalar")
        return
    have = {r.id: r for r in (local.roles if local else [])}
    if pkg_pal is not None:
        for r in pkg_pal.roles:
            if r.id not in have:
                plan.roles_nuevos.append(r)
            elif have[r.id].hex != r.hex:
                add(WARNING, f"paleta {r.id}", f"el rol ya existe con otro color ({have[r.id].hex} y el paquete trae {r.hex}); gana el local")
    final = set(have) | {r.id for r in plan.roles_nuevos}
    missing = [r for r in _roles_used(zf, plan.a_instalar) if r not in final]
    if missing:
        add(ERROR, "paleta", f"las piezas usan roles de color que no existen: {', '.join(missing)}", "el paquete tiene que traer su paleta.json con esos roles")


def _plan_registry(plan, add, reg: dict, pkg: Q.Paquete) -> None:
    inst = reg.get("paquetes", {})
    prev = inst.get(pkg.id)
    plan.registro_previo = prev
    if prev:
        try:
            if Q.parse_semver(pkg.version) < Q.parse_semver(prev.get("version", "0.0.0")):
                add(WARNING, pkg.id, f"ya está instalada la versión {prev.get('version')} y esta es la {pkg.version} (más vieja); la instalación es aditiva")
        except Q.PaqueteError:
            pass
        if prev.get("github", "") != pkg.github:
            plan.cambia_origen = True
            add(WARNING, pkg.id, f"«{pkg.id}» estaba instalado desde «{prev.get('github') or 'un zip local'}» y este paquete dice «{pkg.github or 'ningún repo'}»",
                "confirmá el cambio de origen explícitamente")
    for text in pkg.conflicts:
        try:
            dep = Q.parse_dependency(text)
        except Q.PaqueteError:
            continue
        got = inst.get(dep.id)
        if got and dep.satisfied_by(got.get("version", "0.0.0")):
            add(ERROR, pkg.id, f"choca con «{dep.id}» {got.get('version')}, que ya está instalado (conflicts: {text})", "quitá el otro paquete primero")
    for other_id, entry in inst.items():
        for text in entry.get("conflicts", []):
            try:
                dep = Q.parse_dependency(text)
            except Q.PaqueteError:
                continue
            if dep.id == pkg.id and dep.satisfied_by(pkg.version):
                add(ERROR, pkg.id, f"«{other_id}» ya instalado declara que no convive con «{pkg.id}» {pkg.version}", "quitá el otro paquete primero")
    for text in pkg.requiere:
        try:
            dep = Q.parse_dependency(text)
        except Q.PaqueteError:
            continue
        got = inst.get(dep.id)
        if not got or not dep.satisfied_by(got.get("version", "0.0.0")):
            add(WARNING, pkg.id, f"requiere «{text}» y no está instalado",
                "instalalo antes: el paquete puede quedar incompleto")


# ------------------------------------------------------------------------------------------------------------------------ aplicar

class _Journal:
    """Diario de lo escrito en el catálogo: si algo falla, `rollback` deja el árbol como estaba (borra lo movido, restaura lo reescrito,
    quita las carpetas que se crearon)."""

    def __init__(self, root: str):
        self.root = root
        self.moved: list[str] = []
        self.saved: dict[str, bytes | None] = {}
        self.dirs: list[str] = []

    def _mkdirs(self, directory: str) -> None:
        missing = []
        d = os.path.abspath(directory)
        while not os.path.isdir(d) and d != self.root:
            missing.append(d)
            d = os.path.dirname(d)
        for d in reversed(missing):
            os.mkdir(d)
            self.dirs.append(d)

    def move(self, src: str, dest: str) -> None:
        if os.path.exists(dest):
            raise InstallError(f"«{os.path.relpath(dest, self.root)}» apareció mientras se instalaba; no se pisa")
        self._mkdirs(os.path.dirname(dest))
        os.replace(src, dest)
        self.moved.append(dest)

    def snapshot(self, path: str) -> None:
        if path not in self.saved:
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    self.saved[path] = f.read()
            else:
                self.saved[path] = None

    def write(self, path: str, data: bytes) -> None:
        self.snapshot(path)
        self._mkdirs(os.path.dirname(path))
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)

    def rollback(self) -> None:
        for path in reversed(self.moved):
            if os.path.isfile(path):
                os.remove(path)
        for path, data in self.saved.items():
            for stale in (path + ".tmp",):
                if os.path.isfile(stale):
                    os.remove(stale)
            if data is None:
                if os.path.isfile(path):
                    os.remove(path)
            else:
                with open(path, "wb") as f:
                    f.write(data)
        for d in reversed(self.dirs):
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)


def _dump(obj) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def apply(plan: InstallPlan, *, confirmar_origen: bool = False, now: datetime | None = None) -> InstallResult:
    """Instala lo que dice `plan` (ver el docstring del módulo). Ante **cualquier** fallo el catálogo queda idéntico al de antes
    (si el destino era una línea nueva, la carpeta que se creó se borra). `confirmar_origen` es obligatorio cuando el id estaba instalado
    desde otro repo (`plan.cambia_origen`)."""
    if not plan.ok:
        raise InstallError("el plan tiene errores: " + "; ".join(str(i) for i in plan.errors[:3]))
    if plan.cambia_origen and not confirmar_origen:
        raise InstallError("el paquete cambió de origen respecto de lo instalado; hace falta confirmarlo explícitamente")
    pkg = plan.paquete
    if P.sha256_file(plan.zip_path) != plan.zip_sha256:
        raise InstallError("el zip cambió desde que se armó el plan; volvé a planificar")
    root = plan.root
    created_root = not os.path.exists(root)
    staging = None
    journal = _Journal(root)
    when = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    try:
        os.makedirs(root, exist_ok=True)
        with Z.open_zip(plan.zip_path) as zf:
            insp = Z.inspect(zf, allow_fbx=plan.allow_fbx)
            if insp.findings:
                raise InstallError("el zip ya no pasa la inspección: " + str(insp.findings[0]))
            line_bytes = zf.read("linea.json")
            pal_bytes = zf.read("paleta.json") if "paleta.json" in {e.name for e in insp.entries} else None
            wanted = {f for it in plan.a_instalar for f in it.files}
            staging = tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=root)
            Z.extract_all(zf, staging, insp, only=wanted)
        for name in sorted(wanted):                                  # lo extraído es lo que declaró el manifiesto
            want = pkg.archivos.get(name)
            if want and P.sha256_file(os.path.join(staging, *name.split("/"))) != want:
                raise InstallError(f"«{name}» no coincide con el sha256 de paquete.json")
        avisos = [str(i) for i in plan.warnings]
        cat_dest = None if plan.como_linea else Catalog.open(root)
        merged = _merged_palette(cat_dest, plan)
        _verify_staging(staging, line_bytes if plan.como_linea else _line_bytes(root), merged, avisos)
        for it in plan.a_instalar:
            if it.kind == "receta":
                from .recipe import Recipe, RecipeError

                try:
                    Recipe.load(os.path.join(staging, *it.files[0].split("/")))
                except (RecipeError, ValueError, OSError, KeyError) as exc:
                    raise InstallError(f"la receta «{it.name}» no se puede leer: {exc}") from None

        # ---- movimiento (diario ⇒ reversible)
        if plan.como_linea:
            journal.write(os.path.join(root, "linea.json"), line_bytes)
        for name in sorted(wanted):
            journal.move(os.path.join(staging, *name.split("/")), os.path.join(root, *name.split("/")))
        cat = Catalog.open(root)
        if plan.roles_nuevos:
            journal.snapshot(cat.palette_path)
            cat.set_palette(merged)
        journal.snapshot(os.path.join(root, "CATALOGO.md"))
        cat.write_index()
        refs = {"piezas": [i.name for i in plan.a_instalar if i.kind == "pieza"], "recetas": [i.name for i in plan.a_instalar if i.kind == "receta"],
                "roles": [r.id for r in plan.roles_nuevos]}
        reg = load_registry(root)
        prev = reg["paquetes"].get(pkg.id) or {}
        merged_refs = {k: sorted(set((prev.get("refs") or {}).get(k, [])) | set(v)) for k, v in refs.items()}
        reg["paquetes"][pkg.id] = {
            "version": pkg.version, "nombre": pkg.nombre, "github": pkg.github, "indice": prev.get("indice", ""), "sha256": plan.zip_sha256,
            "fecha": when, "licencia": pkg.licencia, "autores": [a.to_dict() for a in pkg.autores], "origen": pkg.origen,
            "conflicts": list(pkg.conflicts), "refs": merged_refs,
        }
        journal.write(os.path.join(root, REGISTRY_FILE), _dump(reg))            # al final: es lo que da la instalación por hecha
    except BaseException:
        journal.rollback()
        if staging:
            shutil.rmtree(staging, ignore_errors=True)
        if created_root:
            shutil.rmtree(root, ignore_errors=True)
        raise
    shutil.rmtree(staging, ignore_errors=True)
    return InstallResult(paquete=pkg, root=root, piezas=refs["piezas"], recetas=refs["recetas"], roles=refs["roles"],
                         omitidas=[i.name for i in plan.omitidas], avisos=avisos)


def _line_bytes(root: str) -> bytes:
    with open(os.path.join(root, "linea.json"), "rb") as f:
        return f.read()


def _merged_palette(cat: Catalog | None, plan: InstallPlan) -> Palette | None:
    local = cat.palette() if cat is not None else None
    if not plan.roles_nuevos:
        return local
    return Palette(list(local.roles if local else []) + list(plan.roles_nuevos))


def _verify_staging(staging: str, line_bytes: bytes, palette: Palette | None, avisos: list) -> None:
    """Arma en el staging un catálogo de juguete (la línea de destino, la paleta ya fusionada y las piezas nuevas) y corre `Catalog.verify`."""
    with open(os.path.join(staging, "linea.json"), "wb") as f:
        f.write(line_bytes)
    if palette is not None:
        with open(os.path.join(staging, "paleta.json"), "w", encoding="utf-8") as f:
            json.dump(palette.to_dict(), f, indent=2, ensure_ascii=False)
    for sub in ("piezas", "figuras"):
        os.makedirs(os.path.join(staging, sub), exist_ok=True)
    try:
        rep = Catalog.open(staging).verify()
    except (CatalogError, OSError, ValueError) as exc:
        raise InstallError(f"lo extraído no abre como catálogo: {exc}") from None
    if rep.problems:
        raise InstallError("lo que trae el paquete no pasa `verificar`: " + "; ".join(rep.problems[:3]) + (f" (+{len(rep.problems) - 3})" if len(rep.problems) > 3 else ""))
    avisos += [w for w in rep.warnings if "versiones no consecutivas" not in w]
    for n in ("linea.json", "paleta.json"):                           # no son parte de lo que se mueve al catálogo
        p = os.path.join(staging, n)
        if os.path.isfile(p):
            os.remove(p)


__all__ = ["InstallError", "Issue", "Item", "InstallPlan", "InstallResult", "plan_install", "apply", "peek_package", "explicit_root",
           "load_registry", "tree_digest", "REGISTRY_FILE", "REGISTRY_FORMAT"]
