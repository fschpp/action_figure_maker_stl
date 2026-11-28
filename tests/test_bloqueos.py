"""D24 · bloqueo y retiro (`bloqueos.py`, `bloqueos_cli.py`; PLAN_catalogos_online.md §5.8, IX106).

Cuatro bloques: leer `bloqueados.json` con rigor, el CI del índice (rechazar altas y retirar lo listado), la comparación local con las piezas
instaladas y la ida y vuelta hasta «Explorar» (un paquete bloqueado desaparece y avisa si ya estaba instalado)."""
import json

import pytest

from afm import bloqueos as B
from afm import dependencias as D
from afm import indice as X
from afm import instalar as I
from afm import remoto as R
from afm.cli import main
from test_catalog import arm_piece
from test_dependencias import BASE, ent, idx, set_registry
from test_exportar import OWNER, add_arm, cat  # noqa: F401  (cat es una fixture)
from test_remoto import index_body

SHA_A, SHA_B = "ab" * 32, "cd" * 32


def write(tmp_path, obj, name="bloqueados.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


LINEA = {"motivo": "reclamo del autor", "fecha": "2026-11-20"}


# ---------------------------------------------------------------------------------------------------------- leer y validar

def test_un_archivo_que_no_existe_es_una_lista_vacia(tmp_path):
    assert B.leer_bloqueados(str(tmp_path / "no-esta.json")) == []


def test_lee_los_tres_criterios_con_motivo_y_fecha(tmp_path):
    p = write(tmp_path, [{"id": "heroe-x", **LINEA}, {"autor": "Malo", **LINEA}, {"sha256": SHA_A, **LINEA}])
    bl = B.leer_bloqueados(p)
    assert [(b.id, b.autor, b.sha256[:2]) for b in bl] == [("heroe-x", "", ""), ("", "Malo", ""), ("", "", "ab")]
    assert all(b.motivo == "reclamo del autor" and b.fecha == "2026-11-20" for b in bl)
    assert B.a_json(bl)[2] == {"sha256": SHA_A, **LINEA}                    # ida y vuelta al formato del índice


@pytest.mark.parametrize("linea,frag", [
    ({"id": "a", "autor": "b", **LINEA}, "exactamente uno"),
    ({**LINEA}, "exactamente uno"),
    ({"sha256": "ABC", **LINEA}, "64 caracteres"),
    ({"sha256": SHA_A.upper(), **LINEA}, "64 caracteres"),
    ({"id": "Mal Id", **LINEA}, "patrón"),
    ({"id": "ok", "fecha": "2026-11-20"}, "motivo"),
    ({"id": "ok", "motivo": "  ", "fecha": "2026-11-20"}, "motivo"),
    ({"id": "ok", "motivo": "x"}, "fecha"),
    ({"id": "ok", "motivo": "x", "fecha": "2026-13-45"}, "fecha"),
    ({"id": "ok", "motivo": "x", "fecha": "20/11/2026"}, "fecha"),
    ({"id": "ok", **LINEA, "otra": 1}, "claves desconocidas"),
    ("no es objeto", "no es un objeto"),
])
def test_el_ci_no_salta_lineas_raras_las_informa(tmp_path, linea, frag):
    with pytest.raises(B.BloqueosError, match=frag):
        B.leer_bloqueados(write(tmp_path, [linea]))


def test_repetidos_y_archivo_roto(tmp_path):
    with pytest.raises(B.BloqueosError, match="repite"):
        B.leer_bloqueados(write(tmp_path, [{"autor": "Malo", **LINEA}, {"autor": "malo", **LINEA}]))
    (tmp_path / "roto.json").write_text("{no", encoding="utf-8")
    with pytest.raises(B.BloqueosError, match="no se pudo leer"):
        B.leer_bloqueados(str(tmp_path / "roto.json"))
    with pytest.raises(B.BloqueosError, match="lista"):
        B.leer_bloqueados(write(tmp_path, {"otra": []}))
    assert len(B.leer_bloqueados(write(tmp_path, {"bloqueados": [{"id": "x", **LINEA}]}, "obj.json"))) == 1


# ---------------------------------------------------------------------------------------------------------- el CI del índice

def entrada(pid, version="1.0.0", autor="owner", versiones=()):
    e = ent(pid, version, autor=autor, versions=[(v, False) for v in versiones])
    return json.loads(json.dumps(e))


def bl(**kw):
    return R.Block(motivo=LINEA["motivo"], fecha=LINEA["fecha"], **kw)


def test_ix106_rechaza_una_alta_por_id_autor_o_sha256():
    e = entrada("heroe-x")
    sha_ultima = e["latest"]["zip"]["sha256"]
    for b in (bl(id="heroe-x"), bl(autor="OWNER"), bl(sha256=sha_ultima)):
        h = B.comprobar_bloqueados([e], [b], modo="alta")
        assert [(x.codigo, x.severidad) for x in h] == [("IX106", "error")] and "se rechaza el alta" in h[0].mensaje
    assert B.comprobar_bloqueados([e], [bl(id="otro"), bl(autor="otra"), bl(sha256=SHA_A)]) == []


def test_un_bloqueo_por_sha256_alcanza_tambien_a_una_version_vieja():
    e = entrada("heroe-x", "1.1.0", versiones=["1.0.0"])
    vieja = e["versiones"][0]["zip"]["sha256"]
    assert B.comprobar_bloqueados([e], [bl(sha256=vieja)], modo="listado")[0].mensaje.endswith("hay que retirarlo del índice")


def test_retirar_saca_entradas_enteras_y_versiones_sueltas_sin_tocar_el_original():
    a, b, c = entrada("a-uno"), entrada("b-dos", autor="malo"), entrada("c-tres", "1.1.0", versiones=["1.0.0"])
    indice = X.build_index([a, b, c], generado="2026-11-27T00:00:00+00:00")
    original = json.dumps(indice, sort_keys=True)
    vieja = c["versiones"][0]["zip"]["sha256"]
    nuevo, quitados = B.retirar(indice, [bl(id="a-uno"), bl(autor="malo"), bl(sha256=vieja)])
    assert [p["id"] for p in nuevo["paquetes"]] == ["c-tres"] and nuevo["cantidad"] == 1
    assert nuevo["paquetes"][0]["versiones"] == [] and nuevo["paquetes"][0]["latest"]["version"] == "1.1.0"
    assert len(quitados) == 3 and {q.severidad for q in quitados} == {"error", "aviso"}
    assert [x["motivo"] for x in nuevo["bloqueados"]] == [LINEA["motivo"]] * 3         # el cliente sigue avisando de lo instalado
    assert json.dumps(indice, sort_keys=True) == original


def test_si_la_ultima_version_esta_bloqueada_sale_la_entrada_entera():
    e = entrada("heroe-x", "1.1.0", versiones=["1.0.0"])
    nuevo, quitados = B.retirar(X.build_index([e]), [bl(sha256=e["latest"]["zip"]["sha256"])])
    assert nuevo["paquetes"] == [] and nuevo["cantidad"] == 0 and "última versión" in quitados[0].mensaje


def test_sin_bloqueos_el_indice_queda_igual_y_sin_la_clave():
    indice = X.build_index([entrada("a-uno")])
    nuevo, quitados = B.retirar(indice, [])
    assert nuevo == indice and not quitados and "bloqueados" not in nuevo


# ---------------------------------------------------------------------------------------------------------- la app, en local

def test_piezas_bloqueadas_compara_el_sha256_de_cada_pieza_instalada(cat):
    pieza = add_arm(cat)
    mesh, meta = arm_piece("brazo-test-02", h=31.0)                          # otra geometría ⇒ otro sha256
    otra, _w = cat.add_piece(mesh, meta)
    hit = B.piezas_bloqueadas(cat.root, [bl(sha256=pieza.sha256), bl(sha256=SHA_A), bl(id="x")])
    assert [(p.ref, p.paquete) for p in hit] == [(pieza.ref, "")] and otra.ref not in [p.ref for p in hit]
    assert "sigue instalada" in hit[0].mensaje() and "bloqueado por sha256" in hit[0].mensaje()
    assert B.piezas_bloqueadas(cat.root, [bl(id="x"), bl(autor="y")]) == []                # sin sha256 no hay nada que comparar
    assert B.piezas_bloqueadas(cat.root + "-no-existe", [bl(sha256=SHA_A)]) == []         # un catálogo ilegible no frena la auditoría


def test_la_pieza_bloqueada_se_atribuye_al_paquete_que_la_trajo_y_audit_avisa_sin_tocar_nada(cat):
    pieza = add_arm(cat)
    set_registry(cat.root, heroe=("1.2.0", {"github": "owner/heroe", "refs": {"piezas": [pieza.ref]}}))
    i = idx(ent("heroe"), bloqueados=[{"sha256": pieza.sha256, "motivo": "reclamo", "fecha": "2026-11-20"}])
    antes = I.tree_digest(cat.root)
    alertas = D.audit([i], cat.root)
    assert I.tree_digest(cat.root) == antes                                                   # avisa, no borra nada
    assert [(a.kind, a.paquete, a.version) for a in alertas] == [("bloqueado", "heroe", "1.2.0")]
    assert pieza.ref in alertas[0].message and str(alertas[0]).startswith("BLOQUEADO heroe 1.2.0:")


def test_una_pieza_importada_a_mano_tambien_avisa_aunque_no_tenga_paquete(cat):
    pieza = add_arm(cat)
    i = idx(ent("otro"), bloqueados=[{"sha256": pieza.sha256, "motivo": "reclamo", "fecha": "2026-11-20"}])
    set_registry(cat.root)                                                                    # registro vacío: ningún paquete la trajo
    alertas = D.audit([i], cat.root)
    assert len(alertas) == 1 and alertas[0].paquete == "" and str(alertas[0]).startswith("BLOQUEADO (sin paquete):")


# ---------------------------------------------------------------------------------------------------------- hasta «Explorar»

def test_un_paquete_bloqueado_desaparece_de_explorar_y_avisa_si_ya_estaba_instalado(cat):
    set_registry(cat.root, malo=("1.0.0", {"github": "malo/malo", "autores": [{"nombre": "M", "github": "malo"}]}))
    cuerpo = index_body(ent("malo", autor="malo"), ent("bueno"), bloqueados=[{"autor": "malo", **LINEA}])
    i = R.parse_index(cuerpo, url=BASE + "/")
    assert [e.id for e in i.entries if e.visible] == ["bueno"]                               # lo bloqueado no se ofrece
    assert i.find("malo").bloqueado and "reclamo del autor" in i.find("malo").bloqueado
    alertas = D.audit([i], cat.root)
    assert [(a.kind, a.paquete) for a in alertas] == [("bloqueado", "malo")]


def test_el_indice_retirado_ya_no_trae_el_paquete_y_el_cliente_sigue_avisando(cat):
    set_registry(cat.root, malo=("1.0.0", {"github": "malo/malo", "autores": [{"nombre": "M", "github": "malo"}]}))
    indice = X.build_index([entrada("malo", autor="malo"), entrada("bueno")])
    nuevo, _ = B.retirar(indice, [bl(autor="malo")])
    i = R.parse_index(json.dumps(nuevo).encode("utf-8"), url=BASE + "/")
    assert [e.id for e in i.entries] == ["bueno"]
    assert [(a.kind, a.paquete) for a in D.audit([i], cat.root)] == [("bloqueado", "malo")]


# ---------------------------------------------------------------------------------------------------------- CLI

def test_cli_validar_comprobar_y_retirar(tmp_path, capsys):
    archivo = write(tmp_path, [{"autor": "malo", **LINEA}])
    assert main(["catalogo", "bloqueados", "validar", archivo]) == 0
    assert "válido (1 bloqueo(s))" in capsys.readouterr().out
    indice_p = tmp_path / "index.json"
    indice_p.write_text(X.dump_json(X.build_index([entrada("a-uno"), entrada("b-dos", autor="malo")])), encoding="utf-8")
    assert main(["catalogo", "bloqueados", "comprobar", archivo, "--indice", str(indice_p)]) == 1
    assert "IX106 error: malo@b-dos" in capsys.readouterr().out
    salida = tmp_path / "filtrado.json"
    assert main(["catalogo", "bloqueados", "retirar", archivo, "--indice", str(indice_p), "--salida", str(salida)]) == 0
    out = json.loads(salida.read_text(encoding="utf-8"))
    assert [p["id"] for p in out["paquetes"]] == ["a-uno"] and out["bloqueados"][0]["autor"] == "malo"
    assert main(["catalogo", "bloqueados", "comprobar", write(tmp_path, [], "vacio.json"), "--indice", str(indice_p)]) == 0


def test_cli_un_archivo_invalido_sale_2(tmp_path, capsys):
    assert main(["catalogo", "bloqueados", "validar", write(tmp_path, [{"id": "x"}])]) == 2
    assert "motivo" in capsys.readouterr().err
