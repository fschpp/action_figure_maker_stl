"""Manifiesto de paquete `afm-paquete-1` (D02 del plan de catálogos en línea)."""
import copy
import json
import subprocess
import sys

import pytest

from afm import catalog, parts
from afm import paquete as Q

SHA = "a" * 64
SHA2 = "b" * 64


def heroe() -> dict:
    """El ejemplo del plan (§5.2), completo."""
    return {
        "formato": "afm-paquete-1",
        "id": "proceres-127-guemes",
        "nombre": "Próceres 127 — Güemes",
        "version": "1.2.0",
        "descripcion": "Héroe completo de Güemes para la línea proceres-127.",
        "tipo": "heroe",
        "autores": [{"nombre": "Ana Pérez", "github": "owner"}],
        "licencia": "CC-BY-4.0",
        "origen": "propia",
        "github": "owner/repo",
        "linea": {"nombre": "proceres-127", "altura_mm": 127.0},
        "motor": ">=0.2.1",
        "contenido": {"piezas": ["cabeza-guemes-01@1"], "recetas": ["guemes"], "paleta": True},
        "requiere": ["proceres-127-base@^1.0.0#owner/repo"],
        "conflicts": [],
        "experimental": False,
        "categorias": ["heroes"],
        "etiquetas": ["prócer", "histórico"],
        "archivos": {"piezas/cabeza/cabeza-guemes-01@1.stl": SHA},
    }


def coleccion() -> dict:
    d = heroe()
    d.update(id="proceres-127-todo", tipo="coleccion", categorias=["colecciones"], archivos={}, requiere=[])
    d.pop("linea")
    d["contenido"] = {"pines": [
        {"id": "proceres-127-base", "github": "owner/repo", "version": "1.0.0", "sha256": SHA},
        {"id": "proceres-127-guemes", "github": "owner/repo", "version": "1.2.0", "sha256": SHA2}]}
    return d


def referencia() -> dict:
    d = heroe()
    d["origen"] = "referencia"
    d["referencias_requeridas"] = [{"id": "cuerpo-original", "nombre": "Cuerpo original", "sha256": [SHA], "tamano_max": 40_000_000}]
    return d


def codes(d: dict) -> list[str]:
    return [i.code for i in Q.Paquete.from_dict(d).issues()]


# ---------------------------------------------------------------------------------------------------- ida y vuelta


@pytest.mark.parametrize("make", [heroe, coleccion, referencia])
def test_ejemplos_validos_ida_y_vuelta(make):
    p = Q.Paquete.from_dict(make())
    assert p.issues() == []
    assert Q.Paquete.from_dict(p.to_dict()) == p
    assert Q.Paquete.from_json(p.to_json()) == p
    assert p.to_json() == Q.Paquete.from_json(p.to_json()).to_json()


def test_ejemplo_del_plan_sale_identico():
    assert Q.Paquete.from_dict(heroe()).to_dict() == heroe()


def test_to_json_es_estable_y_conserva_acentos():
    text = Q.Paquete.from_dict(heroe()).to_json()
    assert text.endswith("}\n") and "Güemes" in text and "\\u" not in text
    assert list(json.loads(text))[:3] == ["formato", "id", "nombre"]


def test_save_y_load(tmp_path):
    p = Q.Paquete.from_dict(heroe())
    f = tmp_path / Q.MANIFEST_FILE
    p.save(str(f))
    assert Q.Paquete.load(str(f)) == p
    assert f.read_bytes().decode("utf-8") == p.to_json()


def test_load_no_existe_y_no_es_json(tmp_path):
    with pytest.raises(Q.PaqueteError, match="no se pudo leer"):
        Q.Paquete.load(str(tmp_path / "nada.json"))
    (tmp_path / "x.json").write_text("{no es json", encoding="utf-8")
    with pytest.raises(Q.PaqueteError, match="no es JSON"):
        Q.Paquete.load(str(tmp_path / "x.json"))


