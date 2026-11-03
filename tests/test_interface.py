"""Contrato de interfaz de la línea: talles, etiquetas, calibración desde el catálogo y CLI (plan de compatibilidad, Fase 1)."""
import dataclasses
import json
import os
import shutil

import numpy as np
import pytest
from shapely import affinity

from afm import interface as I
from afm.catalog import Catalog
from afm.cli import main
from afm.compat import recipe_key
from afm.ports import Port, PortFace, PortFrame

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


def clone(cat, ref, new_id, k=1.0, dx=0.0, height=None):
    """Copia de una pieza con las caras de sus puertos escaladas por `k` y el centro corrido `dx` en x."""
    meta, mesh = cat.load(ref)
    ports = {}
    for name, p in meta.puertos.items():
        face = p.face
        if face is not None:
            face = PortFace.from_shape(affinity.scale(face.shape(), k, k, origin=(0, 0)))
        c = np.asarray(p.frame.center, float) + np.array([dx, 0.0, 0.0])
        ports[name] = Port(p.clase, p.rol, PortFrame(c, p.frame.axis, p.frame.ref), face, p.sweep_deg)
    new = dataclasses.replace(meta, id=new_id, version=1, puertos=ports, origen={"figura": "otra.stl"}, stl="", sha256="",
                              altura_figura_mm=height if height is not None else meta.altura_figura_mm)
    cat.add_piece(mesh, new)
    return new


@pytest.fixture()
def cat(tmp_path):
    root = tmp_path / "linea"
    shutil.copytree(EJEMPLO, root)
    return Catalog.open(str(root))


def simple_contract(**kw):
    return I.Contract(100.0, {
        "hombro": I.ClassContract("hombro", talles=[I.Talle("S", 4.9, 0.4), I.Talle("M", 5.8, 0.4), I.Talle("L", 6.8, 0.4)],
                                  x_mm=11.7, x_tol_mm=1.0, pared_min_mm=1.2),
        "cuello": I.ClassContract("cuello", escala_max=0.1),
    })


def plug(meta, name):
    return meta.puertos[name]


# ---------------------------------------------------------------------------------------------------- datos
def test_contract_round_trip_and_hash():
    c = simple_contract()
    c2 = I.Contract.from_dict(json.loads(json.dumps(c.to_dict())))
    assert c2.to_dict() == c.to_dict()
    assert c2.hash() == c.hash() and len(c.hash()) == 12
    c3 = simple_contract()
    c3.clases["hombro"].talles[0].radio_mm = 4.7
    assert c3.hash() != c.hash()


def test_contract_rejects_other_version_and_missing_height():
    with pytest.raises(I.ContractError):
        I.Contract.from_dict({"version": 2, "altura_mm": 100})
    with pytest.raises(I.ContractError):
        I.Contract.from_dict({"version": 1})


def test_problems_overlap_and_physical_floor(cat):
    c = simple_contract()
    assert c.problems(cat.params) == []
    c.clases["hombro"].talles.append(I.Talle("X", 5.0, 0.4))          # pisa a S (4,9) y M (5,8)
    assert any("se solapan" in p for p in c.problems(cat.params))
    c = simple_contract()
    c.clases["hombro"].talles[0] = I.Talle("S", 3.0, 0.4)              # piso del hombro: 4,1/2 + 1,2 = 3,25
    assert any("piso físico" in p for p in c.problems(cat.params))
    assert I.floor_radius("hombro", cat.params) == pytest.approx(cat.params.shoulder_pin.shaft_d / 2 + cat.params.min_wall)


# ---------------------------------------------------------------------------------------------------- etiquetar
def test_label_port_talle_x_and_axis(cat):
    c = simple_contract()
    arm = cat.meta("brazo-muneco-01")
    lab = I.label_port(c, plug(arm, "hombro"), arm.altura_figura_mm)      # r 7,07: a 0,27 de L (6,8 ± 0,4); x 15,66
    assert lab.talle == "L" and lab.x_ok is False and not lab.en_contrato
    assert lab.eje_ok is True
    new = clone(cat, "brazo-muneco-01", "brazo-b", k=5.8 / 7.07, dx=11.7 - 15.66)
    lab = I.label_port(c, plug(new, "hombro"), new.altura_figura_mm)
    assert lab.talle == "M" and lab.x_ok and lab.en_contrato
    far = clone(cat, "brazo-muneco-01", "brazo-c", k=5.3 / 7.07, dx=11.7 - 15.66)   # 5,3 queda entre S y M
    lab = I.label_port(c, plug(far, "hombro"), far.altura_figura_mm)
    assert lab.talle is None and lab.mas_cercano in ("S", "M") and any("ningún talle" in a for a in lab.avisos)


