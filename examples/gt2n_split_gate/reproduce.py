"""Solve/export a split-gate GT2N decap or report scoped infeasibility."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from cpcell.tech.gt2n_split_gate import SplitGateDecapProblem, default_design, FLAVORS  # noqa: E402
from cpcell.gds.gds_GT2N_split_gate import export_split_gate, write_lef  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--design", type=Path, help="Explicit JSON frame and track settings"
    )
    parser.add_argument("--sheet-width-nm", type=int, choices=(13, 31), default=31)
    parser.add_argument("--height-nm", type=int, default=144)
    parser.add_argument("--vt", choices=FLAVORS, default="lvt")
    parser.add_argument(
        "--arrangement", choices=("split", "staggered"), default="split"
    )
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "build/gt2n_split_gate"
    )
    args = parser.parse_args(argv)
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # A failed/infeasible rerun must not retain a previous successful layout.
    for name in (
        "solution.json",
        "cell.gds",
        "cell.cdl",
        "cell.lef",
        "manifest.json",
        "checks.json",
    ):
        (out / name).unlink(missing_ok=True)
    report = dict(status="running", verification_status="not_run")
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    try:
        design = (
            json.loads(args.design.read_text())
            if args.design
            else default_design(
                args.sheet_width_nm, args.height_nm, args.vt, args.arrangement
            )
        )
        problem = SplitGateDecapProblem(design)
        result = problem.solve(args.timeout)
        (out / "solution.json").write_text(json.dumps(result, indent=2) + "\n")
        report.update(
            solver_status=result["status"],
            footprint_nm=[problem.width, problem.height],
            scope=result["scope"],
            blocked_terminals=result["blocked_terminals"],
            standard_m0_grid=all(y % 24 == 0 for y in problem.rows),
        )
        if result["status"] in ("FEASIBLE", "OPTIMAL"):
            layout = export_split_gate(result, out / "cell.gds")
            write_lef(result, layout, out / "cell.lef")
            (out / "cell.cdl").write_text(problem.schematic())
            report["status"] = "generated"
        else:
            report["status"] = (
                "infeasible" if result["status"] == "INFEASIBLE" else "unknown"
            )
        sources = [
            "cpcell/core/band_router.py",
            "cpcell/tech/gt2n_split_gate.py",
            "cpcell/tech/gt2n_thin.py",
            "cpcell/gds/gds_GT2N_split_gate.py",
            "cpcell/gds/gds_GT2N_SH.py",
            "examples/gt2n_split_gate/reproduce.py",
        ]
        report["source_sha256"] = {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in sources
        }
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        files = ["solution.json", "report.json"]
        if report["status"] == "generated":
            files += ["cell.gds", "cell.lef", "cell.cdl"]
        (out / "manifest.json").write_text(
            json.dumps(
                {
                    name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                    for name in files
                },
                indent=2,
            )
            + "\n"
        )
        print(json.dumps(report, indent=2))
        # Distinguish a proved scoped impossibility from a timeout in shell flows.
        return {"generated": 0, "infeasible": 2, "unknown": 3}[report["status"]]
    except Exception as error:
        report.update(status="failed", error=str(error))
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
