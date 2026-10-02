"""Backside-powered GT2N N/P/P/N thin-cell technology adapter.

Builds devices and intrinsic conductors from a fixed-band placement, then
provides legal access sites to BandRouter. No metal routes come from this
adapter or from the external reference generator.
"""

from collections import defaultdict
from itertools import pairwise

import klayout.db as db

from cpcell.core.band_router import BandRouter, RouteEdge, Shape


def box(layer, x0, y0, x1, y1):
    values = [2 * v for v in (x0, y0, x1, y1)]
    if any(v != int(v) for v in values) or x1 <= x0 or y1 <= y0:
        raise ValueError(f"Invalid half-nm rectangle on {layer}")
    return Shape(layer, tuple(int(v) for v in values))


def region(shapes, layer):
    result = db.Region()
    for shape in shapes:
        if shape.layer == layer:
            result.insert(db.Box(*shape.box))
    return result.merged()


def canonical_net(net):
    return net.lower() if net.upper() in ("VDD", "VSS") else net


def design_from_circuit(circuit, settings, n_width, p_width):
    placement = settings["placement"]
    if set(placement) != set(circuit.transistors):
        raise ValueError("Placement must name every expanded transistor exactly once")
    devices = []
    for name, transistor in circuit.transistors.items():
        spec = placement[name]
        if set(spec) != {"band", "slot", "flip"}:
            raise ValueError("Each placement needs band, slot and flip")
        devices.append(
            {
                "name": name,
                "model": transistor.model.value,
                "d": canonical_net(transistor.source),
                "g": canonical_net(transistor.gate),
                "s": canonical_net(transistor.drain),
                "b": canonical_net(transistor.bulk),
                **spec,
            }
        )
    return {
        "n_width": n_width,
        "p_width": p_width,
        "devices": devices,
        "ports": settings["ports"],
        "io_pins": list(circuit.io_pins),
    }


