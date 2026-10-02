"""Validated, technology-independent per-cell placement and port constraints.

Placement columns use the same odd PC indices as ``inject_placement``.
Port tracks are zero-based indices in the requested metal layer's grid.
"""

from cpcell.utility.entity import Model


def _record(value, allowed, required, context):
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    if set(value) - allowed or required - set(value):
        raise ValueError(f"{context}: required fields {sorted(required)}, allowed fields {sorted(allowed)}")


def _integer(value, context):
    if type(value) is not int:
        raise ValueError(f"{context} must be an integer")


def read_options(cell):
    """Validate names, domains and schema before creating routing variables."""
    cell.routing_layers = cell.cell_config.get("routing_layers", {"value": ["M0", "M1", "M2"]})["value"]
    if not isinstance(cell.routing_layers, list) or not cell.routing_layers or any(name not in ("M0", "M1", "M2") for name in cell.routing_layers):
        raise ValueError("routing_layers must be a nonempty list of routing metals")
    cell.contact_rows = cell.cell_config.get("contact_rows", {"value": {}})["value"]
    if not isinstance(cell.contact_rows, dict) or cell.contact_rows.keys() - {"gate", "diffusion"}:
        raise ValueError("contact_rows accepts gate and diffusion row-index lists")
    for rows in cell.contact_rows.values():
        if not isinstance(rows, list) or not rows or any(type(ri) is not int or ri not in cell.pc_ri for ri in rows):
            raise ValueError("contact_rows requires nonempty lists of legal PC row indices")
    cell.boundary_ports = {}
    cell.placement_constraints = cell.cell_config.get("placement_constraints", {"value": []})["value"]
    ports = cell.cell_config.get("boundary_ports", {"value": []})["value"]
    if not isinstance(ports, list) or not isinstance(cell.placement_constraints, list):
        raise ValueError("boundary_ports.value and placement_constraints.value must be lists")
    io_nets = {net.name for net in cell.circuit.get_nets(with_power_ground=False) if net.is_io_net()}
    names = set()
    for port in ports:
        _record(port, {"name", "net", "side", "layer", "track"}, {"name", "net", "side", "layer"}, "boundary port")
        if not isinstance(port["name"], str) or not port["name"] or port["name"] in names:
            raise ValueError("Boundary port names must be nonempty, unique strings")
        names.add(port["name"])
        if not isinstance(port["net"], str) or port["net"] not in io_nets:
            raise ValueError(f"Boundary port {port['name']}: net must be a signal I/O net")
        if port["layer"] not in cell.routing_layers:
            raise ValueError("Boundary port layer must be an enabled routing metal")
        direction = cell.fin_tech.layer_stack.metal_layers[cell.lgg.layer_to_idx[port["layer"]]].direction
        sides = ("left", "right") if direction == "H" else ("bottom", "top")
        if port["side"] not in sides:
            raise ValueError(f"Boundary port {port['name']}: {port['layer']} supports sides {sides}")
        tracks = cell.lgg.rows_in_layer(port["layer"]) if direction == "H" else cell.lgg.cols_in_layer(port["layer"])
        if "track" in port:
            _integer(port["track"], "Boundary port track")
            if not 0 <= port["track"] < len(tracks):
                raise ValueError(f"Boundary port {port['name']}: track outside layer grid")
        cell.boundary_ports.setdefault(port["net"], []).append(port)

    for rule in cell.placement_constraints:
        if not isinstance(rule, dict) or not isinstance(rule.get("type"), str):
            raise ValueError("Placement constraint requires a type")
        kind = rule["type"]
        if kind == "fixed":
            _record(rule, {"type", "transistor", "column", "columns", "row", "flip"}, {"type", "transistor"}, "fixed placement")
            members = [rule["transistor"]]
            if not set(rule) & {"column", "columns", "row", "flip"}:
                raise ValueError("Fixed placement needs column, columns, row or flip")
            if "column" in rule and "columns" in rule:
                raise ValueError("Specify column or columns, not both")
            columns = [rule["column"]] if "column" in rule else rule.get("columns", [])
            if not isinstance(columns, list) or ("columns" in rule and not columns):
                raise ValueError("Placement columns must be a nonempty list")
            for col in columns:
                _integer(col, "Placement column")
                if col not in cell.plc_ci:
                    raise ValueError(f"Placement column {col} outside legal columns {cell.plc_ci}; increase insert_num_db for more space")
            if "flip" in rule and type(rule["flip"]) is not bool:
                raise ValueError("Placement flip must be a boolean")
        elif kind in ("order", "align", "mirror"):
            allowed = {"type", "transistors"}
            if kind == "order":
                allowed.add("min_spacing")
            if kind == "mirror":
                allowed.add("opposite_flip")
            _record(rule, allowed, {"type", "transistors"}, f"{kind} placement")
            members = rule["transistors"]
            if not isinstance(members, list) or len(members) < 2 or (kind == "mirror" and len(members) != 2):
                raise ValueError(f"{kind} requires {'exactly' if kind == 'mirror' else 'at least'} two transistors")
            if kind == "order":
                _integer(rule.get("min_spacing", 1), "min_spacing")
                if rule.get("min_spacing", 1) < 1:
                    raise ValueError("min_spacing must be at least one CPP")
            if kind == "mirror" and type(rule.get("opposite_flip", True)) is not bool:
                raise ValueError("opposite_flip must be a boolean")
        else:
            raise ValueError(f"Unknown placement constraint type: {kind}")
        if any(not isinstance(name, str) or name not in cell.circuit.transistors for name in members):
            raise ValueError(f"Unknown transistor in {kind} constraint; use expanded finger names from the netlist (e.g. MP0S0)")
        if len(set(members)) != len(members):
            raise ValueError("Placement constraint repeats a transistor")
        if "row" in rule:
            _integer(rule["row"], "Placement row")
            tran = cell.circuit.transistors[members[0]]
            rows = cell.pmos_placeable_row_indices if tran.model == Model.PMOS else cell.nmos_placeable_row_indices
            if rule["row"] not in rows:
                raise ValueError(f"Illegal placement row for {tran.name}: expected {rows}")


