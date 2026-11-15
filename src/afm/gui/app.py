"""Punto de entrada de la aplicación de escritorio (`afm-gui`)."""
from __future__ import annotations
from ..i18n import LANGUAGES, initial_language, set_language, tr

import argparse
import sys


def _early_language(argv) -> str:
    """Fija el idioma antes de armar el `--help` (que ya sale traducido). `--lang` pisa a lo guardado."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang")
    known, _ = pre.parse_known_args(argv)
    try:
        from . import settings
        saved = settings.language()
    except Exception:  # noqa: BLE001 — las preferencias son una comodidad
        saved = None
    return set_language(initial_language(known.lang, saved))


def main(argv=None) -> int:
    _early_language(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="afm-gui", description=tr("app.action_figure_maker_gtk4"))
    ap.add_argument("archivo", nargs="?", help=tr("app.stl_o_proyecto_a_abrir_al"))
    ap.add_argument("--stl", help=tr("app.stl_a_usar_con_un_proyecto"))
    ap.add_argument("--lang", choices=sorted(LANGUAGES), help=tr("app.idioma_de_la_interfaz"))
    args = ap.parse_args(argv)
    try:
        from .window import Application
    except (ImportError, ValueError) as e:
        print(tr("app.faltan_dependencias", error=e), file=sys.stderr)
        return 1
    stl, project = args.stl, None
    if args.archivo:
        if args.archivo.lower().endswith(".stl"):
            stl = args.archivo
        else:
            project = args.archivo
    return Application(stl, project).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
