"""Manifiesto de paquete `afm-paquete-1` (plan de catálogos en línea, §5.2 y D02).

Un **paquete** es un `.zip` con el árbol del catálogo más un `paquete.json` que dice quién lo hizo, con qué licencia, para qué línea y
qué trae. Este módulo solo define el formato: carga, validación de campos (SPDX, semver, rangos de versión), serialización y el
esquema JSON. No toca el catálogo ni la red (eso es `instalar.py` / `remoto.py`) y no importa `parts`, `catalog` ni `manifold3d`: se
carga rápido y sirve igual en el bot del índice. Las expresiones regulares de id y de referencia se repiten acá a propósito y una
prueba las compara con las de `parts` y `catalog`.

Reglas del formato (plan §5.1 punto 8):

* **Lectura tolerante.** Una clave desconocida no invalida el manifiesto: queda en `Paquete.avisos`. Solo un `formato` de una versión
  mayor se rechaza (`PaqueteFormatoFuturo`) y uno que no es de paquete es `PaqueteError`.
* **Evolución aditiva.** Solo se agregan claves; ninguna cambia de significado.
* `from_dict` falla solo ante lo que impide leer el manifiesto (tipos equivocados, claves obligatorias ausentes); lo demás
  (licencia que no es SPDX, semver inválido, categorías fuera de lista…) lo reporta `issues()` / `problems()`, con el código `CK…`
  de la tabla del §6 cuando existe.
"""
from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass, field

FORMAT = "afm-paquete-1"
MANIFEST_FILE = "paquete.json"
SCHEMA_FILE = "afm-paquete-1.schema.json"

TYPES = ("linea", "piezas", "heroe", "coleccion")
ORIGINS = ("propia", "derivada", "dominio-publico", "con-permiso", "referencia")
# Lista cerrada de categorías para filtrar en «Explorar» (D-14: propuesta [P], a confirmar con el cliente).
CATEGORIES = ("heroes", "cuerpos", "cabezas", "manos", "armas-accesorios", "ropa", "paletas", "lineas", "colecciones", "otros")
CATEGORIES_MIN = 1
CATEGORIES_MAX = 4

ALL_RIGHTS_RESERVED = "todos-los-derechos-reservados"      # con permiso de distribución (§5.8)
# Licencias aceptadas (SPDX). Lista cerrada a propósito: el lint la usa (CK101) y agregar una es agregar una línea.
LICENSES = (
    "CC0-1.0",
    "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-ND-4.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-NC-ND-4.0",
    "CC-BY-3.0", "CC-BY-SA-3.0", "CC-BY-NC-3.0", "CC-BY-NC-SA-3.0",
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "Unlicense",
    "GPL-3.0-only", "GPL-3.0-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later",
    ALL_RIGHTS_RESERVED,
)

NAME_MAX = 80
DESCRIPTION_MAX = 280          # [P] descripción corta; el texto largo va en README.md / descripcion.md
TAGS_MAX = 16                  # [P]
TAG_LEN_MAX = 32               # [P]
ID_MAX = 64                    # [P] la carpeta del índice es `<Autor>@<id>`

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")                              # igual que `parts.ID_RE`
_REF_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*@[1-9]\d*$")                    # `id@n` (la versión de una pieza empieza en 1)
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")                     # `owner/repo`
_USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")                   # usuario de GitHub
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$")
_PARTIAL_RE = re.compile(r"^(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?(?:\.(0|[1-9]\d*))?$")
_FORMAT_RE = re.compile(r"^afm-paquete-(\d+)$")
_REL_PATH_BAD = re.compile(r"(^/|^[A-Za-z]:|\\|(^|/)\.\.?(/|$)|[\x00-\x1f])")


class PaqueteError(ValueError):
    """El manifiesto no se puede leer (no es JSON, no es un paquete, falta una clave obligatoria, tipo equivocado)."""


