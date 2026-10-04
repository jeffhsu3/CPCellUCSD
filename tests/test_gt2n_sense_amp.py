"""Standalone sense-amp geometry and matching; external DRC/LVS/SPICE is opt-in."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import klayout.db as db
import pytest

from cpcell.gds.gds_GT2N_SH import LAYERS
from cpcell.gds.gds_GT2N_sense_amp import export_sense_amp
from cpcell.tech.gt2n_sense_amp import SenseAmpProblem, default_design

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def solution():
    return SenseAmpProblem().solve()


def reflected(region):
    # Reflection about x=147 nm, with coordinates in half-nm units.
    return region.transformed(db.Trans(db.Trans.M90, 588, 0))


def test_exact_devices_and_mirrored_geometry(solution, tmp_path):
    path = tmp_path / "cell.gds"
    export_sense_amp(json.loads(json.dumps(solution)), path)
    layout = db.Layout()
    layout.read(str(path))
    cell = layout.cell(solution["design"]["name"])
    assert layout.dbu == 0.0005
    regions = {
        name: db.Region(cell.shapes(layout.layer(LAYERS[name], 0))).merged()
        for name in ("ACT", "GATE", "GCUT", "SDCON", "VSD", "VG", "VBPR")
    }
    for name, region in regions.items():
        assert (region ^ reflected(region)).is_empty(), name
    assert {p.bbox().height() for p in regions["ACT"].each()} == {62}
    assert {p.bbox().width() for p in regions["GATE"].each()} == {28}
    assert regions["ACT"].count() == 2
    assert regions["GATE"].count() == 8  # Six slots plus two dummy gates.
    labels = {
        s.text.string
        for s in cell.shapes(layout.layer(LAYERS["M1"], 251)).each()
        if s.is_text()
    }
    assert labels == {"SA", "SAN", "SAE", "SAPRECHN", "QA", "QAN"}
    schematic = SenseAmpProblem().schematic()
    assert schematic.count("W=0.031u L=0.014u M=1") == 12
    assert schematic.count("nmos_lvt") == schematic.count("pmos_lvt") == 6
    assert "MTAILA T SAE vss vss nmos_lvt" in schematic
    assert "MTAILB T SAE vss vss nmos_lvt" in schematic
    assert "MPCA1 QA SAPRECHN vdd vdd pmos_lvt" in schematic
    assert "MPCA2 QA SAPRECHN vdd vdd pmos_lvt" in schematic


def test_routes_have_mirrored_inputs_and_balanced_outputs(solution):
    problem = SenseAmpProblem(solution["design"])
    metals = ("M0", "M1", "M2")
    regions = {
        (net, layer): db.Region() for net in problem.router.nets for layer in metals
    }
    for route in solution["routes"]:
        for shape in problem.edges[route["edge"]].shapes:
            if shape.layer in metals:
                regions[route["net"], shape.layer].insert(db.Box(*shape.box))
    for layer in metals:
        assert (regions["SA", layer] ^ reflected(regions["SAN", layer])).is_empty()
        for net in ("SAE", "T"):
            assert (regions[net, layer] ^ reflected(regions[net, layer])).is_empty()
        a, b = (regions[net, layer].merged() for net in ("QA", "QAN"))
        assert a.area() == b.area()
        assert a.perimeter() == b.perimeter()
    assert solution["route_metrics"]["QA"] == solution["route_metrics"]["QAN"]
    assert solution["route_metrics"]["SA"] == solution["route_metrics"]["SAN"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("width_nm", 13),
        ("length_nm", 15),
        ("fingers", 2),
        ("slot", 2),
        ("vt", "svt"),
        ("gate", "wrong"),
    ],
)
def test_unsupported_device_changes_are_rejected(field, value):
    design = default_design()
    design["devices"][0][field] = value
    with pytest.raises(ValueError, match="Unsupported sizing, placement or topology"):
        SenseAmpProblem(design)


def test_required_matching_and_finger_groups():
    design = default_design()
    design["matched_devices"].pop()
    with pytest.raises(ValueError, match="matching pairs"):
        SenseAmpProblem(design)
    design = default_design()
    design["finger_groups"]["TAIL"].pop()
    with pytest.raises(ValueError, match="remain explicit"):
        SenseAmpProblem(design)


def test_corrupt_route_rejected_before_overwriting(solution, tmp_path):
    path = tmp_path / "preserved.gds"
    path.write_bytes(b"previous result")
    bad = deepcopy(solution)
    bad["routes"] = [r for r in bad["routes"] if r["net"] != "SA"]
    with pytest.raises(ValueError, match="mirror matching"):
        export_sense_amp(bad, path)
    assert path.read_bytes() == b"previous result"
    bad = deepcopy(solution)
    bad["height_nm"] += 1
    with pytest.raises(ValueError, match="dimensions"):
        export_sense_amp(bad, path)
    assert path.read_bytes() == b"previous result"


def test_cli_from_foreign_directory_and_manifest(tmp_path):
    output = tmp_path / "new" / "results"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "examples/gt2n_sense_amp/reproduce.py"),
            "--output-dir",
            str(output),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        timeout=90,
    )
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "passed"
    assert report["verification_status"] == report["spice_status"] == "not_run"
    assert report["footprint_nm"] == [294, 288]
    manifest = json.loads((output / "manifest.json").read_text())
    assert set(manifest) == {"solution.json", "cell.gds", "cell.cdl", "report.json"}
    for name, digest in manifest.items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
