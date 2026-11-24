"""D03 · `afm catalogo exportar`: armar el paquete .zip afm-paquete-1 desde un catálogo (PLAN_catalogos_online.md)."""
import hashlib
import json
import os
import zipfile

import pytest

from afm import exportar as X
from afm import parts as P
from afm import paquete as Q
from afm.catalog import Catalog
from afm.cli import main
from afm.palette import Palette, Role
from test_catalog import arm_piece

IDENT = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
OWNER = dict(autores=["Ana Pérez:owner"], licencia="CC-BY-4.0", origen="propia")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def cat(tmp_path):
    return Catalog.create(str(tmp_path / "linea"), "proceres-130", 130.0, 0.4)


def add_arm(cat, pid="brazo-test-01", **kw):
    mesh, meta = arm_piece(pid, **kw)
    saved, _w = cat.add_piece(mesh, meta)
    return saved


def add_torso(cat, pid="torso-test-01"):
    """Torso con sus cinco puertos (las recetas exigen uno)."""
    from afm.ports import port_class, port_from_points
    from test_catalog import BASE
    if cat.versions(pid):
        return cat.meta(pid)
    mesh, meta = arm_piece(pid)
    meta.tipo = "torso"
    meta.puertos = {n: port_from_points(mesh, BASE, port_class(n), cls) for n, (cls, _opt) in P.PORT_SPEC["torso"].items()}
    saved, _w = cat.add_piece(mesh, meta)
    return saved


