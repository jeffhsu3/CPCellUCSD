"""Logic topology, automatic placement, physical views and timing math."""

from dataclasses import replace
from itertools import product
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import klayout.db as db
import numpy as np
import pytest

from cpcell.gds.result import parse_result
from examples.gt2n_logic.cells import LIBRARY_NAME, cells
from examples.gt2n_logic.characterize import (
    crossing,
    measure_timing,
    sensitizations,
    timing_senses,
)
from examples.gt2n_logic.reproduce import configure

ROOT = Path(__file__).resolve().parents[1]


def test_cmos_topologies_exhaustively_match_boolean_functions():
    expected_counts = {
        "NAND4": 8,
        "NOR4": 8,
        "AOI221": 10,
        "OAI221": 10,
        "AND4": 10,
        "OR4": 10,
        "AO221": 12,
        "OA221": 12,
        "NAND5": 10,
        "NOR5": 10,
        "AOI222": 12,
        "OAI222": 12,
        "AND5": 12,
        "OR5": 12,
        "AO222": 14,
        "OA222": 14,
        "MAJ3": 12,
        "MAJI3": 10,
    }
    assert set(cells()) == set(expected_counts)
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
    buffered = cells()["AND4"]
    # Removing the first-stage drive must be caught even though the output
    # inverter has both devices and could drive Y for an assumed Z value.
    with pytest.raises(ValueError, match="Invalid AND4 topology"):
        replace(buffered, devices=buffered.devices[1:]).check_topology()
    with pytest.raises(ValueError, match="Invalid AND4 topology"):
        replace(buffered, devices=buffered.devices[:-1]).check_topology()


def test_all_sensitizing_vectors_and_crossing_interpolation():
    for name, count in [
        ("NAND4", 4),
        ("NOR4", 4),
        ("AOI221", 21),
        ("OAI221", 21),
        ("AND4", 4),
        ("OR4", 4),
        ("AO221", 21),
        ("OA221", 21),
        ("NAND5", 5),
        ("NOR5", 5),
        ("AOI222", 54),
        ("OAI222", 54),
        ("AND5", 5),
        ("OR5", 5),
        ("AO222", 54),
        ("OA222", 54),
        ("MAJ3", 6),
        ("MAJI3", 6),
    ]:
        cell = cells()[name]
        cases = sensitizations(cell)
        assert len(cases) == count
        assert {pin for pin, _ in cases} == set(cell.inputs)
        sense = (
            "positive_unate"
            if name
            in (
                "AND4",
                "OR4",
                "AO221",
                "OA221",
                "AND5",
                "OR5",
                "AO222",
                "OA222",
                "MAJ3",
            )
            else "negative_unate"
        )
        assert timing_senses(cell) == dict.fromkeys(cell.inputs, sense)
    assert crossing([0, 1, 2], [0, 0.4, 1], 0.7, 0, 2, True) == pytest.approx(1.5)
    assert crossing([0, 1, 2], [1, 0.4, 0], 0.7, 0, 2, False) == pytest.approx(0.5)
    with pytest.raises(ValueError, match="Missing"):
        crossing([0, 1], [0, 0.1], 0.7, 0, 1, True)


@pytest.mark.parametrize("name", ["MAJ3", "MAJI3"])
def test_repeated_majority_inputs_align_one_complementary_pair_per_column(
    name, tmp_path
):
    cell = cells()[name]
    config = json.loads(configure(cell, tmp_path, 60).read_text())
    rules = config["placement_constraints"]["value"]
    devices = {d[0] + "S0": d for d in cell.devices}
    covered = []
    gate_counts = {}
    for rule in rules:
        assert rule["type"] == "align"
        assert len(rule["transistors"]) == 2
        first, second = (devices[t] for t in rule["transistors"])
        assert {first[4], second[4]} == {"nmos", "pmos"}
        assert first[2] == second[2]
        gate_counts[first[2]] = gate_counts.get(first[2], 0) + 1
        covered.extend(rule["transistors"])
    assert sorted(covered) == sorted(devices)
    assert gate_counts == (
        {"A": 2, "B": 2, "C": 1, "Z": 1} if name == "MAJ3" else {"A": 2, "B": 2, "C": 1}
    )


def test_majority_repeated_branch_is_electrically_required():
    for name in ("MAJ3", "MAJI3"):
        cell = cells()[name]
        # Removing the second A occurrence breaks the C(A+B) branch. Merely
        # having all three input names in the netlist is not sufficient.
        broken = replace(cell, devices=tuple(d for d in cell.devices if d[0] != "MNP0"))
        with pytest.raises(ValueError, match=f"Invalid {name} topology"):
            broken.check_topology()


