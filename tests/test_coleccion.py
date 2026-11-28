"""D34 · colecciones (PLAN_catalogos_online.md §5.12): pines a versión y sha256 exactos, plan agregado todo o nada y CK402 contra el índice."""
import json
import os

import pytest

from afm import autor as A
from afm import coleccion as C
from afm import dependencias as D
from afm import instalar as I
from afm import paquete as Q
from afm import remoto as R
from afm.catalog import Catalog
from afm.cli import main
from test_dependencias import (entorno, ent, fetched, idx, make_pkg, messages, publish_world)  # noqa: F401  (entorno es autouse)
from test_exportar import add_arm, add_recipe, cat  # noqa: F401  (cat es una fixture)
from test_instalar import dest  # noqa: F401  (dest es una fixture)
from test_remoto import entry_dict, index_body, release, srv  # noqa: F401  (srv es una fixture)


def pin(index, pid, version=None, **over):
    """Un pin tal como lo fijaría una colección sana; `over` pisa campos para armar pines rotos (`version` puede no existir en el índice)."""
    e = index.find(pid)
    rel = e.release(version) or e.latest
    return Q.Pin(**{**dict(id=pid, github=e.github, version=version or rel.version, sha256=rel.sha256), **over})


def coleccion(*pines, pid="mi-coleccion") -> Q.Paquete:
    return Q.Paquete(id=pid, nombre="Mi colección", version="1.0.0", descripcion="d", tipo="coleccion", autores=[Q.Author("Ana", "owner")],
                     licencia="CC-BY-4.0", origen="propia", categorias=["colecciones"], contenido=Q.Contents(pines=list(pines)))


# ------------------------------------------------------------------------------------------------------------------------ CK402 contra el índice
def test_pines_bien_resuelven_en_el_orden_de_la_coleccion(tmp_path):
    i = idx(ent("base"), ent("guemes"))
    col = coleccion(pin(i, "base"), pin(i, "guemes"))
    assert C.comprobar_pines([i], col) == []
    dep = C.resolver([i], col, str(tmp_path / "c"))
    assert dep.ok and [s.entry.id for s in dep.pasos] == ["base", "guemes"] and dep.pasos[0].por == "colección mi-coleccion" and dep.linea == "proceres-130"
    assert dep.raiz == str(tmp_path / "c")


