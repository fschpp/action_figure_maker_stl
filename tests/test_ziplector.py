"""D08 · lector endurecido de zips (PLAN_catalogos_online.md §5.7): ningún caso escribe fuera del staging."""
import io
import os
import stat
import zipfile

import pytest

from afm import ziplector as Z


def make(tmp_path, entries, name="a.zip"):
    """entries: lista de (nombre, bytes | ZipInfo-kwargs). Devuelve la ruta."""
    path = str(tmp_path / name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in entries:
            n, data, *rest = item
            zi = zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            if rest:
                zi.external_attr = rest[0]
            zf.writestr(zi, data)
    return path


def codes(path, **kw):
    with Z.open_zip(path) as zf:
        return [f.code for f in Z.inspect(zf, **kw).findings]


def test_clean_zip_has_no_findings(tmp_path):
    p = make(tmp_path, [("paquete.json", b"{}"), ("LICENSE", b"x"), ("piezas/brazo/a@1.stl", b"s"), ("previews/portada.png", b"p")])
    assert codes(p) == []


@pytest.mark.parametrize("name", ["../fuera.json", "/abs.json", "a/../../b.json", "C:/x.json", "a\\b.json", "a\x01b.json", "a//b.json"])
def test_dangerous_paths_are_ck302(tmp_path, name):
    assert codes(make(tmp_path, [(name, b"x")])) == ["CK302"]


def test_symlink_is_ck302(tmp_path):
    p = make(tmp_path, [("link.json", b"/etc/passwd", (stat.S_IFLNK | 0o777) << 16)])
    assert codes(p) == ["CK302"]


def test_encrypted_flag_is_ck302(tmp_path):
    p = make(tmp_path, [("a.json", b"x")])
    raw = bytearray(open(p, "rb").read())
    i = raw.find(b"PK\x03\x04")
    raw[i + 6] |= 1                                   # bit de cifrado en el encabezado local…
    j = raw.find(b"PK\x01\x02")
    raw[j + 8] |= 1                                   # …y en el directorio central (el que lee zipfile)
    open(p, "wb").write(bytes(raw))
    assert codes(p) == ["CK302"]


def test_case_duplicates_are_ck302(tmp_path):
    p = make(tmp_path, [("README.md", b"a"), ("readme.md", b"b")])
    assert codes(p) == ["CK302"]


def test_nfc_nfd_duplicates_are_ck302(tmp_path):
    p = make(tmp_path, [("piezas/brazo/\u00e9.stl", b"a"), ("piezas/brazo/e\u0301.stl", b"b")])
    assert codes(p) == ["CK302"]


@pytest.mark.parametrize("name", ["run.sh", "x.py", "a.exe", "m.fbx", "sin_extension", "sub/LICENSE"])
def test_extension_not_allowed_is_ck301(tmp_path, name):
    assert codes(make(tmp_path, [(name, b"x")])) == ["CK301"]


def test_fbx_only_when_allowed(tmp_path):
    p = make(tmp_path, [("piezas/cabeza/a@1.fbx", b"x")])
    assert codes(p) == ["CK301"] and codes(p, allow_fbx=True) == []


def test_nested_zip_is_ck301(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("a.json", "{}")
    assert codes(make(tmp_path, [("otro.zip", inner.getvalue())])) == ["CK301"]


def test_too_many_files_is_ck303(tmp_path):
    p = make(tmp_path, [(f"f{i}.json", b"x") for i in range(5)])
    with Z.open_zip(p) as zf:
        assert [f.code for f in Z.inspect(zf, max_files=3).findings] == ["CK303"]


def test_per_file_and_total_caps_are_ck303(tmp_path):
    p = make(tmp_path, [("a.json", b"x" * 2000), ("b.json", b"x" * 2000)])
    with Z.open_zip(p) as zf:
        assert [f.code for f in Z.inspect(zf, max_file_bytes=1000).findings] == ["CK303", "CK303"]
        assert [f.code for f in Z.inspect(zf, max_total_bytes=3000).findings] == ["CK303"]


def test_zip_bomb_ratio_is_ck303(tmp_path):
    p = make(tmp_path, [("bomba.json", b"\0" * (8 * 1024 * 1024))])           # 8 MB de ceros comprimen > 1000:1
    assert codes(p) == ["CK303"]


def test_extract_refuses_when_inspection_has_findings(tmp_path):
    p = make(tmp_path, [("../x.json", b"x")])
    with Z.open_zip(p) as zf:
        with pytest.raises(Z.ZipError):
            Z.extract_all(zf, str(tmp_path / "st"), Z.inspect(zf))
    assert not (tmp_path / "x.json").exists()


def test_extract_writes_only_inside_staging(tmp_path):
    p = make(tmp_path, [("paquete.json", b"{}"), ("piezas/brazo/a@1.stl", b"stl")])
    st = tmp_path / "st"
    st.mkdir()
    with Z.open_zip(p) as zf:
        got = Z.extract_all(zf, str(st), Z.inspect(zf))
    assert got == ["paquete.json", "piezas/brazo/a@1.stl"]
    assert (st / "piezas" / "brazo" / "a@1.stl").read_bytes() == b"stl"
    assert sorted(os.listdir(tmp_path)) == ["a.zip", "st"]


def test_extract_stops_when_header_lies_about_size(tmp_path):
    p = make(tmp_path, [("a.json", b"x" * 5000)])
    st = tmp_path / "st"
    st.mkdir()
    with Z.open_zip(p) as zf:
        insp = Z.inspect(zf)
        insp.entries[0].size = 100                     # el encabezado «miente»
        with pytest.raises(Z.ZipError, match="más de lo que declara"):
            Z.extract_all(zf, str(st), insp)


def test_extract_never_overwrites(tmp_path):
    p = make(tmp_path, [("a.json", b"x")])
    st = tmp_path / "st"
    st.mkdir()
    (st / "a.json").write_text("previo")
    with Z.open_zip(p) as zf:
        with pytest.raises(FileExistsError):
            Z.extract_all(zf, str(st), Z.inspect(zf))
    assert (st / "a.json").read_text() == "previo"


def test_not_a_zip(tmp_path):
    (tmp_path / "x.zip").write_text("no soy un zip")
    with pytest.raises(Z.ZipError):
        Z.open_zip(str(tmp_path / "x.zip"))


def test_only_revisa_solo_lo_pedido_y_no_cuenta_lo_demas(tmp_path):
    """`only` (D07): lo que no se va a extraer no cuenta para los topes ni genera hallazgos."""
    path = tmp_path / "x.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("linea.json", "{}")
        z.writestr("derivado.exe", b"x")
        z.writestr("../fuera.txt", b"x")
    with zipfile.ZipFile(path) as z:
        assert {f.code for f in Z.inspect(z).findings} == {"CK301", "CK302"}
        res = Z.inspect(z, only={"linea.json"}, max_files=1)
        assert res.ok and [e.name for e in res.entries] == ["linea.json"]
