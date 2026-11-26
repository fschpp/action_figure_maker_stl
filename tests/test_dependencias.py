"""D16 · dependencias, actualización aditiva, retiradas y bloqueados (PLAN_catalogos_online.md §5.5, §5.9 y D16).

Tres bloques: resolver `requiere`/`conflicts` sobre el índice (sin red ni zips), bloqueos y retiradas (índice, descarga, auditoría) y la cadena
de instalación todo o nada con zips reales servidos por un `http.server` local."""
import json
import os

import pytest

from afm import dependencias as D
from afm import instalar as I
from afm import paquete as Q
from afm import remoto as R
from afm.catalog import Catalog
from afm.cli import main
from test_catalog import arm_piece
from test_exportar import OWNER, add_arm, add_recipe, cat  # noqa: F401  (cat es una fixture)
from test_instalar import dest, export  # noqa: F401  (dest es una fixture)
from test_remoto import POLICY, Server, entry_dict, index_body, release, sha, srv  # noqa: F401  (srv es una fixture)

BASE = "http://x"


# ------------------------------------------------------------------------------------------------------------------------ ayudas

def ent(pid, version="1.0.0", *, requiere=(), conflicts=(), versions=(), retiradas=(), autor="owner", github=None, linea="proceres-130", **over):
    """Entrada de índice sin red: `versions` = [(versión, prerelease)] además de `latest`."""
    d = entry_dict(BASE, pid=pid, folder=f"{autor}@{pid}", autor=autor, requiere=list(requiere), conflicts=list(conflicts), retiradas=list(retiradas),
                   linea={"nombre": linea, "altura_mm": 130.0}, github=github or f"{autor}/{pid}", **over)
    d["latest"] = release(BASE, f"{pid}{version}".encode(), version=version, path=f"/zips/{pid}-{version}.zip")
    d["versiones"] = []
    for v, pre in versions:
        r = release(BASE, f"{pid}{v}".encode(), version=v, path=f"/zips/{pid}-{v}.zip")
        r["prerelease"] = pre
        d["versiones"].append(r)
    return d


def idx(*entries, **over) -> R.Index:
    return R.parse_index(index_body(*entries, **over), url=BASE + "/")


def want(index, pid, version=None):
    return [(index.find(pid), version)]


def ids(plan):
    return [s.entry.id for s in plan.pasos]


def messages(plan):
    return " | ".join(i.message for i in plan.issues)


def set_registry(root, **pkgs):
    os.makedirs(root, exist_ok=True)
    reg = {"formato": I.REGISTRY_FORMAT, "paquetes": {pid: {"version": v, **extra} for pid, (v, extra) in pkgs.items()}}
    with open(os.path.join(root, I.REGISTRY_FILE), "w", encoding="utf-8") as f:
        json.dump(reg, f)


# ------------------------------------------------------------------------------------------------------------------------ resolver

def test_sin_dependencias_es_un_solo_paso(tmp_path):
    i = idx(ent("solo"))
    plan = D.resolve([i], str(tmp_path / "c"), want(i, "solo"))
    assert plan.ok and ids(plan) == ["solo"] and plan.pasos[0].por == "pedido"


def test_cadena_en_orden_de_instalacion_con_la_version_mas_nueva_permitida(tmp_path):
    i = idx(ent("a", requiere=["b@^1.0.0"]),
            ent("b", "2.0.0", requiere=["c"], versions=[("1.0.0", False), ("1.2.0", False), ("1.3.0-rc.1", True)], retiradas=["1.2.0"]),
            ent("c"))
    plan = D.resolve([i], str(tmp_path / "c"), want(i, "a"))
    assert plan.ok and ids(plan) == ["c", "b", "a"]                                       # dependencias primero
    b = next(s for s in plan.pasos if s.entry.id == "b")
    assert b.release.version == "1.0.0" and b.por == "dependencia de a"                  # 2.0.0 no cumple ^1; 1.2.0 retirada; 1.3.0-rc.1 es prerelease
    again = D.resolve([i], str(tmp_path / "c"), want(i, "a"), allow_retirada=True)
    assert next(s for s in again.pasos if s.entry.id == "b").release.version == "1.2.0"
    pre = D.resolve([i], str(tmp_path / "c"), want(i, "a"), allow_retirada=True, allow_prerelease=True)
    assert next(s for s in pre.pasos if s.entry.id == "b").release.version == "1.3.0-rc.1"


