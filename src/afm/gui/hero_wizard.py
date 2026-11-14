"""Asistente «Nuevo héroe desde el patrón» en tres pantallas (UX etapa 6).

1. El héroe: STL y dónde guardar el proyecto.
2. El patrón: de qué figura de referencia (p. ej. Güemes) se copian los planos.
3. Resumen: opciones (coronilla, entrepierna) y «Crear y abrir».

La lógica (validación y resumen) no usa GTK y se prueba aparte; `open_hero_wizard` arma las ventanas.
"""
from __future__ import annotations

import os

PAGES = ("El héroe", "El patrón", "Resumen")


def parse_number(text: str, what: str) -> float | None:
    """Número opcional con coma o punto decimal; None si está vacío. ValueError con mensaje si no es un número."""
    txt = (text or "").strip().replace(",", ".")
    if not txt:
        return None
    try:
        return float(txt)
    except ValueError:
        raise ValueError(f"{what}: «{txt}» no es un número") from None


def default_output(stl: str) -> str:
    return os.path.splitext(stl)[0] + ".proyecto" if stl else ""


def validate_page(page: int, values: dict) -> str | None:
    """Motivo por el que no se puede avanzar desde la pantalla `page` (0 a 2), o None si está todo bien."""
    stl = (values.get("stl") or "").strip()
    patron = (values.get("patron") or "").strip()
    if page >= 0:
        if not stl:
            return "Elegí el STL del héroe."
        if not os.path.isfile(stl):
            return f"No existe el STL del héroe: «{stl}»."
    if page >= 1:
        if not patron:
            return "Elegí el proyecto del patrón."
        if not os.path.isfile(patron):
            return f"No existe el proyecto del patrón: «{patron}»."
    if page >= 2:
        if not (values.get("out") or "").strip() and not default_output(stl):
            return "Elegí dónde guardar el proyecto del héroe."
        try:
            parse_number(values.get("corona", ""), "Coronilla")
            parse_number(values.get("entrepierna", ""), "Entrepierna")
        except ValueError as exc:
            return str(exc)
    return None


def summary_lines(values: dict) -> list[str]:
    """Texto de la pantalla 3: qué se va a hacer con lo elegido."""
    stl = (values.get("stl") or "").strip()
    out = (values.get("out") or "").strip() or default_output(stl)
    lines = [f"Héroe: {os.path.basename(stl)}", f"Patrón: {os.path.basename((values.get('patron') or '').strip())}",
             f"Proyecto nuevo: {out}"]
    corona = (values.get("corona") or "").strip()
    entre = (values.get("entrepierna") or "").strip()
    lines.append(f"Coronilla: {corona}" if corona else "Coronilla: la detecta el programa")
    lines.append(f"Entrepierna: {entre} mm" if entre else "Entrepierna: la detecta el programa")
    lines.append("Se copian los planos del patrón y se vuelven a dibujar las siluetas sobre la malla del héroe.")
    return lines


