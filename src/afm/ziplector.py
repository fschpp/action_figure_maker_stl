"""Lector endurecido de paquetes `.zip` (plan de catálogos en línea, §5.7 y D08).

Es el **único** punto por el que un `.zip` ajeno entra a la app: lo usan `paquete validar` (D04), el instalador (D05) y el cliente remoto.
Nada se escribe fuera de la carpeta de *staging* que se le da a `extract_all`; antes de extraer, `inspect` revisa **todas** las entradas y
devuelve hallazgos con el código `CK…` del §6:

    CK301  extensión no permitida (lista cerrada), zip anidado, método de compresión raro
    CK302  ruta con `..`, absoluta, con «\\», con letra de unidad, carácter de control, enlace simbólico, cifrada, o duplicada por mayúsculas
    CK303  cantidad de archivos, tamaño por archivo, tamaño total descomprimido o razón de compresión sobre el tope

Módulo de motor puro: sin GTK, sin red, sin `manifold3d`. Los topes son constantes **[P]** (D01 fija los definitivos con la medición real).
"""
from __future__ import annotations

import os
import posixpath
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass, field

MAX_FILES = 2000                          # [P] proceres-127 completo: 116 archivos útiles
MAX_FILE_BYTES = 64 * 1024 * 1024         # [P] por archivo (igual que `exportar.FBX_MAX_BYTES`)
MAX_TOTAL_BYTES = 512 * 1024 * 1024       # [P] descomprimido total
MAX_RATIO = 200                           # [P] descomprimido / comprimido por archivo (los STL/NPZ reales no pasan de ~15)
RATIO_MIN_SIZE = 1024 * 1024              # la razón solo cuenta en archivos de más de 1 MB (uno chico y repetitivo comprime mucho sin ser bomba)
ALLOWED_EXT = (".json", ".md", ".txt", ".stl", ".npz", ".png", ".jpg")
FBX_EXT = ".fbx"                          # solo con `allow_fbx` (el paquete lo trae por `--incluir-fbx`)
ALLOWED_NO_EXT = ("LICENSE",)             # archivos sin extensión que el formato sí lleva (en la raíz)
ALLOWED_METHODS = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
_BAD_PATH = re.compile(r"(^/|^[A-Za-z]:|\\|(^|/)\.\.?(/|$)|//|[\x00-\x1f\x7f])")


class ZipError(ValueError):
    """El `.zip` no se puede abrir o no es un zip."""


@dataclass(frozen=True)
class ZipFinding:
    code: str
    path: str
    message: str
    fix: str = ""

    def __str__(self):
        return f"{self.code} {self.path}: {self.message}" + (f" → {self.fix}" if self.fix else "")


@dataclass
class ZipEntry:
    name: str
    size: int
    compressed: int


@dataclass
class Inspection:
    entries: list[ZipEntry] = field(default_factory=list)      # solo archivos (sin carpetas)
    findings: list[ZipFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries)


