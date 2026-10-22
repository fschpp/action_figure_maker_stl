"""Catálogo en disco de piezas de una línea de figuras (plan de piezas intercambiables, §3.3).

```
<RUTA>/                              # un directorio por línea
  linea.json                         # afm-linea-1: nombre, altura_mm, boquilla, params (perfil), lugares, motor
  piezas/<tipo>/<id>@<n>.stl         # malla cruda, mm reales (ver parts.py)
  piezas/<tipo>/<id>@<n>.pieza.json  # afm-pieza-1
  figuras/<personaje>.receta.json    # afm-receta-1 (Fase 6)
  compat.json                        # caché de compatibilidad completa (Fase 7, generado)
  CATALOGO.md                        # índice legible (generado)
```

Las piezas son inmutables: cambiar una crea la versión n + 1 (`add_piece(..., new_version=True)`). Las recetas fijan
`id@version`. El catálogo vive fuera del repo (los STL generados pesan): `default_root` lo busca en `AFM_CATALOGO` o en
`~/.local/share/afm/catalogos/<linea>/`.
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass

from . import __version__
from . import parts as P
from .params import Params
from .ports import Compat, port_compat

LINE_FORMAT = "afm-linea-1"
LINE_PLACES = ("cabeza", "torso", "pelvis", "brazo", "pierna", "mano")
REF_RE = re.compile(r"^(?P<id>[a-z0-9]+(?:-[a-z0-9]+)*)(?:@(?P<v>\d+))?$")
LINE_HEIGHT_TOL_MM = 0.5


class CatalogError(ValueError):
    """Error del catálogo (no existe, la pieza ya existe, referencia inválida…)."""


def default_root(line: str) -> str:
    """Directorio por defecto del catálogo de la línea: `$AFM_CATALOGO` o `~/.local/share/afm/catalogos/<linea>`."""
    env = os.environ.get("AFM_CATALOGO")
    if env:
        return env
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "afm", "catalogos", line)


def parse_ref(ref: str) -> tuple[str, int | None]:
    """`id` o `id@version` → (id, versión o None)."""
    m = REF_RE.match(ref.strip())
    if not m:
        raise CatalogError(f"referencia inválida «{ref}»: se espera <id> o <id>@<versión>")
    return m.group("id"), (int(m.group("v")) if m.group("v") else None)


@dataclass
class Report:
    """Resultado de `Catalog.verify`: `problems` (errores) y `warnings` (avisos)."""

    problems: list
    warnings: list

    @property
    def ok(self) -> bool:
        return not self.problems


class Catalog:
    def __init__(self, root: str, line: dict):
        self.root = os.path.abspath(root)
        self.line = line

    # ------------------------------------------------------------------ línea
    @classmethod
    def create(cls, root: str, name: str, height_mm: float, nozzle: float = 0.4, params: Params | None = None) -> "Catalog":
        if os.path.exists(os.path.join(root, "linea.json")):
            raise CatalogError(f"«{root}» ya es un catálogo (tiene linea.json)")
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", name or ""):
            raise CatalogError(f"nombre de línea inválido «{name}»: letras, números, guiones y puntos")
        if height_mm <= 0:
            raise CatalogError("la altura de la línea tiene que ser mayor que 0")
        if params is None:
            from .profile import default_for_nozzle
            params = default_for_nozzle(nozzle)
        params.ref_height = float(height_mm)
        params.nozzle = float(nozzle)
        line = {"formato": LINE_FORMAT, "nombre": name, "altura_mm": float(height_mm), "boquilla": float(nozzle),
                "params": params.to_dict(), "lugares": list(LINE_PLACES), "motor": __version__}
        os.makedirs(os.path.join(root, "piezas"), exist_ok=True)
        os.makedirs(os.path.join(root, "figuras"), exist_ok=True)
        cat = cls(root, line)
        cat._write_line()
        cat.write_index()
        return cat

    @classmethod
    def open(cls, root: str) -> "Catalog":
        path = os.path.join(root, "linea.json")
        if not os.path.isfile(path):
            raise CatalogError(f"«{root}» no es un catálogo (falta linea.json); crearlo con `afm catalogo crear`")
        with open(path) as f:
            line = json.load(f)
        if line.get("formato") != LINE_FORMAT:
            raise CatalogError(f"«{path}»: formato {line.get('formato')!r}, se esperaba {LINE_FORMAT!r}")
        return cls(root, line)

    def _write_line(self) -> None:
        with open(os.path.join(self.root, "linea.json"), "w") as f:
            json.dump(self.line, f, indent=2, ensure_ascii=False)

    @property
    def name(self) -> str:
        return self.line["nombre"]

    @property
    def height_mm(self) -> float:
        return float(self.line["altura_mm"])

    @property
    def params(self) -> Params:
        """Perfil de la línea (parámetros comunes a todas las figuras)."""
        return Params.from_dict(self.line["params"])

    # ------------------------------------------------------------------ piezas
    def _dir(self, tipo: str) -> str:
        return os.path.join(self.root, "piezas", tipo)

    def _metas(self) -> list[P.PieceMeta]:
        out = []
        for path in sorted(glob.glob(os.path.join(self.root, "piezas", "*", "*.pieza.json"))):
            out.append(P.read_meta(path))
        return out

    def versions(self, piece_id: str) -> list[int]:
        return sorted(m.version for m in self._metas() if m.id == piece_id)

    def meta(self, ref: str) -> P.PieceMeta:
        """Metadatos de `id` (la última versión) o de `id@n`."""
        pid, ver = parse_ref(ref)
        found = [m for m in self._metas() if m.id == pid and (ver is None or m.version == ver)]
        if not found:
            raise CatalogError(f"no hay ninguna pieza «{ref}» en el catálogo «{self.name}»")
        return max(found, key=lambda m: m.version)

    def load(self, ref: str, check_hash: bool = True):
        """(meta, malla trimesh) de `ref`."""
        m = self.meta(ref)
        return P.read_piece(self._dir(m.tipo), m.id, m.version, check_hash)

    def add_piece(self, mesh, meta: P.PieceMeta, new_version: bool = False) -> tuple[P.PieceMeta, list[str]]:
        """Guarda una pieza. Si el id ya existe hace falta `new_version=True` (crea la versión siguiente); nunca sobrescribe.

        Devuelve (meta ya completa con hash y volumen, avisos). Avisa si la altura de la figura de origen difiere de la
        de la línea (mm reales: la pieza no se reescala)."""
        existing = self.versions(meta.id)
        if existing and not new_version:
            raise CatalogError(f"«{meta.id}» ya existe (versiones {existing}): las piezas son inmutables; "
                               "guardá una versión nueva con new_version=True")
        if existing:
            if self.meta(meta.id).tipo != meta.tipo:
                raise CatalogError(f"«{meta.id}» es de tipo «{self.meta(meta.id).tipo}»: una versión nueva no puede cambiar de tipo")
            meta.version = existing[-1] + 1
        else:
            meta.version = 1
        warnings = []
        if meta.altura_figura_mm is not None and abs(meta.altura_figura_mm - self.height_mm) > LINE_HEIGHT_TOL_MM:
            warnings.append(f"la figura de origen mide {meta.altura_figura_mm:g} mm y la línea «{self.name}» {self.height_mm:g} mm: "
                            "las piezas no se reescalan")
        P.write_piece(self._dir(meta.tipo), meta, mesh)
        self.write_index()
        return meta, warnings

    def import_hand(self, stl_path: str, piece_id: str | None = None, new_version: bool = False):
        """Importa una mano estándar `afm-mano-1` (la de `afm extraer-mano`) como pieza `mano`."""
        meta, mesh = P.piece_from_standard_hand(stl_path, piece_id)
        return self.add_piece(mesh, meta, new_version)

    def list(self, tipo: str | None = None, tag: str | None = None, state: str | None = None,
             all_versions: bool = False) -> list[P.PieceMeta]:
        """Piezas del catálogo (por defecto solo la última versión de cada id), con filtros."""
        metas = self._metas()
        if not all_versions:
            latest: dict[str, P.PieceMeta] = {}
            for m in metas:
                if m.id not in latest or m.version > latest[m.id].version:
                    latest[m.id] = m
            metas = list(latest.values())
        return [m for m in metas if (tipo is None or m.tipo == tipo) and (tag is None or tag in m.etiquetas)
                and (state is None or m.estado == state)]

    def set_state(self, ref: str, state: str) -> P.PieceMeta:
        """Cambia el estado (borrador → validada → impresa): es lo único mutable de una pieza (el STL y los puertos no)."""
        if state not in P.STATES:
            raise CatalogError(f"estado desconocido «{state}» (hay {', '.join(P.STATES)})")
        m = self.meta(ref)
        m.estado = state
        path = os.path.join(self._dir(m.tipo), P.meta_name(m.id, m.version))
        with open(path, "w") as f:
            json.dump(m.to_dict(), f, indent=2, ensure_ascii=False)
        self.write_index()
        return m

    # ------------------------------------------------------------------ compatibilidad barata
    def compatible_with(self, ref: str, params: Params | None = None) -> list[tuple[P.PieceMeta, str, str, Compat]]:
        """Piezas del catálogo que se acoplan con `ref` por algún puerto, con el resultado de la compatibilidad barata
        (solo metadatos): lista de (pieza, puerto de `ref`, puerto de la otra, resultado), sin los pares `no`."""
        me = self.meta(ref)
        params = params or self.params
        out = []
        for other in self.list():
            if other.id == me.id:
                continue
            for na, pa in me.puertos.items():
                for nb, pb in other.puertos.items():
                    if pa.clase != pb.clase:
                        continue
                    c = port_compat(pa, pb, me.altura_figura_mm, other.altura_figura_mm, params)
                    if c.nivel != "no":
                        out.append((other, na, nb, c))
        return out

    # ------------------------------------------------------------------ verificar
    def verify(self) -> Report:
        """Formato, hash, mallas cerradas, puertos, caras y altura de cada pieza; versiones salteadas."""
        problems, warnings = [], []
        by_id: dict[str, list[int]] = {}
        for path in sorted(glob.glob(os.path.join(self.root, "piezas", "*", "*.pieza.json"))):
            try:
                m = P.read_meta(path)
            except (P.PieceError, KeyError, ValueError, json.JSONDecodeError) as exc:
                problems.append(f"{os.path.relpath(path, self.root)}: {exc}")
                continue
            tag = m.ref
            by_id.setdefault(m.id, []).append(m.version)
            if os.path.basename(os.path.dirname(path)) != m.tipo:
                problems.append(f"{tag}: está en piezas/{os.path.basename(os.path.dirname(path))} pero es de tipo «{m.tipo}»")
            if os.path.basename(path) != P.meta_name(m.id, m.version):
                problems.append(f"{tag}: el nombre del archivo no coincide con id@versión")
            problems += [f"{tag}: {x}" for x in m.problems()]
            stl = os.path.join(os.path.dirname(path), m.stl)
            if not os.path.isfile(stl):
                problems.append(f"{tag}: falta el STL «{m.stl}»")
                continue
            if m.sha256 and P.sha256_file(stl) != m.sha256:
                problems.append(f"{tag}: el STL no coincide con el hash guardado (¿se modificó?)")
                continue
            try:
                mesh = P.load_mesh(stl)
            except Exception as exc:       # STL ilegible
                problems.append(f"{tag}: no se pudo leer el STL: {exc}")
                continue
            if not mesh.is_watertight:
                problems.append(f"{tag}: la malla no es estanca")
            if len(m.puertos) < 1 and m.tipo != "accesorio":
                problems.append(f"{tag}: sin puertos")
            for name, p in m.puertos.items():
                if p.face is None:
                    warnings.append(f"{tag}: el puerto «{name}» no tiene cara de unión")
                    continue
                try:
                    if not p.face.shape().is_valid:
                        problems.append(f"{tag}: puerto «{name}»: polígono de la cara inválido")
                except Exception as exc:
                    problems.append(f"{tag}: puerto «{name}»: cara ilegible: {exc}")
            if m.altura_figura_mm is not None and abs(m.altura_figura_mm - self.height_mm) > LINE_HEIGHT_TOL_MM:
                warnings.append(f"{tag}: altura de origen {m.altura_figura_mm:g} mm, la línea usa {self.height_mm:g} mm")
        for pid, vs in by_id.items():
            if sorted(vs) != list(range(1, len(vs) + 1)):
                problems.append(f"{pid}: versiones no consecutivas {sorted(vs)}")
        return Report(problems, warnings)

    # ------------------------------------------------------------------ usos
    def usages(self, ref: str, search: list[str] | None = None) -> list[dict]:
        """Recetas (y `.proyecto` de las carpetas en `search`) que usan `ref`. Con `id` sin versión, cualquiera de sus
        versiones; con `id@n`, solo esa. Sirve para saber qué figuras regenerar al cambiar una pieza."""
        pid, ver = parse_ref(ref)

        def hits(values) -> list[str]:
            found = []
            for v in values:
                if not isinstance(v, str):
                    continue
                try:
                    i, n = parse_ref(v)
                except CatalogError:
                    continue
                if i == pid and (ver is None or n is None or n == ver):
                    found.append(v)
            return found

        out = []
        files = sorted(glob.glob(os.path.join(self.root, "figuras", "*.receta.json")))
        for d in search or []:
            files += sorted(glob.glob(os.path.join(d, "**", "*.proyecto"), recursive=True))
        for path in files:
            try:
                with open(path) as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if path.endswith(".receta.json"):
                slots = {k: v for k, v in (data.get("lugares") or {}).items()}
            else:                       # proyecto: `parts` (Fase 5) = {lugar: {id, version, …}}
                slots = {k: (f"{v['id']}@{v['version']}" if isinstance(v, dict) and "id" in v else None)
                         for k, v in (data.get("parts") or {}).items()}
            for place, v in slots.items():
                if hits([v]):
                    out.append({"archivo": path, "lugar": place, "pieza": v})
        return out

    # ------------------------------------------------------------------ índice
    def write_index(self) -> str:
        """Regenera `CATALOGO.md` (índice legible; es un archivo generado)."""
        metas = sorted(self.list(all_versions=True), key=lambda m: (m.tipo, m.id, m.version))
        lines = [f"# Catálogo «{self.name}»", "",
                 f"_Generado por afm {__version__}. No editar a mano: `afm catalogo listar`._", "",
                 f"- Altura de la línea: **{self.height_mm:g} mm** · boquilla **{self.line.get('boquilla', 0.4):g} mm** · "
                 f"holgura **{self.params.clearance:g} mm**", ""]
        if not metas:
            lines.append("(sin piezas todavía)")
        else:
            lines += ["| Pieza | Tipo | Estado | Etiquetas | Puertos | Origen | Volumen (mm³) |", "|---|---|---|---|---|---|---|"]
            for m in metas:
                origen = m.origen.get("figura") or m.origen.get("nota") or ""
                lines.append(f"| `{m.ref}` | {m.tipo} | {m.estado} | {', '.join(m.etiquetas) or '—'} | "
                             f"{', '.join(m.puertos) or '—'} | {origen} | {m.volumen_mm3:.0f} |")
        path = os.path.join(self.root, "CATALOGO.md")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path
