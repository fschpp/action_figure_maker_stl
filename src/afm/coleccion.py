"""Colecciones (plan de catálogos en línea, §5.12 y D34): un paquete `tipo: coleccion` que no trae piezas, solo **fija** otros paquetes a versión y
`sha256` exactos, en el orden en que se instalan.

El formato (`contenido.pines`) y el lint estático (CK402/CK403) ya están en `paquete.py`; este módulo agrega lo que necesita un índice:

* `comprobar_pines(indices, pkg)` — CK402 contra el índice: cada pin **existe**, es de ese repo, la versión existe, **su `sha256` coincide con el del
  Release** y no está retirado, bloqueado ni inaccesible; las líneas de los pines coinciden entre sí. Lo usa el CI del índice y la app.
* `resolver(indices, pkg, raiz)` — lo mismo, pero devuelve un `dependencias.DepPlan` con un `Step` por pin (en el orden de la colección).
* `plan(indices, pkg, raiz)` — baja (verificado) y planifica cada pin con `dependencias.plan_chain`: **el mismo camino que un `.zip` suelto**, un solo
  plan con los avisos y conflictos de todos y una sola confirmación. Instalarlo es `dependencias.install_chain`: todo o nada (ante cualquier fallo el
  catálogo queda idéntico).

Una colección **no se instala a sí misma** (no tiene archivos): no figura en `paquetes.json`; lo que queda son los paquetes que fija. Una versión nueva
de la colección es un paquete nuevo en el índice: actualizar es aditivo como cualquier otro (§5.9).

Módulo de motor puro: sin GTK.
"""
from __future__ import annotations

import os

from . import dependencias as D
from . import instalar as I
from . import paquete as Q
from . import remoto as R
from . import validar as V


def cargar(zip_path: str) -> Q.Paquete:
    """La colección de un `.zip` ya bajado (o local), con su lint estático (CK402/CK403, manifiesto, hashes). `InstallError` si no sirve."""
    res = V.validate_package(zip_path, verify_catalog=False)
    pkg = res.paquete
    if pkg is None or not res.ok:
        raise I.InstallError("la colección no es válida: " + "; ".join(f.message for f in res.errors[:3]))
    if pkg.tipo != "coleccion":
        raise I.InstallError(f"«{pkg.id}» es un paquete de tipo «{pkg.tipo}», no una colección")
    return pkg


def _find(indices, pin: Q.Pin, add) -> R.Entry | None:
    """La entrada que el pin nombra (por `id`, y por repo si el índice lo dice); error claro si no está o es ambigua."""
    found = [e for idx in indices for e in idx.entries if e.id == pin.id]
    if not found:
        add(pin.id, f"el pin «{pin.id}» no está en los índices consultados", "¿se retiró del índice? sin él la colección no se puede instalar")
        return None
    same = [e for e in found if not e.github or e.github == pin.github]
    if not same:
        add(pin.id, f"el índice liga «{pin.id}» a «{found[0].github}» y la colección lo fija en «{pin.github}»", "puede ser otro paquete con el mismo id: no se instala")
        return None
    if len(same) > 1:
        add(pin.id, f"«{pin.id}» está en más de un índice ({', '.join(e.indice for e in same)})", "agregá solo el índice del que sale la colección")
        return None
    return same[0]


def _check(indices, pkg: Q.Paquete, *, allow_retirada: bool) -> tuple[list, list]:
    """`(issues, pasos)`: los hallazgos (todos, no solo el primero) y un `Step` por pin que se resolvió."""
    issues: list = []
    steps: list = []

    def add(subject, msg, fix=""):
        issues.append(I.Issue(I.ERROR, subject, msg, fix))

    lines: dict = {}
    for pin in pkg.contenido.pines:
        entry = _find(indices, pin, add)
        if entry is None:
            continue
        rel = entry.release(pin.version)
        if rel is None:
            add(pin.id, f"el índice no tiene la versión {pin.version} de «{pin.id}»", "la colección fija una versión que ya no está publicada")
            continue
        if rel.sha256 != pin.sha256:
            add(pin.id, f"el sha256 de «{pin.id}» {pin.version} no coincide: la colección dice {pin.sha256[:12]}… y el Release publicado es {rel.sha256[:12]}…",
                "el zip cambió después de fijarlo: no se instala (avisá a quien mantiene la colección)")
            continue
        why = entry.block_of(rel)
        if why:
            add(pin.id, f"«{pin.id}» {pin.version} no se puede instalar: {why}", "un pin bloqueado deja a la colección fuera")
            continue
        if pin.version in entry.retiradas and not allow_retirada:
            add(pin.id, f"la versión {pin.version} de «{pin.id}» fue retirada por sus responsables", "un pin retirado deja a la colección fuera")
            continue
        if not entry.visible:
            add(pin.id, f"«{pin.id}» figura como inaccesible (su repo o su Release no responden)", "probá más tarde")
            continue
        name = (entry.linea or {}).get("nombre")
        if isinstance(name, str) and name:
            lines.setdefault(name, []).append(pin.id)
        steps.append(D.Step(entry, rel, por=f"colección {pkg.id}"))
    if len(lines) > 1:
        detail = "; ".join(f"{n}: {', '.join(ids)}" for n, ids in sorted(lines.items()))
        add(pkg.id, f"los pines son de líneas distintas ({detail})", "una colección fija paquetes de una sola línea")
    return issues, steps


def comprobar_pines(indices, pkg: Q.Paquete, *, allow_retirada: bool = False) -> list:
    """CK402 contra el índice: los `instalar.Issue` (todos de severidad error) que impiden instalar la colección; vacío si cada pin está bien."""
    return _check(indices, pkg, allow_retirada=allow_retirada)[0]


def linea_de(pasos) -> str:
    """El nombre de la línea de los pines (los que no la dicen no cuentan); \"\" si ninguno la dice."""
    for s in pasos:
        name = (s.entry.linea or {}).get("nombre")
        if isinstance(name, str) and name:
            return name
    return ""


def resolver(indices, pkg: Q.Paquete, raiz: str | None = None, *, allow_retirada: bool = False) -> D.DepPlan:
    """Un `DepPlan` con un paso por pin, en el orden de la colección. Sin `raiz` se usa la carpeta de la línea de los pines. No baja ni escribe nada."""
    issues, steps = _check(indices, pkg, allow_retirada=allow_retirada)
    line = linea_de(steps)
    if raiz is None:
        raiz = I.explicit_root(line) if line else ""
        if not raiz and not issues:
            issues.append(I.Issue(I.ERROR, pkg.id, "los pines no dicen su línea: indicá la carpeta de destino", "--en RUTA"))
    plan = D.DepPlan(raiz=os.path.abspath(raiz) if raiz else "", pasos=steps, issues=issues, linea=line)
    return plan


def plan(indices, pkg: Q.Paquete, raiz: str | None = None, *, como_linea: bool = False, allow_fbx: bool = False, allow_retirada: bool = False, **net):
    """`(DepPlan, Chain | None)`: baja y planifica todos los pines. La cadena es `None` si ya hay errores al resolver (no se baja nada). No escribe."""
    dep = resolver(indices, pkg, raiz, allow_retirada=allow_retirada)
    if dep.errors:
        return dep, None
    return dep, D.plan_chain(dep, como_linea=como_linea, allow_fbx=allow_fbx, allow_retirada=allow_retirada, **net)


__all__ = ["cargar", "comprobar_pines", "resolver", "plan", "linea_de"]