def test_label_rescales_to_contract_height(cat):
    c = simple_contract()
    a = clone(cat, "brazo-muneco-01", "brazo-h", k=5.8 / 7.07 * 0.5, dx=11.7 / 2 - 15.66, height=50.0)   # figura de 50 mm: x = 5,85
    lab = I.label_port(c, plug(a, "hombro"), 50.0)
    assert lab.r_eq == pytest.approx(5.8, abs=0.02) and lab.talle == "M" and lab.x_ok


def test_label_tilted_axis_and_soft_class(cat):
    c = simple_contract()
    arm = cat.meta("brazo-muneco-01")
    p = plug(arm, "hombro")
    t = np.radians(3.0)
    axis = np.array([-np.cos(t), np.sin(t), 0.0])
    tilted = Port(p.clase, p.rol, PortFrame(p.frame.center, axis, p.frame.ref), p.face)
    lab = I.label_port(c, tilted, 100.0)
    assert lab.eje_ok is False and lab.eje_deg == pytest.approx(3.0, abs=0.01)
    head = cat.meta("cabeza-muneco-01")
    assert I.label_port(c, head.puertos["cuello"], 100.0) is None        # el cuello tiene contrato blando: no hay talle


def test_label_piece_skips_right_side_of_mirrorable(cat):
    c = I.Contract(100.0, {"hombro": simple_contract().clases["hombro"]})
    labs = I.label_piece(c, cat.meta("torso-muneco-01"))                 # torso no espejable: hombro_izq y hombro_der
    assert set(labs) == {"hombro_izq", "hombro_der"}
    assert set(I.label_piece(c, cat.meta("brazo-muneco-01"))) == {"hombro"}
    assert I.label_piece(c, cat.meta("torso-muneco-01"), only_plugs=True) == {}


# ---------------------------------------------------------------------------------------------------- calibrar
def test_cluster_radii():
    assert I.cluster_radii([6.8, 4.9, 5.0, 6.7, 6.9], 0.8) == [[4.9, 5.0], [6.7, 6.8, 6.9]]


def test_propose_groups_radii_into_talles(cat):
    clone(cat, "brazo-muneco-01", "brazo-a", k=5.8 / 7.07, dx=-3.96)
    clone(cat, "brazo-muneco-01", "brazo-b", k=5.9 / 7.07, dx=-4.0)
    clone(cat, "brazo-muneco-01", "brazo-c", k=4.9 / 7.07, dx=-5.0)
    prop = I.propose(cat)
    h = prop.contract.clases["hombro"]
    assert [t.id for t in h.talles] == ["S", "M", "L"]                    # 4,9 · 5,8–5,9 · 7,07 (el del muñeco)
    assert [t.radio_mm for t in h.talles][:2] == pytest.approx([4.9, 5.85], abs=0.02)
    assert prop.contract.problems(cat.params) == []
    assert prop.contract.clases["cuello"].soft and "muneca" in prop.contract.clases
    assert prop.contract.clases["muneca"].x_mm is None                    # el origen de la mano no es una posición lateral
    assert I.label_port(prop.contract, cat.meta("mano-muneco-01").puertos["muneca"], 100.0).x_ok is None
    assert prop.contract.hash() == I.propose(cat).contract.hash()         # determinista


def test_propose_warns_below_floor_and_with_few_samples(cat):
    clone(cat, "brazo-muneco-01", "brazo-fino", k=2.0 / 7.07, dx=-3.0)    # r 2,0 < piso 3,25
    prop = I.propose(cat)
    assert any("piso físico" in n for n in prop.notas)
    assert any("orientativos" in n for n in prop.notas)
    assert any("piso físico" in p for p in prop.contract.problems(cat.params))


