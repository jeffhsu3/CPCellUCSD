"""Check generated split-gate layouts against external GT2N DRC/LVS decks."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess

import klayout.db as db
import klayout.rdb as rdb


def label(layout, top, master, trans, pin, net):
    """Copy a released cell's pin text into the array under a unique net name."""
    for index in layout.layer_indexes():
        info = layout.get_info(index)
        if info.datatype != 251 or info.layer not in (20, 25, 30):
            continue
        for shape in master.shapes(index).each():
            if shape.is_text() and shape.text.string == pin:
                position = shape.text.trans.disp
                point = trans * db.Point(position.x, position.y)
                top.shapes(index).insert(db.Text(net, db.Trans(point.x, point.y)))
                return
    raise ValueError(f"Missing released pin {pin}")


def subcircuit(text, name):
    match = re.search(rf"^\.subckt {name} .*?^\.ends {name}\s*$", text, re.M | re.S | re.I)
    if match is None:
        raise ValueError(f"Missing released schematic {name}")
    return match[0] + "\n"


def rowend_array(layout, base, out, pdk, design, cdl, cdl_text, width, height):
    """Three mirrored rows of tap / decap / filler / decap / inverter / decap /
    filler / tap. The released one-pitch cells carry only 42 nm of active and
    rely on merging with their neighbours to meet ACT.2, so the generated cell
    must present active at both edges of both bands."""
    vt, w = design["vt"], design["n_width_nm"]
    gds_path = pdk / f"gds/gt2_6t_std_cells_w13_{vt}.gds"
    cdl_path = pdk / f"cdl/with4nodes/gt2_6t_w13_{vt}.cdl"
    released = db.Layout()
    released.read(str(gds_path))
    one_pitch = {}
    for stem in ("tapbspdn", "filler"):
        name = f"gt2_6t_{stem}_w13_{vt}"
        cell = layout.create_cell(name)
        cell.copy_tree(released.cell(name))
        one_pitch[stem] = cell
    invname = f"gt2_6t_inv_x1_w{w}_{vt}"
    inv = layout.cell(invname)
    if inv is None:
        raise ValueError("Row-end test expects the mixed-row inverter")
    schematics = subcircuit(cdl_text, invname) + "".join(
        subcircuit(cdl_path.read_text(), c.name) for c in one_pitch.values()
    )

    def cell_width(cell):
        return db.Region(cell.shapes(layout.layer(235, 250))).bbox().width()

    tap, filler = one_pitch["tapbspdn"], one_pitch["filler"]
    row = [tap, base, filler, base, inv, base, filler, tap]
    top = layout.create_cell(base.name + "_rowend")
    ports, instances = [], []
    for y, (mirror, dy) in enumerate(((False, 0), (True, 0), (True, 2 * height))):
        offset = 0
        for x, master in enumerate(row):
            trans = db.Trans(0, mirror, offset, dy)
            top.insert(db.CellInstArray(master.cell_index(), trans))
            signals = ("A", "Y") if master is inv else ()
            pins = [f"{pin}_{x}_{y}" for pin in signals]
            for pin, net in zip(signals, pins):
                label(layout, top, master, trans, pin, net)
            ports.extend(pins)
            instances.append(f"X{x}{y} {' '.join(pins)} vdd vss {master.name}")
            offset += cell_width(master)
    layout.write(str(out / "rowend.gds"))
    (out / "rowend_reference.cdl").write_text(
        cdl
        + schematics
        + f"\n.SUBCKT {top.name} {' '.join(ports)} vdd vss\n"
        + "\n".join(instances)
        + f"\n.ENDS {top.name}\n"
    )
    return top.name, [gds_path, cdl_path]


