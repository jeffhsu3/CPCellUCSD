"""A standalone solver/export regression, independent of GT2N library tooling."""

import json
import os
from pathlib import Path
import subprocess
import sys

import klayout.db as db

from cpcell.gds.result import parse_result

ROOT = Path(__file__).resolve().parents[1]


def test_nand4_example_routes_and_exports_both_widths(tmp_path):
    output = tmp_path / "nand4"
    run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "examples/gt2n_logic/reproduce.py"),
            "--output-dir",
            str(output),
            "--widths",
            "31",
            "13",
        ],
        cwd=tmp_path,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "passed"
    assert report["verification_status"] == "not_run"
    tech, pmos, nmos, routes = parse_result(output / "result/NAND4.res")
    assert tech.layout_profile == "gt2n_logic"
    assert len(pmos) == len(nmos) == 4
    assert routes
    assert (
        report["cells"]["31"]["footprint_nm"] == report["cells"]["13"]["footprint_nm"]
    )
    for width in (13, 31):
        entry = report["cells"][str(width)]
        assert entry["footprint_nm"][1] == 144
        gds = db.Layout()
        gds.read(str(output / entry["gds"]))
        cell = gds.cell(entry["name"])
        assert cell is not None
        act = db.Region(cell.shapes(gds.layer(2, 0)))
        assert {p.bbox().height() for p in act.each()} == {2 * width}
        m1 = db.Region(cell.shapes(gds.layer(25, 0))).merged()
        assert m1.bbox().bottom >= 20 and m1.bbox().top <= 268
        contacts = db.Region(cell.shapes(gds.layer(12, 0)))
        assert (contacts.sized(0, 12) & act).is_empty()
        vs = db.Region(cell.shapes(gds.layer(22, 0)))
        assert (vs - m1).is_empty()
        assert (vs - db.Region(cell.shapes(gds.layer(20, 0)))).is_empty()
        pins = {
            s.text.string for s in cell.shapes(gds.layer(25, 251)).each() if s.is_text()
        }
        assert pins == {"A", "B", "C", "D", "Y"}
        assert (output / f"nand4_w{width}.cdl").read_text().count(
            f"W={width / 1000:g}u"
        ) == 8