def _fold(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def inspect(zf: zipfile.ZipFile, *, allow_fbx: bool = False, max_files: int = MAX_FILES, max_file_bytes: int = MAX_FILE_BYTES,
            max_total_bytes: int = MAX_TOTAL_BYTES, max_ratio: int = MAX_RATIO) -> Inspection:
    """Revisa todas las entradas sin descomprimir nada. Los tamaños son los **declarados**; `extract_all` vuelve a comprobarlos al escribir."""
    out = Inspection()
    seen: dict[str, str] = {}
    infos = zf.infolist()
    files = [i for i in infos if not i.filename.endswith("/")]
    if len(files) > max_files:
        out.findings.append(ZipFinding("CK303", "(zip)", f"trae {len(files)} archivos y el tope es {max_files}", "dividí el contenido en paquetes más chicos"))
    total = 0
    for zi in infos:
        name = zi.filename
        is_dir = name.endswith("/")
        bare = name.rstrip("/")
        if not bare or _BAD_PATH.search(bare) or bare.startswith("./"):
            out.findings.append(ZipFinding("CK302", name or "(vacío)", "ruta no permitida (absoluta, con «..», «\\», letra de unidad o caracteres de control)",
                                           "volvé a exportar el paquete con `afm catalogo exportar`"))
            continue
        mode = zi.external_attr >> 16
        if stat.S_ISLNK(mode):
            out.findings.append(ZipFinding("CK302", name, "es un enlace simbólico", "los paquetes no llevan enlaces: incluí el archivo real"))
            continue
        if zi.flag_bits & 0x1:
            out.findings.append(ZipFinding("CK302", name, "está cifrado", "exportá el paquete sin contraseña"))
            continue
        key = _fold(bare)
        if key in seen:
            out.findings.append(ZipFinding("CK302", name, f"duplicado de «{seen[key]}» (mismo nombre salvo mayúsculas o acentos compuestos)",
                                           "dejá un solo archivo por nombre"))
            continue
        seen[key] = name
        if is_dir:
            continue
        ext = posixpath.splitext(bare)[1].lower()
        base = posixpath.basename(bare)
        if ext == ".zip" or ext in (".jar", ".7z", ".rar", ".gz", ".tar"):
            out.findings.append(ZipFinding("CK301", name, "zip o archivo comprimido anidado", "sacalo del paquete"))
            continue
        if not (ext in ALLOWED_EXT or (ext == FBX_EXT and allow_fbx) or (not ext and base in ALLOWED_NO_EXT and "/" not in bare)):
            hint = " (el FBX solo viaja con --incluir-fbx)" if ext == FBX_EXT else ""
            out.findings.append(ZipFinding("CK301", name, f"extensión no permitida «{ext or base}»{hint}", f"solo se aceptan {', '.join(ALLOWED_EXT)}"))
            continue
        if zi.compress_type not in ALLOWED_METHODS:
            out.findings.append(ZipFinding("CK301", name, f"método de compresión no permitido ({zi.compress_type})", "usá almacenado o deflate"))
            continue
        if zi.file_size > max_file_bytes:
            out.findings.append(ZipFinding("CK303", name, f"pesa {zi.file_size / 1e6:.1f} MB y el tope por archivo es {max_file_bytes / 1e6:.0f} MB",
                                           "reducí la malla o sacá el archivo"))
        elif zi.file_size >= RATIO_MIN_SIZE and zi.compress_size and zi.file_size / zi.compress_size > max_ratio:
            out.findings.append(ZipFinding("CK303", name, f"razón de compresión {zi.file_size / zi.compress_size:.0f}:1 (posible bomba; el tope es {max_ratio}:1)",
                                           "no se acepta el archivo"))
        total += zi.file_size
        out.entries.append(ZipEntry(name, zi.file_size, zi.compress_size))
    if total > max_total_bytes:
        out.findings.append(ZipFinding("CK303", "(zip)", f"descomprimido pesa {total / 1e6:.0f} MB y el tope es {max_total_bytes / 1e6:.0f} MB",
                                       "dividí el contenido en paquetes más chicos"))
    return out


def open_zip(path: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ZipError(f"«{path}» no es un .zip legible: {exc}") from None


def extract_all(zf: zipfile.ZipFile, staging: str, inspection: Inspection, *, only=None) -> list[str]:
    """Extrae a `staging` (carpeta existente y vacía) los archivos de `inspection.entries` (o solo los de `only`). Se niega si la inspección
    trae hallazgos. Cada ruta se resuelve y tiene que quedar **dentro** de `staging`; se cuenta lo realmente escrito, no lo declarado, y
    se corta si un archivo supera su tamaño declarado o el tope. Devuelve las rutas escritas (relativas, con «/»)."""
    if inspection.findings:
        raise ZipError("el zip no pasó la inspección: " + "; ".join(str(f) for f in inspection.findings[:3]))
    base = os.path.realpath(staging)
    declared = {e.name: e for e in inspection.entries}
    wanted = [n for n in declared if only is None or n in only]
    written: list[str] = []
    total = 0
    for name in wanted:
        dest = os.path.realpath(os.path.join(base, *name.split("/")))
        if os.path.commonpath([base, dest]) != base or dest == base:
            raise ZipError(f"«{name}» saldría de la carpeta de instalación")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        limit = min(declared[name].size, MAX_FILE_BYTES)
        n = 0
        with zf.open(name) as src, open(dest, "xb") as dst:           # «x»: nunca pisa un archivo que ya está
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                n += len(chunk)
                total += len(chunk)
                if n > limit or total > MAX_TOTAL_BYTES:
                    raise ZipError(f"«{name}» pesa más de lo que declara el zip (¿bomba?)")
                dst.write(chunk)
        written.append(name)
    return written


__all__ = ["ZipError", "ZipFinding", "ZipEntry", "Inspection", "inspect", "open_zip", "extract_all", "MAX_FILES", "MAX_FILE_BYTES",
           "MAX_TOTAL_BYTES", "MAX_RATIO", "ALLOWED_EXT"]
