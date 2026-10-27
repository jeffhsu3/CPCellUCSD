"""Enumeration projects out flow witnesses and keeps bounds honest."""

import pytest
from ortools.sat.python import cp_model

from cpcell.core.band_router import BandRouter, RouteEdge, Shape, merge_boxes


def alternatives():
    root, sink = ("T", "root"), (1, 0, 10)
    return BandRouter(
        [
            RouteEdge(root, sink, (Shape("M0", (0, 0, 10, 2)),), 1),
            RouteEdge(root, sink, (Shape("M0", (0, 4, 10, 6)),), 2),
        ],
        {"a": [root]},
        {"a": [sink]},
        spacing={},
    )


def attached_loop():
    """A root-n1-sink path with a triangle n1-n2-n3 hanging off n1."""
    root, n1, n2, n3, sink = ("T", "root"), (1, 0, 0), (1, 10, 0), (1, 10, 10), (1, 0, 10)
    pairs = [(root, n1), (n1, sink), (n1, n2), (n2, n3), (n3, n1)]
    return BandRouter(
        [RouteEdge(u, v, (Shape("M0", (0, 0, 10, 2)),), 1) for u, v in pairs],
        {"a": [root]},
        {"a": [sink]},
        spacing={},
    )


def same_metal_twice():
    """Two selections draw one M0 bar (split vs whole); a third is elsewhere."""
    root, mid, sink = ("T", "root"), (1, 5, 0), (1, 10, 0)

    def edge(u, v, box, cost):
        return RouteEdge(u, v, (Shape("M0", box),), cost)

    return BandRouter(
        [
            edge(root, mid, (0, 0, 6, 2), 1),
            edge(mid, sink, (4, 0, 10, 2), 1),
            edge(root, sink, (0, 0, 10, 2), 3),
            edge(root, sink, (0, 4, 10, 6), 4),
        ],
        {"a": [root]},
        {"a": [sink]},
        spacing={},
    )


@pytest.mark.parametrize(
    "boxes",
    [
        [(0, 0, 10, 2)],
        [(0, 0, 5, 2), (5, 0, 10, 2)],
        [(4, 0, 10, 2), (0, 0, 6, 2)],
        [(0, 0, 10, 1), (0, 1, 10, 2)],
        [(0, 0, 10, 2), (2, 0, 8, 2), (0, 0, 10, 2)],
    ],
)
def test_merge_boxes_is_canonical_for_one_bar(boxes):
    assert merge_boxes(boxes) == ((0, 0, 10, 2),)


def test_merge_boxes_l_shape_is_order_independent():
    a = merge_boxes([(0, 0, 10, 2), (0, 0, 2, 10)])
    b = merge_boxes([(0, 2, 2, 10), (0, 0, 2, 2), (2, 0, 10, 2)])
    assert a == b == ((0, 0, 10, 2), (0, 2, 2, 10))
    assert merge_boxes([(0, 0, 2, 2), (4, 0, 6, 2)]) == ((0, 0, 2, 2), (4, 0, 6, 2))


def test_distinct_geometry_collapses_equivalent_selections():
    plain = same_metal_twice().enumerate_routes(seconds=5)
    assert plain["exact_count"] == 3 and plain["selections_seen"] == 3
    result = same_metal_twice().enumerate_routes(
        distinct_geometry=True, by_cost=True, seconds=5
    )
    assert result["complete"] and result["exact_count"] == 2
    assert result["selections_seen"] == 3 and result["distinct_geometry"]
    # The cheapest drawing of the shared bar is the one kept.
    assert [s["objective"] for s in result["solutions"]] == [2, 4]


def test_merged_geometry_keeps_nets_and_layers_apart():
    router = same_metal_twice()
    split = router.merged_geometry([{"net": "a", "edge": 0}, {"net": "a", "edge": 1}])
    assert split == router.merged_geometry([{"net": "a", "edge": 2}])
    assert split == (("a", "M0", ((0, 0, 10, 2),)),)
    assert split != router.merged_geometry([{"net": "a", "edge": 3}])


