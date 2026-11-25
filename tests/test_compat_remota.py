"""D15 · compatibilidad antes de descargar (PLAN_catalogos_online.md §5.5 «Fichas»): se mide con las fichas, sin bajar un STL."""
import dataclasses
import json
import os

import pytest

from afm import compat_remota as CR
from afm import guide as G
from afm import indice as X
from afm import remoto as R
from afm.catalog import Catalog
from afm.cli import main
from test_guide import cat as src_cat, clone  # noqa: F401  (src_cat: el muñeco de ejemplo más 4 torsos «de otra figura»)
from test_instalar import export, install
from test_remoto import FAST, POLICY, entry_dict, release, serve_index, sha, srv  # noqa: F401  (srv es una fixture)


def copy_to(dest, src, refs):
    for r in refs:
        meta, mesh = src.load(r)
        dest.add_piece(mesh, dataclasses.replace(meta, stl="", sha256=""))


@pytest.fixture
def dest(src_cat, tmp_path):
    """Catálogo vacío de la misma línea que solo trae los 4 torsos de otra figura (caras 1,0 / 0,9 / 0,5 / 0,3)."""
    d = Catalog.create(str(tmp_path / "destino"), src_cat.name, src_cat.height_mm, 0.4)
    copy_to(d, src_cat, [f"torso-otra-{i}" for i in "abcd"])
    return d


@pytest.fixture
def zip_pierna(src_cat, tmp_path):
    return export(src_cat, tmp_path, "pierna.zip", piezas=["pierna-muneco-01"], github="owner/repo")


def fichas_of(zip_path):
    return CR.parse_fichas(X.dump_json(X.fichas_from_zip(zip_path)))


def no_clash(monkeypatch):
    """`cobertura` suma el choque sentado (pide mallas) y la comparación remota no: se apaga acá para comparar el resto de la cuenta."""
    monkeypatch.setattr(G.SeatedClash, "limb_on_torso", lambda self, mine, other: None)
    monkeypatch.setattr(G.SeatedClash, "torso_with_limb", lambda self, *a, **k: None)


def test_mide_las_piezas_del_paquete_contra_el_catalogo_local(dest, zip_pierna):
    rep = CR.check(dest, fichas_of(zip_pierna))
    assert rep.linea_ok and len(rep.piezas) == 1
    p = rep.piezas[0]
    assert (p.ref, p.tipo, p.estado) == ("pierna-muneco-01@1", "pierna", "nueva")
    cad = p.puertos["cadera"]
    assert cad.universo == 4 and cad.ok + cad.aviso + cad.no == 4 and cad.no >= 1 and cad.ok >= 1      # caras de 1,0 a 0,3: hay de los tres niveles
    assert p.nivel == "ok" and p.encaja and cad.mejor == "ok"
    assert rep.encajan == 1 and "1 de 1 pieza encajan" in rep.resumen()


def test_coincide_con_cobertura_despues_de_instalar(dest, zip_pierna, monkeypatch):
    no_clash(monkeypatch)
    before = CR.check(dest, fichas_of(zip_pierna))
    install(zip_pierna, dest.root)
    after = G.piece_coverage(Catalog.open(dest.root), "pierna-muneco-01@1")
    for name, cov in after.items():
        mine = before.piezas[0].puertos[name]
        assert (mine.universo, mine.ok, mine.aviso, mine.no) == (cov.universo, cov.ok, cov.aviso, cov.no), name
        assert mine.resumen == cov.summary(), name
    assert set(after) == set(before.piezas[0].puertos)


def test_marca_que_el_choque_sentado_no_se_midio(dest, zip_pierna):
    p = CR.check(dest, fichas_of(zip_pierna)).piezas[0]
    assert p.puertos["cadera"].sin_choque and any("choque" in a for a in p.avisos)


def test_no_lee_ninguna_malla(dest, zip_pierna, monkeypatch):
    from afm import parts as P
    f = fichas_of(zip_pierna)
    monkeypatch.setattr(P, "load_mesh", lambda *a, **k: (_ for _ in ()).throw(AssertionError("leyó una malla")))
    monkeypatch.setattr(P, "read_piece", lambda *a, **k: (_ for _ in ()).throw(AssertionError("leyó una malla")))
    CR.check(dest, f)


def test_los_motivos_de_un_no_se_explican(src_cat, tmp_path):
    d = Catalog.create(str(tmp_path / "solo-chicas"), src_cat.name, src_cat.height_mm, 0.4)
    copy_to(d, src_cat, ["torso-otra-d"])                                         # solo la de cara 0,3: no entra el pasador
    z = export(src_cat, tmp_path, "pierna.zip", piezas=["pierna-muneco-01"])
    p = CR.check(d, fichas_of(z)).piezas[0]
    assert p.nivel == "no" and not p.encaja
    assert p.puertos["cadera"].mejor == "no" and p.puertos["cadera"].motivos
    assert any(l.strip().startswith("· ") for l in CR.report_lines(CR.check(d, fichas_of(z))))


