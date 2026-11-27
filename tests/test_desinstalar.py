"""D20 · quitar un paquete instalado (`desinstalar.py`, PLAN_catalogos_online.md §5.4)."""
import json
import os
import shutil

import pytest

from afm import desinstalar as DS
from afm import exportar as X
from afm import instalar as I
from afm.catalog import Catalog
from test_exportar import OWNER, add_arm, add_recipe, cat  # noqa: F401  (cat es una fixture)


@pytest.fixture
def dest(tmp_path):
    return Catalog.create(str(tmp_path / "destino"), "proceres-130", 130.0, 0.4)


def export(cat, tmp_path, name, **kw):
    return X.export_package(cat, str(tmp_path / name), **{**OWNER, **kw}).path


def install(zip_path, root, **kw):
    plan = I.plan_install(zip_path, str(root), **kw)
    assert plan.ok, [str(i) for i in plan.errors]
    return I.apply(plan)


def piezas(c):
    return sorted(m.ref for m in Catalog.open(c.root).list(all_versions=True))


def digest(root):
    """El árbol del catálogo sin la papelera (la papelera es donde va lo quitado; que quede vacía o no es indiferente)."""
    return {k: v for k, v in I.tree_digest(root).items() if not k.startswith(".papelera")}


@pytest.fixture
def heroe(cat, dest, tmp_path):
    """Un héroe (receta + su brazo y su torso) instalado en `dest` con el id `proceres-130-heroe`."""
    ref = add_arm(cat).ref
    add_recipe(cat, "heroe", {"brazo": ref})
    z = export(cat, tmp_path, "heroe.zip", recetas=["heroe"], id="heroe")
    install(z, dest.root)
    return dest


def test_el_plan_no_escribe_y_lista_lo_que_se_va(heroe):
    before = I.tree_digest(heroe.root)
    plan = DS.plan_remove(heroe.root, "heroe")
    assert plan.ok and plan.recetas == ["heroe"] and "brazo-test-01@1" in plan.piezas and "torso-test-01@1" in plan.piezas
    assert not plan.conservadas and not plan.dependientes
    assert I.tree_digest(heroe.root) == before
    assert plan.describe()[0].startswith("Quitar «heroe»")


def test_quitar_manda_todo_a_la_papelera_y_saca_el_registro(heroe):
    plan = DS.plan_remove(heroe.root, "heroe")
    res = DS.apply_remove(plan)
    assert sorted(res.piezas) == sorted(plan.piezas) and res.recetas == ["heroe"]
    assert piezas(heroe) == [] and not os.path.exists(os.path.join(heroe.root, "figuras", "heroe.receta.json"))
    assert "heroe" not in I.load_registry(heroe.root)["paquetes"]
    assert os.path.isfile(os.path.join(res.papelera_recetas, "figuras", "heroe.receta.json"))
    assert Catalog.open(heroe.root).verify().ok
    restored, errors = Catalog.open(heroe.root).restore_deleted()                  # lo quitado se recupera
    assert not errors and sorted(m.ref for m in restored) == sorted(plan.piezas)


def test_no_esta_instalado(dest):
    with pytest.raises(I.InstallError, match="no figura como instalado"):
        DS.plan_remove(dest.root, "nada")


def test_una_pieza_que_usa_una_receta_propia_se_conserva(heroe):
    mine = os.path.join(heroe.root, "figuras", "mia.receta.json")
    with open(os.path.join(heroe.root, "figuras", "heroe.receta.json"), encoding="utf-8") as f:
        data = json.load(f)
    data["personaje"] = "mia"
    with open(mine, "w", encoding="utf-8") as f:
        json.dump(data, f)
    plan = DS.plan_remove(heroe.root, "heroe")
    kept = {k.name: k.why for k in plan.conservadas}
    assert plan.piezas == [] and plan.recetas == ["heroe"]
    assert "mia.receta.json" in kept["brazo-test-01@1"] and "torso-test-01@1" in kept
    DS.apply_remove(plan)
    assert piezas(heroe) == ["brazo-test-01@1", "torso-test-01@1"]                   # la receta propia sigue entera
    assert Catalog.open(heroe.root).usages("brazo-test-01@1")


