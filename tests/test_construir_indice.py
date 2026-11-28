"""El bot: de las altas y los Releases a `data/` (index.json, fichas, portadas). «Listo cuando» de D12: el alta de un paquete de prueba aparece
en index.json tras correr el bot y uno inválido no entra."""
import json
import os

import pytest
from conftest import escribir_alta, png, sha

import common as C
import construir_indice as CI

from afm import bloqueos as B
from afm import remoto as R

ZIP = "mi-paquete-1.0.0.zip"


def correr(raiz, gh, salida, *, releases=True, previo=None, bloqueados=(), **kw):
    return CI.construir(raiz, salida, releases=releases, gh=gh.cliente() if releases else None, previo=previo, bloqueados=bloqueados,
                        generado="2026-11-28T12:00:00Z", **kw)


def entradas(indice):
    return {e["folder"]: e for e in indice["paquetes"]}


def leer(path):
    return json.load(open(path, encoding="utf-8"))


@pytest.fixture
def listo(raiz, gh, hacer_zip, tmp_path):
    """Una alta válida con su Release publicado."""
    z = hacer_zip()
    escribir_alta(raiz, z)
    gh.publicar("ana/mi-paquete", z, name=ZIP)
    return z, str(tmp_path / "data")


def test_una_alta_valida_aparece_en_el_indice_y_una_invalida_no_entra(raiz, gh, hacer_zip, tmp_path):
    z = hacer_zip()
    escribir_alta(raiz, z)
    gh.publicar("ana/mi-paquete", z, name=ZIP)
    escribir_alta(raiz, hacer_zip(pid="sin-release", github="ana/sin-release"))                    # su repo no tiene ningún Release
    escribir_alta(raiz, hacer_zip(pid="licencia-mala", github="ana/licencia-mala"), licencia="inventada")   # no cumple el alta (IX101)
    out = str(tmp_path / "data")
    indice, hallazgos, fatal = correr(raiz, gh, out)
    assert fatal == "" and list(entradas(indice)) == ["ana@mi-paquete"] and indice["cantidad"] == 1
    cod = {(h.codigo, h.folder) for h in hallazgos if h.severidad == "error"}
    assert ("IX105", "ana@sin-release") in cod and ("IX101", "ana@licencia-mala") in cod


def test_la_entrada_trae_lo_que_el_cliente_necesita(listo, raiz, gh):
    z, out = listo
    indice, _h, _f = correr(raiz, gh, out)
    e = entradas(indice)["ana@mi-paquete"]
    assert e["latest"]["version"] == "1.0.0" and e["latest"]["zip"]["sha256"] == sha(z) and e["latest"]["zip"]["tamano"] == os.path.getsize(z)
    assert e["latest"]["zip"]["url"] == f"{gh.url}/dl/ana/mi-paquete/v1.0.0/{ZIP}" and e["latest"]["tag"] == "v1.0.0"
    assert (e["autor"], e["github"], e["licencia"], e["update_check"], e["descargas"]) == ("ana", "ana/mi-paquete", "CC-BY-4.0", "ok", None)
    assert e["portada"] == "paquetes/ana@mi-paquete/portada.png" and e["descripcion_url"] == "paquetes/ana@mi-paquete/descripcion.md"
    assert e["latest"]["fichas"] == "fichas/ana@mi-paquete-1.0.0.json" and e["piezas"]


def test_los_archivos_de_salida(listo, raiz, gh):
    from PIL import Image
    _z, out = listo
    correr(raiz, gh, out)
    assert sorted(os.listdir(out)) == ["fichas", "index.json", "paquetes"]
    assert os.listdir(os.path.join(out, "fichas")) == ["ana@mi-paquete-1.0.0.json"]
    carpeta = os.path.join(out, "paquetes", "ana@mi-paquete")
    assert sorted(os.listdir(carpeta)) == ["descripcion.md", "portada.png"]
    with Image.open(os.path.join(carpeta, "portada.png")) as im:
        assert im.size == (640, 360) and not im.info.get("exif")          # miniatura, reencodada y sin metadatos