@pytest.mark.parametrize("sense", ["negative_unate", "positive_unate"])
def test_timing_pairs_the_correct_input_and_output_edges(sense):
    times = np.arange(0, 100.5, 0.5) * 1e-12
    inputs = np.interp(
        times, np.array([0, 10, 20, 60, 70, 100]) * 1e-12, [0, 0, 0.7, 0.7, 0, 0]
    )
    outputs = np.interp(
        times, np.array([0, 13, 23, 67, 77, 100]) * 1e-12, [0, 0, 0.7, 0.7, 0, 0]
    )
    if sense == "negative_unate":
        outputs = 0.7 - outputs
    measured = measure_timing(times, inputs, outputs, sense, 10e-12, 60e-12, 100e-12)
    rise_delay, fall_delay = (3, 7) if sense == "positive_unate" else (7, 3)
    assert measured == pytest.approx(
        {
            "cell_rise": rise_delay,
            "cell_fall": fall_delay,
            "rise_transition": 6,
            "fall_transition": 6,
        }
    )


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
            "180",
        ],
        cwd=output.parent,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        timeout=2400,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    return output


def test_default_catalog_is_placed_routed_and_exported(generated):
    selected = list(cells().values())
    report = json.loads((generated / "report.json").read_text())
    assert report["status"] == "passed"
    assert report["verification_status"] == report["spice_status"] == "not_run"
    assert "liberty" not in report["views"]
    assert not list(generated.glob("*.lib"))
    assert "batch" not in report
    assert report["library"] == LIBRARY_NAME
    assert set(report["cells"]) == set(cells())
    assert report["views"]["lef"] == f"{LIBRARY_NAME}.lef"
    catalog = json.loads((generated / "catalog.json").read_text())
    assert set(catalog) == set(cells())
    manifest = json.loads((generated / "manifest.json").read_text())
    assert manifest["status"] == "passed"
    assert manifest["views"] == report["views"]
    for name, entry in manifest["artifacts"].items():
        data = (generated / name).read_bytes()
        assert len(data) == entry["bytes"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    library = db.Layout()
    library.read(str(generated / report["views"]["gds"]))
    assert {c.name for c in library.top_cells()} == {c.physical_name for c in selected}
    for cell in selected:
        entry = report["cells"][cell.name]
        assert json.loads((generated / cell.name / "report.json").read_text()) == entry
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
        assert len(pmos) == len(nmos) == len(cell.devices) // 2
        assert {t.gate_net for t in pmos} == set(cell.gate_nets)
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
        lef = (generated / report["views"]["lef"]).read_text()
        assert f"MACRO {cell.physical_name}" in lef
        assert (
            f"module {cell.physical_name}"
            in (generated / report["views"]["v"]).read_text()
        )
        assert (generated / cell.name / "cell.cdl").read_text().count(
            "W=0.031u"
        ) == len(cell.devices)


def test_catalog_listing_and_explicit_subset(tmp_path):
    command = [sys.executable, str(ROOT / "examples/gt2n_logic/reproduce.py")]
    output = tmp_path / "subset"
    listing = subprocess.run(
        command + ["--list-cells", "--output-dir", str(output)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert {line.split()[0] for line in listing.stdout.splitlines()} == set(cells())
    assert not output.exists()
    output.mkdir()
    # Changing the selection must not leave an old timing library/manifest
    # advertised as current when this run performs geometry generation only.
    (output / f"{LIBRARY_NAME}_tt_0p7v25c.lib").write_text("stale")
    (output / "manifest.json").write_text("stale")
    subprocess.run(
        command
        + [
            "--cells",
            "NOR4",
            "NAND4",
            "NOR4",
            "--output-dir",
            str(output),
            "--timeout",
            "60",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "passed"
    assert set(report["cells"]) == {"NAND4", "NOR4"}
    assert set(json.loads((output / "catalog.json").read_text())) == {"NAND4", "NOR4"}
    assert report["library"] == LIBRARY_NAME + "_nand4_nor4"
    assert not list(output.glob("*.lib"))
    gds = db.Layout()
    gds.read(str(output / report["views"]["gds"]))
    assert {cell.name for cell in gds.top_cells()} == {
        cells()[n].physical_name for n in ("NAND4", "NOR4")
    }
    assert (
        json.loads((output / "manifest.json").read_text())["spice_status"] == "not_run"
    )