def test_una_sola_version_por_id(tmp_path):
    i = idx(ent("a", requiere=["c@^1.0.0"]), ent("b", requiere=["c@^2.0.0"]), ent("c", "2.0.0", versions=[("1.0.0", False)]))
    plan = D.resolve([i], str(tmp_path / "c"), [(i.find("a"), None), (i.find("b"), None)])
    assert not plan.ok and "una sola versión por id" in messages(plan)


def test_dos_pedidos_que_comparten_una_dependencia_la_instalan_una_vez(tmp_path):
    i = idx(ent("a", requiere=["c"]), ent("b", requiere=["c@>=1"]), ent("c"))
    plan = D.resolve([i], str(tmp_path / "c"), [(i.find("a"), None), (i.find("b"), None)])
    assert plan.ok and ids(plan) == ["c", "a", "b"]


def test_ciclo(tmp_path):
    i = idx(ent("a", requiere=["b"]), ent("b", requiere=["c"]), ent("c", requiere=["a"]))
    plan = D.resolve([i], str(tmp_path / "c"), want(i, "a"))
    assert not plan.ok and "ciclo de dependencias: a → b → c → a" in messages(plan)


def test_dependencia_que_ningun_indice_tiene_o_sin_version_que_cumpla(tmp_path):
    i = idx(ent("a", requiere=["falta"]), ent("b", requiere=["c@^3"]), ent("c", "1.0.0"))
    assert "ningún índice consultado lo tiene" in messages(D.resolve([i], str(tmp_path / "c"), want(i, "a")))
    assert "ninguna versión cumple ^3" in messages(D.resolve([i], str(tmp_path / "c"), want(i, "b")))


def test_dependencia_con_todas_las_versiones_retiradas_o_prerelease_dice_por_que(tmp_path):
    i = idx(ent("a", requiere=["c@^1"]), ent("c", "1.0.0", retiradas=["1.0.0"]))
    plan = D.resolve([i], str(tmp_path / "c"), want(i, "a"))
    assert not plan.ok and "1.0.0: retirada" in messages(plan)


def test_dependencia_bloqueada_o_inaccesible_no_se_instala(tmp_path):
    i = idx(ent("a", requiere=["c"]), ent("c"), bloqueados=[{"id": "c", "motivo": "reclamo de derechos", "fecha": "2026-11-20"}])
    plan = D.resolve([i], str(tmp_path / "c"), want(i, "a"))
    assert not plan.ok and "bloqueado por id «c» (reclamo de derechos)" in messages(plan)
    j = idx(ent("a", requiere=["c"]), ent("c", update_check="inaccesible"))
    assert "inaccesible" in messages(D.resolve([j], str(tmp_path / "c"), want(j, "a")))


def test_pista_de_repo_desempata_y_sin_pista_es_ambiguo(tmp_path):
    uno = idx(ent("c", autor="ana"), ent("a", requiere=["c#ana/c"]))
    otro = idx(ent("c", autor="beto"))
    plan = D.resolve([uno, otro], str(tmp_path / "c"), want(uno, "a"))
    assert plan.ok and next(s for s in plan.pasos if s.entry.id == "c").entry.autor == "ana"
    ambiguo = idx(ent("a", requiere=["c"]))
    plan = D.resolve([ambiguo, uno, otro], str(tmp_path / "c"), want(ambiguo, "a"))
    assert not plan.ok and "más de un lugar" in messages(plan) and "#owner/repo" in " ".join(i.fix for i in plan.issues)


