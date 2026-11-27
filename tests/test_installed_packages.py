"""D20 · «Paquetes instalados» sin GTK (`afm.gui.installed_packages`): lista, estados, versiones, quitar con confirmación, errores atribuidos y piezas que faltan."""
import glob
import json
import os

import pytest

from afm import desinstalar as DS
from afm import exportar as X
from afm import instalar as I
from afm.catalog import Catalog
from afm.gui import catalog_online as CO
from afm.gui import installed_packages as IP
from afm.i18n import set_language
from test_dependencias import ent, entorno, idx  # noqa: F401  (entorno es autouse)
from test_catalog import arm_piece
from test_exportar import OWNER, add_arm, add_recipe, cat  # noqa: F401
from test_instalar import dest  # noqa: F401


@pytest.fixture(autouse=True)
def _idioma():
    set_language("es")
    yield
    set_language("es")


@pytest.fixture
def heroe(cat, dest, tmp_path):
    """`heroe` 1.0.0 instalado en `dest` desde «owner/heroe»."""
    ref = add_arm(cat).ref
    add_recipe(cat, "heroe", {"brazo": ref})
    z = X.export_package(cat, str(tmp_path / "heroe.zip"), recetas=["heroe"], id="heroe", github="owner/heroe", **OWNER).path
    plan = I.plan_install(z, dest.root)
    assert plan.ok
    I.apply(plan)
    return dest


def online(root, *indexes) -> CO.OnlineState:
    st = CO.OnlineState([], root)
    st.indexes = list(indexes)
    return st


def rows(root, *entries, **over):
    st = IP.InstalledState(online(root, idx(*entries, **over)) if entries else online(root))
    return st, st.rows()


def test_un_renglon_por_paquete_con_version_y_origen(heroe):
    st, rs = rows(heroe.root, ent("heroe", "1.0.0", github="owner/heroe"))
    (r,) = rs
    assert (r.id, r.version, r.origen, r.estado) == ("heroe", "1.0.0", "owner/heroe", IP.AL_DIA)
    assert r.piezas == ["brazo-test-01@1", "torso-test-01@1"] and r.recetas == ["heroe"] and r.titulo and "1.0.0" in r.subtitulo
    assert dict(st.detail_rows(r))["Origen"] == "owner/heroe"


def test_estados_frente_al_indice(heroe):
    _st, (r,) = rows(heroe.root, ent("heroe", "1.1.0", github="owner/heroe", versions=[("1.0.0", False)]))
    assert r.estado == IP.ACTUALIZACION and r.actualizar_a == "1.1.0" and r.puede_actualizar and "1.1.0" in r.estado_texto
    _st, (r,) = rows(heroe.root, ent("heroe", "1.1.0", github="owner/heroe", retiradas=["1.0.0"]))
    assert r.estado == IP.RETIRADA and r.alertas
    _st, (r,) = rows(heroe.root, ent("heroe", "1.0.0", github="owner/heroe"), bloqueados=[{"id": "heroe", "motivo": "reclamo", "fecha": "2026-11-01"}])
    assert r.estado == IP.BLOQUEADO and "reclamo" in r.alertas[0]
    _st, (r,) = rows(heroe.root, ent("heroe", "1.1.0", github="otro/repo"))                 # otro repo: no se ofrece (suplantación, §5.7)
    assert r.estado == IP.SIN_INDICE and r.entry is None and not r.puede_actualizar
    _st, (r,) = rows(heroe.root)
    assert r.estado == IP.SIN_INDICE


def test_versiones_y_destinos_de_instalacion(heroe):
    st, (r,) = rows(heroe.root, ent("heroe", "1.2.0", github="owner/heroe", versions=[("1.0.0", False), ("1.1.0", False), ("1.3.0-beta", True)], retiradas=["1.1.0"]))
    vs = {v.version: v for v in st.versions(r)}
    assert [v.version for v in st.versions(r)] == ["1.3.0-beta", "1.2.0", "1.1.0", "1.0.0"]
    assert vs["1.0.0"].instalada and vs["1.1.0"].retirada and vs["1.3.0-beta"].prerelease and "retirada" in vs["1.1.0"].texto
    assert st.version_target(r, "1.2.0")[1] == "1.2.0" and st.version_target(r, "1.1.0") is None and st.version_target(r, "1.0.0") is None
    assert st.update_target(r) is not None and st.update_target(r)[1] == "1.2.0"
    st2, (r2,) = rows(heroe.root)
    assert st2.versions(r2) == [] and st2.update_target(r2) is None


def test_un_registro_ilegible_se_dice_y_no_rompe_la_lista(heroe, tmp_path):
    bad = tmp_path / "otro"
    bad.mkdir()
    (bad / I.REGISTRY_FILE).write_text("{no es json", encoding="utf-8")
    st = IP.InstalledState(CO.OnlineState([], ""))
    st.online.roots = lambda: [str(bad), heroe.root]
    st.online.indexes = []
    assert [r.id for r in st.rows()] == ["heroe"] and "otro" in st.errors[0]


