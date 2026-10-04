"""
Via-related design rules for FinFET layout optimization.
This module contains constraints for via induction and via-metal connectivity.
"""

from absl import logging as absl_logging


def via_induce_vertical_metal(finfet, supervia_params):
    """
    At layer M1, if a node is connected to a via, it must be connected to the metal layer.

    Args:
        finfet: The FinFET instance
        supervia_params: Dictionary of supervia parameters per layer
    """
    finfet.opt.log_comment("At layer M1, if a node is connected to a via, it must be connected to the metal layer ...")
    # NOTE: SMTCell implements this at flow level
    for u in finfet.lgg.nodes_in_layer("M1"):
        if supervia_params["M1"]:
            # if the layer is a supervia, then we need to skip this layer
            continue
        # get the down neighbor
        layer_idx = u[0]
        row = u[1]
        col = u[2]
        u_d = (layer_idx - 1, row, col)
        if not finfet.lgg.is_node_in_graph(u_d):
            continue
        via_edge = finfet.edge_vars[(u_d, u)]
        metal_edges = []
        # get the front neighbor
        u_f = finfet.lgg.get_front_neighbor(u)
        if u_f is not None:
            metal_edges.append(finfet.edge_vars[(u_f, u)])
        # get the back neighbor
        u_b = finfet.lgg.get_back_neighbor(u)
        if u_b is not None:
            metal_edges.append(finfet.edge_vars[(u, u_b)])

        # Reify the condition that at least one metal edge is active
        # u is (M1_layer_idx, row, col)
        has_metal_connection_var = finfet.opt.NewBoolVar(f"has_metal_conn_M1_R{u[1]}_C{u[2]}")

        if not metal_edges:
            # If there are no possible metal edges connected to u on M1,
            # then has_metal_connection_var must be false.
            finfet.opt.Add(has_metal_connection_var == 0)
        else:
            # has_metal_connection_var is true if OR(metal_edges) is true
            finfet.opt.AddBoolOr(metal_edges).OnlyEnforceIf(has_metal_connection_var)
            # If any metal_edge is true, then has_metal_connection_var must be true
            # (This establishes the other direction of the equivalence: OR(metal_edges) => has_metal_connection_var)
            for metal_edge_var in metal_edges:
                finfet.opt.AddImplication(metal_edge_var, has_metal_connection_var)

        # if the via edge exists, then there must be a metal edge connection at u on M1
        finfet.opt.AddImplication(via_edge, has_metal_connection_var)


def via_induce_horizontal_metal(finfet, supervia_params):
    """
    At layer M0, if a node is connected to a via, it must be connected to the metal layer.

    Args:
        finfet: The FinFET instance
        supervia_params: Dictionary of supervia parameters per layer
    """
    finfet.opt.log_comment("At layer M0, if a node is connected to a via, it must be connected to the metal layer ...")
    # NOTE: SMTCell implements this at flow level
    for u in finfet.lgg.nodes_in_layer("M0"):
        if supervia_params["M0"]:
            # if the layer is a supervia, then we need to skip this layer
            continue
        # get the down neighbor
        layer_idx = u[0]
        row = u[1]
        col = u[2]
        u_d = (layer_idx - 1, row, col)
        if not finfet.lgg.is_node_in_graph(u_d):
            continue
        via_edge = finfet.edge_vars[(u_d, u)]
        metal_edges = []
        # get the left neighbor
        u_l = finfet.lgg.get_left_neighbor(u)
        if u_l is not None:
            metal_edges.append(finfet.edge_vars[(u_l, u)])
        # get the right neighbor
        u_r = finfet.lgg.get_right_neighbor(u)
        if u_r is not None:
            metal_edges.append(finfet.edge_vars[(u, u_r)])
        # Reify the condition that at least one metal edge is active
        # u is (M0_layer_idx, row, col)
        has_metal_connection_var = finfet.opt.NewBoolVar(f"has_metal_conn_M0_R{u[1]}_C{u[2]}")
        if not metal_edges:
            # If there are no possible metal edges connected to u on M0,
            # then has_metal_connection_var must be false.
            finfet.opt.Add(has_metal_connection_var == 0)
        else:
            # has_metal_connection_var is true if OR(metal_edges) is true
            finfet.opt.AddBoolOr(metal_edges).OnlyEnforceIf(has_metal_connection_var)
            # If any metal_edge is true, then has_metal_connection_var must be true
            # (This establishes the other direction of the equivalence: OR(metal_edges) => has_metal_connection_var)
            for metal_edge_var in metal_edges:
                finfet.opt.AddImplication(metal_edge_var, has_metal_connection_var)
        # if the via edge exists, then there must be a metal edge connection at u on M0
        finfet.opt.AddImplication(via_edge, has_metal_connection_var)


