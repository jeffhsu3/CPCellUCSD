"""Place, route and export one NAND4 on the GT2N logic grid using only CPCell."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cpcell.gds.gds_GT2N_SH import GT2NLayout
from cpcell.utility.config import generate_config


def configure(output, timeout):
    generate_config(4, "FinFET", "SH", ["NAND4"], output)
    path = output / "config/NAND4.json"
    config = json.loads(path.read_text())
    values = {
        "layout_profile": "gt2n_logic",
        "routing_layers": ["M0", "M1", "M2"],
        "minimum_gate_cut_length": 1,
        "supervia": ["M0"],
        "MPO": 1,
        "num_search_workers": 4,
        "enforce_diffusion_alignment": False,
        "routing_tolerance": False,
        "m0_pin_extension": False,
        "insert_num_db": 1,
        "contact_rows": {"gate": [2], "diffusion": [0, 1, 3, 4]},
    }
    for key, value in values.items():
        config[key]["value"] = value
    config["placement_constraints"]["value"] = [
        {"type": "align", "transistors": [f"MN{i}S0", f"MP{i}S0"]} for i in range(4)
    ]
    config["mar_c2c_rule"]["value"]["M1"] = 60
    config["eol_c2c_rule"]["value"].update({"M0": 82, "M1": 96, "M2": 82})
    config["max_time"] = {"value": True, "time": timeout}
    path.write_text(json.dumps(config, indent=2) + "\n")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/gt2n_nand4")
    parser.add_argument("--widths", nargs="+", type=int, choices=(13, 31), default=[31])
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = output / "result/NAND4.res"
    result.unlink(missing_ok=True)
    for path in (*output.glob("nand4_w*.gds"), *output.glob("nand4_w*.cdl")):
        path.unlink()
    report = {"status": "running", "verification_status": "not_run", "cells": {}}
    try:
        config = configure(output, args.timeout)
        env = dict(
            os.environ, PYTHONDONTWRITEBYTECODE="1", MPLCONFIGDIR=str(output / "mpl")
        )
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, (str(ROOT), env.get("PYTHONPATH")))
        )
        with (output / "solve.log").open("w") as log:
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "cpcell",
                    "--layer",
                    str(EXAMPLE / "logic.layer"),
                    "--netlist",
                    str(EXAMPLE / "reference.cdl"),
                    "--cell_names",
                    "NAND4",
                    "--cell_config",
                    str(config),
                    "--output_dir",
                    str(output),
                    "--flag_log_constraints",
                    "False",
                ],
                env=env,
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=args.timeout + 60,
            )
        statuses = re.findall(
            r"^status: (\w+)", (output / "solve.log").read_text(), re.M
        )
        if (
            run.returncode
            or not result.is_file()
            or not statuses
            or statuses[-1] not in ("OPTIMAL", "FEASIBLE")
        ):
            raise RuntimeError(f"NAND4 solve failed; see {output / 'solve.log'}")
        report["solver_status"] = statuses[-1]
        report["result_sha256"] = hashlib.sha256(result.read_bytes()).hexdigest()
        report["config_sha256"] = hashlib.sha256(config.read_bytes()).hexdigest()
        for width in dict.fromkeys(args.widths):
            name = f"gt2_6t_nand4_w{width}_lvt"
            gds = output / f"nand4_w{width}.gds"
            layout = GT2NLayout(result, name, gds, nanosheet_width=width, vt="lvt")
            cdl = (EXAMPLE / "reference.cdl").read_text().replace("NAND4", name)
            cdl = cdl.replace("VDD", "vdd").replace("VSS", "vss")
            cdl = cdl.replace("nmos", "nmos_lvt").replace("pmos", "pmos_lvt")
            cdl = cdl.replace("w=46n l=14n nfin=2", f"W={width / 1000:g}u L=0.014u M=1")
            (output / f"nand4_w{width}.cdl").write_text(cdl)
            report["cells"][str(width)] = {
                "name": name,
                "gds": gds.name,
                "footprint_nm": [layout.width, layout.height],
            }
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        raise
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Results: {output / 'report.json'}")


if __name__ == "__main__":
    main()
