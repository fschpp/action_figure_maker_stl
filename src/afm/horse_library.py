"""E11: biblioteca de caballos de la línea (`<catalogo>/caballos/`), separada del catálogo de piezas.

Cada versión de un caballo vive en su propia carpeta y no se sobrescribe nunca (plan 7.1, «versiones inmutables o por hash»):

    <catalogo>/caballos/<id>/v<N>/caballo.json   (ficha `HorseMeta`, formato `afm-caballo-1`)
    <catalogo>/caballos/<id>/v<N>/modelo.stl     (copia administrada del STL, con el SHA-256 de la ficha)

No usa `PieceMeta` ni `Catalog.add_piece()` y el catálogo de piezas no la recorre. Este módulo no importa `manifold3d`: la
geometría pasa por `equestrian.load_horse_mesh`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile

from .equestrian import HorseError, HorseMeta, HorseModel, HorseSpec, load_horse_mesh, sha256_file

LIB_DIR = "caballos"
META_FILE = "caballo.json"
MODEL_FILE = "modelo.stl"
_REF_RE = re.compile(r"^(?P<id>[a-z0-9]+(?:-[a-z0-9]+)*)(?:@(?P<v>[0-9]+))?$")
_VER_RE = re.compile(r"^v([0-9]+)$")


def parse_horse_ref(ref: str) -> tuple[str, int | None]:
    """`id` o `id@versión` → (id, versión o None = la última)."""
    m = _REF_RE.match(ref.strip()) if isinstance(ref, str) else None
    if not m or (m.group("v") is not None and int(m.group("v")) < 1):
        raise HorseError(f"referencia de caballo inválida «{ref}»: se espera <id> o <id>@<versión> (versión ≥ 1)")
    return m.group("id"), (int(m.group("v")) if m.group("v") else None)


class HorseLibrary:
    """Agregar, listar, abrir y verificar caballos. `root` es la carpeta del catálogo de la línea (`Catalog.root`)."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.dir = os.path.join(self.root, LIB_DIR)

    # ------------------------------------------------------------------ lectura
    def _vdir(self, hid: str, version: int) -> str:
        return os.path.join(self.dir, hid, f"v{version}")

    def versions(self, hid: str) -> list[int]:
        d = os.path.join(self.dir, hid)
        if not os.path.isdir(d):
            return []
        out = []
        for n in os.listdir(d):
            m = _VER_RE.match(n)
            if m and os.path.isfile(os.path.join(d, n, META_FILE)):
                out.append(int(m.group(1)))
        return sorted(out)

    def _read(self, hid: str, version: int) -> HorseMeta:
        path = os.path.join(self._vdir(hid, version), META_FILE)
        try:
            with open(path, encoding="utf-8") as fh:
                meta = HorseMeta.from_dict(json.load(fh))
        except (OSError, ValueError) as exc:
            raise HorseError(f"no se pudo leer la ficha {path}: {exc}") from None
        if (meta.id, meta.version) != (hid, version):
            raise HorseError(f"la ficha {path} dice «{meta.ref}» pero está en «{hid}@{version}»")
        return meta

    def meta(self, ref: str) -> HorseMeta:
        hid, v = parse_horse_ref(ref)
        vs = self.versions(hid)
        if not vs:
            raise HorseError(f"no hay un caballo «{hid}» en la biblioteca")
        if v is None:
            v = vs[-1]
        elif v not in vs:
            raise HorseError(f"el caballo «{hid}» no tiene la versión {v} (hay {', '.join(map(str, vs))})")
        return self._read(hid, v)

    def list(self, all_versions: bool = False) -> list[HorseMeta]:
        """Los caballos de la biblioteca ordenados por id: la última versión de cada uno (o todas con `all_versions`)."""
        if not os.path.isdir(self.dir):
            return []
        out = []
        for hid in sorted(os.listdir(self.dir)):
            if not _REF_RE.match(hid):
                continue
            vs = self.versions(hid)
            out += [self._read(hid, v) for v in (vs if all_versions else vs[-1:])]
        return out

    def path(self, ref: str) -> str:
        """Ruta del STL administrado de `ref`."""
        m = self.meta(ref)
        return os.path.join(self._vdir(m.id, m.version), m.spec.archivo)

    def open(self, ref: str, check_hash: bool = True, repair: bool = True) -> HorseModel:
        """Carga el caballo `ref` (verifica el SHA-256 de la ficha)."""
        m = self.meta(ref)
        return load_horse_mesh(m.spec, base_dir=self._vdir(m.id, m.version), repair=repair, check_hash=check_hash)

    # ------------------------------------------------------------------ escritura
    def add(self, stl_path: str, hid: str, nombre: str = "", escala: float = 1.0, yaw: float = 0.0, ref_tipo: str | None = None,
            ref_mm: float | None = None, new_version: bool = False, repair: bool = True) -> HorseMeta:
        """Agrega un caballo (copia administrada del STL) y devuelve su ficha.

        Se valida antes de guardar: el STL tiene que cargar como malla estanca (o repararse si `repair`). Nunca se sobrescribe
        una versión: si el id ya existe hay que pedir `new_version=True` (version + 1); si lo que se agrega es idéntico a la
        última versión (mismo STL y misma ficha) no se crea nada y se devuelve esa. El STL de origen no se toca."""
        if not isinstance(hid, str) or not _REF_RE.match(hid) or "@" in hid:
            raise HorseError("«id» tiene que ser minúsculas, dígitos y guiones (p. ej. «caballo-pardo-01»)")
        if not os.path.isfile(stl_path):
            raise HorseError(f"no existe el STL del caballo: {stl_path}")
        spec = HorseSpec(archivo=MODEL_FILE, sha256=sha256_file(stl_path), escala=escala, yaw=yaw, ref_tipo=ref_tipo, ref_mm=ref_mm)
        vs = self.versions(hid)
        version = (vs[-1] + 1) if vs else 1
        if vs:
            last = self._read(hid, vs[-1])
            if last.spec == spec and last.nombre == " ".join(nombre.split()):
                return last
            if not new_version:
                raise HorseError(f"ya existe el caballo «{hid}» (última versión {vs[-1]}): las versiones no se sobrescriben; "
                                 "agregá una versión nueva")
        meta = HorseMeta(id=hid, version=version, spec=spec, nombre=" ".join(nombre.split()))
        os.makedirs(os.path.join(self.dir, hid), exist_ok=True)
        tmp = tempfile.mkdtemp(prefix=".nuevo-", dir=os.path.join(self.dir, hid))
        try:
            shutil.copyfile(stl_path, os.path.join(tmp, MODEL_FILE))
            load_horse_mesh(spec, base_dir=tmp, repair=repair)                 # levanta HorseError si no sirve
            with open(os.path.join(tmp, META_FILE), "w", encoding="utf-8") as fh:
                json.dump(meta.to_dict(), fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            dest = self._vdir(hid, version)
            if os.path.exists(dest):
                raise HorseError(f"«{meta.ref}» ya existe en la biblioteca")
            os.rename(tmp, dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return meta

    # ------------------------------------------------------------------ verificación
    def verify(self, ref: str | None = None, deep: bool = False) -> dict:
        """Revisa la biblioteca (o un caballo): ficha legible y en su carpeta, STL presente y con el SHA-256 de la ficha; con
        `deep` además lo carga. Devuelve `{ref: [problemas]}` solo con los que tienen problemas (vacío = todo bien)."""
        if ref is not None:
            m = self.meta(ref)
            targets = [(m.id, m.version)]
        else:
            targets = []
            if os.path.isdir(self.dir):
                for hid in sorted(os.listdir(self.dir)):
                    if _REF_RE.match(hid):
                        targets += [(hid, v) for v in self.versions(hid)]
        bad: dict = {}
        for hid, v in targets:
            probs = []
            try:
                m = self._read(hid, v)
                stl = os.path.join(self._vdir(hid, v), m.spec.archivo)
                if not os.path.isfile(stl):
                    probs.append(f"falta el STL {m.spec.archivo}")
                elif sha256_file(stl) != m.spec.sha256:
                    probs.append("el SHA-256 del STL no coincide con el de la ficha: el archivo cambió")
                elif deep:
                    load_horse_mesh(m.spec, base_dir=self._vdir(hid, v))
            except HorseError as exc:
                probs.append(str(exc))
            if probs:
                bad[f"{hid}@{v}"] = probs
        return bad
