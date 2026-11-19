"""T26: los textos de la feature de color existen en es/en/pt y las traducciones son texto real, no copias del español."""
import json
import os

from afm import i18n

LOCALES = os.path.join(os.path.dirname(i18n.__file__), "locales")
# Claves cuyo texto es el mismo en todos los idiomas (palabra o formato neutro): no son copias olvidadas.
NEUTRAL = {"color.panel_fila": {"en", "pt"}, "color.error_fbx": {"en"}}


def _flat(d, p=""):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from _flat(v, p + k + ".")
        else:
            yield p + k, v


def _load(lang):
    with open(os.path.join(LOCALES, f"{lang}.json"), encoding="utf-8") as fh:
        return dict(_flat(json.load(fh)))


def test_color_keys_present_in_three_languages():
    es, en, pt = _load("es"), _load("en"), _load("pt")
    keys = sorted(k for k in es if k.startswith("color."))
    assert len(keys) >= 25
    for lang, table in (("en", en), ("pt", pt)):
        assert [k for k in keys if k not in table] == [], lang
        copied = [k for k in keys if table[k] == es[k] and lang not in NEUTRAL.get(k, set())]
        assert copied == [], f"{lang}: copias del español {copied}"
        assert [k for k in keys if not table[k].strip()] == [], lang
