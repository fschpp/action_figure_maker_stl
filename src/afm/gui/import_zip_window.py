"""Importar un paquete `.zip` desde un archivo (D22): «Compartir → Importar paquete .zip…» y arrastrar y soltar sobre la ventana principal.

Los dos caminos llegan **al mismo diálogo** que «Instalar» de «Explorar» (`install_confirm_window.InstallConfirmDialog`, D19), con un
`import_zip.LocalZipFlow` en lugar de uno del índice. Acá solo hay pegamento GTK: elegir el archivo, recibir lo que se soltó, mirar de qué se trata y
abrir el diálogo. Mirar el archivo no escribe nada (`inspect_zip` no extrae) y el diálogo no escribe hasta que la persona aprieta «Instalar».

    open_import(parent, root, "/ruta/paquete.zip")                       # un zip: abre el diálogo (o avisa por qué no se puede)
    ImportQueue(parent, root_getter, on_installed).add(["a.zip", "b.zip", "x.stl"])      # varios: de a uno; lo que no es .zip se informa
    pick_zip(parent, lambda path: open_import(parent, root, path))        # selector de archivos con filtro .zip
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from ..i18n import tr  # noqa: E402
from . import catalog_online as CO  # noqa: E402
from . import import_zip as IZ  # noqa: E402

MAX_LISTED = 6                                                 # nombres de archivo ignorados que se muestran en el aviso


def _alert(parent: Gtk.Window | None, message: str, detail: str = "") -> None:
    Gtk.AlertDialog(message=message, detail=detail).show(parent)


def pick_zip(parent: Gtk.Window | None, on_picked) -> None:
    """Selector de archivos con filtro `.zip`; llama a `on_picked(ruta)` si la persona eligió uno (si cancela no pasa nada)."""
    dlg = Gtk.FileDialog(title=tr("import_zip_window.elegir_titulo"))
    flt = Gtk.FileFilter(name=tr("import_zip_window.filtro_zip"))
    flt.add_pattern("*.zip")
    flt.add_pattern("*.ZIP")
    store = Gio.ListStore.new(Gtk.FileFilter)
    store.append(flt)
    dlg.set_filters(store)

    def done(d, res) -> None:
        try:
            f = d.open_finish(res)
        except GLib.Error:                                       # cancelado
            return
        path = f.get_path()
        if path:
            on_picked(path)

    dlg.open(parent, None, done)


def open_import(parent: Gtk.Window | None, root: str, zip_path: str, *, on_installed=None, on_closed=None):
    """Abre el diálogo de confirmación para `zip_path`; `root` es el catálogo abierto (o «» y el paquete va a la carpeta de su línea).

    Devuelve el diálogo, o `None` si el archivo no se puede importar (no es un `.zip` legible, no trae `paquete.json` ni `linea.json`…): en ese caso se
    avisa con el motivo, no se tocó nada y `on_closed` se llama igual para que una cola siga con el próximo.
    """
    from .install_confirm_window import InstallConfirmDialog

    try:
        flow = IZ.LocalZipFlow(CO.OnlineState(urls=[], root=root or ""), zip_path)
    except IZ.ZipImportError as exc:
        import os

        _alert(parent, tr("import_zip_window.no_se_pudo", nombre=os.path.basename(zip_path)), str(exc))
        if on_closed is not None:
            on_closed()
        return None
    dlg = InstallConfirmDialog(parent, flow=flow, on_installed=on_installed)
    if on_closed is not None:
        dlg.connect("close-request", lambda *_: on_closed() or False)
    dlg.present()
    return dlg


class ImportQueue:
    """Lo que se soltó en la ventana: los `.zip` se abren **de a uno** (cada uno con su confirmación; los diálogos son modales) y lo demás se informa.

    `root` es una función (el catálogo abierto puede cambiar entre un zip y el siguiente); `on_installed(results)` se llama tras cada instalación.
    """

    def __init__(self, parent: Gtk.Window | None, root, on_installed=None):
        self.parent, self.root, self.on_installed = parent, root, on_installed
        self.pending: list[str] = []
        self.current = None
        self.busy = False

    def add(self, paths) -> tuple[list[str], list[str]]:
        """Encola los `.zip` de `paths` y avisa de lo que no lo es. Devuelve `(zips, otros)`."""
        zips, otros = IZ.split_dropped(paths)
        if otros:
            import os

            names = [os.path.basename(p) or p for p in otros]
            shown = ", ".join(names[:MAX_LISTED]) + (f" (+{len(names) - MAX_LISTED})" if len(names) > MAX_LISTED else "")
            _alert(self.parent, tr("import_zip_window.soltado_ignorado_titulo"), tr("import_zip_window.soltado_ignorado", archivos=shown))
        self.pending += zips
        if len(zips) > 1:
            _alert(self.parent, tr("import_zip_window.varios", n=len(zips)))
        if not self.busy:
            self._next()
        return zips, otros

    def _next(self) -> None:
        if not self.pending:
            self.busy, self.current = False, None
            return
        self.busy = True
        path = self.pending.pop(0)
        self.current = open_import(self.parent, self.root() or "", path, on_installed=self.on_installed, on_closed=self._closed)

    def _closed(self) -> None:
        GLib.idle_add(self._after_closed)

    def _after_closed(self) -> bool:
        self.busy = False
        self._next()
        return False


def attach_drop(window: Gtk.Window, queue: ImportQueue) -> Gtk.DropTarget:
    """Hace que la ventana acepte archivos soltados: los `.zip` van a la cola (misma confirmación que el menú), el resto se informa."""
    target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)

    def dropped(_t, value, _x, _y) -> bool:
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if not paths:
            return False
        queue.add(paths)
        return True

    target.connect("drop", dropped)
    window.add_controller(target)
    return target


__all__ = ["open_import", "pick_zip", "ImportQueue", "attach_drop"]
