"""Reproduce the GT2N standard-row 6T through CPCell, optionally verify it.

The sibling chipforge_gt2n repository is needed only for --verify / --spice.
Its cell generator supplies the comparison layout, never the CPCell layout.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
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


def verify(output, widths, reference_repo, spice, report):
    """Use exactly the sibling's platform DRC, LVS, tiling and margin flows."""
    if not (reference_repo / "scripts/gt2n_sram_bitcell.py").is_file():
        raise FileNotFoundError(f"No GT2N reference implementation at {reference_repo}")
    sys.path.insert(0, str(reference_repo))
    import gdspy
    from scripts.gt2n_klayout import run_drc, run_lvs
    from scripts.gt2n_memory_compiler import resolve_gt2n_root
    from scripts.gt2n_sram_bitcell import (
        BitcellSpec,
        array_name,
        build_library,
        generate_array,
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
            "gt2n_sram_bitcell.py",
            "gt2n_klayout.py",
            "gt2n_transistor.lylvs",
            "gt2n_sram_margins.py",
        )
    }
    for width in widths:
        directory = output / f"w{width}"
        spec = BitcellSpec("6t", f"w{width}", "lvt")
        reference = build_library([spec], (3, 4))
        reference.write_gds(str(directory / "reference.gds"))
        write_netlist([spec], directory / "reference.cdl", (3, 4))
        # Import the actual CPCell result. Only array placement is reused from
        # the reference implementation; no reference cell geometry is copied.
        generated = gdspy.GdsLibrary(unit=1e-9, precision=5e-10)
        generated.read_gds(str(directory / "cpcell.gds"), units="convert")
        generated.add(generate_array(spec, 3, 4, generated.cells[spec.name]))
        generated.write_gds(str(directory / "cpcell_array.gds"))
        checks = {}
        report["cells"][f"w{width}"]["verification"] = checks
        for top in (spec.name, array_name(spec, 3, 4)):
            checks[top] = {}
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
                )
                checks[top][source] = {"drc": drc, "lvs": lvs}
                print(f"w{width} {source} {top}: DRC {drc}, LVS {lvs}", flush=True)
            expected = {spec.name: True} | ({top: True} if top != spec.name else {})
            if any(
                checks[top][source]["lvs"] != expected
                for source in ("reference", "cpcell_array")
            ):
                raise RuntimeError(f"LVS mismatch for {top}")
            if checks[top]["reference"]["drc"] != checks[top]["cpcell_array"]["drc"]:
                raise RuntimeError(f"DRC findings differ from the reference for {top}")
        if spice:
            from scripts.gt2n_sram_margins import Bench, characterize

            print(
                f"w{width}: running Xyce hold/read/write characterization...",
                flush=True,
            )
            margins = characterize(Bench(spec, pdk))
            report["cells"][f"w{width}"]["spice"] = asdict(margins)
            # Same nominal acceptance criteria as the reference's margin test.
            valid = (
                min(margins.hold_snm) > 0.2
                and min(margins.read_snm) > 0.08
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
                raise RuntimeError(
                    f"SPICE margins failed nominal acceptance criteria for w{width}"
                )
            print(
                f"w{width}: hold/read SNM {min(margins.hold_snm):.4f}/{min(margins.read_snm):.4f} V, BWTV {margins.bwtv:.4f} V",
                flush=True,
            )
    report["verification_status"] = "passed"
    if spice:
        report["spice_scope"] = (
            "LVS-matched reference schematic, TT 0.7 V/25 C; estimated 0.25 fF storage-node loads, no extracted RC"
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "build/gt2n_sram_reference"
    )
    parser.add_argument(
        "--widths", nargs="+", type=int, choices=(13, 31), default=[13, 31]
    )
    parser.add_argument(
        "--reference-repo", type=Path, default=ROOT.parent / "chipforge_gt2n"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Compare cell/array DRC and LVS with the reference",
    )
    parser.add_argument(
        "--spice",
        action="store_true",
        help="Also run Xyce on the LVS-matched schematic; implies --verify",
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
        if args.verify or args.spice:
            verify(
                output,
                list(dict.fromkeys(args.widths)),
                args.reference_repo.resolve(),
                args.spice,
                report,
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
