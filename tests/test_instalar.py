"""D05 · `instalar.py` (plan + aplicación atómica) y D06 · `afm catalogo instalar` (PLAN_catalogos_online.md §5.3, §5.4)."""
import json
import os
import zipfile

import pytest

from afm import exportar as X
from afm import instalar as I
from afm import parts as P
from afm import validar as V
from afm.catalog import Catalog
from afm.cli import main
from afm.palette import Palette, Role
from test_catalog import arm_piece
from test_exportar import OWNER, add_arm, add_colored, add_recipe, cat  # noqa: F401  (cat es una fixture)


@pytest.fixture
def dest(tmp_path):
    """Catálogo de destino vacío de la misma línea que `cat`."""
    return Catalog.create(str(tmp_path / "destino"), "proceres-130", 130.0, 0.4)


def export(cat, tmp_path, name="p.zip", **kw):
    kw = {**OWNER, **kw}
    if not any(k in kw for k in ("piezas", "recetas", "linea_completa")):
        kw["piezas"] = ["brazo-test-01"]
    return X.export_package(cat, str(tmp_path / name), **kw).path


def setpal(c, **roles):
    c.set_palette(Palette([Role(k, k, v) for k, v in roles.items()]))


def piezas(c):
    return sorted(m.ref for m in c.list(all_versions=True))


def install(zip_path, root, **kw):
    plan = I.plan_install(zip_path, str(root), **kw)
    assert plan.ok, [str(i) for i in plan.errors]
    return plan, I.apply(plan)


# ---------------------------------------------------------------------------------------------------- plan y aplicación