def mixed_array(layout, base, out, pdk, design, cdl, cdl_text, width, height):
    """Three mirrored rows of released inverter / generated decap / inverter."""
    vt, w = design["vt"], design["n_width_nm"]
    gds_path = pdk / f"gds/gt2_6t_std_cells_w{w}_{vt}.gds"
    released = db.Layout()
    released.read(str(gds_path))
    invname = f"gt2_6t_inv_x1_w{w}_{vt}"
    inv = layout.create_cell(invname)
    inv.copy_tree(released.cell(invname))
    invw = db.Region(inv.shapes(layout.layer(235, 250))).bbox().width()
    match = re.search(
        rf"^\.subckt {invname} .*?^\.ends {invname}\s*$", cdl_text, re.M | re.S | re.I
    )
    if match is None:
        raise ValueError("Missing released inverter schematic")
    top = layout.create_cell(base.name + "_mixed")
    row = [(inv, invw, ("A", "Y")), (base, width, ()), (inv, invw, ("A", "Y"))]
    ports, instances = [], []
    for y in range(3):
        offset = 0
        for x, (master, cellw, signals) in enumerate(row):
            mx, my = x % 2, y % 2
            trans = db.Trans(
                2 * mx, bool(mx != my), offset + mx * cellw, (y + my) * height
            )
            top.insert(db.CellInstArray(master.cell_index(), trans))
            pins = [f"{pin}_{x}_{y}" for pin in signals]
            for pin, net in zip(signals, pins):
                label(layout, top, master, trans, pin, net)
            ports.extend(pins)
            instances.append(f"X{x}{y} {' '.join(pins)} vdd vss {master.name}")
            offset += cellw
    layout.write(str(out / "mixed.gds"))
    (out / "mixed_reference.cdl").write_text(
        cdl
        + match[0]
        + f"\n.SUBCKT {top.name} {' '.join(ports)} vdd vss\n"
        + "\n".join(instances)
        + f"\n.ENDS {top.name}\n"
    )
    return top.name, gds_path