def _gather_bottom_via_between_nodes(finfet, layer_idx, u_1, u_2):
    """
    Helper function to gather via edges between two nodes u_1 and u_2 on a specific layer.
    Returns a list of via edges if they exist, otherwise returns an empty list.

    Args:
        finfet: The FinFET instance
        layer_idx: The layer index
        u_1: First node tuple (layer_idx, row, col)
        u_2: Second node tuple (layer_idx, row, col)

    Returns:
        List of via edge variables
    """
    via_edges = []
    assert finfet.lgg.is_node_in_graph(u_1), f"Node {u_1} does not exist in the graph"
    assert finfet.lgg.is_node_in_graph(u_2), f"Node {u_2} does not exist in the graph"
    assert layer_idx == u_1[0] == u_2[0], f"Layer index mismatch: {layer_idx} != {u_1[0]} or {u_2[0]}"

    if finfet.lgg.layer_to_direction[finfet.lgg.idx_to_layer[layer_idx]] == "V":
        assert u_1[2] == u_2[2], f"Nodes {u_1} and {u_2} must be in the same column for vertical layers"
        bottom_layer_idx = layer_idx - 1
        if bottom_layer_idx < 0:
            return via_edges
        start_row = min(u_1[1], u_2[1])
        end_row = max(u_1[1], u_2[1])
        col = u_1[2]

        # get the nearest node in the bottom layer
        nearest_node = finfet.lgg.get_nearest_node_in_layer(layer=bottom_layer_idx, row=start_row, col=u_1[2])
        assert nearest_node is not None, f"No nearest node found in layer {bottom_layer_idx} at column {u_1[2]}"
        # latch on to the nearest node and start iterating through the rows
        current_row = nearest_node[1]
        for row in finfet.lgg.get_layer_rows_starting_from(layer=bottom_layer_idx, row=current_row):
            if not (start_row <= row <= end_row):
                continue
            current_bottom_node = (bottom_layer_idx, row, col)
            # check if the current bottom node has a via above
            if finfet.lgg.has_via_above(node=current_bottom_node):
                nn_above = finfet.lgg.get_via_above(node=current_bottom_node)
                via_edge = finfet.edge_vars.get((current_bottom_node, nn_above))
                assert via_edge is not None, f"Via edge between {current_bottom_node} and {nn_above} does not exist"
                via_edges.append(via_edge)

    elif finfet.lgg.layer_to_direction[finfet.lgg.idx_to_layer[layer_idx]] == "H":
        assert u_1[1] == u_2[1], f"Nodes {u_1} and {u_2} must be in the same row for horizontal layers"
        bottom_layer_idx = layer_idx - 1
        if bottom_layer_idx < 0:
            return via_edges
        start_col = min(u_1[2], u_2[2])
        end_col = max(u_1[2], u_2[2])
        row = u_1[1]

        # get the nearest node in the bottom layer
        nearest_node = finfet.lgg.get_nearest_node_in_layer(layer=bottom_layer_idx, row=u_1[1], col=start_col)
        assert nearest_node is not None, f"No nearest node found in layer {bottom_layer_idx} at row {u_1[1]}"
        # latch on to the nearest node and start iterating through the columns
        current_col = nearest_node[2]
        for col in finfet.lgg.get_layer_cols_starting_from(layer=bottom_layer_idx, col=current_col):
            if not (start_col <= col <= end_col):
                continue
            current_bottom_node = (bottom_layer_idx, row, col)
            # check if the current bottom node has a via above
            if finfet.lgg.has_via_above(node=current_bottom_node):
                nn_above = finfet.lgg.get_via_above(node=current_bottom_node)
                via_edge = finfet.edge_vars.get((current_bottom_node, nn_above))
                assert via_edge is not None, f"Via edge between {current_bottom_node} and {nn_above} does not exist"
                via_edges.append(via_edge)
    else:
        raise ValueError(f"Layer {finfet.lgg.idx_to_layer[layer_idx]} is neither horizontal nor vertical")

    return via_edges