class PaqueteFormatoFuturo(PaqueteError):
    """El manifiesto es de una versión mayor del formato: hay que actualizar `afm`."""


# ---------------------------------------------------------------------------------------------------------------- semver


@functools.total_ordering
@dataclass(frozen=True)
class Semver:
    """Versión semántica `X.Y.Z[-pre][+build]`. Se ordena según la precedencia de semver.org (el `+build` no cuenta)."""

    major: int
    minor: int
    patch: int
    pre: tuple = ()

    @property
    def core(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def _key(self):
        # sin `pre` (versión final) va DESPUÉS de cualquier prerelease del mismo núcleo; numérico < alfanumérico
        pre = tuple((0, x, "") if isinstance(x, int) else (1, 0, x) for x in self.pre)
        return (self.core, 1 if not self.pre else 0, pre)

    def __lt__(self, other):
        if not isinstance(other, Semver):
            return NotImplemented
        return self._key() < other._key()

    def __str__(self):
        s = f"{self.major}.{self.minor}.{self.patch}"
        return s + ("-" + ".".join(str(x) for x in self.pre) if self.pre else "")


def is_semver(value) -> bool:
    return isinstance(value, str) and _SEMVER_RE.match(value) is not None


def parse_semver(value: str) -> Semver:
    """`1.2.3`, `1.2.3-rc.1` o `1.2.3+meta` → `Semver`. Lo demás (`1.2`, `v1.2.3`, `01.2.3`) es `PaqueteError`."""
    m = _SEMVER_RE.match(value) if isinstance(value, str) else None
    if not m:
        raise PaqueteError(f"«{value}» no es una versión semver (X.Y.Z, p. ej. 1.2.0)")
    pre: tuple = ()
    if m.group(4):
        pre = tuple(int(x) if x.isdigit() else x for x in m.group(4).split("."))
    return Semver(int(m.group(1)), int(m.group(2)), int(m.group(3)), pre)


# ---------------------------------------------------------------------------------------------------------------- rangos

_OPS = (">=", "<=", ">", "<", "=", "^", "~")


@dataclass(frozen=True)
class Range:
    """Rango de versiones simple: uno o más comparadores separados por coma o espacio, todos tienen que cumplirse.

    Comparadores: `>=` `>` `<=` `<` `=` (o versión sola), `^` (compatible: no sube el primer número distinto de cero) y `~` (no
    sube el menor); `*` acepta todo. Las versiones pueden estar incompletas (`^1`, `>=0.2`, `~1.4`): se completan con ceros.
    Un prerelease se ordena según semver y no tiene regla especial. Ejemplos: `>=0.2.1`, `^1.0.0`, `>=1.0.0,<2.0.0`.
    """

    text: str
    comparators: tuple = ()      # ((operador, Semver), …); vacío = `*`

    def satisfies(self, version) -> bool:
        v = parse_semver(version) if isinstance(version, str) else version
        for op, ref in self.comparators:
            if op == ">=" and not v >= ref:
                return False
            if op == ">" and not v > ref:
                return False
            if op == "<=" and not v <= ref:
                return False
            if op == "<" and not v < ref:
                return False
            if op == "=" and not v == ref:
                return False
        return True

    def __str__(self):
        return self.text


def _partial(text: str) -> tuple[Semver, int]:
    """`1`, `1.2` o `1.2.3` (o semver completo con prerelease) → (versión completada con ceros, cantidad de números dados)."""
    if _SEMVER_RE.match(text):
        return parse_semver(text), 3
    m = _PARTIAL_RE.match(text)
    if not m:
        raise PaqueteError(f"«{text}» no es una versión (se espera X, X.Y o X.Y.Z)")
    nums = [g for g in m.groups() if g is not None]
    padded = [int(g) for g in nums] + [0] * (3 - len(nums))
    return Semver(*padded), len(nums)


def _bump_caret(v: Semver) -> Semver:
    if v.major > 0:
        return Semver(v.major + 1, 0, 0)
    if v.minor > 0:
        return Semver(0, v.minor + 1, 0)
    return Semver(0, 0, v.patch + 1)


def parse_range(text: str) -> Range:
    """`>=0.2.1`, `^1.0.0`, `~1.4`, `>=1.0.0,<2.0.0`, `1.2.3`, `*` → `Range`. Otra cosa es `PaqueteError`."""
    if not isinstance(text, str) or not text.strip():
        raise PaqueteError("el rango de versiones está vacío")
    raw = text.strip()
    if raw == "*":
        return Range(raw, ())
    out: list[tuple[str, Semver]] = []
    for tok in [t for t in re.split(r"[,\s]+", re.sub(r"(>=|<=|>|<|=|\^|~)\s+", r"\1", raw)) if t]:   # «>= 1.0.0» = «>=1.0.0»
        op = next((o for o in _OPS if tok.startswith(o)), "")
        body = tok[len(op):]
        if not body:
            raise PaqueteError(f"rango «{raw}»: falta la versión después de «{op}»")
        try:
            v, given = _partial(body)
        except PaqueteError as e:
            raise PaqueteError(f"rango «{raw}»: {e}") from None
        if op in ("", "="):
            if given == 3:
                out.append(("=", v))
            else:                                          # `1.2` = cualquier 1.2.x
                out += [(">=", v), ("<", Semver(v.major, v.minor + 1, 0) if given == 2 else Semver(v.major + 1, 0, 0))]
        elif op == "^":
            out += [(">=", v), ("<", _bump_caret(v) if given == 3 else (Semver(v.major + 1, 0, 0) if given == 1 or v.major > 0 else Semver(0, v.minor + 1, 0)))]
        elif op == "~":
            out += [(">=", v), ("<", Semver(v.major, v.minor + 1, 0) if given >= 2 else Semver(v.major + 1, 0, 0))]
        else:
            out.append((op, v))
    return Range(raw, tuple(out))


def is_range(value) -> bool:
    try:
        parse_range(value)
    except PaqueteError:
        return False
    return True


# ---------------------------------------------------------------------------------------------------------------- dependencias


@dataclass(frozen=True)
class Dependency:
    """Entrada de `requiere` o `conflicts`: `id`, `id@rango` o `id@rango#owner/repo` (la pista de repo ubica el paquete)."""

    id: str
    range: str = "*"
    github: str = ""

    def __str__(self):
        s = self.id + ("" if self.range == "*" else "@" + self.range)
        return s + (f"#{self.github}" if self.github else "")

    def satisfied_by(self, version: str) -> bool:
        return parse_range(self.range).satisfies(version)


def parse_dependency(text: str) -> Dependency:
    """`proceres-127-base@^1.0.0#owner/repo` → `Dependency`. Valida el id, el rango y el repo."""
    if not isinstance(text, str) or not text.strip():
        raise PaqueteError("dependencia vacía")
    body, _, repo = text.strip().partition("#")
    pid, _, rng = body.partition("@")
    if not _ID_RE.match(pid) or len(pid) > ID_MAX:
        raise PaqueteError(f"dependencia «{text}»: id inválido «{pid}» (minúsculas, números y guiones)")
    if "@" in body and not rng:
        raise PaqueteError(f"dependencia «{text}»: falta el rango después de «@»")
    if rng:
        parse_range(rng)
    if "#" in text and not _REPO_RE.match(repo):
        raise PaqueteError(f"dependencia «{text}»: la pista de repo «{repo}» no es «owner/repo»")
    return Dependency(pid, rng.strip() if rng else "*", repo)


# ---------------------------------------------------------------------------------------------------------------- piezas del manifiesto


@dataclass
class Author:
    nombre: str
    github: str = ""

    def to_dict(self) -> dict:
        d = {"nombre": self.nombre}
        if self.github:
            d["github"] = self.github
        return d


@dataclass
class RequiredReference:
    """Archivo que NO viaja en el paquete: la persona lo aporta y la app verifica su `sha256` (§5.11, D26)."""

    id: str
    nombre: str
    sha256: list = field(default_factory=list)       # hashes aceptados (hex minúscula, 64)
    tamano_max: int = 0                              # bytes; tope del archivo que se acepta

    def to_dict(self) -> dict:
        return {"id": self.id, "nombre": self.nombre, "sha256": list(self.sha256), "tamano_max": int(self.tamano_max)}


@dataclass
class Pin:
    """Paquete fijado a versión y `sha256` exactos dentro de una colección (§5.12)."""

    id: str
    github: str
    version: str
    sha256: str

    def to_dict(self) -> dict:
        return {"id": self.id, "github": self.github, "version": self.version, "sha256": self.sha256}


@dataclass
class Contents:
    piezas: list = field(default_factory=list)       # `id@n`
    recetas: list = field(default_factory=list)      # ids de personaje (`figuras/<id>.receta.json`)
    paleta: bool = False
    pines: list = field(default_factory=list)        # `Pin`; solo en `tipo: coleccion`

    def to_dict(self) -> dict:
        d: dict = {"piezas": list(self.piezas), "recetas": list(self.recetas), "paleta": bool(self.paleta)}
        if self.pines:                                # solo colecciones: el resto queda idéntico
            d["pines"] = [p.to_dict() for p in self.pines]
        return d


@dataclass(frozen=True)
class Issue:
    """Un problema del manifiesto. `code` es el `CK…` del §6 (vacío si el plan no le asignó uno), `field` el campo afectado."""

    code: str
    field: str
    message: str

    def __str__(self):
        return f"{self.code + ' ' if self.code else ''}{self.field}: {self.message}"


# ---------------------------------------------------------------------------------------------------------------- Paquete

_KNOWN_KEYS = ("formato", "id", "nombre", "version", "descripcion", "tipo", "autores", "licencia", "origen", "github", "linea",
               "motor", "contenido", "requiere", "conflicts", "experimental", "categorias", "etiquetas", "archivos",
               "referencias_requeridas")


def _need(d: dict, key: str, kind, what: str):
    if key not in d:
        raise PaqueteError(f"falta «{key}» en el manifiesto")
    v = d[key]
    if not isinstance(v, kind) or (kind is not bool and isinstance(v, bool) and kind in (int, float)):
        raise PaqueteError(f"«{key}» tiene que ser {what}")
    return v


def _opt(d: dict, key: str, kind, what: str, default):
    if key not in d or d[key] is None:
        return default
    v = d[key]
    if not isinstance(v, kind) or (isinstance(v, bool) and kind in (int, float)):
        raise PaqueteError(f"«{key}» tiene que ser {what}")
    return v


def _str_list(d: dict, key: str) -> list:
    v = _opt(d, key, list, "una lista", [])
    if not all(isinstance(x, str) for x in v):
        raise PaqueteError(f"«{key}» tiene que ser una lista de textos")
    return list(v)


@dataclass
class Paquete:
    """Manifiesto `afm-paquete-1`. `Paquete.from_dict(p.to_dict()) == p`; `avisos` (claves desconocidas) no entra en la igualdad."""

    id: str
    nombre: str
    version: str
    descripcion: str
    tipo: str
    autores: list
    licencia: str
    origen: str
    github: str = ""
    linea: dict | None = None                         # {"nombre": str, "altura_mm": float}; opcional solo en colecciones
    motor: str = ""                                   # rango de versiones del motor (`>=0.2.1`); vacío = sin requisito
    contenido: Contents = field(default_factory=Contents)
    requiere: list = field(default_factory=list)      # textos `id[@rango][#owner/repo]`
    conflicts: list = field(default_factory=list)
    experimental: bool = False
    categorias: list = field(default_factory=list)
    etiquetas: list = field(default_factory=list)
    archivos: dict = field(default_factory=dict)      # ruta relativa -> sha256
    referencias_requeridas: list = field(default_factory=list)
    avisos: list = field(default_factory=list, compare=False, repr=False)

    # ---- serialización

    def to_dict(self) -> dict:
        d: dict = {"formato": FORMAT, "id": self.id, "nombre": self.nombre, "version": self.version,
                   "descripcion": self.descripcion, "tipo": self.tipo,
                   "autores": [a.to_dict() for a in self.autores], "licencia": self.licencia, "origen": self.origen}
        if self.github:
            d["github"] = self.github
        if self.linea is not None:
            d["linea"] = dict(self.linea)
        if self.motor:
            d["motor"] = self.motor
        d["contenido"] = self.contenido.to_dict()
        d["requiere"] = list(self.requiere)
        d["conflicts"] = list(self.conflicts)
        d["experimental"] = bool(self.experimental)
        d["categorias"] = list(self.categorias)
        d["etiquetas"] = list(self.etiquetas)
        d["archivos"] = dict(self.archivos)
        if self.referencias_requeridas:               # solo si hay: los paquetes sin referencias quedan idénticos
            d["referencias_requeridas"] = [r.to_dict() for r in self.referencias_requeridas]
        return d

    def to_json(self) -> str:
        """Texto estable (claves en orden fijo, UTF-8 sin escapar, salto final): el mismo manifiesto da el mismo archivo."""
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, d) -> "Paquete":
        if not isinstance(d, dict):
            raise PaqueteError("el manifiesto tiene que ser un objeto JSON")
        fmt = d.get("formato")
        m = _FORMAT_RE.match(fmt) if isinstance(fmt, str) else None
        if not m or int(m.group(1)) < 1:
            raise PaqueteError(f"no es un manifiesto de paquete (formato {fmt!r}, se esperaba {FORMAT!r})")
        if int(m.group(1)) > 1:
            raise PaqueteFormatoFuturo(f"este paquete usa el formato {fmt!r} y esta versión de afm entiende {FORMAT!r}: "
                                       "actualizá afm para usarlo")
        authors = []
        for a in _need(d, "autores", list, "una lista"):
            if not isinstance(a, dict) or not isinstance(a.get("nombre"), str):
                raise PaqueteError("cada autor tiene que ser un objeto con «nombre»")
            gh = a.get("github", "")
            if gh is None:
                gh = ""
            if not isinstance(gh, str):
                raise PaqueteError("«github» de un autor tiene que ser un texto")
            authors.append(Author(a["nombre"], gh))
        linea = _opt(d, "linea", dict, "un objeto {nombre, altura_mm}", None)
        if linea is not None:
            if not isinstance(linea.get("nombre"), str) or isinstance(linea.get("altura_mm"), bool) \
                    or not isinstance(linea.get("altura_mm"), (int, float)):
                raise PaqueteError("«linea» tiene que traer «nombre» (texto) y «altura_mm» (número)")
            linea = {"nombre": linea["nombre"], "altura_mm": float(linea["altura_mm"])}
        c = _opt(d, "contenido", dict, "un objeto", {})
        pins = []
        for p in _opt(c, "pines", list, "una lista", []):
            if not isinstance(p, dict) or not all(isinstance(p.get(k), str) for k in ("id", "github", "version", "sha256")):
                raise PaqueteError("cada pin de «contenido.pines» tiene que traer id, github, version y sha256 (textos)")
            pins.append(Pin(p["id"], p["github"], p["version"], p["sha256"]))
        contents = Contents(piezas=_str_list(c, "piezas"), recetas=_str_list(c, "recetas"),
                            paleta=bool(_opt(c, "paleta", bool, "verdadero o falso", False)), pines=pins)
        refs = []
        for r in _opt(d, "referencias_requeridas", list, "una lista", []):
            sha = r.get("sha256", []) if isinstance(r, dict) else None
            size = r.get("tamano_max", 0) if isinstance(r, dict) else None
            if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not isinstance(r.get("nombre"), str) \
                    or not isinstance(sha, list) or not all(isinstance(x, str) for x in sha) \
                    or isinstance(size, bool) or not isinstance(size, int):
                raise PaqueteError("cada «referencias_requeridas» tiene que traer id, nombre (textos), sha256 (lista) y tamano_max (entero)")
            refs.append(RequiredReference(r["id"], r["nombre"], list(sha), size))
        files = _opt(d, "archivos", dict, "un objeto {ruta: sha256}", {})
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
            raise PaqueteError("«archivos» tiene que ser {ruta: sha256} con textos")
        return cls(id=_need(d, "id", str, "un texto"), nombre=_need(d, "nombre", str, "un texto"),
                   version=_need(d, "version", str, "un texto"), descripcion=_need(d, "descripcion", str, "un texto"),
                   tipo=_need(d, "tipo", str, "un texto"), autores=authors, licencia=_need(d, "licencia", str, "un texto"),
                   origen=_need(d, "origen", str, "un texto"), github=_opt(d, "github", str, "un texto", ""), linea=linea,
                   motor=_opt(d, "motor", str, "un texto", ""), contenido=contents,
                   requiere=_str_list(d, "requiere"), conflicts=_str_list(d, "conflicts"),
                   experimental=_opt(d, "experimental", bool, "verdadero o falso", False),
                   categorias=_str_list(d, "categorias"), etiquetas=_str_list(d, "etiquetas"), archivos=dict(files),
                   referencias_requeridas=refs,
                   avisos=[f"clave desconocida «{k}»: se ignora" for k in d if k not in _KNOWN_KEYS])

    @classmethod
    def from_json(cls, text: str) -> "Paquete":
        try:
            data = json.loads(text)
        except (ValueError, TypeError) as e:
            raise PaqueteError(f"el manifiesto no es JSON válido: {e}") from None
        return cls.from_dict(data)

    @classmethod
    def load(cls, path: str) -> "Paquete":
        """Lee un `paquete.json` del disco."""
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            raise PaqueteError(f"no se pudo leer «{path}»: {e}") from None
        return cls.from_json(text)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.to_json())

    # ---- validación

    def issues(self) -> list[Issue]:
        """Problemas de contenido del manifiesto (no abre ningún archivo del paquete)."""
        out: list[Issue] = []

        def add(code: str, fld: str, msg: str):
            out.append(Issue(code, fld, msg))

        if not _ID_RE.match(self.id or "") or len(self.id) > ID_MAX:
            add("", "id", f"inválido «{self.id}»: minúsculas, números y guiones, hasta {ID_MAX} caracteres (p. ej. proceres-127-guemes)")
        name = " ".join((self.nombre or "").split())
        if not name or len(name) > NAME_MAX or any(ord(ch) < 32 for ch in self.nombre):
            add("", "nombre", f"tiene que tener de 1 a {NAME_MAX} caracteres, sin caracteres de control")
        if not is_semver(self.version):
            add("CK103", "version", f"«{self.version}» no es semver (X.Y.Z, p. ej. 1.2.0)")
        desc = (self.descripcion or "").strip()
        if not desc:
            add("CK102", "descripcion", "falta la descripción (una o dos frases)")
        elif len(desc) > DESCRIPTION_MAX:
            add("CK102", "descripcion", f"tiene {len(desc)} caracteres y el máximo es {DESCRIPTION_MAX}: el texto largo va en README.md")
        if self.tipo not in TYPES:
            add("", "tipo", f"desconocido «{self.tipo}» (hay {', '.join(TYPES)})")
        if not self.autores:
            add("", "autores", "falta al menos un autor")
        for i, a in enumerate(self.autores):
            if not (a.nombre or "").strip():
                add("", f"autores[{i}]", "el nombre está vacío")
            if a.github and not _USER_RE.match(a.github):
                add("", f"autores[{i}].github", f"«{a.github}» no es un usuario de GitHub")
        if not self.licencia:
            add("CK101", "licencia", "falta la licencia (SPDX, p. ej. CC-BY-4.0)")
        elif self.licencia not in LICENSES:
            add("CK101", "licencia", f"«{self.licencia}» no es una licencia aceptada (hay {', '.join(LICENSES)})")
        if not self.origen:
            add("CK102", "origen", f"falta el origen (hay {', '.join(ORIGINS)})")
        elif self.origen not in ORIGINS:
            add("CK102", "origen", f"desconocido «{self.origen}» (hay {', '.join(ORIGINS)})")
        if self.github and not _REPO_RE.match(self.github):
            add("", "github", f"«{self.github}» no es «owner/repo»")
        if self.linea is None:
            if self.tipo != "coleccion":
                add("CK104", "linea", "falta la línea con la que se armó y validó el paquete")
        else:
            if not (self.linea["nombre"] or "").strip():
                add("CK104", "linea.nombre", "el nombre de la línea está vacío")
            if not self.linea["altura_mm"] > 0:
                add("CK104", "linea.altura_mm", f"tiene que ser mayor que 0 (vale {self.linea['altura_mm']})")
        if self.motor:
            try:
                parse_range(self.motor)
            except PaqueteError as e:
                add("", "motor", str(e))
        for fld, items in (("requiere", self.requiere), ("conflicts", self.conflicts)):
            for i, text in enumerate(items):
                try:
                    dep = parse_dependency(text)
                except PaqueteError as e:
                    add("", f"{fld}[{i}]", str(e))
                    continue
                if dep.id == self.id:
                    add("", f"{fld}[{i}]", "un paquete no puede depender de sí mismo ni entrar en conflicto consigo")
        ids = [i for i in (_safe_dep_id(t) for t in self.requiere) if i]
        for t in self.conflicts:
            if _safe_dep_id(t) and _safe_dep_id(t) in ids:
                add("", "conflicts", f"«{_safe_dep_id(t)}» está en «requiere» y en «conflicts»")
        if len(set(ids)) != len(ids):
            add("", "requiere", "un mismo id aparece más de una vez (una versión por id)")
        if not (CATEGORIES_MIN <= len(self.categorias) <= CATEGORIES_MAX):
            add("CK106", "categorias", f"tiene que haber de {CATEGORIES_MIN} a {CATEGORIES_MAX} categorías (hay {len(self.categorias)})")
        for cat in self.categorias:
            if cat not in CATEGORIES:
                add("CK106", "categorias", f"«{cat}» no está en la lista (hay {', '.join(CATEGORIES)})")
        if len(set(self.categorias)) != len(self.categorias):
            add("CK106", "categorias", "hay categorías repetidas")
        if len(self.etiquetas) > TAGS_MAX:
            add("", "etiquetas", f"hay {len(self.etiquetas)} y el máximo es {TAGS_MAX}")
        for t in self.etiquetas:
            if not t.strip() or len(t) > TAG_LEN_MAX or any(ord(ch) < 32 for ch in t):
                add("", "etiquetas", f"«{t}» tiene que tener de 1 a {TAG_LEN_MAX} caracteres, sin caracteres de control")
        _check_contents(self, add)
        for path, sha in self.archivos.items():
            if not path or _REL_PATH_BAD.search(path) or path.endswith("/"):
                add("CK302", "archivos", f"ruta no permitida «{path}» (relativa, con «/», sin «..» ni caracteres de control)")
            if not _SHA_RE.match(sha):
                add("CK201", f"archivos[{path}]", "el sha256 tiene que ser hexadecimal en minúsculas de 64 caracteres")
        if self.origen == "referencia" and not self.referencias_requeridas:
            add("CK107", "referencias_requeridas", "el origen es «referencia» pero no declara ningún archivo requerido")
        if self.origen != "referencia" and self.referencias_requeridas:
            add("CK107", "origen", "declara «referencias_requeridas» pero el origen no es «referencia»")
        seen: set[str] = set()
        for i, r in enumerate(self.referencias_requeridas):
            f = f"referencias_requeridas[{i}]"
            if not _ID_RE.match(r.id or ""):
                add("", f + ".id", f"inválido «{r.id}»")
            elif r.id in seen:
                add("", f + ".id", f"«{r.id}» está repetido")
            seen.add(r.id)
            if not (r.nombre or "").strip():
                add("", f + ".nombre", "está vacío")
            if not r.sha256 or not all(_SHA_RE.match(s) for s in r.sha256):
                add("", f + ".sha256", "tiene que traer al menos un sha256 hexadecimal en minúsculas de 64 caracteres")
            if r.tamano_max <= 0:
                add("", f + ".tamano_max", "tiene que ser un tamaño en bytes mayor que 0")
        return out

    def problems(self) -> list[str]:
        """`issues()` como textos (`CK101 licencia: …`)."""
        return [str(i) for i in self.issues()]


