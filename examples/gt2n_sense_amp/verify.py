"""Independent physical and schematic-SPICE comparison against chipforge_gt2n.

Run with the reference repository's Python environment. Geometry generation and
solving do not import that repository. No extracted-RC or mismatch-yield claim
is made: the transient benches use the generated CDL after independent LVS.
"""

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reference-repo", type=Path, required=True)
    parser.add_argument("--pdk-root", type=Path, required=True)
    parser.add_argument("--spice", action="store_true")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(args.reference_repo))
    from scripts.gt2n_klayout import KNOWN_DRC, PLATFORM_DECK, run_drc, run_lvs
    from scripts.gt2n_sense_amp import build_sense_amp, PINS

    out, pdk = args.output_dir, args.pdk_root
    solution = json.loads((out / "solution.json").read_text())
    name = solution["design"]["name"]
    reference = build_sense_amp()
    cdl = reference.netlist(PINS).replace(reference.name, name)
    (out / "reference.cdl").write_text(cdl)
    checks = {
        "drc": dict(run_drc(out / "cell.gds", name, pdk, out / "cell.lyrdb")),
        "lvs_generated": run_lvs(
            out / "cell.gds", out / "cell.cdl", name, out / "generated.lvsdb"
        ),
        "lvs_reference": run_lvs(
            out / "cell.gds", out / "reference.cdl", name, out / "reference.lvsdb"
        ),
    }
    if set(checks["drc"]) - KNOWN_DRC or checks["drc"].get("GATE.2", 0) > 2:
        raise RuntimeError(f"Unexpected DRC: {checks['drc']}")
    if checks["lvs_generated"] != {name: True} or checks["lvs_reference"] != {
        name: True
    }:
        raise RuntimeError(f"LVS mismatch: {checks}")
    print(
        "DRC and LVS against both generated and reference schematics passed", flush=True
    )
    if args.spice:
        from scripts.gt2n_sense_amp_spice import Bench, sense_delay, input_offset

        generated_cdl = (out / "cell.cdl").read_text()

        @dataclass(frozen=True)
        class GeneratedBench(Bench):
            @property
            def sa(self):
                return name, generated_cdl

        generated, baseline = GeneratedBench(pdk), Bench(pdk)
        checks["spice"] = {"scope": "schematic_only_no_extracted_RC", "delay": []}
        for dv in (0.02, 0.05, 0.1, -0.05):
            delay, polarity = sense_delay(generated, dv)
            ref_delay, ref_polarity = sense_delay(baseline, dv)
            if (
                delay is None
                or ref_delay is None
                or not 0 < delay < 100
                or polarity != (dv > 0)
                or polarity != ref_polarity
                or abs(delay - ref_delay) > 1
            ):
                raise RuntimeError(
                    f"Sense delay/polarity mismatch at {dv}: {(delay, polarity)} vs {(ref_delay, ref_polarity)}"
                )
            checks["spice"]["delay"].append(
                dict(
                    input_difference_v=dv,
                    generated_ps=delay,
                    reference_ps=ref_delay,
                    qa_high=polarity,
                )
            )
        offset = input_offset(generated, 0.02)
        reference_offset = input_offset(baseline, 0.02)
        if not 0.005 < offset < 0.05 or abs(offset - reference_offset) > 0.001:
            raise RuntimeError("Offset sensitivity does not match the reference")
        checks["spice"].update(
            input_vth_shift_v=0.02,
            generated_offset_v=offset,
            reference_offset_v=reference_offset,
        )
    files = [
        args.reference_repo / "scripts" / f
        for f in (
            "gt2n_sense_amp.py",
            "gt2n_row.py",
            "gt2n_klayout.py",
            "gt2n_transistor.lylvs",
            "gt2n_sense_amp_spice.py",
            "gt2n_spice.py",
        )
    ]
    files += [pdk / PLATFORM_DECK, pdk / "device/tt/gt2_w31_lvt_tt.sp"]
    checks["source_sha256"] = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }
    checks["status"] = "passed"
    (out / "checks.json").write_text(json.dumps(checks, indent=2) + "\n")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
