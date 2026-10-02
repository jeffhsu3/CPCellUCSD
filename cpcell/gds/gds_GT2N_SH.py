"""Export CPCell SH results using GT2N nanosheet and buried-rail geometry.

Layer numbers and dimensions follow GT2N's techlib/gt2_techfile.layermap,
icv_runset/Include, and released gt2_6t_inv_x1_w{13,31}_lvt reference cells.
The input uses the same doubled-nanometre coordinates as gds_FinFET_SH.
This is an export backend, not a GT2N constraint model or DRC/LVS signoff.
"""

import argparse
import math
from pathlib import Path

import klayout.db as pya

from cpcell.gds.result import parse_result, parse_boundary_ports


LAYERS = {
    "NWELL": 1,
    "ACT": 2,
    "GATE": 3,
    "DUMMY": 4,
    "GCUT": 5,
    "NSEL": 6,
    "PSEL": 7,
    "BPR": 8,
    "VBPR": 9,
    "SDCON": 10,
    "VSD": 11,
    "VG": 12,
    "M0": 20,
    "V0": 22,
    "M1": 25,
    "V1": 27,
    "M2": 30,
    "ELVT": 94,
    "ULVT": 95,
    "SVT": 96,
    "HVT": 97,
    "BOUNDARY": 235,
}
VT_FLAVORS = ("lvt", "elvt", "ulvt", "svt", "hvt")


