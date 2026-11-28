"""IX101–IX108 sin red: lo que revisa el CI de cada Pull Request sobre `paquetes/<Autor>@<id>/`."""
import json
import os

import pytest
from conftest import escribir_alta, png

import common as C
import validar_alta as VA

from afm import bloqueos as B


def codigos(hallazgos, severidad="error"):
    return sorted({h.codigo for h in hallazgos if h.severidad == severidad})


def revisar(raiz, folder, bloqueados=()):
    return VA.revisar_carpeta(raiz, folder, bloqueados)


def test_una_alta_valida_no_tiene_errores(raiz, hacer_zip):
    folder = escribir_alta(raiz, hacer_zip())
    assert folder == "ana@mi-paquete" and codigos(revisar(raiz, folder)) == []


def test_sin_portada_es_un_aviso(raiz, hacer_zip):
    folder = escribir_alta(raiz, hacer_zip(), portada=False)
    h = revisar(raiz, folder)
    assert codigos(h) == [] and codigos(h, "aviso") == ["IX107"]


def test_ix103_solo_los_archivos_del_alta(raiz, hacer_zip):
    folder = escribir_alta(raiz, hacer_zip())
    base = os.path.join(raiz, "paquetes", folder)
    open(os.path.join(base, "modelo.stl"), "w").write("solid")
    os.makedirs(os.path.join(base, "piezas"))
    os.symlink("/etc/passwd", os.path.join(base, "portada.jpg"))
    h = revisar(raiz, folder)
    assert codigos(h) == ["IX103"]
    texto = " ".join(x.mensaje for x in h)
    assert "modelo.stl" in texto and "piezas" in texto and "enlace simbólico" in texto


def test_ix101_meta_ausente_o_roto(raiz, hacer_zip):
    folder = escribir_alta(raiz, hacer_zip())
    meta = os.path.join(raiz, "paquetes", folder, "meta.json")
    open(meta, "w").write("{no es json")
    assert any(h.codigo == "IX101" and "JSON" in h.mensaje for h in revisar(raiz, folder))
    os.remove(meta)
    assert any(h.codigo == "IX101" and "falta meta.json" in h.mensaje for h in revisar(raiz, folder))


@pytest.mark.parametrize("campo,valor,texto", [
    ("categorias", ["armas"], "categorías fuera de la lista"),
    ("categorias", [], "meta.json categorias"),
    ("licencia", "WTFPL-9", "licencia «WTFPL-9» desconocida"),
    ("origen", "robada", "meta.json origen"),
    ("nombre", None, "nombre"),
    ("seguimiento", "fijo", "release_fijo"),
])
def test_ix101_campos_que_no_sirven(raiz, hacer_zip, campo, valor, texto):
    folder = escribir_alta(raiz, hacer_zip(), **{campo: valor})
    h = revisar(raiz, folder)
    assert codigos(h) == ["IX101"] and any(texto in x.mensaje for x in h)


def test_ix101_descripcion_vacia_o_ausente(raiz, hacer_zip):
    folder = escribir_alta(raiz, hacer_zip(), descripcion="  \n")
    assert any("vacío" in h.mensaje for h in revisar(raiz, folder))
    os.remove(os.path.join(raiz, "paquetes", folder, "descripcion.md"))
    assert any("falta descripcion.md" in h.mensaje for h in revisar(raiz, folder))


def test_ix102_la_carpeta_dice_lo_que_dice_el_meta(raiz, hacer_zip):
    folder = escribir_alta(raiz, hacer_zip())
    os.rename(os.path.join(raiz, "paquetes", folder), os.path.join(raiz, "paquetes", "ana@otro-nombre"))
    assert codigos(revisar(raiz, "ana@otro-nombre")) == ["IX102"]
    os.rename(os.path.join(raiz, "paquetes", "ana@otro-nombre"), os.path.join(raiz, "paquetes", "Ana Perez"))
    assert "IX102" in codigos(revisar(raiz, "Ana Perez"))


def test_ix102_el_autor_tiene_que_ser_el_duenio_del_repo(raiz, hacer_zip):
    """Nadie pide el alta del repo de otra persona."""
    folder = escribir_alta(raiz, hacer_zip(), autor="mallory")
    h = revisar(raiz, folder)
    assert codigos(h) == ["IX102"] and "no es el dueño del repo «ana/mi-paquete»" in h[0].mensaje