def test_lo_ya_instalado_que_alcanza_no_se_vuelve_a_instalar(tmp_path):
    root = str(tmp_path / "c")
    set_registry(root, b=("1.4.0", {}))
    i = idx(ent("a", requiere=["b@^1.0.0"]), ent("b", "1.4.0"))
    plan = D.resolve([i], root, want(i, "a"))
    assert plan.ok and ids(plan) == ["a"]


def test_lo_instalado_que_no_alcanza_se_actualiza_con_aviso(tmp_path):
    root = str(tmp_path / "c")
    set_registry(root, b=("1.0.0", {}))
    i = idx(ent("a", requiere=["b@>=1.2.0"]), ent("b", "1.5.0"))
    plan = D.resolve([i], root, want(i, "a"))
    assert plan.ok and ids(plan) == ["b", "a"] and any("está instalada la 1.0.0" in w.message for w in plan.warnings)


def test_conflicts_en_los_dos_sentidos_y_contra_lo_instalado(tmp_path):
    root = str(tmp_path / "c")
    set_registry(root, viejo=("1.0.0", {}))
    i = idx(ent("a", conflicts=["viejo@<2"]), ent("b"), ent("viejo"))
    assert "no convive con «viejo»" in messages(D.resolve([i], root, want(i, "a")))              # lo nuevo declara el choque
    set_registry(root, viejo=("1.0.0", {"conflicts": ["b"]}))
    assert "«viejo» 1.0.0 no convive" not in messages(D.resolve([i], root, want(i, "b")))
    assert "no convive con «b»" in messages(D.resolve([i], root, want(i, "b")))                   # lo instalado declara el choque
    dentro = idx(ent("a", requiere=["b"], conflicts=["b"]), ent("b"))
    assert not D.resolve([dentro], str(tmp_path / "d"), want(dentro, "a")).ok                    # dentro de la misma cadena


def test_un_choque_que_ya_habia_entre_dos_instalados_no_es_de_esta_instalacion(tmp_path):
    root = str(tmp_path / "c")
    set_registry(root, x=("1.0.0", {"conflicts": ["y"]}), y=("1.0.0", {}))
    i = idx(ent("a"))
    assert D.resolve([i], root, want(i, "a")).ok


def test_paquete_de_otra_linea_no_entra_en_este_catalogo(dest):
    i = idx(ent("a", requiere=["b"]), ent("b", linea="proceres-150"))
    plan = D.resolve([i], dest.root, want(i, "a"))
    assert not plan.ok and "línea «proceres-150» y el destino es la línea «proceres-130»" in messages(plan)


def test_version_pedida_que_no_existe(tmp_path):
    i = idx(ent("a"))
    plan = D.resolve([i], str(tmp_path / "c"), want(i, "a", "9.9.9"))
    assert not plan.ok and "9.9.9" in messages(plan)


def test_registro_ilegible_es_error_y_no_se_pisa(tmp_path):
    root = tmp_path / "c"
    root.mkdir()
    (root / I.REGISTRY_FILE).write_text("{no es json")
    i = idx(ent("a"))
    plan = D.resolve([i], str(root), want(i, "a"))
    assert not plan.ok and I.REGISTRY_FILE in messages(plan)


# ------------------------------------------------------------------------------------------------------------------------ bloqueados y retiradas en el índice