class ThinProblem:
    """Fixed two-column, four-band backside cell; routing is optimized."""

    def __init__(self, design):
        self.design = design
        self.n_width, self.p_width = design["n_width"], design["p_width"]
        if (
            type(self.n_width) is not int
            or type(self.p_width) is not int
            or self.n_width not in (13, 31)
            or self.p_width not in (13, 31)
        ):
            raise ValueError("Thin sheets must be 13 or 31 nm")
        self.width = 84
        nlo, nhi = 26, 26 + self.n_width
        plo = max(nhi + 30, 87)
        phi = plo + self.p_width
        self.height = max(288, 2 * phi + 56)
        self.bands = [
            (nlo, nhi),
            (plo, phi),
            (self.height - phi, self.height - plo),
            (self.height - nhi, self.height - nlo),
        ]
        self.cut = (nhi + 10, nhi + 20)
        self.q = phi + 12
        self.bitline = (nlo + nhi) / 2
        self.rows = [
            0,
            self.bitline,
            self.q,
            self.height - self.q,
            self.height - self.bitline,
            self.height,
        ]
        self.xs, self.m1_xs = [0, 21, 42, 63, 84], [21, 42, 63]
        self.frame = []
        self.terminals = defaultdict(list)
        self.intrinsic = []
        self._placement()
        self._frame()
        self._conductors()
        self.edges = []
        self._grid()
        self._access()
        self.ports = self._ports()
        self.router = BandRouter(self.edges, dict(self.terminals), self.ports)

    @property
    def name(self):
        return f"gt2n_sram_thin_n{self.n_width}p{self.p_width}_lvt"

    def _placement(self):
        self.devices = self.design["devices"]
        self.gates, self.diffusion = {}, {}
        names = set()
        for dev in self.devices:
            band, slot = dev["band"], dev["slot"]
            if (
                type(band) is not int
                or type(slot) is not int
                or band not in range(4)
                or slot not in (0, 1)
                or type(dev["flip"]) is not bool
            ):
                raise ValueError("Illegal fixed-band placement")
            model = "nmos" if band in (0, 3) else "pmos"
            if (
                dev["model"] != model
                or dev["name"] in names
                or (band, slot) in self.gates
            ):
                raise ValueError("Overlapping, duplicated or wrong-polarity placement")
            names.add(dev["name"])
            if dev["b"] != ("vss" if model == "nmos" else "vdd"):
                raise ValueError("Unsupported body connection")
            self.gates[band, slot] = dev["g"]
            left, right = (dev["s"], dev["d"]) if dev["flip"] else (dev["d"], dev["s"])
            for x, net in ((42 * slot, left), (42 * (slot + 1), right)):
                if self.diffusion.setdefault((band, x), net) != net:
                    raise ValueError("Adjacent devices disagree on shared diffusion")
        if set(self.gates) != {(0, 0), (0, 1), (1, 1), (2, 0), (3, 0), (3, 1)}:
            raise ValueError(
                "Thin profile requires N:2/P:1/P:1/N:2 devices with inward pull-ups"
            )

    def _frame(self):
        h = self.height

        def add(layer, *coords):
            self.frame.append(box(layer, *coords))

        for x in (21, 63):
            add("GATE", x - 7, -10, x + 7, h + 10)
        for band, slot in self.gates:
            lo, hi = self.bands[band]
            gate = 21 + 42 * slot
            add("ACT", max(0, gate - 42), lo, min(84, gate + 42), hi)
        add("DUMMY", 14, self.cut[1], 28, self.bands[1][1])
        add("DUMMY", 56, self.bands[2][0], 70, h - self.cut[1])
        add("GCUT", 0, self.cut[0], 42, self.cut[1])
        add("GCUT", 42, h - self.cut[1], 84, h - self.cut[0])
        add("GCUT", 33, -5, 75, 5)
        add("GCUT", 9, h - 5, 51, h + 5)
        nsel = self.bands[1][0] - 15
        add("NSEL", 0, 0, 84, nsel)
        add("NSEL", 0, h - nsel, 84, h)
        for layer in ("PSEL", "NWELL"):
            add(layer, 0, nsel, 84, h - nsel)
        self.rails = [(0, "vss"), (h / 2, "vdd"), (h, "vss")]
        for y, _ in self.rails:
            add("BPR", 0, y - 16, 84, y + 16)
        add("BOUNDARY", 0, 0, 84, h)

    def _conductors(self):
        poly = (
            region(self.frame, "GATE")
            - region(self.frame, "DUMMY")
            - region(self.frame, "GCUT")
        )
        for polygon in poly.merged().each():
            nets = {
                net
                for (band, slot), net in self.gates.items()
                if polygon.inside(db.Point(2 * (21 + 42 * slot), sum(self.bands[band])))
            }
            if len(nets) > 1:
                raise ValueError("Gate cuts leave different gate nets shorted")
            if nets:
                net = next(iter(nets))
                self._terminal(net, "VG", db.Region(polygon), polygon.bbox().center().x)
        groups = defaultdict(list)
        for (band, x), net in self.diffusion.items():
            groups[x, net].append(band)
        for (x, net), bands in groups.items():
            y0 = min(self.bands[b][0] for b in bands) - 5
            y1 = max(self.bands[b][1] for b in bands) + 5
            if net in ("vss", "vdd"):
                if len(bands) != 1 or x not in (0, 84):
                    raise ValueError("Thin rail taps must be on a shared cell edge")
                band = bands[0]
                if net != ("vss" if band in (0, 3) else "vdd"):
                    raise ValueError("Wrong rail on a device band")
                if band == 0:
                    y0, via0, via1 = 0, 5, 16
                elif band == 3:
                    y1, via0, via1 = self.height, self.height - 16, self.height - 5
                else:
                    via0, via1 = self.height / 2 - 5.5, self.height / 2 + 5.5
                    if band == 1:
                        y1 = via1
                    else:
                        y0 = via0
                self.frame.append(box("VBPR", x - 8, via0, x + 8, via1))
            else:
                if set(bands) == {0, 1}:
                    y1 = self.q + 6
                elif set(bands) == {2, 3}:
                    y0 = self.height - self.q - 6
                elif len(bands) != 1:
                    raise ValueError("Unsupported diffusion sharing across bands")
            shape = box("SDCON", x - 8, y0, x + 8, y1)
            self.frame.append(shape)
            if net not in ("vdd", "vss"):
                self._terminal(net, "VSD", db.Region(db.Box(*shape.box)), 2 * x)

    def _terminal(self, net, layer, conductor, x):
        node = ("T", str(len(self.intrinsic)))
        self.intrinsic.append((node, net, layer, conductor, x))
        self.terminals[net].append(node)

    def _grid(self):
        def node(layer, x, y):
            return (layer, int(2 * y), int(2 * x))

        for y in self.rows:
            for a, b in pairwise(self.xs):
                shape = box("M0", a - 12, y - 6, b + 12, y + 6)
                self.edges.append(
                    RouteEdge(node(1, a, y), node(1, b, y), (shape,), int(2 * (b - a)))
                )
        for x in self.m1_xs:
            for a, b in pairwise(self.rows):
                shape = box("M1", x - 7, a - 10, x + 7, b + 10)
                self.edges.append(
                    RouteEdge(node(2, x, a), node(2, x, b), (shape,), int(2 * (b - a)))
                )
            for y in self.rows:
                shapes = (
                    box("V0", x - 7, y - 6, x + 7, y + 6),
                    box("M0", x - 12, y - 6, x + 12, y + 6),
                    box("M1", x - 7, y - 10, x + 7, y + 10),
                )
                self.edges.append(RouteEdge(node(1, x, y), node(2, x, y), shapes, 100))

    def _access(self):
        act = region(self.frame, "ACT")
        for node, net, layer, conductor, x in self.intrinsic:
            count = 0
            for y_nm in self.rows:
                y = int(2 * y_nm)
                half = 14 if layer == "VG" else 13
                via = db.Box(x - half, y - 12, x + half, y + 12)
                if not (db.Region(via) - conductor).is_empty():
                    continue
                if layer == "VG" and not (db.Region(via).sized(0, 12) & act).is_empty():
                    continue
                shapes = (
                    Shape(layer, (via.left, via.bottom, via.right, via.top)),
                    Shape("M0", (x - 24, y - 12, x + 24, y + 12)),
                )
                self.edges.append(RouteEdge(node, (1, y, x), shapes, 100, net))
                count += 1
            if not count:
                raise ValueError(f"No legal contact access for {net} {node}")

    def _ports(self):
        ports = defaultdict(list)
        self.port_records = []
        names = set()
        for port in self.design["ports"]:
            if set(port) != {"name", "net", "side", "layer", "track"}:
                raise ValueError("Thin ports require name, net, side, layer, track")
            if (
                not isinstance(port["name"], str)
                or not port["name"]
                or port["name"] in names
            ):
                raise ValueError("Thin port names must be nonempty and unique")
            names.add(port["name"])
            if port["net"] not in self.design["io_pins"]:
                raise ValueError("Boundary port must be a signal I/O net")
            index, side = port["track"], port["side"]
            if port["layer"] == "M0" and side in ("left", "right"):
                if type(index) is not int or index not in range(len(self.rows)):
                    raise ValueError("Invalid M0 port track")
                node = (1, int(2 * self.rows[index]), 0 if side == "left" else 168)
            elif port["layer"] == "M1" and side in ("bottom", "top"):
                if type(index) is not int or index not in range(len(self.m1_xs)):
                    raise ValueError("Invalid M1 port track")
                node = (
                    2,
                    0 if side == "bottom" else 2 * self.height,
                    2 * self.m1_xs[index],
                )
            else:
                raise ValueError("Invalid thin port side/layer")
            if any(node == existing["node"] for existing in self.port_records):
                raise ValueError("Boundary ports cannot share a node")
            ports[port["net"]].append(node)
            self.port_records.append({**port, "node": node})
        return dict(ports)

    def solve(self, seconds=30):
        return {
            "format": "cpcell.fixed_bands.v1",
            "design": self.design,
            "width_nm": self.width,
            "height_nm": self.height,
            **self.router.solve(seconds),
        }

    def schematic(self):
        lines = [f".subckt {self.name} WL BL BLN vdd vss"]
        for dev in self.devices:
            width = self.n_width if dev["model"] == "nmos" else self.p_width
            lines.append(
                f"{dev['name']} {dev['d']} {dev['g']} {dev['s']} {dev['b']} {dev['model']}_lvt W={width / 1000:g}u L=0.014u M=1"
            )
        return "\n".join(lines + [f".ends {self.name}", ""])
