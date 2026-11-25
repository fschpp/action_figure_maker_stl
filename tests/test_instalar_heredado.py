"""D07 · importar `.zip` heredados (catálogo comprimido a mano, sin `paquete.json`) — PLAN_catalogos_online.md D07, §5.7."""
import json
import os
import zipfile

import pytest

from afm import instalar as I
from afm import parts as P
from afm import validar as V
from afm.catalog import Catalog
from afm.cli import main
from test_exportar import add_arm, add_colored, add_recipe, cat  # noqa: F401  (cat es una fixture)

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")
INFO = dict(licencia="CC-BY-4.0", origen="propia", autores=["Ana Pérez:owner"])
JUNK = {"compat.json": b"{}", "COMPATIBILIDAD.md": b"# x", "calibracion.json": b"{}",       # (CATALOGO.md ya viene en el ejemplo)
        ".papelera/lote/x.stl": b"x", "caballos/h/h.stl": b"x", "figuras/z.proyecto": b"x", "notas.txt": b"hola"}


def zip_dir(src, dst, prefix="", extra=None):
    """Comprime a mano la carpeta `src` (como se hace con los `catalogo.zip` que circulan)."""
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _d, files in os.walk(src):
            for f in sorted(files):
                full = os.path.join(base, f)
                z.write(full, prefix + os.path.relpath(full, src).replace(os.sep, "/"))
        for n, b in (extra or {}).items():
            z.writestr(prefix + n, b)
    return str(dst)


def files_of(root, sub="piezas"):
    out = {}
    for base, _d, files in os.walk(os.path.join(root, sub)):
        for f in files:
            out[os.path.relpath(os.path.join(base, f), root)] = P.sha256_file(os.path.join(base, f))
    return out


@pytest.mark.parametrize("prefix", ["", "proceres-127/"])
def test_el_catalogo_de_ejemplo_se_instala_sin_perder_nada(tmp_path, prefix):
    z = zip_dir(EJEMPLO, tmp_path / "heredado.zip", prefix, extra={**JUNK, "__MACOSX/._x": b"x"})
    leg = I.detect_legacy(z)
    assert leg is not None and leg.prefix == prefix and leg.linea["formato"] == "afm-linea-1"
    work = tmp_path / "w"
    work.mkdir()
    dest = tmp_path / "destino"
    plan = I.plan_install_legacy(z, str(dest), str(work), **INFO)
    assert plan.ok, [str(i) for i in plan.errors]
    assert plan.como_linea
    assert set(plan.descartados) == set(JUNK) | {"CATALOGO.md"}          # lo derivado se descarta; el ruido de macOS ni se nombra
    res = I.apply(plan)
    c = Catalog.open(str(dest))
    assert c.verify().ok and len(res.piezas) == 5 and res.recetas == ["muneco"]
    assert files_of(str(dest)) == files_of(EJEMPLO)                          # cada STL y cada ficha, byte por byte
    assert os.path.isfile(dest / "figuras" / "muneco.receta.json") and not (dest / "recetas").exists()
    assert not any((dest / n).exists() for n in ("compat.json", "calibracion.json", ".papelera", "caballos"))
    reg = I.load_registry(str(dest))["paquetes"][res.paquete.id]
    assert reg["licencia"] == "CC-BY-4.0" and reg["origen"] == "propia" and reg["heredado_sha256"] == P.sha256_file(z)
    assert any("recetas/" in a for a in res.avisos)                          # se avisa que las recetas se movieron a figuras/


def test_reinstalar_el_mismo_zip_heredado_no_hace_nada(tmp_path):
    z = zip_dir(EJEMPLO, tmp_path / "h.zip")
    dest = tmp_path / "d"
    work = tmp_path / "w"
    work.mkdir()
    I.apply(I.plan_install_legacy(z, str(dest), str(work), **INFO))
    work2 = tmp_path / "w2"
    work2.mkdir()
    before = I.tree_digest(str(dest))
    plan = I.plan_install_legacy(z, str(dest), str(work2), **INFO)
    assert plan.ok and plan.nada_que_hacer and I.tree_digest(str(dest)) == before