def test_el_indice_lee_los_bloqueos_y_oculta_lo_bloqueado():
    i = idx(ent("uno"), ent("dos", autor="malo"), ent("tres"),
            bloqueados=[{"id": "uno", "motivo": "m1", "fecha": "2026-11-01"}, {"autor": "MALO", "motivo": "m2"},
                        {"sha256": "ab" * 32, "motivo": "m3"}])
    assert len(i.bloqueados) == 3 and i.avisos == []
    assert i.find("uno").bloqueado.startswith("bloqueado por id «uno» (m1), 2026-11-01")
    assert i.find("dos").bloqueado.startswith("bloqueado por autor «MALO»")                       # la coincidencia no distingue mayúsculas
    assert not i.find("uno").visible and not i.find("dos").visible and i.find("tres").visible
    assert R.state(i.find("uno")) == "bloqueado"
    assert [e.id for e in R.search(i.entries, "")] == ["tres"]
    assert {e.id for e in R.search(i.entries, "", incluir_ocultas=True)} == {"uno", "dos", "tres"}


def test_lectura_tolerante_de_bloqueos_rotos():
    i = idx(ent("a"), bloqueados=[{"id": "a", "autor": "x"}, {"sha256": "corto"}, "texto", {"motivo": "sin clave"}, {"id": "otro"}])
    assert [b.id for b in i.bloqueados] == ["otro"] and len(i.avisos) == 4 and i.find("a").visible
    assert any("«bloqueados» no es una lista" in a for a in idx(ent("a"), bloqueados="x").avisos)


def test_bloqueo_por_sha256_afecta_solo_a_esa_version():
    bad = sha(b"a1.0.0")
    i = idx(ent("a", "2.0.0", versions=[("1.0.0", False)]), bloqueados=[{"sha256": sha(b"a1.0.0"), "motivo": "reclamo"}])
    e = i.find("a")
    assert e.visible and e.bloqueado == "" and e.block_of(e.release("1.0.0")) and not e.block_of(e.latest)
    best, _ = D.best_release(e, "<2")
    assert best is None                                                                           # la única 1.x está bloqueada
    assert D.best_release(e)[0].version == "2.0.0" and bad == e.release("1.0.0").sha256


def test_descargar_algo_bloqueado_se_niega_aun_con_permitir_retirada(srv, tmp_path):
    body = b"zip"
    d = entry_dict(srv.base, body, pid="a", retiradas=["1.0.0"])
    srv.routes["/zips/p.zip"] = (200, {}, body)
    srv.routes["/data/index.json"] = (200, {}, index_body(d, bloqueados=[{"id": "a", "motivo": "reclamo"}]))
    i = R.fetch_index(srv.base, policy=POLICY, cache=str(tmp_path / "cache"), retries=1, sleep=lambda s: None)
    with pytest.raises(R.RemoteError, match="bloqueado por id «a»"):
        R.download_package(i.find("a"), policy=POLICY, cache=str(tmp_path / "cache"), allow_retirada=True)
    with pytest.raises(R.RemoteError, match="no se puede instalar"):
        R.resolve([i], "a", instalable=True)
    assert not [f for _r, _d, fs in os.walk(tmp_path / "cache") for f in fs if f.endswith(".zip")]        # no se bajó nada


# ------------------------------------------------------------------------------------------------------------------------ auditoría

def test_audit_avisa_de_retiradas_y_bloqueados_sin_tocar_nada(tmp_path):
    root = str(tmp_path / "c")
    set_registry(root, uno=("1.0.0", {"github": "owner/uno", "sha256": "11" * 32}), dos=("1.0.0", {"github": "malo/dos", "autores": [{"nombre": "M", "github": "malo"}]}),
                 tres=("1.0.0", {"github": "owner/tres", "sha256": "ab" * 32}), cuatro=("1.0.0", {"github": "owner/cuatro", "heredado_sha256": "cd" * 32}),
                 limpio=("1.0.0", {"github": "owner/limpio"}))
    i = idx(ent("uno", "1.1.0", versions=[("1.0.0", False)], retiradas=["1.0.0"]), ent("dos", autor="malo"), ent("limpio"),
            bloqueados=[{"autor": "malo", "motivo": "reclamo"}, {"sha256": "ab" * 32}, {"sha256": "cd" * 32}, {"id": "no-esta"}])
    before = I.tree_digest(root)
    alerts = D.audit([i], root)
    assert I.tree_digest(root) == before                                                         # no borra ni cambia nada
    by = {(a.kind, a.paquete) for a in alerts}
    assert by == {("retirada", "uno"), ("bloqueado", "dos"), ("bloqueado", "tres"), ("bloqueado", "cuatro")}
    retirada = next(a for a in alerts if a.kind == "retirada")
    assert "hay una versión mejor (1.1.0)" in retirada.message and "remoto actualizar uno" in retirada.message
    assert "sigue instalado" in next(a for a in alerts if a.paquete == "dos").message