def test_sin_piezas_con_que_compararse(tmp_path, src_cat):
    vacio = Catalog.create(str(tmp_path / "vacio"), src_cat.name, src_cat.height_mm, 0.4)
    z = export(src_cat, tmp_path, "pierna.zip", piezas=["pierna-muneco-01"])
    rep = CR.check(vacio, fichas_of(z))
    assert rep.piezas[0].nivel == "sin_datos" and not rep.piezas[0].encaja
    assert "sin piezas con qué compararse" in rep.resumen()


def test_otra_altura_de_linea_no_entra(src_cat, zip_pierna, tmp_path):
    alta = Catalog.create(str(tmp_path / "alta"), src_cat.name, src_cat.height_mm + 5.0, 0.4)
    rep = CR.check(alta, fichas_of(zip_pierna))
    assert not rep.linea_ok and "línea nueva" in rep.linea_motivo and rep.resumen().startswith("no entra")
    assert len(CR.report_lines(rep)) == 1


def test_instalada_y_conflicto(dest, zip_pierna, src_cat):
    install(zip_pierna, dest.root)
    f = fichas_of(zip_pierna)
    assert CR.check(Catalog.open(dest.root), f).piezas[0].estado == "instalada"
    meta, mesh = src_cat.load("pierna-muneco-01")
    otra = Catalog.create(os.path.join(os.path.dirname(dest.root), "otra"), src_cat.name, src_cat.height_mm, 0.4)
    mesh2 = mesh.copy()
    mesh2.apply_scale(1.01)
    otra.add_piece(mesh2, dataclasses.replace(meta, stl="", sha256=""))
    p = CR.check(otra, f).piezas[0]
    assert p.estado == "conflicto" and any("otro contenido" in a for a in p.avisos)


def test_avisa_de_roles_de_color_que_la_paleta_no_tiene(dest, zip_pierna):
    d = json.loads(X.dump_json(X.fichas_from_zip(zip_pierna)))
    d["fichas"][0]["color"] = {"modo": "roles", "roles": ["piel", "pantalon"], "fuente": "x.color.npz"}
    p = CR.check(dest, CR.parse_fichas(d)).piezas[0]
    assert any("piel, pantalon" in a for a in p.avisos)


def test_parse_fichas_estricto_con_el_formato_y_tolerante_con_cada_ficha(zip_pierna):
    d = json.loads(X.dump_json(X.fichas_from_zip(zip_pierna)))
    for bad in ("no json", "[]", json.dumps({**d, "formato": "afm-fichas-2"}), json.dumps({**d, "fichas": "x"})):
        with pytest.raises(CR.FichasError):
            CR.parse_fichas(bad)
    d["fichas"].append({"formato": "afm-pieza-1", "id": "Roto Mal", "version": 1, "tipo": "pierna"})
    f = CR.parse_fichas(d)
    assert len(f.metas) == 1 and len(f.avisos) == 1 and "ficha 2" in f.avisos[0]


# ------------------------------------------------------------------------------------------------ red (servidor local)
def entry_con_fichas(srv, zip_path, fichas_body=None, ruta="/data/fichas/owner@pierna-1.0.0.json", **over):
    z = open(zip_path, "rb").read()
    d = entry_dict(srv.base, z, pid="pierna", folder="owner@pierna", linea={"nombre": "ejemplo-100", "altura_mm": 100.0}, **over)
    d["latest"]["version"] = "1.0.0"
    if fichas_body is not None:
        srv.routes[ruta] = (200, {}, fichas_body)
    serve_index(srv, d)
    return R.parse_index(srv.routes["/data/index.json"](_H(srv))[2] if callable(srv.routes["/data/index.json"]) else srv.routes["/data/index.json"][2],
                         url=srv.base + "/data/index.json").entries[0]


class _H:                                                        # `serve_index` registra un handler que mira los encabezados del pedido
    def __init__(self, srv):
        self.headers = {}


def body_of(zip_path):
    return X.dump_json(X.fichas_from_zip(zip_path)).encode("utf-8")


def test_la_url_de_fichas_sale_del_indice_o_de_la_convencion(srv, zip_pierna):
    e = entry_con_fichas(srv, zip_pierna)
    assert CR.fichas_url(e) == srv.base + "/data/fichas/owner@pierna-1.0.0.json"           # convención junto al index.json
    e.latest = R.Release(**{**e.latest.__dict__, "fichas": srv.base + "/otro/f.json"})
    assert CR.fichas_url(e) == srv.base + "/otro/f.json"
    with pytest.raises(R.RemoteError, match="versión 9.9.9"):
        CR.fichas_url(e, "9.9.9")


