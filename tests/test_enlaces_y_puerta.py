"""IX105 (los enlaces resuelven) e IX104 (el zip es lo que pidió el alta): `comprobar_enlaces.py` y `puerta_releases.py`."""
import os

import pytest
from conftest import escribir_alta

import common as C
import comprobar_enlaces as CE
import puerta_releases as PR

ZIP = "mi-paquete-1.0.0.zip"


def meta(**kw):
    return {"id": "mi-paquete", "github": "ana/mi-paquete", "licencia": "CC-BY-4.0", "origen": "propia", **kw}


def codigos(hs, sev="error"):
    return sorted({h.codigo for h in hs if h.severidad == sev})


# ------------------------------------------------------------------------------------------------ comprobar_enlaces

def test_enlaces_ok(gh, hacer_zip):
    gh.publicar("ana/mi-paquete", hacer_zip(), name=ZIP)
    assert CE.comprobar(gh.cliente(), "ana@mi-paquete", meta()) == []


def test_enlaces_repo_que_no_existe(gh):
    h = CE.comprobar(gh.cliente(), "ana@mi-paquete", meta())
    assert codigos(h) == ["IX105"] and "no existe o es privado" in h[0].mensaje


def test_enlaces_sin_release(gh):
    gh.repos["ana/mi-paquete"] = []
    assert "publicá la versión antes de pedir el alta" in CE.comprobar(gh.cliente(), "ana@mi-paquete", meta())[0].mensaje


def test_enlaces_borrador_y_prerelease_no_cuentan(gh, hacer_zip):
    gh.publicar("ana/mi-paquete", hacer_zip(), name=ZIP, draft=True)
    gh.publicar("ana/mi-paquete", hacer_zip(version="1.1.0-rc1"), tag="v1.1.0-rc1", name="mi-paquete-1.1.0-rc1.zip", prerelease=True)
    assert codigos(CE.comprobar(gh.cliente(), "ana@mi-paquete", meta())) == ["IX105"]
    assert CE.comprobar(gh.cliente(), "ana@mi-paquete", meta(seguimiento="fijo", release_fijo="v1.1.0-rc1")) == []      # `fijo` sí puede seguir un prerelease


def test_enlaces_release_sin_zip_o_con_varios(gh):
    gh.repos["ana/mi-paquete"] = [{"tag": "v1.0.0", "assets": {"notas.txt": b"x"}}]
    assert "no tiene mi-paquete-1.0.0.zip" in CE.comprobar(gh.cliente(), "ana@mi-paquete", meta())[0].mensaje
    gh.repos["ana/mi-paquete"] = [{"tag": "v1.0.0", "assets": {"a.zip": b"x", "b.zip": b"y"}}]
    assert codigos(CE.comprobar(gh.cliente(), "ana@mi-paquete", meta())) == ["IX105"]                # ambiguo: no se adivina


def test_enlaces_tope_de_tamano(gh):
    gh.repos["ana/mi-paquete"] = [{"tag": "v1.0.0", "assets": {ZIP: b"0" * (2 * 1024 * 1024)}}]
    assert CE.comprobar(gh.cliente(), "ana@mi-paquete", meta(), tope_mb=1)[0].mensaje.endswith("el tope es 1 MB")


def test_enlaces_github_caido_no_se_da_por_bueno(gh):
    gh.repos["ana/mi-paquete"] = []
    gh.caidos.add("ana/mi-paquete")
    h = CE.comprobar(gh.cliente(), "ana@mi-paquete", meta())
    assert codigos(h) == ["IX105"] and "reintentá" in h[0].mensaje
    muerto = C.GitHub(api="http://127.0.0.1:9", loopback=True, timeout=2)
    assert "reintentá" in CE.comprobar(muerto, "ana@mi-paquete", meta())[0].mensaje


def test_enlaces_cli(gh, hacer_zip, raiz, capsys):
    escribir_alta(raiz, hacer_zip())
    gh.publicar("ana/mi-paquete", hacer_zip(), name=ZIP)
    args = ["--raiz", raiz, "--api", gh.url, "--loopback"]
    assert CE.main(args) == 0
    gh.repos["ana/mi-paquete"].clear()
    assert CE.main(args) == 1 and "IX105" in capsys.readouterr().out


# ------------------------------------------------------------------------------------------------ puerta_releases

def evaluar(gh, tmp_path, m=None, **kw):
    return PR.evaluar(gh.cliente(), "ana@mi-paquete", m or meta(), str(tmp_path), **kw)


