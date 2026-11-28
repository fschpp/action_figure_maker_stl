"""D04 · `afm catalogo paquete validar`: un caso por código CK (PLAN_catalogos_online.md §6)."""
import hashlib
import json
import zipfile

import pytest

from afm import exportar as X
from afm import paquete as Q
from afm import validar as V
from afm import ziplector as Z
from afm.cli import main
from afm.palette import Palette, Role
from test_exportar import OWNER, add_arm, add_colored, cat, names, read, sin_mano  # noqa: F401  (cat es una fixture)


def sha(b):
    return hashlib.sha256(b).hexdigest()


def good(cat, tmp_path, name="p.zip", **kw):
    add_arm(cat)
    return X.export_package(cat, str(tmp_path / name), piezas=["brazo-test-01"], **{**OWNER, **kw}).path


def rewrite(src, dst, mutate=None, refresh=False, manifest=None):
    """Copia `src` a `dst` aplicando `mutate(dict nombre→bytes)`; con `refresh` recalcula `archivos` (para probar lo que NO es un hash roto);
    `manifest(dict)` edita el paquete.json."""
    with zipfile.ZipFile(src) as z:
        files = {n: z.read(n) for n in z.namelist()}
    if mutate:
        mutate(files)
    m = json.loads(files["paquete.json"])
    if refresh:
        m["archivos"] = {n: sha(b) for n, b in sorted(files.items()) if n != "paquete.json"}
    if manifest:
        manifest(m)
    files["paquete.json"] = json.dumps(m, indent=2, ensure_ascii=False).encode()
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for n in ["paquete.json"] + sorted(k for k in files if k != "paquete.json"):
            z.writestr(zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0)), files[n], zipfile.ZIP_DEFLATED)
    return str(dst)


def codes(res, sev=None):
    return sorted({f.code for f in res.findings if sev is None or f.severity == sev})


def test_paquete_exportado_valida_sin_errores(cat, tmp_path):
    res = V.validate_package(good(cat, tmp_path))
    assert res.ok, [str(f) for f in res.errors]
    assert [f.code for f in res.warnings if f.code != "CK203"] == ["CK105"]      # sin portada: solo aviso
    assert res.paquete.id


def test_estricto_convierte_el_aviso_de_portada_en_error(cat, tmp_path):
    p = good(cat, tmp_path)
    assert not V.validate_package(p, estricto=True).ok
    assert V.validate_package(p, estricto=True).errors[0].code in ("CK105", "CK203")


def test_con_portada_pasa_estricto(cat, tmp_path):
    from PIL import Image
    Image.new("RGB", (1280, 720), (10, 20, 30)).save(tmp_path / "c.png")
    add_arm(cat)
    p = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-test-01"], portada=str(tmp_path / "c.png"), **OWNER).path
    res = V.validate_package(p, estricto=True)
    assert "CK105" not in codes(res)


@pytest.mark.parametrize("code,edit", [
    ("CK101", lambda m: m.pop("licencia")),
    ("CK101", lambda m: m.update(licencia="WTFPL-inventada")),
    ("CK102", lambda m: m.update(descripcion="")),
    ("CK102", lambda m: m.update(origen="")),
    ("CK103", lambda m: m.update(version="uno")),
    ("CK106", lambda m: m.update(categorias=["cosas"])),
    ("CK106", lambda m: m.update(categorias=[])),
    ("CK107", lambda m: m.update(origen="referencia")),
])
def test_manifiesto_codes(cat, tmp_path, code, edit):
    p = rewrite(good(cat, tmp_path), tmp_path / "x.zip", manifest=edit)
    res = V.validate_package(p, verify_catalog=False)
    assert code in codes(res, V.ERROR), [str(f) for f in res.findings]
    assert all(f.fix for f in res.findings if f.code == code and f.severity == V.ERROR)


def test_ck104_altura_distinta_de_linea_json(cat, tmp_path):
    p = rewrite(good(cat, tmp_path), tmp_path / "x.zip", manifest=lambda m: m["linea"].update(altura_mm=140.0))
    res = V.validate_package(p, verify_catalog=False)
    assert "CK104" in codes(res) and any("linea.json" in f.message for f in res.findings if f.code == "CK104")


def test_ck104_falta_linea_json(cat, tmp_path):
    p = rewrite(good(cat, tmp_path), tmp_path / "x.zip", mutate=lambda f: f.pop("linea.json"), refresh=True)
    assert "CK104" in codes(V.validate_package(p))


def test_ck201_archivo_alterado(cat, tmp_path):
    p = rewrite(good(cat, tmp_path), tmp_path / "x.zip", mutate=lambda f: f.update({"README.md": b"cambiado"}))
    res = V.validate_package(p)
    bad = [f for f in res.findings if f.code == "CK201"]
    assert [f.path for f in bad] == ["README.md"] and bad[0].fix


def test_ck202_sobra_y_falta(cat, tmp_path):
    def mut(f):
        f["extra.txt"] = b"hola"
        f.pop("LICENSE")
    p = rewrite(good(cat, tmp_path), tmp_path / "x.zip", mutate=mut)
    msgs = {f.path: f.message for f in V.validate_package(p).findings if f.code == "CK202"}
    assert "no figura" in msgs["extra.txt"] and "no está en el zip" in msgs["LICENSE"]


