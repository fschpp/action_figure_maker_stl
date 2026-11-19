"""T38 · Modelo de paleta (`afm.palette`): roles, validación, LUT, overrides, hash y rol más cercano por ancla."""
import json
import subprocess
import sys

import numpy as np
import pytest

from afm.palette import DEFAULT_FLAT, MAX_ANCHORS, MAX_ROLES, Palette, PaletteError, Role

SAMPLE = {
    "formato": "afm-paleta-1",
    "roles": [
        {"id": "piel", "nombre": "Piel", "hex": "#D8A47F", "anclas": ["#A98265", "#89684C"]},
        {"id": "uniforme", "nombre": "Uniforme", "hex": "#F2F0E6", "anclas": ["#BFBAAD", "#9A9587", "#6E6A5D"]},
        {"id": "botas", "nombre": "Botas", "hex": "#201F1C", "anclas": ["#201F1C"]},
    ],
}


def sample() -> Palette:
    return Palette.from_dict(json.loads(json.dumps(SAMPLE)))


def test_roundtrip_dict():
    p = sample()
    assert p.to_dict() == SAMPLE
    assert Palette.from_dict(json.loads(json.dumps(p.to_dict()))).to_dict() == SAMPLE
    assert p.ids() == ["piel", "uniforme", "botas"] and p.get("piel").nombre == "Piel" and p.hex_of("botas") == "#201F1C"
    with pytest.raises(PaletteError, match="no tiene el rol «oro»"):
        p.get("oro")


def test_hex_normalized_to_upper():
    d = {"formato": "afm-paleta-1", "roles": [{"id": "oro", "hex": "#c9a227", "anclas": ["#ffd700"]}]}
    p = Palette.from_dict(d)
    assert p.hex_of("oro") == "#C9A227" and p.get("oro").anclas == ["#FFD700"]
    assert p.get("oro").nombre == "oro"                  # sin nombre: el id


@pytest.mark.parametrize("mutate, texto", [
    (lambda d: d["roles"][0].update(hex="D8A47F"), "#RRGGBB"),                       # sin «#»
    (lambda d: d["roles"][0].update(hex="#D8A47"), "#RRGGBB"),                       # corto
    (lambda d: d["roles"][0].update(hex="#GGGGGG"), "#RRGGBB"),                      # no hexadecimal
    (lambda d: d["roles"][0].update(hex=0xD8A47F), "#RRGGBB"),                       # no es texto
    (lambda d: d["roles"][1].update(id="piel"), "repetido"),
    (lambda d: d["roles"][0].update(id="Piel"), "id de rol inválido"),               # mayúscula
    (lambda d: d["roles"][0].update(id="1piel"), "id de rol inválido"),              # empieza con dígito
    (lambda d: d["roles"][0].update(id="a" * 33), "id de rol inválido"),             # 33 caracteres
    (lambda d: d["roles"][0].update(id=""), "id de rol inválido"),
    (lambda d: d["roles"][0].update(anclas=["#000000"] * (MAX_ANCHORS + 1)), "anclas"),
    (lambda d: d["roles"][0].update(anclas=["rojo"]), "ancla"),
    (lambda d: d["roles"][0].update(nombre="  "), "nombre"),
    (lambda d: d.update(formato="afm-paleta-2"), "formato"),
    (lambda d: d.update(roles={}), "roles"),
    (lambda d: d["roles"].append("x"), "objeto"),
    (lambda d: d["roles"].append({"id": "sin_hex"}), "«hex»"),
])
def test_rejects_bad_hex_duplicate_id_bad_id_too_many_roles(mutate, texto):
    d = json.loads(json.dumps(SAMPLE))
    mutate(d)
    with pytest.raises(PaletteError, match=texto):
        Palette.from_dict(d)


def test_too_many_roles_and_boundary():
    mk = lambda n: {"formato": "afm-paleta-1", "roles": [{"id": f"r{i}", "hex": "#000000"} for i in range(n)]}
    assert len(Palette.from_dict(mk(MAX_ROLES)).roles) == MAX_ROLES            # 64 sí
    with pytest.raises(PaletteError, match="máximo 64"):
        Palette.from_dict(mk(MAX_ROLES + 1))
    with pytest.raises(PaletteError, match="objeto"):
        Palette.from_dict([])


def test_palette_error_is_a_value_error():
    assert issubclass(PaletteError, ValueError)


