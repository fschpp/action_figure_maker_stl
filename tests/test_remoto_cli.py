"""D14 · `afm catalogo remoto …` (PLAN_catalogos_online.md §5.6): buscar, ver, versiones, instalar y actualizar contra un servidor local."""
import json
import os

import pytest

from afm import instalar as I
from afm import remoto as R
from afm.catalog import Catalog
from afm.cli import main
from test_exportar import add_arm, cat  # noqa: F401  (cat es una fixture)
from test_instalar import dest, export  # noqa: F401  (dest es una fixture)
from test_remoto import POLICY, Server, entry_dict, index_body, release, sha, srv  # noqa: F401  (srv es una fixture)


@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("AFM_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("AFM_REMOTO", str(tmp_path / "remoto.json"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("AFM_CATALOGO", raising=False)
    monkeypatch.setattr(R, "policy_for", lambda url, **kw: POLICY)          # las pruebas hablan http con 127.0.0.1


def run(*a):
    return main(["catalogo", "remoto", *a])


def publish(srv, *entries, path="/data/index.json"):
    srv.routes[path] = (200, {}, index_body(*entries))


def real(srv, cat, tmp_path, version="1.0.0", name="p.zip"):
    z = export(cat, tmp_path, name, github="owner/repo", version=version)
    body = open(z, "rb").read()
    srv.routes[f"/zips/{name}"] = (200, {}, body)
    pkg = I.peek_package(z)
    d = entry_dict(srv.base, body, pid=pkg.id, folder=f"owner@{pkg.id}", nombre="Brazo")
    d["latest"] = release(srv.base, body, version=version, path=f"/zips/{name}")
    return d, pkg.id


def test_sin_indices_configurados(capsys):
    assert run("buscar") == 2
    assert "no hay índices configurados" in capsys.readouterr().err


def test_buscar_filtra_ordena_y_oculta_inaccesibles(srv, capsys):
    publish(srv, entry_dict(srv.base, pid="uno", folder="a@uno", nombre="Espada larga", descargas={"total": 5}, tipo="piezas", etiquetas=["arma"]),
            entry_dict(srv.base, pid="dos", folder="b@dos", nombre="Casco", descargas={"total": 50}, tipo="heroe", licencia="CC0-1.0"),
            entry_dict(srv.base, pid="tres", folder="c@tres", nombre="Sin contador", descargas=None),
            entry_dict(srv.base, pid="caido", folder="d@caido", nombre="Caído", update_check="inaccesible", descargas={"total": 999}))
    assert run("buscar", "--indice", srv.base) == 0
    out = capsys.readouterr().out
    assert [l.split()[0] for l in out.splitlines()[:3]] == ["b@dos", "a@uno", "c@tres"]       # descargas desc; sin contador al final; el inaccesible no sale
    assert "d@caido" not in out and "Listar no es verificar" in out and "no instalado" in out
    run("buscar", "arma", "--indice", srv.base)
    assert capsys.readouterr().out.splitlines()[0].startswith("a@uno")
    run("buscar", "--tipo", "heroe", "--indice", srv.base)
    assert capsys.readouterr().out.splitlines()[0].startswith("b@dos")
    run("buscar", "--licencia", "cc0-1.0", "--indice", srv.base)
    assert capsys.readouterr().out.splitlines()[0].startswith("b@dos")
    run("buscar", "--ocultos", "--indice", srv.base)
    assert "d@caido" in capsys.readouterr().out
    run("buscar", "nada-que-coincida", "--indice", srv.base)
    assert "No hay paquetes" in capsys.readouterr().out


def test_indice_configurado_en_remoto_json(srv, tmp_path, capsys):
    publish(srv, entry_dict(srv.base))
    R.save_config([srv.base])
    assert run("buscar") == 0 and "owner@brazo-test" in capsys.readouterr().out


def test_ver_y_versiones(srv, capsys):
    e = entry_dict(srv.base, descripcion="Un brazo.", retiradas=["0.9.0"], motor=">=99.0.0")
    e["versiones"] = [release(srv.base, b"viejo", version="0.9.0", path="/zips/v.zip"), e["latest"]]
    publish(srv, e)
    assert run("ver", "brazo-test", "--indice", srv.base) == 0
    out = capsys.readouterr().out
    assert "CC-BY-4.0" in out and sha(b"zip") in out and "Retiradas:" in out and "Un brazo." in out and "pide el motor >=99.0.0" in out and "Listar no es verificar" in out
    assert run("versiones", "owner@brazo-test", "--indice", srv.base) == 0
    out = capsys.readouterr().out
    assert "1.0.0" in out and "última" in out and "RETIRADA" in out
    assert run("ver", "no-existe", "--indice", srv.base) == 2


def test_id_en_dos_indices_es_ambiguo(srv, capsys):
    publish(srv, entry_dict(srv.base))
    publish(srv, entry_dict(srv.base), path="/otro/data/index.json")
    assert run("ver", "brazo-test", "--indice", srv.base, "--indice", srv.base + "/otro/data/index.json") == 2
    assert "más de un lugar" in capsys.readouterr().err


def test_un_indice_caido_no_impide_usar_otro(srv, capsys):
    publish(srv, entry_dict(srv.base))
    assert run("buscar", "--indice", srv.base, "--indice", "http://127.0.0.1:9/") == 0
    cap = capsys.readouterr()
    assert "owner@brazo-test" in cap.out and "AVISO: índice http://127.0.0.1:9/" in cap.err
    assert run("buscar", "--indice", "http://127.0.0.1:9/") == 2


def test_sin_red_usa_el_guardado(srv, capsys):
    publish(srv, entry_dict(srv.base))
    assert run("buscar", "--indice", srv.base) == 0
    srv.close()
    capsys.readouterr()
    assert run("buscar", "--indice", srv.base) == 0
    cap = capsys.readouterr()
    assert "owner@brazo-test" in cap.out and "índice guardado" in cap.err
    assert run("buscar", "--sin-red", "--indice", srv.base) == 0


def test_instalar_simular_no_escribe_y_si_instala(srv, cat, dest, tmp_path, capsys):
    add_arm(cat)
    d, pid = real(srv, cat, tmp_path)
    publish(srv, d)
    before = I.tree_digest(dest.root)
    assert run("instalar", pid, "--en", dest.root, "--simular", "--indice", srv.base) == 0
    assert I.tree_digest(dest.root) == before and "Simulación" in capsys.readouterr().out
    assert run("instalar", f"owner@{pid}@1.0.0", "--en", dest.root, "--si", "--indice", srv.base) == 0
    assert "Instalado" in capsys.readouterr().out
    assert Catalog.open(dest.root).verify().ok and I.load_registry(dest.root)["paquetes"][pid]["indice"].startswith(srv.base)
    assert run("instalar", pid, "--en", dest.root, "--si", "--indice", srv.base) == 0
    assert "Nada que instalar" in capsys.readouterr().out


def test_instalar_sin_en_usa_la_carpeta_de_la_linea_y_el_estado_lo_refleja(srv, cat, tmp_path, capsys, monkeypatch):
    add_arm(cat)
    d, pid = real(srv, cat, tmp_path)
    publish(srv, d)
    monkeypatch.setenv("AFM_CATALOGO", str(tmp_path / "ignorada"))
    assert run("instalar", pid, "--si", "--indice", srv.base) == 0
    out = capsys.readouterr().out
    assert "se ignora al instalar" in out
    root = os.path.join(str(tmp_path / "data"), "afm", "catalogos", "proceres-130")
    assert I.load_registry(root)["paquetes"][pid]["version"] == "1.0.0"
    run("buscar", "--indice", srv.base)
    assert "instalado 1.0.0" in capsys.readouterr().out


def test_instalar_con_hash_que_cambio_no_toca_nada(srv, cat, dest, tmp_path, capsys):
    add_arm(cat)
    d, pid = real(srv, cat, tmp_path)
    srv.routes["/zips/p.zip"] = (200, {}, b"otra cosa")
    publish(srv, d)
    before = I.tree_digest(dest.root)
    assert run("instalar", pid, "--en", dest.root, "--si", "--indice", srv.base) == 2
    assert "cambió desde que se indexó" in capsys.readouterr().err and I.tree_digest(dest.root) == before


def test_instalar_version_inexistente_o_retirada(srv, cat, dest, tmp_path, capsys):
    add_arm(cat)
    d, pid = real(srv, cat, tmp_path)
    publish(srv, d)
    assert run("instalar", f"{pid}@9.9.9", "--en", dest.root, "--si", "--indice", srv.base) == 2
    d["retiradas"] = ["1.0.0"]
    publish(srv, d)
    assert run("instalar", pid, "--en", dest.root, "--si", "--indice", srv.base) == 2
    assert "retirada" in capsys.readouterr().err
    assert run("instalar", pid, "--en", dest.root, "--si", "--permitir-retirada", "--indice", srv.base) == 0


def test_instalar_inaccesible_se_rechaza(srv, cat, dest, tmp_path, capsys):
    add_arm(cat)
    d, pid = real(srv, cat, tmp_path)
    d["update_check"] = "inaccesible"
    publish(srv, d)
    assert run("instalar", pid, "--en", dest.root, "--si", "--indice", srv.base) == 2
    assert "inaccesible" in capsys.readouterr().err


def test_actualizar_instala_la_version_nueva_y_es_aditiva(srv, cat, dest, tmp_path, capsys):
    add_arm(cat)
    d1, pid = real(srv, cat, tmp_path, "1.0.0", "p1.zip")
    publish(srv, d1)
    assert run("instalar", pid, "--en", dest.root, "--si", "--indice", srv.base) == 0
    assert run("actualizar", "--en", dest.root, "--indice", srv.base) == 0
    assert "Todo está al día" in capsys.readouterr().out
    d2, _ = real(srv, cat, tmp_path, "1.1.0", "p2.zip")
    publish(srv, d2)
    capsys.readouterr()
    assert run("actualizar", "--en", dest.root, "--simular", "--indice", srv.base) == 0
    out = capsys.readouterr().out
    assert "1.0.0 → 1.1.0" in out and I.load_registry(dest.root)["paquetes"][pid]["version"] == "1.0.0"
    assert run("actualizar", pid, "--en", dest.root, "--si", "--indice", srv.base) == 0
    assert I.load_registry(dest.root)["paquetes"][pid]["version"] == "1.1.0" and Catalog.open(dest.root).verify().ok


def test_actualizar_no_pisa_un_paquete_de_otro_repo(srv, cat, dest, tmp_path, capsys):
    add_arm(cat)
    d1, pid = real(srv, cat, tmp_path, "1.0.0", "p1.zip")
    publish(srv, d1)
    run("instalar", pid, "--en", dest.root, "--si", "--indice", srv.base)
    d2, _ = real(srv, cat, tmp_path, "1.1.0", "p2.zip")
    d2["github"] = "intruso/repo"
    publish(srv, d2)
    capsys.readouterr()
    assert run("actualizar", "--en", dest.root, "--si", "--indice", srv.base) == 0
    out = capsys.readouterr().out
    assert "no se actualiza solo" in out and I.load_registry(dest.root)["paquetes"][pid]["version"] == "1.0.0"


def test_actualizar_sin_catalogos(srv, capsys):
    publish(srv, entry_dict(srv.base))
    assert run("actualizar", "--indice", srv.base) == 0
    assert "No hay catálogos" in capsys.readouterr().out


def test_parse_ref_y_search_puros():
    assert R.parse_ref("a@b") == ("a@b", None) and R.parse_ref("b@1.2.0") == ("b", "1.2.0") and R.parse_ref("a@b@1.2.0-rc.1") == ("a@b", "1.2.0-rc.1")
    with pytest.raises(R.RemoteError):
        R.search([], orden="raro")
