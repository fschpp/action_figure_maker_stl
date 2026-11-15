"""Internacionalización: es / en / pt con las mismas claves y variables, y fallback al español."""
import json
import os
import re

import pytest

from afm import i18n

LOCALES = os.path.join(os.path.dirname(i18n.__file__), "locales")


def _flat(d, p=""):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _flat(v, p + k + ".")
        else:
            yield p + k, v


def _load(lang):
    with open(os.path.join(LOCALES, f"{lang}.json"), encoding="utf-8") as fh:
        return dict(_flat(json.load(fh)))


@pytest.fixture(autouse=True)
def _restore_language():
    yield
    i18n.set_language("es")


def test_same_keys_in_every_language():
    es = _load("es")
    for lang in ("en", "pt"):
        assert set(_load(lang)) == set(es), lang


def test_placeholders_match_spanish():
    ph = lambda s: sorted(re.findall(r"%\{\w+\}", s))
    es = _load("es")
    for lang in ("en", "pt"):
        for k, v in _load(lang).items():
            assert ph(v) == ph(es[k]), (lang, k)
            assert v.count("%%") == es[k].count("%%"), (lang, k)


def test_every_key_used_in_code_exists():
    es = _load("es")
    pat = re.compile(r'\b(?:tr|lz|tr_in)\(\s*(?:\w+\s*,\s*)?"([a-z_0-9]+\.[a-z_0-9]+)"')
    src = os.path.dirname(i18n.__file__)
    used = set()
    for root, _d, files in os.walk(src):
        for f in files:
            if f.endswith(".py") and f != "i18n.py":
                with open(os.path.join(root, f), encoding="utf-8") as fh:
                    used |= set(pat.findall(fh.read()))
    assert used - set(es) == set()


def test_default_is_spanish_and_switching_works():
    assert i18n.get_language() == "es"
    assert i18n.tr("common.guardar") == "Guardar"
    i18n.set_language("en")
    assert i18n.tr("common.guardar") == "Save"
    i18n.set_language("pt")
    assert i18n.tr("common.guardar") == "Salvar"
    assert i18n.set_language("xx") == "es"


def test_variables_and_msg():
    i18n.set_language("en")
    assert "3" in i18n.tr("controller.eliminadas_n_pieza_s_join", n=3, join="a")
    m = i18n.lz("common.guardar")
    assert m == "Guardar"                         # se compara como el español
    assert i18n.loc(m) == "Save"                  # y se traduce al mostrarlo


def test_initial_language_priority(monkeypatch):
    monkeypatch.delenv("AFM_LANG", raising=False)
    assert i18n.initial_language("en", "pt") == "en"
    assert i18n.initial_language(None, "pt") == "pt"
    monkeypatch.setenv("AFM_LANG", "en")
    assert i18n.initial_language(None, "pt") == "en"


def test_missing_key_never_raises():
    assert i18n.tr("no.existe") == "no.existe"


def test_no_placeholder_collides_with_library_arguments():
    for k, v in _load("es").items():
        assert not ({"key", "locale", "count", "default"} & set(re.findall(r"%\{(\w+)\}", v))), k


def test_param_placeholder_formats():
    i18n.set_language("en")
    assert "foo" in i18n.tr("controller.parametro_desconocido_key", param="foo")