def test_opcionales_ausentes_toman_su_valor_por_defecto():
    d = heroe()
    for k in ("github", "motor", "requiere", "conflicts", "experimental", "etiquetas", "archivos", "contenido"):
        d.pop(k)
    p = Q.Paquete.from_dict(d)
    assert (p.github, p.motor, p.requiere, p.experimental, p.archivos) == ("", "", [], False, {})
    assert "github" not in p.to_dict() and "motor" not in p.to_dict() and "referencias_requeridas" not in p.to_dict()


# ---------------------------------------------------------------------------------------------------- lectura


@pytest.mark.parametrize("key", ["id", "nombre", "version", "descripcion", "tipo", "autores", "licencia", "origen"])
def test_clave_obligatoria_ausente(key):
    d = heroe()
    d.pop(key)
    with pytest.raises(Q.PaqueteError, match=f"falta «{key}»"):
        Q.Paquete.from_dict(d)


@pytest.mark.parametrize("key,bad", [("id", 3), ("version", 1.2), ("autores", "yo"), ("experimental", "si"), ("etiquetas", "a"),
                                     ("contenido", []), ("linea", "x"), ("archivos", []), ("requiere", [1])])
def test_tipo_equivocado(key, bad):
    d = heroe()
    d[key] = bad
    with pytest.raises(Q.PaqueteError):
        Q.Paquete.from_dict(d)


def test_formato_futuro_y_ajeno():
    d = heroe()
    d["formato"] = "afm-paquete-2"
    with pytest.raises(Q.PaqueteFormatoFuturo, match="actualizá afm"):
        Q.Paquete.from_dict(d)
    assert issubclass(Q.PaqueteFormatoFuturo, Q.PaqueteError)
    for fmt in ("afm-pieza-1", "afm-paquete-0", "paquete", None, 1):
        d["formato"] = fmt
        with pytest.raises(Q.PaqueteError, match="no es un manifiesto de paquete") as e:
            Q.Paquete.from_dict(d)
        assert not isinstance(e.value, Q.PaqueteFormatoFuturo)
    with pytest.raises(Q.PaqueteError, match="objeto JSON"):
        Q.Paquete.from_dict([])


def test_claves_desconocidas_se_toleran_con_aviso():
    d = heroe()
    d["firma"] = "x"
    d["contenido"]["extra"] = 1
    p = Q.Paquete.from_dict(d)
    assert p.issues() == []
    assert any("«firma»" in a for a in p.avisos)
    assert p == Q.Paquete.from_dict(heroe())          # los avisos no entran en la igualdad
    assert "firma" not in p.to_dict()


# ---------------------------------------------------------------------------------------------------- semver y rangos


def test_semver_valido_e_invalido():
    for ok in ("0.0.0", "1.2.3", "10.20.30", "1.0.0-rc.1", "1.0.0-alpha", "1.0.0+build.5", "1.0.0-x.7.z.92+meta"):
        assert Q.is_semver(ok) and str(Q.parse_semver(ok)).startswith(ok.split("+")[0])
    for bad in ("1.2", "v1.2.3", "01.2.3", "1.2.3.4", "1.2.-3", "", "1.2.3-", "1.0.0-rc..1", None, 3):
        assert not Q.is_semver(bad)
    with pytest.raises(Q.PaqueteError, match="semver"):
        Q.parse_semver("1.2")


def test_semver_orden():
    orden = ["0.9.9", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11",
             "1.0.0-rc.1", "1.0.0", "1.0.1", "1.1.0", "2.0.0"]
    vs = [Q.parse_semver(x) for x in orden]
    assert vs == sorted(vs) and len(set(vs)) == len(vs)
    assert Q.parse_semver("1.0.0+a") == Q.parse_semver("1.0.0+b")      # el build no cuenta
    assert Q.parse_semver("1.10.0") > Q.parse_semver("1.9.0")          # numérico, no de texto


