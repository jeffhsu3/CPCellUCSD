"""GT2N cross-coupled N/P pair with independently contacted gates.

Explicit 84 nm split and 126 nm staggered profiles, not a general analog placer.
Its feasibility result covers the supplied frame, track list and routing graph.
"""

from collections import defaultdict
from copy import deepcopy
from itertools import pairwise
import math
import re

import klayout.db as db

from cpcell.core.band_router import BandRouter, RouteEdge
from cpcell.tech.gt2n_thin import box, region


FLAVORS = ("lvt", "elvt", "ulvt", "svt", "hvt")
SPACING = {
    "M0": dict(horizontal=34, vertical=24, corner=30),
    "M1": dict(horizontal=28, vertical=40, corner=32),
    "M2": dict(horizontal=34, vertical=24, corner=30),
    "V0": 28,
    "V1": 28,
    # Contact separation is already constrained by the enclosing M0 pads.
    # GT2's VG.6 is a placeholder, not an additional 14 nm spacing rule.
    "VG": 0,
    "VSD": 0,
}


def cell_name(sheet_width_nm, height_nm, vt, arrangement):
    """Name a generated cell.

    Row-height (144 nm) cells are drop-in gt2_6t library cells and follow the
    released ``gt2_6t_<cell>_w<sheet>_<vt>`` pattern; taller feasibility
    controls keep a research name that states their height.
    """
    if height_nm == 144:
        tag = {"split": "sp", "staggered": "sg"}[arrangement]
        return f"gt2_6t_decapcc_{tag}_w{sheet_width_nm}_{vt}"
    return f"gt2n_{arrangement}_decap_w{sheet_width_nm}_h{height_nm}_{vt}"


def default_design(sheet_width_nm=31, height_nm=144, vt="lvt", arrangement="split"):
    return dict(
        name=cell_name(sheet_width_nm, height_nm, vt, arrangement),
        arrangement=arrangement,
        n_width_nm=sheet_width_nm,
        p_width_nm=sheet_width_nm,
        height_nm=height_nm,
        vt=vt,
        n_flip=False,
        p_flip=arrangement == "staggered",
        cut_center_nm=height_nm / 2,
        m0_tracks_nm=list(range(24, height_nm, 24)),
    )


def node(layer, x, y):
    return (layer, int(2 * y), int(2 * x))