def test_puerta_un_paquete_valido_pasa(gh, hacer_zip, tmp_path):
    z = hacer_zip()
    gh.publicar("ana/mi-paquete", z, name=ZIP)
    r = evaluar(gh, tmp_path)
    assert r.ok and r.hallazgos == [] and os.path.isfile(r.zip_path)
    assert r.paquete.version == "1.0.0" and r.tamano == os.path.getsize(z) and len(r.sha256) == 64


def test_puerta_zip_que_no_es_un_zip(gh, tmp_path):
    gh.repos["ana/mi-paquete"] = [{"tag": "v1.0.0", "assets": {ZIP: b"esto no es un zip"}}]
    r = evaluar(gh, tmp_path)
    assert not r.ok and "no es un paquete válido" in r.hallazgos[0].mensaje


def test_puerta_validacion_estricta(gh, hacer_zip, tmp_path):
    """Sin portada el paquete no pasa: lo mismo que `afm catalogo validar --estricto`."""
    gh.publicar("ana/mi-paquete", hacer_zip(con_portada=False), name=ZIP)
    r = evaluar(gh, tmp_path)
    assert not r.ok and any("validación estricta" in h.mensaje for h in r.hallazgos)


def test_puerta_tope_de_tamano(gh, hacer_zip, tmp_path):
    gh.publicar("ana/mi-paquete", hacer_zip(), name=ZIP)
    r = evaluar(gh, tmp_path, tope_mb=0)
    assert not r.ok and "pesa" in r.hallazgos[0].mensaje and not os.listdir(tmp_path)


@pytest.mark.parametrize("zip_kw,m,texto", [
    ({"pid": "otra-cosa", "github": "ana/mi-paquete"}, {}, "es del paquete «otra-cosa»"),
    ({"github": "mallory/robado"}, {}, "declara github «mallory/robado»"),
    ({"licencia": "CC0-1.0"}, {}, "licencia: el zip dice «CC0-1.0»"),
    ({"origen": "derivada"}, {}, "origen: el zip dice «derivada»"),
    ({"version": "2.0.0"}, {}, "no es la del Release"),
])
def test_ix104_el_zip_no_es_lo_que_pidio_el_alta(gh, hacer_zip, tmp_path, zip_kw, m, texto):
    gh.publicar("ana/mi-paquete", hacer_zip(**zip_kw), name=ZIP)
    r = evaluar(gh, tmp_path, meta(**m))
    assert not r.ok and codigos(r.hallazgos) == ["IX104"] and any(texto in h.mensaje for h in r.hallazgos), [str(h) for h in r.hallazgos]


def test_ix104_el_texto_de_listado_solo_avisa(gh, hacer_zip, tmp_path):
    gh.publicar("ana/mi-paquete", hacer_zip(), name=ZIP)
    r = evaluar(gh, tmp_path, meta(nombre="Otro nombre en el alta"))
    assert r.ok and codigos(r.hallazgos, "aviso") == ["IX104"]


def test_puerta_github_inaccesible_no_es_culpa_del_paquete(tmp_path):
    muerto = C.GitHub(api="http://127.0.0.1:9", loopback=True, timeout=2)
    r = PR.evaluar(muerto, "ana@mi-paquete", meta(), str(tmp_path))
    assert not r.ok and r.inaccesible


def test_puerta_no_sigue_redirects_a_cualquier_host(gh, tmp_path):
    """El zip solo puede venir de GitHub: un `browser_download_url` apuntando a otro host se rechaza."""
    c = C.GitHub(api=gh.url, timeout=2)                                   # sin loopback: 127.0.0.1 ya no es un host permitido
    with pytest.raises(C.ApiError, match="lista de hosts permitidos"):
        c.get_json("/repos/ana/mi-paquete")
    with pytest.raises(C.ApiError, match="lista de hosts permitidos"):
        c.descargar("https://evil.example.org/p.zip", str(tmp_path / "x.zip"), tope_bytes=10)


def test_puerta_cli(gh, hacer_zip, raiz, capsys):
    escribir_alta(raiz, hacer_zip())
    gh.publicar("ana/mi-paquete", hacer_zip(), name=ZIP)
    args = ["--raiz", raiz, "--api", gh.url, "--loopback"]
    assert PR.main(args) == 0 and "ana@mi-paquete: ok (1.0.0" in capsys.readouterr().out
    assert PR.main(args + ["--tope-zip-mb", "0"]) == 1