@pytest.mark.parametrize("rng,yes,no", [
    ("*", ["0.0.1", "9.9.9"], []),
    (">=0.2.1", ["0.2.1", "0.2.2", "1.0.0"], ["0.2.0", "0.1.9"]),
    (">0.2.1", ["0.2.2"], ["0.2.1"]),
    ("<=1.0.0", ["1.0.0", "0.5.0"], ["1.0.1"]),
    ("<1.0.0", ["0.9.9"], ["1.0.0"]),
    ("1.2.3", ["1.2.3"], ["1.2.4", "1.2.2"]),
    ("=1.2.3", ["1.2.3"], ["1.2.4"]),
    ("^1.2.3", ["1.2.3", "1.9.0"], ["1.2.2", "2.0.0"]),
    ("^0.2.3", ["0.2.3", "0.2.9"], ["0.3.0", "0.2.2"]),
    ("^0.0.3", ["0.0.3"], ["0.0.4", "0.0.2"]),
    ("^1", ["1.0.0", "1.99.0"], ["2.0.0", "0.9.0"]),
    ("^0.2", ["0.2.0", "0.2.8"], ["0.3.0"]),
    ("~1.2.3", ["1.2.3", "1.2.9"], ["1.3.0", "1.2.2"]),
    ("~1.2", ["1.2.0", "1.2.7"], ["1.3.0"]),
    ("~1", ["1.0.0", "1.8.0"], ["2.0.0"]),
    ("1.2", ["1.2.0", "1.2.5"], ["1.3.0", "1.1.9"]),
    ("1", ["1.4.0"], ["2.0.0"]),
    (">=1.0.0,<2.0.0", ["1.0.0", "1.9.9"], ["0.9.9", "2.0.0"]),
    (">=1.0.0 <2.0.0", ["1.5.0"], ["2.0.0"]),
    (">= 1.0.0, < 2.0.0", ["1.5.0"], ["2.1.0"]),
    ("^1.0.0-rc.1", ["1.0.0-rc.2", "1.0.0"], ["1.0.0-beta"]),
])
def test_rangos(rng, yes, no):
    r = Q.parse_range(rng)
    assert all(r.satisfies(v) for v in yes), rng
    assert not any(r.satisfies(v) for v in no), rng
    assert str(r) == rng.strip()


@pytest.mark.parametrize("bad", ["", "  ", ">=", "^", "abc", ">=x.y", "1.2.3.4", "v1.0.0", "01.2.3", None, 5, ">=1.0.0,,<<2"])
def test_rangos_invalidos(bad):
    assert not Q.is_range(bad)
    with pytest.raises(Q.PaqueteError):
        Q.parse_range(bad)


def test_rango_acepta_objeto_semver():
    assert Q.parse_range("^1.0.0").satisfies(Q.parse_semver("1.4.0"))


def test_motor_del_motor_actual_cumple_el_rango_del_ejemplo():
    from afm import __version__
    assert Q.parse_range(heroe()["motor"]).satisfies(__version__)


# ---------------------------------------------------------------------------------------------------- dependencias


def test_dependencias():
    d = Q.parse_dependency("proceres-127-base@^1.0.0#owner/repo")
    assert (d.id, d.range, d.github) == ("proceres-127-base", "^1.0.0", "owner/repo")
    assert str(d) == "proceres-127-base@^1.0.0#owner/repo"
    assert d.satisfied_by("1.4.0") and not d.satisfied_by("2.0.0")
    d = Q.parse_dependency("base")
    assert (d.range, d.github) == ("*", "") and str(d) == "base" and d.satisfied_by("0.0.1")
    d = Q.parse_dependency("base#o/r")
    assert (d.range, d.github) == ("*", "o/r")
    d = Q.parse_dependency("base@>=1.0.0 <2.0.0")
    assert d.satisfied_by("1.5.0") and not d.satisfied_by("2.0.0")


@pytest.mark.parametrize("bad", ["", "Base", "base@", "base@x", "base@^1#", "base@^1#no-es-repo", "base@^1#a/b/c", "a b", "@^1"])
def test_dependencias_invalidas(bad):
    with pytest.raises(Q.PaqueteError):
        Q.parse_dependency(bad)


# ---------------------------------------------------------------------------------------------------- lint por código


def mutate(make, **kw) -> dict:
    d = make()
    for k, v in kw.items():
        if v is Ellipsis:
            d.pop(k, None)
        else:
            d[k] = v
    return d