def test_ck203_stl_alterado_con_hash_de_archivos_al_dia(cat, tmp_path):
    def mut(f):
        k = next(n for n in f if n.endswith(".stl"))
        f[k] = f[k] + b" "
    p = rewrite(good(cat, tmp_path), tmp_path / "x.zip", mutate=mut, refresh=True)
    res = V.validate_package(p)
    assert res.findings and "CK203" in codes(res, V.ERROR) and "CK201" not in codes(res)


def test_ck203_se_puede_omitir(cat, tmp_path):
    def mut(f):
        k = next(n for n in f if n.endswith(".stl"))
        f[k] = f[k] + b" "
    p = rewrite(good(cat, tmp_path), tmp_path / "x.zip", mutate=mut, refresh=True)
    assert "CK203" not in codes(V.validate_package(p, verify_catalog=False))


def test_ck204_roles_sin_paleta(cat, tmp_path):
    cat.set_palette(Palette([Role("rojo", "Rojo", "#ff0000")]))
    add_colored(cat, "brazo-roles-01", "roles", b"NPZ")
    p = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-roles-01"], **OWNER).path
    assert "CK204" not in codes(V.validate_package(p, verify_catalog=False))
    q = rewrite(p, tmp_path / "q.zip", mutate=lambda f: f.pop("paleta.json"), refresh=True,
                manifest=lambda m: m["contenido"].update(paleta=False))
    res = V.validate_package(q, verify_catalog=False)
    assert "CK204" in codes(res, V.ERROR)
    r = rewrite(p, tmp_path / "r.zip", mutate=lambda f: f.update({"paleta.json": json.dumps(Palette([Role("azul", "Azul", "#0000ff")]).to_dict()).encode()}), refresh=True)
    assert any("rojo" in f.message for f in V.validate_package(r, verify_catalog=False).findings if f.code == "CK204")


def test_ck204_no_se_duplica_con_ck203(cat, tmp_path):
    cat.set_palette(Palette([Role("rojo", "Rojo", "#ff0000")]))
    add_colored(cat, "brazo-roles-01", "roles", b"NPZ")
    p = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-roles-01"], **OWNER).path
    q = rewrite(p, tmp_path / "q.zip", mutate=lambda f: f.pop("paleta.json"), refresh=True, manifest=lambda m: m["contenido"].update(paleta=False))
    assert not [f for f in V.validate_package(q).findings if f.code == "CK203" and "roles" in f.message]


@pytest.mark.parametrize("name", ["run.sh", "piezas/x.exe", "sin_extension"])
def test_ck301_extension(cat, tmp_path, name):
    p = rewrite(good(cat, tmp_path), tmp_path / "x.zip", mutate=lambda f: f.update({name: b"x"}), refresh=True)
    res = V.validate_package(p)
    assert codes(res, V.ERROR) == ["CK301"]                       # y nada más: no se abre el resto con un archivo no permitido


def test_ck302_ruta_peligrosa_no_abre_nada_mas(cat, tmp_path):
    p = rewrite(good(cat, tmp_path), tmp_path / "x.zip", mutate=lambda f: f.update({"../fuera.json": b"x"}))
    res = V.validate_package(p)
    assert codes(res) == ["CK302"]
    assert not (tmp_path.parent / "fuera.json").exists()


def test_ck303_bomba(cat, tmp_path):
    p = rewrite(good(cat, tmp_path), tmp_path / "x.zip", mutate=lambda f: f.update({"piezas/relleno.json": b"\0" * (8 * 1024 * 1024)}))
    assert "CK303" in codes(V.validate_package(p))


def test_ck304_archivo_de_referencia_empaquetado(cat, tmp_path):
    p = good(cat, tmp_path)
    secreto = b"modelo con derechos"
    def edit(m):
        m["origen"] = "referencia"
        m["referencias_requeridas"] = [{"id": "modelo-x", "nombre": "Modelo X", "sha256": [sha(secreto)], "tamano_max": 1000}]
    q = rewrite(p, tmp_path / "q.zip", mutate=lambda f: f.update({"piezas/brazo/modelo.stl": secreto}), refresh=True, manifest=edit)
    res = V.validate_package(q, verify_catalog=False)
    assert [f.path for f in res.findings if f.code == "CK304"] == ["piezas/brazo/modelo.stl"]


def test_ck306_requiere_sin_pista(cat, tmp_path):
    p = rewrite(good(cat, tmp_path), tmp_path / "x.zip", manifest=lambda m: m.update(requiere=["otro-paquete@^1.0.0"]))
    res = V.validate_package(p, verify_catalog=False)
    assert "CK306" in codes(res, V.WARNING) and "CK306" not in codes(res, V.ERROR)
    q = rewrite(p, tmp_path / "q.zip", manifest=lambda m: m.update(requiere=["otro-paquete@^1.0.0#ana/otro"]))
    assert "CK306" not in codes(V.validate_package(q, verify_catalog=False))


