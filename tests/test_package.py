"""Keep CPCell imports independent of other projects' src packages."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


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
