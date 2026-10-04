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
    if isinstance(spacing, dict):
        if dx <= 0 and dy <= 0:
            return True
        if dy <= 0:
            return dx < spacing["horizontal"]
        if dx <= 0:
            return dy < spacing["vertical"]
        return dx * dx + dy * dy < spacing["corner"] ** 2
    return (dx <= 0 and dy <= 0) or (dx < spacing and dy < spacing)


class BandRouter:
    def __init__(self, edges, terminals, ports, spacing=None, *, matching=None):
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
        self.matching = matching or {}
        self._matching_constraints()
        self.model.Minimize(
            sum(
                edge.cost * self.used[net, i]
                for (net, i) in self.used
                for edge in [self.edges[i]]
            )
        )

    def _matching_constraints(self):
        """Hard geometric mirror and per-layer length/via-count constraints.

        Coordinates use the adapter's integer units. Matching physical route
        lengths is a geometric proxy, not a claim of equal extracted RC.
        """
        self.mirror_edges = []
        self.balance_terms = []
        if set(self.matching) - {
            "mirror_x",
            "mirror_nets",
            "mirror_contact_nets",
            "balanced_nets",
        }:
            raise ValueError("Unknown routing matching option")
        pairs = [(a, b, False) for a, b in self.matching.get("mirror_nets", [])]
        pairs += [(a, b, True) for a, b in self.matching.get("mirror_contact_nets", [])]
        axis = self.matching.get("mirror_x")
        if pairs and type(axis) is not int:
            raise ValueError("mirror_x must be an integer coordinate")

        def signature(edge, reflect=False):
            shapes = []
            for s in edge.shapes:
                x0, y0, x1, y1 = s.box
                shapes.append(
                    (
                        s.layer,
                        (2 * axis - x1, y0, 2 * axis - x0, y1) if reflect else s.box,
                    )
                )
            return tuple(sorted(shapes))

        for a, b, contacts_only in pairs:
            if a not in self.nets or b not in self.nets:
                raise ValueError("Matched nets must exist")
            lookup = {}

            def eligible(edge):
                return not contacts_only or any(
                    s.layer in ("VG", "VSD") for s in edge.shapes
                )

            for i, edge in enumerate(self.edges):
                if (b, i) in self.used and eligible(edge):
                    key = signature(edge)
                    if key in lookup:
                        raise ValueError("Ambiguous mirrored route geometry")
                    lookup[key] = i
            mapped = set()
            for i, edge in enumerate(self.edges):
                if (a, i) not in self.used or not eligible(edge):
                    continue
                j = lookup.get(signature(edge, True))
                self.model.Add(self.used[a, i] == (0 if j is None else self.used[b, j]))
                self.mirror_edges.append(((a, i), None if j is None else (b, j)))
                if j is not None:
                    mapped.add(j)
            for j in set(lookup.values()) - mapped:
                self.model.Add(self.used[b, j] == 0)
                self.mirror_edges.append(((b, j), None))
        for a, b in self.matching.get("balanced_nets", []):
            if a not in self.nets or b not in self.nets or a == b:
                raise ValueError("Balanced nets must be distinct existing nets")
            metrics = sorted(set().union(*(self.edge_metrics(e) for e in self.edges)))
            for metric in metrics:
                terms = {
                    net: [
                        (i, self.edge_metrics(edge).get(metric, 0))
                        for i, edge in enumerate(self.edges)
                        if (net, i) in self.used
                    ]
                    for net in (a, b)
                }
                self.model.Add(
                    sum(weight * self.used[a, i] for i, weight in terms[a])
                    == sum(weight * self.used[b, i] for i, weight in terms[b])
                )
                self.balance_terms.append((a, b, metric, terms))

    @staticmethod
    def edge_metrics(edge):
        metrics = {}
        if edge.u[0] != "T" and edge.v[0] != "T" and edge.u[0] == edge.v[0]:
            layer = edge.shapes[0].layer
            metrics[layer + "_length"] = abs(edge.u[1] - edge.v[1]) + abs(
                edge.u[2] - edge.v[2]
            )
        for shape in edge.shapes:
            if shape.layer in ("VG", "VSD", "V0", "V1", "V2"):
                metrics[shape.layer + "_count"] = (
                    metrics.get(shape.layer + "_count", 0) + 1
                )
        return metrics

    def route_metrics(self, routes):
        result = {net: {} for net in self.nets}
        for route in routes:
            for key, value in self.edge_metrics(self.edges[route["edge"]]).items():
                values = result[route["net"]]
                values[key] = values.get(key, 0) + value
        return result

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
        # Every selected node consumes one unit from the root. Unlike terminal
        # flow alone, this excludes detached cycles used to pad matched lengths.
        active = {}
        reach_in, reach_out = {}, {}
        for node in sorted(nodes - {source}, key=repr):
            active[node] = self.model.NewBoolVar(f"reached_{net}_{node}")
            self.model.AddMaxEquality(active[node], incident.get(node, [0]))
        for i, edge in enumerate(self.edges):
            if (net, i) not in self.used:
                continue
            for u, v, suffix in ((edge.u, edge.v, "f"), (edge.v, edge.u, "b")):
                flow = self.model.NewIntVar(0, len(nodes), f"reach_{net}_{i}_{suffix}")
                self.model.Add(flow <= len(nodes) * self.used[net, i])
                reach_out.setdefault(u, []).append(flow)
                reach_in.setdefault(v, []).append(flow)
        for node in sorted(nodes, key=repr):
            demand = -sum(active.values()) if node == source else active[node]
            self.model.Add(
                sum(reach_in.get(node, [])) - sum(reach_out.get(node, [])) == demand
            )

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
            "route_metrics": self.route_metrics(routes),
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
        for a, b in self.mirror_edges:
            if (a in seen) != (b is not None and b in seen):
                raise ValueError("Route violates mirror matching")
        for a, b, metric, terms in self.balance_terms:
            totals = [
                sum(w for i, w in terms[net] if (net, i) in seen) for net in (a, b)
            ]
            if totals[0] != totals[1]:
                raise ValueError(f"Route violates {metric} matching for {a}/{b}")
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
