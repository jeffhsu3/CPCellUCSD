"""Generate the GT2N thin fixture with CPCell only.

External verification lives in chipforge_gt2n/evals/cpcell_reference.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cpcell.gds.gds_GT2N_thin import export_thin
from cpcell.tech.gt2n_thin import ThinProblem, design_from_circuit
from cpcell.utility import config
from cpcell.utility.util import read_cdl_file


def load_design(n_width, p_width):
    # The CDL reader uses these globals; explicit supply names also allow this
    # example to run outside the repository working directory.
    config.PWR_NET_NAMES = ["VDD"]
    config.GND_NET_NAMES = ["VSS"]
    circuit = read_cdl_file(EXAMPLE / "reference.cdl")[0]
    settings = json.loads((EXAMPLE / "constraints.json").read_text())
    return design_from_circuit(circuit, settings, n_width, p_width)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "build/gt2n_thin_reference"
    )
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=("31x31", "13x13", "31x13", "13x31"),
        default=["31x31", "13x13", "31x13"],
    )
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    variants = list(dict.fromkeys(args.variants))
    report = {
        "status": "running",
        "verification_status": "not_run",
        "spice_status": "not_run",
        "profile": "gt2n_thin_backside",
        "cells": {},
        "input_sha256": {
            name: hashlib.sha256((EXAMPLE / name).read_bytes()).hexdigest()
            for name in ("reference.cdl", "constraints.json")
        },
    }
    try:
        for variant in variants:
            n, p = map(int, variant.split("x"))
            print(f"{variant}: solving fixed four-band placement...", flush=True)
            problem = ThinProblem(load_design(n, p))
            solution = problem.solve(args.timeout)
            directory = output / variant
            directory.mkdir(exist_ok=True)
            (directory / "solution.json").write_text(
                json.dumps(solution, indent=2) + "\n"
            )
            (directory / "cpcell.cdl").write_text(problem.schematic())
            export_thin(solution, directory / "cpcell.gds")
            report["cells"][variant] = {
                "footprint_nm": [problem.width, problem.height],
                "gds": str(directory / "cpcell.gds"),
                "solver": {
                    key: solution[key] for key in ("status", "objective", "wall_time")
                },
            }
            print(
                f"{variant}: {solution['status']}, {problem.width} x {problem.height} nm",
                flush=True,
            )
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        raise
    finally:
        (output / "report.json").write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n"
        )
    print(f"Results: {output / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