def test_counts_route_selections_once_despite_multiple_flow_witnesses():
    router = alternatives()
    before = str(router.model.Proto())
    result = router.enumerate_routes(max_solutions=10, seconds=5)
    assert result["complete"] and result["exact_count"] == 2
    assert result["termination"] == "exhausted"
    assert {s["objective"] for s in result["solutions"]} == {1, 2}
    assert {tuple(r["edge"] for r in s["routes"]) for s in result["solutions"]} == {
        (0,),
        (1,),
    }
    assert str(router.model.Proto()) == before
    assert router.solve()["objective"] == 1


def test_cost_cap_and_caller_constraints_survive_cloning():
    router = alternatives()
    result = router.enumerate_routes(max_cost=1)
    assert result["exact_count"] == 1
    assert result["solutions"][0]["objective"] == 1
    router.model.Add(router.used["a", 0] == 0)
    assert router.enumerate_routes()["exact_count"] == 1
    empty = router.enumerate_routes(max_cost=1)
    assert empty["complete"] and empty["exact_count"] == 0
    assert empty["solutions"] == []


def test_solution_limit_is_only_a_lower_bound():
    result = alternatives().enumerate_routes(max_solutions=1)
    assert result["termination"] == "solution_limit"
    assert not result["complete"] and result["exact_count"] is None
    assert result["count_lower_bound"] == 1
    assert result["solutions"][0]["status"] == "FEASIBLE"


def test_attached_loops_are_not_counted():
    # The tree constraint excludes a cycle attached to the route, so only the
    # minimal root-n1-sink path remains, with or without a cost cap.
    result = attached_loop().enumerate_routes(seconds=5)
    assert result["complete"] and result["exact_count"] == 1
    assert len(result["solutions"][0]["routes"]) == 2
    assert attached_loop().enumerate_routes(max_cost=4, seconds=5)["exact_count"] == 1


def test_validate_rejects_attached_loop():
    router = attached_loop()
    with pytest.raises(ValueError, match="loop"):
        router.validate([{"net": "a", "edge": i} for i in range(5)])


@pytest.mark.parametrize("make", [alternatives, attached_loop])
def test_by_cost_returns_cheapest_first(make):
    result = make().enumerate_routes(by_cost=True, seconds=5)
    costs = [s["objective"] for s in result["solutions"]]
    assert result["complete"] and result["cost_ordered"]
    assert costs == sorted(costs) and costs[0] == make().solve()["objective"]
    assert {s["status"] for s in result["solutions"]} == {"OPTIMAL"}
    first = make().enumerate_routes(by_cost=True, max_solutions=1, seconds=5)
    assert first["solutions"][0]["objective"] == costs[0]
    assert first["cost_ordered"] and not first["complete"]


def test_unordered_enumeration_is_not_cost_ordered():
    result = alternatives().enumerate_routes(seconds=5)
    assert not result["cost_ordered"]
    assert {s["status"] for s in result["solutions"]} == {"FEASIBLE"}


def test_unknown_is_not_an_exact_zero_count(monkeypatch):
    monkeypatch.setattr(cp_model.CpSolver, "Solve", lambda *args: cp_model.UNKNOWN)
    result = alternatives().enumerate_routes()
    assert result["termination"] == "time_limit"
    assert result["last_solver_status"] == "UNKNOWN"
    assert result["exact_count"] is None and result["count_lower_bound"] == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_solutions": 0},
        {"max_solutions": True},
        {"max_solutions": 1.5},
        {"seconds": 0},
        {"seconds": float("inf")},
        {"seconds": float("nan")},
        {"seconds": True},
        {"max_cost": -1},
        {"max_cost": True},
        {"max_cost": 1.2},
        {"by_cost": 1},
        {"by_cost": None},
        {"distinct_geometry": 1},
    ],
)
def test_invalid_budgets_rejected(kwargs):
    with pytest.raises(ValueError):
        alternatives().enumerate_routes(**kwargs)
