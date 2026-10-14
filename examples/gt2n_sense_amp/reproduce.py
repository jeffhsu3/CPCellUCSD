"""Generate a matched GT2N sense amplifier using only CPCell."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from cpcell.tech.gt2n_sense_amp import SenseAmpProblem  # noqa: E402
from cpcell.gds.gds_GT2N_sense_amp import export_sense_amp  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "build/gt2n_sense_amp"
    )
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "running",
        "verification_status": "not_run",
        "spice_status": "not_run",
    }
    # Remove stale outputs so a failed solve cannot be mistaken for success.
    for name in (
        "solution.json",
        "cell.gds",
        "cell.cdl",
        "checks.json",
        "manifest.json",
    ):
        (out / name).unlink(missing_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    try:
        problem = SenseAmpProblem()
        solution = problem.solve(args.timeout)
        export_sense_amp(solution, out / "cell.gds")
        (out / "solution.json").write_text(json.dumps(solution, indent=2) + "\n")
        (out / "cell.cdl").write_text(problem.schematic())
        report.update(
            solver_status=solution["status"],
            footprint_nm=[problem.width, problem.height],
            physical_fingers=12,
            finger_groups=problem.design["finger_groups"],
            matched_devices=problem.design["matched_devices"],
            route_metrics=solution["route_metrics"],
        )
        report["status"] = "passed"
        source = [
            "cpcell/core/band_router.py",
            "cpcell/tech/gt2n_sense_amp.py",
            "cpcell/tech/gt2n_thin.py",
            "cpcell/gds/gds_GT2N_sense_amp.py",
            "cpcell/gds/gds_GT2N_SH.py",
            "examples/gt2n_sense_amp/reproduce.py",
        ]
        report["source_sha256"] = {
            f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in source
        }
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        paths = [
            out / f for f in ("solution.json", "cell.gds", "cell.cdl", "report.json")
        ]
        if (out / "checks.json").exists():
            paths += [
                out / "checks.json",
                *out.glob("*.lyrdb"),
                *out.glob("*.lvsdb"),
                out / "reference.cdl",
            ]
        manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    except Exception as error:
        report.update(status="failed", error=str(error))
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        raise


if __name__ == "__main__":
    main()