def _safe_dep_id(text: str) -> str:
    try:
        return parse_dependency(text).id
    except PaqueteError:
        return ""


def _check_contents(p: Paquete, add) -> None:
    """Revisa `contenido` y su coherencia con `tipo`."""
    c = p.contenido
    for i, ref in enumerate(c.piezas):
        if not _REF_RE.match(ref):
            add("", f"contenido.piezas[{i}]", f"«{ref}» no es una referencia «id@n» (p. ej. cabeza-guemes-01@1)")
    if len(set(c.piezas)) != len(c.piezas):
        add("", "contenido.piezas", "hay piezas repetidas")
    for i, rid in enumerate(c.recetas):
        if not _ID_RE.match(rid):
            add("", f"contenido.recetas[{i}]", f"«{rid}» no es un id de personaje (minúsculas, números y guiones)")
    if len(set(c.recetas)) != len(c.recetas):
        add("", "contenido.recetas", "hay recetas repetidas")
    if p.tipo == "coleccion":
        if c.piezas or c.recetas or c.paleta:
            add("CK403", "contenido", "una colección solo lleva «pines»: no trae piezas, recetas ni paleta")
        if p.archivos:
            add("CK403", "archivos", "una colección no trae archivos de piezas, fichas ni color")
        if not c.pines:
            add("", "contenido.pines", "una colección tiene que fijar al menos un paquete")
        seen: set[str] = set()
        for i, pin in enumerate(c.pines):
            f = f"contenido.pines[{i}]"
            if not _ID_RE.match(pin.id or ""):
                add("CK402", f + ".id", f"inválido «{pin.id}»")
            elif pin.id in seen:
                add("CK402", f + ".id", f"«{pin.id}» está fijado dos veces (una versión por id)")
            seen.add(pin.id)
            if not _REPO_RE.match(pin.github or ""):
                add("CK402", f + ".github", f"«{pin.github}» no es «owner/repo»")
            if not is_semver(pin.version):
                add("CK402", f + ".version", f"«{pin.version}» no es una versión exacta (X.Y.Z)")
            if not _SHA_RE.match(pin.sha256 or ""):
                add("CK402", f + ".sha256", "tiene que ser hexadecimal en minúsculas de 64 caracteres")
    else:
        if c.pines:
            add("", "contenido.pines", "solo las colecciones fijan paquetes (tipo «coleccion»)")
        if p.tipo in ("piezas", "heroe") and not (c.piezas or c.recetas or c.paleta):
            add("", "contenido", "el paquete no trae nada: falta al menos una pieza, una receta o la paleta")


# ---------------------------------------------------------------------------------------------------------------- esquema


def schema() -> dict:
    """JSON Schema del manifiesto (`src/afm/schemas/afm-paquete-1.schema.json`), con la regla aditiva: claves nuevas permitidas."""
    from importlib import resources

    return json.loads(resources.files("afm").joinpath("schemas").joinpath(SCHEMA_FILE).read_text(encoding="utf-8"))