def test_audit_no_confunde_un_paquete_de_otro_repo_con_el_mismo_id(tmp_path):
    root = str(tmp_path / "c")
    set_registry(root, uno=("1.0.0", {"github": "yo/uno"}))
    i = idx(ent("uno", versions=[("1.0.0", False)], retiradas=["1.0.0"], autor="otro"))
    assert D.audit([i], root) == []


# ------------------------------------------------------------------------------------------------------------------------ cadena con zips reales

@pytest.fixture(autouse=True)
def entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("AFM_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("AFM_REMOTO", str(tmp_path / "remoto.json"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("AFM_CATALOGO", raising=False)
    monkeypatch.setattr(R, "policy_for", lambda url, **kw: POLICY)


def make_pkg(cat, tmp_path, pid, piece, version="1.0.0", requiere=(), **kw):
    base = Q.Paquete(id=pid, nombre=pid, version=version, descripcion="d", tipo="piezas", autores=[Q.Author("Ana", "owner")], licencia="CC-BY-4.0",
                     origen="propia", categorias=["manos"], requiere=list(requiere))
    z = export(cat, tmp_path, f"{pid}-{version}.zip", piezas=[piece], base=base, github="owner/repo", **kw)
    return z, open(z, "rb").read()


def publish_world(srv, cat, tmp_path, *, base_version="1.0.0"):
    """`base-pk` (brazo-test-01) y `dep-pk` (brazo-test-02, requiere base-pk@^1.0.0) servidos en un índice."""
    add_arm(cat)
    mesh, meta = arm_piece("brazo-test-02", r=5.2)
    cat.add_piece(mesh, meta)
    zb, bb = make_pkg(cat, tmp_path, "base-pk", "brazo-test-01", base_version)
    zd, bd = make_pkg(cat, tmp_path, "dep-pk", "brazo-test-02", requiere=["base-pk@^1.0.0"])
    srv.routes["/zips/base.zip"], srv.routes["/zips/dep.zip"] = (200, {}, bb), (200, {}, bd)
    eb = entry_dict(srv.base, bb, pid="base-pk", folder="owner@base-pk", nombre="Base", version=base_version)
    eb["latest"] = release(srv.base, bb, version=base_version, path="/zips/base.zip")
    ed = entry_dict(srv.base, bd, pid="dep-pk", folder="owner@dep-pk", nombre="Dep", requiere=["base-pk@^1.0.0"])
    ed["latest"] = release(srv.base, bd, path="/zips/dep.zip")
    srv.routes["/data/index.json"] = (200, {}, index_body(eb, ed))
    return eb, ed


def fetched(srv, tmp_path):
    return R.fetch_index(srv.base, policy=POLICY, cache=str(tmp_path / "cache"), retries=1, sleep=lambda s: None)


def chain_for(srv, tmp_path, root, pid="dep-pk"):
    i = fetched(srv, tmp_path)
    dep = D.resolve([i], root, want(i, pid))
    assert dep.ok, [str(x) for x in dep.errors]
    return dep, D.plan_chain(dep, policy=POLICY, cache=str(tmp_path / "cache"))


def test_cadena_instala_la_dependencia_y_despues_el_paquete(srv, cat, dest, tmp_path):
    publish_world(srv, cat, tmp_path)
    dep, chain = chain_for(srv, tmp_path, dest.root)
    assert ids(dep) == ["base-pk", "dep-pk"] and chain.ok and not chain.nada_que_hacer
    assert any("brazo-test-01@1" in l for l in chain.describe()) and not any("no está instalado" in l for l in chain.describe())   # el aviso de requiere se quita
    results = D.install_chain(chain)
    assert [r.paquete.id for r in results] == ["base-pk", "dep-pk"]
    c = Catalog.open(dest.root)
    assert sorted(m.ref for m in c.list(all_versions=True)) == ["brazo-test-01@1", "brazo-test-02@1"] and c.verify().ok
    assert set(I.load_registry(dest.root)["paquetes"]) == {"base-pk", "dep-pk"}


def test_cadena_en_una_carpeta_nueva_crea_la_linea_una_vez(srv, cat, tmp_path):
    publish_world(srv, cat, tmp_path)
    root = str(tmp_path / "nueva" / "linea")
    _dep, chain = chain_for(srv, tmp_path, root)
    D.install_chain(chain)
    c = Catalog.open(root)
    assert c.name == "proceres-130" and len(c.list(all_versions=True)) == 2 and c.verify().ok


def test_un_fallo_en_el_segundo_paso_deja_el_catalogo_como_estaba(srv, cat, dest, tmp_path, monkeypatch):
    publish_world(srv, cat, tmp_path)
    _dep, chain = chain_for(srv, tmp_path, dest.root)
    before = I.tree_digest(dest.root)
    real, calls = I._dump, []

    def dump(obj):
        calls.append(1)
        if len(calls) == 2:                                    # el registro del 2.º paquete: ya movió sus archivos
            raise OSError("disco lleno")
        return real(obj)

    monkeypatch.setattr(I, "_dump", dump)
    with pytest.raises(OSError):
        D.install_chain(chain)
    assert len(calls) == 2 and I.tree_digest(dest.root) == before        # ni los archivos, ni el registro, ni CATALOGO.md del 1.er paquete quedaron


def test_un_fallo_con_la_linea_nueva_borra_la_carpeta_que_se_creo(srv, cat, tmp_path, monkeypatch):
    publish_world(srv, cat, tmp_path)
    root = str(tmp_path / "nueva" / "linea")
    _dep, chain = chain_for(srv, tmp_path, root)
    real, calls = I._dump, []

    def dump(obj):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disco lleno")
        return real(obj)

    monkeypatch.setattr(I, "_dump", dump)
    with pytest.raises(OSError):
        D.install_chain(chain)
    assert not os.path.exists(root)


def test_el_zip_que_no_es_lo_que_dijo_el_indice_frena_toda_la_cadena(srv, cat, dest, tmp_path):
    eb, _ed = publish_world(srv, cat, tmp_path)
    srv.routes["/zips/base.zip"] = (200, {}, b"otra cosa")                 # el índice dice otro sha256
    i = fetched(srv, tmp_path)
    dep = D.resolve([i], dest.root, want(i, "dep-pk"))
    before = I.tree_digest(dest.root)
    with pytest.raises(R.HashMismatch):
        D.plan_chain(dep, policy=POLICY, cache=str(tmp_path / "cache"), retries=1, sleep=lambda s: None)
    assert I.tree_digest(dest.root) == before


def test_cadena_con_errores_no_se_planifica(srv, cat, dest, tmp_path):
    publish_world(srv, cat, tmp_path)
    i = fetched(srv, tmp_path)
    i.find("base-pk").retiradas.append("1.0.0")
    dep = D.resolve([i], dest.root, want(i, "dep-pk"))
    assert not dep.ok
    with pytest.raises(I.InstallError):
        D.plan_chain(dep, policy=POLICY, cache=str(tmp_path / "cache"))


def test_reinstalar_lo_que_ya_esta_no_hace_nada(srv, cat, dest, tmp_path):
    publish_world(srv, cat, tmp_path)
    D.install_chain(chain_for(srv, tmp_path, dest.root)[1])
    _dep, again = chain_for(srv, tmp_path, dest.root)
    assert again.nada_que_hacer and D.install_chain(again) == []


# ------------------------------------------------------------------------------------------------------------------------ CLI

def run(*a):
    return main(["catalogo", "remoto", *a])


def test_cli_instalar_trae_la_dependencia_con_una_sola_confirmacion(srv, cat, dest, tmp_path, capsys, monkeypatch):
    publish_world(srv, cat, tmp_path)
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or "s")
    assert run("instalar", "dep-pk", "--en", dest.root, "--indice", srv.base) == 0
    out = capsys.readouterr().out
    assert "necesita 1 paquete(s) más" in out and "1. owner@base-pk" in out and "2. owner@dep-pk" in out and len(asked) == 1
    assert set(I.load_registry(dest.root)["paquetes"]) == {"base-pk", "dep-pk"}