def test_un_proyecto_en_las_carpetas_pedidas_tambien_cuenta(heroe, tmp_path):
    proj = tmp_path / "figuras_mias"
    proj.mkdir()
    (proj / "f.proyecto").write_text(json.dumps({"parts": {"brazo": {"id": "brazo-test-01", "version": 1}}}), encoding="utf-8")
    assert "brazo-test-01@1" in DS.plan_remove(heroe.root, "heroe").piezas
    plan = DS.plan_remove(heroe.root, "heroe", search=[str(proj)])
    assert "brazo-test-01@1" not in plan.piezas and any("f.proyecto" in k.why for k in plan.conservadas)


def test_la_receta_de_otro_paquete_protege_las_piezas_que_comparten(cat, dest, tmp_path):
    ref = add_arm(cat).ref
    z_piezas = export(cat, tmp_path, "base.zip", piezas=[ref], id="base")
    add_recipe(cat, "heroe", {"brazo": ref})
    z_heroe = export(cat, tmp_path, "heroe.zip", recetas=["heroe"], id="heroe")
    install(z_piezas, dest.root)
    install(z_heroe, dest.root)                                                     # sus piezas ya estaban: se omiten
    plan = DS.plan_remove(dest.root, "base")
    assert plan.piezas == [] and {k.name for k in plan.conservadas} == {"brazo-test-01@1"}
    assert "heroe.receta.json" in plan.conservadas[0].why


def test_los_dependientes_piden_confirmacion(heroe, cat, dest, tmp_path):
    z = export(cat, tmp_path, "otro.zip", piezas=["brazo-test-01"], id="otro")
    install(z, dest.root)
    reg = I.load_registry(dest.root)
    reg["paquetes"]["otro"]["requiere"] = ["heroe@>=1.0.0"]
    with open(os.path.join(dest.root, I.REGISTRY_FILE), "w", encoding="utf-8") as f:
        json.dump(reg, f)
    plan = DS.plan_remove(dest.root, "heroe")
    assert plan.dependientes and "«otro»" in plan.dependientes[0]
    before = I.tree_digest(dest.root)
    with pytest.raises(I.InstallError, match="requieren este"):
        DS.apply_remove(plan)
    assert I.tree_digest(dest.root) == before
    DS.apply_remove(plan, confirmar_dependientes=True)
    assert "heroe" not in I.load_registry(dest.root)["paquetes"]


def test_instalar_guarda_requiere_en_el_registro(cat, dest, tmp_path):
    ref = add_arm(cat).ref
    base = X.export_package(cat, str(tmp_path / "b.zip"), piezas=[ref], id="con-dep", **OWNER).paquete
    base.requiere = ["otra-cosa@^1.0.0"]
    z = X.export_package(cat, str(tmp_path / "c.zip"), piezas=[ref], base=base, **OWNER).path
    install(z, dest.root)
    assert I.load_registry(dest.root)["paquetes"]["con-dep"]["requiere"] == ["otra-cosa@^1.0.0"]


def test_si_el_registro_cambia_hay_que_volver_a_planificar(heroe):
    plan = DS.plan_remove(heroe.root, "heroe")
    with open(os.path.join(heroe.root, I.REGISTRY_FILE), "a", encoding="utf-8") as f:
        f.write("\n")
    with pytest.raises(I.InstallError, match="volvé a planificar"):
        DS.apply_remove(plan)


@pytest.mark.parametrize("fail_at", ["registro", "receta"])
def test_un_fallo_deja_el_catalogo_como_estaba(heroe, monkeypatch, fail_at):
    before = digest(heroe.root)
    plan = DS.plan_remove(heroe.root, "heroe")
    if fail_at == "registro":
        monkeypatch.setattr(I, "_dump", lambda obj: (_ for _ in ()).throw(OSError("disco lleno")))
    else:
        real = os.replace
        monkeypatch.setattr(DS.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("sin permiso")) if a.endswith(".receta.json") else real(a, b))
    with pytest.raises(OSError):
        DS.apply_remove(plan)
    monkeypatch.undo()
    assert digest(heroe.root) == before
    assert Catalog.open(heroe.root).verify().ok and "heroe" in I.load_registry(heroe.root)["paquetes"]
    shutil.rmtree(os.path.join(heroe.root, ".papelera"), ignore_errors=True)
