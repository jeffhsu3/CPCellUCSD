"""LEF and Verilog views derived from solved geometry and the Boolean spec."""

import klayout.db as db


def rectangles(region):
    for polygon in region.merged().each():
        if polygon.area() != polygon.bbox().area():
            raise ValueError("Expected rectangular unidirectional routing shapes")
        box = polygon.bbox()
        yield " ".join(
            f"{v * 0.0005:.9g}" for v in (box.left, box.bottom, box.right, box.top)
        )


def lef_macro(cell, layout):
    lines = [
        f"MACRO {cell.physical_name}",
        "  CLASS CORE ;",
        "  ORIGIN 0 0 ;",
        "  SYMMETRY X Y ;",
        "  SITE gt2_6t ;",
        f"  SIZE {layout.width / 1000:.9g} BY {layout.height / 1000:.9g} ;",
    ]
    # M1 is the access layer; M0 wiring remains obstructed for external routing.
    # Pin rectangles come from the solved metal rather than guessed positions.
    for net in (*cell.inputs, "Y", "vdd", "vss"):
        power = net in ("vdd", "vss")
        direction = "INOUT" if power else ("OUTPUT" if net == "Y" else "INPUT")
        use = ("POWER" if net == "vdd" else "GROUND") if power else "SIGNAL"
        layer = "BPR" if power else "M1"
        if power:
            y = 144 if net == "vdd" else 0
            metal = db.Region(
                db.Box(0, 2 * (y - 16), int(2 * layout.width), 2 * (y + 16))
            )
        else:
            metal = layout.route_regions.get(("M1", net), db.Region()).merged()
            if metal.is_empty():
                raise ValueError(f"No M1 access for {net}")
        lines += [
            f"  PIN {net}",
            f"    DIRECTION {direction} ;",
            f"    USE {use} ;",
            "    PORT",
            f"      LAYER {layer} ;",
        ]
        lines += [f"        RECT {rect} ;" for rect in rectangles(metal)]
        lines += ["    END", f"  END {net}"]
    lines += ["  OBS"]
    for layer in ("M0", "M1", "M2"):
        obstruction = db.Region()
        for (name, net), metal in layout.route_regions.items():
            if name == layer and not (layer == "M1" and net in (*cell.inputs, "Y")):
                obstruction += metal
        if not obstruction.is_empty():
            lines += [f"    LAYER {layer} ;"] + [
                f"      RECT {rect} ;" for rect in rectangles(obstruction)
            ]
    return "\n".join(lines + ["  END", f"END {cell.physical_name}", ""])


def lef(macros):
    return (
        'VERSION 5.8 ;\nBUSBITCHARS "[]" ;\nDIVIDERCHAR "/" ;\n'
        # Site already exists in the released cell LEF; do not redefine it.
        + "\n".join(macros)
        + "\nEND LIBRARY\n"
    )


def verilog(cells):
    lines = ["// Functional models. Power pins are implicit, as in synthesis Liberty."]
    for cell in cells:
        lines += [
            f"module {cell.physical_name} ({', '.join((*cell.inputs, 'Y'))});",
            f"  input {', '.join(cell.inputs)};",
            "  output Y;",
            f"  assign Y = {cell.function};",
            "endmodule",
            "",
        ]
    return "\n".join(lines)


def abutment_array(cell, layout, directory):
    """2x2 mirrored, electrically independent cells sharing supply rails."""
    native = db.Layout()
    native.dbu = 0.0005
    base = native.create_cell(cell.physical_name)
    base.copy_tree(layout.cell)
    topname = cell.physical_name + "_abut"
    top = native.create_cell(topname)
    w, h = int(layout.width * 2), int(layout.height * 2)
    ports, instances = [], []
    for y in range(2):
        for x in range(2):
            # x reflection = R180 + mirror-x; y reflection = mirror-x.
            trans = db.Trans(2 * x, bool(x != y), x * w + x * w, y * h + y * h)
            top.insert(db.CellInstArray(base.cell_index(), trans))
            nets = [f"{net}_{x}_{y}" for net in (*cell.inputs, "Y")]
            for net, label in zip((*cell.inputs, "Y"), nets):
                metal = layout.route_regions["M1", net].merged()
                point = next(metal.each()).bbox().center()
                top.shapes(native.layer(25, 251)).insert(
                    db.Text(label, db.Trans(trans * point))
                )
            ports += nets
            instances.append(f"X{x}{y} {' '.join(nets)} vdd vss {cell.physical_name}")
    native.write(str(directory / "abutment.gds"))
    cdl = cell.cdl(physical=True) + f"\n.SUBCKT {topname} {' '.join(ports)} vdd vss\n"
    cdl += "\n".join(instances) + f"\n.ENDS {topname}\n"
    (directory / "abutment.cdl").write_text(cdl)
    return topname
