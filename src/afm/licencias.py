"""Licencia efectiva de un armado y `CREDITOS.txt` (plan de catálogos en línea, §5.8 y D23).

Al armar una figura con piezas de varios paquetes, cada pieza trae la licencia de **su** paquete (`paquetes.json`, lo que dejó
`instalar.apply`: autores, licencia, origen, repo y las piezas `id@n` que agregó). Acá se junta todo:

* `usos_de_receta(receta, catálogo)` / `usos_de_figura(...)`: qué piezas (lugar → `id@n`) entran en el armado, accesorios incluidos.
* `fuentes(raiz, usos)`: de qué paquete viene cada una (autor, licencia, origen, enlace). Una pieza que ningún paquete trajo es **del
  propio catálogo** (hecha o importada a mano): se lista como «sin paquete» y no impone nada.
* `efectiva(fuentes)`: la restricción **más fuerte** de todas. Una `NC` ⇒ el resultado es no comercial; una `SA` o `GPL` ⇒ compartir
  igual; una `ND` ⇒ el armado no se comparte; «todos los derechos reservados» ⇒ solo con permiso del autor. Dos licencias de
  «compartir igual» distintas, o una `ND` junto a una `SA`, se avisan como conflicto: no se pueden cumplir a la vez.
* `escribir_creditos(...)` deja `CREDITOS.txt` junto al STL con la licencia efectiva y, por pieza, autor, licencia y enlace;
  `agregar_a_lista_de_piezas(...)` amplía `<figura>_lista_de_piezas.{json,md}` con el mismo bloque.

Es un **resumen automático, no asesoramiento legal**: lo dice el propio archivo. Módulo de motor puro: sin GTK, sin red, sin `manifold3d`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date

from . import paquete as Q
from .i18n import tr

CREDITS_FILE = "CREDITOS.txt"
LICENSE_BLOCK = "licencia"                    # clave del bloque en `<figura>_lista_de_piezas.json`


# ------------------------------------------------------------------------------------------------------------ qué impone una licencia

@dataclass(frozen=True)
class Traits:
    """Lo que una licencia le pide a quien la usa. Sale del identificador SPDX; una licencia que no es de la lista cerrada no impone nada."""

    spdx: str
    conocida: bool = True
    atribucion: bool = False      # hay que nombrar al autor
    nc: bool = False              # solo uso no comercial
    sa: bool = False              # lo derivado se comparte con la misma licencia (CC `SA` y la familia GPL)
    nd: bool = False              # no se puede modificar ni mezclar (CC `ND`)
    reservada: bool = False       # todos los derechos reservados (con permiso de distribución)


def traits(spdx: str | None) -> Traits:
    """`Traits` de un identificador SPDX. «» o desconocida ⇒ `conocida=False` (no se le inventa ninguna restricción)."""
    s = (spdx or "").strip()
    if not s:
        return Traits("", conocida=False)
    if s == Q.ALL_RIGHTS_RESERVED:
        return Traits(s, reservada=True, atribucion=True)
    if s in ("CC0-1.0", "Unlicense"):
        return Traits(s)
    if s.startswith("CC-BY"):
        return Traits(s, atribucion=True, nc="-NC" in s, sa="-SA" in s, nd="-ND" in s)
    if s.startswith(("GPL-", "LGPL-")):
        return Traits(s, atribucion=True, sa=True)
    if s in Q.LICENSES:                                   # MIT, Apache, BSD: piden el crédito y nada más
        return Traits(s, atribucion=True)
    return Traits(s, conocida=False)


# ------------------------------------------------------------------------------------------------------------ de dónde sale cada pieza

@dataclass
class Fuente:
    """Una pieza del armado y el paquete que la trajo."""

    lugar: str                        # torso, brazo, cabeza… o «accesorio»
    ref: str                          # `id@n`
    paquete: str = ""                 # id del paquete; «» = la pieza es del propio catálogo (sin paquete)
    version: str = ""
    nombre: str = ""
    autores: list = field(default_factory=list)      # [{nombre, github?}]
    licencia: str = ""
    origen: str = ""
    github: str = ""                  # `owner/repo` del paquete

    @property
    def sin_paquete(self) -> bool:
        return not self.paquete

    @property
    def url(self) -> str:
        return f"https://github.com/{self.github}" if self.github else ""

    @property
    def traits(self) -> Traits:
        return traits(self.licencia)

    def autores_texto(self) -> str:
        out = []
        for a in self.autores:
            nombre = (a.get("nombre") if isinstance(a, dict) else str(a)) or ""
            gh = (a.get("github") if isinstance(a, dict) else "") or ""
            out.append(f"{nombre} (https://github.com/{gh})" if gh else nombre)
        return ", ".join(x for x in out if x)

    def to_dict(self) -> dict:
        d = {"lugar": self.lugar, "pieza": self.ref, "paquete": self.paquete or None, "version": self.version or None,
             "autores": [dict(a) if isinstance(a, dict) else {"nombre": str(a)} for a in self.autores],
             "licencia": self.licencia or None, "origen": self.origen or None, "enlace": self.url or None}
        return d


def usos_de_receta(recipe, cat) -> list[tuple[str, str]]:
    """`[(lugar, "id@n")]` de las piezas de una receta (sus lugares y sus accesorios). Un `id` sin versión se resuelve a la última, como
    al armar. Una pieza que el catálogo no tiene se deja tal cual: la señala `fuentes` como «sin paquete» y el armado ya la rechaza."""
    out = []
    for lugar in recipe.lugares:
        ref = recipe.lugares.get(lugar)
        if ref:
            out.append((lugar, _resolve(cat, ref)))
    for ref in recipe.accesorios or []:
        out.append(("accesorio", _resolve(cat, ref)))
    return out


def _resolve(cat, ref: str) -> str:
    try:
        return cat.meta(ref).ref
    except Exception:                                      # noqa: BLE001 — pieza que falta o ficha ilegible: se informa la referencia pedida
        return ref


def _registro(raiz: str) -> dict:
    """`paquetes.json` del catálogo; vacío si no existe o no se puede leer (los créditos no frenan el armado)."""
    from .instalar import InstallError, load_registry

    try:
        return load_registry(raiz).get("paquetes") or {}
    except InstallError:
        return {}


def fuentes(raiz: str, usos: list[tuple[str, str]]) -> list[Fuente]:
    """Una `Fuente` por cada `(lugar, ref)`. Si dos paquetes dicen haber traído la misma `id@n`, se toma la de licencia más
    restrictiva (el armado cumple la más exigente). Sin `paquetes.json` ⇒ todo es del catálogo."""
    reg = _registro(raiz)
    dueños: dict[str, list[tuple[str, dict]]] = {}
    for pid in sorted(reg):
        info = reg[pid] if isinstance(reg[pid], dict) else {}
        for ref in ((info.get("refs") or {}).get("piezas") or []):
            dueños.setdefault(ref, []).append((pid, info))
    out = []
    for lugar, ref in usos:
        cand = dueños.get(ref) or []
        if not cand:
            out.append(Fuente(lugar, ref))
            continue
        pid, info = max(cand, key=lambda c: _rigor(traits(c[1].get("licencia"))))
        out.append(Fuente(lugar, ref, paquete=pid, version=str(info.get("version") or ""), nombre=str(info.get("nombre") or ""),
                          autores=[a for a in (info.get("autores") or []) if isinstance(a, dict)], licencia=str(info.get("licencia") or ""),
                          origen=str(info.get("origen") or ""), github=str(info.get("github") or "")))
    return out


def _rigor(t: Traits) -> int:
    return 8 * t.reservada + 4 * t.nd + 2 * t.nc + t.sa


# ------------------------------------------------------------------------------------------------------------ la licencia efectiva

@dataclass
class Efectiva:
    """La restricción más fuerte del armado. `comercial=False` ⇒ no se puede vender; `compartir_igual` ⇒ lo que se comparta va con la
    misma licencia; `sin_derivadas` ⇒ el armado no se comparte; `reservadas` ⇒ hace falta el permiso del autor. `conflictos` = lo que
    no se puede cumplir a la vez. Cada lista de `por_*` dice **qué piezas** lo causan."""

    fuentes: list[Fuente]
    comercial: bool = True
    compartir_igual: bool = False
    sin_derivadas: bool = False
    reservadas: bool = False
    atribucion: bool = False
    por_nc: list[str] = field(default_factory=list)
    por_sa: list[str] = field(default_factory=list)
    por_nd: list[str] = field(default_factory=list)
    por_reservada: list[str] = field(default_factory=list)
    sin_licencia: list[str] = field(default_factory=list)       # piezas con paquete pero sin licencia conocida
    propias: list[str] = field(default_factory=list)            # piezas del catálogo (sin paquete): no imponen nada
    licencias: list[str] = field(default_factory=list)          # las distintas, ordenadas
    conflictos: list[str] = field(default_factory=list)

    @property
    def restringe(self) -> bool:
        """True si hay algo que la persona tiene que saber antes de exportar o compartir (todo menos el simple crédito)."""
        return bool(not self.comercial or self.compartir_igual or self.sin_derivadas or self.reservadas or self.conflictos)

    @property
    def etiqueta(self) -> str:
        """Una línea: lo más fuerte, en el idioma de la interfaz."""
        if not self.restringe:
            return tr("licencias.sin_restricciones") if self.atribucion else tr("licencias.libre")
        partes = []
        if self.reservadas:
            partes.append(tr("licencias.et_reservada"))
        if not self.comercial:
            partes.append(tr("licencias.et_no_comercial"))
        if self.sin_derivadas:
            partes.append(tr("licencias.et_sin_derivadas"))
        if self.compartir_igual:
            partes.append(tr("licencias.et_compartir_igual"))
        if self.conflictos:
            partes.append(tr("licencias.et_conflicto"))
        return " · ".join(partes)

    def lineas(self) -> list[str]:
        """Lo que implica, frase por frase, con las piezas que lo causan."""
        L = []
        if self.reservadas:
            L.append(tr("licencias.l_reservada", piezas=_join(self.por_reservada)))
        if not self.comercial:
            L.append(tr("licencias.l_no_comercial", piezas=_join(self.por_nc)))
        if self.sin_derivadas:
            L.append(tr("licencias.l_sin_derivadas", piezas=_join(self.por_nd)))
        if self.compartir_igual:
            L.append(tr("licencias.l_compartir_igual", piezas=_join(self.por_sa)))
        L += list(self.conflictos)
        if self.atribucion:
            L.append(tr("licencias.l_atribucion"))
        if self.sin_licencia:
            L.append(tr("licencias.l_sin_licencia", piezas=_join(self.sin_licencia)))
        if not L:
            L.append(tr("licencias.libre"))
        return L

    def resumen(self) -> str:
        """Etiqueta y frases en un texto (lo que muestra la ventana antes de exportar)."""
        return self.etiqueta + "\n" + "\n".join(f"• {x}" for x in self.lineas())

    def to_dict(self) -> dict:
        return {"comercial": self.comercial, "compartir_igual": self.compartir_igual, "sin_derivadas": self.sin_derivadas,
                "permiso_del_autor": self.reservadas, "atribucion": self.atribucion, "licencias": list(self.licencias),
                "por_nc": list(self.por_nc), "por_compartir_igual": list(self.por_sa), "por_sin_derivadas": list(self.por_nd),
                "por_reservada": list(self.por_reservada), "sin_licencia": list(self.sin_licencia), "sin_paquete": list(self.propias),
                "conflictos": list(self.conflictos), "piezas": [f.to_dict() for f in self.fuentes]}


def _join(refs) -> str:
    return ", ".join(dict.fromkeys(refs))


def efectiva(fs: list[Fuente]) -> Efectiva:
    """Junta las licencias de las piezas del armado en la restricción más fuerte (ver el docstring del módulo)."""
    e = Efectiva(list(fs))
    sa_distintas: dict[str, list[str]] = {}
    for f in fs:
        if f.sin_paquete:
            e.propias.append(f.ref)
            continue
        t = f.traits
        if not t.conocida:
            e.sin_licencia.append(f.ref)
            continue
        if t.spdx not in e.licencias:
            e.licencias.append(t.spdx)
        e.atribucion = e.atribucion or t.atribucion
        if t.nc or t.reservada:
            e.comercial = False
            e.por_nc.append(f.ref)
        if t.sa:
            e.compartir_igual = True
            e.por_sa.append(f.ref)
            sa_distintas.setdefault(t.spdx, []).append(f.ref)
        if t.nd:
            e.sin_derivadas = True
            e.por_nd.append(f.ref)
        if t.reservada:
            e.reservadas = True
            e.por_reservada.append(f.ref)
    e.licencias.sort()
    if len(sa_distintas) > 1:
        e.conflictos.append(tr("licencias.c_sa_distintas", licencias=", ".join(sorted(sa_distintas))))
    if e.sin_derivadas and e.compartir_igual:
        e.conflictos.append(tr("licencias.c_nd_con_sa", nd=_join(e.por_nd), sa=_join(e.por_sa)))
    return e


def licencia_de_figura(raiz: str, usos: list[tuple[str, str]]) -> Efectiva:
    """`efectiva(fuentes(raiz, usos))`."""
    return efectiva(fuentes(raiz, usos))


def licencia_de_receta(recipe, cat) -> Efectiva:
    """La licencia efectiva de una receta ya cargada contra su catálogo (`recipe.Recipe`, `catalog.Catalog`)."""
    return licencia_de_figura(cat.root, usos_de_receta(recipe, cat))


# ------------------------------------------------------------------------------------------------------------ archivos

def creditos_texto(e: Efectiva, figura: str, hoy: date | None = None) -> str:
    """El contenido de `CREDITOS.txt`."""
    hoy = hoy or date.today()
    out = [tr("licencias.t_titulo", figura=figura), "=" * 60, tr("licencias.t_generado", fecha=hoy.isoformat()), "",
           tr("licencias.t_efectiva"), "-" * 60, e.etiqueta, *[f"  * {x}" for x in e.lineas()], "",
           tr("licencias.t_aviso_legal"), "", tr("licencias.t_piezas"), "-" * 60]
    for f in e.fuentes:
        if f.sin_paquete:
            out.append(f"* {f.lugar}: {f.ref} — {tr('licencias.t_sin_paquete')}")
            continue
        det = [f"{tr('licencias.t_paquete')}: {f.paquete} {f.version}".rstrip()]
        if f.autores:
            det.append(f"{tr('licencias.t_autor')}: {f.autores_texto()}")
        det.append(f"{tr('licencias.t_licencia')}: {f.licencia or tr('licencias.t_sin_dato')}")
        if f.origen:
            det.append(f"{tr('licencias.t_origen')}: {f.origen}")
        if f.url:
            det.append(f"{tr('licencias.t_enlace')}: {f.url}")
        out.append(f"* {f.lugar}: {f.ref}")
        out += [f"    {d}" for d in det]
    return "\n".join(out) + "\n"


def escribir_creditos(outdir: str, e: Efectiva, figura: str, hoy: date | None = None) -> str:
    """Escribe `CREDITOS.txt` en `outdir` (junto al STL ensamblado) y devuelve el nombre del archivo."""
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, CREDITS_FILE), "w", encoding="utf-8") as fh:
        fh.write(creditos_texto(e, figura, hoy))
    return CREDITS_FILE


def markdown(block: dict) -> str:
    """La sección «Licencia efectiva» de `<figura>_lista_de_piezas.md`, a partir del bloque `licencia` del JSON ([] si no hay)."""
    if not block:
        return ""
    e_lines = []
    if block.get("permiso_del_autor"):
        e_lines.append(tr("licencias.l_reservada", piezas=_join(block.get("por_reservada") or [])))
    if not block.get("comercial", True):
        e_lines.append(tr("licencias.l_no_comercial", piezas=_join(block.get("por_nc") or [])))
    if block.get("sin_derivadas"):
        e_lines.append(tr("licencias.l_sin_derivadas", piezas=_join(block.get("por_sin_derivadas") or [])))
    if block.get("compartir_igual"):
        e_lines.append(tr("licencias.l_compartir_igual", piezas=_join(block.get("por_compartir_igual") or [])))
    e_lines += list(block.get("conflictos") or [])
    if block.get("atribucion"):
        e_lines.append(tr("licencias.l_atribucion"))
    if block.get("sin_licencia"):
        e_lines.append(tr("licencias.l_sin_licencia", piezas=_join(block["sin_licencia"])))
    if not e_lines:
        e_lines.append(tr("licencias.libre"))
    rows = ["| Lugar | Pieza | Paquete | Autor | Licencia |", "|---|---|---|---|---|"]
    for p in block.get("piezas") or []:
        autores = ", ".join((a.get("nombre") or "") for a in (p.get("autores") or []) if isinstance(a, dict)) or "-"
        paq = f"{p['paquete']} {p.get('version') or ''}".strip() if p.get("paquete") else tr("licencias.t_sin_paquete")
        rows.append(f"| {p.get('lugar') or '-'} | {p.get('pieza') or '-'} | {paq} | {autores} | {p.get('licencia') or '-'} |")
    return "\n".join(["", f"## {tr('licencias.t_efectiva')}", "", *[f"- {x}" for x in e_lines], "", *rows]) + "\n"


def agregar_a_lista_de_piezas(outdir: str, name: str, e: Efectiva) -> bool:
    """Pone el bloque `licencia` en `<name>_lista_de_piezas.{json,md}` (ya escrita por `export`). False si no hay lista."""
    from .pipeline import parts_list_markdown

    path = os.path.join(outdir, f"{name}_lista_de_piezas.json")
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8") as fh:
        lst = json.load(fh)
    lst[LICENSE_BLOCK] = e.to_dict()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(lst, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(outdir, f"{name}_lista_de_piezas.md"), "w", encoding="utf-8") as fh:
        fh.write(parts_list_markdown(lst))
    return True


def finalizar_exportacion(raiz: str, recipe, cat, outdir: str, name: str, progress=None) -> dict:
    """Lo que hace `afm armar` después de exportar: calcula la licencia efectiva, la avisa si restringe, escribe `CREDITOS.txt` y amplía
    la lista de piezas. Devuelve el bloque para el informe: `{archivo, lista_de_piezas, …Efectiva.to_dict()}`. No falla el armado: un
    problema al escribir se avisa y se devuelve `{}`."""
    say = progress or (lambda *_: None)
    try:
        e = licencia_de_receta(recipe, cat)
        if e.restringe:
            say("AVISO: " + tr("licencias.aviso_armado", resumen=e.etiqueta))
        archivo = escribir_creditos(outdir, e, name)
        lista = agregar_a_lista_de_piezas(outdir, name, e)
    except (OSError, ValueError) as exc:
        say("AVISO: " + tr("licencias.no_se_escribieron_creditos", error=exc))
        return {}
    return {"archivo": archivo, "en_lista_de_piezas": lista, **e.to_dict()}


__all__ = ["CREDITS_FILE", "Traits", "Fuente", "Efectiva", "traits", "usos_de_receta", "fuentes", "efectiva", "licencia_de_figura",
           "licencia_de_receta", "creditos_texto", "escribir_creditos", "agregar_a_lista_de_piezas", "finalizar_exportacion", "markdown"]