def test_lut_shape_order_and_flat_255():
    p = sample()
    t = p.lut(["botas", "piel"])
    assert t.shape == (256, 3) and t.dtype == np.uint8
    assert tuple(t[0]) == (0x20, 0x1F, 0x1C) and tuple(t[1]) == (0xD8, 0xA4, 0x7F)          # el orden pedido, no el de la paleta
    assert (t[2:] == (0xC8, 0xC8, 0xC8)).all() and tuple(t[255]) == (0xC8, 0xC8, 0xC8)       # lo demás y el 255: color plano
    assert (p.lut(["piel"], flat="#102030")[255] == (0x10, 0x20, 0x30)).all()
    assert DEFAULT_FLAT == "#C8C8C8"
    with pytest.raises(PaletteError, match="faltan roles en la paleta: oro, plata"):
        p.lut(["piel", "oro", "plata", "oro"])
    with pytest.raises(PaletteError):
        p.lut(["piel"], flat="gris")


def test_default_flat_matches_colorbake():
    from afm import colorbake
    assert DEFAULT_FLAT == colorbake.DEFAULT_FLAT


def test_with_overrides_leaves_original_untouched():
    p = sample()
    before = json.dumps(p.to_dict(), sort_keys=True)
    q = p.with_overrides({"uniforme": "#1a2b3c"})
    assert q.hex_of("uniforme") == "#1A2B3C" and q.hex_of("piel") == p.hex_of("piel")
    assert json.dumps(p.to_dict(), sort_keys=True) == before                                # el original, intacto
    assert q.get("uniforme").anclas == p.get("uniforme").anclas and q.get("uniforme").anclas is not p.get("uniforme").anclas
    assert p.with_overrides({}).to_dict() == p.to_dict()
    with pytest.raises(PaletteError, match="no existen: oro"):
        p.with_overrides({"oro": "#FFD700"})
    with pytest.raises(PaletteError, match="#RRGGBB"):
        p.with_overrides({"piel": "rojo"})


def test_digest_changes_with_hex_but_not_with_dict_order():
    p = sample()
    d = p.digest()
    assert len(d) == 64 and d == sample().digest()
    # mismo contenido con las claves de cada diccionario en otro orden, hex en minúscula y la lista de roles al revés
    d2 = {"roles": [dict(reversed(list(r.items()))) for r in reversed(SAMPLE["roles"])], "formato": "afm-paleta-1"}
    for r in d2["roles"]:
        r["hex"] = r["hex"].lower()
    assert Palette.from_dict(d2).digest() == d
    assert p.with_overrides({"piel": "#D8A480"}).digest() != d                              # un hex distinto
    assert Palette([]).digest() != d
    q = sample()                                                                            # nombre y anclas no son color
    q.get("piel").nombre = "Piel clara"
    q.get("piel").anclas.append("#000001")
    assert q.digest() == d


def test_nearest_picks_closest_anchor_and_respects_limit():
    p = sample()
    rid, de = p.nearest((0xBF, 0xBA, 0xAD))                                                 # exactamente una ancla del uniforme
    assert rid == "uniforme" and de == pytest.approx(0.0, abs=1e-6)
    assert p.nearest("#8A6A4E")[0] == "piel"                                                 # cerca de una ancla de la piel (acepta «#hex»)
    assert p.nearest((0x22, 0x20, 0x1D))[0] == "botas"
    rid, de = p.nearest((0x30, 0x30, 0x30))
    assert rid == "botas" and 0 < de < 20
    assert p.nearest((0, 255, 0), max_delta_e=15.0) is None                                  # verde puro: ninguna ancla llega
    assert p.nearest((0, 255, 0))[0] in p.ids()                                              # sin límite siempre hay respuesta
    assert Palette([Role("x", "X", "#FF0000")]).nearest((255, 0, 0)) is None                 # sin anclas no hay sugerencia
    assert Palette([]).nearest((1, 2, 3)) is None
    with pytest.raises(PaletteError):
        p.nearest((1, 2))


def test_module_imports_without_pillow_or_skimage():
    code = ("import sys\nimport afm.palette as pal\n"
            "bad = [m for m in ('PIL', 'skimage', 'trimesh', 'scipy') if m in sys.modules]\n"
            "assert not bad, bad\n"
            "pal.Palette([pal.Role('a', 'A', '#000000')]).lut(['a'])\n"
            "assert 'skimage' not in sys.modules and 'PIL' not in sys.modules\n")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