@pytest.mark.parametrize("kw,code", [
    ({"licencia": ""}, "CK101"),
    ({"licencia": "Propietaria"}, "CK101"),
    ({"licencia": "cc-by-4.0"}, "CK101"),
    ({"descripcion": "  "}, "CK102"),
    ({"descripcion": "x" * 281}, "CK102"),
    ({"origen": ""}, "CK102"),
    ({"origen": "robada"}, "CK102"),
    ({"version": "1.2"}, "CK103"),
    ({"version": "v1.2.0"}, "CK103"),
    ({"linea": ...}, "CK104"),
    ({"linea": {"nombre": "", "altura_mm": 127.0}}, "CK104"),
    ({"linea": {"nombre": "x", "altura_mm": 0}}, "CK104"),
    ({"categorias": []}, "CK106"),
    ({"categorias": ["heroes", "cuerpos", "cabezas", "manos", "ropa"]}, "CK106"),
    ({"categorias": ["inventada"]}, "CK106"),
    ({"categorias": ["heroes", "heroes"]}, "CK106"),
    ({"origen": "referencia"}, "CK107"),
    ({"archivos": {"piezas/x.stl": "abc"}}, "CK201"),
    ({"archivos": {"../x.stl": SHA}}, "CK302"),
    ({"archivos": {"/etc/x.stl": SHA}}, "CK302"),
    ({"archivos": {"a\\b.stl": SHA}}, "CK302"),
    ({"archivos": {"a/./b.stl": SHA}}, "CK302"),
    ({"archivos": {"C:/x.stl": SHA}}, "CK302"),
    ({"archivos": {"a\x01b.stl": SHA}}, "CK302"),
])
def test_codigos_ck(kw, code):
    assert code in codes(mutate(heroe, **kw))


def test_referencias_sin_origen_referencia_es_ck107():
    d = heroe()
    d["referencias_requeridas"] = referencia()["referencias_requeridas"]
    assert "CK107" in codes(d)


def test_colecciones_ck402_y_ck403():
    assert codes(coleccion()) == []
    d = coleccion()
    d["contenido"]["pines"][0]["sha256"] = "zz"
    d["contenido"]["pines"][1]["version"] = "^1.0.0"
    d["contenido"]["pines"][0]["github"] = "sin-barra"
    got = codes(d)
    assert got.count("CK402") == 3
    d = coleccion()
    d["contenido"]["pines"].append(dict(d["contenido"]["pines"][0]))
    assert "CK402" in codes(d)                                   # el mismo id fijado dos veces
    d = coleccion()
    d["contenido"]["piezas"] = ["x-01@1"]
    assert "CK403" in codes(d)
    d = coleccion()
    d["archivos"] = {"piezas/x.stl": SHA}
    assert "CK403" in codes(d)
    d = coleccion()
    d["contenido"]["pines"] = []
    assert any(i.field == "contenido.pines" for i in Q.Paquete.from_dict(d).issues())


def test_pines_fuera_de_una_coleccion():
    d = heroe()
    d["contenido"]["pines"] = coleccion()["contenido"]["pines"]
    assert any(i.field == "contenido.pines" for i in Q.Paquete.from_dict(d).issues())


def test_coleccion_no_necesita_linea_pero_si_la_trae_se_valida():
    d = coleccion()
    d["linea"] = {"nombre": "x", "altura_mm": -1}
    assert "CK104" in codes(d)


def test_otros_problemas_del_manifiesto():
    cases = {
        "id": mutate(heroe, id="Mal Id"),
        "nombre": mutate(heroe, nombre=" "),
        "tipo": mutate(heroe, tipo="otro"),
        "autores": mutate(heroe, autores=[]),
        "autores[0].github": mutate(heroe, autores=[{"nombre": "x", "github": "no valido!"}]),
        "github": mutate(heroe, github="sin-barra"),
        "motor": mutate(heroe, motor="mayor que dos"),
        "requiere[0]": mutate(heroe, requiere=["Mal Id"]),
        "conflicts[0]": mutate(heroe, conflicts=["x@zzz"]),
        "etiquetas": mutate(heroe, etiquetas=["x" * 33]),
        "contenido.piezas[0]": mutate(heroe, contenido={"piezas": ["cabeza"], "recetas": [], "paleta": False}),
        "contenido.recetas[0]": mutate(heroe, contenido={"piezas": ["a-01@1"], "recetas": ["Mal"], "paleta": False}),
        "contenido": mutate(heroe, contenido={"piezas": [], "recetas": [], "paleta": False}),
    }
    for fld, d in cases.items():
        assert any(i.field == fld for i in Q.Paquete.from_dict(d).issues()), fld


