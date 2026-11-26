"""Herramientas de autor de paquetes (plan de catálogos en línea, D09): `paquete init`, `paquete github` y `paquete empaquetar`.

El autor trabaja en una **carpeta de paquete** (un repo de GitHub): es el contenido de un paquete `afm-paquete-1` ya descomprimido,
más lo que sirve para publicarlo:

    paquete.json  README.md  LICENSE  CREDITOS.md  .gitignore
    .github/workflows/publicar-paquete.yml     # valida en estricto, arma <id>-<version>.zip y lo publica en un Release
    linea.json  paleta.json  piezas/…  figuras/…  previews/portada.png     # lo que aporta el autor (p. ej. un zip exportado)

`pack_dir` arma el `.zip` desde esa carpeta con el mismo formato y el mismo orden que `exportar.export_package` (mismo contenido ⇒ mismo
zip): recalcula `archivos` (sha256 de cada archivo) y `contenido` (piezas, recetas y paleta que de verdad hay), de modo que el autor no
mantiene a mano lo que se deduce de los archivos. Lo que no se deduce (licencia, origen, autores, descripción) lo escribe el autor:
cada persona responde por su contenido. Nada de acá toca un catálogo ni la red; el Release lo publica el workflow, no `afm`.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from . import __version__
from . import parts as P
from . import paquete as Q
from .catalog import CatalogError, parse_ref
from .exportar import (CREDITS_FILE, LICENSE_FILE, README_FILE, ExportError, ExportResult, _entry_from_data, _entry_from_path,
                       default_license_text, parse_author, write_zip)
from .paquete import MANIFEST_FILE, Author, Contents, Paquete, PaqueteError

WORKFLOW_PATH = ".github/workflows/publicar-paquete.yml"
GITIGNORE = ".gitignore"
DIST_DIR = "dist"                                   # salida del workflow: nunca entra al zip
AFM_REPO_URL = "https://github.com/fschpp/action_figure_maker_stl.git"

# perfil -> (tipo del manifiesto, categorías por defecto). El perfil es una comodidad de `init`; el tipo es lo que queda en el manifiesto.
PROFILES = {
    "heroe": ("heroe", ["heroes"]),
    "piezas": ("piezas", ["otros"]),
    "linea": ("linea", ["lineas"]),
    "coleccion": ("coleccion", ["colecciones"]),
}

_PIECE_RE = re.compile(r"^piezas/[^/]+/([a-z0-9]+(?:-[a-z0-9]+)*@[1-9]\d*)\.pieza\.json$")
_RECIPE_RE = re.compile(r"^figuras/([^/]+)\.receta\.json$")


class AutorError(CatalogError):
    """No se puede crear, completar o empaquetar la carpeta del paquete; el mensaje dice qué corregir."""


@dataclass
class InitResult:
    dir: str
    paquete: Paquete
    archivos: list                                   # rutas relativas escritas, en orden
    pendientes: list = field(default_factory=list)   # qué le falta al autor para que `validar` pase


# ---------------------------------------------------------------------------------------------------- workflow

def workflow_text() -> str:
    """`.github/workflows/publicar-paquete.yml`: en cada push a `main` valida (estricto), arma `<id>-<version>.zip` y publica un Release.

    La versión sale de `paquete.json`, o de `[release X.Y.Z]` en el mensaje del último commit. Un Release que ya existe no se pisa:
    se omite el paso (así un push sin cambio de versión no falla). El mensaje del commit se lee con `git log` dentro del script, nunca
    interpolado en el YAML, y la versión llega por variables de entorno.
    """
    return f'''# Generado por `afm catalogo paquete init` / `afm catalogo paquete github`.
# En cada push a main: valida el paquete en modo estricto, arma <id>-<version>.zip y lo publica en un Release de este repo.
# El índice de catálogos toma el Release solo: no hace falta abrir un PR por cada versión nueva.
# Versión: la de paquete.json, o la de `[release X.Y.Z]` si el mensaje del último commit la trae.
name: Publicar paquete

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: write

env:
  AFM_REF: main    # rama, etiqueta o commit de afm que arma y valida el paquete (fijá una etiqueta para armados reproducibles)

jobs:
  publicar:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Instalar afm
        run: pip install "git+{AFM_REPO_URL}@${{AFM_REF}}"

      - name: Versión a publicar
        id: ver
        run: |
          ID=$(python -c "import json; print(json.load(open('paquete.json', encoding='utf-8'))['id'])")
          VER=$(python -c "import json; print(json.load(open('paquete.json', encoding='utf-8'))['version'])")
          PEDIDA=$(git log -1 --pretty=%B | sed -n 's/.*\\[release \\([0-9][0-9A-Za-z.+-]*\\)\\].*/\\1/p' | head -n 1)
          if [ -n "$PEDIDA" ]; then VER="$PEDIDA"; fi
          echo "id=$ID" >> "$GITHUB_OUTPUT"
          echo "version=$VER" >> "$GITHUB_OUTPUT"

      - name: Armar el paquete
        env:
          ID: ${{{{ steps.ver.outputs.id }}}}
          VER: ${{{{ steps.ver.outputs.version }}}}
        run: afm catalogo paquete empaquetar . --version-paquete "$VER" -o "{DIST_DIR}/$ID-$VER.zip"

      - name: Validar (estricto)
        env:
          ID: ${{{{ steps.ver.outputs.id }}}}
          VER: ${{{{ steps.ver.outputs.version }}}}
        run: afm catalogo paquete validar "{DIST_DIR}/$ID-$VER.zip" --estricto

      - name: Publicar el Release
        env:
          GH_TOKEN: ${{{{ github.token }}}}
          ID: ${{{{ steps.ver.outputs.id }}}}
          VER: ${{{{ steps.ver.outputs.version }}}}
        run: |
          TAG="v$VER"
          if gh release view "$TAG" > /dev/null 2>&1; then
            echo "El Release $TAG ya existe y no se sobrescribe: subí la versión en paquete.json o escribí [release X.Y.Z] en el commit."
            exit 0
          fi
          EXTRA=""
          case "$VER" in *-*) EXTRA="--prerelease" ;; esac
          gh release create "$TAG" "{DIST_DIR}/$ID-$VER.zip" --title "$ID $VER" --notes "Paquete $ID $VER" --target "$GITHUB_SHA" $EXTRA
'''


# ---------------------------------------------------------------------------------------------------- init

def _title(pid: str) -> str:
    return " ".join(w for w in pid.split("-") if w).capitalize()


def _readme(p: Paquete) -> str:
    L = [f"# {p.nombre}", ""]
    if p.descripcion:
        L += [p.descripcion, ""]
    if p.linea and p.linea.get("nombre"):
        L.append(f"- **Línea:** {p.linea['nombre']}" + (f" ({p.linea['altura_mm']:g} mm)" if p.linea.get("altura_mm", 0) > 0 else ""))
    L.append("- **Autores:** " + ", ".join(a.nombre + (f" (@{a.github})" if a.github else "") for a in p.autores))
    L += [f"- **Licencia:** {p.licencia}", f"- **Origen:** {p.origen}", "",
          "Se instala con `afm catalogo instalar paquete.zip` (o desde «Compartir → Importar paquete .zip…» en la app).", ""]
    return "\n".join(L)


def _credits(p: Paquete) -> str:
    L = ["# Créditos", ""]
    L += [f"- {a.nombre}" + (f" (https://github.com/{a.github})" if a.github else "") + f": autoría, {p.licencia}" for a in p.autores]
    L += ["", "Si el paquete mezcla piezas de más de una persona o licencia, listá acá quién hizo qué y con qué licencia.", ""]
    return "\n".join(L)


def init_package_dir(dir: str, *, id: str, perfil: str = "heroe", nombre: str = "", descripcion: str = "", version: str = "0.1.0",
                     autores=(), licencia: str = "", origen: str = "", github: str = "", linea: str = "", altura_mm: float = 0.0,
                     categorias=()) -> InitResult:
    """Crea la carpeta de un paquete nuevo (ver el docstring del módulo). `dir` no puede existir con contenido.

    `licencia`, `origen` y al menos un autor (o `github`, de donde sale el autor) son obligatorios: los da el autor. La línea
    (`linea`, `altura_mm`) puede quedar vacía: `pack_dir` la toma de `linea.json` cuando el autor lo agrega. `descripcion` vacía queda
    como pendiente (la escribe el autor). Devuelve lo que falta para que `validar` pase en `pendientes`.
    """
    if perfil not in PROFILES:
        raise AutorError(f"perfil desconocido «{perfil}» (hay {', '.join(PROFILES)})")
    if os.path.exists(dir) and (not os.path.isdir(dir) or os.listdir(dir)):
        raise AutorError(f"«{dir}» ya existe y no está vacía: elegí una carpeta nueva")
    tipo, default_cats = PROFILES[perfil]
    authors = [a if isinstance(a, Author) else parse_author(a) for a in autores]
    if not authors and github and "/" in github:
        authors = [Author(github.split("/", 1)[0], github.split("/", 1)[0])]
    miss = [n for n, v in (("--licencia", licencia), ("--origen", origen), ("--autor (o --github)", authors)) if not v]
    if miss:
        raise AutorError("faltan datos que solo el autor puede dar: " + ", ".join(miss) + " (cada persona responde por su contenido)")
    p = Paquete(id=id, nombre=nombre or _title(id), version=version, descripcion=descripcion.strip(), tipo=tipo, autores=authors,
                licencia=licencia, origen=origen, github=github,
                linea=None if tipo == "coleccion" else {"nombre": linea, "altura_mm": float(altura_mm)},
                motor=f">={__version__}", categorias=list(categorias) or list(default_cats))
    bad = [i for i in p.issues() if i.field.split("[")[0].split(".")[0] in ("id", "nombre", "version", "tipo", "licencia", "origen",
                                                                          "github", "autores", "categorias")]
    if bad:
        raise AutorError("los datos del paquete no son válidos:\n  " + "\n  ".join(str(i) for i in bad))
    files = {
        MANIFEST_FILE: p.to_json(),
        README_FILE: _readme(p),
        LICENSE_FILE: default_license_text(p.licencia),
        CREDITS_FILE: _credits(p),
        GITIGNORE: f"{DIST_DIR}/\n",
        WORKFLOW_PATH: workflow_text(),
    }
    try:
        for rel, text in files.items():
            path = os.path.join(dir, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
    except OSError as exc:
        raise AutorError(f"no se pudo escribir en «{dir}»: {exc}") from None
    pend = [str(i) for i in p.issues() if not i.code == "CK104"]
    if tipo == "coleccion":
        pend.append("agregá los pines de las versiones que fija la colección en `contenido.pines` de paquete.json")
    else:
        pend.append("traé el contenido: `linea.json`, `piezas/`, `figuras/` y `paleta.json` de tu catálogo "
                    "(`afm catalogo exportar … -o p.zip` y descomprimí acá lo que no sea paquete.json, README.md ni LICENSE)")
        if not (linea and altura_mm > 0):
            pend.append("la línea y su altura se toman de `linea.json` al empaquetar (o escribilas en `linea` de paquete.json)")
    pend.append("agregá `previews/portada.png` (1280×720): el workflow valida en modo estricto y sin portada no publica")
    return InitResult(dir=dir, paquete=p, archivos=list(files), pendientes=pend)


# ---------------------------------------------------------------------------------------------------- github

def _read_manifest_raw(dir: str) -> tuple[str, dict]:
    path = os.path.join(dir, MANIFEST_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        raise AutorError(f"«{dir}» no tiene {MANIFEST_FILE}: creala con `afm catalogo paquete init`") from None
    except ValueError as exc:
        raise AutorError(f"{path} no es JSON válido: {exc}") from None
    if not isinstance(data, dict):
        raise AutorError(f"{path}: el manifiesto tiene que ser un objeto JSON")
    return path, data


def set_github(dir: str, repo: str, *, overwrite_workflow: bool = False) -> tuple[bool, bool]:
    """Fija `github` en `paquete.json` y deja el workflow de Release (equivale a `set-github` + `add-release-workflow` de modkit).

    Se edita el JSON tal cual está: no se pierde ninguna clave (ni las que esta versión de afm no conoce) y `github` queda después de
    `origen`. Un workflow que ya existe y difiere del generado no se pisa salvo `overwrite_workflow`. Devuelve
    `(github_cambio, workflow_escrito)`.
    """
    if not Q._REPO_RE.match(repo or ""):
        raise AutorError(f"«{repo}» no es «owner/repo»")
    path, data = _read_manifest_raw(dir)
    changed = data.get("github") != repo
    if changed:
        out: dict = {}
        for k, v in data.items():
            if k != "github":
                out[k] = v
            if k == "origen":
                out["github"] = repo
        out.setdefault("github", repo)              # sin «origen» (manifiesto incompleto): al final
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise AutorError(f"no se pudo escribir {path}: {exc}") from None
    wf = os.path.join(dir, *WORKFLOW_PATH.split("/"))
    text = workflow_text()
    wrote = False
    if os.path.exists(wf):
        with open(wf, encoding="utf-8") as f:
            current = f.read()
        if current != text and not overwrite_workflow:
            raise AutorError(f"{WORKFLOW_PATH} ya existe y tiene cambios: usá --sobrescribir-workflow para reemplazarlo")
        wrote = current != text
    else:
        wrote = True
    if wrote:
        os.makedirs(os.path.dirname(wf), exist_ok=True)
        with open(wf, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return changed, wrote


# ---------------------------------------------------------------------------------------------------- empaquetar

def collect_files(root: str, skip: str = "") -> dict[str, str]:
    """`{ruta dentro del zip: ruta en disco}` de la carpeta del paquete. Quedan afuera lo oculto (`.git`, `.github`, `.gitignore`…),
    `dist/` de la raíz y `skip`; un enlace simbólico es un error (el zip no los admite)."""
    out: dict[str, str] = {}
    skip_abs = os.path.abspath(skip) if skip else ""
    for base, dirs, names in os.walk(root):
        rel_base = os.path.relpath(base, root)
        keep = []
        for d in sorted(dirs):
            if d.startswith(".") or (rel_base == "." and d == DIST_DIR):
                continue
            if os.path.islink(os.path.join(base, d)):
                raise AutorError(f"«{d}» es un enlace simbólico: el paquete no admite enlaces")
            keep.append(d)
        dirs[:] = keep
        for n in sorted(names):
            path = os.path.join(base, n)
            if n.startswith(".") or os.path.abspath(path) == skip_abs:
                continue
            if os.path.islink(path):
                raise AutorError(f"«{n}» es un enlace simbólico: el paquete no admite enlaces")
            out[n if rel_base == "." else rel_base.replace(os.sep, "/") + "/" + n] = path
    return out


def _derive_contents(p: Paquete, files: dict[str, str]) -> Contents:
    refs = sorted({m.group(1) for a in files if (m := _PIECE_RE.match(a))}, key=lambda r: (parse_ref(r)[0], parse_ref(r)[1]))
    recipes = sorted({m.group(1) for a in files if (m := _RECIPE_RE.match(a))})
    return Contents(piezas=refs, recetas=recipes, paleta="paleta.json" in files)


def pack_dir(dir: str, out: str, *, version: str = "", overwrite: bool = False, check_manifest: bool = True) -> ExportResult:
    """Arma `out` (un `.zip`) con la carpeta del paquete. Recalcula `archivos` y, salvo en colecciones, `contenido` (y completa `linea`
    desde `linea.json` si el manifiesto la dejó vacía). `version` reemplaza la del manifiesto solo en el zip (así el workflow publica
    `[release X.Y.Z]` sin tocar el repo). Con `check_manifest` un manifiesto inválido es un error; `validar` lo apaga para que el lint
    cuente cada hallazgo con su código. El contenido no se abre: lo revisa `validar`."""
    if not out.lower().endswith(".zip"):
        raise AutorError(f"el paquete tiene que terminar en .zip (se pidió «{out}»)")
    if os.path.exists(out) and not overwrite:
        raise AutorError(f"«{out}» ya existe (usá --sobrescribir)")
    if not os.path.isdir(dir):
        raise AutorError(f"«{dir}» no es una carpeta")
    _path, raw = _read_manifest_raw(dir)
    try:
        p = Paquete.from_dict(raw)
    except PaqueteError as exc:
        raise AutorError(f"{MANIFEST_FILE}: {exc}") from None
    files = collect_files(dir, skip=out)
    files.pop(MANIFEST_FILE, None)
    avisos = [f"{MANIFEST_FILE}: {w}" for w in p.avisos]
    if version:
        p.version = version
    if p.tipo != "coleccion":
        derived = _derive_contents(p, files)
        if (derived.piezas, derived.recetas, derived.paleta) != (p.contenido.piezas, p.contenido.recetas, p.contenido.paleta):
            avisos.append(f"el contenido de {MANIFEST_FILE} se actualizó según los archivos: {len(derived.piezas)} pieza(s), "
                          f"{len(derived.recetas)} receta(s), paleta: {'sí' if derived.paleta else 'no'}")
        p.contenido = derived
        if "linea.json" in files and not (p.linea and p.linea.get("nombre") and p.linea.get("altura_mm", 0) > 0):
            try:
                with open(files["linea.json"], encoding="utf-8") as f:
                    ld = json.load(f)
                cur = p.linea or {}
                p.linea = {"nombre": cur.get("nombre") or ld["nombre"],
                           "altura_mm": cur["altura_mm"] if cur.get("altura_mm", 0) > 0 else float(ld["altura_mm"])}
            except (OSError, ValueError, KeyError, TypeError):
                pass                                # linea.json roto: lo dice `validar` (CK104/CK203), no se esconde acá
    entries = {a: _entry_from_path(a, path) for a, path in sorted(files.items())}
    p.archivos = {a: e.sha256 for a, e in entries.items()}
    if check_manifest:
        problems = p.problems()
        if problems:
            raise AutorError(f"el manifiesto del paquete no es válido:\n  " + "\n  ".join(problems))
    manifest = _entry_from_data(MANIFEST_FILE, p.to_json().encode("utf-8"))
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        write_zip(out, [manifest] + [entries[a] for a in sorted(entries)])
    except ExportError as exc:
        raise AutorError(str(exc)) from None
    return ExportResult(path=out, paquete=p, archivos={a: e.size for a, e in sorted(entries.items())}, avisos=avisos,
                        sha256=P.sha256_file(out), tamano=os.path.getsize(out))


__all__ = ["AutorError", "InitResult", "PROFILES", "WORKFLOW_PATH", "collect_files", "init_package_dir", "pack_dir", "set_github",
           "workflow_text"]
