"""Same-net physical spacing must survive solving and solution re-export."""

import pytest

from cpcell.core.band_router import BandRouter, RouteEdge, Shape
from cpcell.gds.gds_GT2N_thin import export_thin
from cpcell.tech.gt2n_thin import ThinProblem
from examples.gt2n_thin.reproduce import load_design


def router_for_boxes(boxes, spacing, bridges=(), bridge_owner=None):
    root, hub = ("T", "root"), (2, 0, 0)
    edges = [RouteEdge(root, hub, (Shape("VG", (-1, -1, 1, 1)),), 1)]
    ports = [(2, i + 1, 0) for i in range(len(boxes))]
    for port, bounds in zip(ports, boxes):
        edges.append(RouteEdge(hub, port, (Shape("M0", bounds),), 1))
    for i, bounds in enumerate(bridges):
        edges.append(
            RouteEdge(hub, (2, 100 + i, 0), (Shape("M0", bounds),), 1, bridge_owner)
        )
    return BandRouter(edges, {"a": [root]}, {"a": ports}, spacing={"M0": spacing})


@pytest.mark.parametrize(
    "second,valid",
    [
        ((13, 0, 23, 10), False),
        ((14, 0, 24, 10), True),
        ((0, 15, 10, 25), False),
        ((0, 16, 10, 26), True),
        ((12, 14, 22, 24), False),
        ((13, 14, 23, 24), True),
        ((10, 0, 20, 10), True),
        ((9, 0, 19, 10), True),
    ],
)
def test_same_net_spacing_thresholds_and_joins(second, valid):
    router = router_for_boxes(
        [(0, 0, 10, 10), second], dict(horizontal=4, vertical=6, corner=5)
    )
    result = router.solve_result()
    assert result["status"] == ("OPTIMAL" if valid else "INFEASIBLE")
    routes = [dict(net="a", edge=i) for i in range(3)]
    if valid:
        router.validate(routes)
    else:
        with pytest.raises(ValueError, match="Same-net M0 spacing"):
            router.validate(routes)


@pytest.mark.parametrize(
    "bridges",
    [
        [(10, 0, 16, 10)],
        [(10, 0, 13, 10), (13, 0, 16, 10)],
    ],
)
@pytest.mark.parametrize("vertical", [False, True])
def test_selected_segments_may_fill_the_gap(bridges, vertical):
    boxes = [(0, 0, 10, 10), (16, 0, 26, 10)]
    if vertical:
        boxes = [(y0, x0, y1, x1) for x0, y0, x1, y1 in boxes]
        bridges = [(y0, x0, y1, x1) for x0, y0, x1, y1 in bridges]
    router = router_for_boxes(boxes, 10, bridges)
    solution = router.solve()
    assert len(solution["routes"]) == 3 + len(bridges)
    router.validate(solution["routes"])
    with pytest.raises(ValueError, match="Same-net M0 spacing"):
        router.validate([dict(net="a", edge=i) for i in range(3)])
    router.model.Add(router.used["a", 3] == 0)
    assert router.solve_result()["status"] == "INFEASIBLE"


def test_foreign_net_or_partial_gap_fill_does_not_exempt_spacing():
    for bridges, owner in [([(10, 0, 16, 10)], "b"), ([(10, 0, 16, 9)], None)]:
        router = router_for_boxes([(0, 0, 10, 10), (16, 0, 26, 10)], 10, bridges, owner)
        assert router.solve_result()["status"] == "INFEASIBLE"


def test_conflicting_shapes_in_one_edge_are_rejected():
    root, port = ("T", "root"), (1, 0, 0)
    router = BandRouter(
        [
            RouteEdge(
                root,
                port,
                (Shape("M0", (0, 0, 10, 10)), Shape("M0", (12, 0, 22, 10))),
                1,
            ),
        ],
        {"a": [root]},
        {"a": [port]},
        spacing={"M0": 4},
    )
    assert router.solve_result()["status"] == "INFEASIBLE"
    with pytest.raises(ValueError, match="Same-net M0 spacing"):
        router.validate([dict(net="a", edge=0)])


def test_explicit_same_net_cut_rule_and_legacy_override():
    root, port = ("T", "root"), (1, 0, 0)
    edges = [
        RouteEdge(
            root, port, (Shape("V0", (0, 0, 10, 10)), Shape("V0", (12, 0, 22, 10))), 1
        )
    ]
    for rules, status in [
        (None, "OPTIMAL"),
        ({}, "OPTIMAL"),
        ({"V0": 4}, "INFEASIBLE"),
    ]:
        router = BandRouter(
            edges,
            {"a": [root]},
            {"a": [port]},
            spacing={"V0": 4},
            same_net_spacing=rules,
        )
        assert router.solve_result()["status"] == status
        if status == "INFEASIBLE":
            with pytest.raises(ValueError, match="Same-net V0 spacing"):
                router.validate([dict(net="a", edge=0)])


def test_thin_wl_seven_nm_spacing_rejected_by_solver_and_export(tmp_path):
    problem = ThinProblem(load_design(31, 31))
    forced = [
        next(
            i
            for i, e in enumerate(problem.edges)
            if e.u == (2, 0, 2 * x) and e.v == (2, int(2 * problem.bitline), 2 * x)
        )
        for x in (21, 42)
    ]
    for i in forced:
        problem.router.model.Add(problem.router.used["WL", i] == 1)
    assert problem.router.solve_result()["status"] == "INFEASIBLE"
    # Reconstruct a historical solution with the old inter-net-only checks.
    problem.router = BandRouter(
        problem.edges, dict(problem.terminals), problem.ports, same_net_spacing={}
    )
    for i in forced:
        problem.router.model.Add(problem.router.used["WL", i] == 1)
    for net in problem.router.nets:
        problem.router.model.Add(
            sum(v for (n, _), v in problem.router.used.items() if n == net)
            == sum(problem.router.reached[net])
        )
    bad = problem.solve()
    problem.router.validate(bad["routes"], tree=True)
    dest = tmp_path / "preserved.gds"
    dest.write_bytes(b"previous layout")
    with pytest.raises(ValueError, match="Same-net M1 spacing"):
        export_thin(bad, dest)
    assert dest.read_bytes() == b"previous layout"