def test_dependencias_cruzadas():
    d = mutate(heroe, requiere=["proceres-127-guemes@^1"])
    assert any("de sí mismo" in i.message for i in Q.Paquete.from_dict(d).issues())
    d = mutate(heroe, requiere=["base@^1", "base@^2"])
    assert any("más de una vez" in i.message for i in Q.Paquete.from_dict(d).issues())
    d = mutate(heroe, requiere=["base@^1"], conflicts=["base"])
    assert any("«requiere» y en «conflicts»" in i.message for i in Q.Paquete.from_dict(d).issues())
    assert codes(mutate(heroe, requiere=["a@^1", "b@^1"], conflicts=["c"])) == []


def test_linea_base_puede_ir_sin_piezas():
    d = mutate(heroe, tipo="linea", categorias=["lineas"], contenido={"piezas": [], "recetas": [], "paleta": False}, archivos={})
    assert Q.Paquete.from_dict(d).issues() == []


def test_piezas_repetidas_y_referencias_requeridas_mal_formadas():
    d = mutate(heroe, contenido={"piezas": ["a-01@1", "a-01@1"], "recetas": ["g", "g"], "paleta": False})
    msgs = [i.message for i in Q.Paquete.from_dict(d).issues()]
    assert "hay piezas repetidas" in msgs and "hay recetas repetidas" in msgs
    d = referencia()
    d["referencias_requeridas"] = [{"id": "Mal", "nombre": "", "sha256": ["x"], "tamano_max": 0},
                                   {"id": "ok", "nombre": "a", "sha256": [SHA], "tamano_max": 1},
                                   {"id": "ok", "nombre": "a", "sha256": [SHA], "tamano_max": 1}]
    flds = [i.field for i in Q.Paquete.from_dict(d).issues()]
    for f in ("referencias_requeridas[0].id", "referencias_requeridas[0].nombre", "referencias_requeridas[0].sha256",
              "referencias_requeridas[0].tamano_max", "referencias_requeridas[2].id"):
        assert f in flds, f
    for bad in ({"id": "x"}, {"id": "x", "nombre": "n", "sha256": "a" * 64, "tamano_max": 1},
                {"id": "x", "nombre": "n", "sha256": [], "tamano_max": True}):
        d["referencias_requeridas"] = [bad]
        with pytest.raises(Q.PaqueteError):
            Q.Paquete.from_dict(d)


def test_issue_y_problems_se_leen_bien():
    p = Q.Paquete.from_dict(mutate(heroe, licencia="x"))
    [i] = p.issues()
    assert (i.code, i.field) == ("CK101", "licencia")
    assert p.problems() == [str(i)] and str(i).startswith("CK101 licencia: ")
    assert str(Q.Issue("", "id", "malo")) == "id: malo"


# ---------------------------------------------------------------------------------------------------- esquema JSON


@pytest.fixture(scope="module")
def validator():
    js = pytest.importorskip("jsonschema")
    s = Q.schema()
    js.Draft202012Validator.check_schema(s)
    return js.Draft202012Validator(s)


@pytest.mark.parametrize("make", [heroe, coleccion, referencia])
def test_el_esquema_valida_los_ejemplos(validator, make):
    assert list(validator.iter_errors(make())) == []


def test_el_esquema_permite_claves_nuevas(validator):
    d = heroe()
    d["firma"] = {"cualquier": "cosa"}
    d["contenido"]["extra"] = [1]
    assert list(validator.iter_errors(d)) == []


def test_el_esquema_rechaza_formato_de_otra_version(validator):
    d = heroe()
    d["formato"] = "afm-paquete-2"
    assert list(validator.iter_errors(d))


