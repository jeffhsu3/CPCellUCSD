"""Reproduce the reference footprint/ports through the actual CPCell solver.

External DRC/LVS and Xyce checks are exposed by the example's --spice command;
these regressions require only this repository's Python dependencies.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import klayout.db as pya

from cpcell.core.finfet import FinFET
from cpcell.gds.result import parse_boundary_ports, parse_result
from cpcell.tech.tech import FinFET_Tech
from cpcell.utility.entity import LayerStack

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/gt2n_sram"


def region(layout, cell, layer, datatype=0):
    return pya.Region(cell.shapes(layout.layer(layer, datatype))).merged()


class ReferenceGridTest(unittest.TestCase):
    def test_nonuniform_tracks_are_used_by_the_routing_graph(self):
        model = FinFET.__new__(FinFET)
        model.fin_tech = FinFET_Tech(
            "test", 2, 4, 46, LayerStack(str(EXAMPLE / "reference.layer"))
        )
        model.num_col = 11
        model._init_graph()
        self.assertEqual(model.lgg.rows_in_layer("PC"), [28, 84, 144, 204])
        self.assertEqual(model.lgg.rows_in_layer("M1"), [28, 84, 144, 204])
        self.assertEqual(model.lgg.cols_in_layer("M1"), [42, 168, 252, 378])
        self.assertEqual(model.lgg.cols_in_layer("PC"), list(range(0, 462, 42)))

    def test_invalid_explicit_tracks_are_rejected(self):
        original = json.loads((EXAMPLE / "reference.layer").read_text())
        for layer, tracks in (
            ("PC", [0, 42]),
            ("M0", []),
            ("M0", [42, 14]),
            ("M0", [14, 14]),
            ("M0", [True, 42]),
            ("M1", [-1, 21]),
            ("M0", [14.1, 42]),
            ("M0", [float("inf")]),
        ):
            data = json.loads(json.dumps(original))
            data[layer]["tracks"] = tracks
            with (
                self.subTest(layer=layer, tracks=tracks),
                self.assertRaises(ValueError),
            ):
                LayerStack(data)


class SRAMReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)
        run = subprocess.run(
            [
                sys.executable,
                str(EXAMPLE / "reproduce.py"),
                "--output-dir",
                str(cls.output),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            env=dict(
                os.environ,
                PYTHONDONTWRITEBYTECODE="1",
                MPLCONFIGDIR=str(cls.output / "mpl"),
            ),
        )
        if run.returncode:
            raise AssertionError(run.stdout + run.stderr)

    def test_solver_matches_reference_placement_and_boundary_ports(self):
        result = self.output / "result/SRAM6T_REF.res"
        tech, pmos, nmos, routes = parse_result(result)
        self.assertEqual(tech.layout_profile, "gt2n_sram")
        self.assertEqual(tech.col * tech.cp_pitch, 210)
        expected = {
            "MPDLS0": (126, False),
            "MPDRS0": (210, True),
            "MPULS0": (126, False),
            "MPURS0": (210, True),
            "MPGLS0": (42, False),
            "MPGRS0": (294, True),
        }
        self.assertEqual({t.name: (t.x, t.flip) for t in pmos + nmos}, expected)
        ports = parse_boundary_ports(result)
        self.assertEqual(
            {(p["net"], p["side"], p["row"], p["col"]) for p in ports},
            {
                ("WL", "left", 28, 0),
                ("WL", "right", 28, 378),
                ("BL", "bottom", 28, 42),
                ("BL", "top", 204, 42),
                ("BLN", "bottom", 28, 378),
                ("BLN", "top", 204, 378),
            },
        )
        self.assertTrue(all(max(r.metal_0, r.metal_1) <= 2 for r in routes))
        for route in routes:
            if (route.metal_0, route.metal_1) == (0, 1):
                gate = (route.col_0 / 42) % 2 == 0
                self.assertIn(route.row_0, (28, 144) if gate else (84, 204))

    def test_both_sheet_widths_export_reference_frame_and_pins(self):
        for width in (13, 31):
            with self.subTest(width=width):
                layout = pya.Layout()
                layout.read(str(self.output / f"w{width}/cpcell.gds"))
                cell = layout.cell(f"gt2n_sram_cell_6t_w{width}_lvt")
                self.assertEqual(layout.dbu, 0.0005)
                self.assertEqual(
                    region(layout, cell, 235, 250).bbox(), pya.Box(0, 0, 420, 288)
                )
                self.assertEqual(
                    region(layout, cell, 20, 251).bbox(), pya.Box(0, 16, 420, 40)
                )
                bitlines = pya.Region(pya.Box(28, 0, 56, 288)) + pya.Region(
                    pya.Box(364, 0, 392, 288)
                )
                self.assertTrue((region(layout, cell, 25, 251) ^ bitlines).is_empty())
                self.assertTrue(region(layout, cell, 30).is_empty())
                self.assertEqual(
                    {
                        polygon.bbox().height()
                        for polygon in region(layout, cell, 2).each()
                    },
                    {2 * width},
                )
                self.assertEqual(
                    {
                        s.text.string
                        for s in cell.shapes(layout.layer(8, 251)).each()
                        if s.is_text()
                    },
                    {"vdd", "vss"},
                )
                cdl = (self.output / f"w{width}/cpcell.cdl").read_text()
                self.assertEqual(cdl.count(f"W={width / 1000:g}u"), 6)
                self.assertEqual(cdl.count("nmos_lvt"), 4)
                self.assertEqual(cdl.count("pmos_lvt"), 2)

    def test_report_distinguishes_generation_from_external_validation(self):
        report = json.loads((self.output / "report.json").read_text())
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["verification_status"], "not_run")
        self.assertEqual(report["footprint_nm"], [210, 144])
        self.assertEqual(set(report["cells"]), {"w13", "w31"})


if __name__ == "__main__":
    unittest.main()
