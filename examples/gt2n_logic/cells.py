"""Complementary static-CMOS topologies, independent of reference cell layouts."""

from dataclasses import dataclass
from itertools import product

LIBRARY_NAME = "gt2_cpcell_logic_w31_lvt"


@dataclass(frozen=True)
class Cell:
    name: str
    inputs: tuple[str, ...]
    # Each device is (name, drain, gate, source, model). Bodies follow polarity.
    devices: tuple[tuple[str, str, str, str, str], ...]
    function: str
    inverting: bool = True

    @property
    def physical_name(self):
        return f"gt2_6t_{self.name.lower()}_w31_lvt"

    def expected(self, values):
        a = dict(zip(self.inputs, values))
        if self.name in ("NAND4", "AND4", "NAND5", "AND5"):
            positive = all(values)
        elif self.name in ("NOR4", "OR4", "NOR5", "OR5"):
            positive = any(values)
        elif self.name in ("AOI221", "AO221"):
            positive = (a["A1"] and a["A2"]) or (a["B1"] and a["B2"]) or a["C"]
        elif self.name in ("OAI221", "OA221"):
            positive = (a["A1"] or a["A2"]) and (a["B1"] or a["B2"]) and a["C"]
        elif self.name in ("AOI222", "AO222"):
            positive = (
                (a["A1"] and a["A2"]) or (a["B1"] and a["B2"]) or (a["C1"] and a["C2"])
            )
        elif self.name in ("OAI222", "OA222"):
            positive = (
                (a["A1"] or a["A2"]) and (a["B1"] or a["B2"]) and (a["C1"] or a["C2"])
            )
        elif self.name in ("MAJ3", "MAJI3"):
            positive = sum(values) >= 2
        else:
            raise ValueError(self.name)
        return int(not positive) if self.inverting else int(positive)

    @property
    def gate_nets(self):
        return tuple(dict.fromkeys(d[2] for d in self.devices))

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

        internal = tuple(g for g in self.gate_nets if g not in self.inputs)
        for values in product((0, 1), repeat=len(self.inputs)):
            stable = []
            # Enumerate internally driven gate nets, requiring a unique,
            # rail-driven, self-consistent state. No Boolean value is supplied
            # for the first stage of a buffered cell: connectivity determines it.
            for levels in product((0, 1), repeat=len(internal)):
                signals = dict(zip(self.inputs, values)) | dict(zip(internal, levels))
                graph = nx.Graph()
                graph.add_nodes_from(("Y", "VDD", "VSS", *internal))
                for _, d, g, s, model in self.devices:
                    if signals[g] == (1 if model == "nmos" else 0):
                        graph.add_edge(d, s)
                if nx.has_path(graph, "VDD", "VSS"):
                    continue
                driven = {}
                for net in ("Y", *internal):
                    high = nx.has_path(graph, net, "VDD")
                    low = nx.has_path(graph, net, "VSS")
                    if high == low:
                        break
                    driven[net] = int(high)
                else:
                    if all(driven[net] == signals[net] for net in internal):
                        stable.append(driven["Y"])
            if stable != [self.expected(values)]:
                raise ValueError(
                    f"Invalid {self.name} topology at {dict(zip(self.inputs, values))}"
                )
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
    """The complete cell catalog, keyed by logical name in alphabetical order."""
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
    result = {
        c.name: c
        for c in (
            Cell("NAND4", four, tuple(nand), "!(A & B & C & D)"),
            Cell("NOR4", four, tuple(nor), "!(A | B | C | D)"),
            Cell("AOI221", five, tuple(aoi), "!((A1 & A2) | (B1 & B2) | C)"),
            Cell("OAI221", five, tuple(oai), "!((A1 | A2) & (B1 | B2) & C)"),
        )
    }
    five_inputs = ("A", "B", "C", "D", "E")
    six_inputs = ("A1", "A2", "B1", "B2", "C1", "C2")
    result["NAND5"] = Cell(
        "NAND5",
        five_inputs,
        tuple(
            series(five_inputs, "Y", "VSS", "N", "nmos")
            + parallel(five_inputs, "Y", "VDD", "P", "pmos")
        ),
        "!(A & B & C & D & E)",
    )
    result["NOR5"] = Cell(
        "NOR5",
        five_inputs,
        tuple(
            parallel(five_inputs, "Y", "VSS", "N", "nmos")
            + series(five_inputs, "Y", "VDD", "P", "pmos")
        ),
        "!(A | B | C | D | E)",
    )
    aoi222, oai222 = [], []
    for i, group in enumerate(("A", "B", "C")):
        gates = (group + "1", group + "2")
        p_start, p_end = (
            ("Y" if i == 0 else f"P{i}"),
            ("VDD" if i == 2 else f"P{i + 1}"),
        )
        n_start, n_end = (
            ("Y" if i == 0 else f"N{i}"),
            ("VSS" if i == 2 else f"N{i + 1}"),
        )
        aoi222 += series(gates, "Y", "VSS", "N" + group, "nmos")
        aoi222 += parallel(gates, p_start, p_end, "P" + group, "pmos")
        oai222 += parallel(gates, n_start, n_end, "N" + group, "nmos")
        oai222 += series(gates, "Y", "VDD", "P" + group, "pmos")
    result["AOI222"] = Cell(
        "AOI222", six_inputs, tuple(aoi222), "!((A1 & A2) | (B1 & B2) | (C1 & C2))"
    )
    result["OAI222"] = Cell(
        "OAI222", six_inputs, tuple(oai222), "!((A1 | A2) & (B1 | B2) & (C1 | C2))"
    )
    # Factored majority: AB + C(A+B). The dual pull-up network implements
    # (A'+B') (C'+A'B'), using five devices per polarity. A and B each drive
    # two devices in each row; these are distinct physical gate columns.
    majority = (
        series(("A", "B"), "Y", "VSS", "NAB", "nmos")
        + [("MNC", "Y", "C", "N1", "nmos")]
        + parallel(("A", "B"), "N1", "VSS", "NP", "nmos")
        + parallel(("A", "B"), "Y", "P1", "PP", "pmos")
        + [("MPC", "P1", "C", "VDD", "pmos")]
        + series(("A", "B"), "P1", "VDD", "PAB", "pmos")
    )
    result["MAJI3"] = Cell(
        "MAJI3", ("A", "B", "C"), tuple(majority), "!((A & B) | (A & C) | (B & C))"
    )
    for source, name in (
        ("NAND4", "AND4"),
        ("NOR4", "OR4"),
        ("AOI221", "AO221"),
        ("OAI221", "OA221"),
        ("NAND5", "AND5"),
        ("NOR5", "OR5"),
        ("AOI222", "AO222"),
        ("OAI222", "OA222"),
        ("MAJI3", "MAJ3"),
    ):
        first = result[source]
        devices = tuple(
            (
                instance,
                "Z" if drain == "Y" else drain,
                gate,
                "Z" if source == "Y" else source,
                model,
            )
            for instance, drain, gate, source, model in first.devices
        ) + (("MINV_N", "Y", "Z", "VSS", "nmos"), ("MINV_P", "Y", "Z", "VDD", "pmos"))
        result[name] = Cell(
            name, first.inputs, devices, first.function[1:], inverting=False
        )
    return dict(sorted(result.items()))


def library_name(selected):
    """A stable library identifier, with distinct names for selected subsets."""
    names = {cell.name for cell in selected}
    if names == set(cells()):
        return LIBRARY_NAME
    return LIBRARY_NAME + "_" + "_".join(sorted(name.lower() for name in names))


def catalog_entries(selected):
    return {
        cell.name: {
            "physical_name": cell.physical_name,
            "inputs": list(cell.inputs),
            "output": "Y",
            "function": cell.function,
            "transistors": len(cell.devices),
        }
        for cell in selected
    }