def test_el_paquete_convertido_es_un_paquete_valido(tmp_path):
    z = zip_dir(EJEMPLO, tmp_path / "h.zip")
    conv = I.prepare_legacy(z, str(tmp_path / "p.zip"), **INFO)
    assert V.validate_package(conv.path).ok and conv.paquete.tipo == "linea" and conv.paquete.version == "1.0.0"
    assert conv.paquete.id == "ejemplo-100"                              # id por defecto: el nombre de la línea (la de catalogo/ejemplo)
    again = I.prepare_legacy(z, str(tmp_path / "q.zip"), **INFO)
    assert P.sha256_file(conv.path) == P.sha256_file(again.path)        # determinista: sirve para detectar «ya instalado»


def test_sin_licencia_origen_o_autor_no_se_convierte(tmp_path):
    z = zip_dir(EJEMPLO, tmp_path / "h.zip")
    for bad in (dict(INFO, licencia=""), dict(INFO, origen=""), dict(INFO, autores=[]), dict(INFO, licencia="inventada-1.0")):
        with pytest.raises(I.InstallError):
            I.prepare_legacy(z, str(tmp_path / "p.zip"), **bad)


def test_lo_que_no_es_heredado(cat, tmp_path):
    add_arm(cat)
    from afm import exportar as X
    pkg = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-test-01"], autores=["A:a"], licencia="CC-BY-4.0", origen="propia").path
    assert I.detect_legacy(pkg) is None                                   # trae paquete.json
    sin = tmp_path / "s.zip"
    with zipfile.ZipFile(sin, "w") as z:
        z.writestr("README.md", "x")
    assert I.detect_legacy(str(sin)) is None                              # no trae linea.json: lo rechaza el camino normal
    dentro = tmp_path / "d.zip"
    with zipfile.ZipFile(dentro, "w") as z, zipfile.ZipFile(pkg) as src:
        for n in src.namelist():
            z.writestr("carpeta/" + n, src.read(n))
    with pytest.raises(I.InstallError, match="dentro de la carpeta"):
        I.detect_legacy(str(dentro))
    dos = tmp_path / "dos.zip"
    with zipfile.ZipFile(dos, "w") as z:
        z.writestr("a/linea.json", "{}")
        z.writestr("b/linea.json", "{}")
    with pytest.raises(I.InstallError, match="más de una línea"):
        I.detect_legacy(str(dos))


def test_solo_se_extrae_lo_conocido_y_lo_hostil_no_llega_a_disco(tmp_path):
    hostil = {"../../fuera.txt": b"x", "piezas/brazo/../../../x.stl": b"x", "/abs.json": b"x", "piezas/brazo/otra-cosa.exe": b"x"}
    z = zip_dir(EJEMPLO, tmp_path / "h.zip", extra=hostil)
    conv = I.prepare_legacy(z, str(tmp_path / "p.zip"), **INFO)
    assert set(hostil) <= set(conv.descartados)                          # descartados, no extraídos
    assert not (tmp_path / "fuera.txt").exists()
    assert V.validate_package(conv.path).ok


def test_duplicado_por_mayusculas_entre_lo_conservado_se_rechaza(tmp_path):
    z = zip_dir(EJEMPLO, tmp_path / "h.zip")
    with zipfile.ZipFile(z, "a") as zf:
        zf.writestr("PIEZAS/brazo/brazo-muneco-01@1.stl", b"x")          # no coincide con el patrón (mayúsculas): se descarta
        zf.writestr("figuras/Hero.receta.json", b"{}")
        zf.writestr("figuras/hero.receta.json", b"{}")                    # dos recetas que solo difieren en mayúsculas
    with pytest.raises(I.InstallError, match="inspección"):
        I.prepare_legacy(z, str(tmp_path / "p.zip"), **INFO)


def test_un_catalogo_roto_no_se_instala(tmp_path):
    z = zip_dir(EJEMPLO, tmp_path / "h.zip")
    rotos = tmp_path / "rotos.zip"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(rotos, "w") as out:
        for n in src.namelist():
            data = src.read(n)
            if n.endswith("brazo-muneco-01@1.stl"):
                data = data[:-40] + b"\0" * 40                            # el hash de la ficha ya no coincide
            out.writestr(n, data)
    with pytest.raises(I.InstallError, match="verificar"):
        I.prepare_legacy(str(rotos), str(tmp_path / "p.zip"), **INFO)