def test_el_plan_no_escribe_nada(cat, dest, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    before = I.tree_digest(dest.root)
    plan = I.plan_install(z, dest.root)
    assert plan.ok and [i.name for i in plan.a_instalar] == ["brazo-test-01@1"] and not plan.como_linea
    assert I.tree_digest(dest.root) == before
    assert plan.describe()[0].startswith("Paquete «") and any("brazo-test-01@1" in l for l in plan.describe())


def test_instala_en_un_catalogo_de_la_misma_linea(cat, dest, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    _plan, res = install(z, dest.root)
    assert res.piezas == ["brazo-test-01@1"] and piezas(Catalog.open(dest.root)) == ["brazo-test-01@1"]
    assert Catalog.open(dest.root).verify().ok
    reg = I.load_registry(dest.root)["paquetes"][res.paquete.id]
    assert reg["version"] == "1.0.0" and reg["licencia"] == "CC-BY-4.0" and reg["refs"]["piezas"] == ["brazo-test-01@1"] and reg["sha256"] == P.sha256_file(z)
    assert "brazo-test-01@1" in open(os.path.join(dest.root, "CATALOGO.md"), encoding="utf-8").read()
    assert not [n for n in os.listdir(dest.root) if n.startswith(I.STAGING_PREFIX)]


def test_destino_que_no_existe_se_crea_como_linea_nueva(cat, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    nuevo = tmp_path / "otra" / "linea"
    plan, res = install(z, nuevo)
    assert plan.como_linea and Catalog.open(str(nuevo)).name == "proceres-130" and piezas(Catalog.open(str(nuevo))) == ["brazo-test-01@1"]
    assert Catalog.open(str(nuevo)).verify().ok


def test_como_linea_no_pisa_un_catalogo_existente_ni_una_carpeta_con_cosas(cat, dest, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    assert not I.plan_install(z, dest.root, como_linea=True).ok
    ocupada = tmp_path / "ocupada"
    ocupada.mkdir()
    (ocupada / "algo.txt").write_text("x")
    assert not I.plan_install(z, str(ocupada)).ok


def test_mismo_hash_se_omite_y_reinstalar_no_hace_nada(cat, dest, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    install(z, dest.root)
    before = I.tree_digest(dest.root)
    plan = I.plan_install(z, dest.root)
    assert plan.ok and plan.nada_que_hacer and [i.name for i in plan.omitidas] == ["brazo-test-01@1"]
    assert I.tree_digest(dest.root) == before


def test_colision_con_otra_geometria_es_conflicto_y_ofrece_linea_nueva(cat, dest, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    mesh, meta = arm_piece("brazo-test-01", r=6.0)             # mismo id@1, otro STL
    dest.add_piece(mesh, meta)
    plan = I.plan_install(z, dest.root)
    assert not plan.ok and plan.sugerir_linea and "otra geometría" in plan.errors[0].message
    with pytest.raises(I.InstallError):
        I.apply(plan)


def test_altura_distinta_no_entra_en_ese_catalogo(cat, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    alta = Catalog.create(str(tmp_path / "alta"), "proceres-150", 150.0, 0.4)
    plan = I.plan_install(z, alta.root)
    assert not plan.ok and plan.sugerir_linea and "150" in plan.errors[0].message
    assert plan.errors[0].fix.startswith("instalalo como línea nueva")


def test_el_perfil_distinto_es_aviso_y_gana_el_local(cat, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    otro = Catalog.create(str(tmp_path / "otro"), "proceres-130", 130.0, 0.4)
    otro.line["params"]["clearance"] = float(otro.line["params"].get("clearance", 0.2)) + 0.1
    otro._write_line()
    plan = I.plan_install(z, otro.root)
    assert plan.ok and any(i.subject == "perfil" and "clearance" in i.message for i in plan.warnings)


def test_paleta_falta_se_agrega_y_si_el_rol_existe_gana_el_local(cat, dest, tmp_path):
    add_colored(cat, "brazo-test-01", "roles")
    setpal(cat, rojo="#FF0000", azul="#0000FF")
    z = export(cat, tmp_path)
    plan, res = install(z, dest.root)                          # destino sin paleta: llega entera
    assert [r.id for r in plan.roles_nuevos] == ["rojo", "azul"] and Catalog.open(dest.root).palette().ids() == ["rojo", "azul"]
    assert res.roles == ["rojo", "azul"]
    otro = Catalog.create(str(tmp_path / "otro"), "proceres-130", 130.0, 0.4)
    setpal(otro, rojo="#AA0000")
    plan2, _res2 = install(z, otro.root)
    assert [r.id for r in plan2.roles_nuevos] == ["azul"] and any("gana el local" in i.message for i in plan2.warnings)
    pal = Catalog.open(otro.root).palette()
    assert pal.ids() == ["rojo", "azul"] and pal.hex_of("rojo") == "#AA0000"


def test_paleta_faltante_en_el_paquete_es_error(cat, dest, tmp_path):
    add_colored(cat, "brazo-test-01", "roles")
    setpal(cat, rojo="#FF0000")
    z = export(cat, tmp_path)
    with zipfile.ZipFile(z) as src:                            # el mismo paquete sin paleta.json (y sin tocar su manifiesto: lo detecta el lint)
        files = {n: src.read(n) for n in src.namelist() if n != "paleta.json"}
    bad = str(tmp_path / "sinpaleta.zip")
    with zipfile.ZipFile(bad, "w") as out:
        for n, b in files.items():
            out.writestr(n, b)
    assert not I.plan_install(bad, dest.root).ok


def test_recetas_se_instalan_y_se_omiten_si_son_iguales(cat, dest, tmp_path):
    ref = add_arm(cat).ref
    add_recipe(cat, "heroe", {"brazo": ref})
    z = export(cat, tmp_path, piezas=(), recetas=["heroe"])
    _plan, res = install(z, dest.root)
    assert res.recetas == ["heroe"] and os.path.isfile(os.path.join(dest.root, "figuras", "heroe.receta.json"))
    plan2 = I.plan_install(z, dest.root)
    assert plan2.nada_que_hacer and {i.kind for i in plan2.omitidas} == {"pieza", "receta"}
    with open(os.path.join(dest.root, "figuras", "heroe.receta.json"), "a") as f:
        f.write(" ")
    assert not I.plan_install(z, dest.root).ok


# ---------------------------------------------------------------------------------------------------- numeración y archivo extra (D-9)


def test_versiones_con_huecos_y_paquetes_json_en_la_raiz_se_toleran(cat, dest, tmp_path):
    add_arm(cat)
    mesh, meta = arm_piece("brazo-test-01", r=5.5)
    cat.add_piece(mesh, meta, new_version=True)                 # @2
    z = export(cat, tmp_path, piezas=["brazo-test-01@2"])       # el paquete trae el @2 sin el @1
    _plan, res = install(z, dest.root)
    d = Catalog.open(dest.root)
    assert res.piezas == ["brazo-test-01@2"] and d.versions("brazo-test-01") == [2]      # se conserva el número: no se renumera a @1
    rep = d.verify()
    assert rep.ok and any("no consecutivas" in w for w in rep.warnings)                 # el hueco es aviso, no error
    assert os.path.isfile(os.path.join(dest.root, I.REGISTRY_FILE)) and d.list()        # el archivo extra en la raíz no molesta
    mesh3, meta3 = arm_piece("brazo-test-01", r=6.5)
    assert d.add_piece(mesh3, meta3, new_version=True)[0].version == 3                  # y la numeración sigue desde el último
    again = X.export_package(d, str(tmp_path / "re.zip"), linea_completa=True, **OWNER)   # exportar con paquetes.json presente tampoco lo arrastra
    assert I.REGISTRY_FILE not in zipfile.ZipFile(again.path).namelist()


# ---------------------------------------------------------------------------------------------------- registro, origen y dependencias


def test_cambio_de_origen_pide_confirmacion_explicita(cat, dest, tmp_path):
    add_arm(cat)
    install(export(cat, tmp_path, "a.zip", github="ana/uno"), dest.root)
    mesh, meta = arm_piece("brazo-test-02", r=5.2)
    cat.add_piece(mesh, meta)
    z2 = export(cat, tmp_path, "b.zip", piezas=["brazo-test-02"], id=I.peek_package(str(tmp_path / "a.zip")).id, github="otro/repo", version="1.1.0")
    plan = I.plan_install(z2, dest.root)
    assert plan.ok and plan.cambia_origen
    with pytest.raises(I.InstallError):
        I.apply(plan)
    res = I.apply(plan, confirmar_origen=True)
    reg = I.load_registry(dest.root)["paquetes"][res.paquete.id]
    assert reg["github"] == "otro/repo" and reg["version"] == "1.1.0" and reg["refs"]["piezas"] == ["brazo-test-01@1", "brazo-test-02@1"]    # aditivo


def test_conflicts_y_requiere(cat, dest, tmp_path):
    add_arm(cat)
    install(export(cat, tmp_path, "a.zip", id="base"), dest.root)
    # el paquete nuevo declara un conflicto con «base»
    mesh, meta = arm_piece("brazo-test-02", r=5.2)
    cat.add_piece(mesh, meta)
    z = export(cat, tmp_path, "b.zip", piezas=["brazo-test-02"], id="otro")
    with zipfile.ZipFile(z) as src:
        files = {n: src.read(n) for n in src.namelist()}
    m = json.loads(files["paquete.json"])
    m["conflicts"], m["requiere"] = ["base@>=1.0.0"], ["falta-algo"]
    files["paquete.json"] = json.dumps(m).encode()
    c = str(tmp_path / "c.zip")
    with zipfile.ZipFile(c, "w") as out:
        for n, b in files.items():
            out.writestr(n, b)
    plan = I.plan_install(c, dest.root)
    assert not plan.ok and any("choca con «base»" in i.message for i in plan.errors)
    assert any("requiere «falta-algo»" in i.message for i in plan.warnings)


def test_una_coleccion_no_se_instala_desde_un_zip(cat, dest, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    with zipfile.ZipFile(z) as src:
        files = {n: src.read(n) for n in src.namelist()}
    m = json.loads(files["paquete.json"])
    m["tipo"] = "coleccion"
    files["paquete.json"] = json.dumps(m).encode()
    c = str(tmp_path / "col.zip")
    with zipfile.ZipFile(c, "w") as out:
        for n, b in files.items():
            out.writestr(n, b)
    assert not I.plan_install(c, dest.root).ok


# ---------------------------------------------------------------------------------------------------- todo o nada


def _scenario(cat, dest, tmp_path):
    add_colored(cat, "brazo-test-01", "roles")
    setpal(cat, rojo="#FF0000")
    ref = cat.meta("brazo-test-01").ref
    add_recipe(cat, "heroe", {"brazo": ref})
    return export(cat, tmp_path, piezas=(), recetas=["heroe"])


def test_el_zip_que_cambia_entre_el_plan_y_la_aplicacion_se_rechaza(cat, dest, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    plan = I.plan_install(z, dest.root)
    with open(z, "ab") as f:
        f.write(b"\0")
    before = I.tree_digest(dest.root)
    with pytest.raises(I.InstallError, match="cambió"):
        I.apply(plan)
    assert I.tree_digest(dest.root) == before


def test_si_verificar_falla_el_catalogo_no_cambia(cat, dest, tmp_path, monkeypatch):
    add_arm(cat)
    z = export(cat, tmp_path)
    plan = I.plan_install(z, dest.root)
    before = I.tree_digest(dest.root)
    monkeypatch.setattr(Catalog, "verify", lambda self, **kw: type("R", (), {"problems": ["x@1: malla rota"], "warnings": [], "ok": False})())
    with pytest.raises(I.InstallError, match="verificar"):
        I.apply(plan)
    assert I.tree_digest(dest.root) == before


def test_un_fallo_en_cualquier_paso_deja_el_arbol_identico(cat, dest, tmp_path, monkeypatch):
    z = _scenario(cat, dest, tmp_path)
    real = os.replace
    calls = {"n": 0, "fail_at": None}

    def counting(src, dst, *a, **k):
        calls["n"] += 1
        if calls["fail_at"] == calls["n"]:
            raise OSError("disco lleno (simulado)")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", counting)
    plan = I.plan_install(z, dest.root)
    assert plan.ok
    # recorrido en seco sobre una copia del destino para contar los pasos que usan os.replace
    total = _count_replaces(cat, tmp_path, z, counting, calls)
    assert total >= 6                                          # 3 archivos de pieza + receta + paleta + registro como mínimo
    before = I.tree_digest(dest.root)
    for k in range(1, total + 1):
        calls["n"], calls["fail_at"] = 0, k
        with pytest.raises(OSError, match="simulado"):
            I.apply(plan)
        assert I.tree_digest(dest.root) == before, f"el paso {k} dejó restos"
    calls["fail_at"] = None
    calls["n"] = 0
    I.apply(plan)                                              # y sin fallos se instala completo
    assert Catalog.open(dest.root).verify().ok


def _count_replaces(cat, tmp_path, z, counting, calls):
    probe = Catalog.create(str(tmp_path / "sonda"), "proceres-130", 130.0, 0.4)
    plan = I.plan_install(z, probe.root)
    calls["n"], calls["fail_at"] = 0, None
    I.apply(plan)
    return calls["n"]


def test_un_fallo_al_regenerar_el_indice_o_al_escribir_el_registro_tambien_se_deshace(cat, dest, tmp_path, monkeypatch):
    z = _scenario(cat, dest, tmp_path)
    plan = I.plan_install(z, dest.root)
    before = I.tree_digest(dest.root)
    monkeypatch.setattr(Catalog, "write_index", lambda self: (_ for _ in ()).throw(RuntimeError("índice")))
    with pytest.raises(RuntimeError):
        I.apply(plan)
    assert I.tree_digest(dest.root) == before
    monkeypatch.undo()
    monkeypatch.setattr(I, "_dump", lambda obj: (_ for _ in ()).throw(RuntimeError("registro")))
    with pytest.raises(RuntimeError):
        I.apply(plan)
    assert I.tree_digest(dest.root) == before


def test_si_falla_una_linea_nueva_la_carpeta_creada_desaparece(cat, tmp_path, monkeypatch):
    add_arm(cat)
    z = export(cat, tmp_path)
    nuevo = tmp_path / "nueva"
    plan = I.plan_install(z, str(nuevo))
    monkeypatch.setattr(I, "_dump", lambda obj: (_ for _ in ()).throw(RuntimeError("registro")))
    with pytest.raises(RuntimeError):
        I.apply(plan)
    assert not nuevo.exists()


def test_apply_exige_un_plan_sin_errores(cat, tmp_path):
    add_arm(cat)
    z = export(cat, tmp_path)
    alta = Catalog.create(str(tmp_path / "alta"), "proceres-150", 150.0, 0.4)
    with pytest.raises(I.InstallError, match="errores"):
        I.apply(I.plan_install(z, alta.root))


def test_lo_que_instala_pasa_el_validador_estricto_de_origen(cat, tmp_path):
    """El paquete exportado y el instalado son el mismo contenido: `validar` (D04) y el instalador coinciden sobre lo que es válido."""
    add_arm(cat)
    z = export(cat, tmp_path)
    assert V.validate_package(z).ok and I.plan_install(z, str(tmp_path / "x")).ok


# ---------------------------------------------------------------------------------------------------- D06 · CLI


def test_cli_simular_no_escribe(cat, dest, tmp_path, capsys):
    add_arm(cat)
    z = export(cat, tmp_path)
    before = I.tree_digest(dest.root)
    assert main(["catalogo", "instalar", z, "--en", dest.root, "--simular"]) == 0
    out = capsys.readouterr().out
    assert "brazo-test-01@1" in out and "Simulación" in out and I.tree_digest(dest.root) == before


def test_cli_instala_y_listar_muestra_las_piezas(cat, dest, tmp_path, capsys):
    add_arm(cat)
    z = export(cat, tmp_path)
    assert main(["catalogo", "instalar", z, "--en", dest.root, "--si"]) == 0
    assert "Instalado" in capsys.readouterr().out
    assert main(["catalogo", "listar", dest.root]) == 0
    assert "brazo-test-01" in capsys.readouterr().out
    assert main(["catalogo", "verificar", dest.root]) == 0
    assert main(["catalogo", "instalar", z, "--en", dest.root, "--si"]) == 0          # segunda vez: nada que hacer
    assert "Nada que instalar" in capsys.readouterr().out


def test_cli_cancelar_no_escribe(cat, dest, tmp_path, capsys, monkeypatch):
    add_arm(cat)
    z = export(cat, tmp_path)
    before = I.tree_digest(dest.root)
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    assert main(["catalogo", "instalar", z, "--en", dest.root]) == 1
    assert "Cancelado" in capsys.readouterr().out and I.tree_digest(dest.root) == before


def test_cli_error_sugiere_linea_nueva_y_sin_en_usa_la_ruta_explicita(cat, tmp_path, capsys, monkeypatch):
    add_arm(cat)
    z = export(cat, tmp_path)
    alta = Catalog.create(str(tmp_path / "alta"), "proceres-150", 150.0, 0.4)
    assert main(["catalogo", "instalar", z, "--en", alta.root, "--si"]) == 1
    assert "--como-linea" in capsys.readouterr().out
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("AFM_CATALOGO", str(tmp_path / "ignorada"))                    # D-4: se avisa y se ignora
    assert main(["catalogo", "instalar", z, "--si"]) == 0
    out = capsys.readouterr().out
    assert "AFM_CATALOGO" in out and os.path.isfile(tmp_path / "xdg" / "afm" / "catalogos" / "proceres-130" / "linea.json")
    assert not (tmp_path / "ignorada").exists()


def test_cli_zip_roto_o_sin_manifiesto(tmp_path, capsys):
    mal = tmp_path / "mal.zip"
    mal.write_bytes(b"no soy un zip")
    assert main(["catalogo", "instalar", str(mal), "--en", str(tmp_path / "d")]) == 2
    vacio = tmp_path / "vacio.zip"
    with zipfile.ZipFile(vacio, "w") as z:
        z.writestr("README.md", "x")
    assert main(["catalogo", "instalar", str(vacio), "--en", str(tmp_path / "d")]) == 2
    assert "paquete.json" in capsys.readouterr().err
