"""Generate CPCell GT2N logic cells, including buffered non-inverting gates.

--verify runs device-level LVS and full platform DRC, including mirrored
abutment. --spice also generates an experimental schematic-only TT Liberty.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import klayout.db as db

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from cpcell.gds.gds_GT2N_SH import GT2NLayout
from cpcell.utility.config import generate_config
from examples.gt2n_logic.cells import cells, catalog_entries, library_name
from examples.gt2n_logic.artifacts import lef, lef_macro, verilog, abutment_array


def configure(cell, output, timeout):
    generate_config(4, "FinFET", "SH", [cell.name], output)
    path = output / "config" / f"{cell.name}.json"
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
        "insert_num_db": 4 if cell.name.startswith(("AO", "OA", "MAJ")) else 1,
        "contact_rows": {"gate": [2], "diffusion": [0, 1, 3, 4]},
    }
    for key, value in values.items():
        config[key]["value"] = value
    # With gate access above/below ACT, aligned complementary gates provide
    # continuous shared poly. Order, position and source/drain flips are free.
    pairs = []
    for gate in cell.gate_nets:
        by_model = {
            model: [d[0] + "S0" for d in cell.devices if d[2] == gate and d[4] == model]
            for model in ("nmos", "pmos")
        }
        if len(by_model["nmos"]) != len(by_model["pmos"]):
            raise ValueError(
                f"{cell.name}/{gate}: gate access needs complementary pairs"
            )
        # Repeated input gates occupy distinct columns. Aligning every device
        # on that net would force same-row devices to overlap (e.g. majority).
        pairs.extend(
            {"type": "align", "transistors": [n, p]}
            for n, p in zip(by_model["nmos"], by_model["pmos"])
        )
    config["placement_constraints"]["value"] = pairs
    config["mar_c2c_rule"]["value"]["M1"] = 60
    # Solver coordinates are twice nanometers. Include both exported end
    # extensions plus the PDK's same-track edge spacing (17 nm on M0/M2,
    # 20 nm on M1). Do not rely on an extra grid point being excluded by EOL.
    config["eol_c2c_rule"]["value"].update(
        {"M0": 2 * (12 + 17 + 12), "M1": 2 * (14 + 20 + 14), "M2": 2 * (12 + 17 + 12)}
    )
    config["max_time"] = {"value": True, "time": timeout}
    path.write_text(json.dumps(config, indent=2) + "\n")
    return path


def solve(cell, output, timeout):
    folder = output / cell.name
    folder.mkdir(exist_ok=True)
    (folder / "report.json").unlink(missing_ok=True)
    config = configure(cell, output, timeout)
    result = output / "result" / f"{cell.name}.res"
    # A failed or timed-out solve must never reuse a stale successful result.
    if result.exists():
        result.unlink()
    env = dict(
        os.environ, PYTHONDONTWRITEBYTECODE="1", MPLCONFIGDIR=str(output / "mpl")
    )
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(ROOT), env.get("PYTHONPATH")))
    )
    with (folder / "solve.log").open("w") as log:
        run = subprocess.run(
            [
                sys.executable,
                "-m",
                "cpcell",
                "--layer",
                str(EXAMPLE / "logic.layer"),
                "--netlist",
                str(output / "solver.cdl"),
                "--cell_names",
                cell.name,
                "--cell_config",
                str(config),
                "--output_dir",
                str(output),
                "--flag_log_constraints",
                "False",
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=timeout + 60,
        )
    log = (folder / "solve.log").read_text()
    statuses = re.findall(r"^status: (\w+)", log, re.M)
    if (
        run.returncode
        or not result.is_file()
        or not statuses
        or statuses[-1] not in ("OPTIMAL", "FEASIBLE")
    ):
        raise RuntimeError(f"{cell.name} solve failed; see {folder / 'solve.log'}")
    layout = GT2NLayout(
        result, cell.physical_name, folder / "cell.gds", nanosheet_width=31, vt="lvt"
    )
    if len(layout.nmos_transistor_data) + len(layout.pmos_transistor_data) != len(
        cell.devices
    ):
        raise RuntimeError("Device count changed during generation")
    (folder / "cell.cdl").write_text(cell.cdl(physical=True))
    return layout, {
        "solver_status": statuses[-1],
        "footprint_nm": [layout.width, layout.height],
        "area_um2": layout.width * layout.height * 1e-6,
        "transistors": len(cell.devices),
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "result_sha256": hashlib.sha256(result.read_bytes()).hexdigest(),
    }


def verify(cell, layout, folder, pdk):
    from scripts.gt2n_klayout import run_drc, run_lvs, KNOWN_DRC

    checks = {}
    top = abutment_array(cell, layout, folder)
    for scope, stem, name in [
        ("cell", "cell", cell.physical_name),
        ("abutment", "abutment", top),
    ]:
        gds = folder / f"{stem}.gds"
        cdl = folder / f"{stem}.cdl"
        drc = dict(run_drc(gds, name, pdk, folder / f"{scope}.lyrdb"))
        lvs = run_lvs(gds, cdl, name, folder / f"{scope}.lvsdb")
        checks[scope] = {"drc": drc, "lvs": lvs}
        print(f"{cell.name} {scope}: DRC {drc}, LVS {lvs}", flush=True)
        expected = {cell.physical_name: True}
        if scope == "abutment":
            expected[top] = True
        if lvs != expected:
            raise RuntimeError(f"{cell.name} {scope} LVS mismatch")
        if set(drc) - KNOWN_DRC:
            raise RuntimeError(f"{cell.name} {scope} unexpected DRC: {drc}")
    return checks


def view_files(selected):
    name = library_name(selected)
    return {kind: f"{name}.{kind}" for kind in ("gds", "lef", "cdl", "v")} | {
        "liberty": f"{name}_tt_0p7v25c.lib"
    }


def write_library(selected, layouts, output, report):
    """Package one catalog selection, retaining per-cell evidence and hashes."""
    views = view_files(selected)
    report["library"] = library_name(selected)
    report["views"] = {
        k: v
        for k, v in views.items()
        if k != "liberty" or report["spice_status"] == "passed"
    }
    library = db.Layout()
    library.dbu = 0.0005
    macros = []
    for cell in selected:
        layout = layouts[cell.name]
        library.create_cell(cell.physical_name).copy_tree(layout.cell)
        macros.append(lef_macro(cell, layout))
        folder = output / cell.name
        folder.mkdir(exist_ok=True)
        (folder / "report.json").write_text(
            json.dumps(report["cells"][cell.name], indent=2, allow_nan=False) + "\n"
        )
    library.write(str(output / views["gds"]))
    (output / views["lef"]).write_text(lef(macros))
    (output / views["cdl"]).write_text(
        "\n".join(c.cdl(physical=True) for c in selected)
    )
    (output / views["v"]).write_text(verilog(selected))
    (output / "catalog.json").write_text(
        json.dumps(catalog_entries(selected), indent=2) + "\n"
    )
    if report["spice_status"] == "passed":
        from examples.gt2n_logic.characterize import liberty

        (output / views["liberty"]).write_text(liberty(selected, report))


def write_manifest(selected, output, report):
    artifacts = set(report["views"].values()) | {
        "catalog.json",
        "report.json",
        "solver.cdl",
    }
    for cell in selected:
        artifacts.update((f"config/{cell.name}.json", f"result/{cell.name}.res"))
        artifacts.update(
            f"{cell.name}/{name}" for name in ("cell.gds", "cell.cdl", "report.json")
        )
        if "verification" in report["cells"][cell.name]:
            artifacts.update(
                f"{cell.name}/{name}"
                for name in (
                    "cell.lyrdb",
                    "cell.lvsdb",
                    "abutment.gds",
                    "abutment.cdl",
                    "abutment.lyrdb",
                    "abutment.lvsdb",
                )
            )
        if "characterization" in report["cells"][cell.name]:
            artifacts.update(
                str(p.relative_to(output)) for p in (output / cell.name).glob("*.cir")
            )
    artifacts.update(report.get("source_reports", {}))
    artifacts.update(report.get("verification_sources", {}))
    manifest = {
        "schema_version": 1,
        "library": report["library"],
        "cells": list(catalog_entries(selected)),
        "views": report["views"],
        "status": report["status"],
        "verification_status": report["verification_status"],
        "spice_status": report["spice_status"],
        "characterization_scope": "schematic-only TT 0.7 V 25 C; no extracted RC or power tables",
        "input_sha256": report["input_sha256"],
        "artifacts": {
            name: {
                "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest(),
                "bytes": (output / name).stat().st_size,
            }
            for name in sorted(artifacts)
        },
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--cells",
        nargs="+",
        choices=tuple(cells()),
        help="Generate selected cells (default: entire catalog)",
    )
    parser.add_argument(
        "--list-cells", action="store_true", help="Print the cell catalog and exit"
    )
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--reference-repo", type=Path, default=ROOT.parent / "chipforge_gt2n"
    )
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--spice", action="store_true")
    args = parser.parse_args(argv)
    if args.list_cells:
        for name, entry in catalog_entries(cells().values()).items():
            print(f"{name:8} {entry['transistors']:2}T  Y = {entry['function']}")
        return
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be finite and positive")
    names = args.cells or list(cells())
    default_dir = "gt2n_logic_custom" if args.cells else "gt2n_logic_library"
    output = (args.output_dir or ROOT / "build" / default_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Do not leave an earlier Liberty/physical library looking current after
    # a failed run or a generation-only invocation with different cells.
    for filename in (
        "cells.gds",
        "cells.lef",
        "cells.cdl",
        "cells.v",
        "cells_tt.lib",
        "manifest.json",
        "catalog.json",
    ):
        (output / filename).unlink(missing_ok=True)
    for pattern in (
        "gt2_cpcell_logic_*.gds",
        "gt2_cpcell_*.lef",
        "gt2_cpcell_logic_*.cdl",
        "gt2_cpcell_logic_*.v",
        "gt2_cpcell_logic_*.lib",
    ):
        for path in output.glob(pattern):
            path.unlink()
    selected = [cells()[name] for name in dict.fromkeys(names)]
    report = {
        "status": "running",
        "verification_status": "not_run",
        "spice_status": "not_run",
        "width_nm": 31,
        "vt": "lvt",
        "library": library_name(selected),
        "cells": {},
        "input_sha256": {
            name: hashlib.sha256((EXAMPLE / name).read_bytes()).hexdigest()
            for name in (
                "cells.py",
                "logic.layer",
                "reproduce.py",
                "artifacts.py",
                "characterize.py",
            )
        },
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "solver.cdl").write_text("\n".join(c.cdl() for c in selected))
    layouts = {}
    try:
        if args.verify or args.spice:
            repo = args.reference_repo.resolve()
            sys.path.insert(0, str(repo))
            from scripts.gt2n_memory_compiler import resolve_gt2n_root

            pdk = resolve_gt2n_root()
            report["pdk"] = str(pdk)
            model = pdk / "device/tt/gt2_w31_lvt_tt.sp"
            report["model_sha256"] = hashlib.sha256(model.read_bytes()).hexdigest()
            report["reference_sources_sha256"] = {
                name: hashlib.sha256((repo / "scripts" / name).read_bytes()).hexdigest()
                for name in (
                    "gt2n_klayout.py",
                    "gt2n_transistor.lylvs",
                    "gt2n_spice.py",
                )
            }
        for cell in selected:
            vectors = cell.check_topology()
            print(
                f"{cell.name}: topology checked ({vectors} vectors); solving...",
                flush=True,
            )
            layout, entry = solve(cell, output, args.timeout)
            report["cells"][cell.name] = entry
            entry["logic_vectors"] = vectors
            layouts[cell.name] = layout
            print(
                f"{cell.name}: {entry['solver_status']}, {layout.width:g} x {layout.height:g} nm",
                flush=True,
            )
            if args.verify or args.spice:
                entry["verification"] = verify(cell, layout, output / cell.name, pdk)
            if args.spice:
                from examples.gt2n_logic.characterize import truth_table, characterize

                entry["spice_truth_table"] = truth_table(cell, pdk, output / cell.name)
                entry["characterization"] = characterize(cell, pdk, output / cell.name)
                print(
                    f"{cell.name}: SPICE truth table and timing sweeps passed",
                    flush=True,
                )
            (output / cell.name / "report.json").write_text(
                json.dumps(entry, indent=2, allow_nan=False) + "\n"
            )
        if args.verify or args.spice:
            report["verification_status"] = "passed"
        if args.spice:
            report["spice_status"] = "passed"
        write_library(selected, layouts, output, report)
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        raise
    finally:
        (output / "report.json").write_text(
            json.dumps(report, indent=2, allow_nan=False) + "\n"
        )
    write_manifest(selected, output, report)
    print(f"Results: {output / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