def add_recipe(cat, name, lugares, **extra):
    lugares = {"torso": add_torso(cat).ref, **lugares}
    d = {"formato": "afm-receta-1", "personaje": name, "linea": cat.name,
         "lugares": {k: lugares.get(k) for k in ("cabeza", "torso", "pelvis", "brazo", "pierna", "mano")},
         "opciones": {"cintura": False, "clic_hombro": False, "clic_cadera": False, "torso_mirror": True}, "ajustes": {}}
    d.update(extra)
    os.makedirs(os.path.join(cat.root, "figuras"), exist_ok=True)
    path = os.path.join(cat.root, "figuras", f"{name}.receta.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return path


def add_colored(cat, pid, mode, payload=b"datos de color"):
    """Brazo cuya ficha declara su color (`rgb` = FBX entero, `roles` = paquete .npz) con un archivo de verdad al lado."""
    mesh, meta = arm_piece(pid)
    folder = cat._dir(meta.tipo)
    os.makedirs(folder, exist_ok=True)
    name = P.roles_name(pid, 1) if mode == "roles" else P.color_name(pid, 1)
    with open(os.path.join(folder, name), "wb") as f:
        f.write(payload)
    meta.color = {"fuente": name, "sha256": sha(payload), "similitud": IDENT}
    if mode == "roles":
        meta.color.update(modo="roles", roles=["rojo"])
    P.write_piece(folder, meta, mesh)
    return meta


def sin_mano(avisos):
    """Los brazos de prueba traen puerto «muneca»: el aviso de la mano que falta no es lo que se prueba en esos casos."""
    return [w for w in avisos if "no se incluyó ninguna mano" not in w]


def names(path):
    with zipfile.ZipFile(path) as z:
        return z.namelist()


def read(path, name):
    with zipfile.ZipFile(path) as z:
        return z.read(name)


def unzip(path, dest):
    with zipfile.ZipFile(path) as z:
        z.extractall(dest)
    return str(dest)


# ---------------------------------------------------------------------------------------------------- una pieza


def test_exporta_una_pieza_y_el_zip_es_un_catalogo_que_verifica(cat, tmp_path):
    m = add_arm(cat)
    res = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-test-01"], **OWNER)
    assert names(res.path)[0] == "paquete.json"
    assert sorted(names(res.path)[1:]) == sorted(["README.md", "LICENSE", "linea.json", f"piezas/brazo/{m.id}@1.pieza.json", f"piezas/brazo/{m.id}@1.stl"])
    p = Q.Paquete.from_json(read(res.path, "paquete.json").decode("utf-8"))
    assert p == res.paquete and p.issues() == []
    assert (p.tipo, p.linea, p.contenido.piezas, p.contenido.paleta) == ("piezas", {"nombre": "proceres-130", "altura_mm": 130.0}, ["brazo-test-01@1"], False)
    assert p.categorias == ["cuerpos"] and p.licencia == "CC-BY-4.0" and p.autores[0].github == "owner"
    assert set(p.archivos) == set(names(res.path)) - {"paquete.json"}
    assert all(sha(read(res.path, k)) == v for k, v in p.archivos.items())
    out = Catalog.open(unzip(res.path, tmp_path / "x")).verify()
    assert out.ok, out.problems
    assert res.tamano == os.path.getsize(res.path) and res.sha256 == P.sha256_file(res.path)


def test_el_zip_es_determinista(cat, tmp_path):
    add_arm(cat)
    a = X.export_package(cat, str(tmp_path / "a.zip"), piezas=["brazo-test-01"], **OWNER)
    for root, _d, files in os.walk(cat.root):          # cambiar las fechas de los archivos no cambia el zip
        for f in files:
            os.utime(os.path.join(root, f), (1_000_000_000, 1_000_000_000))
    b = X.export_package(cat, str(tmp_path / "b.zip"), piezas=["brazo-test-01"], **OWNER)
    assert a.sha256 == b.sha256
    with zipfile.ZipFile(a.path) as z:
        assert {i.date_time for i in z.infolist()} == {X.ZIP_DATE} and {i.external_attr >> 16 for i in z.infolist()} == {0o100644}
        assert not any(i.filename.endswith("/") for i in z.infolist())


def test_lo_derivado_no_viaja(cat, tmp_path):
    add_arm(cat)
    for rel in ("compat.json", "COMPATIBILIDAD.md", "calibracion.json", "CATALOGO.md", ".papelera/lote/x.stl", "caballos/h/h.stl", "figuras/z.proyecto"):
        os.makedirs(os.path.dirname(os.path.join(cat.root, rel)), exist_ok=True)
        with open(os.path.join(cat.root, rel), "w") as f:
            f.write("x")
    res = X.export_package(cat, str(tmp_path / "p.zip"), linea_completa=True, **OWNER)
    assert not any(n.startswith((".papelera", "caballos")) or n in ("compat.json", "COMPATIBILIDAD.md", "calibracion.json", "CATALOGO.md")
                   or n.endswith(".proyecto") for n in names(res.path))


def test_pieza_inexistente_y_pedidos_incoherentes(cat, tmp_path):
    add_arm(cat)
    z = str(tmp_path / "p.zip")
    with pytest.raises(X.ExportError, match="no hay ninguna pieza «nada-01»"):
        X.export_package(cat, z, piezas=["nada-01"], **OWNER)
    with pytest.raises(X.ExportError, match="no se eligió qué exportar"):
        X.export_package(cat, z, **OWNER)
    with pytest.raises(X.ExportError, match="no se combina"):
        X.export_package(cat, z, linea_completa=True, piezas=["brazo-test-01"], **OWNER)
    with pytest.raises(X.ExportError, match="terminar en .zip"):
        X.export_package(cat, str(tmp_path / "p.tar"), piezas=["brazo-test-01"], **OWNER)
    assert not os.path.exists(z)


def test_sin_licencia_origen_o_autor_no_se_exporta(cat, tmp_path):
    add_arm(cat)
    z = str(tmp_path / "p.zip")
    with pytest.raises(X.ExportError, match=r"--licencia, --origen, --autor"):
        X.export_package(cat, z, piezas=["brazo-test-01"])
    with pytest.raises(X.ExportError, match="--licencia"):
        X.export_package(cat, z, piezas=["brazo-test-01"], autores=["Ana"], origen="propia")
    with pytest.raises(X.ExportError, match="CK101"):
        X.export_package(cat, z, piezas=["brazo-test-01"], **{**OWNER, "licencia": "Propietaria"})
    assert not os.path.exists(z) and not os.path.exists(z + ".tmp")


def test_no_pisa_una_salida_existente(cat, tmp_path):
    add_arm(cat)
    z = str(tmp_path / "p.zip")
    X.export_package(cat, z, piezas=["brazo-test-01"], **OWNER)
    with pytest.raises(X.ExportError, match="ya existe"):
        X.export_package(cat, z, piezas=["brazo-test-01"], **OWNER)
    X.export_package(cat, z, piezas=["brazo-test-01"], overwrite=True, **OWNER)


def test_ficha_alterada_o_stl_perdido_frenan_la_exportacion(cat, tmp_path):
    m = add_arm(cat)
    stl = os.path.join(cat._dir("brazo"), f"{m.id}@1.stl")
    with open(stl, "ab") as f:
        f.write(b" ")
    with pytest.raises(X.ExportError, match="no coincide con el hash"):
        X.export_package(cat, str(tmp_path / "p.zip"), piezas=[m.id], **OWNER)
    os.remove(stl)
    with pytest.raises(X.ExportError, match="falta el STL"):
        X.export_package(cat, str(tmp_path / "p.zip"), piezas=[m.id], **OWNER)


# ---------------------------------------------------------------------------------------------------- manos y recetas


def test_el_brazo_con_muneca_lleva_su_mano(cat, tmp_path):
    from afm.ports import port_from_points
    from test_catalog import BASE
    add_arm(cat)
    res = X.export_package(cat, str(tmp_path / "a.zip"), piezas=["brazo-test-01"], **OWNER)
    assert any("no se incluyó ninguna mano" in w and "mano-test-01" in w for w in res.avisos)
    mesh, meta = arm_piece("mano-test-01")
    meta.tipo = "mano"
    meta.puertos = {"muneca": port_from_points(mesh, BASE, "muneca", "enchufe")}
    cat.add_piece(mesh, meta)
    res = X.export_package(cat, str(tmp_path / "b.zip"), piezas=["brazo-test-01"], **OWNER)
    assert res.paquete.contenido.piezas == ["brazo-test-01@1", "mano-test-01@1"]
    assert any("se agregó la mano «mano-test-01@1»" in w for w in res.avisos)


def test_receta_arrastra_sus_piezas_y_se_copia_tal_cual(cat, tmp_path):
    add_arm(cat, "brazo-a-01")
    add_arm(cat, "brazo-b-01")
    path = add_recipe(cat, "guemes_color", {"brazo": "brazo-a-01@1"})
    res = X.export_package(cat, str(tmp_path / "p.zip"), recetas=["guemes_color"], **OWNER)
    p = res.paquete
    assert (p.tipo, p.id, p.categorias, p.contenido.recetas, p.contenido.piezas) == ("heroe", "proceres-130-guemes-color", ["heroes"], ["guemes_color"], ["brazo-a-01@1", "torso-test-01@1"])
    assert read(res.path, "figuras/guemes_color.receta.json") == open(path, "rb").read()
    assert sin_mano(res.avisos) == [] and p.issues() == []
    assert Catalog.open(unzip(res.path, tmp_path / "x")).verify().ok


def test_receta_con_id_sin_version_se_fija_y_sin_caballo(cat, tmp_path):
    add_arm(cat, "brazo-a-01")
    mesh, meta = arm_piece("brazo-a-01")
    cat.add_piece(mesh, meta, new_version=True)                 # ahora hay @1 y @2
    add_recipe(cat, "heroe", {"brazo": "brazo-a-01"}, cabalgadura={"caballo": "h@1", "sha256": "0" * 64, "asiento": [0, 0, 0]}, accesorios=[])
    res = X.export_package(cat, str(tmp_path / "p.zip"), recetas=["heroe.receta.json"], **OWNER)
    rec = json.loads(read(res.path, "figuras/heroe.receta.json"))
    assert rec["lugares"]["brazo"] == "brazo-a-01@2" and "cabalgadura" not in rec
    assert any("«brazo» usaba «brazo-a-01» sin versión" in w for w in res.avisos)
    assert any("se quitó la cabalgadura" in w for w in res.avisos)
    assert res.paquete.contenido.piezas == ["brazo-a-01@2", "torso-test-01@1"]
    assert res.paquete.archivos["figuras/heroe.receta.json"] == sha(read(res.path, "figuras/heroe.receta.json"))


def test_receta_inexistente_o_con_pieza_perdida(cat, tmp_path):
    add_arm(cat, "brazo-a-01")
    add_recipe(cat, "rota", {"brazo": "brazo-zzz-01@1"})
    with pytest.raises(X.ExportError, match=r"no hay ninguna receta «nada» en «proceres-130» \(hay: rota\)"):
        X.export_package(cat, str(tmp_path / "p.zip"), recetas=["nada"], **OWNER)
    with pytest.raises(X.ExportError, match="receta «rota», lugar «brazo»: no hay ninguna pieza «brazo-zzz-01@1»"):
        X.export_package(cat, str(tmp_path / "p.zip"), recetas=["rota"], **OWNER)


def test_linea_completa_omite_la_receta_rota_con_aviso(cat, tmp_path):
    add_arm(cat, "brazo-a-01")
    mesh, meta = arm_piece("brazo-a-01")
    cat.add_piece(mesh, meta, new_version=True)
    add_recipe(cat, "buena", {"brazo": "brazo-a-01@2"})
    add_recipe(cat, "rota", {"brazo": "brazo-zzz-01@1"})
    res = X.export_package(cat, str(tmp_path / "p.zip"), linea_completa=True, **OWNER)
    p = res.paquete
    assert (p.tipo, p.id, p.categorias, p.contenido.recetas, p.contenido.piezas) == ("linea", "proceres-130", ["lineas"], ["buena"], ["brazo-a-01@1", "brazo-a-01@2", "torso-test-01@1"])
    assert any("se omite la receta «rota»" in w for w in res.avisos)


# ---------------------------------------------------------------------------------------------------- color


def test_pieza_rgb_sin_fbx_sale_sin_color_y_verifica(cat, tmp_path):
    add_colored(cat, "brazo-rgb-01", "rgb")
    res = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-rgb-01"], **OWNER)
    assert not any(n.endswith(".fbx") for n in names(res.path))
    assert json.loads(read(res.path, "piezas/brazo/brazo-rgb-01@1.pieza.json")).get("color") in (None, {})
    assert any("brazo-rgb-01@1: se exporta sin color" in w for w in res.avisos)
    assert Catalog.open(unzip(res.path, tmp_path / "x")).verify().ok
    assert cat.meta("brazo-rgb-01").color                       # el catálogo de origen no se toca


def test_incluir_fbx_lo_lleva_y_respeta_el_tope(cat, tmp_path, monkeypatch):
    add_colored(cat, "brazo-rgb-01", "rgb", b"FBX" * 10)
    res = X.export_package(cat, str(tmp_path / "a.zip"), piezas=["brazo-rgb-01"], incluir_fbx=True, **OWNER)
    assert "piezas/brazo/brazo-rgb-01@1.fbx" in names(res.path) and sin_mano(res.avisos) == []
    assert Catalog.open(unzip(res.path, tmp_path / "x")).verify().ok
    monkeypatch.setattr(X, "FBX_MAX_BYTES", 5)
    res = X.export_package(cat, str(tmp_path / "b.zip"), piezas=["brazo-rgb-01"], incluir_fbx=True, **OWNER)
    assert not any(n.endswith(".fbx") for n in names(res.path)) and any("el tope es" in w for w in res.avisos)


def test_fbx_perdido_o_alterado(cat, tmp_path):
    add_colored(cat, "brazo-rgb-01", "rgb", b"FBX")
    fbx = os.path.join(cat._dir("brazo"), "brazo-rgb-01@1.fbx")
    with open(fbx, "ab") as f:
        f.write(b"!")
    with pytest.raises(X.ExportError, match="no coincide con el hash"):
        X.export_package(cat, str(tmp_path / "a.zip"), piezas=["brazo-rgb-01"], incluir_fbx=True, **OWNER)
    os.remove(fbx)
    res = X.export_package(cat, str(tmp_path / "b.zip"), piezas=["brazo-rgb-01"], incluir_fbx=True, **OWNER)
    assert any("falta el FBX" in w for w in res.avisos)


def test_pieza_por_roles_lleva_su_paquete_la_paleta_y_el_retoque(cat, tmp_path):
    cat.set_palette(Palette([Role("rojo", "Rojo", "#ff0000")]))
    meta = add_colored(cat, "brazo-roles-01", "roles", b"NPZ")
    folder = cat._dir("brazo")
    rname = P.retouch_name(meta.id, 1)
    with open(os.path.join(folder, rname), "wb") as f:
        f.write(b"{}")
    meta.color["retoque"] = {"fuente": rname, "sha256": sha(b"{}"), "n": 0, "roles": []}
    with open(os.path.join(folder, P.meta_name(meta.id, 1)), "w") as f:
        json.dump(meta.to_dict(), f, indent=2, ensure_ascii=False)
    res = X.export_package(cat, str(tmp_path / "p.zip"), piezas=[meta.id], **OWNER)
    assert {"paleta.json", "piezas/brazo/brazo-roles-01@1.color.npz", "piezas/brazo/brazo-roles-01@1.retoque.json"} <= set(names(res.path))
    assert res.paquete.contenido.paleta is True and sin_mano(res.avisos) == []
    assert read(res.path, "paleta.json") == open(cat.palette_path, "rb").read()
    with open(os.path.join(folder, "brazo-roles-01@1.color.npz"), "ab") as f:
        f.write(b"!")
    with pytest.raises(X.ExportError):                            # el paquete de roles alterado frena
        X.export_package(cat, str(tmp_path / "q.zip"), piezas=[meta.id], **OWNER)


def test_la_paleta_no_viaja_si_nadie_la_usa(cat, tmp_path):
    cat.set_palette(Palette([Role("rojo", "Rojo", "#ff0000")]))
    add_arm(cat)
    res = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-test-01"], **OWNER)
    assert "paleta.json" not in names(res.path) and res.paquete.contenido.paleta is False
    res = X.export_package(cat, str(tmp_path / "l.zip"), linea_completa=True, **OWNER)
    assert "paleta.json" in names(res.path) and res.paquete.contenido.paleta is True


# ---------------------------------------------------------------------------------------------------- manifiesto, textos y portada


def test_manifiesto_base_y_argumentos_que_lo_pisan(cat, tmp_path):
    add_arm(cat)
    base = Q.Paquete(id="mi-brazo", nombre="Mi brazo", version="2.1.0", descripcion="Un brazo.", tipo="piezas",
                     autores=[Q.Author("Ana", "owner")], licencia="MIT", origen="derivada", categorias=["manos"], etiquetas=["x"],
                     requiere=["otro@^1"], conflicts=["malo"], experimental=True)
    res = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-test-01"], base=base, version="2.2.0", etiquetas=["y"])
    p = res.paquete
    assert (p.id, p.nombre, p.version, p.licencia, p.origen, p.categorias, p.etiquetas) == ("mi-brazo", "Mi brazo", "2.2.0", "MIT", "derivada", ["manos"], ["y"])
    assert p.requiere == ["otro@^1"] and p.conflicts == ["malo"] and p.experimental is True
    assert p.contenido.piezas == ["brazo-test-01@1"] and p.linea["nombre"] == "proceres-130"


def test_readme_licencia_creditos_y_portada(cat, tmp_path):
    from PIL import Image
    add_arm(cat)
    (tmp_path / "R.md").write_text("# Mío\n", encoding="utf-8")
    (tmp_path / "L.txt").write_text("Texto completo de la licencia\n", encoding="utf-8")
    (tmp_path / "C.md").write_text("Créditos\n", encoding="utf-8")
    Image.new("RGB", (1280, 720), (10, 20, 30)).save(tmp_path / "c.jpg")
    res = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-test-01"], readme=str(tmp_path / "R.md"), license_file=str(tmp_path / "L.txt"),
                           creditos=str(tmp_path / "C.md"), portada=str(tmp_path / "c.jpg"), **OWNER)
    assert read(res.path, "README.md") == "# Mío\n".encode("utf-8") and read(res.path, "LICENSE").startswith(b"Texto completo")
    assert read(res.path, "CREDITOS.md") == "Créditos\n".encode("utf-8") and read(res.path, "previews/portada.png")[:8] == b"\x89PNG\r\n\x1a\n"
    assert sin_mano(res.avisos) == [] and "previews/portada.png" in res.paquete.archivos
    Image.new("RGB", (100, 50)).save(tmp_path / "chica.png")
    res = X.export_package(cat, str(tmp_path / "q.zip"), piezas=["brazo-test-01"], portada=str(tmp_path / "chica.png"), **OWNER)
    assert any("100×50" in w for w in res.avisos)
    (tmp_path / "no.png").write_text("no soy imagen")
    with pytest.raises(X.ExportError, match="no es una imagen legible"):
        X.export_package(cat, str(tmp_path / "r.zip"), piezas=["brazo-test-01"], portada=str(tmp_path / "no.png"), **OWNER)
    with pytest.raises(X.ExportError, match="no se puede leer"):
        X.export_package(cat, str(tmp_path / "r.zip"), piezas=["brazo-test-01"], readme=str(tmp_path / "falta.md"), **OWNER)