def test_ck307_ficha_con_color_y_sin_fbx(cat, tmp_path):
    add_colored(cat, "brazo-rgb-01", "rgb", b"FBX")
    p = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-rgb-01"], incluir_fbx=True, **OWNER).path
    assert "CK307" not in codes(V.validate_package(p, allow_fbx=True))
    q = rewrite(p, tmp_path / "q.zip", mutate=lambda f: f.pop("piezas/brazo/brazo-rgb-01@1.fbx"), refresh=True)
    res = V.validate_package(q)
    assert codes(res, V.ERROR).count("CK307") == 1 and [f for f in res.findings if f.code == "CK203" and "FBX" in f.message] == []


def test_fbx_sin_permiso_es_ck301(cat, tmp_path):
    add_colored(cat, "brazo-rgb-01", "rgb", b"FBX")
    p = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-rgb-01"], incluir_fbx=True, **OWNER).path
    assert "CK301" in codes(V.validate_package(p))


def test_coleccion_ck402_ck403(tmp_path):
    base = {"formato": "afm-paquete-1", "id": "col", "nombre": "Col", "version": "1.0.0", "descripcion": "d", "tipo": "coleccion",
            "autores": [{"nombre": "Ana"}], "licencia": "CC-BY-4.0", "origen": "propia", "categorias": ["colecciones"],
            "contenido": {"piezas": [], "recetas": [], "paleta": False, "pines": [{"id": "a", "github": "mal", "version": "1", "sha256": "x"}]},
            "archivos": {"README.md": sha(b"r"), "LICENSE": sha(b"l"), "piezas/brazo/x.stl": sha(b"s")}}
    z = tmp_path / "c.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("paquete.json", json.dumps(base))
        zf.writestr("README.md", b"r")
        zf.writestr("LICENSE", b"l")
        zf.writestr("piezas/brazo/x.stl", b"s")                    # README y LICENSE pueden viajar; un STL no (D34)
    res = V.validate_package(str(z))
    assert {"CK402", "CK403"} <= set(codes(res, V.ERROR))
    assert [f.path for f in res.findings if f.code == "CK403"] == ["paquete.json:archivos"] and "piezas/brazo/x.stl" in next(f.message for f in res.findings if f.code == "CK403")
    assert "CK105" not in codes(res)                               # una colección no lleva portada obligatoria


def test_sin_manifiesto_y_formato_futuro(cat, tmp_path):
    p = rewrite(good(cat, tmp_path), tmp_path / "x.zip")
    with zipfile.ZipFile(p) as z, zipfile.ZipFile(tmp_path / "s.zip", "w") as o:
        for n in z.namelist():
            if n != "paquete.json":
                o.writestr(n, z.read(n))
    res = V.validate_package(str(tmp_path / "s.zip"))
    assert [(f.code, f.path) for f in res.findings] == [("CK102", "paquete.json")]
    q = rewrite(p, tmp_path / "f.zip", manifest=lambda m: m.update(formato="afm-paquete-9"))
    res = V.validate_package(q)
    assert res.findings and "actualiz" in res.findings[0].fix


def test_no_modifica_el_zip(cat, tmp_path):
    p = good(cat, tmp_path)
    before = open(p, "rb").read()
    V.validate_package(p)
    assert open(p, "rb").read() == before


# ---------------------------------------------------------------------------------------------------- CLI

def test_cli_codigos_de_salida(cat, tmp_path, capsys):
    p = good(cat, tmp_path)
    assert main(["catalogo", "paquete", "validar", p]) == 0
    out = capsys.readouterr().out
    assert "OK:" in out and "CK105" in out
    assert main(["catalogo", "paquete", "validar", p, "--estricto"]) == 1
    assert "FALLA:" in capsys.readouterr().out
    bad = rewrite(p, tmp_path / "b.zip", mutate=lambda f: f.update({"README.md": b"x"}))
    assert main(["catalogo", "paquete", "validar", bad]) == 1
    assert "CK201 README.md" in capsys.readouterr().out
    (tmp_path / "n.zip").write_text("nada")
    assert main(["catalogo", "paquete", "validar", str(tmp_path / "n.zip")]) == 2


def test_un_hueco_de_versiones_no_es_un_hallazgo(cat, tmp_path):
    """Eliminar una versión (`delete_pieces`) deja un hueco a propósito; el validador estricto no puede bloquear la publicación por eso."""
    from test_exportar import arm_piece
    add_arm(cat)                                              # brazo-test-01@1
    mesh, meta = arm_piece("brazo-test-01")
    cat.add_piece(mesh, meta, new_version=True)               # @2
    cat.delete_pieces(["brazo-test-01@1"])                    # queda solo la @2: hueco
    assert any("versiones no consecutivas" in w for w in cat.verify().warnings)      # el catálogo sí lo avisa
    p = X.export_package(cat, str(tmp_path / "p.zip"), piezas=["brazo-test-01"], **OWNER).path
    res = V.validate_package(p, estricto=True)
    assert not [f for f in res.findings if "versiones no consecutivas" in f.message]
