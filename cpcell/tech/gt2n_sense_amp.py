"""Fixed, matched GT2N sense-amplifier devices with CP-SAT generated routing.

Twelve explicit 31 nm / 14 nm fingers implement the current-latched reference.
Parallel tail/precharge fingers remain separate physical devices. This adapter
uses a two-row frame; it does not pass through the single-height exporter.
"""

from collections import defaultdict
from copy import deepcopy
from itertools import pairwise

from cpcell.core.band_router import BandRouter, RouteEdge
from cpcell.tech.gt2n_thin import box

PINS = ("SA", "SAN", "SAE", "SAPRECHN", "QA", "QAN", "vdd", "vss")
# Each row lists the physical left diffusion, gate and right diffusion.
N = [
    ("N1", "QA", "QAN", "XA"),
    ("INA", "XA", "SA", "T"),
    ("TAILA", "T", "SAE", "vss"),
    ("TAILB", "vss", "SAE", "T"),
    ("INB", "T", "SAN", "XB"),
    ("N2", "XB", "QA", "QAN"),
]
P = [
    ("P1", "QA", "QAN", "vdd"),
    ("PCA1", "vdd", "SAPRECHN", "QA"),
    ("PCA2", "QA", "SAPRECHN", "vdd"),
    ("PCB2", "vdd", "SAPRECHN", "QAN"),
    ("PCB1", "QAN", "SAPRECHN", "vdd"),
    ("P2", "vdd", "QA", "QAN"),
]
PAIRS = [(a[0], b[0]) for row in (N, P) for a, b in zip(row[:3], reversed(row[3:]))]


def default_design():
    return {
        "name": "gt2n_cpcell_senseamp_w31_lvt",
        "devices": [
            dict(
                name=name,
                model=model,
                left=left,
                gate=gate,
                right=right,
                slot=slot,
                width_nm=31,
                length_nm=14,
                fingers=1,
                vt="lvt",
            )
            for model, row in (("nmos", N), ("pmos", P))
            for slot, (name, left, gate, right) in enumerate(row, 1)
        ],
        "matched_devices": [list(p) for p in PAIRS],
        "finger_groups": {
            "TAIL": ["TAILA", "TAILB"],
            "PRE_A": ["PCA1", "PCA2"],
            "PRE_B": ["PCB1", "PCB2"],
        },
    }


def node(layer, x, y):
    return (layer, int(2 * y), int(2 * x))


