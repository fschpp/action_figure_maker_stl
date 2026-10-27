"""La cabeza de catálogo se arma con la misma referencia de giro con la que se guardó su puerto `cuello`.

Con un cuello inclinado (como el de Belgrano, ~24°) `assembly.host_frame` calculaba la referencia con +Z mientras
`extract_all` la guarda con +Y: las dos quedan opuestas y la cabeza armada salía girada 180° sobre el eje del cuello
(la vista previa, que acopla puerto con puerto, la mostraba bien). Con el cuello vertical no se notaba."""
import dataclasses

import numpy as np
import pytest

from afm import solids as S
from afm.assembly import PartChoice, synthesize_host
from afm.extract import extract_all
from afm.head import split_head
from afm.params import Params
from afm.planes import Plane
from afm.ports import coupling_transform
from afm.project import demo_project
from afm.testfigure import make_doll


def _choice(e):
    return PartChoice(e.meta, e.mesh)


@pytest.mark.parametrize("tilt_deg", [-24.0, 24.0, 0.0])
def test_armed_head_matches_the_port_to_port_placement(tilt_deg):
    doll = S.to_manifold(make_doll())
    pr = demo_project()
    k = np.tan(np.radians(tilt_deg)) * 10
    pr.head = dataclasses.replace(pr.head, plane=Plane.from_points((-5, -5, 80), (5, -5, 80), (0, 5, 80 + k), "cuello"))
    res = extract_all(doll, pr, "muneco", ["cabeza", "torso"])
    head, torso = (_choice(next(e for e in res.pieces if e.meta.tipo == t)) for t in ("cabeza", "torso"))
    expected = head.mesh.copy()
    expected.apply_transform(coupling_transform(head.port("head").frame, torso.meta.puertos["cuello"].frame))

    host = synthesize_host({"torso": torso, "cabeza": head}, Params())
    split = split_head(host.body, host.head, Params(), host.parts["cabeza"])
    got = S.to_trimesh(split.head)
    # misma orientación: `place_part` centra sobre el centroide de la cara del cuello (<1 mm de corrimiento a propósito),
    # así que se compara sin la traslación; una cabeza girada 180° sobre el cuello (nariz hacia atrás) da varios mm de diferencia
    assert np.allclose(got.bounds - got.centroid, expected.bounds - expected.centroid, atol=0.35), (got.bounds, expected.bounds)
    assert np.linalg.norm(got.centroid - expected.centroid) < 1.5