def test_textos_por_defecto():
    p = Q.Paquete.from_dict({"formato": "afm-paquete-1", "id": "x", "nombre": "X", "version": "1.0.0", "descripcion": "Desc.", "tipo": "piezas",
                             "autores": [{"nombre": "Ana", "github": "owner"}], "licencia": "CC0-1.0", "origen": "propia",
                             "linea": {"nombre": "l", "altura_mm": 127}, "contenido": {"piezas": ["a-01@1"], "recetas": ["r"], "paleta": True}})
    t = X.default_readme(p)
    assert t.startswith("# X\n\nDesc.") and "`a-01@1`" in t and "@owner" in t and "Paleta de color: sí" in t
    assert "https://spdx.org/licenses/CC0-1.0.html" in X.default_license_text("CC0-1.0")
    assert "derechos reservados" in X.default_license_text(Q.ALL_RIGHTS_RESERVED)


def test_slug_y_autor():
    assert X.slug("Proceres 127_Güemes") == "proceres-127-guemes" and X.slug("  --A--  ") == "a" and len(X.slug("a" * 100)) == 64
    assert X.parse_author("Ana Pérez:owner") == Q.Author("Ana Pérez", "owner") and X.parse_author("Ana") == Q.Author("Ana")
    assert X.default_categories("piezas", []) == ["otros"]


