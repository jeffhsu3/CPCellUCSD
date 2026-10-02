"""Four-band routing and standalone export, without the sibling reference repo."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import klayout.db as db
import pytest

from cpcell.core.band_router import BandRouter, RouteEdge, Shape
from cpcell.gds.gds_GT2N_SH import LAYERS
from cpcell.gds.gds_GT2N_thin import export_thin
from cpcell.tech.gt2n_thin import ThinProblem
from examples.gt2n_thin.reproduce import load_design

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def solutions():
    return {
        (n, p): ThinProblem(load_design(n, p)).solve()
        for n, p in ((31, 31), (13, 13), (31, 13), (13, 31))
    }


def region(layout, cell, name, datatype=0):
    return db.Region(cell.shapes(layout.layer(LAYERS[name], datatype))).merged()


def test_all_sizes_route_export_and_retain_boundary_pins(solutions, tmp_path):
    for (n, p), solution in solutions.items():
        problem = ThinProblem(solution["design"])
        assert solution["status"] == "OPTIMAL"
        assert (solution["width_nm"], solution["height_nm"]) == (
            84,
            292 if p == 31 else 288,
        )
        # Serialized edge indices must reconstruct identically in the exporter.
        filename = tmp_path / f"{n}x{p}.gds"
        export_thin(json.loads(json.dumps(solution)), filename)
        layout = db.Layout()
        layout.read(str(filename))
        cell = layout.cell(problem.name)
        assert layout.dbu == 0.0005
        assert region(layout, cell, "BOUNDARY", 250).bbox() == db.Box(
            0, 0, 168, 2 * problem.height
        )
        assert {
            polygon.bbox().height() for polygon in region(layout, cell, "ACT").each()
        } == {2 * n, 2 * p}
        assert region(layout, cell, "M2").is_empty()
        wl = db.Region(db.Box(70, 0, 98, 2 * problem.height))
        assert (region(layout, cell, "M1", 251) ^ wl).is_empty()
        bitlines = db.Region()
        for y in (problem.bitline, problem.height - problem.bitline):
            bitlines.insert(db.Box(0, int(2 * y - 12), 168, int(2 * y + 12)))
        assert (region(layout, cell, "M0", 251) ^ bitlines).is_empty()
        assert len(solution["design"]["devices"]) == 6
        assert len(problem.intrinsic) == 8  # Shared gates and shared SDCONs.
        schematic = problem.schematic()
        assert schematic.count(f"nmos_lvt W={n / 1000:g}u") == 4
        assert schematic.count(f"pmos_lvt W={p / 1000:g}u") == 2
        assert schematic.count("L=0.014u") == 6


def test_corrupt_solution_rejected_before_overwriting(solutions, tmp_path):
    path = tmp_path / "preserved.gds"
    path.write_bytes(b"previous result")
    solution = deepcopy(solutions[31, 31])
    problem = ThinProblem(solution["design"])
    # Remove a selected intrinsic contact: a missing connection cannot export.
    index = next(
        i
        for i, route in enumerate(solution["routes"])
        if problem.edges[route["edge"]].owner is not None
    )
    del solution["routes"][index]
    with pytest.raises(ValueError, match="Disconnected route"):
        export_thin(solution, path)
    assert path.read_bytes() == b"previous result"
    solution = deepcopy(solutions[31, 31])
    solution["height_nm"] += 1
    with pytest.raises(ValueError, match="dimensions"):
        export_thin(solution, path)
    assert path.read_bytes() == b"previous result"


def test_placement_and_electrical_constraints_are_enforced():
    design = load_design(31, 31)
    bad = deepcopy(design)
    bad["devices"][0]["flip"] = True
    with pytest.raises(ValueError, match="shared diffusion"):
        ThinProblem(bad)
    bad = deepcopy(design)
    bad["devices"][2]["g"] = "unexpected_gate_net"
    with pytest.raises(ValueError, match="gate nets shorted"):
        ThinProblem(bad)
    bad = deepcopy(design)
    bad["devices"][0]["band"] = 1
    with pytest.raises(ValueError, match="wrong-polarity"):
        ThinProblem(bad)
    bad = deepcopy(design)
    bad["ports"][1] = dict(bad["ports"][0], name="duplicate_location")
    with pytest.raises(ValueError, match="share a node"):
        ThinProblem(bad)
    bad = deepcopy(design)
    bad["ports"][0]["track"] = True
    with pytest.raises(ValueError, match="Invalid M1 port track"):
        ThinProblem(bad)


def test_geometry_conflicts_constrain_independent_routing_nodes():
    terminals = {"a": [("T", "a")], "b": [("T", "b")]}
    ports = {"a": [(1, 0, 0)], "b": [(1, 0, 1)]}
    edges = [
        RouteEdge(terminals[net][0], ports[net][0], (Shape("M0", bounds),), 1, net)
        for net, bounds in [("a", (0, 0, 10, 10)), ("b", (20, 0, 30, 10))]
    ]
    router = BandRouter(edges, terminals, ports, spacing={"M0": 14})
    with pytest.raises(ValueError, match="INFEASIBLE"):
        router.solve()
    with pytest.raises(ValueError, match="geometry conflict"):
        router.validate([{"net": "a", "edge": 0}, {"net": "b", "edge": 1}])
    # Reducing the required physical clearance makes the same graph feasible.
    assert (
        BandRouter(edges, terminals, ports, spacing={"M0": 10}).solve()["status"]
        == "OPTIMAL"
    )


def test_cli_works_outside_repo_and_reports_unverified_generation(tmp_path):
    output = tmp_path / "new" / "results"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "examples/gt2n_thin/reproduce.py"),
            "--output-dir",
            str(output),
            "--variants",
            "31x13",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        timeout=60,
    )
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "passed"
    assert report["verification_status"] == report["spice_status"] == "not_run"
    assert report["cells"]["31x13"]["footprint_nm"] == [84, 288]
    assert (output / "31x13/solution.json").is_file()