def apply_placement(cell):
    for rule in cell.placement_constraints:
        kind = rule["type"]
        if kind == "fixed":
            var = cell.transistor_vars[rule["transistor"]]
            if "column" in rule:
                cell.opt.Add(var.x_var == rule["column"])
            if "columns" in rule:
                cell.opt.AddAllowedAssignments([var.x_var], [[col] for col in rule["columns"]])
            if "row" in rule:
                cell.opt.Add(var.y_var == rule["row"])
            if "flip" in rule:
                cell.opt.Add(var.flip_var == int(rule["flip"]))
            continue
        variables = [cell.transistor_vars[name] for name in rule["transistors"]]
        for left, right in zip(variables, variables[1:]):
            if kind == "order":
                cell.opt.Add(right.x_var - left.x_var >= 2 * rule.get("min_spacing", 1))
            elif kind == "align":
                cell.opt.Add(left.x_var == right.x_var)
            else:
                # Gate centres are (x_var + 1)*CPP/2, cell width is
                # (cpp_cost + 3)*CPP/2. Mirror around the solved cell centre.
                cell.opt.Add(left.x_var + right.x_var == cell.cpp_cost + 1)
                if rule.get("opposite_flip", True):
                    cell.opt.Add(left.flip_var + right.flip_var == 1)


def port_candidates(cell, port):
    """Return legal grid attachment nodes; physical edge stubs are exported."""
    layer, side = port["layer"], port["side"]
    rows, cols = cell.lgg.rows_in_layer(layer), cell.lgg.cols_in_layer(layer)
    horizontal = side in ("left", "right")
    tracks = rows if horizontal else cols
    coordinate = tracks[port["track"]] if "track" in port else None
    candidates = []
    for node in cell.lgg.nodes_in_layer(layer):
        if coordinate is not None and node[1 if horizontal else 2] != coordinate:
            continue
        if side == "left" and node[2] != min(cols):
            continue
        if side == "bottom" and node[1] != min(rows):
            continue
        if side == "top" and node[1] != max(rows):
            continue
        if side == "right" and not right_edge_widths(cell, port, node):
            continue
        candidates.append(node)
    if not candidates:
        raise ValueError(f"Boundary port {port['name']} has no legal attachment nodes")
    return candidates


def right_edge_widths(cell, port, node):
    cols = cell.lgg.cols_in_layer(port["layer"])
    pitch = cell.fin_tech.get_pitch("PC")
    return [cpp for cpp in cell.plc_ci if node[2] == max(col for col in cols if col <= (cpp + 2) * pitch)]


def solved_ports(cell):
    result = []
    for net, ports in cell.boundary_ports.items():
        start = cell.circuit.nets[net].num_terminals()
        for i, port in enumerate(ports):
            node = next(node for node, var in cell.node_is_SON_vars[net][start + i].items() if cell.solver.Value(var))
            result.append({**port, "row": node[1], "col": node[2]})
    return result
