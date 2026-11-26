"""Exportar un catálogo (o parte) como paquete `.zip` `afm-paquete-1` (plan de catálogos en línea, D03).

`export_package` toma el árbol de un catálogo, elige qué va (piezas, recetas o la línea completa), arma el `paquete.json` y escribe un
`.zip` con los archivos **en la raíz** y el mismo árbol del catálogo:

    paquete.json  README.md  LICENSE  [CREDITOS.md]  linea.json  [paleta.json]
    piezas/<tipo>/<id>@<n>.{pieza.json,stl,color.npz,retoque.json}      figuras/<nombre>.receta.json      [previews/portada.png]

Qué **no** viaja (§3 del plan): `compat.json`, `COMPATIBILIDAD.md`, `calibracion.json`, `CATALOGO.md`, `.papelera/`, `caballos/` y los
`.proyecto`. El FBX de color tampoco, salvo con `incluir_fbx` y bajo un tope; una pieza `modo: rgb` sin FBX sale **sin color** (con aviso)
y su ficha pierde el bloque `color` para que `Catalog.verify` no la marque como rota (CK307).

El zip es **determinista**: mismas entradas ⇒ mismos bytes (orden alfabético, fecha y permisos fijos). Los archivos del catálogo se copian tal
cual; solo se reescribe una ficha a la que se le quita el color y una receta a la que hay que fijar versiones o quitar el caballo.
Módulo de motor puro: sin GTK, sin red, sin `manifold3d`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from dataclasses import dataclass, field

from . import __version__
from . import parts as P
from .catalog import Catalog, CatalogError, parse_ref
from .paquete import ALL_RIGHTS_RESERVED, MANIFEST_FILE, Author, Contents, Paquete, PaqueteError, parse_semver
from .recipe import Recipe, RecipeError

FBX_MAX_BYTES = 64 * 1024 * 1024        # [P] tope por FBX con `incluir_fbx` (los de `proceres-127` pesan 26–36 MB; D01 fija el definitivo)
ZIP_DATE = (1980, 1, 1, 0, 0, 0)        # fecha fija: el mismo catálogo da el mismo zip
ZIP_LEVEL = 6
README_FILE, LICENSE_FILE, CREDITS_FILE = "README.md", "LICENSE", "CREDITOS.md"
COVER_FILE = "previews/portada.png"
COVER_SIZE = (1280, 720)

# categoría que se propone según el tipo de pieza (el autor la cambia con `categorias`)
_CATEGORY_OF_TYPE = {"cabeza": "cabezas", "torso": "cuerpos", "torso_sup": "cuerpos", "pelvis": "cuerpos", "brazo": "cuerpos",
                     "pierna": "cuerpos", "antebrazo": "cuerpos", "pantorrilla": "cuerpos", "bota": "cuerpos", "mano": "manos",
                     "accesorio": "armas-accesorios"}


class ExportError(CatalogError):
    """No se puede armar el paquete (falta una pieza, un archivo, un dato del manifiesto…); el mensaje dice qué corregir."""


@dataclass
class ExportResult:
    path: str
    paquete: Paquete
    archivos: dict                  # arcname -> tamaño en bytes (sin `paquete.json`)
    avisos: list = field(default_factory=list)
    sha256: str = ""                # del zip entero
    tamano: int = 0                 # bytes del zip


@dataclass
class _Entry:
    """Un archivo del zip: `path` (se copia del disco) o `data` (bytes ya armados)."""

    arcname: str
    path: str = ""
    data: bytes | None = None
    sha256: str = ""
    size: int = 0


def slug(text: str, limit: int = 64) -> str:
    """`Proceres 127_Güemes` → `proceres-127-guemes` (minúsculas, números y guiones: un id válido)."""
    import unicodedata

    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:limit].strip("-")


def parse_author(text: str) -> Author:
    """`Ana Pérez` o `Ana Pérez:usuario-github` → `Author` (el usuario de GitHub va después del último «:»)."""
    text = (text or "").strip()
    name, sep, gh = text.rpartition(":")
    return Author(name.strip(), gh.strip()) if sep else Author(text)


def _entry_from_path(arcname: str, path: str) -> _Entry:
    return _Entry(arcname, path=path, sha256=P.sha256_file(path), size=os.path.getsize(path))


def _entry_from_data(arcname: str, data: bytes) -> _Entry:
    return _Entry(arcname, data=data, sha256=hashlib.sha256(data).hexdigest(), size=len(data))


def _dump(obj) -> bytes:
    """Mismo estilo que el resto del catálogo (`json.dump(…, indent=2, ensure_ascii=False)`, sin salto final)."""
    return json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------------------------------- qué piezas y recetas van


def _resolve(cat: Catalog, ref: str, what: str) -> P.PieceMeta:
    """`id` o `id@n` → la ficha. Si falta, `ExportError` que dice quién la pidió."""
    try:
        return cat.meta(ref)
    except CatalogError:
        raise ExportError(f"{what}: no hay ninguna pieza «{ref}» en el catálogo «{cat.name}»") from None


def _recipe_path(cat: Catalog, name: str) -> str:
    stem = name[:-len(".receta.json")] if name.endswith(".receta.json") else name
    path = os.path.join(cat.root, "figuras", f"{stem}.receta.json")
    if not os.path.isfile(path):
        have = sorted(f[:-len(".receta.json")] for f in os.listdir(os.path.join(cat.root, "figuras"))
                      if f.endswith(".receta.json")) if os.path.isdir(os.path.join(cat.root, "figuras")) else []
        raise ExportError(f"no hay ninguna receta «{stem}» en «{cat.name}» (hay: {', '.join(have) or 'ninguna'})")
    return path


def _prepare_recipe(cat: Catalog, name: str, avisos: list) -> tuple[str, _Entry, list[P.PieceMeta], bool]:
    """(nombre, entrada del zip, piezas que usa, ¿cambia colores de la paleta?). Fija `id` → `id@n` y quita el caballo (fuera de v1); si no hay nada que cambiar, copia el archivo tal cual."""
    path = _recipe_path(cat, name)
    stem = os.path.basename(path)[:-len(".receta.json")]
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        rec = Recipe.from_dict(json.loads(json.dumps(raw)))
    except (OSError, ValueError, RecipeError) as exc:
        raise ExportError(f"la receta «{stem}» no se puede leer: {exc}") from None
    if rec.linea != cat.name:
        avisos.append(f"receta «{stem}»: es de la línea «{rec.linea}» y el catálogo es «{cat.name}»")
    changed = False
    metas: list[P.PieceMeta] = []
    lugares = dict(raw.get("lugares") or {})
    for place, ref in list(lugares.items()):
        if not ref:
            continue
        m = _resolve(cat, ref, f"receta «{stem}», lugar «{place}»")
        if ref != m.ref:
            lugares[place] = m.ref
            changed = True
            avisos.append(f"receta «{stem}»: «{place}» usaba «{ref}» sin versión y se fijó a «{m.ref}»")
        metas.append(m)
    acc = list(raw.get("accesorios") or [])
    for i, ref in enumerate(acc):
        m = _resolve(cat, ref, f"receta «{stem}», accesorio")
        if ref != m.ref:
            acc[i] = m.ref
            changed = True
            avisos.append(f"receta «{stem}»: el accesorio «{ref}» sin versión se fijó a «{m.ref}»")
        metas.append(m)
    if raw.get("cabalgadura"):
        changed = True
        avisos.append(f"receta «{stem}»: se quitó la cabalgadura (los caballos no viajan en el paquete todavía)")
    arc = f"figuras/{stem}.receta.json"
    if not changed:
        return stem, _entry_from_path(arc, path), metas, bool(raw.get("paleta_override"))
    out = dict(raw)
    out["lugares"] = lugares
    if "accesorios" in out:
        out["accesorios"] = acc
    out.pop("cabalgadura", None)
    return stem, _entry_from_data(arc, _dump(out)), metas, bool(raw.get("paleta_override"))


def _with_hands(cat: Catalog, metas: list[P.PieceMeta], avisos: list) -> list[P.PieceMeta]:
    """Un brazo con puerto `muneca` necesita una mano aparte: si el catálogo tiene la que sale de cortarlo (`mano-<…>`), va con él."""
    have = {m.ref for m in metas}
    out = list(metas)
    for m in metas:
        if m.tipo != "brazo" or "muneca" not in m.puertos or any(x.tipo == "mano" for x in metas):
            continue
        hid = cat.default_hand_id(m.id)
        try:
            hand = cat.meta(hid)
        except CatalogError:
            avisos.append(f"el brazo «{m.ref}» tiene puerto «muneca» y no se incluyó ninguna mano (no existe «{hid}»): agregá una con --piezas")
            continue
        if hand.ref not in have:
            have.add(hand.ref)
            out.append(hand)
            avisos.append(f"se agregó la mano «{hand.ref}» del brazo «{m.ref}»")
    return out


# ---------------------------------------------------------------------------------------------------- archivos de cada pieza


def _piece_entries(cat: Catalog, m: P.PieceMeta, include_fbx: bool, avisos: list) -> list[_Entry]:
    """Los archivos de la pieza. Verifica el hash del STL y del archivo de color contra la ficha."""
    files = cat._piece_files(m)
    if not files:
        raise ExportError(f"{m.ref}: no está la ficha en piezas/*/ del catálogo")
    folder = os.path.dirname(files[0])
    by_name = {os.path.basename(f): f for f in files}
    stl_name = os.path.basename(m.stl or P.stl_name(m.id, m.version))
    if stl_name not in by_name:
        raise ExportError(f"{m.ref}: falta el STL «{stl_name}»")
    if m.sha256 and P.sha256_file(by_name[stl_name]) != m.sha256:
        raise ExportError(f"{m.ref}: el STL no coincide con el hash de la ficha (¿se modificó?); corré `afm catalogo verificar`")
    base = f"piezas/{m.tipo}/"
    out = [_entry_from_path(base + stl_name, by_name[stl_name])]
    keep_color = bool(m.color)
    color_names: list[str] = []
    if m.color:
        roles = P.color_mode(m.color) == "roles"
        default = P.roles_name(m.id, m.version) if roles else P.color_name(m.id, m.version)
        color_name = os.path.basename(m.color.get("fuente") or default)
        cpath = os.path.join(folder, color_name)
        if roles:
            if not os.path.isfile(cpath):
                raise ExportError(f"{m.ref}: falta el paquete de roles de color «{color_name}»")
            if m.color.get("sha256") and P.sha256_file(cpath) != m.color["sha256"]:
                raise ExportError(f"{m.ref}: el paquete de roles «{color_name}» no coincide con el hash de la ficha")
            color_names.append(color_name)
            rt = m.color.get("retoque") if isinstance(m.color.get("retoque"), dict) else {}
            if rt:
                rname = os.path.basename(rt.get("fuente") or P.retouch_name(m.id, m.version))
                if rname in by_name:
                    color_names.append(rname)
                else:
                    avisos.append(f"{m.ref}: la ficha declara un retoque de color y falta «{rname}»: se exporta sin él")
        elif not include_fbx:
            keep_color = False
            avisos.append(f"{m.ref}: se exporta sin color (el FBX no viaja; usá --incluir-fbx o pasá la pieza a roles de color)")
        elif not os.path.isfile(cpath):
            keep_color = False
            avisos.append(f"{m.ref}: se exporta sin color (falta el FBX «{color_name}» en el catálogo)")
        elif os.path.getsize(cpath) > FBX_MAX_BYTES:
            keep_color = False
            avisos.append(f"{m.ref}: se exporta sin color (el FBX pesa {os.path.getsize(cpath) / 1e6:.0f} MB y el tope es {FBX_MAX_BYTES / 1e6:.0f} MB)")
        elif m.color.get("sha256") and P.sha256_file(cpath) != m.color["sha256"]:
            raise ExportError(f"{m.ref}: el FBX «{color_name}» no coincide con el hash de la ficha")
        else:
            color_names.append(color_name)
    for cn in color_names:
        out.append(_entry_from_path(base + cn, os.path.join(folder, cn)))
    meta_name = P.meta_name(m.id, m.version)
    if m.color and not keep_color:                     # CK307: sin el archivo de color, la ficha no puede declararlo
        clean = P.PieceMeta.from_dict(m.to_dict())
        clean.color = None
        out.append(_entry_from_data(base + meta_name, _dump(clean.to_dict())))
    else:
        out.append(_entry_from_path(base + meta_name, by_name[meta_name]))
    return out


# ---------------------------------------------------------------------------------------------------- manifiesto y textos


def default_categories(tipo: str, metas: list[P.PieceMeta]) -> list[str]:
    if tipo == "heroe":
        return ["heroes"]
    if tipo == "linea":
        return ["lineas"]
    if tipo == "coleccion":
        return ["colecciones"]
    cats = list(dict.fromkeys(_CATEGORY_OF_TYPE.get(m.tipo, "otros") for m in sorted(metas, key=lambda x: x.ref)))
    return cats[:4] or ["otros"]


def default_license_text(spdx: str) -> str:
    """LICENSE mínimo: nombra la licencia y dónde está su texto completo. El autor puede pasar el suyo (`license_file`)."""
    if spdx == ALL_RIGHTS_RESERVED:
        return "Todos los derechos reservados.\nEl autor autoriza la distribución de este paquete tal como está.\n"
    return f"Licencia: {spdx}\nTexto completo: https://spdx.org/licenses/{spdx}.html\n"


def default_readme(p: Paquete) -> str:
    L = [f"# {p.nombre}", "", p.descripcion, ""]
    if p.linea:
        L.append(f"- **Línea:** {p.linea['nombre']} ({p.linea['altura_mm']:g} mm)")
    L.append("- **Autores:** " + ", ".join(a.nombre + (f" (@{a.github})" if a.github else "") for a in p.autores))
    L += [f"- **Licencia:** {p.licencia}", f"- **Origen:** {p.origen}", f"- **Versión:** {p.version}", "", "## Contenido", ""]
    c = p.contenido
    L.append(f"- Piezas ({len(c.piezas)}): " + (", ".join(f"`{r}`" for r in c.piezas) or "ninguna"))
    L.append(f"- Recetas ({len(c.recetas)}): " + (", ".join(f"`{r}`" for r in c.recetas) or "ninguna"))
    L.append("- Paleta de color: " + ("sí" if c.paleta else "no"))
    L += ["", "Se instala con `afm catalogo instalar paquete.zip` (o desde «Compartir → Importar paquete .zip…»).", ""]
    return "\n".join(L)


def _cover_bytes(path: str, avisos: list) -> bytes:
    from io import BytesIO

    from PIL import Image

    try:
        with Image.open(path) as im:
            im.load()
            if im.size != COVER_SIZE:
                avisos.append(f"la portada mide {im.size[0]}×{im.size[1]} y se espera {COVER_SIZE[0]}×{COVER_SIZE[1]}")
            buf = BytesIO()
            im.convert("RGBA" if "A" in im.getbands() else "RGB").save(buf, format="PNG")
    except (OSError, ValueError) as exc:
        raise ExportError(f"la portada «{path}» no es una imagen legible: {exc}") from None
    return buf.getvalue()


def _read_text(path: str, what: str) -> bytes:
    try:
        with open(path, "rb") as f:
            data = f.read()
        data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ExportError(f"{what} «{path}» no se puede leer como texto UTF-8: {exc}") from None
    return data


# ---------------------------------------------------------------------------------------------------- exportar


def write_zip(out: str, entries: list) -> None:
    """Escribe `out` con `entries` (`_Entry`, en ese orden): fecha, permisos y compresión fijos, así que el mismo contenido da el mismo zip.

    Se escribe aparte (`out.tmp`) y se mueve al final; `ExportError` si no se puede escribir. Lo comparten `export_package` y
    `autor.pack_dir` (D09)."""
    tmp = out + ".tmp"
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=ZIP_LEVEL) as zf:
            for e in entries:
                zi = zipfile.ZipInfo(e.arcname, date_time=ZIP_DATE)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o100644 << 16
                zi.create_system = 3
                if e.data is None:
                    with open(e.path, "rb") as src:
                        zf.writestr(zi, src.read(), compresslevel=ZIP_LEVEL)
                else:
                    zf.writestr(zi, e.data, compresslevel=ZIP_LEVEL)
        os.replace(tmp, out)
    except OSError as exc:
        raise ExportError(f"no se pudo escribir «{out}»: {exc}") from None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def export_package(cat: Catalog, out: str, *, piezas=(), recetas=(), linea_completa: bool = False, incluir_fbx: bool = False,
                   base: Paquete | None = None, id: str = "", nombre: str = "", version: str = "", descripcion: str = "",
                   tipo: str = "", autores=(), licencia: str = "", origen: str = "", github: str = "", categorias=(),
                   etiquetas=(), readme: str = "", license_file: str = "", creditos: str = "", portada: str = "",
                   overwrite: bool = False) -> ExportResult:
    """Escribe `out` (un `.zip`) con lo elegido. `base` es un `paquete.json` ya escrito por el autor: sus campos son los valores por
    defecto y los argumentos los pisan. Sin `licencia`, `origen` y autor (de `base` o de los argumentos) no se exporta: cada persona
    responde por su contenido. `ExportError` si falta algo; el catálogo no se modifica nunca."""
    if not out.lower().endswith(".zip"):
        raise ExportError(f"el paquete tiene que terminar en .zip (se pidió «{out}»)")
    if os.path.exists(out) and not overwrite:
        raise ExportError(f"«{out}» ya existe (usá --sobrescribir)")
    if linea_completa and (piezas or recetas):
        raise ExportError("«línea completa» no se combina con piezas ni recetas sueltas")
    if not (piezas or recetas or linea_completa):
        raise ExportError("no se eligió qué exportar: piezas, recetas o la línea completa")
    avisos: list[str] = []
    metas: dict[str, P.PieceMeta] = {}

    def add(m: P.PieceMeta):
        metas.setdefault(m.ref, m)

    for ref in piezas:
        parse_ref(ref)
        add(_resolve(cat, ref, "--piezas"))
    if linea_completa:
        for m in cat.list(all_versions=True):
            add(m)
        names = sorted(f[:-len(".receta.json")] for f in os.listdir(os.path.join(cat.root, "figuras")) if f.endswith(".receta.json")) \
            if os.path.isdir(os.path.join(cat.root, "figuras")) else []
    else:
        names = list(dict.fromkeys(recetas))
        for m in _with_hands(cat, [metas[r] for r in sorted(metas)], avisos):
            add(m)
    entries: dict[str, _Entry] = {}
    recipe_names: list[str] = []
    has_override = False
    for name in names:
        try:
            stem, entry, rmetas, override = _prepare_recipe(cat, name, avisos)
        except ExportError as exc:
            if not linea_completa:
                raise
            avisos.append(f"se omite la receta «{name}»: {exc}")         # en la línea completa, una receta rota no frena el resto
            continue
        entries[entry.arcname] = entry
        recipe_names.append(stem)
        has_override = has_override or override
        for m in rmetas:
            add(m)
    ordered = [metas[r] for r in sorted(metas, key=lambda r: (parse_ref(r)[0], parse_ref(r)[1]))]
    for m in ordered:
        for e in _piece_entries(cat, m, incluir_fbx, avisos):
            entries[e.arcname] = e
    uses_roles = any(k.endswith(".color.npz") for k in entries)
    with_palette = os.path.isfile(cat.palette_path) and (linea_completa or uses_roles or has_override)
    if with_palette:
        entries["paleta.json"] = _entry_from_path("paleta.json", cat.palette_path)
    entries["linea.json"] = _entry_from_path("linea.json", os.path.join(cat.root, "linea.json"))

    # ---- manifiesto
    b = base
    tipo = tipo or (b.tipo if b else "") or ("linea" if linea_completa else ("heroe" if recipe_names and not piezas else "piezas"))
    name_hint = "" if linea_completa else (recipe_names[0] if len(recipe_names) == 1 else (ordered[0].id if len(ordered) == 1 else ""))
    pid = id or (b.id if b else "") or slug(f"{cat.name}-{name_hint}" if name_hint else cat.name)
    authors = [a if isinstance(a, Author) else parse_author(a) for a in autores] or (list(b.autores) if b else [])
    pkg = Paquete(
        id=pid, nombre=nombre or (b.nombre if b else "") or (f"{cat.name} — {name_hint}" if name_hint else cat.name),
        version=version or (b.version if b else "") or "1.0.0",
        descripcion=descripcion or (b.descripcion if b else "") or f"{'Línea' if tipo == 'linea' else 'Paquete'} para la línea {cat.name}.",
        tipo=tipo, autores=authors, licencia=licencia or (b.licencia if b else ""), origen=origen or (b.origen if b else ""),
        github=github or (b.github if b else ""), linea={"nombre": cat.name, "altura_mm": cat.height_mm},
        motor=f">={__version__}",
        contenido=Contents(piezas=[m.ref for m in ordered], recetas=sorted(recipe_names), paleta=with_palette),
        requiere=list(b.requiere) if b else [], conflicts=list(b.conflicts) if b else [],
        experimental=bool(b.experimental) if b else False,
        categorias=list(categorias) or (list(b.categorias) if b else []) or default_categories(tipo, ordered),
        etiquetas=list(etiquetas) or (list(b.etiquetas) if b else []))
    if not (pkg.licencia and pkg.origen and pkg.autores):
        miss = [n for n, v in (("--licencia", pkg.licencia), ("--origen", pkg.origen), ("--autor", pkg.autores)) if not v]
        raise ExportError("faltan datos que solo el autor puede dar: " + ", ".join(miss) + " (cada persona responde por su contenido)")

    # ---- textos y portada
    entries[README_FILE] = _entry_from_data(README_FILE, _read_text(readme, "el README") if readme else default_readme(pkg).encode("utf-8"))
    entries[LICENSE_FILE] = _entry_from_data(LICENSE_FILE, _read_text(license_file, "la licencia") if license_file
                                             else default_license_text(pkg.licencia).encode("utf-8"))
    if creditos:
        entries[CREDITS_FILE] = _entry_from_data(CREDITS_FILE, _read_text(creditos, "los créditos"))
    if portada:
        entries[COVER_FILE] = _entry_from_data(COVER_FILE, _cover_bytes(portada, avisos))
    pkg.archivos = {k: e.sha256 for k, e in sorted(entries.items())}
    problems = pkg.problems()
    if problems:
        raise ExportError("el manifiesto del paquete no es válido:\n  " + "\n  ".join(problems))
    try:
        parse_semver(pkg.version)
    except PaqueteError as exc:                        # ya lo cubre CK103; por si cambia la regla
        raise ExportError(str(exc)) from None
    manifest = _entry_from_data(MANIFEST_FILE, pkg.to_json().encode("utf-8"))

    # ---- zip (determinista; se escribe aparte y se mueve al final)
    write_zip(out, [manifest] + [entries[k] for k in sorted(entries)])
    return ExportResult(path=out, paquete=pkg, archivos={k: e.size for k, e in sorted(entries.items())}, avisos=avisos,
                        sha256=P.sha256_file(out), tamano=os.path.getsize(out))