def test_quitar_pide_confirmacion_y_no_escribe_sin_ella(heroe):
    st, (r,) = rows(heroe.root)
    before = I.tree_digest(heroe.root)
    plan = st.plan_remove(r)
    title, lines, needs_dep = st.remove_confirmation(plan)
    assert "heroe" in title and not needs_dep and any("heroe" in l for l in lines) and any("papelera" in l for l in lines)
    t = st.task_remove(plan).start()
    t.join(10)
    assert t.status == CO.FAILED and I.tree_digest(heroe.root) == before                       # sin `acepto` no se escribe nada
    t = st.task_remove(plan, acepto=True).start()
    t.join(10)
    assert t.status == CO.DONE and isinstance(t.result, DS.RemoveResult)
    assert st.rows() == [] and "heroe" in st.result_lines(t.result)[0]
    assert Catalog.open(heroe.root).verify().ok


def test_los_dependientes_cambian_el_boton_y_exigen_confirmar(heroe, cat, tmp_path):
    z = X.export_package(cat, str(tmp_path / "otro.zip"), piezas=["brazo-test-01"], id="otro", github="owner/otro", **OWNER).path
    I.apply(I.plan_install(z, heroe.root))
    reg = I.load_registry(heroe.root)
    reg["paquetes"]["otro"]["requiere"] = ["heroe@>=1.0.0"]
    with open(os.path.join(heroe.root, I.REGISTRY_FILE), "w", encoding="utf-8") as f:
        json.dump(reg, f)
    st = IP.InstalledState(online(heroe.root))
    r = next(x for x in st.rows() if x.id == "heroe")
    plan = st.plan_remove(r)
    assert st.remove_confirmation(plan)[2]
    t = st.task_remove(plan, acepto=True).start()
    t.join(10)
    assert t.status == CO.FAILED and "requieren este" in t.error_text
    t = st.task_remove(plan, acepto=True, confirmar_dependientes=True).start()
    t.join(10)
    assert t.status == CO.DONE


def test_diagnostico_atribuye_cada_problema_al_paquete(heroe):
    st = IP.InstalledState(online(heroe.root))
    assert st.diagnose(heroe.root).ok
    stl = glob.glob(os.path.join(heroe.root, "piezas", "brazo", "*.stl"))[0]
    with open(stl, "ab") as f:
        f.write(b" ")
    os.remove(glob.glob(os.path.join(heroe.root, "figuras", "*.receta.json"))[0])
    diag = st.diagnose(heroe.root)
    assert not diag.ok and not diag.propios
    texts = " | ".join(diag.of("heroe"))
    assert "brazo-test-01@1" in texts and "falta la receta heroe" in texts
    t = st.task_diagnose(heroe.root).start()
    t.join(30)
    assert t.status == CO.DONE and t.result.of("heroe") == diag.of("heroe")


def test_un_problema_de_una_pieza_propia_no_es_de_ningun_paquete(heroe):
    mesh, meta = arm_piece("brazo-mio-01")
    Catalog.open(heroe.root).add_piece(mesh, meta)
    stl = os.path.join(heroe.root, "piezas", "brazo", "brazo-mio-01@1.stl")
    with open(stl, "ab") as f:
        f.write(b" ")
    diag = IP.InstalledState(online(heroe.root)).diagnose(heroe.root)
    assert any("brazo-mio-01@1" in t for t in diag.propios) and diag.of("heroe") == []


def test_piezas_que_faltan_y_donde_buscarlas(heroe, tmp_path):
    st = IP.InstalledState(online(heroe.root, idx(
        ent("manos-pro", "1.0.0", github="owner/manos", nombre="Manos pro", piezas=[{"ref": "mano-x-01@1", "tipo": "mano", "puertos": {}}]),
        ent("otra", "2.0.0", github="owner/otra", piezas=[{"ref": "mano-x-01@1", "tipo": "mano", "puertos": {}}], update_check="inaccesible"))))
    assert st.missing_refs(heroe.root, ["brazo-test-01@1", "mano-x-01@1", "mano-x-01@1", "cabeza-y-01@1"]) == ["mano-x-01@1", "cabeza-y-01@1"]
    recipe = os.path.join(heroe.root, "figuras", "heroe.receta.json")
    with open(recipe, encoding="utf-8") as f:
        d = json.load(f)
    d["lugares"]["mano"] = "mano-x-01@1"
    d["accesorios"] = ["espada-z-01@2"]
    p2 = tmp_path / "con_faltas.receta.json"
    p2.write_text(json.dumps(d), encoding="utf-8")
    missing = st.recipe_missing(heroe.root, str(p2))
    assert missing == ["mano-x-01@1", "espada-z-01@2"] and st.recipe_missing(heroe.root, recipe) == []
    offers = st.find_pieces(missing)
    assert [o.ref for o in offers] == missing and offers[0].encontrada and [e.id for e in offers[0].entradas] == ["manos-pro"]   # la inaccesible no se ofrece
    assert not offers[1].encontrada
    lines = st.offer_lines(offers)
    assert "Manos pro" in lines[0] and "no está en los índices" in lines[1]
    assert st.find_pieces(["mano-x-01"])[0].encontrada                                      # `id` sin versión: cualquiera
    assert st.recipe_missing(heroe.root, str(tmp_path / "no_existe.json")) == []
