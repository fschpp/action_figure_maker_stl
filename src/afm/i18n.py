"""Internacionalización de la interfaz (español, inglés y portugués) sobre `python-i18n`.

El español es el idioma de origen y el de reserva: si a un idioma le falta una clave se muestra el texto en español, y el
idioma por defecto al importar este módulo es SIEMPRE el español (así las pruebas y el motor no dependen del sistema).
La interfaz gráfica elige el idioma al arrancar (`app.main`) y lo puede cambiar desde la cabecera.

Uso:
    from afm.i18n import tr, lz, loc
    tr("window.guardar")                       # texto en el idioma actual
    tr("controller.pieza_n", n=3)              # con variables: «Pieza %{n}» en el JSON
    TITULO = lz("steps.figura")                # constante de módulo: se comporta como el texto en español...
    loc(TITULO)                                # ...y se traduce al mostrarla

Los textos viven en `afm/locales/<idioma>.json` (claves anidadas con punto; `%{var}` para variables; `%%` para un `%`).
"""
from __future__ import annotations

import locale as _locale
import os

import i18n as _i18n

SOURCE_LANGUAGE = "es"
DEFAULT_LANGUAGE = SOURCE_LANGUAGE
# código -> nombre del idioma escrito en ese mismo idioma (así se reconoce aunque la interfaz esté en otro)
LANGUAGES: dict[str, str] = {"es": "Español", "en": "English", "pt": "Português"}
LOCALES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")

_i18n.load_path[:] = [LOCALES_DIR]
_i18n.set("file_format", "json")
_i18n.set("filename_format", "{locale}.{format}")
_i18n.set("skip_locale_root_data", True)
_i18n.set("fallback", SOURCE_LANGUAGE)
_i18n.set("enable_memoization", True)
_i18n.set("error_on_missing_translation", False)
_i18n.set("error_on_missing_placeholder", False)
_i18n.set("locale", DEFAULT_LANGUAGE)


def normalize(code: str | None) -> str | None:
    """«pt_BR.UTF-8» -> «pt»; None si el idioma no está disponible."""
    if not code:
        return None
    base = str(code).replace("-", "_").split(".")[0].split("_")[0].lower()
    return base if base in LANGUAGES else None


def get_language() -> str:
    return _i18n.get("locale")


def set_language(code: str | None) -> str:
    """Cambia el idioma de los textos nuevos. Un código desconocido deja el español. Devuelve el idioma aplicado."""
    lang = normalize(code) or DEFAULT_LANGUAGE
    _i18n.set("locale", lang)
    return lang


def system_language() -> str:
    """Idioma del sistema (variable AFM_LANG, LANGUAGE, LC_ALL, LC_MESSAGES, LANG o la configuración regional) o español."""
    for var in ("AFM_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        for part in (os.environ.get(var) or "").split(":"):
            lang = normalize(part)
            if lang:
                return lang
    try:
        return normalize(_locale.getlocale()[0]) or DEFAULT_LANGUAGE
    except (ValueError, TypeError):
        return DEFAULT_LANGUAGE


def initial_language(cli: str | None = None, saved: str | None = None) -> str:
    """Idioma con el que arranca la interfaz: `--lang` > variable AFM_LANG > el elegido en la ventana > el del sistema."""
    return normalize(cli) or normalize(os.environ.get("AFM_LANG")) or normalize(saved) or system_language()


def tr(key: str, /, **values) -> str:
    """Texto de `key` en el idioma actual (o en español si falta); `values` rellena los `%{nombre}`. Nunca lanza."""
    try:
        if any(isinstance(v, Msg) for v in values.values()):     # una constante de módulo usada como variable
            values = {k: loc(v) for k, v in values.items()}
        return _i18n.t(key, **values)
    except Exception:  # noqa: BLE001 — una traducción rota no debe tumbar la interfaz
        return key


def tr_in(lang: str, key: str, /, **values) -> str:
    """Como `tr` pero en un idioma dado (para pruebas y para el selector)."""
    try:
        return _i18n.t(key, locale=lang, **values)
    except Exception:  # noqa: BLE001
        return key


class Msg(str):
    """Texto de una constante de módulo. Vale lo mismo que el original en español (igualdad, hash, `in`, formato), así la
    lógica que lo compara no cambia, pero recuerda su clave: `loc(msg)` lo devuelve en el idioma actual."""

    key: str

    def __new__(cls, key: str):
        obj = super().__new__(cls, tr_in(SOURCE_LANGUAGE, key))
        obj.key = key
        return obj


lz = Msg


def loc(text):
    """Traduce un `Msg` (constante de módulo); cualquier otro texto vuelve igual."""
    return tr(text.key) if isinstance(text, Msg) else text