class GT2NLayout:
    """Write one cell, preserving other cells in an existing GT2N library.

    ``nanosheet_width`` is the physical ACT width (13 or 31 nm), independent
    of the abstract FinFET width used by the placement solver. LVT uses no
    threshold marker. All geometry below is in nm; GDS uses a 0.5 nm DBU.
    """

    DBU = 0.0005
    SOLVER_RESCALE = 2.0
    ROUTE_Y_OFFSET = 36.0

    def __init__(
        self, result_file, subckt_name, gds_file, nanosheet_width=13, vt="lvt"
    ):
        if nanosheet_width not in (13, 31):
            raise ValueError("GT2N nanosheet_width must be 13 or 31 nm")
        if vt not in VT_FLAVORS:
            raise ValueError(f"Unsupported GT2N VT flavor: {vt}")
        if not subckt_name or len(subckt_name.encode("ascii")) > 32:
            raise ValueError("GDS cell names must contain 1–32 ASCII characters")
        self.result_file = result_file
        self.subckt_name = subckt_name
        self.nanosheet_width = nanosheet_width
        self.vt = vt
        (
            self.tech_data,
            self.pmos_transistor_data,
            self.nmos_transistor_data,
            self.metal_data,
        ) = parse_result(result_file)
        self.boundary_ports = parse_boundary_ports(result_file)
        if self.tech_data.layout_profile not in ("default", "gt2n_sram", "gt2n_logic"):
            raise ValueError(f"Unknown GT2N layout profile: {self.tech_data.layout_profile}")
        self.sram_profile = self.tech_data.layout_profile in ("gt2n_sram", "gt2n_logic")
        self.ROUTE_Y_OFFSET = 0.0 if self.sram_profile else 36.0
        self.route_rows = (14, 42, 72, 102) if self.sram_profile else (36, 60, 84, 108)
        if self.tech_data.layout_profile == "gt2n_logic":
            self.route_rows = (16, 42, 72, 102, 128)
        self.m1_end_extension = 10 if self.sram_profile else 14
        self.width = self.tech_data.col * self.tech_data.cp_pitch
        self.height = 144.0
        self._validate_result()

        # Generate in isolation: failed validation/drawing cannot damage an
        # existing library. copy_tree handles DBU conversion without rescaling
        # any cells already in that library.
        self.layout = pya.Layout()
        self.layout.dbu = self.DBU
        self.cell = self.layout.create_cell(subckt_name)
        self.draw()
        self.save(gds_file)

    def _validate_result(self):
        expected = {
            "track": 4,
            "cp_pitch": 42,
            "m0_pitch": 24,
            "m2_pitch": 24,
            "cp_width": 14,
            "m0_width": 12,
            "m1_width": 14,
            "m2_width": 12,
        }
        for field, value in expected.items():
            actual = getattr(self.tech_data, field)
            if not math.isclose(actual, value):
                raise ValueError(
                    f"GT2N SH requires {field}={value}, got {actual}; "
                    "solve using input/config/GT2N_FinFET_2F_4T_4242OF0.layer"
                )
        if self.tech_data.col < 2 or self.tech_data.m1_pitch < 28:
            raise ValueError(
                "GT2N requires at least two CPP columns and M1 pitch >= 28 nm"
            )
        if not math.isfinite(self.tech_data.m1_pitch):
            raise ValueError("M1 pitch must be finite")
        if self.tech_data.power_config not in ("M0BPR", "BPR"):
            raise ValueError(
                f"Unsupported power configuration: {self.tech_data.power_config}"
            )

        self.gates = {"n": {}, "p": {}}
        self.terminals = {"n": {}, "p": {}}
        for kind, transistors, row in (
            ("n", self.nmos_transistor_data, 0),
            ("p", self.pmos_transistor_data, 96),
        ):
            if not transistors:
                raise ValueError(f"GT2N SH requires a nonempty {kind.upper()}MOS row")
            for tran in transistors:
                if tran.y != row or tran.width != 84 or tran.height != 48:
                    raise ValueError(
                        f"Unsupported SH placement for {tran.name}: expected one 84 x 48 solver finger at y={row}"
                    )
                left = tran.x / self.SOLVER_RESCALE
                gate = left + 21
                if left < 21 or left + 63 > self.width or gate % 42 != 0:
                    raise ValueError(
                        f"Transistor {tran.name} is outside the GT2N gate grid"
                    )
                if gate in self.gates[kind]:
                    raise ValueError(f"Overlapping transistors at {kind} gate x={gate}")
                self.gates[kind][gate] = tran.gate_net
                left_net, right_net = (
                    (tran.drain_net, tran.source_net)
                    if tran.flip
                    else (tran.source_net, tran.drain_net)
                )
                for x, net in ((left, left_net), (left + 42, right_net)):
                    previous = self.terminals[kind].setdefault(x, net)
                    if previous != net:
                        raise ValueError(
                            f"Conflicting diffusion nets at {kind} x={x}: {previous}, {net}"
                        )
                    if net.upper() in ("VDD", "VSS") and net.upper() != (
                        "VSS" if kind == "n" else "VDD"
                    ):
                        raise ValueError(f"Unsupported {kind}MOS connection to {net}")
                for col, net in (
                    (tran.source_col, tran.source_net),
                    (tran.drain_col, tran.drain_net),
                ):
                    physical_col = left if net == left_net else left + 42
                    if col != -1 and col / self.SOLVER_RESCALE != physical_col:
                        # A device with identical S/D nets can use either side.
                        if not (
                            left_net == right_net
                            and col / self.SOLVER_RESCALE == left + 42
                        ):
                            raise ValueError(
                                f"Terminal column disagrees with orientation for {tran.name}"
                            )
                if tran.gate_col != -1 and tran.gate_col / self.SOLVER_RESCALE != gate:
                    raise ValueError(
                        f"Gate column disagrees with placement for {tran.name}"
                    )

        for metal in self.metal_data:
            a, b = metal.metal_0, metal.metal_1
            if not (0 <= a <= b <= 3 and b - a <= 1):
                raise ValueError(f"Unsupported routing layer pair: {a}, {b}")
            x0, y0, x1, y1 = self._route_coords(metal)
            if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
                raise ValueError("Routing coordinates must be finite")
            if any(y not in self.route_rows for y in (y0, y1)):
                raise ValueError(f"Route is off the configured GT2N signal grid: {metal}")
            if self.tech_data.layout_profile == "gt2n_logic":
                if 2 in (a, b) and any(y in (16, 128) for y in (y0, y1)):
                    raise ValueError("GT2N logic M1 must stay on the inner tracks for row abutment")
                if (a, b) == (0, 1):
                    if x0 % 42 == 0:
                        if y0 != 72 or self.gates["n"].get(x0) != self.gates["p"].get(x0):
                            raise ValueError("GT2N logic gate access requires aligned complementary gates at y=72")
                    elif y0 == 72:
                        raise ValueError("GT2N logic diffusion access cannot extend into the central gate track")
            if not (
                0 <= x0 <= self.width
                and 0 <= x1 <= self.width
                and min(self.route_rows) <= y0 <= max(self.route_rows)
                and min(self.route_rows) <= y1 <= max(self.route_rows)
            ):
                raise ValueError(f"Route outside SH canvas: {metal}")
            if a != b and (x0 != x1 or y0 != y1):
                raise ValueError(f"Via endpoints must coincide: {metal}")
            if a == b and ((a in (0, 2) and x0 != x1) or (a in (1, 3) and y0 != y1)):
                raise ValueError(f"Route has an unsupported direction: {metal}")
            if a == 0:
                # PC edges are represented by GATE or SDCON below. Verify
                # those implicit connections instead of silently dropping them.
                for y in (y0, y1):
                    kind = "n" if y < 72 else "p"
                    nets = self.gates if x0 % 42 == 0 else self.terminals
                    if nets[kind].get(x0) != metal.net:
                        raise ValueError(
                            f"No matching device terminal for route: {metal}"
                        )

    def _route_coords(self, metal):
        return (
            metal.col_0 / self.SOLVER_RESCALE,
            metal.row_0 / self.SOLVER_RESCALE + self.ROUTE_Y_OFFSET,
            metal.col_1 / self.SOLVER_RESCALE,
            metal.row_1 / self.SOLVER_RESCALE + self.ROUTE_Y_OFFSET,
        )

    def _box(self, layer, x0, y0, x1, y1, datatype=0):
        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"Invalid {layer} rectangle: {(x0, y0, x1, y1)}")
        coords = [v / (1000 * self.DBU) for v in (x0, y0, x1, y1)]
        if any(not math.isclose(v, round(v), abs_tol=1e-8) for v in coords):
            raise ValueError(f"{layer} rectangle is off the 0.5 nm manufacturing grid")
        box = pya.Box(*(round(v) for v in coords))
        self.cell.shapes(self.layout.layer(LAYERS[layer], datatype)).insert(box)
        return box

    def _pin(self, layer, net, box):
        # GT2N uses pin-purpose rectangles AND text on datatype 251.
        shapes = self.cell.shapes(self.layout.layer(LAYERS[layer], 251))
        shapes.insert(box)
        center = box.center()
        shapes.insert(pya.Text(net, pya.Trans(center.x, center.y)))

    def _metal(self, layer, net, x0, y0, x1, y1):
        for port in self.boundary_ports:
            if port["net"] == net and port["layer"] == layer:
                if port["side"] == "left":
                    x0 = max(0, x0)
                elif port["side"] == "right":
                    x1 = min(self.width, x1)
        box = self._box(layer, x0, y0, x1, y1)
        self.route_regions.setdefault((layer, net), pya.Region()).insert(box)
        port_layers = {port["layer"] for port in self.boundary_ports if port["net"] == net}
        if net in self.tech_data.io_pins and (not port_layers or layer in port_layers):
            self._pin(layer, net, box)

    def draw(self):
        self._box("BOUNDARY", 0, 0, self.width, self.height, datatype=250)
        self._box("NWELL", 0, 72, self.width, 144)
        self._box("NSEL", 0, 0, self.width, 72)
        self._box("PSEL", 0, 72, self.width, 144)
        if self.vt != "lvt":
            self._box(self.vt.upper(), 0, 0, self.width, self.height)
        for net, y in (("VSS", 0), ("VDD", 144)):
            box = self._box("BPR", 0, y - 16, self.width, y + 16)
            self._pin("BPR", net.lower() if self.sram_profile else net, box)

        self._draw_devices()
        self._draw_contacts()
        self.route_regions = {}
        self._draw_routes()
        self._draw_boundary_ports()
        for layer in ("M0", "M1", "M2"):
            nets = [
                (net, region.merged())
                for (name, net), region in self.route_regions.items()
                if name == layer
            ]
            for i, (net, region) in enumerate(nets):
                for other_net, other in nets[i + 1 :]:
                    if not region.interacting(other).is_empty():
                        raise ValueError(
                            f"GT2N {layer} geometry would short {net} and {other_net}; revise the routing constraints"
                        )
        # Join overlapping ACT and SDCON rectangles at shared diffusions.
        for layer in ("ACT", "SDCON"):
            shapes = self.cell.shapes(self.layout.layer(LAYERS[layer], 0))
            region = pya.Region(shapes).merged()
            shapes.clear()
            shapes.insert(region)

    def _draw_devices(self):
        active_bottom, active_top = (35, 48) if self.nanosheet_width == 13 else (26, 57)
        for kind in ("n", "p"):
            y0, y1 = (
                (active_bottom, active_top)
                if kind == "n"
                else (144 - active_top, 144 - active_bottom)
            )
            for gate in self.gates[kind]:
                self._box("ACT", gate - 42, y0, gate + 42, y1)
        for i in range(self.tech_data.col + 1):
            x = i * 42
            self._box("GATE", x - 7, 0, x + 7, 144)
            for kind, y in (("n", 0), ("p", 72)):
                if x not in self.gates[kind]:
                    dummy_lo, dummy_hi = y, y + 72
                    if self.sram_profile:
                        if kind == "p" and x in self.gates["n"]:
                            dummy_lo = 77
                        elif kind == "n" and x in self.gates["p"]:
                            dummy_hi = 67
                    self._box("DUMMY", x - 7, dummy_lo, x + 7, dummy_hi)
            if (
                x in self.gates["n"]
                and x in self.gates["p"]
                and self.gates["n"][x] != self.gates["p"][x]
            ):
                half_cut = 21 if self.sram_profile else 7
                self._box("GCUT", x - half_cut, 67, x + half_cut, 77)
            elif self.sram_profile and ((x in self.gates["n"]) != (x in self.gates["p"])):
                self._box("GCUT", x - 21, 67, x + 21, 77)
        for y in (0, 144):
            self._box("GCUT", -7, y - 5, self.width + 7, y + 5)

    def _draw_contacts(self):
        contacts = {}
        for kind in ("n", "p"):
            for x, net in self.terminals[kind].items():
                # Released GT2N cells use the same SDCON extents for W13/W31.
                y0, y1 = (21, 62) if kind == "n" else (82, 123)
                if self.sram_profile:
                    n0, n1 = (35, 48) if self.nanosheet_width == 13 else (26, 57)
                    y0, y1 = (n0 - 5, n1 + 5) if kind == "n" else (144 - n1 - 5, 144 - n0 + 5)
                if net.upper() in ("VDD", "VSS"):
                    if kind == "n":
                        y0 = 10 if self.sram_profile else 5
                        self._box("VBPR", x - 8, y0, x + 8, y0 + 11)
                    else:
                        y1 = 134 if self.sram_profile else 139
                        self._box("VBPR", x - 8, y1 - 11, x + 8, y1)
                contacts[kind, x] = [net, y0, y1]
        for metal in self.metal_data:
            if (metal.metal_0, metal.metal_1) != (0, 1):
                continue
            x, y, _, _ = self._route_coords(metal)
            if x % 42 == 0:
                continue
            kind = "n" if y < 72 else "p"
            contact = contacts[kind, x]
            contact[1] = min(contact[1], y - 6)
            contact[2] = max(contact[2], y + 6)

        for x in sorted(set(self.terminals["n"]) | set(self.terminals["p"])):
            n, p = contacts.get(("n", x)), contacts.get(("p", x))
            if n and p and n[0] == p[0]:
                self._box("SDCON", x - 8, n[1], x + 8, p[2])
            else:
                if n and p and n[2] >= p[1]:
                    raise ValueError(f"SDCON would short {n[0]} and {p[0]} at x={x}")
                for contact in (n, p):
                    if contact:
                        self._box("SDCON", x - 8, contact[1], x + 8, contact[2])

    def _draw_routes(self):
        for metal in self.metal_data:
            a, b = metal.metal_0, metal.metal_1
            x0, y0, x1, y1 = self._route_coords(metal)
            if a == b == 0:
                continue
            if a != b:
                if a == 0:
                    layer = "VG" if x0 % 42 == 0 else "VSD"
                    half_width = 7 if layer == "VG" else 6.5
                else:
                    layer, half_width = ("V0" if a == 1 else "V1"), 7
                self._box(layer, x0 - half_width, y0 - 6, x0 + half_width, y0 + 6)
                # Every via needs a landing, including via-only solver nodes.
                if a <= 1:
                    self._metal("M0", metal.net, x0 - 12, y0 - 6, x0 + 12, y0 + 6)
                if a >= 1:
                    self._metal("M1", metal.net, x0 - 7, y0 - self.m1_end_extension, x0 + 7, y0 + self.m1_end_extension)
                if a == 2:
                    self._metal("M2", metal.net, x0 - 12, y0 - 6, x0 + 12, y0 + 6)
                continue

            layer = f"M{a - 1}"
            if a == 2:
                self._metal(
                    layer, metal.net, x0 - 7, min(y0, y1) - self.m1_end_extension, x0 + 7, max(y0, y1) + self.m1_end_extension
                )
            else:
                self._metal(
                    layer, metal.net, min(x0, x1) - 12, y0 - 6, max(x0, x1) + 12, y0 + 6
                )

    def _draw_boundary_ports(self):
        for port in self.boundary_ports:
            layer, net, side = port["layer"], port["net"], port["side"]
            row, col = port["row"], port["col"]
            index = {"M0": 1, "M1": 2, "M2": 3}[layer]
            # The attachment must be on a route of this net, including via
            # endpoints and points inside merged wire segments.
            connected = any(
                metal.net == net
                and index in (metal.metal_0, metal.metal_1)
                and min(metal.row_0, metal.row_1) <= row <= max(metal.row_0, metal.row_1)
                and min(metal.col_0, metal.col_1) <= col <= max(metal.col_0, metal.col_1)
                for metal in self.metal_data
            )
            x, y = col / self.SOLVER_RESCALE, row / self.SOLVER_RESCALE + self.ROUTE_Y_OFFSET
            if net not in self.tech_data.io_pins or not connected:
                raise ValueError(f"Boundary port {port['name']} is not attached to a routed signal I/O net")
            if not 0 <= x <= self.width or not 0 <= y <= self.height:
                raise ValueError(f"Boundary port {port['name']} is outside the cell")
            if side in ("top", "bottom"):
                physical_row = max(self.route_rows) if side == "top" else min(self.route_rows)
                expected_row = (physical_row - self.ROUTE_Y_OFFSET) * self.SOLVER_RESCALE
                if row != expected_row:
                    raise ValueError(f"Boundary port {port['name']} must attach at the outer routing row")
                self._metal(layer, net, x - 7, y if side == "top" else 0,
                            x + 7, self.height if side == "top" else y)
            elif side == "left":
                if col != 0:
                    raise ValueError(f"Boundary port {port['name']} must attach at the left routing edge")
                # A zero-length stub still needs a pin rectangle at the edge.
                self._metal(layer, net, 0, y - 6, max(x, 12), y + 6)
            else:
                self._metal(layer, net, min(x, self.width - 12), y - 6, self.width, y + 6)

    def save(self, filename):
        path = Path(filename)
        if path.exists():
            library = pya.Layout()
            library.read(str(path))
            # A PROBE3 file uses conflicting layer numbers; do not mix PDKs.
            boundary = library.find_layer(LAYERS["BOUNDARY"], 250)
            if boundary is None or all(
                c.shapes(boundary).is_empty() for c in library.each_cell()
            ):
                raise ValueError(
                    "Existing GDS is not a GT2N library (missing prBoundary 235/250)"
                )
            # Require a DBU that exactly represents this backend's half-nm grid.
            ratio = self.DBU / library.dbu
            if not math.isclose(ratio, round(ratio)) or ratio < 1:
                raise ValueError(
                    "Existing GDS DBU cannot represent the GT2N 0.5 nm grid"
                )
            target = library.cell(self.subckt_name)
            if target is None:
                target = library.create_cell(self.subckt_name)
            else:
                target.clear()
            target.copy_tree(self.cell)
            self.layout, self.cell = library, target
        path.parent.mkdir(parents=True, exist_ok=True)
        self.layout.write(str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result_file", required=True)
    parser.add_argument("--subckt_name", required=True)
    parser.add_argument("--gds_file", required=True)
    parser.add_argument("--nanosheet_width", type=int, choices=(13, 31), default=13)
    parser.add_argument("--vt", choices=VT_FLAVORS, default="lvt")
    args = parser.parse_args()
    try:
        GT2NLayout(**vars(args))
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
