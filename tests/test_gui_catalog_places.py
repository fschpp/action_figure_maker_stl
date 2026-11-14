"""Pestaña Catálogo reorganizada (UX etapa 6): estado por lugar y orden de la pestaña."""
import pytest

from afm.gui.controller import Controller

S = Controller.catalog_row_state


def test_row_state_levels():
    assert S({"lugar": "brazo", "pieza": None}) == ("modelo", "·")
    assert S({"lugar": "brazo", "pieza": "b@1", "compat": None}) == ("pendiente", "○")
    assert S({"lugar": "brazo", "pieza": "b@1", "compat": {"nivel": "ok"}}) == ("ok", "✓")
    assert S({"lugar": "brazo", "pieza": "b@1", "compat": {"nivel": "aviso"}}) == ("aviso", "⚠")
    assert S({"lugar": "brazo", "pieza": "b@1", "compat": {"nivel": "no"}}) == ("no", "✗")
    # la revisión previa (`nivel` en la fila) gana sobre `compat`
    assert S({"lugar": "brazo", "pieza": "b@1", "nivel": "no", "compat": {"nivel": "ok"}}) == ("no", "✗")


def test_trim_size_label_only_for_solid_shapes():
    from afm.gui.controller import trim_size_label

    assert trim_size_label("polygon") is None
    assert "Radio" in trim_size_label("sphere") and "Arista" in trim_size_label("box") and "Radio" in trim_size_label("cylinder")
