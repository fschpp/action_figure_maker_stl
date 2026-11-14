from afm.gui import hero_wizard as H


def test_parse_number():
    assert H.parse_number("", "x") is None and H.parse_number(" 12,5 ", "x") == 12.5
    try:
        H.parse_number("abc", "Coronilla")
    except ValueError as e:
        assert "Coronilla" in str(e)
    else:
        raise AssertionError


def test_validate_pages(tmp_path):
    stl, pat = tmp_path / "h.stl", tmp_path / "g.proyecto"
    v = {"stl": "", "patron": "", "out": "", "corona": "", "entrepierna": ""}
    assert "STL" in H.validate_page(0, v)
    v["stl"] = str(stl)
    assert "No existe el STL" in H.validate_page(0, v)
    stl.write_text("x")
    assert H.validate_page(0, v) is None
    assert "patrón" in H.validate_page(1, v)
    v["patron"] = str(pat)
    assert "No existe el proyecto" in H.validate_page(1, v)
    pat.write_text("{}")
    assert H.validate_page(1, v) is None and H.validate_page(2, v) is None
    v["corona"] = "alto"
    assert "Coronilla" in H.validate_page(2, v)


def test_summary_and_default_output(tmp_path):
    v = {"stl": str(tmp_path / "h.stl"), "patron": str(tmp_path / "g.proyecto"), "out": "", "corona": "1,2", "entrepierna": ""}
    text = "\n".join(H.summary_lines(v))
    assert str(tmp_path / "h.proyecto") in text and "Coronilla: 1,2" in text and "Entrepierna: la detecta" in text