def test_fetch_baja_solo_las_fichas_y_las_guarda(srv, zip_pierna, tmp_path):
    e = entry_con_fichas(srv, zip_pierna, body_of(zip_pierna))
    cache = str(tmp_path / "c")
    f = CR.fetch_fichas(e, policy=POLICY, cache=cache, **FAST)
    assert [m.ref for m in f.metas] == ["pierna-muneco-01@1"]
    assert all(not path.endswith(".zip") for path, _h in srv.log if not path.endswith("index.json"))       # no se pidió el zip
    srv.routes["/data/fichas/owner@pierna-1.0.0.json"] = (503, {}, b"")                    # el servidor falla: sale de la copia guardada
    g = CR.fetch_fichas(e, policy=POLICY, cache=cache, **FAST)
    assert g.metas[0].ref == "pierna-muneco-01@1" and g.avisos and "sin conexión" in g.avisos[0]
    assert CR.fetch_fichas(e, policy=POLICY, cache=cache, offline=True).metas


def test_fetch_sin_red_ni_copia(srv, zip_pierna, tmp_path):
    e = entry_con_fichas(srv, zip_pierna)
    with pytest.raises(R.OfflineError):
        CR.fetch_fichas(e, policy=POLICY, cache=str(tmp_path / "c"), offline=True)
    with pytest.raises(R.RemoteError, match="HTTP 404"):
        CR.fetch_fichas(e, policy=POLICY, cache=str(tmp_path / "c"), **FAST)               # 404: no hay fichas y no se reintenta
    srv.routes["/data/fichas/owner@pierna-1.0.0.json"] = (503, {}, b"")
    with pytest.raises(R.NetworkError):
        CR.fetch_fichas(e, policy=POLICY, cache=str(tmp_path / "c"), **FAST)               # 5xx sin copia guardada


def test_fetch_no_guarda_lo_que_no_se_puede_leer(srv, zip_pierna, tmp_path):
    e = entry_con_fichas(srv, zip_pierna, b"esto no es json")
    cache = str(tmp_path / "c")
    with pytest.raises(CR.FichasError):
        CR.fetch_fichas(e, policy=POLICY, cache=cache, **FAST)
    assert not os.path.exists(os.path.join(cache, "fichas")) or not os.listdir(os.path.join(cache, "fichas"))


def test_fetch_respeta_la_politica_de_hosts(srv, zip_pierna):
    e = entry_con_fichas(srv, zip_pierna)
    e.latest = R.Release(**{**e.latest.__dict__, "fichas": "https://evil.example/f.json"})
    with pytest.raises(R.HostNotAllowed):
        CR.fetch_fichas(e, policy=POLICY, **FAST)


# ------------------------------------------------------------------------------------------------ CLI
@pytest.fixture
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("AFM_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("AFM_REMOTO", str(tmp_path / "remoto.json"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("AFM_CATALOGO", raising=False)
    monkeypatch.setattr(R, "policy_for", lambda url, **kw: POLICY)


def publicar(srv, zip_path, con_fichas=True):
    z = open(zip_path, "rb").read()
    d = entry_dict(srv.base, z, pid="pierna", folder="owner@pierna", linea={"nombre": "ejemplo-100", "altura_mm": 100.0})
    serve_index(srv, d)
    if con_fichas:
        srv.routes["/data/fichas/owner@pierna-1.0.0.json"] = (200, {}, body_of(zip_path))


def test_cli_ver_compat(entorno, srv, zip_pierna, dest, capsys):
    publicar(srv, zip_pierna)
    assert main(["catalogo", "remoto", "ver", "pierna", "--indice", srv.base, "--compat", "--en", dest.root]) == 0
    out = capsys.readouterr().out
    assert "Compatibilidad con tu catálogo: 1 de 1 pieza encajan" in out and "pierna-muneco-01@1" in out and "cadera: compatible con" in out
    assert not any(p.endswith(".zip") for p, _h in srv.log if p.startswith("/zips"))                 # no se bajó el zip


def test_cli_ver_sin_compat_no_pide_fichas(entorno, srv, zip_pierna, capsys):
    publicar(srv, zip_pierna)
    assert main(["catalogo", "remoto", "ver", "pierna", "--indice", srv.base]) == 0
    assert not any("fichas" in p for p, _h in srv.log)


def test_cli_ver_compat_sin_catalogo_local(entorno, srv, zip_pierna, tmp_path, capsys):
    publicar(srv, zip_pierna)
    assert main(["catalogo", "remoto", "ver", "pierna", "--indice", srv.base, "--compat", "--en", str(tmp_path / "nada")]) == 0
    assert "No hay un catálogo" in capsys.readouterr().out


def test_cli_ver_compat_fichas_ausentes_es_error_claro(entorno, srv, zip_pierna, dest, capsys):
    publicar(srv, zip_pierna, con_fichas=False)
    assert main(["catalogo", "remoto", "ver", "pierna", "--indice", srv.base, "--compat", "--en", dest.root]) == 2
    assert "HTTP 404" in capsys.readouterr().err
