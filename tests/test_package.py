"""Keep CPCell imports independent of other projects' src packages."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from cpcell.utility import config


def test_pin_defaults_work_outside_checkout_and_allow_local_overrides(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("PWR_NET_NAMES", "GND_NET_NAMES", "INPUT_NET_NAMES", "OUTPUT_NET_NAMES"):
        monkeypatch.setattr(config, name, getattr(config, name, []), raising=False)
    config.init()
    assert "A" in config.INPUT_NET_NAMES and "Y" in config.OUTPUT_NET_NAMES
    (tmp_path / "input").mkdir()
    (tmp_path / "input/pin_input_collection.json").write_text('{"CUSTOM_INPUT": 1}')
    config.init()
    assert set(config.INPUT_NET_NAMES) == {"CUSTOM_INPUT"}
    assert "Y" in config.OUTPUT_NET_NAMES


ROOT = Path(__file__).resolve().parents[1]


class PackageTest(unittest.TestCase):
    def test_unrelated_src_package_does_not_affect_entry_points(self):
        with tempfile.TemporaryDirectory() as directory:
            foreign_src = Path(directory) / "src"
            foreign_src.mkdir()
            (foreign_src / "__init__.py").write_text(
                'raise RuntimeError("CPCell imported another project\'s src package")\n'
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = os.pathsep.join((directory, str(ROOT)))
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            for module in (
                "cpcell",
                "cpcell.main",
                "cpcell.utility.config",
                "cpcell.gds.gds_GT2N_SH",
                "cpcell.gds.gds_FinFET_SH",
            ):
                with self.subTest(module=module):
                    run = subprocess.run(
                        [sys.executable, "-m", module, "--help"],
                        cwd=directory,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                    self.assertIn("usage:", run.stdout)


if __name__ == "__main__":
    unittest.main()