@pytest.mark.parametrize("kw", [
    {"licencia": "Propietaria"}, {"version": "1.2"}, {"version": "v1.0.0"}, {"descripcion": ""}, {"descripcion": "x" * 281},
    {"origen": "robada"}, {"tipo": "otro"}, {"categorias": []}, {"categorias": ["inventada"]},
    {"categorias": ["heroes", "heroes"]}, {"categorias": ["heroes", "cuerpos", "cabezas", "manos", "ropa"]},
    {"linea": ...}, {"linea": {"nombre": "x", "altura_mm": 0}}, {"autores": []}, {"id": "Mal Id"}, {"github": "sin-barra"},
    {"archivos": {"x.stl": "abc"}}, {"etiquetas": ["x" * 33]}, {"origen": "referencia"},
    {"contenido": {"piezas": ["cabeza"]}}, {"experimental": "si"},
])
def test_esquema_y_codigo_coinciden_al_rechazar(validator, kw):
    d = mutate(heroe, **kw)
    assert list(validator.iter_errors(d)), kw
    try:
        p = Q.Paquete.from_dict(d)
    except Q.PaqueteError:
        return
    assert p.issues(), kw


def test_esquema_exige_pines_en_la_coleccion(validator):
    d = coleccion()
    d["contenido"]["pines"] = []
    assert list(validator.iter_errors(d))
    d = coleccion()
    d["contenido"]["pines"][0]["sha256"] = "zz"
    assert list(validator.iter_errors(d))


def test_el_esquema_y_las_constantes_dicen_lo_mismo():
    props = Q.schema()["properties"]
    assert tuple(props["tipo"]["enum"]) == Q.TYPES
    assert tuple(props["origen"]["enum"]) == Q.ORIGINS
    assert tuple(props["licencia"]["enum"]) == Q.LICENSES
    assert tuple(props["categorias"]["items"]["enum"]) == Q.CATEGORIES
    assert (props["categorias"]["minItems"], props["categorias"]["maxItems"]) == (Q.CATEGORIES_MIN, Q.CATEGORIES_MAX)
    assert props["descripcion"]["maxLength"] == Q.DESCRIPTION_MAX and props["nombre"]["maxLength"] == Q.NAME_MAX
    assert props["id"]["pattern"] == Q._ID_RE.pattern and props["version"]["pattern"] == Q._SEMVER_RE.pattern
    assert props["formato"]["const"] == Q.FORMAT


# ---------------------------------------------------------------------------------------------------- coherencia con el resto


def test_regex_de_id_igual_que_la_pieza_y_el_catalogo():
    assert Q._ID_RE.pattern == parts.ID_RE.pattern
    for ok in ("a", "pierna-botas-01", "x1-y2"):
        assert Q._ID_RE.match(ok) and catalog.parse_ref(ok + "@3")[0] == ok
        assert Q._REF_RE.match(ok + "@3")
    for bad in ("A", "-a", "a-", "a--b", "a_b", ""):
        assert not Q._ID_RE.match(bad)
    assert not Q._REF_RE.match("a@0") and not Q._REF_RE.match("a") and not Q._REF_RE.match("a@01")


def test_tipos_de_paquete_y_formato_no_chocan_con_otros_formatos():
    from afm import palette, recipe
    assert len({Q.FORMAT, parts.PIECE_FORMAT, catalog.LINE_FORMAT, palette.FORMAT, recipe.RECIPE_FORMAT}) == 5


def test_el_modulo_no_carga_el_motor_pesado():
    code = ("import sys, afm.paquete as q; q.Paquete.from_json(q.Paquete.from_json('{\"formato\":\"afm-paquete-1\",\"id\":\"a\",\"nombre\":\"a\","
            "\"version\":\"1.0.0\",\"descripcion\":\"a\",\"tipo\":\"linea\",\"autores\":[]," "\"licencia\":\"MIT\",\"origen\":\"propia\"}').to_json());"
            "bad = [m for m in ('manifold3d', 'trimesh', 'scipy', 'PIL', 'afm.parts', 'afm.catalog', 'afm.solids') if m in sys.modules];"
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_el_esquema_viaja_con_el_paquete():
    from importlib import resources
    assert resources.files("afm").joinpath("schemas").joinpath(Q.SCHEMA_FILE).is_file()
