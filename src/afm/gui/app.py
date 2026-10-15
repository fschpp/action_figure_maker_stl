"""Punto de entrada de la aplicación de escritorio (`afm-gui`)."""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="afm-gui", description="Action Figure Maker (GTK4)")
    ap.add_argument("archivo", nargs="?", help="STL o .proyecto a abrir al iniciar")
    ap.add_argument("--stl", help="STL a usar con un .proyecto (si cambió de lugar)")
    args = ap.parse_args(argv)
    try:
        from .window import Application
    except (ImportError, ValueError) as e:
        print("Faltan dependencias de la interfaz (GTK4, PyGObject, moderngl).\n"
              "En Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 libgl1\n"
              "y: pip install 'action-figure-maker-stl[gui]'\n"
              f"Detalle: {e}", file=sys.stderr)
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
