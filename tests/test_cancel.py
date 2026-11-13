"""Cancelación cooperativa de las tareas largas (UX etapa 2c)."""
import threading

import pytest

from afm.gui.controller import Controller
from afm.progress import CancelToken, TaskCancelled
from afm.testfigure import make_doll


def test_token_passes_progress_through_until_cancelled():
    seen = []
    tok = CancelToken()
    p = tok.wrap(seen.append)
    p("Paso 1")
    assert seen == ["Paso 1"] and not tok.cancelled
    tok.cancel()
    with pytest.raises(TaskCancelled):
        p("Paso 2")
    assert seen == ["Paso 1"]


def test_token_wrap_without_progress_still_cancels():
    tok = CancelToken()
    p = tok.wrap(None)
    assert p("x") is None
    tok.cancel()
    with pytest.raises(TaskCancelled):
        p("y")


def test_cancel_from_another_thread_stops_a_job_at_its_next_step():
    tok = CancelToken()
    started, go = threading.Event(), threading.Event()
    out = {}

    def job(progress):
        for i in range(100):
            progress(f"paso {i}")
            if i == 1:
                started.set()
                go.wait(2)

    def run():
        try:
            job(tok.wrap(lambda _t: None))
        except TaskCancelled as e:
            out["err"] = e

    th = threading.Thread(target=run)
    th.start()
    started.wait(2)
    tok.cancel()
    go.set()
    th.join(2)
    assert isinstance(out.get("err"), TaskCancelled)


def test_cancelled_generation_leaves_controller_usable(tmp_path):
    m = make_doll()
    m.apply_scale(0.05)
    path = str(tmp_path / "muneco.stl")
    m.export(path)
    c = Controller()
    c.open_stl(path)
    c.generated_once = True
    c.busy = True
    c.finish_generate(error=TaskCancelled())
    assert not c.busy and "cancelada" in c.message.lower()
    assert c.plane_faults == {}                      # cancelar no es una falla de ningún plano
