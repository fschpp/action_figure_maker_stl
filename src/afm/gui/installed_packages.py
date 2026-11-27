"""«Paquetes instalados» sin GTK (D20): lista con versión y origen, Actualizar, Versiones, Quitar, errores atribuidos y piezas que faltan.

La ventana (`installed_packages_window`) solo dibuja lo que sale de acá; todo el flujo se puede conducir sin ventana:

    online = CO.OnlineState(root=ruta_del_catalogo); online.refresh()      # índices (sin red: el guardado)
    st = InstalledState(online)
    rows = st.rows()                                   # un renglón por paquete de `paquetes.json`, con su estado frente al índice
    st.versions(rows[0])                               # versiones que el índice trae (instalada, retirada, prerelease)
    plan = st.plan_remove(rows[0])                     # qué se quitaría y qué se conserva (NO escribe); `st.remove_confirmation(plan)`
    st.task_remove(plan, acepto=True).start().join()   # recién ahora se mueve a la papelera (todo o nada); sin `acepto` no escribe nada
    st.task_diagnose(root).start().join().result       # `Diagnosis`: los problemas de `verificar` atribuidos al paquete que trajo cada pieza

«Actualizar» y «Instalar esta versión» no se hacen acá: `update_target(row)` / `version_target(row, v)` devuelven la entrada y la versión para
abrir el diálogo de confirmación de D19 (nada se instala sin esa confirmación). Quitar **no se puede cancelar una vez que empezó a escribir**.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .. import desinstalar as DS
from .. import instalar as I
from .. import paquete as Q
from .. import remoto as R
from ..catalog import Catalog, CatalogError
from ..i18n import tr
from . import catalog_online as CO

# estado de un renglón frente al índice, de más a menos importante
BLOQUEADO, RETIRADA, ACTUALIZACION, AL_DIA, SIN_INDICE = "bloqueado", "retirada", "actualizacion", "al_dia", "sin_indice"
STATES = (BLOQUEADO, RETIRADA, ACTUALIZACION, AL_DIA, SIN_INDICE)
MAX_LISTED = 6                                    # cuántos nombres se listan en un resumen antes de «y N más»


@dataclass
class Row:
    root: str
    id: str
    nombre: str
    version: str
    origen: str                                    # «owner/repo» o «zip local»
    fecha: str
    licencia: str
    autores: str
    piezas: list
    recetas: list
    roles: list
    estado: str = SIN_INDICE
    actualizar_a: str = ""                         # versión nueva (solo con estado `actualizacion`)
    alertas: list = field(default_factory=list)    # textos de `dependencias.audit` (retirada, bloqueado)
    entry: R.Entry | None = None

    @property
    def titulo(self) -> str:
        return self.nombre or self.id

    @property
    def subtitulo(self) -> str:
        return tr("installed_packages.subtitulo", version=self.version or "?", origen=self.origen)

    @property
    def puede_actualizar(self) -> bool:
        return self.estado == ACTUALIZACION and self.entry is not None

    @property
    def tiene_versiones(self) -> bool:
        return self.entry is not None and bool(self.entry.versiones or self.entry.latest)

    @property
    def estado_texto(self) -> str:
        return tr(f"installed_packages.estado_{self.estado}", version=self.actualizar_a)


@dataclass
class VersionRow:
    version: str
    instalada: bool
    retirada: bool
    prerelease: bool
    publicado: str
    tamano: int
    bloqueada: str = ""                            # motivo si el índice bloquea esa versión (por `sha256`)

    @property
    def instalable(self) -> bool:
        return not self.retirada and not self.bloqueada

    @property
    def texto(self) -> str:
        marks = [m for m, on in ((tr("installed_packages.v_instalada"), self.instalada), (tr("installed_packages.v_retirada"), self.retirada),
                                 (tr("installed_packages.v_prerelease"), self.prerelease)) if on]
        return self.version + (f"  ({', '.join(marks)})" if marks else "") + (f" · {CO.fmt_size(self.tamano)}" if self.tamano else "")


@dataclass
class Diagnosis:
    """Los problemas de `Catalog.verify` de un catálogo, atribuidos al paquete que trajo la pieza (o a nadie: lo agregó la persona)."""

    root: str
    por_paquete: dict = field(default_factory=dict)       # id del paquete -> [líneas]
    propios: list = field(default_factory=list)           # sin paquete: piezas propias del catálogo o problemas de la línea
    avisos: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.por_paquete and not self.propios

    def of(self, pid: str) -> list:
        return list(self.por_paquete.get(pid, []))


@dataclass
class Offer:
    """Una pieza que falta (`id@n`) y los paquetes del índice que dicen traerla."""

    ref: str
    entradas: list = field(default_factory=list)          # [R.Entry] que se pueden instalar

    @property
    def encontrada(self) -> bool:
        return bool(self.entradas)


def _summary(names: list, kind_key: str) -> str:
    if not names:
        return tr(f"installed_packages.sin_{kind_key}")
    shown = ", ".join(names[:MAX_LISTED])
    return shown + (tr("installed_packages.y_n_mas", n=len(names) - MAX_LISTED) if len(names) > MAX_LISTED else "")


class InstalledState:
    """Estado de la ventana «Paquetes instalados»: `online` es el `OnlineState` de «Explorar» (mismos índices, mismos catálogos)."""

    def __init__(self, online: CO.OnlineState):
        self.online = online
        self.errors: list[str] = []

    # ---- lista
    @staticmethod
    def origin_text(got: dict) -> str:
        return got.get("github") or tr("installed_packages.zip_local")

    def entry_for(self, pid: str, got: dict) -> R.Entry | None:
        """La entrada del índice que corresponde a lo instalado. Con otro repo que el instalado no hay entrada (suplantación, §5.7)."""
        cands = [e for idx in self.online.indexes for e in idx.entries if e.id == pid and e.github == got.get("github", "")]
        if not cands:
            return None
        same = [e for e in cands if got.get("indice") and e.indice == got["indice"]]
        return (same or cands)[0]

    def rows(self) -> list[Row]:
        """Un renglón por paquete instalado en los catálogos de la ventana, ordenados por catálogo y nombre. Un `paquetes.json` ilegible va a `errors`."""
        self.errors = []
        alerts = {}
        for a in self.online.alerts():
            alerts.setdefault((a.paquete, a.version), []).append(a)
        out: list[Row] = []
        for root in self.online.roots():
            try:
                reg = I.load_registry(root)["paquetes"]
            except (I.InstallError, OSError) as exc:
                self.errors.append(f"{root}: {exc}")
                continue
            for pid, got in sorted(reg.items()):
                if not isinstance(got, dict):
                    continue
                refs = got.get("refs") if isinstance(got.get("refs"), dict) else {}
                autores = ", ".join(a.get("nombre", "") for a in got.get("autores", []) if isinstance(a, dict))
                row = Row(root=root, id=pid, nombre=str(got.get("nombre", "")), version=str(got.get("version", "")), origen=self.origin_text(got),
                          fecha=str(got.get("fecha", ""))[:10], licencia=str(got.get("licencia", "")), autores=autores,
                          piezas=list(refs.get("piezas", [])), recetas=list(refs.get("recetas", [])), roles=list(refs.get("roles", [])))
                row.entry = self.entry_for(pid, got)
                found = alerts.get((pid, row.version), [])
                row.alertas = [str(a) for a in found]
                kinds = {a.kind for a in found}
                if "bloqueado" in kinds:
                    row.estado = BLOQUEADO
                elif "retirada" in kinds:
                    row.estado = RETIRADA
                elif row.entry is not None:
                    st = R.state(row.entry, root)
                    if st.startswith("actualización"):
                        row.estado, row.actualizar_a = ACTUALIZACION, st.split(" ", 1)[1]
                    else:
                        row.estado = AL_DIA
                out.append(row)
        return sorted(out, key=lambda r: (r.root, r.titulo.casefold()))

    def detail_rows(self, row: Row) -> list[tuple[str, str]]:
        """[(rótulo, valor)] de la ficha: versión, origen, autor, licencia, fecha, qué trajo y en qué catálogo está."""
        out = [(tr("installed_packages.l_version"), row.version or "?"), (tr("installed_packages.l_origen"), row.origen),
               (tr("installed_packages.l_autores"), row.autores or "—"), (tr("installed_packages.l_licencia"), row.licencia or "—"),
               (tr("installed_packages.l_instalado"), row.fecha or "—"),
               (tr("installed_packages.l_piezas"), _summary(row.piezas, "piezas")), (tr("installed_packages.l_recetas"), _summary(row.recetas, "recetas")),
               (tr("installed_packages.l_catalogo"), row.root)]
        if row.roles:
            out.append((tr("installed_packages.l_roles"), ", ".join(row.roles)))
        return out

    # ---- versiones y actualización
    def versions(self, row: Row) -> list[VersionRow]:
        e = row.entry
        if e is None:
            return []
        seen, out = set(), []
        for r in ([e.latest] if e.latest else []) + list(e.versiones):
            if r is None or r.version in seen:
                continue
            seen.add(r.version)
            out.append(VersionRow(r.version, r.version == row.version, r.version in e.retiradas, r.prerelease, r.publicado[:10], r.tamano, e.block_of(r)))

        def key(v: VersionRow):
            try:
                return Q.parse_semver(v.version)
            except Q.PaqueteError:
                return Q.parse_semver("0.0.0")
        return sorted(out, key=key, reverse=True)

    def update_target(self, row: Row) -> tuple[R.Entry, str] | None:
        """(entrada, versión) para abrir el diálogo de confirmación con «Actualizar»; `None` si no hay nada que actualizar."""
        return (row.entry, row.actualizar_a) if row.puede_actualizar else None

    def version_target(self, row: Row, version: str) -> tuple[R.Entry, str] | None:
        """(entrada, versión) para «Instalar esta versión» (una retirada o bloqueada no se ofrece)."""
        v = next((x for x in self.versions(row) if x.version == version), None)
        return (row.entry, version) if v is not None and v.instalable and not v.instalada else None

    # ---- quitar
    def plan_remove(self, row: Row, search=()) -> DS.RemovePlan:
        return DS.plan_remove(row.root, row.id, search=search)

    @staticmethod
    def remove_confirmation(plan: DS.RemovePlan) -> tuple[str, list[str], bool]:
        """(titulo, líneas, pide_dependientes) del diálogo de confirmación de «Quitar». Nada se quita sin `acepto`."""
        lines = [tr("installed_packages.q_se_van", piezas=len(plan.piezas), recetas=len(plan.recetas))]
        lines += [tr("installed_packages.q_pieza", ref=r) for r in plan.piezas] + [tr("installed_packages.q_receta", nombre=n) for n in plan.recetas]
        if plan.conservadas:
            lines.append(tr("installed_packages.q_se_quedan", n=len(plan.conservadas)))
            lines += [tr("installed_packages.q_conservada", tipo=k.kind, nombre=k.name, motivo=k.why) for k in plan.conservadas]
        if plan.roles:
            lines.append(tr("installed_packages.q_roles", roles=", ".join(plan.roles)))
        lines += [tr("installed_packages.q_aviso", texto=a) for a in plan.avisos if not a.startswith("los roles de paleta")]
        lines += [tr("installed_packages.q_dependiente", texto=d) for d in plan.dependientes]
        lines += [tr("installed_packages.q_error", texto=e) for e in plan.errores]
        lines.append(tr("installed_packages.q_papelera"))
        return tr("installed_packages.q_titulo", id=plan.id, version=plan.version), lines, bool(plan.dependientes)

    @staticmethod
    def result_lines(res: DS.RemoveResult) -> list[str]:
        out = [tr("installed_packages.r_quitado", id=res.id, piezas=len(res.piezas), recetas=len(res.recetas))]
        if res.conservadas:
            out.append(tr("installed_packages.r_conservadas", n=len(res.conservadas)))
        if res.papelera_piezas or res.papelera_recetas:
            out.append(tr("installed_packages.r_papelera", ruta=os.path.dirname(res.papelera_piezas or res.papelera_recetas)))
        return out

    def task_remove(self, plan: DS.RemovePlan, *, acepto: bool = False, confirmar_dependientes: bool = False, notify=None) -> CO.Task:
        def work(task: CO.Task):
            if not acepto:                               # sin confirmar no se llega a escribir (ni se bloquea la cancelación)
                raise I.InstallError(tr("catalog_online.sin_confirmar"))
            task.progress(0, 0, tr("installed_packages.quitando"))
            task.lock_cancel()
            return DS.apply_remove(plan, confirmar_dependientes=confirmar_dependientes)
        return CO.Task(work, notify=notify, name="afm-quitar")

    # ---- errores atribuidos
    def diagnose(self, root: str) -> Diagnosis:
        """`Catalog.verify` de `root` con cada problema atribuido al paquete que trajo la pieza (los de piezas propias quedan en `propios`),
        más lo que un paquete dice haber traído y ya no está en el catálogo."""
        diag = Diagnosis(root)
        try:
            cat = Catalog.open(root)
            reg = I.load_registry(root)["paquetes"]
            report = cat.verify()
        except (CatalogError, I.InstallError, OSError) as exc:
            diag.propios.append(str(exc))
            return diag
        owner = {ref: pid for pid, got in reg.items() if isinstance(got, dict) for ref in ((got.get("refs") or {}).get("piezas") or [])}
        for text in report.problems:
            pid = owner.get(text.split(":", 1)[0].strip(), "")
            (diag.por_paquete.setdefault(pid, []) if pid else diag.propios).append(text)
        diag.avisos = list(report.warnings)
        for pid, got in sorted(reg.items()):
            if not isinstance(got, dict):
                continue
            refs = got.get("refs") or {}
            for ref in refs.get("piezas", []):
                try:
                    cat.meta(ref)
                except CatalogError:
                    diag.por_paquete.setdefault(pid, []).append(tr("installed_packages.falta_pieza", ref=ref))
            for name in refs.get("recetas", []):
                if not os.path.isfile(os.path.join(root, "figuras", f"{name}.receta.json")):
                    diag.por_paquete.setdefault(pid, []).append(tr("installed_packages.falta_receta", nombre=name))
        return diag

    def task_diagnose(self, root: str, *, notify=None) -> CO.Task:
        def work(task: CO.Task):
            task.set_text(tr("installed_packages.revisando"))
            return self.diagnose(root)
        return CO.Task(work, notify=notify, name="afm-diagnostico")

    # ---- piezas que faltan (al abrir una receta o un proyecto)
    @staticmethod
    def missing_refs(root: str, refs) -> list[str]:
        """Los `id@n` de `refs` que no están en el catálogo `root` (en orden y sin repetir). Una `id` sin versión falta si no hay ninguna."""
        try:
            cat = Catalog.open(root)
        except CatalogError:
            return []
        out = []
        for ref in dict.fromkeys(r for r in refs if isinstance(r, str) and r):
            try:
                cat.meta(ref)
            except CatalogError:
                out.append(ref)
        return out

    @classmethod
    def recipe_missing(cls, root: str, recipe_path: str) -> list[str]:
        """Las piezas de una receta (lugares y accesorios) que no están en `root`."""
        from ..recipe import Recipe, RecipeError

        try:
            r = Recipe.load(recipe_path)
        except (RecipeError, OSError, ValueError):
            return []
        return cls.missing_refs(root, list(r.lugares.values()) + list(r.accesorios))

    def find_pieces(self, refs) -> list[Offer]:
        """Para cada `id@n`, los paquetes del índice que dicen traer esa pieza (resumen de piezas del índice; solo entradas instalables y visibles).
        Una `id` sin versión coincide con cualquier versión."""
        out = []
        for ref in refs:
            try:
                pid, ver = R.parse_ref(ref)
            except CatalogError:
                continue
            found = []
            for idx in self.online.indexes:
                for e in idx.entries:
                    if not (e.visible and e.instalable):
                        continue
                    for p in e.raw.get("piezas") or []:
                        r = p.get("ref", "") if isinstance(p, dict) else ""
                        if r == ref or (ver is None and r.rsplit("@", 1)[0] == pid):
                            found.append(e)
                            break
            out.append(Offer(ref, found))
        return out

    def offer_lines(self, offers: list[Offer]) -> list[str]:
        """Texto del aviso «faltan N piezas»: cada una con los paquetes que la traen (o que no se encontró en los índices)."""
        lines = []
        for o in offers:
            if o.encontrada:
                lines.append(tr("installed_packages.of_en", ref=o.ref, paquetes=", ".join(f"{e.nombre or e.id} {e.latest.version}" for e in o.entradas[:3])))
            else:
                lines.append(tr("installed_packages.of_no", ref=o.ref))
        return lines


__all__ = ["AL_DIA", "ACTUALIZACION", "BLOQUEADO", "RETIRADA", "SIN_INDICE", "STATES", "Diagnosis", "InstalledState", "Offer", "Row", "VersionRow"]