def test_cli_cancelar_o_simular_no_escribe_nada(srv, cat, dest, tmp_path, capsys, monkeypatch):
    publish_world(srv, cat, tmp_path)
    before = I.tree_digest(dest.root)
    assert run("instalar", "dep-pk", "--en", dest.root, "--simular", "--indice", srv.base) == 0
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert run("instalar", "dep-pk", "--en", dest.root, "--indice", srv.base) == 1
    assert I.tree_digest(dest.root) == before


def test_cli_dependencia_imposible_no_instala_nada(srv, cat, dest, tmp_path, capsys):
    eb, _ed = publish_world(srv, cat, tmp_path)
    i = json.loads(srv.routes["/data/index.json"][2])
    i["paquetes"][0]["retiradas"] = ["1.0.0"]
    srv.routes["/data/index.json"] = (200, {}, json.dumps(i).encode())
    before = I.tree_digest(dest.root)
    assert run("instalar", "dep-pk", "--en", dest.root, "--si", "--indice", srv.base) == 1
    out = capsys.readouterr().out
    assert "retirada" in out and "No se instaló nada" in out and I.tree_digest(dest.root) == before


def test_cli_sin_dependencias_instala_solo_lo_pedido_y_avisa(srv, cat, dest, tmp_path, capsys):
    publish_world(srv, cat, tmp_path)
    assert run("instalar", "dep-pk", "--en", dest.root, "--si", "--sin-dependencias", "--indice", srv.base) == 0
    out = capsys.readouterr().out
    assert "requiere «base-pk@^1.0.0» y no está instalado" in out
    assert set(I.load_registry(dest.root)["paquetes"]) == {"dep-pk"}


