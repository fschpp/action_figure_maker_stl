"""Dependencias, instalación en cadena y auditoría de retiradas y bloqueados (plan de catálogos en línea, §5.5, §5.9 y D16).

Tres cosas, todas sin GTK y sin escribir nada salvo `install_chain`:

* `resolve(indices, raiz, pedidos)` decide **qué hay que instalar** para que un paquete (o varios) quede completo: sigue `requiere` por el índice
  (sin bajar nada), elige la versión más nueva que cumple el rango y no está retirada, bloqueada ni en prerelease, respeta **una sola versión por
  `id`**, detecta ciclos, `conflicts` (en los dos sentidos, también contra lo ya instalado), paquetes de otra línea y dependencias que ningún
  índice tiene. Devuelve un `DepPlan` con los pasos **en orden de instalación** (las dependencias primero) y los hallazgos. Con un solo error
  no se instala nada.
* `plan_chain` baja los zips de cada paso (verificados con el `sha256` del índice, por `remoto.download_package`) y arma el plan de cada uno; `install_chain`
  los aplica **todos o ninguno**: un único diario (`instalar._Journal`) cubre la cadena entera, así que si el paso 3 falla el catálogo queda como estaba
  antes del paso 1 (y si la línea la creó el paso 1, la carpeta se borra). Cada paso se vuelve a planificar justo antes de aplicarlo, contra lo que el
  paso anterior ya escribió (paleta, registro), para que no se pise nada.
* `audit(indices, raiz)` compara lo **ya instalado** con las retiradas y los bloqueos del índice y devuelve alertas. **No borra nada** (§5.5: «se avisa,
  no se borra»).

Lo que el índice sabe de `requiere` y `conflicts` es lo de la **última** versión del paquete; para una versión vieja el zip manda: `plan_install` avisa si
el `paquete.json` pide algo que no está instalado.

Módulo de motor puro: sin GTK, sin `manifold3d`; la red solo la usa `plan_chain` (vía `remoto.download_package`).
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

from . import __version__
from . import instalar as I
from . import paquete as Q
from . import remoto as R
from .catalog import Catalog, CatalogError


# ------------------------------------------------------------------------------------------------------------------------ resolver


@dataclass
class Step:
    """Un paquete a instalar: `por` = «pedido» o «dependencia de <id>»."""

    entry: R.Entry
    release: R.Release
    por: str = "pedido"


@dataclass
class DepPlan:
    raiz: str
    pasos: list = field(default_factory=list)          # `Step`, dependencias primero
    issues: list = field(default_factory=list)         # `instalar.Issue`
    linea: str = ""                                    # la línea en la que se instala todo

    @property
    def errors(self) -> list:
        return [i for i in self.issues if i.severity == I.ERROR]

    @property
    def warnings(self) -> list:
        return [i for i in self.issues if i.severity == I.WARNING]

    @property
    def ok(self) -> bool:
        return bool(self.pasos) and not self.errors

    def describe(self) -> list:
        out = []
        for n, s in enumerate(self.pasos, 1):
            out.append(f"{n}. {s.entry.folder} {s.release.version}" + ("" if s.por == "pedido" else f"  ({s.por})"))
        out += [str(i) for i in self.issues]
        return out


def _candidates(indexes, dep: Q.Dependency) -> list:
    found = [e for idx in indexes for e in idx.entries if e.id == dep.id]
    if dep.github:
        found = [e for e in found if e.github.casefold() == dep.github.casefold()]
    return found


def _releases(entry: R.Entry) -> list:
    seen, out = set(), []
    for r in ([entry.latest] if entry.latest else []) + list(entry.versiones):
        if r.version not in seen:
            seen.add(r.version)
            out.append(r)
    return out


def best_release(entry: R.Entry, dep_range: str = "*", *, allow_retirada: bool = False, allow_prerelease: bool = False) -> tuple:
    """(la versión más nueva que cumple `dep_range`, por qué no hubo ninguna). Descarta retiradas, bloqueadas y prerelease salvo que se pida."""
    rng = Q.parse_range(dep_range)
    reasons = []
    best = None
    for r in _releases(entry):
        if not rng.satisfies(r.version):
            continue
        if entry.block_of(r):
            reasons.append(f"{r.version}: {entry.block_of(r)}")
            continue
        if r.version in entry.retiradas and not allow_retirada:
            reasons.append(f"{r.version}: retirada")
            continue
        if r.prerelease and not allow_prerelease:
            reasons.append(f"{r.version}: prerelease")
            continue
        if best is None or Q.parse_semver(r.version) > Q.parse_semver(best.version):
            best = r
    if best is not None:
        return best, ""
    if reasons:
        return None, "las versiones que cumplen no se pueden instalar (" + "; ".join(reasons) + ")"
    have = ", ".join(r.version for r in _releases(entry)) or "ninguna instalable"
    return None, f"ninguna versión cumple {dep_range} (el índice tiene: {have})"


def resolve(indexes, raiz: str, pedidos, *, allow_retirada: bool = False, allow_prerelease: bool = False) -> DepPlan:
    """Qué instalar en `raiz` para los `pedidos` = `[(Entry, versión | None)]`. No baja ni escribe nada. Ver el docstring del módulo."""
    raiz = os.path.abspath(raiz)
    plan = DepPlan(raiz=raiz)

    def add(sev, subject, msg, fix=""):
        plan.issues.append(I.Issue(sev, subject, msg, fix))

    try:
        installed = I.load_registry(raiz)["paquetes"]
    except I.InstallError as exc:
        add(I.ERROR, I.REGISTRY_FILE, str(exc), "arreglalo o movelo antes de instalar")
        return plan
    try:
        line_name = Catalog.open(raiz).name if os.path.isfile(os.path.join(raiz, "linea.json")) else ""
    except CatalogError:
        line_name = ""
    chosen: dict = {}                                   # id → Step (ya resuelto)
    stack: list = []                                    # ids en curso (ciclos)
    order: list = []

    def usable(entry, rel, por) -> bool:
        why = entry.block_of(rel)
        if why:
            add(I.ERROR, entry.id, f"no se puede instalar: {why}", "")
            return False
        if rel.version in entry.retiradas and not allow_retirada:
            add(I.ERROR, entry.id, f"la versión {rel.version} fue retirada por sus responsables", "elegí otra versión")
            return False
        if not entry.visible:
            add(I.ERROR, entry.id, "figura como inaccesible (su repo o su Release no responden)", "probá más tarde")
            return False
        return True

    def visit(entry, rel, por) -> None:
        prev = chosen.get(entry.id)
        if prev is not None:
            if prev.release.version != rel.version:
                add(I.ERROR, entry.id, f"se pidieron dos versiones distintas ({prev.release.version} y {rel.version}); se instala una sola versión por id",
                    "ajustá el rango o instalá una por vez")
            return
        if not usable(entry, rel, por):
            return
        if entry.motor:
            try:
                if not Q.parse_range(entry.motor).satisfies(__version__.split("+")[0]):
                    add(I.WARNING, entry.id, f"pide el motor {entry.motor} y este es {__version__}", "actualizá afm")
            except Q.PaqueteError:
                pass
        stack.append(entry.id)
        for text in entry.requiere:
            try:
                dep = Q.parse_dependency(text)
            except Q.PaqueteError as exc:
                add(I.ERROR, entry.id, f"su requiere «{text}» no se entiende: {exc}", "")
                continue
            satisfy(dep, entry)
        stack.pop()
        if entry.id not in chosen:
            step = Step(entry, rel, por)
            chosen[entry.id] = step
            order.append(step)

    def satisfy(dep: Q.Dependency, who: R.Entry) -> None:
        por = f"dependencia de {who.id}"
        step = chosen.get(dep.id)
        if step is not None:                                                # ya elegida: tiene que cumplir este rango también
            if not dep.satisfied_by(step.release.version):
                add(I.ERROR, dep.id, f"«{who.id}» pide {dep} y ya se eligió la {step.release.version}; se instala una sola versión por id", "")
            return
        if dep.id in stack:
            add(I.ERROR, dep.id, "ciclo de dependencias: " + " → ".join([*stack[stack.index(dep.id):], dep.id]), "avisá a quien mantiene los paquetes")
            return
        got = installed.get(dep.id)
        if got is not None and dep.satisfied_by(got.get("version", "0.0.0")):
            return                                                          # ya está y alcanza
        cands = _candidates(indexes, dep)
        if not cands:
            hint = f" (con la pista {dep.github})" if dep.github else ""
            add(I.ERROR, dep.id, f"«{who.id}» requiere «{dep}»{hint} y ningún índice consultado lo tiene", "agregá el índice que lo publica o instalalo desde un zip")
            return
        if len(cands) > 1:
            add(I.ERROR, dep.id, f"«{dep.id}» está en más de un lugar ({', '.join(f'{e.folder} de {e.indice}' for e in cands)})",
                f"agregá la pista de repo al requiere: {dep.id}@{dep.range}#owner/repo")
            return
        entry = cands[0]
        rel, why = best_release(entry, dep.range, allow_retirada=allow_retirada, allow_prerelease=allow_prerelease)
        if rel is None:
            add(I.ERROR, dep.id, f"«{who.id}» requiere «{dep}»: {why}", "")
            return
        if got is not None:
            add(I.WARNING, dep.id, f"está instalada la {got.get('version')} y «{who.id}» pide {dep}: se instala la {rel.version} (aditivo, no toca lo que usan tus recetas)")
        visit(entry, rel, por)

    for entry, version in pedidos:
        rel = entry.release(version)
        if rel is None:
            add(I.ERROR, entry.id, f"no tiene {'la versión ' + version if version else 'una versión instalable'} en el índice", "")
            continue
        visit(entry, rel, "pedido")
    plan.pasos = order

    # ---- una sola línea para todo (el alcance de un paquete es su línea)
    lines = {}
    for s in plan.pasos:
        name = (s.entry.linea or {}).get("nombre", "")
        if name:
            lines.setdefault(name, []).append(s.entry.id)
    target = line_name or (next(iter(lines)) if lines else "")
    plan.linea = target
    for name, ids in lines.items():
        if target and name != target:
            add(I.ERROR, ", ".join(ids), f"pertenece a la línea «{name}» y el destino es la línea «{target}»",
                "instalalo en otro catálogo (--en) o como línea nueva (--como-linea)")

    # ---- conflicts, en los dos sentidos, entre lo que queda instalado y lo que se agrega
    final = {pid: (got.get("version", "0.0.0"), list(got.get("conflicts", [])), False) for pid, got in installed.items()}
    for s in plan.pasos:
        final[s.entry.id] = (s.release.version, list(s.entry.conflicts), True)
    seen = set()
    for a, (va, confs, nuevo_a) in sorted(final.items()):
        for text in confs:
            try:
                dep = Q.parse_dependency(text)
            except Q.PaqueteError:
                continue
            other = final.get(dep.id)
            if other is None or dep.id == a or not dep.satisfied_by(other[0]) or not (nuevo_a or other[2]):
                continue                                                    # un choque que ya había entre dos instalados no es de esta instalación
            key = tuple(sorted((a, dep.id)))
            if key in seen:
                continue
            seen.add(key)
            add(I.ERROR, a, f"no convive con «{dep.id}» {other[0]} (conflicts: {text}); «{a}» queda en la {va}", "quitá uno de los dos o elegí otras versiones")
    return plan


# ------------------------------------------------------------------------------------------------------------------------ cadena


@dataclass
class Chain:
    raiz: str
    pasos: list                                         # `Step`
    zips: list                                          # ruta del zip bajado de cada paso
    plans: list                                         # `InstallPlan` de vista previa (contra el disco de ahora)
    como_linea: bool = False
    allow_fbx: bool = False

    @property
    def errors(self) -> list:
        return [i for p in self.plans for i in p.errors]

    @property
    def ok(self) -> bool:
        return bool(self.plans) and all(p.ok for p in self.plans)

    @property
    def nada_que_hacer(self) -> bool:
        return all(p.nada_que_hacer for p in self.plans)

    @property
    def cambia_origen(self) -> bool:
        return any(p.cambia_origen for p in self.plans)

    def describe(self) -> list:
        out = []
        for s, p in zip(self.pasos, self.plans):
            out.append(f"── {s.entry.folder} {s.release.version}" + ("" if s.por == "pedido" else f"  ({s.por})"))
            out += ["   " + l for l in p.describe()]
        return out


def plan_chain(dep: DepPlan, *, como_linea: bool = False, allow_fbx: bool = False, allow_retirada: bool = False, **net) -> Chain:
    """Baja (verificado) y planifica cada paso de `dep`. **No escribe en el catálogo.** `RemoteError` si un zip no coincide con el índice."""
    if dep.errors or not dep.pasos:
        raise I.InstallError("las dependencias tienen errores: " + "; ".join(str(i) for i in dep.errors[:3]))
    ids = {s.entry.id: s.release.version for s in dep.pasos}
    zips, plans = [], []
    for n, s in enumerate(dep.pasos):
        path = R.download_package(s.entry, version=s.release.version, allow_retirada=allow_retirada, **net)
        plan = I.plan_install(path, dep.raiz, como_linea=como_linea and n == 0, allow_fbx=allow_fbx)
        pkg = plan.paquete
        if pkg is not None:
            if pkg.id != s.entry.id:
                plan.issues.append(I.Issue(I.ERROR, s.entry.id, f"el zip trae el paquete «{pkg.id}» y el índice lo lista como «{s.entry.id}»", "no se instala: avisá a quien mantiene el índice"))
            if pkg.version != s.release.version:
                plan.issues.append(I.Issue(I.ERROR, s.entry.id, f"el zip es la versión {pkg.version} y el índice dice {s.release.version}", "no se instala: probá más tarde"))
            # lo que esta cadena instala antes no es una falta: se quita el aviso «requiere X y no está instalado»
            for text in pkg.requiere:
                try:
                    d = Q.parse_dependency(text)
                except Q.PaqueteError:
                    continue
                if d.id in ids and d.satisfied_by(ids[d.id]):
                    plan.issues = [i for i in plan.issues if i.message != f"requiere «{text}» y no está instalado"]
        plan.indice = s.entry.indice
        zips.append(path)
        plans.append(plan)
    return Chain(raiz=dep.raiz, pasos=list(dep.pasos), zips=zips, plans=plans, como_linea=como_linea, allow_fbx=allow_fbx)


def install_chain(chain: Chain, *, confirmar_origen: bool = False, now=None) -> list:
    """Aplica la cadena **toda o ninguna**; devuelve los `InstallResult`. Ante cualquier fallo el catálogo queda idéntico al de antes."""
    if not chain.ok:
        raise I.InstallError("la cadena tiene errores: " + "; ".join(str(i) for i in chain.errors[:3]))
    if chain.cambia_origen and not confirmar_origen:
        raise I.InstallError("un paquete cambió de origen respecto de lo instalado; hace falta confirmarlo explícitamente")
    root = chain.raiz
    created_root = not os.path.exists(root)
    journal = I._Journal(root)
    results = []
    try:
        for n, (s, path) in enumerate(zip(chain.pasos, chain.zips)):
            plan = I.plan_install(path, root, como_linea=chain.como_linea and n == 0, allow_fbx=chain.allow_fbx)      # contra lo que el paso anterior ya escribió
            plan.indice = s.entry.indice
            if not plan.ok:
                raise I.InstallError(f"{s.entry.id}: " + "; ".join(str(i) for i in plan.errors[:3]))
            if plan.nada_que_hacer:
                continue
            results.append(I.apply(plan, confirmar_origen=confirmar_origen, now=now, journal=journal))
    except BaseException:
        journal.rollback()
        if created_root:
            shutil.rmtree(root, ignore_errors=True)
        raise
    return results


# ------------------------------------------------------------------------------------------------------------------------ auditoría


@dataclass(frozen=True)
class Alert:
    kind: str                           # retirada | bloqueado
    paquete: str
    version: str
    message: str

    def __str__(self):
        quien = f"{self.paquete} {self.version}".strip() or "(sin paquete)"
        return f"{'BLOQUEADO' if self.kind == 'bloqueado' else 'RETIRADA'} {quien}: {self.message}"


def audit(indexes, raiz: str) -> list:
    """Alertas sobre lo **instalado** en `raiz`: versiones que el índice marcó como retiradas y paquetes bloqueados (por `id`, autor o `sha256`).

    Se compara en local con lo que el índice ya trae (no se envía nada). **No borra ni cambia nada**: la persona decide (`remoto actualizar`, quitar)."""
    reg = I.load_registry(raiz)["paquetes"]
    blocks = [b for idx in indexes for b in idx.bloqueados]
    out = []
    for pid, got in sorted(reg.items()):
        have = got.get("version", "")
        owners = {a.get("github", "").casefold() for a in got.get("autores", []) if isinstance(a, dict)} | {(got.get("github", "").split("/")[0]).casefold()}
        hashes = {h for h in (got.get("sha256"), got.get("heredado_sha256")) if h}
        for b in blocks:
            hit = (b.id and b.id == pid) or (b.autor and b.autor.casefold() in owners) or (b.sha256 and b.sha256 in hashes)
            if hit:
                out.append(Alert("bloqueado", pid, have, b.describe() + "; sigue instalado, quitalo o reemplazalo si corresponde"))
                break
        for idx in indexes:
            for e in idx.entries:
                if e.id != pid or (got.get("github") and e.github and got["github"] != e.github):
                    continue
                if have in e.retiradas:
                    better, _ = best_release(e)
                    fix = f"hay una versión mejor ({better.version}): `afm catalogo remoto actualizar {pid}`" if better and Q.parse_semver(better.version) > Q.parse_semver(have) else "no hay una versión mejor publicada"
                    out.append(Alert("retirada", pid, have, f"sus responsables la retiraron; {fix}"))
    from .bloqueos import piezas_bloqueadas     # D24: el `sha256` de cada pieza instalada contra la lista, aunque el paquete se haya importado a mano

    for p in piezas_bloqueadas(raiz, blocks):
        out.append(Alert("bloqueado", p.paquete, p.version, p.mensaje()))
    seen, uniq = set(), []
    for a in out:
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq


__all__ = ["Alert", "Chain", "DepPlan", "Step", "audit", "best_release", "install_chain", "plan_chain", "resolve"]
