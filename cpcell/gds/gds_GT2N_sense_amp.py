"""Export exact fingers and matched routes from the GT2N sense-amp adapter."""

from pathlib import Path

import klayout.db as db
from cpcell.gds.gds_GT2N_SH import LAYERS
from cpcell.tech.gt2n_sense_amp import SenseAmpProblem


def export_sense_amp(solution, filename):
    if solution.get("format") != "cpcell.sense_amp.v1":
        raise ValueError("Expected a CPCell sense-amplifier solution")
    problem = SenseAmpProblem(solution["design"])
    if (solution["width_nm"], solution["height_nm"]) != (problem.width, problem.height):
        raise ValueError("Sense-amplifier dimensions disagree")
    problem.router.validate(solution["routes"])
    layout = db.Layout()
    layout.dbu = 0.0005
    cell = layout.create_cell(problem.name)
    shapes = list(problem.frame)
    for route in solution["routes"]:
        shapes.extend(problem.edges[route["edge"]].shapes)
    for s in shapes:
        cell.shapes(
            layout.layer(LAYERS[s.layer], 250 if s.layer == "BOUNDARY" else 0)
        ).insert(db.Box(*s.box))
    for net, s in problem.pin_boxes.items():
        layer = cell.shapes(layout.layer(LAYERS[s.layer], 251))
        b = db.Box(*s.box)
        layer.insert(b)
        layer.insert(db.Text(net, db.Trans(b.center())))
    for y, net in problem.rails:
        layer = cell.shapes(layout.layer(LAYERS["BPR"], 251))
        layer.insert(db.Box(0, 2 * y - 32, 588, 2 * y + 32))
        layer.insert(db.Text(net, db.Trans(294, 2 * y)))
    for index in layout.layer_indexes():
        if layout.get_info(index).datatype == 0:
            dest = cell.shapes(index)
            merged = db.Region(dest).merged()
            dest.clear()
            dest.insert(merged)
    filename = Path(filename)
    filename.parent.mkdir(parents=True, exist_ok=True)
    layout.write(str(filename))
    return layout
