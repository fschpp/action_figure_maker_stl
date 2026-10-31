"""El arma viaja con la figura: referencia en el proyecto, STL aparte y dentro del ensamblado, al lado de la figura."""
import json
import os

import numpy as np
import pytest
import trimesh

from afm import accessories as A
from afm import props as P
from afm import solids as S
from afm import weapon as W
from afm.catalog import Catalog
from afm.gui.session import Session
from afm.project import Project, demo_project
from afm.testfigure import make_doll

from test_weapon import HAND_AG, _sword_manifold, cat_hand, sword_stl, std_hand       # noqa: F401  (fixtures)


@pytest.fixture(scope="module")
def doll_stl(tmp_path_factory):
    d = tmp_path_factory.mktemp("doll")
    m = make_doll()
    m.apply_scale(0.05)
    path = str(d / "muneco.stl")
    m.export(path)
    return path


@pytest.fixture()
def weapon_cat(cat_hand, sword_stl):                                      # noqa: F811
    path, pts = sword_stl
    meta, _ = W.add_weapon_to_catalog(cat_hand, W.load_weapon_mesh(path, 10.0), W.WeaponSpec(path, 10.0, pts), piece_id="excalibur-01")
    return cat_hand, meta


# ---------------------------------------------------------------- proyecto
def test_project_without_accessories_is_unchanged():
    assert "accesorios" not in demo_project().to_dict()


def test_project_accessories_round_trip():
    p = demo_project()
    p.accesorios = [A.AccessoryRef("/cat/linea", "excalibur-01@1", "ab" * 32)]
    d = json.loads(json.dumps(p.to_dict()))
    assert d["accesorios"] == [{"catalogo": "/cat/linea", "pieza": "excalibur-01@1", "sha256": "ab" * 32}]
    assert Project.from_dict(d).accesorios == p.accesorios


# ---------------------------------------------------------------- colocación al lado de la figura
def test_place_beside_puts_the_accessories_to_the_right_on_the_same_bed():
    fig = trimesh.creation.box((20, 10, 50))
    fig.apply_translation([0, 0, 25 + 3.0])                                # la figura apoya en z = 3
    a, b = (A.print_pose(trimesh.creation.box((30, 4, 2))) for _ in range(2))
    pa, pb = A.place_beside(fig, [a, b], gap=10.0)
    assert pa.bounds[0][0] == pytest.approx(fig.bounds[1][0] + 10.0) and pa.bounds[0][2] == pytest.approx(3.0)
    assert pb.bounds[0][0] == pytest.approx(pa.bounds[1][0] + 10.0)
    assert (pa.bounds[0][1] + pa.bounds[1][1]) / 2 == pytest.approx(0.0)
    up = trimesh.creation.box((1, 1, 1))
    up.apply_translation([0, 0, 7])
    assert A.print_pose(up).bounds[0][2] == pytest.approx(0.0) and up.bounds[0][2] > 6.0        # copia: no toca la original


def test_load_ref_checks_type_and_hash(weapon_cat):
    cat, meta = weapon_cat
    m, mesh, warns = A.load_ref(A.AccessoryRef(os.path.abspath(cat.root), meta.ref, meta.sha256))
    assert m.tipo == "accesorio" and mesh.is_watertight and warns == []
    _, _, warns = A.load_ref(A.AccessoryRef(os.path.abspath(cat.root), meta.ref, "00" * 32))
    assert warns and "cambió en el catálogo" in warns[0]
    with pytest.raises(A.AccessoryError, match="no un accesorio"):
        A.load_ref(A.AccessoryRef(os.path.abspath(cat.root), "mano-agarre-01@1"))
    with pytest.raises(A.AccessoryError):
        A.load_ref(A.AccessoryRef(os.path.abspath(cat.root), "no-existe-01"))
    with pytest.raises(A.AccessoryError):
        A.load_ref(A.AccessoryRef("/no/hay/catalogo", "x-01"))


# ---------------------------------------------------------------- exportación de la ventana principal
def _built_session(doll_stl):
    s = Session()
    s.load(doll_stl)
    s.auto_symmetry()
    demo = demo_project()
    for l in demo.limbs:
        s.add_limb(l.name, l.kind, l.plane, l.thickness, auto=False)
        s.set_limb_polygon(l.name, l.polygon)
    out = s.build()
    assert out.validation["ok"], out.validation["problems"]
    return s


