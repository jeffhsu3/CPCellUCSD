"""Generate the GT2N sram fixture with CPCell only.

External verification lives in chipforge_gt2n/evals/cpcell_reference.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cpcell.gds.gds_GT2N_SH import GT2NLayout
from cpcell.gds.result import parse_result
from cpcell.utility.config import generate_config


def cell_name(width):
    return f"gt2n_sram_cell_6t_w{width}_lvt"


def schematic(width):
    """Physical-size CDL corresponding to the abstract two-fin solver input."""
    lines = []
    for line in (EXAMPLE / "reference.cdl").read_text().splitlines():
        if line.startswith(".SUBCKT"):
            lines.append(f".subckt {cell_name(width)} WL BL BLN vdd vss")
        elif line.startswith(".ENDS"):
            lines.append(f".ends {cell_name(width)}")
        elif line.startswith("M"):
            name, drain, gate, source, bulk, model, *_ = line.split()
            nets = [
                v.lower() if v in ("VDD", "VSS") else v
                for v in (drain, gate, source, bulk)
            ]
            lines.append(
                f"{name} {' '.join(nets)} {model}_lvt W={width / 1000:g}u L=0.014u M=1"
            )
    return "\n".join(lines) + "\n"


def solve(output, timeout=30):
    generate_config(4, "FinFET", "SH", ["SRAM6T_REF"], output)
    config_file = output / "config/SRAM6T_REF.json"
    settings = json.loads(config_file.read_text())
    settings.update(json.loads((EXAMPLE / "constraints.json").read_text()))
    settings["max_time"] = {"value": True, "time": timeout}
    config_file.write_text(json.dumps(settings, indent=2) + "\n")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(ROOT), env.get("PYTHONPATH")))
    )
    env.setdefault("MPLCONFIGDIR", str(output / "mpl"))
    with (output / "solve.log").open("w") as log:
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "cpcell",
                "--layer",
                str(EXAMPLE / "reference.layer"),
                "--netlist",
                str(EXAMPLE / "reference.cdl"),
                "--cell_names",
                "SRAM6T_REF",
                "--cell_config",
                str(config_file),
                "--output_dir",
                str(output),
                "--flag_log_constraints",
                "False",
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=timeout + 30,
        )
    if run.returncode:
        raise RuntimeError(f"CPCell failed; see {output / 'solve.log'}")
    result = output / "result/SRAM6T_REF.res"
    tech, pmos, nmos, routes = parse_result(result)
    if (tech.col * tech.cp_pitch, len(pmos), len(nmos)) != (210, 2, 4):
        raise RuntimeError(
            "The solve did not reproduce the reference footprint/device count"
        )
    if any(max(r.metal_0, r.metal_1) > 2 for r in routes):
        raise RuntimeError("Unexpected routing above M1")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "build/gt2n_sram_reference"
    )
    parser.add_argument(
        "--widths", nargs="+", type=int, choices=(13, 31), default=[13, 31]
    )
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "verification_status": "not_run", "cells": {}}
    try:
        print("Solving fixed reference placement with CPCell...", flush=True)
        result = solve(output)
        report["footprint_nm"] = [210, 144]
        report["result"] = str(result)
        report["input_sha256"] = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                EXAMPLE / "reference.cdl",
                EXAMPLE / "reference.layer",
                output / "config/SRAM6T_REF.json",
            )
        }
        for width in dict.fromkeys(args.widths):
            directory = output / f"w{width}"
            directory.mkdir(exist_ok=True)
            # A fresh isolated library is written each time; export validation
            # finishes before its existing counterpart can be replaced.
            GT2NLayout(
                result,
                cell_name(width),
                directory / "cpcell.gds",
                nanosheet_width=width,
            )
            (directory / "cpcell.cdl").write_text(schematic(width))
            report["cells"][f"w{width}"] = {"gds": str(directory / "cpcell.gds")}
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