def test_fbx_de_color_solo_viaja_con_permitir_fbx(cat, tmp_path):
    add_colored(cat, "brazo-test-01", "rgb")
    z = zip_dir(cat.root, tmp_path / "h.zip")
    sin = I.prepare_legacy(z, str(tmp_path / "sin.zip"), **INFO)
    assert any(d.endswith(".fbx") for d in sin.descartados) and any("FBX" in a for a in sin.avisos)
    with zipfile.ZipFile(sin.path) as pz:
        assert not any(n.endswith(".fbx") for n in pz.namelist())
        ficha = json.loads(pz.read("piezas/brazo/brazo-test-01@1.pieza.json"))
    assert not ficha.get("color")                                         # la pieza sale sin color, como en `exportar`
    con = I.prepare_legacy(z, str(tmp_path / "con.zip"), allow_fbx=True, **INFO)
    assert not any(d.endswith(".fbx") for d in con.descartados)
    with zipfile.ZipFile(con.path) as pz:
        assert "piezas/brazo/brazo-test-01@1.fbx" in pz.namelist()
    dest = tmp_path / "d"
    work = tmp_path / "w"
    work.mkdir()
    plan = I.plan_install_legacy(z, str(dest), str(work), allow_fbx=True, **INFO)
    assert plan.ok, [str(i) for i in plan.errors]
    I.apply(plan)
    assert Catalog.open(str(dest)).verify().ok and (dest / "piezas" / "brazo" / "brazo-test-01@1.fbx").is_file()


def test_las_recetas_de_figuras_y_la_paleta_viajan(cat, tmp_path):
    ref = add_arm(cat).ref
    add_recipe(cat, "heroe", {"brazo": ref})
    z = zip_dir(cat.root, tmp_path / "h.zip", "mi-linea/")
    work = tmp_path / "w"
    work.mkdir()
    I.apply(I.plan_install_legacy(z, str(tmp_path / "d"), str(work), **INFO))
    assert os.path.isfile(tmp_path / "d" / "figuras" / "heroe.receta.json")


# ---------------------------------------------------------------------------------------------------- CLI


def test_cli_instala_un_zip_heredado_con_las_opciones(tmp_path, capsys):
    z = zip_dir(EJEMPLO, tmp_path / "h.zip", "catalogo/", extra=JUNK)
    dest = str(tmp_path / "d")
    assert main(["catalogo", "instalar", z, "--en", dest, "--licencia", "CC-BY-4.0", "--origen", "propia", "--autor", "Ana:ana", "--si"]) == 0
    out = capsys.readouterr().out
    assert "no se instalan" in out and "Instalado" in out
    assert main(["catalogo", "verificar", dest]) == 0 and main(["catalogo", "listar", dest]) == 0
    assert "torso-muneco-01" in capsys.readouterr().out


def test_cli_pregunta_lo_que_falta_y_sin_respuesta_no_instala(tmp_path, capsys, monkeypatch):
    z = zip_dir(EJEMPLO, tmp_path / "h.zip")
    dest = str(tmp_path / "d")
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(EOFError()))
    assert main(["catalogo", "instalar", z, "--en", dest, "--si"]) == 2
    assert "--licencia" in capsys.readouterr().err and not os.path.exists(dest)
    answers = iter(["CC-BY-4.0", "propia", "Ana Pérez", "s"])
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    assert main(["catalogo", "instalar", z, "--en", dest]) == 0
    assert Catalog.open(dest).verify().ok


def test_cli_simular_con_un_heredado_no_escribe(tmp_path, capsys):
    z = zip_dir(EJEMPLO, tmp_path / "h.zip")
    dest = tmp_path / "d"
    assert main(["catalogo", "instalar", z, "--en", str(dest), "--licencia", "MIT", "--origen", "propia", "--autor", "Ana", "--simular"]) == 0
    assert "Simulación" in capsys.readouterr().out and not dest.exists()