class SplitGateDecapProblem:
    def __init__(self, design=None):
        self.design = deepcopy(default_design() if design is None else design)
        d = self.design
        # Preserve previously generated split-gate JSON and route numbering.
        d.setdefault("arrangement", "split")
        if set(d) != set(default_design()):
            raise ValueError("Unknown or missing split-gate design fields")
        if d["arrangement"] not in ("split", "staggered"):
            raise ValueError("Arrangement must be split or staggered")
        self.width = 84 if d["arrangement"] == "split" else 126
        self.gate_x = (42, 42 if self.width == 84 else 84)
        if not isinstance(d["name"], str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]{0,63}", d["name"]
        ):
            raise ValueError("Invalid split-gate cell name")
        if any(
            type(d[k]) is not int or d[k] not in (13, 31)
            for k in ("n_width_nm", "p_width_nm")
        ):
            raise ValueError("Sheet widths must be 13 or 31 nm")
        if (
            type(d["height_nm"]) is not int
            or d["height_nm"] < 144
            or d["height_nm"] % 24
        ):
            raise ValueError("Height must be a multiple of 24 nm and at least 144 nm")
        if d["vt"] not in FLAVORS or any(
            type(d[k]) is not bool for k in ("n_flip", "p_flip")
        ):
            raise ValueError("Invalid threshold flavor or diffusion orientation")
        self.name, self.height = d["name"], d["height_nm"]
        tracks = d["m0_tracks_nm"]
        if not isinstance(tracks, list) or not tracks:
            raise ValueError("At least one explicit M0 track is required")
        values = [*tracks, d["cut_center_nm"]]
        if any(
            type(v) not in (int, float) or not math.isfinite(v) or 2 * v != int(2 * v)
            for v in values
        ):
            raise ValueError("Tracks and gate cut must use finite half-nm coordinates")
        if tracks != sorted(set(tracks)) or any(
            y < 12 or y > self.height - 12 for y in tracks
        ):
            raise ValueError(
                "M0 tracks must be sorted, unique, and inside the footprint"
            )
        self.rows = tracks
        self.bands = [
            (41.5 - d["n_width_nm"] / 2, 41.5 + d["n_width_nm"] / 2),
            (
                self.height - 41.5 - d["p_width_nm"] / 2,
                self.height - 41.5 + d["p_width_nm"] / 2,
            ),
        ]
        self.cut = (d["cut_center_nm"] - 5, d["cut_center_nm"] + 5)
        if self.cut[0] - self.bands[0][1] < 10 or self.bands[1][0] - self.cut[1] < 10:
            raise ValueError("Gate cut must clear both ACT bands by at least 10 nm")
        self.frame, self.edges, self.intrinsic = [], [], []
        self.terminals = defaultdict(list)
        self.access = []
        self._frame()
        self._conductors()
        self._grid()
        self._contacts()
        self.router = BandRouter(self.edges, dict(self.terminals), {}, spacing=SPACING)
        # A via-only M1 landing is 20 nm long. Require an adjoining M1 segment
        # so every selected M1 conductor meets the 28 nm minimum length.
        for (net, i), used in self.router.used.items():
            edge = self.edges[i]
            if any(s.layer in ("V0", "V1") for s in edge.shapes):
                landing = edge.u if edge.u[0] == 2 else edge.v
                adjacent = [
                    self.router.used[net, j]
                    for j, e in enumerate(self.edges)
                    if (net, j) in self.router.used
                    and e.u[0] == e.v[0] == 2
                    and landing in (e.u, e.v)
                ]
                self.router.model.Add(used <= sum(adjacent))

    def _frame(self):
        def add(layer, *coords):
            self.frame.append(box(layer, *coords))

        w, h = self.width, self.height
        add("BOUNDARY", 0, 0, w, h)
        add("NSEL", 0, 0, w, h / 2)
        add("PSEL", 0, h / 2, w, h)
        add("NWELL", 0, h / 2, w, h)
        for x in range(0, w + 1, 42):
            add("GATE", x - 7, 0, x + 7, h)
            if x in (0, w):
                add("DUMMY", x - 7, 0, x + 7, h)
        for y in (0, h):
            add("BPR", 0, y - 16, w, y + 16)
            add("GCUT", -7, y - 5, w + 7, y + 5)
        if self.design["arrangement"] == "split":
            add("GCUT", -7, self.cut[0], w + 7, self.cut[1])
        else:
            # Adjacent N/P columns each terminate in a dummy on the opposite
            # ACT edge. The markers start 10 nm from the centre line: 4 nm
            # clear of the centre-track VG (VG.3 forbids touching) and 5 nm
            # short of the ACT edge they must cover (ACT.6).
            center = self.design["cut_center_nm"]
            add("DUMMY", 35, center + 10, 49, h)
            add("DUMMY", 77, 0, 91, center - 10)
        staggered = self.design["arrangement"] == "staggered"
        for x, (lo, hi) in zip(self.gate_x, self.bands):
            # Staggered: both bands run the full cell width, as in every
            # released multi-pitch cell, so a one-pitch tap or filler beside
            # the cell merges its 42 nm active with ours (ACT.2). The other
            # device's gate then crosses this band as a tied-off isolation
            # gate under its partial DUMMY marker, as gate x=84 does in the
            # released mux2.
            if staggered:
                add("ACT", 0, lo, w, hi)
            else:
                add("ACT", x - 42, lo, x + 42, hi)
        if self.design["vt"] != "lvt":
            add(self.design["vt"].upper(), 0, 0, w, h)
        for kind, band in enumerate(self.bands):
            x = self.gate_x[kind]
            flip = self.design["p_flip" if kind else "n_flip"]
            source = x + 21 if flip else x - 21
            drain = 2 * x - source
            # Keep the released 41 nm drain contact length for W13 and W31.
            clo, chi = (21, 62) if kind == 0 else (h - 62, h - 21)
            slo, shi = (5, chi) if kind == 0 else (clo, h - 5)
            via0, via1 = (5, 16) if kind == 0 else (h - 16, h - 5)
            add("SDCON", source - 8, slo, source + 8, shi)
            add("VBPR", source - 8, via0, source + 8, via1)
            if staggered:
                # Contact the diffusion beyond the isolation gate and tie it
                # to the source supply, so no active is left floating.
                tie = 2 * drain - source
                add("SDCON", tie - 8, slo, tie + 8, shi)
                add("VBPR", tie - 8, via0, tie + 8, via1)
            shape = box("SDCON", drain - 8, clo, drain + 8, chi)
            self.frame.append(shape)
            self._terminal(
                "drain_p" if kind else "drain_n",
                "net12" if kind else "net7",
                "VSD",
                db.Region(db.Box(*shape.box)),
                drain,
            )

    def _terminal(self, name, net, layer, conductor, x):
        terminal = ("T", name)
        self.terminals[net].append(terminal)
        self.intrinsic.append((terminal, net, layer, conductor, x))

    def _conductors(self):
        # Derive actual electrical gate segments. Geometric x-alignment alone
        # must never merge N/P gates separated by a cut.
        gates = (
            region(self.frame, "GATE")
            - region(self.frame, "GCUT")
            - region(self.frame, "DUMMY")
        )
        assigned = set()
        for polygon in gates.merged().each():
            devices = [
                i
                for i, band in enumerate(self.bands)
                if polygon.inside(db.Point(2 * self.gate_x[i], int(sum(band))))
            ]
            if len(devices) > 1:
                raise ValueError("Gate cuts leave different gate nets shorted")
            if devices:
                kind = devices[0]
                assigned.add(kind)
                self._terminal(
                    "gate_p" if kind else "gate_n",
                    "net7" if kind else "net12",
                    "VG",
                    db.Region(polygon),
                    self.gate_x[kind],
                )
        if assigned != {0, 1}:
            raise ValueError("Missing split-gate transistor segment")

    def _edge(self, u, v, shapes, cost, owner=None):
        # Routing metals stay inside the cell and retain enough clearance for
        # reflected copies on every boundary; rail shapes may straddle it.
        for s in shapes:
            x0, y0, x1, y1 = s.box
            if s.layer in ("M0", "M2") and (
                x0 < 17
                or x1 > 2 * self.width - 17
                or y0 < 12
                or y1 > 2 * self.height - 12
            ):
                return
            if s.layer == "M1" and (
                x0 < 14
                or x1 > 2 * self.width - 14
                or y0 < 20
                or y1 > 2 * self.height - 20
            ):
                return
        self.edges.append(RouteEdge(u, v, tuple(shapes), int(cost), owner))

    def _grid(self):
        for layer, metal in ((1, "M0"), (3, "M2")):
            for y in self.rows:
                for a, b in pairwise(range(21, self.width, 21)):
                    self._edge(
                        node(layer, a, y),
                        node(layer, b, y),
                        [box(metal, a - 12, y - 6, b + 12, y + 6)],
                        2 * (b - a),
                    )
        for x in range(21, self.width, 42):
            for a, b in pairwise(self.rows):
                if b - a >= 8:
                    self._edge(
                        node(2, x, a),
                        node(2, x, b),
                        [box("M1", x - 7, a - 10, x + 7, b + 10)],
                        2 * (b - a),
                    )
            for y in self.rows:
                for layer, metal, via in ((1, "M0", "V0"), (3, "M2", "V1")):
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

    def _contacts(self):
        act = region(self.frame, "ACT")
        forbidden = region(self.frame, "GCUT") + region(self.frame, "DUMMY")
        for terminal, net, layer, conductor, x in self.intrinsic:
            candidates = []
            for y in self.rows:
                half = 7 if layer == "VG" else 6.5
                shape = box(layer, x - half, y - 6, x + half, y + 6)
                via = db.Region(db.Box(*shape.box))
                reasons = []
                if not (via - conductor).is_empty():
                    reasons.append("outside_conductor")
                if layer == "VG":
                    if not via.interacting(forbidden).is_empty():
                        reasons.append("touches_gate_cut_or_dummy")
                    if (
                        not (via.sized(0, 12, 2) & act).is_empty()
                        or not via.interacting(act).is_empty()
                    ):
                        reasons.append("VG_ACT_clearance_below_6nm")
                before = len(self.edges)
                if not reasons:
                    self._edge(
                        terminal,
                        node(1, x, y),
                        [shape, box("M0", x - 12, y - 6, x + 12, y + 6)],
                        100,
                        net,
                    )
                    if len(self.edges) == before:
                        reasons.append("boundary_clearance")
                candidates.append(
                    dict(y_nm=y, allowed=not reasons, rejected_by=reasons)
                )
            self.access.append(
                dict(terminal=terminal[1], net=net, layer=layer, candidates=candidates)
            )
        # Empty access sets are retained as terminals, not silently dropped.
        # Flow conservation then produces a genuine CP-SAT INFEASIBLE result.

    def solve(self, seconds=30):
        result = self.router.solve_result(seconds)
        return dict(
            format="cpcell.split_gate.v1",
            design=deepcopy(self.design),
            width_nm=self.width,
            height_nm=self.height,
            scope=f"Fixed {self.design['arrangement']} decap placement, explicit tracks, M0/M1/M2 candidates and mirrored boundary clearance; not a global width proof",
            access=deepcopy(self.access),
            blocked_terminals=[
                a["terminal"]
                for a in self.access
                if not any(c["allowed"] for c in a["candidates"])
            ],
            **result,
        )

    def validate(self, routes):
        self.router.validate(routes)
        for route in routes:
            e = self.edges[route["edge"]]
            if any(s.layer in ("V0", "V1") for s in e.shapes):
                landing = e.u if e.u[0] == 2 else e.v
                if not any(
                    r["net"] == route["net"]
                    and self.edges[r["edge"]].u[0] == self.edges[r["edge"]].v[0] == 2
                    and landing in (self.edges[r["edge"]].u, self.edges[r["edge"]].v)
                    for r in routes
                ):
                    raise ValueError("M1 via landing requires a wire segment")

    def schematic(self):
        vt = self.design["vt"]
        return (
            f".SUBCKT {self.name} vdd vss\n"
            f"MN net7 net12 vss vss nmos_{vt} W={self.design['n_width_nm'] / 1000:g}u L=0.014u M=1\n"
            f"MP net12 net7 vdd vdd pmos_{vt} W={self.design['p_width_nm'] / 1000:g}u L=0.014u M=1\n"
            f".ENDS {self.name}\n"
        )
