"""M01 — `MountSpec`: ida y vuelta, defectos, campos desconocidos y valores inválidos."""
import json

import pytest

from afm.mount import DEFAULT_CLEARANCE, MOUNT_FIELDS, MountError, MountSpec


def _spec(**over):
    d = dict(arma="espada-link@1", lugar="pelvis", ancla=(11.0, -3.0, 58.0), direccion=(0.0, -0.5, 1.0))
    d.update(over)
    return MountSpec(**d)


def test_ida_y_vuelta_por_json():
    s = _spec(lado="der", holgura=0.3, pared=1.5, estaciones=2)
    back = MountSpec.from_dict(json.loads(json.dumps(s.to_dict())))
    assert back == s
    assert back.to_dict() == s.to_dict()


def test_to_dict_es_completo_y_estable():
    d = _spec().to_dict()
    assert set(d) == set(MOUNT_FIELDS)
    assert d["pared"] is None and d["holgura"] == DEFAULT_CLEARANCE and d["estaciones"] == 1
    assert d["ancla"] == [11.0, -3.0, 58.0]                     # listas, no tuplas: igual tras pasar por JSON


def test_defectos_al_leer_un_diccionario_minimo():
    s = MountSpec.from_dict({"arma": "espada-link", "lugar": "pelvis", "ancla": [1, 2, 3], "direccion": [0, 0, 1]})
    assert (s.lado, s.tipo, s.holgura, s.pared, s.estaciones) == ("izq", "lazo", DEFAULT_CLEARANCE, None, 1)
    assert s.ancla == (1.0, 2.0, 3.0)


def test_campo_desconocido_se_rechaza():
    d = _spec().to_dict()
    d["colro"] = 1
    with pytest.raises(MountError, match="colro"):
        MountSpec.from_dict(d)


@pytest.mark.parametrize("falta", ["arma", "lugar", "ancla", "direccion"])
def test_faltan_campos_obligatorios(falta):
    d = _spec().to_dict()
    del d[falta]
    with pytest.raises(MountError, match=falta):
        MountSpec.from_dict(d)


@pytest.mark.parametrize("over", [
    {"arma": "Espada Link"}, {"arma": ""}, {"lugar": "rodilla"}, {"lado": "centro"}, {"tipo": "gancho"},
    {"direccion": (0, 0, 0)}, {"ancla": (1, 2)}, {"ancla": (1, 2, float("nan"))}, {"ancla": ("a", 2, 3)},
    {"holgura": -0.1}, {"holgura": 5}, {"holgura": True}, {"pared": 0}, {"pared": -1}, {"estaciones": 0},
    {"estaciones": 3}, {"estaciones": 1.5},
])
def test_valores_invalidos(over):
    with pytest.raises(MountError):
        _spec(**over)


def test_wall_usa_min_wall_si_no_se_pidio():
    assert _spec().wall(1.2) == 1.2
    assert _spec(pared=1.6).wall(1.2) == 1.6


def test_direccion_unitaria():
    u = _spec(direccion=(0, 3, 4)).unit_direction()
    assert u == pytest.approx((0.0, 0.6, 0.8))


def test_listas_del_json_quedan_como_tuplas():
    s = MountSpec.from_dict(_spec().to_dict())
    assert isinstance(s.ancla, tuple) and isinstance(s.direccion, tuple)
    hash(s)                                                      # inmutable y con hash: usable como clave
