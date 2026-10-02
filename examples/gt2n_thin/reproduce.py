"""Reproduce the backside-powered GT2N thin 6T with fixed bands and CP-SAT routing.

Generation needs only CPCell. --verify / --spice also need chipforge_gt2n and
its verification tools. Reference geometry is used only for comparison.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
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


def verify(output, variants, reference_repo, spice, report):
    if not (reference_repo / "scripts/gt2n_thin_bitcell.py").is_file():
        raise FileNotFoundError(f"No GT2N thin reference at {reference_repo}")
    sys.path.insert(0, str(reference_repo))
    import gdspy
    from scripts.gt2n_klayout import run_drc, run_lvs
    from scripts.gt2n_memory_compiler import resolve_gt2n_root
    from scripts.gt2n_thin_bitcell import (
        ThinCellSpec,
        array_name,
        build_library,
        generate_thin_array,
        write_netlist,
    )

    pdk = resolve_gt2n_root()
    report["pdk"] = str(pdk)
    report["reference_repo"] = str(reference_repo)
    report["reference_sources_sha256"] = {
        name: hashlib.sha256(
            (reference_repo / "scripts" / name).read_bytes()
        ).hexdigest()
        for name in (
            "gt2n_thin_bitcell.py",
            "gt2n_klayout.py",
            "gt2n_transistor.lylvs",
            "gt2n_sram_margins.py",
        )
    }
    for variant in variants:
        n, p = map(int, variant.split("x"))
        directory = output / variant
        spec = ThinCellSpec(n_width=f"w{n}", p_width=f"w{p}", supply="backside")
        entry = report["cells"][variant]
        if entry["footprint_nm"] != [spec.width, spec.height]:
            raise RuntimeError(f"Reference dimensions changed for {variant}")
        build_library([spec], (3, 4)).write_gds(str(directory / "reference.gds"))
        write_netlist([spec], directory / "reference.cdl", (3, 4))
        generated = gdspy.GdsLibrary(unit=1e-9, precision=5e-10)
        generated.read_gds(str(directory / "cpcell.gds"), units="convert")
        generated.add(
            generate_thin_array(spec, 3, 4, bitcell=generated.cells[spec.name])
        )
        generated.write_gds(str(directory / "cpcell_array.gds"))
        checks = entry["verification"] = {}
        # First compare with the actual solver input's physical-size schematic.
        entry["input_lvs"] = run_lvs(
            directory / "cpcell.gds",
            directory / "cpcell.cdl",
            spec.name,
            directory / "input.lvsdb",
        )
        if entry["input_lvs"] != {spec.name: True}:
            raise RuntimeError(f"Input schematic LVS mismatch for {variant}")
        for top in (spec.name, array_name(spec, 3, 4)):
            checks[top] = {}
            # Shared-edge diffusions merge across instances, requiring flat LVS.
            flatten = spec.name if top != spec.name else None
            for source in ("reference", "cpcell_array"):
                stem = directory / f"{source}_{top}"
                drc = dict(
                    run_drc(
                        directory / f"{source}.gds",
                        top,
                        pdk,
                        stem.with_suffix(".lyrdb"),
                    )
                )
                lvs = run_lvs(
                    directory / f"{source}.gds",
                    directory / "reference.cdl",
                    top,
                    stem.with_suffix(".lvsdb"),
                    flatten=flatten,
                )
                checks[top][source] = {"drc": drc, "lvs": lvs}
                print(f"{variant} {source} {top}: DRC {drc}, LVS {lvs}", flush=True)
            if any(checks[top][source]["lvs"] != {top: True} for source in checks[top]):
                raise RuntimeError(f"Reference LVS mismatch for {top}")
            if checks[top]["reference"]["drc"] != checks[top]["cpcell_array"]["drc"]:
                raise RuntimeError(f"DRC findings differ from reference for {top}")
        if spice:
            from scripts.gt2n_sram_margins import Bench, characterize

            print(
                f"{variant}: running Xyce hold/read/write characterization...",
                flush=True,
            )
            margins = characterize(Bench(spec, pdk))
            entry["spice"] = asdict(margins)
            # Mixed sizing intentionally trades read SNM for writability. Keep
            # its acceptance floor explicit rather than using equal-width limits.
            valid = (
                min(margins.hold_snm) > 0.2
                and min(margins.read_snm) > 0.05
                and abs(margins.read_snm[0] - margins.read_snm[1]) < 0.002
                and 0 < margins.read_bump < min(margins.read_snm) + 0.1
                and margins.srrv > 0.1
                and margins.read_upset_pulse is None
                and margins.bwtv is not None
                and 0.15 < margins.bwtv < 0.4
                and margins.wl_write_margin is not None
                and margins.wl_write_margin > 0.1
                and margins.iwrite > 0
                and margins.twrite_ps is not None
                and margins.write_delay_ps is not None
                and margins.write_delay_ps < margins.twrite_ps
            )
            if not valid:
                raise RuntimeError(f"Nominal SPICE acceptance failed for {variant}")
            print(
                f"{variant}: hold/read SNM {min(margins.hold_snm):.4f}/{min(margins.read_snm):.4f} V",
                flush=True,
            )
    report["verification_status"] = "passed"
    if spice:
        report["spice_status"] = "passed"
        report["spice_scope"] = (
            "LVS-matched reference schematic, TT 0.7 V/25 C; "
            "estimated 0.25 fF storage-node loads, no extracted RC"
        )


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
    parser.add_argument(
        "--reference-repo", type=Path, default=ROOT.parent / "chipforge_gt2n"
    )
    parser.add_argument(
        "--verify", action="store_true", help="Compare cell/array DRC and LVS"
    )
    parser.add_argument(
        "--spice",
        action="store_true",
        help="Also run nominal Xyce margins; implies --verify",
    )
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
        if args.verify or args.spice:
            verify(output, variants, args.reference_repo.resolve(), args.spice, report)
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