class SenseAmpProblem:
    width, height = 294, 288

    def __init__(self, design=None):
        self.design = deepcopy(default_design() if design is None else design)
        self.name = self.design["name"]
        if (
            not self.name.isascii()
            or not self.name.replace("_", "").isalnum()
            or len(self.name) > 32
        ):
            raise ValueError("Invalid sense-amplifier cell name")
        expected = default_design()
        if set(self.design) != set(expected):
            raise ValueError("Unknown or missing sense-amplifier design fields")
        # Validate exact sizing/topology before drawing or solving. This initial
        # profile deliberately exposes no unsupported arbitrary analog sizes.
        devices = {d["name"]: d for d in self.design["devices"]}
        if len(devices) != 12 or len(self.design["devices"]) != 12:
            raise ValueError("Sense amplifier requires twelve unique physical fingers")
        if self.design["matched_devices"] != expected["matched_devices"]:
            raise ValueError("All six device matching pairs are required")
        if self.design["finger_groups"] != expected["finger_groups"]:
            raise ValueError("Parallel tail and precharge fingers must remain explicit")
        for d in expected["devices"]:
            if devices.get(d["name"]) != d:
                raise ValueError(
                    f"Unsupported sizing, placement or topology for {d['name']}"
                )
        self.frame, self.edges = [], []
        self.terminals = defaultdict(list)
        self.ports, self.pin_boxes = {}, {}
        self.rows0 = [14, 48, 72, 96, 216]
        self.rows2 = [132, 156, 180, 240]
        self.rows1 = sorted(set(self.rows0 + self.rows2 + [159, 210]))
        self.columns = [21 + 42 * k for k in range(7)]
        self.xs = list(range(21, 274, 21))
        self._frame()
        self._grid()
        self._contacts()
        self._ports()
        self.router = BandRouter(
            self.edges,
            dict(self.terminals),
            self.ports,
            spacing={
                "M0": dict(horizontal=34, vertical=24, corner=30),
                "M1": dict(horizontal=28, vertical=40, corner=32),
                "M2": dict(horizontal=34, vertical=24, corner=30),
                "V0": 28,
                "V1": 28,
                "VG": 28,
                "VSD": 28,
            },
            matching={
                "mirror_x": 294,
                "mirror_nets": [("SA", "SAN"), ("SAE", "SAE"), ("T", "T")],
                "mirror_contact_nets": [("QA", "QAN")],
                "balanced_nets": [("QA", "QAN")],
            },
        )

    def _frame(self):
        def add(layer, *coords):
            self.frame.append(box(layer, *coords))

        add("BOUNDARY", 0, 0, 294, 288)
        for x in range(0, 295, 42):
            add("GATE", x - 7, 0, x + 7, 288)
            if x in (0, 294):
                add("DUMMY", x - 7, 0, x + 7, 288)
        for lo, hi, layer in [
            (0, 72, "NSEL"),
            (72, 216, "PSEL"),
            (216, 288, "NSEL"),
            (72, 216, "NWELL"),
        ]:
            add(layer, 0, lo, 294, hi)
        self.rails = [(0, "vss"), (144, "vdd"), (288, "vss")]
        for y, _ in self.rails:
            add("BPR", 0, y - 16, 294, y + 16)
        for y in (0, 288):
            add("GCUT", -7, y - 5, 301, y + 5)
        add("GCUT", -7, 139, 63, 149)
        add("GCUT", 231, 139, 301, 149)
        add("GCUT", 63, 67, 231, 77)
        for lo, hi in [(26, 57), (87, 118)]:
            add("ACT", 0, lo, 294, hi)
        for k in (0, 6):
            x = self.columns[k]
            add("SDCON", x - 8, 21, x + 8, 123)
        for k in (1, 3, 5):
            x = self.columns[k]
            add("SDCON", x - 8, 82, x + 8, 139)
            add("VBPR", x - 8, 128, x + 8, 139)
        for k in (2, 4):
            x = self.columns[k]
            add("SDCON", x - 8, 21, x + 8, 62)
            add("SDCON", x - 8, 82, x + 8, 123)
        add("SDCON", 139, 5, 155, 62)
        add("VBPR", 139, 5, 155, 16)

    def _edge(self, u, v, shapes, cost, owner=None):
        self.edges.append(RouteEdge(u, v, tuple(shapes), int(cost), owner))

    def _grid(self):
        for layer, rows in [(1, self.rows0), (3, self.rows2)]:
            metal = "M0" if layer == 1 else "M2"
            xs = self.xs if layer == 1 else self.columns
            for y in rows:
                for a, b in pairwise(xs):
                    self._edge(
                        node(layer, a, y),
                        node(layer, b, y),
                        [box(metal, a - 12, y - 6, b + 12, y + 6)],
                        2 * (b - a),
                    )
        for x in self.columns:
            for a, b in pairwise(self.rows1):
                self._edge(
                    node(2, x, a),
                    node(2, x, b),
                    [box("M1", x - 7, a - 10, x + 7, b + 10)],
                    2 * (b - a),
                )
            for layer, rows, via, metal in [
                (1, self.rows0, "V0", "M0"),
                (3, self.rows2, "V1", "M2"),
            ]:
                for y in rows:
                    self._edge(
                        node(layer, x, y),
                        node(2, x, y),
                        [
                            box(via, x - 7, y - 6, x + 7, y + 6),
                            box(metal, x - 12, y - 6, x + 12, y + 6),
                            box("M1", x - 7, y - 10, x + 7, y + 10),
                        ],
                        100,
                    )

    def _terminal(self, net, kind, x, ys):
        terminal = ("T", str(sum(len(v) for v in self.terminals.values())))
        self.terminals[net].append(terminal)
        half = 7 if kind == "VG" else 6.5
        for y in ys:
            self._edge(
                terminal,
                node(1, x, y),
                [
                    box(kind, x - half, y - 6, x + half, y + 6),
                    box("M0", x - 12, y - 6, x + 12, y + 6),
                ],
                100,
                net,
            )

    def _contacts(self):
        # Contacts are chosen from legal access sites, not from reference metal.
        for net, x in [("QA", 21), ("QAN", 273)]:
            self._terminal(net, "VSD", x, [48, 72, 96])
        for net, x in [("QA", 105), ("QAN", 189)]:
            self._terminal(net, "VSD", x, [96])
            self._terminal("T", "VSD", x, [48])
        for net, x in [("QAN", 42), ("QA", 252)]:
            self._terminal(net, "VG", x, [14, 72])
        for net, x in [("SA", 84), ("SAE", 126), ("SAE", 168), ("SAN", 210)]:
            self._terminal(net, "VG", x, [14])
            self._terminal("SAPRECHN", "VG", x, [216])

    def _ports(self):
        for net, k in [
            ("SA", 1),
            ("SAN", 5),
            ("SAE", 3),
            ("QA", 2),
            ("QAN", 4),
            ("SAPRECHN", 0),
        ]:
            x = self.columns[k]
            y0 = 210 if net == "SAPRECHN" else 159
            pin = ("P", net)
            shape = box("M1", x - 7, y0, x + 7, 273)
            self.pin_boxes[net] = shape
            self.ports[net] = [pin]
            self._edge(node(2, x, y0), pin, [shape], 2 * (273 - y0), net)

    def schematic(self):
        lines = [f".SUBCKT {self.name} {' '.join(PINS)}"]
        for d in self.design["devices"]:
            # Orient S/D consistently with the reference, especially the
            # intentionally duplicated tail and precharge fingers.
            left, right = d["left"], d["right"]
            if d["name"] in ("TAILB", "INB", "N2", "PCA1", "PCB2", "P2"):
                left, right = right, left
            bulk = "vss" if d["model"] == "nmos" else "vdd"
            lines.append(
                f"M{d['name']} {left} {d['gate']} {right} {bulk} {d['model']}_lvt W=0.031u L=0.014u M=1"
            )
        return "\n".join(lines + [f".ENDS {self.name}", ""])

    def solve(self, seconds=60):
        return dict(
            self.router.solve(seconds),
            format="cpcell.sense_amp.v1",
            design=self.design,
            width_nm=self.width,
            height_nm=self.height,
        )