def test_propose_scales_to_line_height(cat):
    clone(cat, "brazo-muneco-01", "brazo-chico", k=1.0, height=50.0)      # misma cara en una figura de 50 mm: ×2 a 100 mm
    rows = I.propose(cat).muestras["hombro"]
    assert sorted(round(r[2], 1) for r in rows) == [7.1, 14.1]


# ---------------------------------------------------------------------------------------------------- catálogo
def test_catalog_contract_save_load_and_verify(cat):
    assert cat.contract() is None
    base = cat.verify()
    assert base.ok
    prop = I.propose(cat)
    cat.set_contract(prop.contract)
    again = Catalog.open(cat.root)
    assert again.contract().hash() == prop.contract.hash()
    assert "interfaz" in json.load(open(os.path.join(cat.root, "linea.json")))
    rep = again.verify()
    assert rep.ok, rep.problems
    clone(again, "brazo-muneco-01", "brazo-raro", k=0.7)                   # r 4,9 con un solo talle: fuera de contrato
    rep = again.verify()
    assert rep.ok
    assert any("brazo-raro" in w and "fuera de contrato" in w for w in rep.warnings)
    assert again.clear_contract() and again.contract() is None
    assert not any("fuera de contrato" in w for w in again.verify().warnings)


def test_set_contract_refuses_an_incoherent_one(cat):
    bad = simple_contract()
    bad.clases["hombro"].talles.append(I.Talle("X", 5.0, 0.4))
    with pytest.raises(I.ContractError):
        cat.set_contract(bad)
    assert cat.contract() is None


def test_verify_flags_contract_height_mismatch(cat):
    c = I.propose(cat).contract
    c.altura_mm = 130.0
    cat.line["interfaz"] = c.to_dict()
    assert any("altura" in p for p in cat.verify().problems)


def test_contract_hash_enters_the_compat_key(cat):
    from afm.recipe import Recipe

    rec = Recipe(personaje="x", linea=cat.name, lugares={"torso": "torso-muneco-01@1", "cabeza": "cabeza-muneco-01@1",
                                                         "brazo": "brazo-muneco-01@1", "pierna": "pierna-muneco-01@1"})
    k0, _ = recipe_key(rec, cat)
    assert recipe_key(rec, cat)[0] == k0                                    # sin contrato la clave es estable
    cat.set_contract(I.propose(cat).contract)
    k1, _ = recipe_key(rec, cat)
    assert k1 != k0
    c = cat.contract()
    c.clases["hombro"].x_tol_mm += 0.5
    cat.set_contract(c)
    assert recipe_key(rec, cat)[0] not in (k0, k1)


# ---------------------------------------------------------------------------------------------------- CLI
def test_cli_contract_flow(cat, capsys):
    assert main(["catalogo", "contrato", cat.root]) == 0
    assert "no tiene contrato" in capsys.readouterr().out
    assert main(["catalogo", "contrato", cat.root, "--validar"]) == 1
    capsys.readouterr()
    assert main(["catalogo", "contrato", cat.root, "--calibrar"]) == 0
    out = capsys.readouterr().out
    assert "Propuesta" in out and "no se guardó nada" in out and Catalog.open(cat.root).contract() is None
    assert main(["catalogo", "contrato", cat.root, "--calibrar", "--guardar"]) == 0
    assert "huella" in capsys.readouterr().out
    assert Catalog.open(cat.root).contract() is not None
    assert main(["catalogo", "contrato", cat.root, "--ver"]) == 0
    out = capsys.readouterr().out
    assert "brazo-muneco-01@1" in out and "talle" in out
    assert main(["catalogo", "contrato", cat.root, "--validar"]) == 0
    assert "coherente" in capsys.readouterr().out
    assert main(["catalogo", "verificar", cat.root]) == 0
    capsys.readouterr()
    assert main(["catalogo", "contrato", cat.root, "--quitar"]) == 0
    assert Catalog.open(cat.root).contract() is None


def test_cli_calibrar_refuses_to_save_a_problematic_proposal(cat, capsys):
    clone(cat, "brazo-muneco-01", "brazo-fino", k=2.0 / 7.07, dx=-3.0)
    assert main(["catalogo", "contrato", cat.root, "--calibrar", "--guardar"]) == 1
    assert Catalog.open(cat.root).contract() is None