def test_cli_instalar_bloqueado_es_error_de_uso(srv, cat, dest, tmp_path, capsys):
    publish_world(srv, cat, tmp_path)
    i = json.loads(srv.routes["/data/index.json"][2])
    i["bloqueados"] = [{"id": "dep-pk", "motivo": "reclamo", "fecha": "2026-11-25"}]
    srv.routes["/data/index.json"] = (200, {}, json.dumps(i).encode())
    assert run("instalar", "dep-pk", "--en", dest.root, "--si", "--permitir-retirada", "--indice", srv.base) == 2
    assert "bloqueado por id «dep-pk» (reclamo)" in capsys.readouterr().err
    assert run("buscar", "--indice", srv.base) == 0
    assert "dep-pk" not in capsys.readouterr().out


def test_cli_auditar_y_actualizar_avisan_de_lo_retirado(srv, cat, dest, tmp_path, capsys):
    publish_world(srv, cat, tmp_path)
    assert run("instalar", "dep-pk", "--en", dest.root, "--si", "--indice", srv.base) == 0
    capsys.readouterr()
    assert run("auditar", "--en", dest.root, "--indice", srv.base) == 0
    assert "Nada que avisar" in capsys.readouterr().out
    i = json.loads(srv.routes["/data/index.json"][2])
    i["paquetes"][0]["retiradas"] = ["1.0.0"]
    i["bloqueados"] = [{"id": "dep-pk", "motivo": "reclamo"}]
    srv.routes["/data/index.json"] = (200, {}, json.dumps(i).encode())
    before = I.tree_digest(dest.root)
    assert run("auditar", "--en", dest.root, "--indice", srv.base) == 1
    out = capsys.readouterr().out
    assert "RETIRADA base-pk 1.0.0" in out and "BLOQUEADO dep-pk 1.0.0" in out and "No se borró nada" in out
    assert run("actualizar", "--en", dest.root, "--si", "--indice", srv.base) == 0
    assert "AVISO: BLOQUEADO dep-pk" in capsys.readouterr().out
    assert I.tree_digest(dest.root) == before


