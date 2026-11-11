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


@dataclass
class DeleteResult:
    """Resultado de `Catalog.delete_pieces`: las piezas eliminadas y los errores de disco (si los hubo)."""

    deleted: list
    errors: list

    @property
    def refs(self) -> list[str]:
        return [m.ref for m in self.deleted]


@dataclass
class WristCut:
    """Resultado de `Catalog.cut_wrist`: el brazo nuevo (sin mano, con puerto `muneca`), la mano y de qué versión salió."""

    arm: P.PieceMeta
    hand: P.PieceMeta
    from_ref: str
    warnings: list
    hand_volume_mm3: float
    wrist_radius_mm: float


@dataclass
class WristPlan:
    """Corte de muñeca calculado y todavía sin guardar (`Catalog.plan_wrist_cut`): para mirarlo antes de escribir."""

    from_ref: str                  # brazo del que sale
    arm_meta: P.PieceMeta          # brazo nuevo (sin mano, con puerto `muneca`); la versión la fija `add_piece`
    hand_meta: P.PieceMeta
    arm_mesh: object               # trimesh, marco de la pieza
    hand_mesh: object              # trimesh, marco de la mano estándar (origen en la muñeca)
    hand_world: object             # trimesh de la mano donde estaba en el brazo (para dibujar el corte)
    warnings: list
    hand_volume_mm3: float
    wrist_radius_mm: float


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

    # ------------------------------------------------------------------ contrato de interfaz (plan de compatibilidad, Fase 1)
    def contract(self):
        """Contrato de interfaz de la línea (`interface.Contract`), o None si `linea.json` no trae la clave `interfaz`."""
        from .interface import Contract

        raw = self.line.get("interfaz")
        return Contract.from_dict(raw) if raw else None

    def set_contract(self, contract) -> None:
        """Guarda el contrato en `linea.json` (clave `interfaz`). Falla antes de escribir si es incoherente."""
        from .interface import ContractError

        probs = contract.problems(self.params)
        if probs:
            raise ContractError("; ".join(probs))
        self.line["interfaz"] = contract.to_dict()
        self._write_line()

    def clear_contract(self) -> bool:
        if "interfaz" not in self.line:
            return False
        del self.line["interfaz"]
        self._write_line()
        return True

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

    @staticmethod
    def default_hand_id(arm_id: str) -> str:
        """Id de la mano que sale de cortar un brazo: `brazo-guemes-01` → `mano-guemes-01` (como en `agregar`)."""
        return f"mano-{re.sub(r'^brazo-', '', arm_id)}"

    def plan_wrist_cut(self, ref: str, points, invert: bool = False, hand_id: str | None = None) -> "WristPlan":
        """Calcula el corte de muñeca de un brazo del catálogo SIN escribir nada (la ventana lo muestra antes de guardar).

        `ref`: `id` o `id@n` de una pieza `brazo` sin puerto `muneca` (la mano pegada). `points`: 3 puntos de la muñeca en el
        marco de la pieza (el de la figura de origen: el mismo de `afm catalogo agregar --muneca`); si caen en el lado −X se
        espejan a +X. El punto 1 es la referencia de giro (siempre en el mismo lugar anatómico, p. ej. el lado del pulgar).
        `hand_id`: id de la mano (por defecto `mano-<resto del id del brazo>`, como en `agregar`). Que ese id ya exista no
        se mira acá sino al guardar (`save_wrist_plan`): se puede mirar el corte y después elegir otro id."""
        import copy

        import numpy as np

        from . import solids as S
        from .extract import _clean
        from .hand import HandError, WristFrame, split_at_wrist, to_local
        from .ports import Port, PortError, PortFrame, face_from_mesh

        arm_meta, arm_mesh = self.load(ref)
        if arm_meta.tipo != "brazo":
            raise CatalogError(f"«{arm_meta.ref}» es una pieza «{arm_meta.tipo}»: el corte de muñeca es para un «brazo»")
        if "muneca" in arm_meta.puertos:
            raise CatalogError(f"«{arm_meta.ref}» ya tiene el puerto «muneca» (ya está sin mano); elegí la versión con la mano pegada")
        later = [v for v in self.versions(arm_meta.id) if v > arm_meta.version]
        pid = hand_id or self.default_hand_id(arm_meta.id)
        if not P.ID_RE.match(pid):
            raise CatalogError(f"id de mano inválido «{pid}»: minúsculas, números y guiones (p. ej. mano-guemes-01)")
        if self.versions(pid) and self.meta(pid).tipo != "mano":
            raise CatalogError(f"«{pid}» es una pieza «{self.meta(pid).tipo}», no una mano")

        pts = np.asarray(points, float).reshape(-1, 3)
        if pts.shape != (3, 3):
            raise CatalogError("el plano de muñeca necesita 3 puntos (x, y, z)")
        mirrored = bool(pts[:, 0].mean() < 0)
        if mirrored:
            pts = pts * np.array([-1.0, 1.0, 1.0])
        warnings: list[str] = []
        if mirrored:
            warnings.append("los 3 puntos estaban en el lado −X: se espejaron a +X, que es donde está la pieza")
        if later:
            warnings.append(f"«{arm_meta.id}» ya tiene versiones más nuevas ({later}); la nueva sale de la {arm_meta.version} y las pasa por encima")

        try:
            ws = split_at_wrist(S.to_manifold(arm_mesh), WristFrame.from_points(pts), invert=invert)
        except (HandError, S.MeshNotManifold) as exc:
            raise CatalogError(f"muñeca de «{arm_meta.ref}»: {exc}") from exc
        rest = _clean(ws.rest, "brazo (sin mano)", warnings)
        hand_local = _clean(to_local(ws.hand, ws.frame), "mano", warnings)
        wf = PortFrame.from_wrist(ws.frame, toward_hand=True)
        hf = PortFrame.hand_standard()
        try:
            wrist_port = Port("muneca", "toma", wf, face_from_mesh(rest, wf, place="muneca"))
            hand_port = Port("muneca", "enchufe", hf, face_from_mesh(hand_local, hf, place="muneca"))
        except PortError as exc:
            raise CatalogError(f"muñeca de «{arm_meta.ref}»: {exc}") from exc

        arm_new = copy.deepcopy(arm_meta)
        arm_new.puertos["muneca"] = wrist_port
        arm_new.estado = "borrador"
        arm_new.origen = {**arm_new.origen, "nota": f"sin mano: muñeca cortada de {arm_meta.ref}", "fecha": P.make_origin()["fecha"],
                          "motor": __version__}
        origin = {**P.make_origin(arm_meta.origen.get("figura", ""), arm_meta.origen.get("proyecto", ""),
                                  note=f"mano cortada de {arm_meta.ref}")}
        hand_meta = P.PieceMeta(id=pid, version=1, tipo="mano", puertos={"muneca": hand_port}, descripcion=f"Mano de {arm_meta.id}",
                                origen=origin, altura_figura_mm=arm_meta.altura_figura_mm, lado=arm_meta.lado,
                                espejable=arm_meta.espejable, etiquetas=list(arm_meta.etiquetas))
        for m in (arm_new, hand_meta):                       # todo se valida antes de escribir: o se guardan las dos o ninguna
            bad = m.problems()
            if bad:
                raise CatalogError(f"{m.id}: {'; '.join(bad)}")
        for label, tm in (("brazo", rest), ("mano", hand_local)):
            if not tm.is_watertight:
                raise CatalogError(f"el corte dejó la malla del {label} sin cerrar: no se guarda (probá otros 3 puntos de muñeca)")
        return WristPlan(from_ref=arm_meta.ref, arm_meta=arm_new, hand_meta=hand_meta, arm_mesh=rest, hand_mesh=hand_local,
                         hand_world=S.to_trimesh(ws.hand), warnings=warnings, hand_volume_mm3=float(ws.hand_volume),
                         wrist_radius_mm=float(ws.face_radius))

    def save_wrist_plan(self, plan: "WristPlan", new_hand_version: bool = False) -> "WristCut":
        """Guarda el resultado de `plan_wrist_cut`: la mano primero y el brazo nuevo después (versión siguiente del id). Si el id
        de la mano ya existe hace falta `new_hand_version=True`; sin eso no se escribe nada."""
        pid = plan.hand_meta.id
        have = self.versions(pid)
        if have and not new_hand_version:
            raise CatalogError(f"la mano «{pid}» ya existe (versiones {have}): pasá otro id o pedí una versión nueva")
        hand_saved, w1 = self.add_piece(plan.hand_mesh, plan.hand_meta, new_version=bool(have))
        arm_saved, w2 = self.add_piece(plan.arm_mesh, plan.arm_meta, new_version=True)
        return WristCut(arm=arm_saved, hand=hand_saved, from_ref=plan.from_ref,
                        warnings=list(plan.warnings) + [w for w in dict.fromkeys(w1 + w2)],
                        hand_volume_mm3=plan.hand_volume_mm3, wrist_radius_mm=plan.wrist_radius_mm)

    def retune_joint(self, ref: str, joint, note: str = "") -> tuple[P.PieceMeta, list[str]]:
        """Guarda una versión nueva de la pieza `ref` (un `brazo` o una `pierna`) con la articulación `joint` (`JointSpec` en el
        marco de la pieza) en lugar de la del mismo nombre. La malla y los puertos no cambian: la articulación es un dato de
        la pieza (el hueco se talla al armar), así que la versión nueva es la misma malla con otro eje / plano / rango.

        Las piezas son inmutables: la versión original queda como está (las recetas que la fijan por `id@n` arman igual) y la
        nueva sale en estado «borrador». Devuelve (meta de la versión nueva, avisos)."""
        import copy

        from .joint_edit import MIN_H_MM, differs, height
        from .joints import JointSpec

        meta, mesh = self.load(ref)
        kind = {"brazo": "elbow", "pierna": "knee"}.get(meta.tipo)
        if kind is None:
            raise CatalogError(f"«{meta.ref}» es una pieza «{meta.tipo}»: solo un brazo o una pierna lleva articulación")
        if joint.kind != kind:
            raise CatalogError(f"la articulación «{joint.name}» es «{joint.kind}» y una pieza «{meta.tipo}» lleva «{kind}»")
        slots = [i for i, jd in enumerate(meta.articulaciones or []) if jd.get("name") == joint.name]
        if not slots:
            raise CatalogError(f"«{meta.ref}» no trae una articulación «{joint.name}» para reajustar")
        before = JointSpec.from_dict(meta.articulaciones[slots[0]])
        if not differs(before, joint):
            raise CatalogError("la articulación no cambió: no hay nada que guardar")
        if height(joint) < MIN_H_MM:
            raise CatalogError(f"el eje tiene que quedar al menos {MIN_H_MM:g} mm arriba del plano de articulación")
        new = copy.deepcopy(meta)
        new.articulaciones[slots[0]] = joint.to_dict()
        new.estado = "borrador"
        new.origen = {**new.origen, "nota": note or f"articulación «{joint.name}» reajustada desde {meta.ref}",
                      "fecha": P.make_origin()["fecha"], "motor": __version__}
        warnings = []
        later = [v for v in self.versions(meta.id) if v > meta.version]
        if later:
            warnings.append(f"«{meta.id}» ya tiene versiones más nuevas ({later}); la nueva sale de la {meta.version} y las pasa por encima")
        saved, w = self.add_piece(mesh, new, new_version=True)
        return saved, warnings + w

    def cut_wrist(self, ref: str, points, invert: bool = False, hand_id: str | None = None,
                  new_hand_version: bool = False) -> "WristCut":
        """Le corta la mano a un brazo que ya está en el catálogo (no se vuelve a necesitar la figura ni su `.proyecto`).

        Las piezas son inmutables: el brazo original queda como está (las recetas que lo fijan por `id@n` siguen armando igual)
        y se guarda una versión nueva SIN mano, con el puerto `muneca` (toma), más una pieza `mano` aparte (enchufe) en el
        marco estándar. Ver `plan_wrist_cut` para los argumentos. Si el id de la mano ya existe hace falta `new_hand_version=True`.
        No se escribe nada hasta que el corte, las mallas y los puertos son válidos."""
        return self.save_wrist_plan(self.plan_wrist_cut(ref, points, invert, hand_id), new_hand_version)

    def add_accessory(self, mesh, piece_id: str, agarre: dict, new_version: bool = False, descripcion: str = "", figura: str = "",
                      extra: dict | None = None):
        """Guarda una pieza `accesorio` con su agarre (ver `parts.piece_accessory`); `extra` suma claves al mecanismo (p. ej. «arma»)."""
        meta, mesh = P.piece_accessory(mesh, piece_id, agarre, descripcion, figura, extra=extra)
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

    def rename_piece(self, ref: str, nombre: str) -> list[P.PieceMeta]:
        """Cambia el nombre para mostrar de una pieza (TODAS las versiones de su `id`). Es solo un dato de la ficha: el `id`, la
        `ref`, el STL y el hash no cambian, así que las recetas y los proyectos que la nombran siguen armando igual.
        `nombre` vacío quita el nombre (se vuelve a mostrar el `id`). No se admite un nombre que ya usa OTRA pieza (ni el `id`
        de otra), para que la lista no tenga dos filas iguales."""
        try:
            name = P.clean_name(nombre)
        except P.PieceError as exc:
            raise CatalogError(str(exc)) from exc
        pid, _ver = parse_ref(ref)
        metas = [m for m in self._metas() if m.id == pid]
        if not metas:
            raise CatalogError(f"no hay ninguna pieza «{ref}» en el catálogo «{self.name}»")
        if name:
            for o in self._metas():
                if o.id != pid and name.lower() in (o.display_name.lower(), o.id.lower()):
                    raise CatalogError(f"el nombre «{name}» ya lo usa la pieza «{o.id}»: elegí otro")
        for m in metas:
            m.nombre = name
            path = os.path.join(self._dir(m.tipo), P.meta_name(m.id, m.version))
            with open(path, "w") as f:
                json.dump(m.to_dict(), f, indent=2, ensure_ascii=False)
        self.write_index()
        return metas

    def measure_capacity(self, ref: str, write: bool = False) -> dict:
        """Capacidad del anfitrión de cada toma con pasador de `ref` (plan de compatibilidad, Fase 3): `{puerto: dict | None}`,
        medida sobre el STL de la pieza (con la comprobación del hash). Con `write=True` la deja en el `.pieza.json`
        (`capacidad` del puerto). Es un dato derivado de la malla y de los `Params` de la línea: no toca el STL, el hash, el
        id ni la versión, y el resto de los metadatos queda igual (como `set_state`, no crea una versión nueva)."""
        from .capacity import measure, pin_of

        m, mesh = self.load(ref)
        params = self.params
        out: dict = {}
        for name, port in m.puertos.items():
            if port.rol != "toma" or pin_of(port.clase, params) is None:
                continue
            out[name] = measure(mesh, port, params)
        if write and out:
            for name, cap in out.items():
                m.puertos[name].capacity = cap
            path = os.path.join(self._dir(m.tipo), P.meta_name(m.id, m.version))
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(m.to_dict(), f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        return out

    def _piece_files(self, meta: P.PieceMeta) -> list[str]:
        """Archivos de una pieza (`.pieza.json` primero, después el STL), buscados en `piezas/*/` aunque estén en otra carpeta."""
        meta_file = P.meta_name(meta.id, meta.version)
        found = sorted(glob.glob(os.path.join(self.root, "piezas", "*", meta_file)))
        if not found:
            return []
        folder = os.path.dirname(found[0])
        files = list(found)
        stl = os.path.join(folder, os.path.basename(meta.stl or P.stl_name(meta.id, meta.version)))
        if os.path.isfile(stl):
            files.append(stl)
        return files

    def delete_pieces(self, refs: list[str]) -> "DeleteResult":
        """Elimina del disco las versiones `id@n` indicadas (el `.pieza.json` y el STL) y regenera el índice.

        Pide siempre `id@versión`: una `id` sola significa «la última» en el resto del catálogo y borrarla por error es
        irreversible. Primero se comprueba que TODAS existan (si una no existe no se borra nada). Las demás versiones
        del mismo id no cambian de número: queda un hueco, y una versión nueva sigue contando desde la mayor que quede.
        También se quitan del `compat.json` los resultados que dependían de lo borrado. No toca las recetas ni los
        proyectos que la usaban: `usages` dice cuáles son para avisar antes."""
        if not refs:
            raise CatalogError("no se indicó ninguna pieza para eliminar")
        metas: dict[str, P.PieceMeta] = {}
        for ref in refs:
            pid, ver = parse_ref(ref)
            if ver is None:
                raise CatalogError(f"«{ref}»: para eliminar hay que indicar la versión (<id>@<n>)")
            metas[f"{pid}@{ver}"] = self.meta(f"{pid}@{ver}")
        deleted, errors = [], []
        for meta in metas.values():
            files = self._piece_files(meta)
            try:
                for path in files:              # el `.pieza.json` va primero: si algo falla, la pieza ya no figura en el catálogo
                    os.remove(path)
            except OSError as exc:
                errors.append(f"{meta.ref}: {exc}")
                continue
            deleted.append(meta)
        if deleted:
            self.write_index()
            from .compat import CompatCache
            CompatCache(self.root).prune(self)
        return DeleteResult(deleted, errors)

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
                    # el puerto «_der» es el «_izq» espejado: una pieza espejable ya se compara por el lado izquierdo
                    if (nb.endswith("_der") and me.espejable) or (na.endswith("_der") and other.espejable):
                        continue
                    c = port_compat(pa, pb, me.altura_figura_mm, other.altura_figura_mm, params)
                    if c.nivel != "no":
                        out.append((other, na, nb, c))
        return out

    def compatible_grips(self, ref: str) -> list[tuple[P.PieceMeta, Compat]]:
        """Accesorios que se sujetan en la mano `ref` (o manos donde se sujeta el accesorio `ref`), por el agarre de ambos;
        sin los pares `no`. Aparte de `compatible_with` a propósito: el agarre no es un puerto (no tiene cara de unión)."""
        from .props import grip_compat

        me = self.meta(ref)
        mine = (me.mecanismo or {}).get("agarre")
        if not mine or me.tipo not in ("mano", "accesorio"):
            return []
        want = "accesorio" if me.tipo == "mano" else "mano"
        out = []
        for other in self.list(tipo=want):
            theirs = (other.mecanismo or {}).get("agarre")
            if not theirs:
                continue
            c = grip_compat(mine, theirs) if me.tipo == "mano" else grip_compat(theirs, mine)
            if c.nivel != "no":
                out.append((other, c))
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
            ag = (m.mecanismo or {}).get("agarre")
            if ag is not None and m.tipo in ("mano", "accesorio"):
                from .props import grip_problems
                problems += [f"{tag}: {x}" for x in grip_problems(ag, m.tipo)]
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
        problems_c, warnings_c = self._verify_contract()
        problems += problems_c
        warnings += warnings_c
        for pid, vs in by_id.items():
            if len(set(vs)) != len(vs):
                problems.append(f"{pid}: versiones repetidas {sorted(vs)}")
            elif sorted(vs) != list(range(1, len(vs) + 1)):
                # eliminar una versión (`delete_pieces`) deja un hueco a propósito: las recetas fijan `id@versión`
                warnings.append(f"{pid}: versiones no consecutivas {sorted(vs)} (¿se eliminó alguna?)")
        return Report(problems, warnings)

    def _verify_contract(self) -> tuple[list, list]:
        """Con contrato: lo valida y avisa de los enchufes de clase dura que quedan fuera de contrato (no es un problema:
        una pieza «fuera de contrato» es válida, solo no entra en un talle)."""
        from .interface import ContractError, HARD_CLASSES, label_piece

        try:
            contract = self.contract()
        except (ContractError, KeyError, ValueError, TypeError) as exc:
            return [f"linea.json: contrato de interfaz ilegible: {exc}"], []
        if contract is None:
            return [], []
        problems = [f"contrato de interfaz: {x}" for x in contract.problems(self.params)]
        if abs(contract.altura_mm - self.height_mm) > LINE_HEIGHT_TOL_MM:
            problems.append(f"contrato de interfaz: altura {contract.altura_mm:g} mm y la línea usa {self.height_mm:g} mm")
        warnings = []
        for m in self.list():
            for name, lab in label_piece(contract, m, only_plugs=True).items():
                if lab.clase in HARD_CLASSES and not lab.en_contrato:
                    warnings.append(f"{m.ref}: puerto «{name}» fuera de contrato: {lab.resumen()}")
        return problems, warnings

    # ------------------------------------------------------------------ usos
    def usages(self, ref: str, search: list[str] | None = None, recursive: bool = True) -> list[dict]:
        """Recetas (y `.proyecto` de las carpetas en `search`, con subcarpetas salvo `recursive=False`) que usan `ref`.
        Con `id` sin versión, cualquiera de sus versiones; con `id@n`, solo esa. Sirve para saber qué figuras regenerar
        al cambiar o eliminar una pieza."""
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
            pattern = os.path.join(d, "**", "*.proyecto") if recursive else os.path.join(d, "*.proyecto")
            files += sorted(glob.glob(pattern, recursive=recursive))
        for path in files:
            try:
                with open(path) as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if path.endswith(".receta.json"):
                slots = {k: v for k, v in (data.get("lugares") or {}).items()}
                for i, acc in enumerate(data.get("accesorios") or []):      # los accesorios que acompañan a la figura
                    slots[f"accesorio{i + 1}"] = acc
            else:                       # proyecto: `parts` (Fase 5) = {lugar: {id, version, …}}
                slots = {k: ((f"{v['id']}@{v['version']}" if v.get("version") is not None else v["id"])
                             if isinstance(v, dict) and "id" in v else None)
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
            lines += ["| Pieza | Nombre | Tipo | Estado | Etiquetas | Puertos | Origen | Volumen (mm³) |", "|---|---|---|---|---|---|---|---|"]
            for m in metas:
                origen = m.origen.get("figura") or m.origen.get("nota") or ""
                ag = (m.mecanismo or {}).get("agarre") or {}
                grip = (f"agarre Ø{ag['diametro_taladro_mm']:g}" if m.tipo == "mano" and "diametro_taladro_mm" in ag
                        else f"agarre Ø{ag['taladro_mm']:g}" if m.tipo == "accesorio" and "taladro_mm" in ag else "")
                lines.append(f"| `{m.ref}` | {m.nombre or '—'} | {m.tipo} | {m.estado} | {', '.join(m.etiquetas) or '—'} | "
                             f"{', '.join(list(m.puertos) + ([grip] if grip else [])) or '—'} | {origen} | {m.volumen_mm3:.0f} |")
        path = os.path.join(self.root, "CATALOGO.md")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path