def test_sin_raiz_se_usa_la_carpeta_de_la_linea(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    i = idx(ent("base"))
    dep = C.resolver([i], coleccion(pin(i, "base")))
    assert dep.raiz == str(tmp_path / "data" / "afm" / "catalogos" / "proceres-130")


def test_sha256_distinto_es_un_error_y_se_dicen_todos_los_problemas(tmp_path):
    i = idx(ent("base"), ent("guemes", "1.2.0"), ent("otro"))
    col = coleccion(pin(i, "base", sha256="a" * 64), pin(i, "guemes", version="1.0.0"), pin(i, "otro"), Q.Pin("fantasma", "owner/fantasma", "1.0.0", "b" * 64))
    msgs = [i_.message for i_ in C.comprobar_pines([i], col)]
    assert any("el sha256 de «base» 1.0.0 no coincide" in m for m in msgs)
    assert any("no tiene la versión 1.0.0 de «guemes»" in m for m in msgs)
    assert any("el pin «fantasma» no está en los índices" in m for m in msgs)
    assert len(msgs) == 3                                                     # «otro» está bien: no se mezcla con los errores
    assert not C.resolver([i], col, str(tmp_path / "c")).ok


def test_pin_de_otro_repo_es_otro_paquete():
    i = idx(ent("base", github="owner/base"))
    msgs = [x.message for x in C.comprobar_pines([i], coleccion(pin(i, "base", github="intruso/base")))]
    assert msgs and "«intruso/base»" in msgs[0] and "owner/base" in msgs[0]


def test_pin_retirado_bloqueado_o_inaccesible_deja_a_la_coleccion_fuera():
    i = idx(ent("a", retiradas=["1.0.0"]), ent("b"), ent("c", update_check="inaccesible"), bloqueados=[{"id": "b", "motivo": "reclamo de derechos", "fecha": "2026-11-20"}])
    col = coleccion(pin(i, "a"), pin(i, "b"), pin(i, "c"))
    text = " | ".join(x.message for x in C.comprobar_pines([i], col))
    assert "fue retirada" in text and "reclamo de derechos" in text and "inaccesible" in text
    assert C.comprobar_pines([i], coleccion(pin(i, "a")), allow_retirada=True) == []


def test_pines_de_lineas_distintas():
    i = idx(ent("a", linea="proceres-127"), ent("b", linea="proceres-130"))
    msgs = [x.message for x in C.comprobar_pines([i], coleccion(pin(i, "a"), pin(i, "b")))]
    assert len(msgs) == 1 and "líneas distintas" in msgs[0] and "proceres-127: a" in msgs[0]


def test_dos_indices_con_el_mismo_id_es_ambiguo():
    a, b = idx(ent("base")), idx(ent("base"))
    a.entries[0].indice, b.entries[0].indice = "https://uno/", "https://dos/"
    msgs = [x.message for x in C.comprobar_pines([a, b], coleccion(pin(a, "base")))]
    assert msgs and "más de un índice" in msgs[0]


def test_cargar_rechaza_lo_que_no_es_una_coleccion(cat, tmp_path):
    add_arm(cat)
    z, _ = make_pkg(cat, tmp_path, "base-pk", "brazo-test-01")
    with pytest.raises(I.InstallError, match="no una colección"):
        C.cargar(z)


# ------------------------------------------------------------------------------------------------------------------------ instalar de punta a punta
def publish_collection(srv, cat, tmp_path, *, tamper_sha=False, linea_pines=None):
    """El mundo de `publish_world` (base-pk y dep-pk) más una colección que fija los dos, servida en el mismo índice."""
    eb, ed = publish_world(srv, cat, tmp_path)
    d = str(tmp_path / "col")
    A.init_package_dir(d, id="mi-coleccion", perfil="coleccion", autores=["Ana Pérez:ana"], licencia="CC-BY-4.0", origen="propia", github="owner/coleccion",
                       linea="", altura_mm=0.0)
    raw = json.load(open(os.path.join(d, "paquete.json"), encoding="utf-8"))
    raw["descripcion"] = "Fija la base y el héroe a versiones exactas."
    raw["contenido"] = {"pines": [{"id": "base-pk", "github": "owner/repo", "version": "1.0.0", "sha256": eb["latest"]["zip"]["sha256"]},
                                  {"id": "dep-pk", "github": "owner/repo", "version": "1.0.0", "sha256": "0" * 64 if tamper_sha else ed["latest"]["zip"]["sha256"]}]}
    json.dump(raw, open(os.path.join(d, "paquete.json"), "w", encoding="utf-8"))
    z = str(tmp_path / "mi-coleccion.zip")
    A.pack_dir(d, z, check_manifest=False)
    body = open(z, "rb").read()
    srv.routes["/zips/col.zip"] = (200, {}, body)
    ec = entry_dict(srv.base, body, pid="mi-coleccion", folder="owner@mi-coleccion", nombre="Mi colección", tipo="coleccion", github="owner/coleccion", linea={})
    ec["latest"] = release(srv.base, body, path="/zips/col.zip")
    srv.routes["/data/index.json"] = (200, {}, index_body(eb, ed, ec))
    return z


def run(*a):
    return main(["catalogo", "remoto", *a])


def test_instalar_la_coleccion_deja_lo_mismo_que_instalar_a_mano_cada_pin(srv, cat, dest, tmp_path, capsys):
    publish_collection(srv, cat, tmp_path)
    assert run("instalar", "mi-coleccion", "--en", dest.root, "--si", "--indice", srv.base) == 0
    out = capsys.readouterr().out
    assert "fija 2 paquete(s); se instalan todos o ninguno" in out and "1. owner@base-pk 1.0.0" in out
    manual = Catalog.create(str(tmp_path / "manual"), "proceres-130", 130.0, 0.4)
    for pid in ("base-pk", "dep-pk"):
        assert run("instalar", pid, "--en", manual.root, "--si", "--sin-dependencias", "--indice", srv.base) == 0
    a, b = Catalog.open(dest.root), Catalog.open(manual.root)
    assert sorted(m.ref for m in a.list(all_versions=True)) == sorted(m.ref for m in b.list(all_versions=True)) == ["brazo-test-01@1", "brazo-test-02@1"]
    assert set(I.load_registry(dest.root)["paquetes"]) == set(I.load_registry(manual.root)["paquetes"]) == {"base-pk", "dep-pk"}      # la colección no figura
    assert a.verify().ok


def test_en_una_carpeta_nueva_sin_dar_la_ruta_usa_la_carpeta_de_la_linea(srv, cat, tmp_path, monkeypatch):
    publish_collection(srv, cat, tmp_path)
    assert run("instalar", "mi-coleccion", "--si", "--indice", srv.base) == 0
    root = str(tmp_path / "data" / "afm" / "catalogos" / "proceres-130")
    assert Catalog.open(root).name == "proceres-130" and len(Catalog.open(root).list(all_versions=True)) == 2


def test_simular_no_escribe_y_pregunta_una_sola_vez(srv, cat, dest, tmp_path, capsys, monkeypatch):
    publish_collection(srv, cat, tmp_path)
    before = I.tree_digest(dest.root)
    assert run("instalar", "mi-coleccion", "--en", dest.root, "--simular", "--indice", srv.base) == 0
    assert "Simulación: no se escribió nada." in capsys.readouterr().out and I.tree_digest(dest.root) == before
    asked = []
    monkeypatch.setattr("afm.remoto_cli._ask", lambda q: asked.append(q) or "n")
    assert run("instalar", "mi-coleccion", "--en", dest.root, "--indice", srv.base) == 1
    assert len(asked) == 1 and "2 paquetes" in asked[0] and I.tree_digest(dest.root) == before


def test_un_pin_con_otro_sha256_no_instala_nada(srv, cat, dest, tmp_path, capsys):
    publish_collection(srv, cat, tmp_path, tamper_sha=True)
    before = I.tree_digest(dest.root)
    assert run("instalar", "mi-coleccion", "--en", dest.root, "--si", "--indice", srv.base) == 1
    assert "el sha256 de «dep-pk» 1.0.0 no coincide" in capsys.readouterr().out
    assert I.tree_digest(dest.root) == before and I.load_registry(dest.root)["paquetes"] == {}       # ni siquiera el primer pin, que estaba bien


def test_un_fallo_a_mitad_de_la_instalacion_deja_el_catalogo_como_estaba(srv, cat, dest, tmp_path, monkeypatch, capsys):
    publish_collection(srv, cat, tmp_path)
    before = I.tree_digest(dest.root)
    real, calls = I._dump, []

    def boom(obj):
        calls.append(1)
        if len(calls) == 2:                                                  # el registro del 2.º pin: el primero ya escribió y el segundo ya movió sus archivos
            raise OSError("disco lleno")
        return real(obj)

    monkeypatch.setattr(I, "_dump", boom)
    assert run("instalar", "mi-coleccion", "--en", dest.root, "--si", "--indice", srv.base) == 1
    assert "no se instaló nada" in capsys.readouterr().err and len(calls) == 2
    assert I.tree_digest(dest.root) == before


def test_un_zip_de_pin_alterado_en_el_servidor_se_detecta(srv, cat, dest, tmp_path, capsys):
    publish_collection(srv, cat, tmp_path)
    srv.routes["/zips/dep.zip"] = (200, {}, b"otro contenido")
    before = I.tree_digest(dest.root)
    assert run("instalar", "mi-coleccion", "--en", dest.root, "--si", "--indice", srv.base) == 2
    assert I.tree_digest(dest.root) == before


def test_reinstalar_la_coleccion_ya_instalada_no_hace_nada(srv, cat, dest, tmp_path, capsys):
    publish_collection(srv, cat, tmp_path)
    assert run("instalar", "mi-coleccion", "--en", dest.root, "--si", "--indice", srv.base) == 0
    capsys.readouterr()
    assert run("instalar", "mi-coleccion", "--en", dest.root, "--si", "--indice", srv.base) == 0
    assert "Nada que instalar" in capsys.readouterr().out


# ------------------------------------------------------------------------------------------------------------------------ CI del índice
def test_ci_comprobar_contra_un_index_json(srv, cat, tmp_path, capsys):
    z = publish_collection(srv, cat, tmp_path)
    index_path = tmp_path / "index.json"
    index_path.write_bytes(srv.routes["/data/index.json"][2])
    assert main(["catalogo", "coleccion", "comprobar", z, "--indice", str(index_path)]) == 0
    assert "los 2 pin(es) existen con el sha256" in capsys.readouterr().out
    data = json.loads(index_path.read_text())
    next(e for e in data["paquetes"] if e["id"] == "dep-pk")["latest"]["zip"]["sha256"] = "f" * 64
    index_path.write_text(json.dumps(data))
    assert main(["catalogo", "coleccion", "comprobar", z, "--indice", str(index_path)]) == 1
    out = capsys.readouterr().out
    assert "CK402 dep-pk" in out and "no coincide" in out and "queda fuera" in out
    assert main(["catalogo", "coleccion", "comprobar", z, "--indice", str(tmp_path / "no_esta.json")]) == 2
    assert main(["catalogo", "coleccion", "comprobar", str(tmp_path / "no.zip"), "--indice", str(index_path)]) == 2
