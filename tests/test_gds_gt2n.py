"""Geometry and integration checks; these do not replace GT2N DRC/LVS."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import klayout.db as pya

from src.gds.gds_GT2N_SH import GT2NLayout
from src.gds.gds_FinFET_SH import FinFETLayout
from src.gds.result import parse_result


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/gt2n_inv.res"


def region(layout, cell, number, datatype=0):
    index = layout.find_layer(number, datatype)
    return pya.Region() if index is None else pya.Region(cell.shapes(index)).merged()


class GT2NExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.result = self.directory / "cell.res"
        self.result.write_text(FIXTURE.read_text())
        self.gds = self.directory / "nested/library.gds"

    def export(self, name="INV_X1", **kwargs):
        GT2NLayout(self.result, name, self.gds, **kwargs)
        layout = pya.Layout()
        layout.read(str(self.gds))
        return layout, layout.cell(name)

    def change(self, old, new):
        text = self.result.read_text()
        self.assertIn(old, text)
        self.result.write_text(text.replace(old, new))

    def test_native_layers_dimensions_and_power(self):
        layout, cell = self.export()
        self.assertEqual(layout.dbu, 0.0005)
        self.assertEqual(region(layout, cell, 235, 250).bbox(), pya.Box(0, 0, 168, 288))
        self.assertEqual(region(layout, cell, 2).area(), 2 * 168 * 26)
        self.assertEqual(region(layout, cell, 8).area(), 2 * 168 * 64)
        for number, width, height in (
            (9, 32, 22),
            (11, 26, 24),
            (12, 28, 24),
            (22, 28, 24),
        ):
            boxes = [p.bbox() for p in region(layout, cell, number).each()]
            self.assertTrue(boxes, number)
            self.assertTrue(
                all(b.width() == width and b.height() == height for b in boxes), number
            )
        # No legacy CA/SDT layers, or FinFET M0 power rails.
        self.assertIsNone(layout.find_layer(14, 0))
        self.assertIsNone(layout.find_layer(88, 0))
        m0 = region(layout, cell, 20)
        self.assertTrue((m0 & region(layout, cell, 8)).is_empty())
        sdcon = region(layout, cell, 10)
        self.assertTrue((region(layout, cell, 9) - sdcon).is_empty())
        self.assertTrue((region(layout, cell, 11) - sdcon).is_empty())
        # Matching P/N output diffusions are joined by SDCON.
        self.assertFalse((sdcon & pya.Region(pya.Box(26, 120, 58, 168))).is_empty())

    def test_pin_rectangles_and_text(self):
        layout, cell = self.export()
        for number, names in (
            (8, {"VSS", "VDD"}),
            (20, {"I", "ZN"}),
            (25, {"I", "ZN"}),
        ):
            shapes = cell.shapes(layout.layer(number, 251))
            self.assertEqual(
                {s.text.string for s in shapes.each() if s.is_text()}, names
            )
            self.assertFalse(region(layout, cell, number, 251).is_empty())
            self.assertTrue(
                (
                    region(layout, cell, number, 251) - region(layout, cell, number)
                ).is_empty()
            )

    def test_width_and_vt_flavors(self):
        for width in (13, 31):
            for vt, marker in (
                ("lvt", None),
                ("elvt", 94),
                ("ulvt", 95),
                ("svt", 96),
                ("hvt", 97),
            ):
                with self.subTest(width=width, vt=vt):
                    layout, cell = self.export(nanosheet_width=width, vt=vt)
                    self.assertEqual(
                        region(layout, cell, 2).area(), 2 * 168 * width * 2
                    )
                    for number in (94, 95, 96, 97):
                        self.assertEqual(
                            region(layout, cell, number).is_empty(), number != marker
                        )

    def test_flipped_transistor_power_side(self):
        self.change(
            "NF   84.0    48.0    42.0      ZN    -1.0     VSS",
            "F    84.0    48.0    -1.0     VSS    42.0      ZN",
        )
        layout, cell = self.export()
        # Physical right-side supply remains at x=63 nm after swapping S/D.
        self.assertEqual(region(layout, cell, 9).bbox().left, 110)

    def test_different_gate_nets_and_unaligned_rows(self):
        self.change("84.0     I   PMOS", "84.0     B   PMOS")
        layout, cell = self.export()
        self.assertFalse(
            (region(layout, cell, 5) & pya.Region(pya.Box(70, 134, 98, 154))).is_empty()
        )
        # A shifted PMOS row must not be truncated by zip(PMOS, NMOS).
        self.change(
            "COL                                2",
            "COL                                3",
        )
        self.change(
            "MM1S0  42.0  96.0    NF   84.0    48.0    42.0      ZN    -1.0     VDD  84.0     B   PMOS",
            "MM1S0 126.0  96.0    NF   84.0    48.0   126.0      ZN    -1.0     VDD 168.0     B   PMOS",
        )
        layout, cell = self.export()
        self.assertEqual(region(layout, cell, 2).bbox().right, 252)

    def test_reversed_routes_m2_and_via_only_pins(self):
        self.change(
            "1     0.0   0.0   ZN  =>  1      0.0   42.0   ZN",
            "1     0.0  42.0   ZN  =>  1      0.0    0.0   ZN",
        )
        self.change(
            "** Technology Parameters **",
            "2 144 84 I => 3 144 84 I\n3 144 84 I => 3 144 126 I\n\n** Technology Parameters **",
        )
        layout, cell = self.export()
        self.assertEqual(region(layout, cell, 27).bbox().width(), 28)
        self.assertFalse(region(layout, cell, 30, 251).is_empty())
        # Removing the M2 segment still leaves a contacted, labelled landing.
        self.change("3 144 84 I => 3 144 126 I\n", "")
        layout, cell = self.export()
        self.assertFalse(region(layout, cell, 30, 251).is_empty())

    def test_append_replace_and_existing_dbu(self):
        layout, cell = self.export()
        # Existing callers can use the original exporter's 0.25 nm DBU.
        layout.dbu = 0.00025
        layout.write(str(self.gds))
        before = region(layout, cell, 2).area()
        parent = layout.create_cell("PARENT")
        parent.insert(pya.CellInstArray(cell.cell_index(), pya.Trans()))
        layout.write(str(self.gds))
        layout, cell = self.export("SECOND")
        self.assertEqual(layout.dbu, 0.00025)
        self.assertEqual(region(layout, layout.cell("INV_X1"), 2).area(), before)
        self.assertEqual(region(layout, cell, 235, 250).bbox(), pya.Box(0, 0, 336, 576))
        layout, cell = self.export(nanosheet_width=31)
        self.assertEqual(len(list(layout.each_cell())), 3)
        instance = next(layout.cell("PARENT").each_inst())
        self.assertEqual(instance.cell_index, cell.cell_index())
        self.assertEqual(region(layout, cell, 2).area(), 2 * 336 * 124)

    def test_invalid_results_do_not_modify_library(self):
        self.export()
        original = self.gds.read_bytes()
        variants = (
            (
                "CPP                             42.0",
                "CPP                             45.0",
                "cp_pitch",
            ),
            (
                "2     0.0   0.0   ZN  =>  2    144.0    0.0   ZN",
                "2 0 0 ZN => 2 144 84 ZN",
                "direction",
            ),
            (
                "0     0.0  42.0   ZN  =>  1      0.0   42.0   ZN",
                "0 0 -42 ZN => 1 0 -42 ZN",
                "canvas",
            ),
            (
                "0     0.0  42.0   ZN  =>  1      0.0   42.0   ZN",
                "0 0 42 ZN => 1 0 42 WRONG",
                "Net mismatch",
            ),
            (
                "0     0.0  42.0   ZN  =>  1      0.0   42.0   ZN",
                "broken route",
                "expected MET",
            ),
            (
                "** Technology Parameters **",
                "1 0 84 I => 1 0 126 I\n** Technology Parameters **",
                "short",
            ),
        )
        for old, new, error in variants:
            with self.subTest(error=error):
                self.result.write_text(FIXTURE.read_text())
                self.change(old, new)
                with self.assertRaisesRegex(ValueError, error):
                    self.export()
                self.assertEqual(self.gds.read_bytes(), original)

    def test_reject_non_gt2n_library(self):
        self.gds.parent.mkdir()
        layout = pya.Layout()
        layout.create_cell("PROBE3").shapes(layout.layer(100, 0)).insert(
            pya.Box(0, 0, 100, 100)
        )
        layout.write(str(self.gds))
        original = self.gds.read_bytes()
        with self.assertRaisesRegex(ValueError, "not a GT2N library"):
            self.export()
        self.assertEqual(self.gds.read_bytes(), original)

    def test_shared_reader_and_finfet_backend(self):
        self.assertEqual(
            FinFETLayout._parse_result(None, FIXTURE), parse_result(FIXTURE)
        )
        legacy = self.directory / "finfet.gds"
        FinFETLayout(FIXTURE, "LEGACY", str(legacy))
        layout = pya.Layout()
        layout.read(str(legacy))
        cell = layout.cell("LEGACY")
        self.assertEqual(layout.dbu, 0.00025)
        self.assertFalse(region(layout, cell, 100).is_empty())
        self.assertFalse(region(layout, cell, 15).is_empty())

    def test_make_integration_and_failure_propagation(self):
        output = self.directory / "flow"
        (output / "result").mkdir(parents=True)
        (output / "result/INV_X1.res").write_text(FIXTURE.read_text())
        command = [
            "make",
            "gt2n_gds",
            f"PYTHON={sys.executable}",
            f"OUT_DIR={output}",
            f"GDS_FILE={self.gds}",
            "CELL_NAME=INV_X1",
        ]
        run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertNotIn("relocatePin", run.stdout)
        self.assertTrue(self.gds.exists())
        command[-1] = "CELL_NAME=MISSING INV_X1"
        run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)


if __name__ == "__main__":
    unittest.main()