def open_hero_wizard(window) -> None:
    """Abre el asistente sobre la ventana principal `window` (usa su controlador, `_run_task` y `_new_from_pattern_done`)."""
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, GLib, Gtk

    if window.ctrl.busy:
        return
    win = Gtk.Window(title="Nuevo héroe desde el patrón", transient_for=window, modal=True, default_width=640)
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
    values: dict = {"stl": "", "patron": "", "out": "", "corona": "", "entrepierna": ""}
    entries: dict = {}
    page = {"n": 0}

    title = Gtk.Label(xalign=0)
    title.add_css_class("title-3")
    stack = Gtk.Stack(vexpand=True)

    def pick_file(key, title_txt, patterns, save=False):
        dlg = Gtk.FileDialog(title=title_txt)
        flt = Gtk.FileFilter()
        flt.set_name(", ".join(patterns))
        for pt in patterns:
            flt.add_pattern(pt)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        dlg.set_filters(filters)

        def finished(d, res):
            try:
                f = (d.save_finish if save else d.open_finish)(res)
            except GLib.Error:
                return
            entries[key].set_text(f.get_path())

        (dlg.save if save else dlg.open)(win, None, finished)

    def file_row(parent, key, label, placeholder, patterns, save=False):
        parent.append(Gtk.Label(label=label, xalign=0))
        row = Gtk.Box(spacing=6)
        ent = Gtk.Entry(placeholder_text=placeholder, hexpand=True)
        btn = Gtk.Button(label="Elegir…")
        btn.connect("clicked", lambda *_: pick_file(key, label, patterns, save))
        row.append(ent)
        row.append(btn)
        parent.append(row)
        entries[key] = ent
        ent.connect("changed", lambda e, k=key: values.__setitem__(k, e.get_text()))

    p1 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    p1.append(Gtk.Label(label="El STL que generaste para el héroe (con sus proporciones propias).", xalign=0, wrap=True))
    file_row(p1, "stl", "STL del héroe", "p. ej. sanmartin.stl", ["*.stl", "*.STL"])
    file_row(p1, "out", "Guardar el proyecto como (opcional)", "por defecto, junto al STL", ["*.proyecto"], save=True)
    entries["stl"].connect("changed", lambda e: entries["out"].set_placeholder_text(default_output(e.get_text()) or "por defecto, junto al STL"))
    stack.add_named(p1, "0")

    p2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    p2.append(Gtk.Label(label="La figura de referencia (p. ej. Güemes): de ahí se copian los planos de brazos, piernas y cuello.",
                        xalign=0, wrap=True))
    file_row(p2, "patron", "Proyecto del patrón", "p. ej. guemes.proyecto", ["*.proyecto", "*.json"])
    stack.add_named(p2, "1")

    p3 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    summary = Gtk.Label(xalign=0, wrap=True, max_width_chars=80)
    p3.append(summary)
    p3.append(Gtk.Label(label="Opciones (solo si hace falta):", xalign=0, margin_top=6))
    for key, label, hint in (("corona", "Coronilla", "altura en unidades del STL; si lleva sombrero o plumas"),
                             ("entrepierna", "Entrepierna", "altura en mm; si lleva abrigo largo")):
        row = Gtk.Box(spacing=6)
        row.append(Gtk.Label(label=label + ":", xalign=0, width_chars=12))
        ent = Gtk.Entry(placeholder_text=hint, hexpand=True)
        ent.connect("changed", lambda e, k=key: (values.__setitem__(k, e.get_text()), refresh_summary()))
        entries[key] = ent
        row.append(ent)
        p3.append(row)
    stack.add_named(p3, "2")

    status = Gtk.Label(label="", xalign=0, wrap=True, max_width_chars=80)
    status.add_css_class("error")
    bar = Gtk.Box(spacing=6, halign=Gtk.Align.END)
    back = Gtk.Button(label="Atrás")
    nxt = Gtk.Button(label="Siguiente")
    nxt.add_css_class("suggested-action")
    go = Gtk.Button(label="Crear y abrir")
    go.add_css_class("suggested-action")
    cancel = Gtk.Button(label="Cancelar")
    for b in (cancel, back, nxt, go):
        bar.append(b)

    def refresh_summary():
        summary.set_text("\n".join(summary_lines(values)))

    def show(n: int):
        page["n"] = n
        stack.set_visible_child_name(str(n))
        title.set_text(f"Paso {n + 1} de {len(PAGES)}: {PAGES[n]}")
        back.set_visible(n > 0)
        nxt.set_visible(n < len(PAGES) - 1)
        go.set_visible(n == len(PAGES) - 1)
        status.set_text("")
        if n == 2:
            refresh_summary()

    def on_next(*_):
        why = validate_page(page["n"], values)
        if why:
            status.set_text(why)
            return
        show(page["n"] + 1)

    def on_go(*_):
        why = validate_page(2, values)
        if why:
            status.set_text(why)
            return
        stl, patron = values["stl"].strip(), values["patron"].strip()
        out = values["out"].strip() or default_output(stl)
        try:
            job = window.ctrl.new_from_pattern_job(patron, stl, out, parse_number(values["corona"], "Coronilla"),
                                                   parse_number(values["entrepierna"], "Entrepierna"))
        except ValueError as exc:
            status.set_text(str(exc))
            return
        win.close()
        window.ctrl.busy = True
        window._run_task("Creando el héroe desde el patrón…", job, lambda res, err: window._new_from_pattern_done(res, err, stl),
                         "Héroe creado")

    back.connect("clicked", lambda *_: show(page["n"] - 1))
    nxt.connect("clicked", on_next)
    go.connect("clicked", on_go)
    cancel.connect("clicked", lambda *_: win.close())
    for w in (title, stack, status, bar):
        root.append(w)
    win.set_child(root)
    show(0)
    win.present()