def test_session_export_carries_the_weapon(doll_stl, weapon_cat, tmp_path):
    cat, meta = weapon_cat
    s = _built_session(doll_stl)
    plain = str(tmp_path / "sin")
    s.export(plain, "muneco")
    base = trimesh.load(os.path.join(plain, "muneco_ensamblada.stl"))
    s.project.accesorios = [A.AccessoryRef(os.path.abspath(cat.root), meta.ref, meta.sha256)]
    out = str(tmp_path / "con")
    info = s.export(out, "muneco")
    asm = trimesh.load(os.path.join(out, "muneco_ensamblada.stl"))
    # el arma está dentro del ensamblado, a la derecha de la figura, y la figura sigue igual
    assert info["archivos"]["accesorios"] == ["piezas/muneco_excalibur-01.stl"] and info["accesorios"][0]["pieza"] == "excalibur-01@1"
    assert os.path.exists(os.path.join(out, "piezas", "muneco_excalibur-01.stl"))
    assert asm.bounds[1][0] > base.bounds[1][0] + 10.0
    assert asm.bounds[0][2] == pytest.approx(base.bounds[0][2], abs=1e-6) and asm.bounds[0][0] == pytest.approx(base.bounds[0][0])
    sword = trimesh.load(os.path.join(out, "piezas", "muneco_excalibur-01.stl"))
    assert sword.bounds[0][2] == pytest.approx(0.0, abs=1e-6)                 # lista para imprimir acostada
    assert asm.volume == pytest.approx(base.volume + sword.volume, rel=1e-3)
    # el proyecto guardado la lleva y se reabre
    d = json.load(open(os.path.join(out, "muneco.proyecto")))
    assert d["accesorios"][0]["pieza"] == "excalibur-01@1"
    s2 = Session.open_project(os.path.join(out, "muneco.proyecto"))
    assert [a.pieza for a in s2.project.accesorios] == ["excalibur-01@1"]


def test_session_export_without_a_matching_catalog_fails_clearly(doll_stl, tmp_path):
    s = _built_session(doll_stl)
    s.project.accesorios = [A.AccessoryRef("/no/hay/catalogo", "excalibur-01@1")]
    with pytest.raises(A.AccessoryError, match="excalibur-01@1"):
        s.export(str(tmp_path / "x"), "muneco")


def test_weapon_fit_uses_the_projects_hand(weapon_cat):
    cat, meta = weapon_cat
    p = Project()
    assert A.project_accessory_fit(p, meta)["nivel"] == "sin_datos"          # sin mano pegada


# ---------------------------------------------------------------- «afm armar»: el arma dentro del ensamblado
def test_armar_puts_the_weapon_in_the_assembled_file(tmp_path, capsys):
    import shutil

    from afm import cli
    from afm.recipe import Recipe

    root = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
    c = str(tmp_path / "linea")
    shutil.copytree(root, c, ignore=shutil.ignore_patterns("compat.json", "COMPATIBILIDAD.md"))
    cat = Catalog.open(c)
    cat.add_accessory(S.to_printable_trimesh(P.make_prop(P.GripSpec(), 10.0)), "espada-prueba-01", P.accessory_grip(P.GripSpec(), 10.0))
    d = json.load(open(os.path.join(c, "recetas", "muneco.receta.json")))
    out_plain, out_acc = str(tmp_path / "a"), str(tmp_path / "b")
    rp = str(tmp_path / "r.json")
    Recipe.from_dict(d).save(rp)
    assert cli.main(["armar", c, rp, "--out", out_plain, "--name", "m"]) == 0
    d["accesorios"] = ["espada-prueba-01"]
    Recipe.from_dict(d).save(rp)
    assert cli.main(["armar", c, rp, "--out", out_acc, "--name", "m"]) == 0
    capsys.readouterr()
    plain = trimesh.load(os.path.join(out_plain, "m_ensamblada.stl"))
    asm = trimesh.load(os.path.join(out_acc, "m_ensamblada.stl"))
    sword = trimesh.load(os.path.join(out_acc, "piezas", "m_espada-prueba-01.stl"))
    assert asm.bounds[1][0] > plain.bounds[1][0] + 10.0 and asm.volume == pytest.approx(plain.volume + sword.volume, rel=1e-3)
    proj = json.load(open(os.path.join(out_acc, "m.proyecto")))
    assert proj["accesorios"][0]["pieza"] == "espada-prueba-01@1" and proj["accesorios"][0]["catalogo"] == os.path.abspath(c)
