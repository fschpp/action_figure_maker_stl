import numpy as np
import trimesh

from afm.gui import thumbnails as T


def _alpha(img):
    return np.asarray(img)[..., 3]


def test_render_draws_piece_and_keeps_transparent_corners():
    mesh = trimesh.creation.cylinder(radius=5, height=40, sections=24)
    img = T.render_thumbnail(mesh, 48)
    a = _alpha(img)
    assert img.size == (48, 48) and a[24, 24] == 255 and a[0, 0] == 0
    # el eje más largo va hacia arriba: ocupa casi toda la altura y poco el ancho
    rows, cols = np.where(a > 0)
    assert rows.max() - rows.min() > 0.8 * 48 and cols.max() - cols.min() < 0.4 * 48


def test_cache_uses_disk_and_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    T.clear_memory()
    calls = []

    def loader():
        calls.append(1)
        return trimesh.creation.box(extents=(10, 20, 30))

    root = str(tmp_path / "cat")
    first = T.get_thumbnail(root, "mano@1", loader)
    second = T.get_thumbnail(root, "mano@1", loader)
    assert first is second and len(calls) == 1
    T.clear_memory()
    T.get_thumbnail(root, "mano@1", loader)         # sale del disco, no vuelve a renderizar
    assert len(calls) == 1


def test_failed_load_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    T.clear_memory()

    def boom():
        raise ValueError("sin STL")

    assert T.get_thumbnail(str(tmp_path), "x@1", boom) is None