def test_el_cliente_de_afm_lee_el_indice_sin_avisos(listo, raiz, gh):
    """El contrato de punta a punta: lo que escribe el bot lo entiende `afm.remoto`, incluida la `portada` y la URL del zip."""
    _z, out = listo
    correr(raiz, gh, out)
    idx = R.parse_index(open(os.path.join(out, "index.json"), "rb").read(), url="https://ana.github.io/afm-catalogo-indice/data/index.json")
    assert idx.avisos == [] and [e.folder for e in idx.entries] == ["ana@mi-paquete"]
    e = idx.entries[0]
    assert e.latest.version == "1.0.0" and e.latest.url.startswith(gh.url) and e.portada == "https://ana.github.io/afm-catalogo-indice/data/paquetes/ana@mi-paquete/portada.png"


def test_el_index_cumple_su_esquema(listo, raiz, gh):
    import jsonschema
    from afm import indice as IX
    _z, out = listo
    correr(raiz, gh, out)
    jsonschema.validate(leer(os.path.join(out, "index.json")), IX.schema("indice"))


def test_sin_releases_se_arma_solo_con_los_meta(raiz, hacer_zip, tmp_path):
    escribir_alta(raiz, hacer_zip())
    out = str(tmp_path / "data")
    indice, _h, fatal = correr(raiz, None, out, releases=False)
    e = entradas(indice)["ana@mi-paquete"]
    assert fatal == "" and "latest" not in e and e["update_check"] == "off"
    assert R.parse_index(open(os.path.join(out, "index.json"), "rb").read()).entries[0].latest is None          # se ve, no se puede instalar


def test_prerelease_y_borrador_no_son_la_ultima(listo, raiz, gh, hacer_zip):
    _z, out = listo
    gh.publicar("ana/mi-paquete", hacer_zip(version="1.1.0-rc1"), tag="v1.1.0-rc1", name="mi-paquete-1.1.0-rc1.zip", prerelease=True,
                published_at="2026-11-29T00:00:00Z")
    gh.publicar("ana/mi-paquete", hacer_zip(version="2.0.0"), tag="v2.0.0", name="mi-paquete-2.0.0.zip", draft=True)
    indice, _h, _f = correr(raiz, gh, out)
    assert entradas(indice)["ana@mi-paquete"]["latest"]["version"] == "1.0.0"


def test_la_version_nueva_deja_la_anterior_en_versiones(listo, raiz, gh, hacer_zip):
    _z, out = listo
    primero, _h, _f = correr(raiz, gh, out)
    gh.publicar("ana/mi-paquete", hacer_zip(version="1.1.0"), tag="v1.1.0", name="mi-paquete-1.1.0.zip", published_at="2026-12-01T00:00:00Z")
    segundo, _h, _f = correr(raiz, gh, out, previo=primero)
    e = entradas(segundo)["ana@mi-paquete"]
    assert e["latest"]["version"] == "1.1.0" and [v["version"] for v in e["versiones"]] == ["1.0.0"]
    assert e["versiones"][0]["zip"]["sha256"] == primero["paquetes"][0]["latest"]["zip"]["sha256"]               # sigue instalable, sin volver a bajarla
    tercero, _h, _f = correr(raiz, gh, out, previo=segundo)                                                      # una corrida más no duplica nada
    assert [v["version"] for v in entradas(tercero)["ana@mi-paquete"]["versiones"]] == ["1.0.0"]


def test_si_la_puerta_falla_se_conserva_la_ultima_version_buena(listo, raiz, gh):
    _z, out = listo
    bueno, _h, _f = correr(raiz, gh, out)
    gh.repos["ana/mi-paquete"][0]["assets"][ZIP] = b"el autor subio un archivo roto"
    malo, hallazgos, fatal = correr(raiz, gh, out, previo=bueno)
    e = entradas(malo)["ana@mi-paquete"]
    assert fatal == "" and e["latest"] == entradas(bueno)["ana@mi-paquete"]["latest"] and e["update_check"].startswith("error: ")
    assert any(h.severidad == "aviso" and "se conserva la versión 1.0.0" in h.mensaje for h in hallazgos)


def test_sin_version_previa_un_paquete_que_falla_no_se_lista(listo, raiz, gh):
    _z, out = listo
    gh.repos["ana/mi-paquete"][0]["assets"][ZIP] = b"roto"
    indice, hallazgos, fatal = correr(raiz, gh, out)
    assert fatal == "" and indice["paquetes"] == [] and any("no se lista" in h.mensaje for h in hallazgos)


