"""`afm catalogo paquete validar` (plan de catálogos en línea, D04): lint `CK…` (§6) sobre un paquete `.zip`.

`validate_package` abre el zip con el lector endurecido (`ziplector`, D08), revisa el manifiesto (`Paquete.issues`), el contenido contra
`archivos` (CK201/CK202), la coherencia de color, referencias y línea, y por último extrae a una carpeta temporal y corre `Catalog.verify`
(CK203). Cada hallazgo trae el archivo y qué corregir. Con `estricto`, los avisos cuentan como errores (así lo usa la puerta de releases
del índice). No modifica el zip ni ningún catálogo.

Paquetes por referencia (D26): una pieza sin STL cuyo `sha256` es el de una `referencias_requeridas` no es un error de CK203 (se espera que la
persona aporte el archivo); el archivo en sí dentro del zip sí lo es (CK304).

Pendiente del plan: CK305 (licencias distintas por pieza sin CREDITOS.md) no se evalúa porque `PieceMeta` no tiene licencia por pieza todavía.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import tempfile
import zipfile
from dataclasses import dataclass, field

from . import parts as P
from . import paquete as Q
from . import ziplector as Z
from .catalog import Catalog, CatalogError

ERROR, WARNING = "error", "aviso"
COVER_FILE = "previews/portada.png"
HEIGHT_TOL_MM = 0.5                                  # CK104 (§6)

# `Catalog.verify` ya detecta estas dos y el validador las reporta con su código propio: se descartan de CK203 para no duplicarlas
_VERIFY_AS_OTHER = ("falta el FBX de color", "paleta de la línea no tiene", "usa roles que la paleta")
_GAP_WARNING = "versiones no consecutivas"       # aviso de `Catalog.verify` tras `delete_pieces`; no impide compartir el paquete


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    path: str
    message: str
    fix: str = ""

    def __str__(self):
        tag = "ERROR" if self.severity == ERROR else "AVISO"
        return f"{tag} {self.code or '----'} {self.path}: {self.message}" + (f" → {self.fix}" if self.fix else "")


@dataclass
class ValidationResult:
    path: str
    paquete: Q.Paquete | None = None
    findings: list[Finding] = field(default_factory=list)
    estricto: bool = False

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR or self.estricto]

    @property
    def warnings(self) -> list[Finding]:
        return [] if self.estricto else [f for f in self.findings if f.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors


# ---------------------------------------------------------------------------------------------------------------- piezas

_MISSING_KEY_CODE = {"licencia": "CK101", "descripcion": "CK102", "origen": "CK102", "version": "CK103", "linea": "CK104", "categorias": "CK106"}
_MANIFEST_FIX = {
    "CK101": "agregá `licencia` en paquete.json (p. ej. CC-BY-4.0)",
    "CK102": "completá `descripcion` y `origen` en paquete.json",
    "CK103": "usá una versión X.Y.Z en `version`",
    "CK104": "revisá `linea` en paquete.json",
    "CK106": f"elegí de 1 a {Q.CATEGORIES_MAX} de: {', '.join(Q.CATEGORIES)}",
    "CK107": "`origen: referencia` y `referencias_requeridas` van juntos",
    "CK201": "el sha256 de `archivos` tiene que ser hex de 64 caracteres",
    "CK302": "usá rutas relativas dentro del paquete",
    "CK402": "cada pin: `id`, `github` (owner/repo), `version` exacta y `sha256`",
    "CK403": "una colección solo lleva `pines`",
}


def _piece_metas(zf: zipfile.ZipFile, names: list[str], findings: list[Finding]) -> list[tuple[str, P.PieceMeta]]:
    out = []
    for n in names:
        if not (n.startswith("piezas/") and n.endswith(".pieza.json")):
            continue
        try:
            d = json.loads(zf.read(n).decode("utf-8"))
            out.append((n, P.PieceMeta.from_dict(d)))
        except (ValueError, KeyError, TypeError, P.PieceError) as exc:
            findings.append(Finding("CK203", ERROR, n, f"la ficha no se puede leer: {exc}", "volvé a exportar la pieza"))
    return out


def validate_package(path: str, *, estricto: bool = False, allow_fbx: bool = False, verify_catalog: bool = True) -> ValidationResult:
    """Valida el `.zip` `path`. Nunca lanza por un paquete malo: todo sale como hallazgo (solo `ZipError` si ni siquiera es un zip)."""
    res = ValidationResult(path=path, estricto=estricto)
    fs = res.findings

    def add(code, sev, p, msg, fix=""):
        fs.append(Finding(code, sev, p, msg, fix))

    with Z.open_zip(path) as zf:
        insp = Z.inspect(zf, allow_fbx=allow_fbx)
        for f in insp.findings:
            add(f.code, ERROR, f.path, f.message, f.fix)
        if insp.findings:
            return res                                           # extensión, ruta, enlace o tamaño malos: no se abre nada más (nada llega al disco)
        names = sorted(e.name for e in insp.entries)

        # ---- manifiesto
        if Q.MANIFEST_FILE not in names:
            add("CK102", ERROR, Q.MANIFEST_FILE, "falta el manifiesto del paquete", "exportá con `afm catalogo exportar` o creá paquete.json")
            return res
        try:
            pkg = Q.Paquete.from_json(zf.read(Q.MANIFEST_FILE).decode("utf-8"))
        except Q.PaqueteFormatoFuturo as exc:
            add("", ERROR, Q.MANIFEST_FILE, str(exc), "actualizá afm")
            return res
        except (Q.PaqueteError, UnicodeDecodeError, ValueError) as exc:
            key = re.search(r"falta «(\w+)»", str(exc))                      # clave obligatoria ausente: lleva el código de su regla
            code = _MISSING_KEY_CODE.get(key.group(1), "") if key else ""
            add(code, ERROR, Q.MANIFEST_FILE, f"no se puede leer: {exc}", _MANIFEST_FIX.get(code, "corregí el JSON o volvé a exportar"))
            return res
        res.paquete = pkg
        for w in pkg.avisos:
            add("", WARNING, Q.MANIFEST_FILE, w)
        for i in pkg.issues():
            add(i.code, ERROR, f"{Q.MANIFEST_FILE}:{i.field}", i.message, _MANIFEST_FIX.get(i.code, "corregí el campo en paquete.json"))

        # ---- CK201 / CK202: lo declarado contra lo que hay
        listed = set(names) - {Q.MANIFEST_FILE}
        for n in sorted(listed - set(pkg.archivos)):
            add("CK202", ERROR, n, "está en el zip pero no figura en `archivos` de paquete.json", "volvé a exportar o declaralo con su sha256")
        for n in sorted(set(pkg.archivos) - listed):
            add("CK202", ERROR, n, "figura en `archivos` pero no está en el zip", "agregá el archivo o sacalo de `archivos`")
        sha_of: dict[str, str] = {}
        for n in sorted(listed):
            h = hashlib.sha256()
            with zf.open(n) as src:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    h.update(chunk)
            sha_of[n] = h.hexdigest()
            want = pkg.archivos.get(n)
            if want and want != sha_of[n]:
                add("CK201", ERROR, n, "el sha256 no coincide con el de `archivos` (el archivo cambió después de armar el paquete)", "volvé a exportar el paquete")

        # ---- README / portada
        for need in ("README.md", "LICENSE"):
            if need not in listed:
                add("CK102", ERROR, need, "falta en el paquete", "pasá --readme / --licencia-archivo o volvé a exportar")
        if pkg.tipo != "coleccion" and COVER_FILE not in listed:
            add("CK105", WARNING, COVER_FILE, "el paquete no trae portada", "pasá --portada IMAGEN (1280×720) al exportar")

        # ---- CK304: archivos de una referencia requerida empaquetados
        banned = {s: r.id for r in pkg.referencias_requeridas for s in r.sha256}
        for n, s in sha_of.items():
            if s in banned:
                add("CK304", ERROR, n, f"es el archivo de la referencia requerida «{banned[s]}» (no se distribuye en el paquete)", "sacalo del paquete")

        # ---- CK306: dependencias sin pista de repo
        for i, text in enumerate(pkg.requiere):
            try:
                dep = Q.parse_dependency(text)
            except Q.PaqueteError:
                continue
            if not dep.github:
                add("CK306", WARNING, f"{Q.MANIFEST_FILE}:requiere[{i}]", f"«{dep.id}» no trae pista de repo (id@rango#owner/repo)",
                    "agregá `#owner/repo` para que el instalador lo encuentre fuera del índice")

        # ---- fichas: CK307 (FBX ausente), CK204 (roles sin paleta)
        metas = _piece_metas(zf, names, fs)
        used_hashes = {m.sha256 for _n, m in metas if m.sha256}            # D26: el sha256 de la ficha es el del archivo que completa la pieza
        for i, r in enumerate(pkg.referencias_requeridas):
            if not used_hashes & set(r.sha256):
                add("", WARNING, f"{Q.MANIFEST_FILE}:referencias_requeridas[{i}]",
                    f"ninguna pieza del paquete tiene el sha256 de «{r.id}»: el archivo no completaría nada",
                    "el sha256 de la ficha de la pieza tiene que estar entre los de la referencia")
        palette_ids: set[str] | None = None
        if "paleta.json" in listed:
            try:
                from .palette import Palette
                palette_ids = set(Palette.from_dict(json.loads(zf.read("paleta.json").decode("utf-8"))).ids())
            except Exception as exc:                              # paleta ilegible
                add("CK204", ERROR, "paleta.json", f"no se puede leer: {exc}", "corregí o volvé a exportar la paleta")
        for n, m in metas:
            folder = posixpath.dirname(n)
            color = m.color or {}
            if color and P.color_mode(color) == "rgb":
                fbx = posixpath.join(folder, posixpath.basename(color.get("fuente") or P.color_name(m.id, m.version)))
                if fbx not in listed:
                    add("CK307", ERROR, n, f"declara el bloque `color` con el FBX «{posixpath.basename(fbx)}» y el paquete no lo trae",
                        "exportá sin ese color (el exportador quita el bloque solo) o usá --incluir-fbx")
            used = list(dict.fromkeys((color.get("roles") or []) + ((color.get("retoque") or {}).get("roles") or [])))
            if used and palette_ids is not None:
                miss = [r for r in used if r not in palette_ids]
                if miss:
                    add("CK204", ERROR, n, f"usa roles que no están en paleta.json: {', '.join(miss)}", "agregá los roles a la paleta del paquete")
            elif used and "paleta.json" not in listed:
                add("CK204", ERROR, n, f"usa roles de color ({', '.join(used)}) y el paquete no trae paleta.json", "exportá incluyendo la paleta")

        # ---- CK104: altura de la línea contra linea.json
        if pkg.linea and "linea.json" in listed:
            try:
                ld = json.loads(zf.read("linea.json").decode("utf-8"))
                h = float(ld.get("altura_mm"))
                if abs(h - pkg.linea["altura_mm"]) > HEIGHT_TOL_MM:
                    add("CK104", ERROR, f"{Q.MANIFEST_FILE}:linea.altura_mm", f"dice {pkg.linea['altura_mm']:g} mm y linea.json dice {h:g} mm (tolerancia {HEIGHT_TOL_MM} mm)",
                        "corregí la altura en paquete.json")
                if ld.get("nombre") != pkg.linea["nombre"]:
                    add("CK104", ERROR, f"{Q.MANIFEST_FILE}:linea.nombre", f"dice «{pkg.linea['nombre']}» y linea.json «{ld.get('nombre')}»", "corregí el nombre de la línea")
            except (ValueError, TypeError) as exc:
                add("CK104", ERROR, "linea.json", f"no se puede leer: {exc}", "volvé a exportar")
        elif pkg.tipo != "coleccion" and pkg.contenido.piezas + pkg.contenido.recetas and "linea.json" not in listed:
            add("CK104", ERROR, "linea.json", "falta linea.json", "volvé a exportar el paquete")

        # ---- CK203: el contenido abre como catálogo y pasa `Catalog.verify`
        structural = any(f.severity == ERROR and f.code in ("CK201", "CK202") for f in fs)
        if verify_catalog and pkg.tipo != "coleccion" and "linea.json" in listed and not structural:
            with tempfile.TemporaryDirectory(prefix="afm-validar-") as tmp:
                try:
                    Z.extract_all(zf, tmp, insp, only={n for n in names if n != Q.MANIFEST_FILE})
                    rep = Catalog.open(tmp).verify(extra_refs={h: r.nombre for r in pkg.referencias_requeridas for h in r.sha256})
                except (Z.ZipError, CatalogError, OSError, ValueError) as exc:
                    add("CK203", ERROR, "linea.json", f"el contenido no abre como catálogo: {exc}", "volvé a exportar el paquete")
                else:
                    for p in rep.problems:
                        if not any(k in p for k in _VERIFY_AS_OTHER):
                            tag, _, text = p.partition(": ")
                            add("CK203", ERROR, tag, text or p, "corregí la pieza en el catálogo de origen y volvé a exportar")
                    for w in rep.warnings:
                        if _GAP_WARNING in w or ": incompleta: falta el archivo requerido" in w:   # hueco de versiones a propósito; D26: lo esperable en un paquete por referencia
                            continue
                        tag, _, text = w.partition(": ")
                        add("CK203", WARNING, tag, text or w)
    return res


__all__ = ["Finding", "ValidationResult", "validate_package", "ERROR", "WARNING"]
