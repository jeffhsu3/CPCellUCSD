"""CP-SAT routing for fixed device bands and explicit conductor access sites.

This is independent of the single-height FinFET model. A technology adapter
supplies physical conductor terminals, candidate metal/via geometries and ports.
All geometry uses integer half-nanometre units. No routed wires are prescribed.
"""

from dataclasses import dataclass
from itertools import combinations

import networkx as nx
from ortools.sat.python import cp_model


@dataclass(frozen=True)
class Shape:
    layer: str
    box: tuple[int, int, int, int]


@dataclass(frozen=True)
class RouteEdge:
    u: tuple
    v: tuple
    shapes: tuple[Shape, ...]
    cost: int
    owner: str | None = None


def close_boxes(a, b, spacing):
    """Whether rectangles overlap/touch or violate a positive edge spacing."""
    dx = max(a[0] - b[2], b[0] - a[2])
    dy = max(a[1] - b[3], b[1] - a[3])
    return (dx <= 0 and dy <= 0) or (dx < spacing and dy < spacing)


class BandRouter:
    def __init__(self, edges, terminals, ports, spacing=None):
        self.edges = list(edges)
        self.terminals = terminals
        self.ports = ports
        self.spacing = {"M0": 28, "M1": 28, "V0": 28} if spacing is None else spacing
        self.nets = sorted(terminals)
        if not self.nets or any(not nodes for nodes in terminals.values()):
            raise ValueError("Every routed net needs an intrinsic conductor terminal")
        if set(ports) - set(terminals):
            raise ValueError("Boundary ports must name existing signal nets")
        self.model = cp_model.CpModel()
        self.used = {}
        self.node_usage = {}
        for net in self.nets:
            self._net_constraints(net)
        for usage in self.node_usage.values():
            self.model.AddAtMostOne(usage)
        self._geometry_constraints()
        self.model.Minimize(
            sum(
                edge.cost * self.used[net, i]
                for (net, i) in self.used
                for edge in [self.edges[i]]
            )
        )

    def _net_constraints(self, net):
        source = self.terminals[net][0]
        sinks = set(self.terminals[net][1:]) | set(self.ports.get(net, []))
        sinks.discard(source)
        if not sinks:
            raise ValueError(f"Net {net} has nothing to connect")
        incoming, outgoing, incident = {}, {}, {}
        for i, edge in enumerate(self.edges):
            if edge.owner not in (None, net):
                continue
            used = self.model.NewBoolVar(f"wire_{net}_{i}")
            self.used[net, i] = used
            forward = self.model.NewIntVar(0, len(sinks), f"flow_{net}_{i}_f")
            backward = self.model.NewIntVar(0, len(sinks), f"flow_{net}_{i}_b")
            self.model.Add(forward + backward <= len(sinks) * used)
            self.model.Add(forward + backward >= used)
            for u, v, flow in ((edge.u, edge.v, forward), (edge.v, edge.u, backward)):
                outgoing.setdefault(u, []).append(flow)
                incoming.setdefault(v, []).append(flow)
            for node in (edge.u, edge.v):
                incident.setdefault(node, []).append(used)
        nodes = set(incident) | sinks | {source}
        for node in sorted(nodes, key=repr):
            demand = len(sinks) if node == source else -int(node in sinks)
            self.model.Add(
                sum(outgoing.get(node, [])) - sum(incoming.get(node, [])) == demand
            )
            if node[0] != "T":
                occupied = self.model.NewBoolVar(f"node_{net}_{node}")
                self.model.AddMaxEquality(occupied, incident.get(node, [0]))
                self.node_usage.setdefault(node, []).append(occupied)
        # One contact per intrinsic conductor. Shared gates/diffusions are
        # already represented by a single terminal, not independent fake pins.
        for node in self.terminals[net]:
            self.model.Add(sum(incident.get(node, [])) == 1)

    def _geometry_constraints(self):
        # End extensions and via landings participate in conflicts, even when
        # abstract graph nodes differ. Crossing M0/M1 is allowed without a via.
        conflicts = set()
        for i, a in enumerate(self.edges):
            for j in range(i, len(self.edges)):
                b = self.edges[j]
                if any(
                    sa.layer == sb.layer
                    and close_boxes(sa.box, sb.box, self.spacing.get(sa.layer, 0))
                    for sa in a.shapes
                    for sb in b.shapes
                ):
                    conflicts.add((i, j))
        for net, other in combinations(self.nets, 2):
            for i, j in sorted(conflicts):
                for a, b in sorted({(i, j), (j, i)}):
                    if (net, a) in self.used and (other, b) in self.used:
                        self.model.Add(self.used[net, a] + self.used[other, b] <= 1)

    def solve(self, seconds=30):
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = seconds
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 32
        status = solver.Solve(self.model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise ValueError(f"Fixed-band routing failed: {solver.StatusName(status)}")
        routes = [
            {"net": net, "edge": i}
            for (net, i), var in self.used.items()
            if solver.Value(var)
        ]
        self.validate(routes)
        return {
            "status": solver.StatusName(status),
            "objective": solver.ObjectiveValue(),
            "wall_time": solver.WallTime(),
            "routes": routes,
        }

    def validate(self, routes):
        """Independently verify connectivity and geometry before GDS export."""
        chosen = {}
        seen = set()
        for route in routes:
            net, i = route["net"], route["edge"]
            if type(i) is not int or (net, i) not in self.used or (net, i) in seen:
                raise ValueError("Invalid or duplicated routing edge")
            seen.add((net, i))
            chosen.setdefault(net, []).append(self.edges[i])
        occupied = {}
        for net in self.nets:
            graph = nx.Graph()
            for edge in chosen.get(net, []):
                graph.add_edge(edge.u, edge.v)
                for node in (edge.u, edge.v):
                    if node[0] != "T" and occupied.setdefault(node, net) != net:
                        raise ValueError("Different nets share a routing node")
            required = set(self.terminals[net]) | set(self.ports.get(net, []))
            if not required <= set(graph) or not nx.is_connected(graph):
                raise ValueError(f"Disconnected route for {net}")
            if any(graph.degree(node) != 1 for node in self.terminals[net]):
                raise ValueError(f"Conductor {net} must use exactly one contact")
        for net, other in combinations(self.nets, 2):
            for a in chosen[net]:
                for b in chosen[other]:
                    if any(
                        sa.layer == sb.layer
                        and close_boxes(sa.box, sb.box, self.spacing.get(sa.layer, 0))
                        for sa in a.shapes
                        for sb in b.shapes
                    ):
                        raise ValueError(
                            f"Routing geometry conflict between {net} and {other}"
                        )