def test_ix102_mayusculas_en_el_usuario_no_molestan(raiz, hacer_zip):
    """Los usuarios de GitHub no distinguen mayúsculas: `Ana` y `ana` son la misma persona."""
    folder = escribir_alta(raiz, hacer_zip(), autor="Ana")
    assert codigos(revisar(raiz, folder)) == []


def test_ix107_portada_que_no_sirve(raiz, hacer_zip):
    folder = escribir_alta(raiz, hacer_zip())
    p = os.path.join(raiz, "paquetes", folder, "portada.png")
    open(p, "wb").write(b"\x89PNG\r\n\x1a\n" + b"0" * (C.TOPE_PORTADA_BYTES + 1))
    assert any("tope" in h.mensaje for h in revisar(raiz, folder) if h.codigo == "IX107")
    open(p, "wb").write(b"GIF89a....")
    assert any("cabecera" in h.mensaje for h in revisar(raiz, folder) if h.codigo == "IX107")
    open(p, "wb").write(b"\x89PNG\r\n\x1a\n" + b"basura")
    assert any("no es una imagen legible" in h.mensaje for h in revisar(raiz, folder) if h.codigo == "IX107")


def test_ix107_portada_enorme_en_pixeles(raiz, hacer_zip):
    folder = escribir_alta(raiz, hacer_zip())
    png(os.path.join(raiz, "paquetes", folder, "portada.png"), size=(C.TOPE_PORTADA_PX + 1, 8))
    assert any("lado máximo" in h.mensaje for h in revisar(raiz, folder))


def test_ix107_tamano_distinto_solo_avisa(raiz, hacer_zip):
    folder = escribir_alta(raiz, hacer_zip())
    png(os.path.join(raiz, "paquetes", folder, "portada.png"), size=(800, 800))
    h = revisar(raiz, folder)
    assert codigos(h) == [] and codigos(h, "aviso") == ["IX107"]


def test_ix108_fuente_por_url_no_se_acepta(raiz, hacer_zip):
    folder = escribir_alta(raiz, hacer_zip(), github=None, fuente={"tipo": "url", "url": "https://example.org/p.zip", "sha256": "a" * 64})
    h = revisar(raiz, folder)
    assert codigos(h) == ["IX108"]            # y nada más: no se duplica con «falta github»


def test_ix106_bloqueado_por_id_y_por_autor(raiz, hacer_zip):
    folder = escribir_alta(raiz, hacer_zip())
    por_id = [B.Block(id="mi-paquete", motivo="reclamo", fecha="2026-11-01")]
    por_autor = [B.Block(autor="ANA")]
    assert any(h.codigo == "IX106" and "se rechaza el alta" in h.mensaje for h in revisar(raiz, folder, por_id))
    assert codigos(revisar(raiz, folder, por_autor)) == ["IX106"]
    assert codigos(revisar(raiz, folder, [B.Block(id="otro-paquete")])) == []


def test_cli_codigos_de_salida(raiz, hacer_zip, capsys):
    escribir_alta(raiz, hacer_zip())
    assert VA.main(["--raiz", raiz]) == 0
    assert "1 carpeta(s) revisadas: 0 error(es)" in capsys.readouterr().out
    escribir_alta(raiz, hacer_zip(pid="otro", github="ana/otro"), licencia="inventada")
    assert VA.main(["--raiz", raiz]) == 1
    assert VA.main(["--raiz", raiz, "--solo", "ana@mi-paquete"]) == 0                    # `--solo` limita a lo que cambió el PR


def test_cli_bloqueados_roto_es_error(raiz, hacer_zip, capsys):
    escribir_alta(raiz, hacer_zip())
    open(os.path.join(raiz, "bloqueados.json"), "w").write('{"no": "una lista"}')
    assert VA.main(["--raiz", raiz]) == 1
    assert "IX106" in capsys.readouterr().err


def test_las_categorias_del_script_son_las_del_esquema_y_las_de_afm():
    """La lista cerrada de categorías (decisión 4) no puede divergir entre el CI, el esquema y la app."""
    from afm import paquete as Q
    assert set(C.CATEGORIAS) == set(Q.CATEGORIES)