def check(out, stem, top, drc_deck, lvs_deck):
    result = {}
    for lvs, deck in ((False, drc_deck), (True, lvs_deck)):
        report = out / (stem + (".lvsdb" if lvs else ".lyrdb"))
        cmd = [
            "klayout",
            "-b",
            "-r",
            str(deck),
            "-rd",
            f"in_gds={out / (stem + '.gds')}",
            "-rd",
            f"topcell={top}",
            "-rd",
            f"report_file={report}",
        ]
        if lvs:
            cmd += [
                "-rd",
                f"cdl_file={out / (stem + '_reference.cdl')}",
                "-rd",
                f"target_netlist={out / (stem + '_extracted.cdl')}",
            ]
        else:
            cmd += ["-rd", "drc_mode=full", "-rd", "drc_use_nets=1"]
        with report.with_suffix(report.suffix + ".log").open("w") as log:
            subprocess.run(
                cmd, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120
            )
        if lvs:
            database = db.LayoutVsSchematic()
            database.read(str(report))
            matches = {
                pair.first().name: pair.status() == db.NetlistCrossReference.Match
                for pair in database.xref().each_circuit_pair()
                if pair.first() is not None
            }
            if top not in matches or not all(matches.values()):
                raise ValueError(f"LVS mismatch: {matches}")
            result["lvs"] = matches
        else:
            counts = dict(
                Counter(re.findall(r"<category>'([^'<]+)'", report.read_text()))
            )
            if counts != {"GATE.2": 2}:
                raise ValueError(f"Unexpected DRC on {top}: {counts}")
            layout = db.Layout()
            layout.read(str(out / (stem + ".gds")))
            gate = (
                db.Region(layout.cell(top).begin_shapes_rec(layout.layer(3, 0)))
                .bbox()
                .to_dtype(layout.dbu)
            )
            outside = {round(gate.left, 9), round(gate.right, 9)}
            database = rdb.ReportDatabase()
            database.load(str(report))
            observed = set()
            for item in database.each_item():
                for value in item.each_value():
                    if not value.is_edge():
                        raise ValueError("Expected outside gate edge")
                    edge = value.edge()
                    if (
                        abs(edge.p1.x - edge.p2.x) > 1e-9
                        or round(edge.p1.x, 9) not in outside
                    ):
                        raise ValueError("GATE.2 marker is not an outside edge")
                    observed.add(round(edge.p1.x, 9))
            if observed != outside:
                raise ValueError("Missing outside-edge markers")
            result["drc"] = counts
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pdk-root", type=Path, required=True)
    parser.add_argument(
        "--drc-deck",
        type=Path,
        required=True,
        help="KLayout deck including the VG/ACT overlap guard",
    )
    parser.add_argument("--lvs-deck", type=Path, required=True)
    args = parser.parse_args(argv)
    if "vg.interacting(act)" not in args.drc_deck.read_text():
        raise ValueError("DRC deck must include the VG/ACT overlap guard")
    out = args.output_dir.resolve()
    (out / "checks.json").unlink(missing_ok=True)
    manifest = json.loads((out / "manifest.json").read_text())
    for name, digest in manifest.items():
        if hashlib.sha256((out / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Generated artifact changed: {name}")
    solution = json.loads((out / "solution.json").read_text())
    if solution["status"] not in ("OPTIMAL", "FEASIBLE"):
        raise ValueError("No feasible layout to verify")
    design = solution["design"]
    name = design["name"]
    w = design["n_width_nm"]
    vt = design["vt"]
    if w != design["p_width_nm"]:
        raise ValueError(
            "Released-reference verification requires equal N/P sheet widths"
        )
    reference_name = f"gt2_6t_decapcc_w{w}_{vt}"
    cdl_path = args.pdk_root / f"cdl/with4nodes/gt2_6t_w{w}_{vt}.cdl"
    match = re.search(
        rf"^\.subckt {reference_name} .*?^\.ends {reference_name}\s*$",
        cdl_path.read_text(),
        re.M | re.S | re.I,
    )
    if match is None:
        raise ValueError("Missing released decap schematic")
    cdl = match[0].replace(reference_name, name) + "\n"
    (out / "cell_reference.cdl").write_text(cdl)
    layout = db.Layout()
    layout.read(str(out / "cell.gds"))
    base = layout.cell(name)
    array = layout.create_cell(name + "_array")
    width = int(round(solution["width_nm"] / 1000 / layout.dbu))
    height = int(round(solution["height_nm"] / 1000 / layout.dbu))
    instances = []
    for y in range(3):
        for x in range(3):
            mx, my = x % 2, y % 2
            transform = db.Trans(
                2 * mx, bool(mx != my), (x + mx) * width, (y + my) * height
            )
            array.insert(db.CellInstArray(base.cell_index(), transform))
            instances.append(f"X{x}{y} vdd vss {name}")
    layout.write(str(out / "array.gds"))
    (out / "array_reference.cdl").write_text(
        cdl
        + f".SUBCKT {array.name} vdd vss\n"
        + "\n".join(instances)
        + f"\n.ENDS {array.name}\n"
    )
    checks = dict(
        status="running",
        scope="KLayout DRC and released-CDL transistor LVS; standalone and 3x3 mirrored array, plus mixed inverter rows and rows ending in one-pitch tap/filler cells at 144 nm height; no ICV",
    )
    cases = [("cell", name), ("array", array.name)]
    sources = [Path(__file__), cdl_path, args.drc_deck, args.lvs_deck]
    if solution["height_nm"] == 144:
        mixed, gds_path = mixed_array(
            layout,
            base,
            out,
            args.pdk_root,
            design,
            cdl,
            cdl_path.read_text(),
            width,
            height,
        )
        cases.append(("mixed", mixed))
        sources.append(gds_path)
        rowend, extra = rowend_array(
            layout,
            base,
            out,
            args.pdk_root,
            design,
            cdl,
            cdl_path.read_text(),
            width,
            height,
        )
        cases.append(("rowend", rowend))
        sources.extend(extra)
    generation = json.loads((out / "report.json").read_text())

    def verification_status(status):
        generation["verification_status"] = status
        path = out / "report.json"
        path.write_text(json.dumps(generation, indent=2) + "\n")
        manifest["report.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    verification_status("running")
    try:
        for stem, top in cases:
            checks[stem] = check(
                out, stem, top, args.drc_deck.resolve(), args.lvs_deck.resolve()
            )
    except Exception:
        verification_status("failed")
        raise
    checks["status"] = "passed"
    checks["source_sha256"] = {
        str(p.resolve()): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources
    }
    checks["artifact_sha256"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in out.iterdir()
        if p.suffix in (".gds", ".cdl", ".lvsdb", ".lyrdb", ".lef")
    }
    (out / "checks.json").write_text(json.dumps(checks, indent=2) + "\n")
    verification_status("passed_with_outer_gate_markers")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
