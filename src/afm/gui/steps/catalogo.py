"""Pestaña «Catálogo» de la barra lateral (se arma una vez al abrir la ventana)."""
from __future__ import annotations
from ...i18n import tr

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..widgets import collapsible as _collapsible
from ..widgets import help_note as _help


def build(win) -> None:
    """Agrega la pestaña «Catálogo» a `win.tabs` (win = la `MainWindow`)."""
    box = win._tab_page(tr("common.catalogo_2"))
    win.btn_cat_new = win._button(tr("catalogo.crear_catalogo_de_la_linea_carpeta"), win._on_catalog_new)
    win.btn_cat_open = win._button(tr("catalogo.abrir_catalogo_de_la_linea_carpeta"), win._on_catalog_open)
    win.btn_cat_hero = win._button(tr("catalogo.nuevo_heroe_desde_el_patron_p"), win._on_new_from_pattern)
    win.btn_cat_hero.set_tooltip_text(tr("catalogo.crea_el_proyecto_de_un_heroe"))
    win.lbl_catalog = Gtk.Label(label=tr("common.sin_catalogo_abierto"), xalign=0, wrap=True)
    win.btn_cat_add = win._button(tr("catalogo.catalogar_esta_figura"), win._on_catalog_add)
    win.btn_cat_guide = win._button(tr("catalogo.guia_de_cortes_cuanto_del_catalogo"), win._on_cut_guide)
    win.btn_cat_guide.set_tooltip_text(tr("catalogo.mide_la_cara_de_cada_corte"))
    win.btn_cat_fitcm = win._button(tr("catalogo.ajustar_cintura_y_muneca_al_patron"), win._on_fit_waist_wrist)
    win.btn_cat_fitcm.set_tooltip_text(tr("catalogo.mueve_el_plano_de_la_cintura"))
    win.btn_cat_manage = win._button(tr("catalogo.administrar_catalogo_piezas_certificacion"), win._on_catalog_manage)
    win.btn_cat_build = win._button(tr("catalogo.armar_figura_desde_catalogo_sin_stl"), win._on_catalog_build)
    win.btn_cat_online = win._button(tr("catalogo.explorar_catalogos_en_linea"), win._on_catalog_online)
    win.btn_cat_online.set_tooltip_text(tr("catalogo.explorar_catalogos_en_linea_tip"))
    win.btn_cat_forget = win._button(tr("catalogo.olvidar_el_catalogo_recordado"), lambda *_: win.ctrl.forget_catalog())
    win.btn_cat_forget.set_tooltip_text(tr("catalogo.cierra_el_catalogo_y_borra_lo"))
    win.btn_cat_review = win._button(tr("catalogo.revisar_las_piezas_elegidas_sin_generar"), win._on_catalog_review)
    win.btn_cat_scale = win._button(tr("common.escalar_la_figura_a_la_altura"), lambda *_: win.ctrl.scale_to_line())
    win.btn_cat_align = win._button(tr("catalogo.alinear_todos_los_cortes_a_la"), win._on_align_all)
    win.chk_part_preview = Gtk.CheckButton(label=tr("catalogo.dibujar_las_piezas_elegidas_antes_de"),
                                            active=win.ctrl.show_part_preview)
    win.chk_part_preview.connect("toggled", lambda b: None if win._updating else win.ctrl.set_show_part_preview(b.get_active()))
    win.btn_divide_model = win._button(tr("catalogo.dividir_figura_actual_y_ajustar_piezas"), win._on_divide_model)
    win.btn_proportions = win._button(tr("catalogo.comparar_proporciones_con_un_patron"), win._on_proportions)
    win.btn_proportions.set_tooltip_text(tr("catalogo.tu_figura_en_piezas_al_lado"))
    win.btn_reset_piece_scales = win._button(tr("catalogo.restablecer_escalas_por_pieza"), lambda *_: win.ctrl.reset_part_scales())
    win.parts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    win._parts_sig = None

    # ---- orden nuevo (etapa 6): primero los lugares de la figura (lo más usado), la gestión del catálogo en un menú
    places = _collapsible(box, tr("catalogo.lugares_de_la_figura"))
    places.append(win.lbl_catalog)
    bar = Gtk.Box(spacing=6)
    pop_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    popover = Gtk.Popover(child=pop_box)
    for b in (win.btn_cat_new, win.btn_cat_open, win.btn_cat_add, win.btn_cat_hero, win.btn_cat_guide, win.btn_cat_fitcm,
              win.btn_cat_manage, win.btn_cat_online, win.btn_cat_build, win.btn_cat_forget):
        b.add_css_class("flat")
        b.set_halign(Gtk.Align.FILL)
        if b.get_child() is not None:
            b.get_child().set_halign(Gtk.Align.START)
        b.connect("clicked", lambda *_: popover.popdown())          # el menú se cierra al elegir (si no, queda encima de la ventana que se abre)
        pop_box.append(b)
    win.btn_cat_forget.add_css_class("destructive-action")      # «Olvidar» ya no queda junto a las acciones frecuentes
    win.menu_catalog = Gtk.MenuButton(label=tr("common.catalogo"), popover=popover)
    win.menu_catalog.set_tooltip_text(tr("catalogo.abrir_crear_administrar_o_catalogar_guia"))
    bar.append(win.menu_catalog)
    bar.append(win.btn_cat_review)
    places.append(bar)
    places.append(Gtk.Label(label=tr("catalogo.pieza_de_cada_lugar_al_cambiar"), xalign=0, css_classes=["dim-label"]))
    places.append(win.parts_box)

    sec = _collapsible(box, tr("catalogo.ajustes_de_la_figura"), expanded=False)
    for w in (win.btn_cat_scale, win.btn_cat_align, win.chk_part_preview, win.btn_divide_model, win.btn_proportions,
              win.btn_reset_piece_scales):
        sec.append(w)

    _help(box, tr("catalogo.elegi_en_cada_lugar_la_pieza"))
    long_sec = _collapsible(box, tr("catalogo.ayuda_del_catalogo"), expanded=False, sub=True)
    _help(long_sec, tr("catalogo.crear_catalogo_arma_una_carpeta_nueva"))

    # --------------------------------------------------------- Miembros
