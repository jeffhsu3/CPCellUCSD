"""Hard matching and reachability constraints independent of any technology."""

import pytest

from cpcell.core.band_router import BandRouter, RouteEdge, Shape, close_boxes


def test_anisotropic_spacing_boundaries():
    spacing = dict(horizontal=4, vertical=6, corner=5)
    a = (0, 0, 10, 10)
    assert close_boxes(a, (13, 0, 23, 10), spacing)
    assert not close_boxes(a, (14, 0, 24, 10), spacing)
    assert close_boxes(a, (0, 15, 10, 25), spacing)
    assert not close_boxes(a, (0, 16, 10, 26), spacing)
    assert close_boxes(a, (12, 14, 22, 24), spacing)
    assert not close_boxes(a, (13, 14, 23, 24), spacing)
    assert close_boxes(a, (10, 10, 20, 20), spacing)


def test_mirror_is_a_hard_constraint_in_solver_and_validator():
    terminals = {"a": [("T", "a")], "b": [("T", "b")]}
    ports = {"a": [(1, 0, -20)], "b": [(1, 0, 20)]}
    edges = [
        RouteEdge(terminals[net][0], ports[net][0], (Shape("M0", bounds),), 1, net)
        for net, bounds in [("a", (-22, -2, -18, 2)), ("b", (18, -2, 22, 2))]
    ]
    matching = dict(mirror_x=0, mirror_nets=[("a", "b")])
    assert (
        BandRouter(edges, terminals, ports, spacing={}, matching=matching).solve()[
            "status"
        ]
        == "OPTIMAL"
    )
    edges[1] = RouteEdge(
        edges[1].u, edges[1].v, (Shape("M0", (18, -2, 23, 2)),), 1, "b"
    )
    router = BandRouter(edges, terminals, ports, spacing={}, matching=matching)
    with pytest.raises(ValueError, match="INFEASIBLE"):
        router.solve()
    with pytest.raises(ValueError, match="mirror matching"):
        router.validate([dict(net="a", edge=0), dict(net="b", edge=1)])


def balanced_router(extra_via=False, length=10, matching=True):
    edges, terminals, ports = [], {}, {}
    for net, y, distance in [("a", 0, 10), ("b", 100, length)]:
        t, u, v = ("T", net), (1, y, 0), (1, y, distance)
        terminals[net], ports[net] = [t], [v]
        shapes = [Shape("VG", (-1, y - 1, 1, y + 1))]
        if extra_via and net == "b":
            shapes.append(Shape("V0", (-1, y - 1, 1, y + 1)))
        edges.extend(
            [
                RouteEdge(t, u, tuple(shapes), 1, net),
                RouteEdge(
                    u, v, (Shape("M0", (0, y - 1, distance, y + 1)),), distance, net
                ),
            ]
        )
    return BandRouter(
        edges,
        terminals,
        ports,
        spacing={},
        matching={"balanced_nets": [("a", "b")]} if matching else {},
    )


@pytest.mark.parametrize(
    "options,metric", [({"length": 11}, "M0_length"), ({"extra_via": True}, "V0_count")]
)
def test_length_and_via_balance_are_hard_constraints(options, metric):
    # Both designs are connected and geometrically legal without matching.
    solution = balanced_router(**options, matching=False).solve()
    router = balanced_router(**options)
    with pytest.raises(ValueError, match="INFEASIBLE"):
        router.solve()
    with pytest.raises(ValueError, match=metric):
        router.validate(solution["routes"])
    assert balanced_router().solve()["route_metrics"]["a"] == {
        "VG_count": 1,
        "M0_length": 10,
    }


def test_selected_detached_cycle_cannot_pad_wire_length():
    t, end = ("T", "a"), (1, 0, 0)
    edges = [RouteEdge(t, end, (Shape("VG", (-1, -1, 1, 1)),), 1)]
    cycle = [(1, 100, 100), (1, 100, 110), (1, 110, 110)]
    for a, b in zip(cycle, cycle[1:] + cycle[:1]):
        edges.append(RouteEdge(a, b, (Shape("M0", (100, 100, 110, 110)),), 1))
    router = BandRouter(edges, {"a": [t]}, {"a": [end]}, spacing={})
    assert router.solve()["routes"] == [dict(net="a", edge=0)]
    for i in (1, 2, 3):
        router.model.Add(router.used["a", i] == 1)
    with pytest.raises(ValueError, match="INFEASIBLE"):
        router.solve()
    with pytest.raises(ValueError, match="Disconnected route"):
        router.validate([dict(net="a", edge=i) for i in range(4)])
