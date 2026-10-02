"""Logic topology, automatic placement, physical views and timing math."""

from dataclasses import replace
from itertools import product
import json
import os
from pathlib import Path
import subprocess
import sys

import klayout.db as db
import pytest

from cpcell.gds.result import parse_result
from examples.gt2n_logic.cells import cells
from examples.gt2n_logic.characterize import crossing, sensitizations

ROOT = Path(__file__).resolve().parents[1]


def test_cmos_topologies_exhaustively_match_boolean_functions():
    expected_counts = {"NAND4": 8, "NOR4": 8, "AOI221": 10, "OAI221": 10}
    for cell in cells().values():
        assert len(cell.devices) == expected_counts[cell.name]
        assert cell.check_topology() == 2 ** len(cell.inputs)
        for values in product((0, 1), repeat=len(cell.inputs)):
            assert cell.expected(values) in (0, 1)
    cell = cells()["NAND4"]
    # A missing series device must be detected, even though the Boolean
    # function and all exported declarations remain unchanged.
    with pytest.raises(ValueError, match="Invalid NAND4 topology"):
        replace(cell, devices=cell.devices[1:]).check_topology()


def test_all_sensitizing_vectors_and_crossing_interpolation():
    for name, count in [("NAND4", 4), ("NOR4", 4), ("AOI221", 21), ("OAI221", 21)]:
        cell = cells()[name]
        cases = sensitizations(cell)
        assert len(cases) == count
        assert {pin for pin, _ in cases} == set(cell.inputs)
    assert crossing([0, 1, 2], [0, 0.4, 1], 0.7, 0, 2, True) == pytest.approx(1.5)
    assert crossing([0, 1, 2], [1, 0.4, 0], 0.7, 0, 2, False) == pytest.approx(0.5)
    with pytest.raises(ValueError, match="Missing"):
        crossing([0, 1], [0, 0.1], 0.7, 0, 1, True)


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    output = tmp_path_factory.mktemp("logic") / "results"
    run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "examples/gt2n_logic/reproduce.py"),
            "--output-dir",
            str(output),
            "--timeout",
            "60",
        ],
        cwd=output.parent,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    return output


def test_all_four_cells_are_placed_routed_and_exported(generated):
    report = json.loads((generated / "report.json").read_text())
    assert report["status"] == "passed"
    assert report["verification_status"] == report["spice_status"] == "not_run"
    assert not (generated / "cells_tt.lib").exists()
    library = db.Layout()
    library.read(str(generated / "cells.gds"))
    assert {c.name for c in library.top_cells()} == {
        c.physical_name for c in cells().values()
    }
    for cell in cells().values():
        entry = report["cells"][cell.name]
        assert entry["solver_status"] in ("OPTIMAL", "FEASIBLE")
        assert entry["footprint_nm"][1] == 144
        assert entry["transistors"] == len(cell.devices)
        config = json.loads((generated / "config" / f"{cell.name}.json").read_text())
        assert not config["inject_placement"]["value"]
        assert all(
            rule["type"] == "align" for rule in config["placement_constraints"]["value"]
        )
        tech, pmos, nmos, routes = parse_result(
            generated / "result" / f"{cell.name}.res"
        )
        assert tech.layout_profile == "gt2n_logic"
        assert len(pmos) == len(nmos) == len(cell.inputs)
        assert routes
        layout = library.cell(cell.physical_name)
        m1 = db.Region(layout.shapes(library.layer(25, 0))).merged()
        # Mirrored rows require >=20 nm between same-column M1 ends.
        assert m1.bbox().bottom >= 20
        assert m1.bbox().top <= 268
        contacts = db.Region(layout.shapes(library.layer(12, 0)))
        act = db.Region(layout.shapes(library.layer(2, 0)))
        assert (contacts.sized(0, 12) & act).is_empty()
        vs = db.Region(layout.shapes(library.layer(22, 0)))
        assert (vs - m1).is_empty()
        assert (vs - db.Region(layout.shapes(library.layer(20, 0)))).is_empty()
        sdcon = db.Region(layout.shapes(library.layer(10, 0)))
        assert sdcon.bbox().bottom >= 20
        assert sdcon.bbox().top <= 268
        assert all(
            p.bbox().height() == 24
            for p in db.Region(layout.shapes(library.layer(20, 0))).merged().each()
        )
        pins = {
            s.text.string
            for s in layout.shapes(library.layer(25, 251)).each()
            if s.is_text()
        }
        assert pins == set(cell.inputs) | {"Y"}
        lef = (generated / "cells.lef").read_text()
        assert f"MACRO {cell.physical_name}" in lef
        assert f"module {cell.physical_name}" in (generated / "cells.v").read_text()
        assert (generated / cell.name / "cell.cdl").read_text().count(
            "W=0.031u"
        ) == len(cell.devices)
