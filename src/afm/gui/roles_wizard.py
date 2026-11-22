"""T47 · Lógica del asistente «Roles de color»: de un FBX con textura a roles asignados, guardados en el catálogo o en la figura base.
Sin GTK ni OpenGL: se prueba sin ventana (la ventana está en `roles_wizard_window.py`).

Flujo: `load(fbx)` calcula los clusters de color (`colorroles.propose`) → por cada cluster se decide un rol de la paleta de la línea, un
**rol nuevo** (con el color del cluster) o «sin rol» → `save_piece(ref)` (versión nueva de una pieza del catálogo) o `save_figure(ruta)`
(paquete `.color.npz` de la figura base, que queda anotado en `project.color.roles`).

Reglas (plan §11, R-1): nada se asigna solo. Los clusters que **proponen** un rol vienen preseleccionados pero marcados como sugerencia y
hay que confirmarlos (`confirm`/`confirm_suggestions`) o cambiarlos; con clusters sin decidir o sin confirmar no se puede guardar. Al guardar
se suman como **anclas** del rol los colores de los clusters asignados (hasta `MAX_ANCHORS`; las anclas que ya tenía no se tocan) y se crean los
roles nuevos; la paleta se escribe antes que la pieza (la pieza exige que sus roles existan) y **se restituye si guardar la pieza falla**.
`PIL`/`trimesh` no se importan al cargar el módulo (R3).

O09 · Antes de guardar en una pieza, `check_piece(ref)` la alinea y muestra el resultado (`PieceCheck`: hacia dónde quedó la cara del FBX y la pieza con el
color real). Si la cara de una cabeza quedó hacia atrás (o no se pudo decidir), `save_piece` **no guarda** salvo que se pase `confirm_front=True`.

O10 · Con `load(fbx, piece_ref=ref)` el análisis **alinea primero y agrupa después**: los clusters salen solo de la parte del FBX que la pieza tiene (`colorpiece.piece_region`),
no del busto entero; los clusters de menos del 1 % de esa parte se funden con otro, el filtro de mayoría es de 7 px y los roles que cubren menos del 0,5 % de la
pieza no se guardan."""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

from ..colorroles import FIGURE_K, FILTER_PX, MERGE_MIN_PCT, PIECE_FILTER_PX, REGION_K, ROLE_MIN_PCT
from ..i18n import tr
from ..palette import DEFAULT_FLAT, MAX_ANCHORS, Palette, PaletteError, Role, hex_to_rgb

HIGHLIGHT_RGB = (255, 40, 200)       # el cluster de la fila elegida se ve siempre en este magenta, sea cual sea su color (un gris oscuro sobre fondo oscuro no se veía)
DIM_MIX = 0.55                       # los demás clusters se ven con su color lavado hacia DIM_TO (se distingue la figura sin competir con el resaltado)
DIM_TO = (215, 215, 215)


class RolesWizardError(ValueError):
    """Lo pedido no se puede hacer (rol inexistente, clusters sin decidir, sin catálogo…). El mensaje está listo para mostrarse."""


@dataclass
class PieceCheck:
    """O09 · Resultado de alinear el FBX con una pieza, antes de guardar. `matrix`: 4×4 FBX → pieza. `frente_ok`: `True` la cara quedó del lado del frente de la
    pieza, `False` hacia atrás, `None` no se pudo comprobar (no es una cabeza, o el FBX no tiene rol de piel). `parts`: la pieza con el color real,
    `[(nombre, malla, rgba)]` para `PreviewRenderer.render_parts` (solo con `preview=True`). `message`: texto listo para mostrar."""
    ref: str
    matrix: list
    report: dict
    frente_ok: bool | None
    angulo_deg: float | None
    ambigua: bool
    message: str
    frame: object = None                         # `FigureFrame` calculado acá (`None` si la similitud la dio quien llama)
    parts: list | None = None
    warnings: list | None = None
    key: tuple = ()
    girada: bool = False                          # el motor giró 180° la pose que mejor calzaba para poner la cara de frente (`alineacion.yaw180.girada`)
    margen_mm: float | None = None                # cuánto mejor calzaba la pose sin girar (`alineacion.yaw180.margen_mm`)

    @property
    def needs_confirmation(self) -> bool:
        """Guardar exige confirmación explícita si la cara quedó hacia atrás, si no se pudo decidir hacia dónde mira, o si el programa tuvo que **girar** la pose
        180° por su cuenta para ponerla de frente (siempre se pregunta: no se corrige solo)."""
        return self.frente_ok is False or self.ambigua or self.girada