def test_seguimiento_fijo_sigue_el_tag_pedido(listo, raiz, gh, hacer_zip):
    z, out = listo
    folder = "ana@mi-paquete"
    meta = os.path.join(raiz, "paquetes", folder, "meta.json")
    d = leer(meta)
    d.update(seguimiento="fijo", release_fijo="v1.0.0")
    json.dump(d, open(meta, "w", encoding="utf-8"))
    gh.publicar("ana/mi-paquete", hacer_zip(version="1.1.0"), tag="v1.1.0", name="mi-paquete-1.1.0.zip", published_at="2026-12-01T00:00:00Z")
    indice, _h, _f = correr(raiz, gh, out)
    e = entradas(indice)[folder]
    assert e["latest"]["version"] == "1.0.0" and (e["seguimiento"], e["release_fijo"]) == ("fijo", "v1.0.0")


def test_lo_bloqueado_se_retira_y_se_avisa_a_los_clientes(listo, raiz, gh):
    _z, out = listo
    bl = [B.Block(id="mi-paquete", motivo="reclamo del titular", fecha="2026-11-30")]
    indice, hallazgos, fatal = correr(raiz, gh, out, bloqueados=bl)
    assert fatal == "" and indice["paquetes"] == []
    assert indice["bloqueados"][0]["id"] == "mi-paquete"                                     # la clave aditiva para quien ya lo instaló
    assert not os.path.exists(os.path.join(out, "paquetes", "ana@mi-paquete"))              # y no se publica ni su portada
    assert any(h.codigo == "IX106" for h in hallazgos)


def test_bloqueo_por_sha256_saca_solo_esa_version(listo, raiz, gh, hacer_zip):
    z, out = listo
    primero, _h, _f = correr(raiz, gh, out)
    gh.publicar("ana/mi-paquete", hacer_zip(version="1.1.0"), tag="v1.1.0", name="mi-paquete-1.1.0.zip", published_at="2026-12-01T00:00:00Z")
    bl = [B.Block(sha256=sha(z), motivo="malla robada")]
    indice, _h, _f = correr(raiz, gh, out, previo=primero, bloqueados=bl)
    e = entradas(indice)["ana@mi-paquete"]
    assert e["latest"]["version"] == "1.1.0" and e["versiones"] == []


def test_si_github_no_responde_no_se_publica_un_indice_vacio(raiz, hacer_zip, tmp_path):
    escribir_alta(raiz, hacer_zip())
    muerto = C.GitHub(api="http://127.0.0.1:9", loopback=True, timeout=2)
    out = str(tmp_path / "data")
    indice, hallazgos, fatal = CI.construir(raiz, out, releases=True, gh=muerto, previo=None, bloqueados=(), generado="2026-11-28T12:00:00Z")
    assert "no se publica" in fatal and not os.path.exists(out)                             # el workflow no despliega nada


def test_si_github_no_responde_se_conserva_lo_anterior(listo, raiz, gh, tmp_path):
    _z, out = listo
    bueno, _h, _f = correr(raiz, gh, out)
    muerto = C.GitHub(api="http://127.0.0.1:9", loopback=True, timeout=2)
    indice, _h, fatal = CI.construir(raiz, str(tmp_path / "data2"), releases=True, gh=muerto, previo=bueno, bloqueados=(), generado="2026-11-28T13:00:00Z")
    e = entradas(indice)["ana@mi-paquete"]
    assert fatal == "" and e["latest"]["version"] == "1.0.0" and e["update_check"] == "inaccesible"


def test_la_salida_anterior_se_reemplaza_entera(listo, raiz, gh):
    _z, out = listo
    os.makedirs(os.path.join(out, "paquetes", "viejo@fantasma"))
    correr(raiz, gh, out)
    assert os.listdir(os.path.join(out, "paquetes")) == ["ana@mi-paquete"]


def test_cli(listo, raiz, gh, capsys):
    _z, out = listo
    args = ["--raiz", raiz, "--salida", out, "--releases", "--api", gh.url, "--loopback"]
    assert CI.main(args) == 0 and "1 paquete(s) listado(s)" in capsys.readouterr().out
    assert os.path.isfile(os.path.join(out, "index.json"))
    open(os.path.join(raiz, "bloqueados.json"), "w").write("esto no es json")
    assert CI.main(args) == 2                                                                # una lista de bloqueos rota frena la corrida


def test_cli_previo_inexistente_se_ignora(listo, raiz, gh):
    _z, out = listo
    assert CI.main(["--raiz", raiz, "--salida", out, "--releases", "--api", gh.url, "--loopback", "--previo", "/no/existe.json"]) == 0
