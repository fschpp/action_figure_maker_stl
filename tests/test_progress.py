"""Barra de progreso: porcentaje, pendientes y tiempo estimado (lógica sin GTK) y que los planes coincidan con lo que el motor anuncia."""
import os
import shutil

import pytest

from afm import progress as PR
from afm.gui import figure_builder as FB
from afm.gui.controller import Controller

EJEMPLO = os.path.join(os.path.dirname(__file__), "..", "catalogo", "ejemplo")


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_tracker_percent_pending_and_eta():
    clk = Clock()
    tr = PR.ProgressTracker([PR.Stage("a", 1), PR.Stage("b", 2), PR.Stage("c", 1)], clock=clk)
    s = tr.snapshot()
    assert s.percent == 0 and s.pending == ["a", "b", "c"] and s.eta is None
    assert tr.feed("a…") and tr.snapshot().pending == ["b", "c"]
    clk.t = 10
    assert tr.feed("b…")
    clk.t = 20                                           # 10 s por unidad; «b» pesa 2 y lleva 10 s => mitad
    s = tr.snapshot()
    assert s.percent == 50 and s.eta == pytest.approx(20.0) and s.pending == ["c"] and s.current == "b…"
    assert not tr.feed("algo que no está en el plan…") and tr.snapshot().current.startswith("algo")
    tr.feed("c…")
    assert tr.snapshot().pending == []
    tr.finish("Listo")
    s = tr.snapshot()
    assert s.percent == 100 and s.finished and s.eta == 0.0


def test_tracker_never_goes_backwards_and_skips_unseen_stages():
    clk = Clock()
    tr = PR.ProgressTracker([PR.Stage("uno"), PR.Stage("dos"), PR.Stage("tres")], clock=clk)
    tr.feed("tres…")                                     # «uno» y «dos» no se anunciaron (condicionales): quedan hechos
    assert tr.snapshot().pending == []
    assert not tr.feed("uno…") and tr.snapshot().pending == []     # un mensaje viejo no hace retroceder
    prev = -1.0
    for i in range(5):
        clk.t += 3
        f = tr.snapshot().fraction
        assert f >= prev and f < 1.0
        prev = f


def test_prefix_match_and_text_formats():
    tr = PR.ProgressTracker([PR.Stage("Revisando «brazo»")])
    assert tr.feed("Revisando «brazo»…")
    assert PR.fmt_duration(75) == "1 min 15 s" and PR.fmt_duration(8) == "8 s"
    assert PR.fmt_eta(None) == "" and PR.fmt_eta(3) == "casi listo" and PR.fmt_eta(42) == "unos 40 s" and PR.fmt_eta(190) == "unos 3 min"
    text, now, pend = PR.describe(PR.Snapshot(0.4, "Cavidad de brazo…", ["a", "b"], 2, 4, 30.0, 45.0))
    assert text.startswith("40 % · faltan unos 45 s") and now == "Ahora: Cavidad de brazo…" and pend == "Pendiente (2): a · b"
    _t, _n, pend = PR.describe(PR.Snapshot(0.1, "x", [str(i) for i in range(9)], 1, 10, 1.0, None))
    assert pend.endswith("… y 3 más")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    base = tmp_path_factory.mktemp("prog")
    root = str(base / "ejemplo")
    shutil.copytree(EJEMPLO, root)
    b = FB.FigureBuilder(root, "con-barra")
    for place, ref in (("torso", "torso-muneco-01"), ("cabeza", "cabeza-muneco-01"), ("brazo", "brazo-muneco-01"),
                       ("pierna", "pierna-muneco-01"), ("mano", "mano-muneco-01")):
        b.set_choice(place, ref)
    stages = b.progress_stages()
    seen: list[str] = []
    info = b.generate(str(base / "salida"), seen.append)
    return {"root": root, "stages": stages, "seen": seen, "info": info, "path": info["proyecto"]}


def test_armar_plan_matches_what_the_engine_announces(built):
    stages, seen = built["stages"], built["seen"]
    tr = PR.ProgressTracker(stages)
    matched = [tr.feed(m) for m in seen]
    assert stages[0].key == "Armando el cuerpo base" and stages[-1].key == "Guardando el proyecto"
    assert any(matched)
    # todos los pasos del plan se anunciaron de verdad (un plan que sobra o falta desvía el porcentaje)
    announced = {PR._norm(m) for m in seen}
    missing = [s.key for s in stages if not any(a.startswith(PR._norm(s.key)) for a in announced)]
    assert not missing, f"pasos planeados que el motor nunca anunció: {missing}"
    # y el motor no anuncia pasos de construcción fuera del plan
    keys = [PR._norm(s.key) for s in stages]
    unplanned = [m for m in seen if not m.startswith(("AVISO", "Escribiendo", "Calculando", "Aplanando"))
                 and not any(PR._norm(m).startswith(k) for k in keys)]
    assert not unplanned, f"mensajes del motor sin paso en el plan: {unplanned}"
    tr.finish()
    assert tr.snapshot().percent == 100


def test_main_window_plans_and_real_build_agree(built):
    c = Controller()
    c.open_project(built["path"])
    c.session.project.params.decimate = 0
    seen: list[str] = []
    out = c.generate(seen.append)
    assert out is not None, c.message
    assert c.progress_stages, "begin_generate debe dejar el plan para la ventana"
    keys = [PR._norm(s.key) for s in c.progress_stages]
    assert keys[0] == "armando el cuerpo base" and keys[-1] == "validando la figura"
    announced = [PR._norm(m) for m in seen]
    assert [k for k in keys if not any(a.startswith(k) for a in announced)] == []
    assert [m for m in announced if not any(m.startswith(k) for k in keys)] == []


def test_export_and_preview_plans(built):
    c = Controller()
    c.open_project(built["path"])
    c.session.project.params.decimate = 0
    assert c.generate() is not None
    assert c.session.progress_plan("preview") is None          # catálogo sin STL: la revisión no informa por lugar
    out = os.path.join(os.path.dirname(built["path"]), "otra")
    seen: list[str] = []
    job = c.begin_export(out, orient=True, flat=False)
    assert job is not None
    keys = [PR._norm(s.key) for s in c.progress_stages]
    job(seen.append)
    announced = [PR._norm(m) for m in seen]
    assert [k for k in keys if not any(a.startswith(k) for a in announced)] == []
    assert [m for m in announced if not any(m.startswith(k) for k in keys)] == []
    c.finish_export(None, out, error=RuntimeError("fin de la prueba"))        # libera `busy`
