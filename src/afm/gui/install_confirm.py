"""Diálogo de confirmación e instalación sin GTK (D19): lo que se instala, de quién es, con qué licencia, qué toca y qué conflictos hay, **antes** de escribir.

La ventana (`install_confirm_window`) solo dibuja lo que sale de acá, así que todo el flujo se puede conducir sin ventana:

    flow = InstallFlow(state, entry)                     # `state` es un `catalog_online.OnlineState`
    flow.task_propose().start().join()                   # baja y verifica los zips y arma el plan: NO escribe en el catálogo
    c = flow.confirmation                                # autor, licencia, origen, tamaño, qué agrega, línea destino, conflictos y avisos
    flow.task_install(acepto=True).start().join()        # recién ahora se escribe (todo o nada); sin `acepto=True` no escribe nada
    flow.report                                          # lo que se instaló + `verificar` del catálogo + `cobertura` de las piezas nuevas

Etapas (`flow.stage`): `PROPONIENDO` (bajando) → `CONFIRMANDO` → `INSTALANDO` → `LISTO`; `FALLO` si algo salió mal (el catálogo queda como estaba) y
`CANCELADO` si se canceló la descarga. Cambiar una opción (`como_linea`, `allow_fbx`) obliga a proponer de nuevo: la confirmación que se mostró era
de otro plan.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import catalog_ops as OPS
from .. import instalar as I
from .. import remoto as R
from ..catalog import Catalog, CatalogError
from ..i18n import tr
from ..progress import TaskCancelled
from . import catalog_online as CO

INICIO, PROPONIENDO, CONFIRMANDO, INSTALANDO, LISTO, FALLO, CANCELADO = "inicio", "proponiendo", "confirmando", "instalando", "listo", "fallo", "cancelado"
MAX_COBERTURA = 8                    # piezas de las que se mide la cobertura en el informe (el resto se pide con `afm catalogo cobertura`)


@dataclass
class Section:
    titulo: str
    lineas: list = field(default_factory=list)


@dataclass
class Confirmation:
    """Todo lo que el diálogo muestra antes de escribir. Se recalcula con el estado de las opciones de `flow` cada vez que se pide."""

    titulo: str
    boton: str
    puede_instalar: bool
    rows: list                                   # [(rótulo, valor)]
    trae: str                                    # «Trae 6 piezas, 1 receta y 5 roles de paleta nuevos (18,2 MB).»
    secciones: list                              # [Section]
    errores: list = field(default_factory=list)
    avisos: list = field(default_factory=list)
    no_verificado: str = ""                      # «Listado no verificado…» (siempre)
    pide_origen: bool = False                    # un paquete ya instalado cambia de repo: hace falta confirmarlo (suplantación, plan §5.7)
    crea_linea: bool = False                     # el destino no existe: se crea la línea con el `linea.json` del paquete
    nada_que_hacer: bool = False
    ofrece_su_linea: bool = False                # no encaja en este catálogo: se puede instalar como línea nueva
    ofrece_fbx: bool = False                     # el paquete trae FBX de color y no se permitió


@dataclass
class Report:
    """Lo que quedó después de instalar."""

    results: list
    resumen: list                                # una línea por paquete (+ avisos)
    verificar: list = field(default_factory=list)
    verificar_ok: bool = True
    cobertura: list = field(default_factory=list)
    carpeta: str = ""


def _authors(p, *, github: bool) -> str:
    out = []
    for a in p.autores:
        out.append(f"{a.nombre} (@{a.github})" if github and a.github else a.nombre)
    return ", ".join(out) or "—"


def _origin_text(origen: str) -> str:
    if not origen:
        return "—"
    key = f"install_confirm.origen_{origen.replace('-', '_')}"
    text = tr(key)
    return origen if text == key else f"{origen} — {text}"          # un origen que no se conoce se muestra tal cual


class InstallFlow:
    """El flujo de instalar un paquete del índice. `version=None` es la última versión; las opciones se fijan antes de `task_propose()`."""

    pide_datos = False                                  # D22: un zip heredado pide autor, licencia y origen antes de proponer (`LocalZipFlow`)

    def __init__(self, state: CO.OnlineState, entry: R.Entry | None, version: str | None = None):
        self.state = state
        self.entry = entry
        self.version = version
        self.como_linea = False
        self.allow_fbx = False
        self.allow_retirada = False
        self.confirmar_origen = False               # lo tilda la persona cuando se le avisa que un paquete cambia de origen
        self.stage = INICIO
        self.proposal: CO.Proposal | None = None
        self.report: Report | None = None
        self.error = ""

    # ------------------------------------------------------------------------------------------------------------------ proponer
    def task_propose(self, *, notify=None) -> CO.Task:
        """Baja y verifica los zips y arma el plan. **No escribe en el catálogo.** El resultado queda en `proposal` y la etapa en `CONFIRMANDO`."""
        def work(task: CO.Task):
            self.stage, self.error, self.proposal, self.confirmar_origen = PROPONIENDO, "", None, False
            try:
                prop = self._make_proposal(task)
            except TaskCancelled:
                self.stage = CANCELADO
                raise
            except Exception as exc:                     # noqa: BLE001 — el diálogo muestra el motivo; no se escribió nada
                self.stage, self.error = FALLO, str(exc)
                raise
            self.proposal, self.stage = prop, CONFIRMANDO
            return prop
        return CO.Task(work, notify=notify, name="afm-confirmar")

    # ------------------------------------------------------------------------------------------------------------------ ganchos (D22)
    # `import_zip.LocalZipFlow` cambia solo esto: de dónde sale el plan, qué dice la confirmación de su origen y cómo se escribe. El resto
    # (etapas, opciones, `confirmation`, `task_install`, `report`) es el mismo código: el diálogo no distingue de dónde viene el paquete.
    @property
    def title(self) -> str:
        return self.entry.nombre or self.entry.id

    @property
    def name(self) -> str:
        return self.entry.id

    @property
    def preparing_text(self) -> str:
        return tr("install_confirm_window.preparando")

    def volver_a_datos(self) -> None:
        """Solo un zip heredado tiene formulario al que volver."""

    def close(self) -> None:
        """Libera lo que el flujo haya dejado en disco (un zip heredado deja una carpeta de trabajo)."""

    def _make_proposal(self, task: CO.Task) -> CO.Proposal:
        def progress(done: int, total: int) -> None:
            task.progress(done, total, tr("catalog_online.descargando", nombre=self.entry.folder, hecho=CO.fmt_size(done), total=CO.fmt_size(total)))
        return self.state.propose(self.entry, self.version, como_linea=self.como_linea, allow_fbx=self.allow_fbx,
                                  allow_retirada=self.allow_retirada, progress=progress, root=self.line_root() if self.como_linea else "")

    def _write(self, prop: CO.Proposal) -> list:
        return self.state.install(prop, acepto=True, confirmar_origen=self.confirmar_origen)

    def _fallback_rows(self) -> list:
        """Autor, licencia y origen según el índice, cuando el plan no se pudo armar (error de dependencias)."""
        e, dash = self.entry, "—"
        return [(tr("install_confirm.l_autor"), e.autor or dash), (tr("install_confirm.l_licencia"), e.licencia or dash),
                (tr("install_confirm.l_origen"), _origin_text(e.origen))]

    def _source_rows(self, prop: CO.Proposal) -> list:
        return [(tr("install_confirm.l_descarga"), CO.fmt_size(prop.tamano_total))]

    def _package_lines(self, prop: CO.Proposal, plans: list) -> list:
        lines = []
        for n, (s, p) in enumerate(zip(prop.pasos, plans), 1):
            pk = p.paquete
            meta = f" — {_authors(pk, github=False)} · {pk.licencia} · {pk.origen}" if pk is not None else ""
            lines.append(f"{n}. {s.entry.folder} {s.release.version}{meta}" + ("" if s.por == "pedido" else f" ({s.por})"))
        return lines

    def _entry_warnings(self) -> list:
        out = []
        if self.entry.experimental:
            out.append(tr("catalog_online.aviso_experimental"))
        rel = self.entry.release(self.version)
        if rel is not None and rel.prerelease:
            out.append(tr("catalog_online.aviso_prerelease"))
        return out

    def _confirmation_title(self, paso) -> str:
        rel = self.entry.release(self.version)
        version = (paso.release.version if paso is not None else None) or (rel.version if rel else "")
        return tr("install_confirm.titulo_instalar", id=self.entry.id, version=version)

    def _unverified_text(self) -> str:
        return tr("catalog_online.aviso_no_verificado")

    def line_root(self) -> str:
        """Carpeta donde se crea la línea nueva del paquete (`--como-linea` necesita una carpeta que todavía no sea un catálogo)."""
        return R.default_root(self.entry)

    # ------------------------------------------------------------------------------------------------------------------ confirmación
    @property
    def confirmation(self) -> Confirmation | None:
        prop = self.proposal
        if prop is None:
            return None
        paso = prop.pasos[-1] if prop.pasos else None            # el pedido va al final: sus dependencias se instalan antes
        plans = prop._plans()
        main = plans[-1].paquete if plans else None
        dash = "—"
        rows: list = []
        if main is not None:
            rows += [(tr("install_confirm.l_autor"), _authors(main, github=True)), (tr("install_confirm.l_licencia"), main.licencia or dash)]
            if main.github:
                rows.append((tr("install_confirm.l_repo"), main.github))
            rows.append((tr("install_confirm.l_origen"), _origin_text(main.origen)))
        else:                                                    # el plan no se pudo armar (error de dependencias): lo que dice el índice
            rows += self._fallback_rows()
        crea = any(p.como_linea for p in plans)
        rows.append((tr("install_confirm.l_destino"), self._destination(prop, main, crea)))
        rows.append((tr("install_confirm.l_carpeta"), prop.root))
        rows += self._source_rows(prop)

        secciones: list = []
        has_plan = prop.planificado
        if has_plan:
            lines = self._package_lines(prop, plans)
            secciones.append(Section(tr("install_confirm.sec_paquetes", n=len(lines)), lines))
            for titulo, names in (("sec_piezas", prop.piezas), ("sec_recetas", prop.recetas), ("sec_roles", prop.roles),
                                  ("sec_ya_estan", [i.name for p in plans for i in p.omitidas]),
                                  ("sec_descartados", [d for p in plans for d in p.descartados])):
                if names:
                    secciones.append(Section(tr(f"install_confirm.{titulo}", n=len(names)), list(names)))

        avisos = [str(i) for i in prop.warnings] + self._entry_warnings()
        if has_plan and prop.cambia_origen:
            avisos.append(tr("install_confirm.aviso_origen"))
        errores = [str(i) for i in prop.errors]
        pide = prop.cambia_origen
        puede = prop.ok and not prop.nada_que_hacer and (not pide or self.confirmar_origen)
        return Confirmation(
            titulo=self._confirmation_title(paso),
            boton=tr("install_confirm.boton_instalar"), puede_instalar=puede, rows=rows, trae=prop.headline() if has_plan else "",
            secciones=secciones, errores=errores, avisos=avisos, no_verificado=self._unverified_text(), pide_origen=pide,
            crea_linea=crea, nada_que_hacer=prop.nada_que_hacer,
            ofrece_su_linea=not self.como_linea and (any(p.sugerir_linea for p in plans) or any("--como-linea" in i.fix for i in prop.errors)),
            ofrece_fbx=not self.allow_fbx and any("fbx" in str(i).lower() for i in prop.errors))

    def _destination(self, prop: CO.Proposal, main, crea: bool) -> str:
        """«proceres-130» (130 mm) si ya existe; «línea nueva «proceres-130» (130 mm)» si se va a crear."""
        name, height = "", None
        if crea and main is not None and main.linea:
            name, height = main.linea.get("nombre", ""), main.linea.get("altura_mm")
        else:
            local = self.state._local_line(prop.root)
            if local is not None:
                name, height = local
        if not name:
            return tr("install_confirm.destino_sin_linea")
        text = f"«{name}»" + (f" ({height:g} mm)" if isinstance(height, (int, float)) and not isinstance(height, bool) else "")
        return tr("install_confirm.destino_nueva", linea=text) if crea else text

    # ------------------------------------------------------------------------------------------------------------------ instalar
    def task_install(self, *, acepto: bool = False, notify=None) -> CO.Task:
        """Escribe el catálogo. **Sin `acepto=True` no escribe nada.** Todo o nada: un fallo deja el catálogo como estaba (etapa `FALLO`).
        Al terminar mide `verificar` y la `cobertura` de las piezas nuevas (`report`)."""
        def work(task: CO.Task):
            prop = self.proposal
            if prop is None or self.stage not in (CONFIRMANDO, FALLO):
                raise I.InstallError(tr("install_confirm.sin_propuesta"))
            if not acepto:                                   # sin confirmar no se llega a escribir
                raise I.InstallError(tr("catalog_online.sin_confirmar"))
            c = self.confirmation
            if c is not None and c.pide_origen and not self.confirmar_origen:
                raise I.InstallError(tr("catalog_online.cambia_origen"))
            self.stage, self.error = INSTALANDO, ""
            task.progress(0, 0, tr("catalog_online.instalando"))
            task.lock_cancel()
            try:
                results = self._write(prop)
            except Exception as exc:                         # noqa: BLE001 — todo o nada: el catálogo quedó como estaba
                self.stage, self.error = FALLO, str(exc)
                raise
            task.set_text(tr("install_confirm.verificando"))
            self.report = self._report(prop, results)
            self.stage = LISTO
            return self.report
        return CO.Task(work, notify=notify, name="afm-instalar")

    def _report(self, prop: CO.Proposal, results: list) -> Report:
        """El informe: lo instalado, `verificar` del catálogo y `cobertura` de las piezas nuevas. Una falla al medir no deshace nada: se informa."""
        rep = Report(results, self.state.result_lines(results), carpeta=prop.root)
        try:
            cat = Catalog.open(prop.root)
        except (CatalogError, OSError, KeyError, ValueError) as exc:
            rep.verificar, rep.verificar_ok = [tr("install_confirm.no_se_pudo_verificar", error=str(exc))], False
            return rep
        try:
            v = OPS.verify(cat)
            rep.verificar, rep.verificar_ok = list(v.lines), v.ok
        except Exception as exc:                             # noqa: BLE001
            rep.verificar, rep.verificar_ok = [tr("install_confirm.no_se_pudo_verificar", error=str(exc))], False
        refs = [ref for r in results for ref in r.piezas]
        for ref in refs[:MAX_COBERTURA]:
            try:
                rep.cobertura.append(f"{ref}:")
                rep.cobertura += OPS.coverage_lines(cat, ref)
            except Exception as exc:                         # noqa: BLE001
                rep.cobertura.append(f"  {tr('install_confirm.no_se_pudo_cobertura', error=str(exc))}")
        if len(refs) > MAX_COBERTURA:
            rep.cobertura.append(tr("install_confirm.cobertura_resto", n=len(refs) - MAX_COBERTURA))
        return rep


__all__ = ["InstallFlow", "Confirmation", "Section", "Report", "INICIO", "PROPONIENDO", "CONFIRMANDO", "INSTALANDO", "LISTO", "FALLO", "CANCELADO", "MAX_COBERTURA"]
