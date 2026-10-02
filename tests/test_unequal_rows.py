"""Placement semantics and a routed 2-PMOS/4-NMOS topology regression."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ortools.sat.python import cp_model

from cpcell.core.finfet import FinFET
from cpcell.core import placement, routing
from cpcell.gds.result import parse_result
from cpcell.tech.tech import FinFET_Tech
from cpcell.utility import config
from cpcell.utility.entity import Circuit, LayerStack, Model
from cpcell.utility.util import read_cdl_file


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/sram6t.cdl"
LAYER = ROOT / "input/config/GT2N_FinFET_2F_4T_4242OF0.layer"


def placement_model(p_count, n_count, aligned, columns=4):
    """Build the production occupancy/break constraints without routing costs."""
    cell = Circuit()
    for kind, count in ((Model.PMOS, p_count), (Model.NMOS, n_count)):
        for i in range(count):
            name = f"M{kind.value}{i}"
            cell.transistors[name] = SimpleNamespace(name=name, model=kind)
    model = FinFET.__new__(FinFET)
    model.circuit = cell
    model.fin_tech = SimpleNamespace(
        height_config="SH", enforce_diffusion_alignment=aligned
    )
    model.opt = cp_model.CpModel()
    model.opt.log_comment = lambda *_: None
    model.plc_ci = list(range(1, 2 * columns, 2))
    model.domain_mos_placable_ci = cp_model.Domain.FromValues(model.plc_ci)
    model.domain_mos_placable_ri = cp_model.Domain.FromValues([0, 2])
    model.pmos_placeable_row_indices = [2]
    model.nmos_placeable_row_indices = [0]
    model.transistor_vars = {}
    model.placed_tran_ci_vars = {}
    model.has_tran_at_ci_vars = {}
    model.db_pmos_cols_vars = {}
    model.db_nmos_cols_vars = {}
    model._init_transistor_vars()
    model._init_diffusion_break_vars()
    placement.diffusion_alignment(model)
    return model


class UnequalRowsTest(unittest.TestCase):
    def test_canvas_capacity_uses_larger_row(self):
        for p_count, n_count in ((2, 4), (4, 2), (2, 2)):
            with self.subTest(p=p_count, n=n_count):
                model = placement_model(p_count, n_count, False)
                circuit = model.circuit
                self.assertEqual(circuit.get_minimum_col(num_db=1), 1 + 2 * p_count + 2)
                self.assertEqual(
                    circuit.get_minimum_col(num_db=1, allow_unequal_rows=True),
                    1 + 2 * max(p_count, n_count) + 2,
                )

    def test_unequal_occupancy_requires_independent_breaks(self):
        for p_count, n_count in ((2, 4), (4, 2)):
            for aligned in (True, False):
                with self.subTest(p=p_count, n=n_count, aligned=aligned):
                    model = placement_model(p_count, n_count, aligned)
                    solver = cp_model.CpSolver()
                    status = solver.Solve(model.opt)
                    if aligned:
                        self.assertEqual(status, cp_model.INFEASIBLE)
                        continue
                    self.assertEqual(status, cp_model.OPTIMAL)
                    self.assertEqual(
                        sum(solver.Value(v) for v in model.db_pmos_cols_vars.values()),
                        4 - p_count,
                    )
                    self.assertEqual(
                        sum(solver.Value(v) for v in model.db_nmos_cols_vars.values()),
                        4 - n_count,
                    )

    def test_equal_counts_can_have_different_break_positions(self):
        for aligned in (True, False):
            model = placement_model(2, 2, aligned, columns=3)
            model.opt.Add(model.db_pmos_cols_vars[1] == 1)
            model.opt.Add(model.db_nmos_cols_vars[1] == 0)
            status = cp_model.CpSolver().Solve(model.opt)
            self.assertEqual(
                status, cp_model.INFEASIBLE if aligned else cp_model.OPTIMAL
            )

    def test_full_column_break_is_true_only_if_both_rows_are_empty(self):
        for p_break, n_break in ((0, 0), (0, 1), (1, 0), (1, 1)):
            model = placement_model(1, 1, False, columns=2)
            model.opt.Add(model.db_pmos_cols_vars[1] == p_break)
            model.opt.Add(model.db_nmos_cols_vars[1] == n_break)
            solver = cp_model.CpSolver()
            self.assertEqual(solver.Solve(model.opt), cp_model.OPTIMAL)
            self.assertEqual(solver.Value(model.db_cols_vars[1]), p_break and n_break)

    def test_first_column_need_not_pair_gates_in_independent_mode(self):
        for aligned in (True, False):
            model = placement_model(1, 1, aligned, columns=2)
            model.opt.Add(model.db_pmos_cols_vars[1] == 1)
            model.opt.Add(model.db_nmos_cols_vars[1] == 0)
            model.gate_share_pair_vars = {}
            model.gate_share_at_col_vars = {}
            model.lgg = SimpleNamespace(col_in_layer=lambda layer, ci: ci * 42)
            routing.bind_gate_sharing_to_columns(model)
            solver = cp_model.CpSolver()
            status = solver.Solve(model.opt)
            self.assertEqual(
                status, cp_model.INFEASIBLE if aligned else cp_model.OPTIMAL
            )
            if not aligned:
                self.assertEqual(solver.Value(model.gate_share_at_col_vars[84]), 0)

    def test_invalid_options_fail_before_building_or_writing_results(self):
        config.init()
        circuit = read_cdl_file(FIXTURE)[0]
        technology = FinFET_Tech("test", 2, 4, 46, LayerStack(str(LAYER)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            output = Path(directory) / "output"
            cases = (
                ({}, "allow_unequal_rows"),
                (
                    {"allow_unequal_rows": {"value": True}},
                    "require enforce_diffusion_alignment",
                ),
                ({"allow_unequal_rows": {"value": "true"}}, "must be a boolean"),
                ({"enforce_diffusion_alignment": {"value": 0}}, "must be a boolean"),
            )
            for options, message in cases:
                with self.subTest(options=options):
                    path.write_text(json.dumps(options))
                    with self.assertRaisesRegex(ValueError, message):
                        FinFET(circuit, technology, cell_config=path, output_dir=output)
                    self.assertFalse(output.exists())
                    self.assertFalse(technology.allow_unequal_rows)
                    self.assertTrue(technology.enforce_diffusion_alignment)

    def test_cell_options_override_technology_without_leaking_to_other_cells(self):
        circuit = placement_model(2, 2, True).circuit
        technology = FinFET_Tech(
            "test",
            2,
            4,
            46,
            LayerStack(str(LAYER)),
            allow_unequal_rows=True,
            enforce_diffusion_alignment=False,
        )
        with tempfile.TemporaryDirectory() as directory:
            config.generate_config(4, "FinFET", "SH", ["TEST"], directory)
            path = Path(directory) / "config/TEST.json"
            for inherit in (False, True):
                with self.subTest(inherit=inherit):
                    settings = json.loads(path.read_text())
                    if inherit:
                        settings.pop("allow_unequal_rows")
                        settings.pop("enforce_diffusion_alignment")
                    path.write_text(json.dumps(settings))
                    model = FinFET.__new__(FinFET)
                    # Stop after option resolution/canvas sizing: this tests
                    # constructor configuration, not a second routing solve.
                    with patch.object(
                        FinFET,
                        "_init_graph",
                        side_effect=RuntimeError("graph boundary"),
                    ):
                        with self.assertRaisesRegex(RuntimeError, "graph boundary"):
                            model.__init__(
                                circuit,
                                technology,
                                cell_config=path,
                                output_dir=directory,
                            )
                    self.assertEqual(model.fin_tech.allow_unequal_rows, inherit)
                    self.assertEqual(
                        model.fin_tech.enforce_diffusion_alignment, not inherit
                    )
                    self.assertTrue(technology.allow_unequal_rows)
                    self.assertFalse(technology.enforce_diffusion_alignment)

    def test_route_six_transistor_cell_with_independent_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            config.generate_config(4, "FinFET", "SH", ["SRAM6T"], output)
            path = output / "config/SRAM6T.json"
            settings = json.loads(path.read_text())
            for key, value in {
                "allow_unequal_rows": True,
                "enforce_diffusion_alignment": False,
                "insert_num_db": 2,
                "num_search_workers": 4,
            }.items():
                settings[key]["value"] = value
            settings["max_time"] = {"value": True, "time": 30}
            settings["use_relative_gap"] = {"value": True, "perc": 0.05}
            path.write_text(json.dumps(settings))
            env = dict(
                os.environ,
                PYTHONDONTWRITEBYTECODE="1",
                MPLCONFIGDIR=str(output / "mpl"),
            )
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cpcell",
                    "--layer",
                    str(LAYER),
                    "--netlist",
                    str(FIXTURE),
                    "--cell_names",
                    "SRAM6T",
                    "--cell_config",
                    str(path),
                    "--output_dir",
                    str(output),
                    "--flag_log_constraints",
                    "False",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            tech, pmos, nmos, routes = parse_result(output / "result/SRAM6T.res")
            self.assertEqual((len(pmos), len(nmos)), (2, 4))
            self.assertEqual(set(tech.io_pins), {"WL", "BL", "BLB"})
            self.assertTrue({"Q", "QB", "WL", "BL", "BLB"} <= {r.net for r in routes})
            self.assertNotEqual({t.x for t in pmos}, {t.x for t in nmos})
            # Every row-local break agrees with actual placement, and no PC
            # route uses a gate in the missing transistor's row.
            values = {}
            for line in (output / "result/SRAM6T.var").read_text().splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].startswith("db_"):
                    values[parts[1]] = int(parts[0])
            for kind, transistors in (("pmos", pmos), ("nmos", nmos)):
                positions = {t.x for t in transistors}
                for key, value in values.items():
                    if not key.startswith(f"db_{kind}_ci_"):
                        continue
                    ci = int(key.rsplit("_", 1)[1])
                    self.assertEqual(value, int(ci * 42 not in positions))
                    if value:
                        for route in routes:
                            for layer, row, col in (
                                (route.metal_0, route.row_0, route.col_0),
                                (route.metal_1, route.row_1, route.col_1),
                            ):
                                own_row = row >= 96 if kind == "pmos" else row <= 48
                                self.assertFalse(
                                    layer == 0 and col == (ci + 1) * 42 and own_row
                                )


if __name__ == "__main__":
    unittest.main()
