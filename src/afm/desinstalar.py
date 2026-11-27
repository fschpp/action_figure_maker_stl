"""Quitar un paquete instalado (plan de catálogos en línea, §5.4 y D20): `plan_remove` no escribe; `apply_remove` es todo o nada.

Se quita **solo lo que el paquete agregó** (lo que dice `paquetes.json`) y **nada que otra cosa siga usando**:

* una pieza que otro paquete también instaló (mismo `id@n`, mismo hash: «omitida» al instalar) se **conserva**;
* una pieza que usa una receta (o un `.proyecto` de las carpetas que se pidió revisar) que **no se va con el paquete** se **conserva**, y el
  plan dice cuál la usa: así no queda ninguna receta rota sin aviso;
* una receta que el paquete trajo se quita, salvo que otro paquete también la haya instalado;
* los **roles de paleta** que el paquete agregó se conservan (la paleta es de la línea, no del paquete; quitar un rol puede dejar sin color
  a piezas de otras personas).

Lo quitado **no se borra**: las piezas van a `.papelera/` (`Catalog.delete_pieces`, se recuperan con `restore_deleted`) y las recetas a
`.papelera/recetas/<fecha>-<id>/figuras/`. Ante cualquier fallo se deshace todo (piezas, recetas, índice y registro). Los paquetes que
**requieren** a este y siguen instalados no frenan el plan, pero hacen falta una confirmación explícita (`confirmar_dependientes`).

Módulo de motor puro: sin GTK, sin red, sin `manifold3d`.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
from dataclasses import dataclass, field

from . import instalar as I
from . import parts as P
from . import paquete as Q
from .catalog import TRASH_DIR, Catalog, CatalogError

RECIPES_TRASH = "recetas"                       # `.papelera/recetas/<fecha>-<id>/figuras/…`: fuera de los lotes de piezas (no los toca `restore_deleted`)
REMOVED_FILE = "quitado.json"


@dataclass(frozen=True)
class Kept:
    """Algo que el paquete había agregado y que **se queda** en el catálogo, con el motivo."""

    kind: str                                   # pieza | receta
    name: str
    why: str

    def __str__(self):
        return f"{self.kind} {self.name}: {self.why}"


@dataclass
class RemovePlan:
    root: str
    id: str
    version: str = ""
    nombre: str = ""
    piezas: list = field(default_factory=list)        # `id@n` que se van a la papelera
    recetas: list = field(default_factory=list)       # nombres de `figuras/<nombre>.receta.json` que se van a la papelera
    conservadas: list = field(default_factory=list)   # `Kept`
    roles: list = field(default_factory=list)         # roles de paleta que agregó el paquete: se conservan
    dependientes: list = field(default_factory=list)  # textos: paquetes instalados que requieren a este
    avisos: list = field(default_factory=list)
    errores: list = field(default_factory=list)
    registro_sha: str = ""                            # del `paquetes.json` al planificar (si cambió, hay que volver a planificar)

    @property
    def ok(self) -> bool:
        return not self.errores

    @property
    def necesita_confirmar_dependientes(self) -> bool:
        return bool(self.dependientes)

    def describe(self) -> list[str]:
        """Líneas para consola o diálogo."""
        out = [f"Quitar «{self.id}» {self.version}".rstrip()]
        out.append(f"  se van a la papelera: {len(self.piezas)} pieza(s), {len(self.recetas)} receta(s)")
        out += [f"    pieza {r}" for r in self.piezas] + [f"    receta {n}" for n in self.recetas]
        if self.conservadas:
            out.append(f"  se conservan ({len(self.conservadas)}):")
            out += [f"    {k}" for k in self.conservadas]
        out += [f"  AVISO: {a}" for a in self.avisos]
        out += [f"  REQUIERE ESTE PAQUETE: {d}" for d in self.dependientes]
        out += [f"  ERROR: {e}" for e in self.errores]
        return out


@dataclass
class RemoveResult:
    id: str
    piezas: list
    recetas: list
    conservadas: list
    papelera_piezas: str = ""                         # carpeta del lote en `.papelera/` ("" si no había piezas)
    papelera_recetas: str = ""
    avisos: list = field(default_factory=list)


def _file_sha(path: str) -> str:
    return P.sha256_file(path) if os.path.isfile(path) else ""


def plan_remove(root: str, pkg_id: str, *, search=()) -> RemovePlan:
    """Qué se quitaría de `root` al desinstalar `pkg_id`. No escribe nada. `search`: carpetas con `.proyecto` que también cuentan como usos."""
    reg = I.load_registry(root)
    got = reg["paquetes"].get(pkg_id)
    if not isinstance(got, dict):
        raise I.InstallError(f"«{pkg_id}» no figura como instalado en «{root}»")
    plan = RemovePlan(root=root, id=pkg_id, version=str(got.get("version", "")), nombre=str(got.get("nombre", "")),
                      registro_sha=_file_sha(os.path.join(root, I.REGISTRY_FILE)))
    try:
        cat = Catalog.open(root)
    except CatalogError as exc:
        plan.errores.append(str(exc))
        return plan
    refs = got.get("refs") if isinstance(got.get("refs"), dict) else {}
    others = {oid: o for oid, o in reg["paquetes"].items() if oid != pkg_id and isinstance(o, dict)}

    def owner_of(kind: str, name: str) -> str:
        for oid, o in sorted(others.items()):
            if name in ((o.get("refs") or {}).get(kind) or []):
                return oid
        return ""

    # ---- recetas del paquete
    gone_paths: set[str] = set()
    for name in refs.get("recetas", []):
        path = os.path.join(root, "figuras", f"{name}.receta.json")
        if not os.path.isfile(path):
            plan.avisos.append(f"la receta «{name}» ya no está en el catálogo (se borró a mano)")
            continue
        other = owner_of("recetas", name)
        if other:
            plan.conservadas.append(Kept("receta", name, f"también la instaló «{other}»"))
            continue
        plan.recetas.append(name)
        gone_paths.add(os.path.abspath(path))

    # ---- piezas del paquete
    for ref in refs.get("piezas", []):
        try:
            cat.meta(ref)
        except CatalogError:
            plan.avisos.append(f"la pieza «{ref}» ya no está en el catálogo (se borró a mano)")
            continue
        other = owner_of("piezas", ref)
        if other:
            plan.conservadas.append(Kept("pieza", ref, f"también la instaló «{other}»"))
            continue
        blockers = [u for u in cat.usages(ref, search=list(search)) if os.path.abspath(u["archivo"]) not in gone_paths]
        if blockers:
            u = blockers[0]
            more = f" (y {len(blockers) - 1} más)" if len(blockers) > 1 else ""
            plan.conservadas.append(Kept("pieza", ref, f"la usa «{os.path.basename(u['archivo'])}» en «{u['lugar']}»{more}"))
            continue
        plan.piezas.append(ref)

    plan.roles = list(refs.get("roles", []))
    if plan.roles:
        plan.avisos.append(f"los roles de paleta que agregó el paquete ({', '.join(plan.roles)}) se conservan: la paleta es de la línea")
    if plan.piezas and not search:
        plan.avisos.append("no se revisaron los archivos .proyecto: una figura guardada que use estas piezas va a pedirlas al abrirse")
    if plan.conservadas:
        plan.avisos.append("lo que se conserva queda en el catálogo como propio: ya no figura en ningún paquete")

    # ---- quién lo necesita (`requiere` se guarda en el registro desde D20; los registros viejos no lo tienen)
    for oid, o in sorted(others.items()):
        for text in o.get("requiere", []) or []:
            try:
                dep = Q.parse_dependency(text)
            except Q.PaqueteError:
                continue
            if dep.id == pkg_id:
                plan.dependientes.append(f"«{oid}» {o.get('version', '')} requiere «{text}»")
    return plan


def apply_remove(plan: RemovePlan, *, confirmar_dependientes: bool = False, now: datetime.datetime | None = None) -> RemoveResult:
    """Quita lo que dice `plan` (ver el docstring del módulo). Todo o nada: ante cualquier fallo el catálogo queda como estaba."""
    if not plan.ok:
        raise I.InstallError("el plan tiene errores: " + "; ".join(plan.errores[:3]))
    if plan.dependientes and not confirmar_dependientes:
        raise I.InstallError("otros paquetes instalados requieren este; hace falta confirmarlo explícitamente: " + "; ".join(plan.dependientes))
    root = plan.root
    reg_path = os.path.join(root, I.REGISTRY_FILE)
    if _file_sha(reg_path) != plan.registro_sha:
        raise I.InstallError("el registro de paquetes cambió desde que se armó el plan; volvé a planificar")
    reg = I.load_registry(root)
    if plan.id not in reg["paquetes"]:
        raise I.InstallError(f"«{plan.id}» ya no figura como instalado")
    cat = Catalog.open(root)
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d-%H%M%S-%f")
    journal = I._Journal(root)
    batch = ""
    moved_recipes: list[tuple[str, str]] = []                       # (origen, destino en la papelera)
    rfolder = os.path.join(root, TRASH_DIR, RECIPES_TRASH, f"{stamp}-{plan.id}")
    try:
        journal.snapshot(os.path.join(root, "CATALOGO.md"))
        journal.snapshot(reg_path)
        if plan.piezas:
            res = cat.delete_pieces(plan.piezas)
            batch = res.trash
            if res.errors:
                raise I.InstallError("no se pudieron quitar todas las piezas: " + "; ".join(res.errors[:3]))
        for name in plan.recetas:
            src = os.path.join(root, "figuras", f"{name}.receta.json")
            dest = os.path.join(rfolder, "figuras", f"{name}.receta.json")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.replace(src, dest)
            moved_recipes.append((src, dest))
        if moved_recipes:
            with open(os.path.join(rfolder, REMOVED_FILE), "w", encoding="utf-8") as f:
                json.dump({"paquete": plan.id, "version": plan.version, "fecha": stamp, "recetas": list(plan.recetas)}, f, indent=2, ensure_ascii=False)
        del reg["paquetes"][plan.id]
        journal.write(reg_path, I._dump(reg))                       # al final: es lo que da la desinstalación por hecha
    except BaseException:
        for src, dest in reversed(moved_recipes):
            if os.path.isfile(dest):
                os.replace(dest, src)
        shutil.rmtree(rfolder, ignore_errors=True)
        if batch:
            try:
                cat.restore_deleted(os.path.basename(batch))
            except CatalogError:
                pass
        journal.rollback()
        raise
    return RemoveResult(id=plan.id, piezas=list(plan.piezas), recetas=list(plan.recetas), conservadas=list(plan.conservadas),
                        papelera_piezas=batch, papelera_recetas=rfolder if moved_recipes else "", avisos=list(plan.avisos))


__all__ = ["Kept", "RemovePlan", "RemoveResult", "apply_remove", "plan_remove"]
