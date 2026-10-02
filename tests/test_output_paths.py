"""Exercise output-directory handling through the actual Python entry points."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cpcell.gds.result import parse_result


ROOT = Path(__file__).resolve().parents[1]


class OutputPathsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def run_module(self, module, *args, cwd=ROOT):
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, (str(ROOT), env.get("PYTHONPATH")))
        )
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        run = subprocess.run(
            [sys.executable, "-m", module, *map(str, args)],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return run

    def test_config_creates_absolute_and_relative_output_paths(self):
        for output in (
            self.directory / "absolute run" / "nested",
            Path("relative run") / "nested",
        ):
            with self.subTest(output=output):
                target = self.directory / output
                self.assertFalse(target.exists())
                for _ in range(2):  # Existing directories must work too.
                    self.run_module(
                        "cpcell.utility.config",
                        "--cell_names",
                        "INV_X1",
                        "--output_dir",
                        output,
                        cwd=self.directory,
                    )
                    config = json.loads((target / "config/INV_X1.json").read_text())
                    self.assertIn("model_preset", config)

    def test_direct_solve_creates_output_tree(self):
        self.run_module(
            "cpcell.utility.config",
            "--cell_names",
            "INV_X1",
            "--output_dir",
            self.directory / "inputs",
        )
        config_path = self.directory / "inputs/config/INV_X1.json"
        config = json.loads(config_path.read_text())
        config["num_search_workers"]["value"] = 1
        config["max_time"] = {"value": True, "time": 10}
        config_path.write_text(json.dumps(config))

        for log_constraints in ("False", "True"):
            with self.subTest(log_constraints=log_constraints):
                run_config = dict(config)
                if log_constraints == "False":
                    # Previously generated configs omit the new row options.
                    run_config.pop("allow_unequal_rows", None)
                    run_config.pop("enforce_diffusion_alignment", None)
                    run_config.pop("boundary_ports", None)
                    run_config.pop("placement_constraints", None)
                config_path.write_text(json.dumps(run_config))
                output = self.directory / f"fresh solve {log_constraints}" / "nested"
                self.assertFalse(output.exists())
                self.run_module(
                    "cpcell.main",
                    "--layer",
                    ROOT / "input/config/GT2N_FinFET_2F_4T_4242OF0.layer",
                    "--netlist",
                    ROOT / "input/cdl/PROBE3_2F4T.cdl",
                    "--cell_names",
                    "INV_X1",
                    "--cell_config",
                    config_path,
                    "--output_dir",
                    output,
                    "--flag_log_constraints",
                    log_constraints,
                )
                result = output / "result/INV_X1.res"
                tech, pmos, nmos, metals = parse_result(result)
                self.assertEqual(tech.cp_pitch, 42)
                self.assertTrue(pmos and nmos and metals)
                self.assertGreater((output / "result/INV_X1.var").stat().st_size, 0)
                self.assertTrue((output / "pinLayouts/INV_X1.pinlayout").is_file())
                self.assertTrue((output / "view").is_dir())
                if log_constraints == "True":
                    self.assertGreater(
                        (output / "constraint/INV_X1.log").stat().st_size, 0
                    )


if __name__ == "__main__":
    unittest.main()
