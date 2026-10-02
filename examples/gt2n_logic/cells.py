"""Complementary static-CMOS topologies, independent of reference cell layouts."""

from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True)
class Cell:
    name: str
    inputs: tuple[str, ...]
    # Each device is (name, drain, gate, source, model). Bodies follow polarity.
    devices: tuple[tuple[str, str, str, str, str], ...]
    function: str

    @property
    def physical_name(self):
        return f"gt2_6t_{self.name.lower()}_w31_lvt"

    def expected(self, values):
        a = dict(zip(self.inputs, values))
        if self.name == "NAND4":
            return int(not all(values))
        if self.name == "NOR4":
            return int(not any(values))
        if self.name == "AOI221":
            return int(not ((a["A1"] and a["A2"]) or (a["B1"] and a["B2"]) or a["C"]))
        if self.name == "OAI221":
            return int(not ((a["A1"] or a["A2"]) and (a["B1"] or a["B2"]) and a["C"]))
        raise ValueError(self.name)

    def cdl(self, physical=False):
        name = self.physical_name if physical else self.name
        supply = ("vdd", "vss") if physical else ("VDD", "VSS")
        lines = [f".SUBCKT {name} {' '.join(self.inputs)} Y {' '.join(supply)}"]
        for instance, drain, gate, source, model in self.devices:
            bulk = "VSS" if model == "nmos" else "VDD"
            nets = [
                n.lower() if physical and n in ("VDD", "VSS") else n
                for n in (drain, gate, source, bulk)
            ]
            model_name = f"{model}_lvt" if physical else model
            sizing = "W=0.031u L=0.014u M=1" if physical else "w=46n l=14n nfin=2"
            lines.append(f"{instance} {' '.join(nets)} {model_name} {sizing}")
        return "\n".join(lines + [f".ENDS {name}", ""])

    def check_topology(self):
        """Exhaustively compare switch-network connectivity to the Boolean spec."""
        import networkx as nx

        for values in product((0, 1), repeat=len(self.inputs)):
            signals = dict(zip(self.inputs, values))
            graph = nx.Graph()
            graph.add_nodes_from(("Y", "VDD", "VSS"))
            for _, d, g, s, model in self.devices:
                if signals[g] == (1 if model == "nmos" else 0):
                    graph.add_edge(d, s)
            high = nx.has_path(graph, "Y", "VDD")
            low = nx.has_path(graph, "Y", "VSS")
            if high == low or high != bool(self.expected(values)):
                raise ValueError(f"Invalid {self.name} topology at {signals}")
        return 2 ** len(self.inputs)


def series(gates, start, end, prefix, model):
    nodes = [start] + [f"{prefix}{i}" for i in range(1, len(gates))] + [end]
    return [
        (f"M{prefix}{i}", nodes[i], gate, nodes[i + 1], model)
        for i, gate in enumerate(gates)
    ]


def parallel(gates, start, end, prefix, model):
    return [(f"M{prefix}{i}", start, gate, end, model) for i, gate in enumerate(gates)]


def cells():
    four = ("A", "B", "C", "D")
    five = ("A1", "A2", "B1", "B2", "C")
    nand = series(four, "Y", "VSS", "N", "nmos") + parallel(
        four, "Y", "VDD", "P", "pmos"
    )
    nor = parallel(four, "Y", "VSS", "N", "nmos") + series(
        four, "Y", "VDD", "P", "pmos"
    )
    aoi = (
        series(five[:2], "Y", "VSS", "NA", "nmos")
        + series(five[2:4], "Y", "VSS", "NB", "nmos")
        + [("MNC", "Y", "C", "VSS", "nmos")]
        + parallel(five[:2], "Y", "P1", "PA", "pmos")
        + parallel(five[2:4], "P1", "P2", "PB", "pmos")
        + [("MPC", "P2", "C", "VDD", "pmos")]
    )
    oai = (
        parallel(five[:2], "Y", "N1", "NA", "nmos")
        + parallel(five[2:4], "N1", "N2", "NB", "nmos")
        + [("MNC", "N2", "C", "VSS", "nmos")]
        + series(five[:2], "Y", "VDD", "PA", "pmos")
        + series(five[2:4], "Y", "VDD", "PB", "pmos")
        + [("MPC", "Y", "C", "VDD", "pmos")]
    )
    return {
        c.name: c
        for c in (
            Cell("NAND4", four, tuple(nand), "!(A & B & C & D)"),
            Cell("NOR4", four, tuple(nor), "!(A | B | C | D)"),
            Cell("AOI221", five, tuple(aoi), "!((A1 & A2) | (B1 & B2) | C)"),
            Cell("OAI221", five, tuple(oai), "!((A1 | A2) & (B1 | B2) & C)"),
        )
    }
