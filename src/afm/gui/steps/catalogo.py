"""Pestaña «Catálogo» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..widgets import collapsible as _collapsible
from ..widgets import help_note as _help


def build(win) -> None:
    """Agrega la pestaña «Catálogo» a `win.tabs` (win = la `MainWindow`)."""
    box = win._tab_page("Catálogo")
    win.btn_cat_new = win._button("Crear catálogo de la línea (carpeta nueva)…", win._on_catalog_new)
    win.btn_cat_open = win._button("Abrir catálogo de la línea (carpeta)…", win._on_catalog_open)
    win.btn_cat_hero = win._button("Nuevo héroe desde el patrón (p. ej. Güemes)…", win._on_new_from_pattern)
    win.btn_cat_hero.set_tooltip_text("Crea el proyecto de un héroe nuevo copiando los planos del proyecto de la figura patrón (cintura, cuello, "
                                       "rodillas, codos, muñeca, pasadores y parámetros) y volviendo a dibujar las siluetas sobre la malla del "
                                       "héroe. Lo abre y muestra una imagen para revisar que los planos caigan bien. No copia recortes, piezas "
                                       "de catálogo ni escalas del patrón.")
    win.lbl_catalog = Gtk.Label(label="Sin catálogo abierto", xalign=0, wrap=True)
    win.btn_cat_add = win._button("Catalogar esta figura…", win._on_catalog_add)
    win.btn_cat_guide = win._button("Guía de cortes (cuánto del catálogo los acepta)…", win._on_cut_guide)
    win.btn_cat_guide.set_tooltip_text("Mide la cara de cada corte (hombro, cadera, cuello) sin generar nada y cuenta cuántas piezas "
                                        "del catálogo abierto la aceptan, como pieza de esta figura y como torso que recibe piezas de otras.")
    win.btn_cat_fitcm = win._button("Ajustar cintura y muñeca al patrón…", win._on_fit_waist_wrist)
    win.btn_cat_fitcm.set_tooltip_text("Mueve el plano de la cintura y el de la muñeca de esta figura hasta que sus caras tengan el tamaño de la "
                                        "pelvis y de la mano del patrón (p. ej. Güemes) del catálogo abierto. Muestra qué cambiaría antes de aplicarlo.")
    win.btn_cat_manage = win._button("Administrar catálogo (piezas, certificación, compatibilidad)…", win._on_catalog_manage)
    win.btn_cat_build = win._button("Armar figura desde catálogo (sin STL)…", win._on_catalog_build)
    win.btn_cat_forget = win._button("Olvidar el catálogo recordado", lambda *_: win.ctrl.forget_catalog())
    win.btn_cat_forget.set_tooltip_text("Cierra el catálogo y borra lo recordado: la próxima vez hay que abrirlo a mano. "
                                         "No toca la carpeta ni el proyecto.")
    win.btn_cat_review = win._button("Revisar las piezas elegidas (sin generar)", win._on_catalog_review)
    win.btn_cat_scale = win._button("Escalar la figura a la altura de la línea", lambda *_: win.ctrl.scale_to_line())
    win.btn_cat_align = win._button("Alinear todos los cortes a la línea", win._on_align_all)
    win.chk_part_preview = Gtk.CheckButton(label="Dibujar las piezas elegidas antes de generar",
                                            active=win.ctrl.show_part_preview)
    win.chk_part_preview.connect("toggled", lambda b: None if win._updating else win.ctrl.set_show_part_preview(b.get_active()))
    win.btn_divide_model = win._button("Dividir figura actual y ajustar piezas…", win._on_divide_model)
    win.btn_proportions = win._button("Comparar proporciones con un patrón…", win._on_proportions)
    win.btn_proportions.set_tooltip_text("Tu figura en piezas al lado del cuerpo de una figura patrón del catálogo (p. ej. Güemes), en tres "
                                          "vistas, para ir igualando la escala de cada pieza viendo el resultado.")
    win.btn_reset_piece_scales = win._button("Restablecer escalas por pieza", lambda *_: win.ctrl.reset_part_scales())
    win.parts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    win._parts_sig = None

    # ---- orden nuevo (etapa 6): primero los lugares de la figura (lo más usado), la gestión del catálogo en un menú
    places = _collapsible(box, "Lugares de la figura")
    places.append(win.lbl_catalog)
    bar = Gtk.Box(spacing=6)
    pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    popover = Gtk.Popover(child=pop_box)
    for b in (win.btn_cat_new, win.btn_cat_open, win.btn_cat_add, win.btn_cat_hero, win.btn_cat_guide, win.btn_cat_fitcm,
              win.btn_cat_manage, win.btn_cat_build, win.btn_cat_forget):
        b.add_css_class("flat")
        b.set_halign(Gtk.Align.FILL)
        if b.get_child() is not None:
            b.get_child().set_halign(Gtk.Align.START)
        pop_box.append(b)
    win.btn_cat_forget.add_css_class("destructive-action")      # «Olvidar» ya no queda junto a las acciones frecuentes
    win.menu_catalog = Gtk.MenuButton(label="Catálogo ▾", popover=popover)
    win.menu_catalog.set_tooltip_text("Abrir, crear, administrar o catalogar; guía de cortes y ajuste al patrón")
    bar.append(win.menu_catalog)
    bar.append(win.btn_cat_review)
    places.append(bar)
    places.append(Gtk.Label(label="Pieza de cada lugar (al cambiar hay que generar)", xalign=0, css_classes=["dim-label"]))
    places.append(win.parts_box)

    sec = _collapsible(box, "Ajustes de la figura", expanded=False)
    for w in (win.btn_cat_scale, win.btn_cat_align, win.chk_part_preview, win.btn_divide_model, win.btn_proportions,
              win.btn_reset_piece_scales):
        sec.append(w)

    _help(box, "Elegí en cada lugar la pieza del modelo o una del catálogo. «Revisar» mide el acople sin generar; "
               "✓ ok · ⚠ aviso · ✗ no. Todo el detalle está en «Ayuda del catálogo».")
    long_sec = _collapsible(box, "Ayuda del catálogo", expanded=False, sub=True)
    _help(long_sec, "«Crear catálogo» arma una carpeta nueva para la línea (nombre y altura de figura; es lo mismo que «afm catalogo "
               "crear») y la deja abierta. Si ya tenés una, abrila con «Abrir catálogo». «Catalogar esta figura» (elegís los lugares y si va como versión nueva; con cabeza de reemplazo guarda ESA cabeza) guarda "
               "sus piezas crudas (cabeza, torso, brazo, pierna, con los planos y siluetas que ya definiste) para usarlas en "
               "otras figuras. En cada lugar elegí «la del modelo» o una pieza del catálogo: «Generar figura» la pone en el "
               "puerto que el modelo tiene en ese lugar, sin escalarla, y la fila muestra cómo acopla (ok / aviso / no, "
               "IoU y escalón entre las caras). La mano de catálogo se acopla en la muñeca del brazo de catálogo. «Revisar» mide el acople de cada pieza SIN "
               "generar y la dibuja ya colocada (verde = ok, ámbar = aviso, rojo = no); «Ver por qué» superpone las dos "
               "caras. Una pelvis sola conserva el torso, brazos y piernas del modelo: definí antes el plano de cintura. "
               "«Armar figura desde catálogo» (también en la barra de arriba) arma una figura NUEVA sin abrir un STL: una pieza por lugar, "
               "cada una marcada con cómo acopla a la ya elegida, y exporta las piezas, la figura ensamblada y la receta. «Administrar catálogo» abre una ventana con todas las versiones de pieza, una vista previa 3/4 de cada una "
               "y el borrado de una o varias (útil si se catalogó desde un proyecto equivocado); los lugares de esta figura que usaban una pieza "
               "borrada vuelven a la del modelo. «Dividir figura actual» separa las piezas crudas para ajustar su escala (una sola o por eje X, Y, Z; sirve para la pieza del modelo y para la de catálogo); los puertos acompañan el cambio "
               "y los pasadores se regeneran con las medidas del proyecto. Brazos y piernas se ajustan por par espejado. "
               "«Restablecer» vuelve a las proporciones originales. Con un torso de catálogo la figura sale entera del catálogo. El catálogo se reabre solo "
               "al cargar la figura.")

    # --------------------------------------------------------- Miembros
