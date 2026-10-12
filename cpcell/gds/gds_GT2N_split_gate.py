"""Export feasible split-gate decaps, preserving separate N/P gate conductors."""

from pathlib import Path

import klayout.db as db

from cpcell.gds.gds_GT2N_SH import LAYERS
from cpcell.tech.gt2n_split_gate import SplitGateDecapProblem


def export_split_gate(solution, filename):
    if solution.get("format") != "cpcell.split_gate.v1":
        raise ValueError("Expected a CPCell split-gate solution")
    if solution.get("status") not in ("OPTIMAL", "FEASIBLE"):
        raise ValueError("Cannot export an infeasible or unproven split-gate result")
    problem = SplitGateDecapProblem(solution["design"])
    if (solution["width_nm"], solution["height_nm"]) != (problem.width, problem.height):
        raise ValueError("Split-gate solution dimensions disagree")
    problem.validate(solution["routes"])
    layout = db.Layout()
    layout.dbu = 0.0005
    cell = layout.create_cell(problem.name)
    shapes = list(problem.frame)
    for route in solution["routes"]:
        shapes.extend(problem.edges[route["edge"]].shapes)
    for shape in shapes:
        datatype = 250 if shape.layer == "BOUNDARY" else 0
        cell.shapes(layout.layer(LAYERS[shape.layer], datatype)).insert(
            db.Box(*shape.box)
        )
    for y, net in ((0, "vss"), (problem.height, "vdd")):
        dest = cell.shapes(layout.layer(LAYERS["BPR"], 251))
        dest.insert(db.Box(0, 2 * y - 32, 2 * problem.width, 2 * y + 32))
        dest.insert(db.Text(net, db.Trans(problem.width, 2 * y)))
    for index in layout.layer_indexes():
        if layout.get_info(index).datatype == 0:
            dest = cell.shapes(index)
            merged = db.Region(dest).merged()
            dest.clear()
            dest.insert(merged)
    filename = Path(filename)
    filename.parent.mkdir(parents=True, exist_ok=True)
    layout.write(str(filename))
    return layout


def write_lef(solution, layout, filename):
    problem = SplitGateDecapProblem(solution["design"])
    problem.validate(solution["routes"])
    cell = layout.cell(problem.name)
    lines = [
        "VERSION 5.8 ;",
        'BUSBITCHARS "[]" ;',
        'DIVIDERCHAR "/" ;',
        f"MACRO {problem.name}",
        "  CLASS CORE ;",
        "  ORIGIN 0 0 ;",
        "  SYMMETRY X Y ;",
    ]
    # Taller cases are feasibility controls, not drop-in 6T library cells.
    if problem.height == 144:
        lines.append("  SITE gt2_6t ;")
    # Every released gt2_6t macro names its GDS cell with FOREIGN.
    lines.append(f"    FOREIGN {problem.name} 0 0 ;")
    lines.append(f"  SIZE {problem.width / 1000:g} BY {problem.height / 1000:g} ;")
    for y, net, use in ((0, "vss", "GROUND"), (problem.height, "vdd", "POWER")):
        lines += [
            f"  PIN {net}",
            "    DIRECTION INOUT ;",
            f"    USE {use} ;",
            "    PORT",
            "      LAYER BPR ;",
            f"        RECT 0 {(y - 16) / 1000:g} {problem.width / 1000:g} {(y + 16) / 1000:g} ;",
            "    END",
            f"  END {net}",
        ]
    lines.append("  OBS")
    for layer in ("M0", "M1", "M2"):
        polygons = db.Region(cell.shapes(layout.layer(LAYERS[layer], 0))).merged()
        if polygons.is_empty():
            continue
        lines.append(f"    LAYER {layer} ;")
        for p in polygons.each():
            b = p.bbox()
            if p.area() != b.area():
                raise ValueError("Non-rectangular split-gate LEF obstruction")
            lines.append(
                "      RECT "
                + " ".join(
                    f"{v * layout.dbu:g}" for v in (b.left, b.bottom, b.right, b.top)
                )
                + " ;"
            )
    lines += ["  END", f"END {problem.name}", "END LIBRARY", ""]
    Path(filename).write_text("\n".join(lines))