def _metal_segments_connected_to_via(finfet, direction):
    """Require a via somewhere on each continuous metal segment.

    Scan each track, carrying via reachability only across selected metal
    edges. At a segment's far end the carry must be true. Vias above or below
    the layer count; an unused candidate or a via across a gap cannot help.
    """
    incident_vias = {}
    for (u, v), edge in finfet.edge_vars.items():
        if u[0] != v[0]:
            incident_vias.setdefault(u, []).append(edge)
            incident_vias.setdefault(v, []).append(edge)

    horizontal = direction == "H"
    for layer, idx in finfet.lgg.layer_to_idx.items():
        if idx == 0 or finfet.lgg.layer_to_direction[layer] != direction:
            continue
        rows = finfet.lgg.rows_in_layer(layer)
        cols = finfet.lgg.cols_in_layer(layer)
        tracks = (
            [[(idx, row, col) for col in cols] for row in rows]
            if horizontal else
            [[(idx, row, col) for row in rows] for col in cols]
        )
        for nodes in tracks:
            previous_reached = None
            for i, u in enumerate(nodes):
                incoming = finfet.edge_vars[(nodes[i - 1], u)] if i else None
                outgoing = finfet.edge_vars[(u, nodes[i + 1])] if i + 1 < len(nodes) else None
                reasons = list(incident_vias.get(u, []))
                if incoming is not None:
                    carry = finfet.opt.NewBoolVar(f"via_carry_{u}")
                    finfet.opt.AddMinEquality(carry, [incoming, previous_reached])
                    reasons.append(carry)
                reached = finfet.opt.NewBoolVar(f"via_reached_{u}")
                if reasons:
                    finfet.opt.AddMaxEquality(reached, reasons)
                else:
                    finfet.opt.Add(reached == 0)
                if incoming is not None:
                    # incoming AND NOT outgoing means this is a segment end.
                    clause = [incoming.Not(), reached]
                    if outgoing is not None:
                        clause.append(outgoing)
                    finfet.opt.AddBoolOr(clause)
                previous_reached = reached


def vertical_metal_must_be_connected_to_via(finfet):
    """Require each vertical metal segment to contact a via."""
    finfet.opt.log_comment("Vertical metal must be connected to a via ...")
    _metal_segments_connected_to_via(finfet, "V")


def horizontal_metal_must_be_connected_to_via(finfet):
    """Require each horizontal metal segment to contact a via."""
    finfet.opt.log_comment("Horizontal metal must be connected to a via ...")
    _metal_segments_connected_to_via(finfet, "H")


def via_separation_rules(finfet_instance, via_params):
    """Exclude pairs of vias strictly inside the minimum Manhattan distance."""
    finfet_instance.opt.log_comment("Enforcing via separation rules (L1 Manhattan distance)...")
    for (layer_1, layer_2), via_dist in via_params.items():
        indices = {finfet_instance.lgg.layer_to_idx[layer_1], finfet_instance.lgg.layer_to_idx[layer_2]}
        candidates = [
            (u, edge) for (u, v), edge in finfet_instance.edge_vars.items()
            if u[0] != v[0] and {u[0], v[0]} == indices
        ]
        for i, (u, edge) in enumerate(candidates):
            for v, other in candidates[i + 1:]:
                if abs(u[1] - v[1]) + abs(u[2] - v[2]) < via_dist:
                    finfet_instance.opt.AddAtMostOne([edge, other])