def test_cli_actualizar_es_aditivo_y_trae_la_dependencia_nueva_de_la_version_nueva(srv, cat, dest, tmp_path, capsys):
    """Instalar 1.0.0 y luego 1.1.0 (que ahora requiere otro paquete) con una receta ya armada: la receta y lo que usa no se tocan."""
    add_arm(cat)
    mesh, meta = arm_piece("brazo-test-02", r=5.2)
    cat.add_piece(mesh, meta)
    mesh, meta = arm_piece("brazo-test-03", r=5.4)
    cat.add_piece(mesh, meta)
    z1, b1 = make_pkg(cat, tmp_path, "heroe-pk", "brazo-test-01", "1.0.0")
    z2, b2 = make_pkg(cat, tmp_path, "heroe-pk", "brazo-test-02", "1.1.0", requiere=["extra-pk@^1.0.0"])
    ze, be = make_pkg(cat, tmp_path, "extra-pk", "brazo-test-03", "1.0.0")
    for name, body in (("h1", b1), ("h2", b2), ("ex", be)):
        srv.routes[f"/zips/{name}.zip"] = (200, {}, body)

    def entry(pid, body, name, version, **over):
        d = entry_dict(srv.base, body, pid=pid, folder=f"owner@{pid}", **over)
        d["latest"] = release(srv.base, body, version=version, path=f"/zips/{name}.zip")
        return d

    srv.routes["/data/index.json"] = (200, {}, index_body(entry("heroe-pk", b1, "h1", "1.0.0")))
    assert run("instalar", "heroe-pk", "--en", dest.root, "--si", "--indice", srv.base) == 0
    add_recipe(dest, "guemes", {"brazo": "brazo-test-01@1"})
    before = {k: v for k, v in I.tree_digest(dest.root).items() if k != I.REGISTRY_FILE and k != "CATALOGO.md"}
    srv.routes["/data/index.json"] = (200, {}, index_body(entry("heroe-pk", b2, "h2", "1.1.0", requiere=["extra-pk@^1.0.0"]), entry("extra-pk", be, "ex", "1.0.0")))
    capsys.readouterr()
    assert run("actualizar", "--en", dest.root, "--si", "--indice", srv.base) == 0
    out = capsys.readouterr().out
    assert "heroe-pk: 1.0.0 → 1.1.0" in out and "necesita 1 paquete(s) más" in out
    reg = I.load_registry(dest.root)["paquetes"]
    assert reg["heroe-pk"]["version"] == "1.1.0" and reg["extra-pk"]["version"] == "1.0.0"
    after = I.tree_digest(dest.root)
    assert all(after.get(k) == v for k, v in before.items())                                   # nada de lo anterior cambió (la receta incluida)
    assert {"brazo-test-01@1", "brazo-test-02@1", "brazo-test-03@1"} <= {m.ref for m in Catalog.open(dest.root).list(all_versions=True)}
    assert Catalog.open(dest.root).verify().ok
