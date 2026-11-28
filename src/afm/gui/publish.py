"""Asistente «Publicar…» sin GTK (D21): de piezas o recetas del catálogo a un paquete listo para compartir, sin usar la terminal.

La ventana (`publish_window`) solo dibuja lo que sale de acá, así que todo el flujo se puede conducir sin ventana:

    flow = PublishFlow(root)                          # `root`: la carpeta del catálogo abierto
    form = flow.form                                  # lo que la persona completa (piezas, recetas, nombre, licencia, origen, créditos…)
    form.piezas, form.nombre = ["brazo-test-01"], "Brazo de prueba"; form.autores = ["Ana Pérez:ana"]; form.licencia, form.origen = "CC-BY-4.0", "propia"
    flow.problems()                                   # lo que falta, en español, ANTES de armar nada
    flow.task_validate().start().join()               # arma el paquete en una carpeta temporal y lo valida con `--estricto`: NO escribe en el catálogo
    flow.report                                       # errores y avisos legibles (con qué corregir), tamaño y sha256
    flow.export_zip(destino)                          # el .zip (solo si la validación no tiene errores)
    flow.prepare_repo(carpeta, "owner/repo")          # la carpeta del repo de GitHub con el workflow que publica el Release
    flow.prepare_alta(carpeta)                        # `<Autor>@<id>/` con `meta.json` y `descripcion.md` para pedir el alta en el índice

Nada de esto escribe en el catálogo: el paquete se arma en una carpeta temporal y recién se copia adonde la persona elige. Quien decide qué contenido
subir y con qué licencia es la persona: la app explica cada licencia y cada origen, pero **no los verifica** (plan §5.8).
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field

from .. import autor as A
from .. import exportar as X
from .. import indice as IX
from .. import paquete as Q
from .. import validar as V
from ..catalog import Catalog, CatalogError
from ..i18n import tr
from ..progress import TaskCancelled
from . import catalog_online as CO

INICIO, VALIDANDO, VALIDADO, FALLO = "inicio", "validando", "validado", "fallo"
TIPOS = ("piezas", "heroe")                       # lo que se arma desde un catálogo abierto (la línea completa se marca aparte; las colecciones son D34)
_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?$")


class PublishError(CatalogError):
    """No se puede publicar (falta algo, la validación tiene errores…); el catálogo no se toca nunca."""


# ---------------------------------------------------------------------------------------------------------------- licencias y orígenes en español

def license_help(spdx: str) -> str:
    """Qué permite una licencia, en español llano (y en el idioma de la interfaz): se arma con lo que dice su identificador."""
    if not spdx:
        return tr("publish.lic_elegi")
    if spdx == Q.ALL_RIGHTS_RESERVED:
        return tr("publish.lic_reservada")
    lines = []
    if spdx in ("CC0-1.0", "Unlicense"):
        lines.append(tr("publish.lic_dominio"))
    elif spdx.startswith("CC-"):
        lines.append(tr("publish.lic_cc_base"))
        if "-BY-" in spdx:
            lines.append(tr("publish.lic_credito"))
        if "-SA" in spdx:
            lines.append(tr("publish.lic_igual"))
        if "-ND" in spdx:
            lines.append(tr("publish.lic_sin_derivadas"))
        if "-NC" in spdx:
            lines.append(tr("publish.lic_no_comercial"))
        else:
            lines.append(tr("publish.lic_comercial"))
    else:
        lines.append(tr("publish.lic_software"))
    return " ".join(lines)


def origin_help(origen: str) -> str:
    """Qué quiere decir cada origen del contenido."""
    key = f"publish.origen_{origen.replace('-', '_')}"
    text = tr(key)
    return origen if text == key else text


def suggest_id(nombre: str) -> str:
    """«Brazo de Prueba» → `brazo-de-prueba` (minúsculas, números y guiones; recortado al máximo)."""
    s = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:Q.ID_MAX].strip("-")


def parse_authors(text: str) -> list:
    """Una persona por línea o separada por comas: `Ana Pérez` o `Ana Pérez:usuario-github`."""
    return [a.strip() for a in re.split(r"[\n,;]+", text or "") if a.strip()]


def _csv(text: str) -> list:
    return [x.strip() for x in re.split(r"[,\n]+", text or "") if x.strip()]


# ---------------------------------------------------------------------------------------------------------------- el formulario

@dataclass
class Form:
    """Todo lo que la persona completa. Los campos de texto aceptan lo que escribe (se limpian al usarlos)."""

    tipo: str = "piezas"                         # `piezas` o `heroe`
    piezas: list = field(default_factory=list)   # `id` (todas sus versiones) o `id@n`
    recetas: list = field(default_factory=list)  # nombre de la figura (`figuras/<nombre>.receta.json`)
    linea_completa: bool = False
    id: str = ""
    nombre: str = ""
    version: str = "1.0.0"
    descripcion: str = ""                        # corta (≤ 280 caracteres)
    autores: list = field(default_factory=list)  # `Nombre` o `Nombre:usuario-github`
    licencia: str = ""
    origen: str = ""
    creditos: str = ""                           # texto libre para CREDITOS.md
    github: str = ""                             # `owner/repo` (para el repo y el alta en el índice)
    categorias: list = field(default_factory=list)
    etiquetas: list = field(default_factory=list)
    readme: str = ""                             # ruta a un README.md propio ("" = el que arma afm)
    portada: str = ""                            # ruta a la imagen de portada ("" = sin portada)
    incluir_fbx: bool = False

    def effective_id(self) -> str:
        return (self.id or suggest_id(self.nombre)).strip()


@dataclass
class Problem:
    campo: str
    texto: str


@dataclass
class Issue:
    """Un hallazgo de la validación, para mostrar: qué pasó, dónde y qué corregir."""

    severidad: str                               # `error` | `aviso`
    codigo: str
    donde: str
    texto: str
    arreglo: str = ""


@dataclass
class Report:
    ok: bool
    errores: list
    avisos: list
    paquete: Q.Paquete | None = None
    tamano: int = 0
    sha256: str = ""
    resumen: str = ""                            # «Paquete «x» 1.0.0: 2 piezas, 1 receta, 18,2 MB»
    avisos_exportacion: list = field(default_factory=list)


# ---------------------------------------------------------------------------------------------------------------- el flujo

class PublishFlow:
    def __init__(self, root: str):
        self.root = root
        self.form = Form()
        self.stage = INICIO
        self.report: Report | None = None
        self.error = ""
        self._tmp: str | None = None             # carpeta temporal con el zip validado
        self._zip: str | None = None
        self._stamp: tuple | None = None         # el formulario con el que se validó (si cambia, hay que validar de nuevo)

    # ------------------------------------------------------------------------------------------------------------ qué hay para elegir
    def catalog(self) -> Catalog:
        return Catalog.open(self.root)

    def available_pieces(self) -> list:
        """`[(id, [versiones], tipo)]` ordenado por id: lo que se puede subir."""
        by: dict = {}
        for m in self.catalog().list(all_versions=True):
            by.setdefault(m.id, (m.tipo, []))[1].append(m.version)
        return [(pid, sorted(vs), tipo) for pid, (tipo, vs) in sorted(by.items())]

    def available_recipes(self) -> list:
        base = os.path.join(self.root, "figuras")
        try:
            names = sorted(n[:-len(".receta.json")] for n in os.listdir(base) if n.endswith(".receta.json"))
        except OSError:
            return []
        return names

    def line(self) -> tuple:
        """(nombre, altura_mm) de la línea del catálogo, o ("", None)."""
        try:
            cat = self.catalog()
            return cat.name, cat.height_mm
        except CatalogError:
            return "", None

    # ------------------------------------------------------------------------------------------------------------ lo que falta
    def problems(self) -> list:
        """Lo que hay que completar antes de armar el paquete, en español y sin códigos (la validación a fondo viene después)."""
        f = self.form
        out = []
        if not (f.piezas or f.recetas or f.linea_completa):
            out.append(Problem("contenido", tr("publish.falta_contenido")))
        if f.linea_completa and (f.piezas or f.recetas):
            out.append(Problem("contenido", tr("publish.linea_no_se_mezcla")))
        if not f.nombre.strip():
            out.append(Problem("nombre", tr("publish.falta_nombre")))
        elif len(f.nombre.strip()) > Q.NAME_MAX:
            out.append(Problem("nombre", tr("publish.nombre_largo", max=Q.NAME_MAX)))
        pid = f.effective_id()
        if not pid:
            out.append(Problem("id", tr("publish.falta_id")))
        elif not _ID_RE.match(pid) or len(pid) > Q.ID_MAX:
            out.append(Problem("id", tr("publish.id_invalido", max=Q.ID_MAX)))
        if not _VERSION_RE.match(f.version.strip()):
            out.append(Problem("version", tr("publish.version_invalida")))
        if len(f.descripcion) > Q.DESCRIPTION_MAX:
            out.append(Problem("descripcion", tr("publish.descripcion_larga", max=Q.DESCRIPTION_MAX)))
        if not [a for a in f.autores if a.strip()]:
            out.append(Problem("autores", tr("publish.falta_autor")))
        if not f.licencia:
            out.append(Problem("licencia", tr("publish.falta_licencia")))
        elif f.licencia not in Q.LICENSES:
            out.append(Problem("licencia", tr("publish.licencia_desconocida", licencia=f.licencia)))
        if not f.origen:
            out.append(Problem("origen", tr("publish.falta_origen")))
        elif f.origen not in Q.ORIGINS:
            out.append(Problem("origen", tr("publish.origen_desconocido", origen=f.origen)))
        if f.github and not Q._REPO_RE.match(f.github.strip()):
            out.append(Problem("github", tr("publish.github_invalido")))
        if f.portada and not os.path.isfile(f.portada):
            out.append(Problem("portada", tr("publish.portada_no_existe", ruta=f.portada)))
        if f.readme and not os.path.isfile(f.readme):
            out.append(Problem("readme", tr("publish.readme_no_existe", ruta=f.readme)))
        return out

    def warnings_before(self) -> list:
        """Avisos de criterio que no impiden publicar (para mostrar al lado del formulario)."""
        f, out = self.form, []
        if f.licencia and "-NC" in f.licencia:
            out.append(tr("publish.aviso_nc"))
        if f.origen == "derivada" and f.licencia in ("CC0-1.0", "Unlicense", "MIT"):
            out.append(tr("publish.aviso_derivada_libre"))
        if f.origen == "referencia":
            out.append(tr("publish.aviso_referencia"))
        if f.incluir_fbx:
            out.append(tr("publish.aviso_fbx"))
        return out

    # ------------------------------------------------------------------------------------------------------------ armar y validar
    def _export_args(self, out: str) -> dict:
        f = self.form
        return dict(piezas=list(f.piezas), recetas=list(f.recetas), linea_completa=f.linea_completa, incluir_fbx=f.incluir_fbx, id=f.effective_id(),
                    nombre=f.nombre.strip(), version=f.version.strip(), descripcion=f.descripcion.strip(), tipo=f.tipo if f.tipo in TIPOS else "piezas",
                    autores=[a.strip() for a in f.autores if a.strip()], licencia=f.licencia, origen=f.origen, github=f.github.strip(),
                    categorias=list(f.categorias), etiquetas=list(f.etiquetas), readme=f.readme, creditos=f.creditos, portada=f.portada, overwrite=True)

    def _signature(self) -> tuple:
        f = self.form
        return (self.root, tuple(f.piezas), tuple(f.recetas), f.linea_completa, f.incluir_fbx, f.effective_id(), f.nombre, f.version, f.descripcion, f.tipo,
                tuple(f.autores), f.licencia, f.origen, f.github, tuple(f.categorias), tuple(f.etiquetas), f.readme, f.creditos, f.portada)

    @property
    def validated(self) -> bool:
        """Hay un paquete armado y validado **con lo que hoy dice el formulario** y sin errores."""
        return self.report is not None and self.report.ok and self._stamp == self._signature() and self._zip is not None and os.path.isfile(self._zip)

    def _cleanup(self) -> None:
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
        self._tmp = self._zip = None

    def close(self) -> None:
        """Borra lo temporal. Se llama al cerrar el asistente."""
        self._cleanup()

    def task_validate(self, *, notify=None) -> CO.Task:
        """Arma el paquete en una carpeta temporal y lo valida con las reglas estrictas. **No escribe en el catálogo.** El resultado queda en `report`."""
        def work(task: CO.Task):
            self.stage, self.error, self.report = VALIDANDO, "", None
            self._cleanup()
            try:
                probs = self.problems()
                if probs:
                    raise PublishError("; ".join(p.texto for p in probs))
                task.progress(0, 0, tr("publish.armando"))
                self._tmp = tempfile.mkdtemp(prefix="afm-publicar-")
                out = os.path.join(self._tmp, f"{self.form.effective_id()}-{self.form.version.strip()}.zip")
                sig = self._signature()
                res = X.export_package(self.catalog(), out, **self._export_args(out))
                task.progress(0, 0, tr("publish.validando"))
                val = V.validate_package(out, estricto=True, allow_fbx=self.form.incluir_fbx)
            except TaskCancelled:
                self.stage = INICIO
                self._cleanup()
                raise
            except Exception as exc:                 # noqa: BLE001 — la ventana muestra el motivo; no se escribió nada en el catálogo
                self.stage, self.error = FALLO, str(exc)
                self._cleanup()
                raise
            self.report = self._report(res, val)
            self._zip, self._stamp = out, sig
            self.stage = VALIDADO if self.report.ok else FALLO
            if not self.report.ok:
                self.error = tr("publish.validacion_con_errores", n=len(self.report.errores))
            return self.report
        return CO.Task(work, notify=notify, name="afm-publicar")

    def validate(self) -> Report:
        """Lo mismo, sin hilo (para pruebas y para la línea de comandos)."""
        t = self.task_validate()
        t.start().join()
        if t.error is not None and not isinstance(t.error, PublishError) and self.report is None:
            raise t.error
        if self.report is None:
            raise PublishError(self.error or str(t.error))
        return self.report

    def _report(self, res: X.ExportResult, val: V.ValidationResult) -> Report:
        def conv(f: V.Finding, sev: str) -> Issue:
            return Issue(sev, f.code or "", f.path, f.message, f.fix)
        errores = [conv(f, "error") for f in val.errors]
        avisos = [conv(f, "aviso") for f in val.warnings]
        p = res.paquete
        resumen = tr("publish.resumen", id=p.id, version=p.version, piezas=len(p.contenido.piezas), recetas=len(p.contenido.recetas), tamano=CO.fmt_size(res.tamano))
        return Report(ok=not errores, errores=errores, avisos=avisos, paquete=p, tamano=res.tamano, sha256=res.sha256, resumen=resumen,
                      avisos_exportacion=list(res.avisos))

    # ------------------------------------------------------------------------------------------------------------ salidas
    def _need_valid(self) -> None:
        if not self.validated:
            raise PublishError(tr("publish.validar_primero"))

    def export_zip(self, out: str, *, overwrite: bool = False) -> str:
        """Copia el paquete validado a `out` (un `.zip`). `PublishError` si no se validó con lo que hoy dice el formulario o si `out` ya existe."""
        self._need_valid()
        if not out.lower().endswith(".zip"):
            out += ".zip"
        if os.path.exists(out) and not overwrite:
            raise PublishError(tr("publish.ya_existe", ruta=out))
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        shutil.copyfile(self._zip, out)
        return out

    def _extract(self, dest: str) -> None:
        """Descomprime el paquete validado en `dest` (una carpeta nueva o vacía)."""
        if os.path.exists(dest) and (not os.path.isdir(dest) or os.listdir(dest)):
            raise PublishError(tr("publish.carpeta_no_vacia", ruta=dest))
        os.makedirs(dest, exist_ok=True)
        base = os.path.abspath(dest)
        with zipfile.ZipFile(self._zip) as zf:
            for info in zf.infolist():
                target = os.path.abspath(os.path.join(base, *info.filename.split("/")))
                if os.path.commonpath([base, target]) != base:         # el zip lo armamos nosotros, pero no se extrae nada fuera de `dest`
                    raise PublishError(tr("publish.zip_raro", nombre=info.filename))
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)

    def prepare_repo(self, dest: str, repo: str = "") -> list:
        """Deja en `dest` la carpeta del repo de GitHub: el paquete desempaquetado, `paquete.json` con `github` y el workflow que publica el Release con
        cada tag. Devuelve los pasos siguientes (texto). `dest` tiene que no existir o estar vacía."""
        self._need_valid()
        repo = (repo or self.form.github).strip()
        if not Q._REPO_RE.match(repo):
            raise PublishError(tr("publish.github_invalido"))
        self._extract(dest)
        try:
            A.set_github(dest, repo)
        except A.AutorError as exc:
            raise PublishError(str(exc)) from None
        p = self.report.paquete
        return [tr("publish.repo_paso1", dir=dest, repo=repo), tr("publish.repo_paso2", version=p.version), tr("publish.repo_paso3", id=p.id, version=p.version)]

    def prepare_alta(self, dest: str, *, autor: str = "") -> tuple:
        """Escribe `dest/<Autor>@<id>/` con `meta.json` y `descripcion.md`: la carpeta que se manda en el alta al índice (una sola vez; las versiones
        nuevas las toma el índice solas). Pide `github`. Devuelve (carpeta, pasos siguientes)."""
        self._need_valid()
        pkg = self.report.paquete
        github = self.form.github.strip()
        if github and github != pkg.github:
            pkg = Q.Paquete.from_json(pkg.to_json())
            pkg.github = github
        if not pkg.github:
            raise PublishError(tr("publish.alta_pide_github"))
        try:
            meta = IX.meta_from_paquete(pkg, autor=autor)
            folder = IX.folder_name(pkg, autor)
        except IX.IndiceError as exc:
            raise PublishError(str(exc)) from None
        target = os.path.join(dest, folder)
        if os.path.exists(target) and os.listdir(target):
            raise PublishError(tr("publish.carpeta_no_vacia", ruta=target))
        os.makedirs(target, exist_ok=True)
        with open(os.path.join(target, "meta.json"), "w", encoding="utf-8", newline="\n") as f:
            f.write(IX.dump_json(meta))
        long_text = ""
        if self.form.readme and os.path.isfile(self.form.readme):
            with open(self.form.readme, encoding="utf-8") as f:
                long_text = f.read()
        text = long_text or f"# {pkg.nombre}\n\n{pkg.descripcion}\n"
        with open(os.path.join(target, "descripcion.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(text if text.endswith("\n") else text + "\n")
        if self.form.portada and os.path.isfile(self.form.portada):
            ext = os.path.splitext(self.form.portada)[1].lower()
            if ext in (".png", ".jpg", ".jpeg"):
                shutil.copyfile(self.form.portada, os.path.join(target, "portada" + (".jpg" if ext == ".jpeg" else ext)))
        return target, [tr("publish.alta_paso1", folder=folder), tr("publish.alta_paso2"), tr("publish.alta_paso3", version=pkg.version)]


__all__ = ["PublishFlow", "PublishError", "Form", "Problem", "Issue", "Report", "license_help", "origin_help", "suggest_id", "parse_authors", "TIPOS"]
