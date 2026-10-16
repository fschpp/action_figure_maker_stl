#!/usr/bin/env bash
# Instala Action Figure Maker (afm / afm-gui) para un usuario, en Debian/Ubuntu y derivados.
#
#   ./packaging/instalar.sh              instala en ~/.local/share/action-figure-maker
#   ./packaging/instalar.sh /otra/ruta   instala en otra carpeta
#   ./packaging/instalar.sh --dry-run    solo muestra lo que haría
#
# Crea un entorno virtual con el Python del SISTEMA y --system-site-packages: así usa el PyGObject / GTK4
# que instala apt (un Python distinto del de `python3-gi` falla con «No module named 'gi'»).
# Deja `afm` y `afm-gui` en ~/.local/bin y una entrada «Action Figure Maker» en el menú de aplicaciones.
set -euo pipefail

DRY=0
DEST=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
        *) DEST="$arg" ;;
    esac
done
DEST="${DEST:-$HOME/.local/share/action-figure-maker}"
PY="${AFM_PYTHON:-/usr/bin/python3}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"

run() { if [ "$DRY" = 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }

if [ ! -x "$PY" ]; then
    echo "No se encontró $PY. Indicá otro Python con AFM_PYTHON=/ruta/a/python3" >&2
    exit 1
fi
if [ "$DRY" = 0 ] && ! "$PY" -c 'import gi; gi.require_version("Gtk", "4.0")' 2>/dev/null; then
    cat >&2 <<MSG
Falta GTK4 para $PY. En Debian/Ubuntu:
    sudo apt install python3-venv python3-gi python3-gi-cairo gir1.2-gtk-4.0 libgl1 libegl1
y volvé a correr este script.
MSG
    exit 1
fi

echo "Instalando en $DEST (Python: $PY)"
run "$PY" -m venv --system-site-packages "$DEST"
run "$DEST/bin/pip" install --upgrade pip
run "$DEST/bin/pip" install "$REPO[gui]"

run mkdir -p "$BIN" "$APPS"
run ln -sf "$DEST/bin/afm" "$BIN/afm"
run ln -sf "$DEST/bin/afm-gui" "$BIN/afm-gui"

DESKTOP="$APPS/action-figure-maker.desktop"
if [ "$DRY" = 1 ]; then
    echo "[dry-run] escribir $DESKTOP"
else
    cat > "$DESKTOP" <<ENTRY
[Desktop Entry]
Type=Application
Name=Action Figure Maker
Comment=Convierte un STL de una pieza en una figura articulada
Exec=$DEST/bin/afm-gui %f
Terminal=false
Categories=Graphics;3DGraphics;
MimeType=model/stl;application/sla;
ENTRY
fi

echo "Listo. Abrilo desde el menú («Action Figure Maker») o con: afm-gui figura.stl"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "Ojo: $BIN no está en el PATH; agregalo o usá $DEST/bin/afm-gui" ;;
esac