@dataclass
class Choice:
    kind: str                        # "role" (rol de la paleta o de un rol nuevo ya creado), "new" (crea el rol) o "none"
    role: str | None = None
    nombre: str = ""                 # solo "new"
    confirmed: bool = True           # False = viene de la sugerencia y todavía nadie la confirmó


def _hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*(int(x) for x in rgb))


class RolesWizard:
    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.tm = None
        self.proposal = None
        self.origen: dict = {}
        self.fbx_path = ""
        self.choices: dict[int, Choice] = {}
        self.warnings: list[str] = []
        self.similarity: list | None = None          # 4×4 FBX → figura calculada desde la GUI (`compute_similarity`); `save_piece` la usa si no se pasa otra
        self.similarity_info: dict = {}
        self.last_check: PieceCheck | None = None    # O09: la última prueba en una pieza (se descarta si cambian las decisiones, el FBX o la similitud)
        self.region: np.ndarray | None = None        # O10: triángulos del FBX que la pieza tiene (los clusters salen de ahí); None = el FBX entero
        self.region_ref: str = ""                    # O10: pieza con que se calculó la región
        self.dropped_roles: list[str] = []           # O10: roles que `package()` dejó afuera por cubrir < 0,5 % de la pieza

    # ------------------------------------------------------------------ catálogo y paleta
    def _cat(self):
        cat = self.ctrl.catalog_handle()
        if cat is None:
            raise RolesWizardError(tr("common.no_hay_un_catalogo_abierto"))
        return cat

    def line_palette(self) -> Palette:
        """Paleta guardada de la línea abierta (vacía si no hay catálogo o todavía no tiene `paleta.json`)."""
        cat = self.ctrl.catalog_handle()
        if cat is None:
            return Palette([])
        try:
            return cat.palette() or Palette([])
        except ValueError as exc:
            raise RolesWizardError(str(exc)) from exc

    def piece_refs(self) -> list[str]:
        """Piezas del catálogo (todas las versiones) a las que se les puede poner color por roles."""
        return sorted(m.ref for m in self.ctrl.catalog_pieces())

    # ------------------------------------------------------------------ cargar
    @property
    def loaded(self) -> bool:
        return self.proposal is not None

    def load(self, fbx_path: str, k: int | None = None, piece_ref: str | None = None) -> None:
        """Lee el FBX y propone `k` clusters (puede tardar: se llama desde un hilo). Descarta lo decidido antes.

        O10 · Con `piece_ref` (una pieza del catálogo) se alinea el FBX con la pieza **antes** de agrupar y los clusters salen solo de lo que la pieza
        tiene (`load_textured`). `k` por defecto: `REGION_K` (8) con pieza y `FIGURE_K` (10) sin ella."""
        from .. import parts as P
        from ..colorroles import RolesError
        from ..fbxio import FbxError, load_textured

        try:
            tm = load_textured(fbx_path)
            origen = {"nombre": os.path.basename(fbx_path), "sha256": P.sha256_file(fbx_path)}
            self.load_textured(tm, origen, k, piece_ref)
        except (FbxError, RolesError, OSError) as exc:
            raise RolesWizardError(tr("roles_wizard.error_fbx", error=exc)) from exc
        self.fbx_path = fbx_path

    def load_textured(self, tm, origen: dict | None = None, k: int | None = None, piece_ref: str | None = None) -> None:
        """Igual que `load` pero con un `TexturedMesh` ya leído."""
        from ..colorroles import propose

        pal = self.line_palette()
        region = self._piece_region(tm, piece_ref) if piece_ref else None
        if k is None:
            k = REGION_K if region is not None else FIGURE_K
        self.proposal = propose(tm, pal if pal.roles else None, k=k, region=region, min_share=MERGE_MIN_PCT if region is not None else 0.0)
        self.tm = tm
        self.origen = dict(origen or {})
        self.fbx_path = ""
        self.warnings = []
        self.last_check = None
        self.region, self.region_ref = region, (piece_ref or "") if region is not None else ""
        self.choices = {c: Choice("role", s, confirmed=False) for c, s in enumerate(self.proposal.suggested) if s is not None}
        if piece_ref and region is None:
            self.warnings.append(tr("roles_wizard.region_no", ref=piece_ref))

    def _piece_region(self, tm, ref: str):
        """O10 · Máscara de triángulos de `tm` que corresponden a la pieza `ref` (lo que está por encima del plano del cuello y dentro de su caja), o `None` si no se
        puede saber (no es una cabeza, la pieza no tiene el puerto `cuello`, el FBX no calza): entonces se agrupa sobre el FBX entero y se avisa.

        Se alinea el FBX **sin** pista de la cara: la región casi no cambia con el giro de 180° sobre el eje del cuello (la caja de la pieza es simétrica en el
        plano horizontal), así que alcanza con un calce geométrico; el sentido de la cara se decide después, con los roles (O05/O06)."""
        from .. import parts as P
        from ..catalog import CatalogError
        from ..colorpiece import PieceColorError, align_fbx_to_piece, piece_region, piece_front

        cat = self._cat()
        try:
            meta, mesh = cat.load(ref)
            if meta.tipo != "cabeza":
                return None
            tmesh = P._as_trimesh(mesh)
            pf = piece_front(meta, tmesh)
            frame = align_fbx_to_piece(tm, tmesh, require_ok=False)
            M = np.asarray(frame.matrix, float)
            V = np.asarray(tm.vertices, float) @ M[:3, :3].T + M[:3, 3]
            region = piece_region(V, tm.faces, pf, tmesh)
        except (CatalogError, P.PieceError, PieceColorError, ValueError, KeyError) as exc:
            self.warnings.append(str(exc))
            return None
        return region if int(np.count_nonzero(region)) > 0 else None

    def _need_loaded(self) -> None:
        if not self.loaded:
            raise RolesWizardError(tr("roles_wizard.sin_analisis"))

    def _check_cluster(self, c: int) -> int:
        self._need_loaded()
        if not isinstance(c, (int, np.integer)) or not 0 <= c < self.proposal.k:
            raise RolesWizardError(tr("roles_wizard.cluster_inexistente", n=c))
        return int(c)

    # ------------------------------------------------------------------ decidir
    def new_role_ids(self) -> list[str]:
        """Ids de los roles nuevos que se crearían al guardar (en el orden de los clusters)."""
        out: list[str] = []
        for c in sorted(self.choices):
            ch = self.choices[c]
            if ch.kind == "new" and ch.role not in out:
                out.append(ch.role)
        return out

    def role_options(self) -> list[str]:
        """Ids que se pueden elegir en la lista de un cluster: roles de la paleta y roles nuevos ya pedidos."""
        return self.line_palette().ids() + [r for r in self.new_role_ids() if r not in self.line_palette().ids()]

    def _prune(self) -> None:
        """Quita las decisiones que apuntan a un rol nuevo que ya nadie crea."""
        valid = set(self.line_palette().ids()) | set(self.new_role_ids())
        for c in [c for c, ch in self.choices.items() if ch.kind == "role" and ch.role not in valid]:
            del self.choices[c]

    def set_role(self, c: int, role_id: str) -> None:
        """El cluster `c` va al rol `role_id` (de la paleta o un rol nuevo que otro cluster ya pidió). Cuenta como confirmado."""
        c = self._check_cluster(c)
        if role_id not in self.role_options():
            raise RolesWizardError(tr("roles_wizard.rol_inexistente", rol=role_id))
        self.choices[c] = Choice("role", role_id)
        self._prune()

    def set_none(self, c: int) -> None:
        """El cluster `c` queda «sin rol» (sale del color plano)."""
        c = self._check_cluster(c)
        self.choices[c] = Choice("none")
        self._prune()

    def set_new(self, c: int, role_id: str, nombre: str = "") -> None:
        """El cluster `c` crea el rol `role_id` (con el color de este cluster; si otro cluster ya pidió ese rol nuevo, se suma a él)."""
        c = self._check_cluster(c)
        role_id = (role_id or "").strip()
        if role_id in self.line_palette().ids():
            raise RolesWizardError(tr("roles_wizard.rol_nuevo_existe", rol=role_id))
        try:
            Role(role_id, (nombre or "").strip() or role_id, _hex(self.proposal.centers_rgb[c]))
        except PaletteError as exc:
            raise RolesWizardError(str(exc)) from exc
        if len(self.line_palette().roles) + len(set(self.new_role_ids()) | {role_id}) > 64:
            raise RolesWizardError(tr("roles_wizard.demasiados_roles"))
        self.choices[c] = Choice("new", role_id, (nombre or "").strip())
        self._prune()

    def confirm(self, c: int) -> None:
        """Confirma la sugerencia del cluster `c` tal cual."""
        c = self._check_cluster(c)
        ch = self.choices.get(c)
        if ch is not None:
            ch.confirmed = True

    def confirm_suggestions(self) -> int:
        """Confirma todas las sugerencias que siguen sin tocar. Devuelve cuántas eran."""
        self._need_loaded()
        todo = [c for c, ch in self.choices.items() if not ch.confirmed]
        for c in todo:
            self.choices[c].confirmed = True
        return len(todo)

    # ------------------------------------------------------------------ estado
    def undecided(self) -> list[int]:
        self._need_loaded()
        return [c for c in range(self.proposal.k) if c not in self.choices]

    def unconfirmed(self) -> list[int]:
        self._need_loaded()
        return sorted(c for c, ch in self.choices.items() if not ch.confirmed)

    def blockers(self) -> str:
        """Por qué todavía no se puede guardar ("" si se puede)."""
        if not self.loaded:
            return tr("roles_wizard.sin_analisis")
        und, unc = self.undecided(), self.unconfirmed()
        if und:
            return tr("roles_wizard.faltan_decidir", n=len(und), lista=", ".join(str(c + 1) for c in und))
        if unc:
            return tr("roles_wizard.sugerencias_sin_confirmar", n=len(unc), lista=", ".join(str(c + 1) for c in unc))
        return ""

    @property
    def can_save(self) -> bool:
        return self.blockers() == ""

    def clusters(self) -> list[dict]:
        """Una ficha por cluster: `n, hex, pct, estado ("sin_decidir" | "sugerido" | "decidido"), tipo, rol, sugerencia`."""
        self._need_loaded()
        out = []
        for c in range(self.proposal.k):
            ch = self.choices.get(c)
            estado = "sin_decidir" if ch is None else ("decidido" if ch.confirmed else "sugerido")
            out.append({"n": c, "hex": _hex(self.proposal.centers_rgb[c]), "pct": float(self.proposal.share[c]), "estado": estado,
                        "tipo": None if ch is None else ch.kind, "rol": None if ch is None else ch.role,
                        "sugerencia": self.proposal.suggested[c]})
        return out

    # ------------------------------------------------------------------ resultado
    def assignment(self) -> dict:
        """`{cluster: rol | None}` para `build_package` (solo con todo decidido)."""
        if self.undecided():
            raise RolesWizardError(self.blockers())
        return {c: (None if ch.kind == "none" else ch.role) for c, ch in self.choices.items()}

    def final_palette(self) -> Palette:
        """La paleta que quedaría al guardar: la de la línea + los roles nuevos (con el color de su cluster más grande) + las anclas
        de los clusters asignados (las que ya tenía el rol van primero y no se tocan; tope `MAX_ANCHORS`)."""
        self._need_loaded()
        base = self.line_palette()
        assign = {c: ch for c, ch in self.choices.items() if ch.kind != "none"}
        anchors: dict[str, list[str]] = {}
        for c in sorted(assign):
            hx = _hex(self.proposal.centers_rgb[c])
            lst = anchors.setdefault(assign[c].role, [])
            if hx not in lst:
                lst.append(hx)
        roles = []
        for r in base.roles:
            have = list(r.anclas)
            for hx in anchors.get(r.id, []):
                if hx not in have and len(have) < MAX_ANCHORS:
                    have.append(hx)
            roles.append(Role(r.id, r.nombre, r.hex, have))
        for rid in self.new_role_ids():
            first = min(c for c, ch in assign.items() if ch.role == rid and ch.kind == "new")
            names = [ch.nombre for _c, ch in sorted(assign.items()) if ch.kind == "new" and ch.role == rid and ch.nombre]
            nombre = names[0] if names else rid
            roles.append(Role(rid, nombre, _hex(self.proposal.centers_rgb[first]), anchors.get(rid, [])[:MAX_ANCHORS]))
        try:
            return Palette(roles)
        except PaletteError as exc:
            raise RolesWizardError(str(exc)) from exc

    def package(self):
        """`RolesPackage` con lo decidido (`colorroles.build_package`). O10: con clusters de una pieza el filtro de mayoría es de `PIECE_FILTER_PX` y los roles
        que cubren menos de `ROLE_MIN_PCT` % de la pieza no se incluyen (`dropped_roles` los lista)."""
        from ..colorroles import RolesError, build_package, prune_small_roles

        try:
            pkg = build_package(self.tm, self.proposal, self.assignment(), origen=self.origen,
                                filter_px=PIECE_FILTER_PX if self.region is not None else FILTER_PX)
            self.dropped_roles = []
            if self.region is not None:
                pkg, self.dropped_roles = prune_small_roles(pkg, self.region, ROLE_MIN_PCT)
            return pkg
        except RolesError as exc:
            raise RolesWizardError(str(exc)) from exc

    def _with_palette(self, cat, action):
        """Escribe la paleta final, corre `action()` y, si falla, restituye la paleta de antes (o la quita si la línea no tenía)."""
        old = cat.palette()
        new = self.final_palette()
        changed = old is None or old.to_dict() != new.to_dict()
        if changed:
            cat.set_palette(new)
        try:
            return action()
        except Exception:
            if changed:
                if old is not None:
                    cat.set_palette(old)
                elif os.path.exists(cat.palette_path):
                    os.remove(cat.palette_path)
            raise

    def compute_similarity(self, stl_path: str, project_path: str) -> dict:
        """Calcula la similitud FBX → figura con el FBX ya cargado (o el de `fbx_path`) y la deja en `self.similarity` para `save_piece`.
        Devuelve `{"media_mm", "ok"}`. Si el FBX no calza con esa figura levanta `RolesWizardError` y no deja nada guardado.
        Puede tardar (se llama desde un hilo)."""
        from ..figsim import FigureSimilarityError, compute_figure_similarity
        from ..frame import SIMILARITY_OK_MM

        if not self.fbx_path:
            raise RolesWizardError(tr("roles_wizard.sim_sin_fbx"))
        try:
            sim = compute_figure_similarity(stl_path, project_path, self.fbx_path, tm=self.tm)
        except FigureSimilarityError as exc:
            raise RolesWizardError(str(exc)) from exc
        if not sim.ok:
            raise RolesWizardError(tr("roles_wizard.sim_no_calza", media=f"{sim.media_mm:.3f}", max=f"{SIMILARITY_OK_MM:g}"))
        self.similarity = sim.matrix
        self.similarity_info = {"media_mm": sim.media_mm, "stl": os.path.basename(stl_path), "proyecto": os.path.basename(project_path)}
        return {"media_mm": sim.media_mm, "ok": True}

    def clear_similarity(self) -> None:
        self.similarity, self.similarity_info = None, {}

    def _check_key(self, ref: str, similarity) -> tuple:
        """Identifica lo que se probó: pieza, análisis, decisiones y similitud. Si algo cambia, la prueba anterior ya no vale."""
        sim = None if similarity is None else tuple(np.round(np.asarray(similarity, float).ravel(), 9).tolist())
        return (ref, id(self.proposal), tuple((c, ch.kind, ch.role) for c, ch in sorted(self.choices.items())), sim)

    def fresh_check(self, ref: str, similarity=None) -> PieceCheck | None:
        """La última prueba si sigue valiendo para `ref` con las decisiones y la similitud de ahora; si no, `None`."""
        if similarity is None:
            similarity = self.similarity
        c = self.last_check
        return c if c is not None and self.loaded and c.key == self._check_key(ref, similarity) else None

    def check_piece(self, ref: str, similarity=None, preview: bool = False) -> PieceCheck:
        """O09 · Alinea el FBX con la pieza `ref` y dice hacia dónde quedó la cara, SIN guardar nada (puede tardar ≈10 s: se llama desde un hilo).

        Con `similarity` (4×4 FBX → figura entera, para piezas recortadas de una figura) no se alinea: se usa esa matriz y, en una cabeza, se mide hacia
        dónde queda la cara con ella. Con `preview=True` además se arma la pieza con el color real (`parts`), con el mismo ajuste no rígido que se aplicaría
        al guardar. Si el FBX no calza con la pieza levanta `RolesWizardError`. Reusa la prueba anterior si nada cambió."""
        from .. import parts as P
        from ..catalog import CatalogError
        from ..colorpiece import PieceColorError, PieceFrontError, align_roles_package, front_hint
        from ..colorroles import RolesError
        from ..frame import FRONT_MAX_DEG

        if not self.can_save:
            raise RolesWizardError(self.blockers())
        if similarity is None:
            similarity = self.similarity
        key = self._check_key(ref, similarity)
        old = self.last_check
        if old is not None and old.key == key and (old.parts is not None or not preview):
            return old
        cat = self._cat()
        try:
            meta, mesh = cat.load(ref)
            pkg, pal = self.package(), self.final_palette()
            tmesh = P._as_trimesh(mesh)
            frame = None
            if similarity is None:
                try:
                    frame = align_roles_package(meta, tmesh, pkg, pal)
                except PieceFrontError as exc:             # calza, pero la cara quedó hacia atrás o no se pudo decidir: se muestra igual
                    frame = exc.frame
                matrix, report = np.asarray(frame.matrix, float), dict(frame.report)
            else:
                matrix, report = np.asarray(similarity, float), {}
                if matrix.shape != (4, 4) or not np.all(np.isfinite(matrix)):
                    raise RolesWizardError(tr("roles_wizard.sim_invalida"))
                if meta.tipo == "cabeza":
                    ang, vec = front_hint(meta, tmesh, pkg).angle(matrix)
                    report = {"frente_ok": None if ang is None else bool(ang < FRONT_MAX_DEG), "frente": {"angulo_deg": None if ang is None else round(ang, 1)}}
        except (CatalogError, P.PieceError, PieceColorError, RolesError, ValueError) as exc:
            if isinstance(exc, RolesWizardError):
                raise
            raise RolesWizardError(tr("roles_wizard.no_calza_pieza", ref=ref, error=exc)) from exc
        ok_front = report.get("frente_ok")
        ang = (report.get("frente") or {}).get("angulo_deg")
        ambigua = bool(report.get("ambigua"))
        shown = "?" if ang is None else f"{ang:.0f}"
        yaw = report.get("yaw180") or {}
        girada = bool(yaw.get("girada")) and ok_front is True
        margen = yaw.get("margen_mm")
        if girada:
            msg = tr("roles_wizard.frente_girada", ang=shown, margen=("?" if margen is None else f"{float(margen):.2f}"))
        elif ambigua:
            msg = tr("roles_wizard.frente_ambigua")
        elif ok_front is False:
            msg = tr("roles_wizard.frente_mal", ang=shown)
        elif ok_front is True:
            msg = tr("roles_wizard.frente_bien", ang=shown)
        elif meta.tipo == "cabeza":
            msg = tr("roles_wizard.frente_sin_dato")
        else:
            msg = tr("roles_wizard.frente_no_aplica")
        chk = PieceCheck(ref, matrix.tolist(), report, ok_front, ang, ambigua, msg, frame=frame, key=key, girada=girada,
                         margen_mm=None if margen is None else float(margen))
        if preview:
            chk.parts, chk.warnings = self._real_color_parts(cat, meta, mesh, tmesh, pkg, pal, matrix, report)
        self.last_check = chk
        return chk

    @staticmethod
    def _real_color_parts(cat, meta, mesh, tmesh, pkg, pal, matrix, report) -> tuple[list, list]:
        """(`[(nombre, malla, rgba)]`, avisos): la pieza con el color real, con el ajuste no rígido (y su compuerta) que se aplicaría al guardar."""
        from .. import parts as P
        from ..colorpreview import color_parts

        probe = P.PieceMeta.from_dict(meta.to_dict())
        probe.color = {"similitud": matrix.tolist(), "alineacion": dict(report)}
        fitted, warns = cat.fitted_roles(probe, mesh, pkg)
        return color_parts(fitted.to_textured(pal).transformed(matrix), tmesh), list(warns)

    def save_piece(self, ref: str, similarity=None, confirm_front: bool = False):
        """Guarda una **versión nueva** de la pieza `ref` con este color por roles (como `afm catalogo color`). `similarity`: 4×4 FBX → figura
        entera para una pieza recortada de una figura; sin ella se alinea por momentos (solo sirve con una pieza completa, plan §5.7).
        Devuelve la `PieceMeta` nueva. Si algo falla no queda nada escrito (ni la paleta).

        O09: si en una cabeza la cara del FBX quedó hacia atrás (o no se pudo decidir hacia dónde mira) NO se guarda, salvo que se pase
        `confirm_front=True` (la persona vio el aviso y eligió guardar igual; la ficha anota `alineacion.frente_confirmado`)."""
        from .. import parts as P
        from ..catalog import CatalogError

        if not self.can_save:
            raise RolesWizardError(self.blockers())
        if similarity is None:
            similarity = self.similarity
        chk = self.check_piece(ref, similarity)
        if chk.needs_confirmation and not confirm_front:
            raise RolesWizardError(tr("roles_wizard.frente_no_guardado", mensaje=chk.message))
        if chk.girada and confirm_front and chk.frame is not None:
            chk.frame.report["giro_confirmado"] = True               # la persona vio que la pose se giró 180° y lo aceptó: queda anotado en la ficha
        cat = self._cat()
        try:
            meta, mesh = cat.load(ref)
        except ValueError as exc:
            raise RolesWizardError(str(exc)) from exc
        pkg = self.package()

        def action():
            return cat.add_piece(mesh, P.PieceMeta.from_dict(meta.to_dict()), new_version=True, color_roles=pkg, similarity=similarity,
                                 alignment=chk.frame if similarity is None else None, front_confirmed=bool(confirm_front and chk.needs_confirmation))

        try:
            new, warns = self._with_palette(cat, action)
        except (CatalogError, P.PieceError) as exc:
            raise RolesWizardError(str(exc)) from exc
        self.warnings = list(warns)
        self.last_check = None
        return new

    def save_figure(self, path: str) -> str:
        """Escribe el paquete de roles de la figura base en `path` (`.color.npz`) y lo anota en `project.color.roles` (ruta relativa al
        proyecto si queda dentro de su carpeta). Hace falta una figura abierta y la línea abierta (de ahí sale la paleta, plan §12.2)."""
        from ..project import ColorSpec

        if not self.can_save:
            raise RolesWizardError(self.blockers())
        if not getattr(self.ctrl, "loaded", False):
            raise RolesWizardError(tr("roles_wizard.sin_figura"))
        cat = self._cat()
        pkg = self.package()
        path = os.path.abspath(path if path.endswith(".npz") else path + ".color.npz")
        proj_path = getattr(self.ctrl.session, "project_path", None)
        shown = path
        if proj_path:
            rel = os.path.relpath(path, os.path.dirname(os.path.abspath(proj_path)))
            if not rel.startswith(".."):
                shown = rel

        def action():
            try:
                pkg.save(path)
            except OSError as exc:
                raise RolesWizardError(f"{path}: {exc}") from exc
            return path

        self._with_palette(cat, action)
        spec = self.ctrl.session.project.color or ColorSpec()
        spec.roles = shown
        self.ctrl.session.project.color = spec
        self.ctrl.color_report = None
        self.ctrl._changed(tr("roles_wizard.guardada_figura", ruta=shown, roles=", ".join(pkg.roles)), scene=False)
        return path

    # ------------------------------------------------------------------ vista previa
    def preview_lut(self, highlight: int | None = None, mode: str = "clusters") -> np.ndarray:
        """Tabla (k, 3) uint8 con el color de cada cluster: el propio (`clusters`) o el del rol elegido (`roles`; sin rol o sin decidir = plano).
        Con `highlight` ese cluster se pinta de `HIGHLIGHT_RGB` y el resto con su color lavado hacia un gris claro (`DIM_MIX`)."""
        self._need_loaded()
        pal = self.final_palette() if mode == "roles" else None
        out = np.zeros((self.proposal.k, 3), np.uint8)
        for c in range(self.proposal.k):
            if mode == "roles":
                ch = self.choices.get(c)
                out[c] = hex_to_rgb(pal.hex_of(ch.role)) if ch is not None and ch.kind != "none" else hex_to_rgb(DEFAULT_FLAT)
            else:
                out[c] = self.proposal.centers_rgb[c]
        if highlight is not None:
            washed = np.rint(out.astype(float) * (1.0 - DIM_MIX) + np.asarray(DIM_TO, float) * DIM_MIX)
            out = washed.astype(np.uint8)
            out[highlight] = HIGHLIGHT_RGB
        return out

    def preview_parts(self, highlight: int | None = None, mode: str = "clusters") -> list:
        """`[TexturedPart]` del FBX para `PreviewRenderer.render_textured`, con cada cluster pintado de su color (o del rol elegido)."""
        import trimesh

        from .render import TexturedPart

        self._need_loaded()
        image = np.ascontiguousarray(self.preview_lut(highlight, mode)[np.asarray(self.proposal.labels_atlas, np.intp)])
        mesh = trimesh.Trimesh(np.asarray(self.tm.vertices, float), np.asarray(self.tm.faces, np.int64), process=False)
        return [TexturedPart("fbx", mesh, np.ascontiguousarray(self.tm.corner_uv, np.float32), image)]
