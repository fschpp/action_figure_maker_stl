"""Importar un paquete `.zip` desde un archivo (D22), sin GTK: el mismo diálogo de confirmación que «Instalar» de «Explorar» (D19), pero con el zip
que la persona eligió o soltó en la ventana, en vez de uno bajado del índice.

    flow = LocalZipFlow(OnlineState(urls=[], root=catalogo_abierto_o_vacio), "/ruta/paquete.zip")   # `ZipImportError` si no es un .zip que se pueda leer
    flow.pide_datos                      # True solo en un zip heredado (sin paquete.json, D07): hay que decir autor, licencia y origen
    flow.datos.autores, flow.datos.licencia, flow.datos.origen = "Ana Pérez", "CC-BY-4.0", "propia"; flow.confirmar_datos()    # [] o los problemas
    flow.task_propose().start().join()   # abre el zip, lo inspecciona y arma el plan: NO escribe en el catálogo
    flow.confirmation                    # lo mismo que muestra D19: autor, licencia, origen, destino, qué agrega, conflictos y avisos
    flow.task_install(acepto=True).start().join()      # recién ahora se escribe (todo o nada); sin `acepto=True` no escribe nada
    flow.close()                         # borra la carpeta de trabajo de un zip heredado

Se instala **exactamente** lo que instala `afm catalogo instalar` (`instalar.plan_install` / `apply`): el diálogo es otra cara del mismo camino. Un
zip local no resuelve lo que el paquete `requiere` (eso lo hace el índice): las dependencias que falten salen como avisos, igual que en la consola.
Lo que se importa no pasó por el índice, así que el diálogo lo dice («importado desde un archivo»; la app no verifica licencia ni origen).
"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass

from .. import instalar as I
from .. import paquete as Q
from .. import ziplector as Z
from ..catalog import CatalogError
from ..i18n import tr
from . import catalog_online as CO
from . import install_confirm as IC
from . import publish as PB

PAQUETE, HEREDADO = "paquete", "heredado"



class ZipImportError(CatalogError):
    """El archivo no se puede importar (no es un `.zip` legible, trae varias líneas…). No se tocó nada."""


# ------------------------------------------------------------------------------------------------------------------------ qué es el archivo

@dataclass
class ZipInfo:
    path: str
    kind: str                         # PAQUETE (trae paquete.json) | HEREDADO (un catálogo comprimido a mano)
    tamano: int
    linea: str                        # nombre de la línea que declara ("" si no declara ninguna, p. ej. una colección)
    paquete: Q.Paquete | None = None  # solo en un PAQUETE


def split_dropped(paths) -> tuple[list[str], list[str]]:
    """(zips, otros) de lo que se soltó en la ventana: acepta solo `.zip` (sin mirar mayúsculas). El resto se informa, no se abre."""
    zips, otros = [], []
    for p in paths or ():
        (zips if str(p).lower().endswith(".zip") else otros).append(str(p))
    return zips, otros


def inspect_zip(path: str) -> ZipInfo:
    """Mira qué trae el archivo **sin extraerlo**: un paquete (`paquete.json`) o un zip heredado (`linea.json`). `ZipImportError` si no es ninguno."""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ZipImportError(tr("import_zip.no_es_archivo", ruta=path))
    if not path.lower().endswith(".zip"):
        raise ZipImportError(tr("import_zip.no_es_zip", nombre=os.path.basename(path)))
    size = os.path.getsize(path)
    try:
        legacy = I.detect_legacy(path)
        if legacy is not None:
            nombre = legacy.linea.get("nombre")
            return ZipInfo(path, HEREDADO, size, nombre if isinstance(nombre, str) else "")
        pkg = I.peek_package(path)
    except (Z.ZipError, I.InstallError) as exc:
        raise ZipImportError(str(exc)) from None
    return ZipInfo(path, PAQUETE, size, (pkg.linea or {}).get("nombre", ""), pkg)


# ------------------------------------------------------------------------------------------------------------------------ datos de un zip heredado

@dataclass
class LegacyData:
    """Lo que un zip heredado no trae y solo quien lo instala puede decir. La app no lo inventa (como `--licencia/--origen/--autor` de la consola)."""

    autores: str = ""                 # «Ana Pérez, Beto:beto-gh» (uno por línea o separados por comas)
    licencia: str = ""
    origen: str = ""
    id: str = ""                      # opcional: por defecto el nombre de la línea
    nombre: str = ""                  # opcional
    version: str = ""                 # opcional: por defecto 1.0.0

    def problems(self) -> list[str]:
        out = []
        if not PB.parse_authors(self.autores):
            out.append(tr("import_zip.falta_autor"))
        if self.licencia not in Q.LICENSES:
            out.append(tr("import_zip.falta_licencia"))
        if self.origen not in Q.ORIGINS:
            out.append(tr("import_zip.falta_origen"))
        if self.id and not (Q._ID_RE.match(self.id) and len(self.id) <= Q.ID_MAX):
            out.append(tr("import_zip.id_malo", max=Q.ID_MAX))
        if self.version and not Q.is_semver(self.version):
            out.append(tr("import_zip.version_mala"))
        return out

    def clear_invalid_optionals(self) -> None:
        """Vacía el id y la versión si no sirven: son opcionales (hay valor por defecto) y ya se avisó del problema; no se arrastran a la próxima prueba."""
        if self.id and not (Q._ID_RE.match(self.id) and len(self.id) <= Q.ID_MAX):
            self.id = ""
        if self.version and not Q.is_semver(self.version):
            self.version = ""

    def info(self) -> dict:
        """Los argumentos de `instalar.plan_install_legacy`."""
        return {"licencia": self.licencia, "origen": self.origen, "autores": PB.parse_authors(self.autores), "id": self.id.strip(),
                "nombre": self.nombre.strip(), "version": self.version.strip()}


# ------------------------------------------------------------------------------------------------------------------------ propuesta y flujo

@dataclass
class LocalProposal(CO.Proposal):
    """Lo que se mostrará en la confirmación de un zip local: un único plan (`instalar.InstallPlan`). Existir **no** escribió nada en el catálogo."""

    plan: I.InstallPlan | None = None
    size: int = 0                     # tamaño del archivo que eligió la persona

    @property
    def planificado(self) -> bool:
        return self.plan is not None

    @property
    def errors(self) -> list:
        return list(self.plan.errors) if self.plan else []

    @property
    def warnings(self) -> list:
        return list(self.plan.warnings) if self.plan else []

    @property
    def ok(self) -> bool:
        return bool(self.plan and self.plan.ok)

    @property
    def nada_que_hacer(self) -> bool:
        return bool(self.plan and self.plan.ok and self.plan.nada_que_hacer)

    @property
    def cambia_origen(self) -> bool:
        return bool(self.plan and self.plan.cambia_origen)

    @property
    def pasos(self) -> list:
        return []

    @property
    def tamano_total(self) -> int:
        return self.size

    def _plans(self) -> list:
        return [self.plan] if self.plan else []

    def lines(self) -> list[str]:
        return self.plan.describe() if self.plan else []


class LocalZipFlow(IC.InstallFlow):
    """El flujo de instalar un `.zip` local. Tiene la misma forma que `InstallFlow` (etapas, opciones, `confirmation`, `task_install`, `report`): el
    diálogo no distingue de dónde viene el paquete. `state` es un `catalog_online.OnlineState` (de él sale el catálogo abierto, el destino)."""

    def __init__(self, state: CO.OnlineState, zip_path: str):
        super().__init__(state, None)                # sin entrada de índice
        self.zinfo = inspect_zip(zip_path)
        self.zip_path = self.zinfo.path
        self.datos = LegacyData()
        self.datos_ok = self.zinfo.kind == PAQUETE   # un paquete ya trae autor, licencia y origen
        self._work = ""                              # carpeta de trabajo de un zip heredado (el paquete convertido tiene que vivir hasta instalar)
        self._installable = self.zip_path

    # ------------------------------------------------------------------------------------------------------------------ lo que el diálogo pide
    @property
    def pide_datos(self) -> bool:                    # type: ignore[override]
        return not self.datos_ok

    @property
    def heredado(self) -> bool:
        return self.zinfo.kind == HEREDADO

    @property
    def title(self) -> str:
        pk = self.proposal.plan.paquete if self.proposal is not None and self.proposal.plan is not None else self.zinfo.paquete
        return pk.nombre or pk.id if pk is not None else os.path.basename(self.zip_path)

    @property
    def name(self) -> str:
        pk = self.proposal.plan.paquete if self.proposal is not None and self.proposal.plan is not None else self.zinfo.paquete
        return pk.id if pk is not None else os.path.basename(self.zip_path)

    @property
    def preparing_text(self) -> str:
        return tr("import_zip.leyendo", nombre=os.path.basename(self.zip_path))

    def confirmar_datos(self) -> list[str]:
        """Da por buenos los datos de un zip heredado y deja pasar a proponer; si hay problemas los devuelve y no avanza."""
        problems = self.datos.problems() if self.heredado else []
        if problems:
            self.datos.clear_invalid_optionals()
        self.datos_ok = not problems
        return problems

    def volver_a_datos(self) -> None:
        """Vuelve al formulario de un zip heredado (la confirmación que se vio era de otros datos). No escribe nada."""
        if self.heredado and self.stage in (IC.CONFIRMANDO, IC.FALLO, IC.CANCELADO):
            self.datos_ok, self.proposal, self.error, self.stage = False, None, "", IC.INICIO

    def close(self) -> None:
        if self._work:
            shutil.rmtree(self._work, ignore_errors=True)
            self._work = ""

    # ------------------------------------------------------------------------------------------------------------------ destino
    def line_root(self) -> str:
        """Carpeta donde se crea la línea nueva del paquete (sin mirar `$AFM_CATALOGO`, igual que la consola)."""
        if not self.zinfo.linea:
            raise I.InstallError(tr("import_zip.sin_linea"))
        return I.explicit_root(self.zinfo.linea)

    def target_root(self) -> str:
        """El catálogo abierto en la app; si no hay ninguno, la carpeta de la línea del paquete (que se crea)."""
        return self.line_root() if self.como_linea else (self.state.root or self.line_root())

    # ------------------------------------------------------------------------------------------------------------------ proponer / instalar
    def _make_proposal(self, task: CO.Task) -> LocalProposal:
        if not self.datos_ok:
            raise I.InstallError(tr("import_zip.faltan_datos"))
        root = self.target_root()
        task.progress(0, 0, tr("import_zip.leyendo", nombre=os.path.basename(self.zip_path)))
        if self.heredado:
            if not self._work:
                self._work = tempfile.mkdtemp(prefix="afm-importar-")
            plan = I.plan_install_legacy(self.zip_path, root, self._work, como_linea=self.como_linea, allow_fbx=self.allow_fbx, **self.datos.info())
            self._installable = os.path.join(self._work, "heredado.zip")
        else:
            plan = I.plan_install(self.zip_path, root, como_linea=self.como_linea, allow_fbx=self.allow_fbx)
        task.progress(0, 0)                          # un punto de cancelación más: hasta acá no se escribió nada
        return LocalProposal(entry=None, root=root, dep=None, plan=plan, size=self.zinfo.tamano)

    def _write(self, prop: LocalProposal) -> list:
        """Vuelve a planificar contra el catálogo **de ahora** (pudo cambiar mientras se miraba la confirmación) y aplica todo o nada."""
        old = prop.plan
        plan = I.plan_install(self._installable, prop.root, como_linea=self.como_linea, allow_fbx=self.allow_fbx)
        plan.descartados, plan.heredado_sha256 = old.descartados, old.heredado_sha256        # lo propio de un zip heredado (D07)
        if not plan.ok:
            raise I.InstallError(tr("catalog_online.plan_con_errores", errores="; ".join(str(i) for i in plan.errors[:3])))
        if plan.cambia_origen and not self.confirmar_origen:
            raise I.InstallError(tr("catalog_online.cambia_origen"))
        if plan.nada_que_hacer:
            return []
        return [I.apply(plan, confirmar_origen=self.confirmar_origen)]

    # ------------------------------------------------------------------------------------------------------------------ lo que cambia en la confirmación
    def _fallback_rows(self) -> list:
        return []

    def _source_rows(self, prop: CO.Proposal) -> list:
        rows = [(tr("import_zip.l_archivo"), self.zip_path), (tr("import_zip.l_tamano"), CO.fmt_size(prop.tamano_total))]
        if self.heredado:
            rows.append((tr("import_zip.l_tipo"), tr("import_zip.tipo_heredado")))
        return rows

    def _package_lines(self, prop: CO.Proposal, plans: list) -> list:
        pk = plans[0].paquete if plans else None
        if pk is None:
            return []
        authors = ", ".join(a.nombre for a in pk.autores) or "—"
        return [f"1. {pk.id} {pk.version} — {authors} · {pk.licencia} · {pk.origen}"]

    def _entry_warnings(self) -> list:
        return [tr("import_zip.aviso_heredado")] if self.heredado else []

    def _confirmation_title(self, paso) -> str:
        pk = self.proposal.plan.paquete if self.proposal is not None and self.proposal.plan is not None else None
        if pk is None:
            return tr("import_zip.titulo", nombre=os.path.basename(self.zip_path))
        return tr("install_confirm.titulo_instalar", id=pk.id, version=pk.version)

    def _unverified_text(self) -> str:
        return tr("import_zip.aviso_archivo")


__all__ = ["LocalZipFlow", "LocalProposal", "LegacyData", "ZipInfo", "ZipImportError", "inspect_zip", "split_dropped", "PAQUETE", "HEREDADO"]