# ---------------------------------------------------------------------------------------------------- CLI


def test_cli_exportar(cat, tmp_path, capsys):
    add_arm(cat)
    z = str(tmp_path / "p.zip")
    ok = ["--autor", "Ana:owner", "--licencia", "CC-BY-4.0", "--origen", "propia"]
    assert main(["catalogo", "exportar", cat.root, "--piezas", "brazo-test-01", "-o", z, *ok]) == 0
    out = capsys.readouterr().out
    assert os.path.isfile(z) and "Paquete «proceres-130-brazo-test-01»" in out and "1 pieza(s), 0 receta(s)" in out and "AVISO:" in out
    assert main(["catalogo", "exportar", cat.root, "--piezas", "brazo-test-01", "-o", z, *ok]) == 2
    assert "ya existe" in capsys.readouterr().err
    assert main(["catalogo", "exportar", cat.root, "--piezas", "brazo-test-01", "-o", str(tmp_path / "q.zip")]) == 2
    assert "faltan datos que solo el autor puede dar" in capsys.readouterr().err
    with pytest.raises(SystemExit):                                      # --piezas, --receta y --linea-completa son excluyentes
        main(["catalogo", "exportar", cat.root, "--piezas", "a", "--linea-completa", "-o", z])
    capsys.readouterr()


def test_cli_manifiesto_y_listas(cat, tmp_path, capsys):
    add_arm(cat, "brazo-a-01")
    add_arm(cat, "brazo-b-01")
    base = Q.Paquete(id="dos", nombre="Dos", version="1.0.0", descripcion="D.", tipo="piezas", autores=[Q.Author("Ana")], licencia="MIT", origen="propia", categorias=["cuerpos"])
    (tmp_path / "paquete.json").write_text(base.to_json(), encoding="utf-8")
    z = str(tmp_path / "p.zip")
    assert main(["catalogo", "exportar", cat.root, "--piezas", "brazo-a-01,brazo-b-01", "-o", z, "--manifiesto", str(tmp_path / "paquete.json")]) == 0
    capsys.readouterr()
    assert Q.Paquete.from_json(read(z, "paquete.json").decode("utf-8")).contenido.piezas == ["brazo-a-01@1", "brazo-b-01@1"]
    assert main(["catalogo", "exportar", cat.root, "--piezas", "brazo-a-01", "-o", str(tmp_path / "x.zip"), "--manifiesto", str(tmp_path / "no.json")]) == 2
    assert "no se pudo leer" in capsys.readouterr().err
