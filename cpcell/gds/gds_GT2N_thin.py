"""Export a validated CPCell fixed-band solution on the GT2N thin profile."""

import argparse
import json
from pathlib import Path

import klayout.db as db

from cpcell.gds.gds_GT2N_SH import LAYERS
from cpcell.tech.gt2n_thin import ThinProblem


def export_thin(solution, filename):
    if solution.get("format") != "cpcell.fixed_bands.v1":
        raise ValueError("Expected a CPCell fixed-band routing solution")
    problem = ThinProblem(solution["design"])
    if (solution["width_nm"], solution["height_nm"]) != (problem.width, problem.height):
        raise ValueError("Solution dimensions disagree with its device bands")
    problem.router.validate(solution["routes"])
    layout = db.Layout()
    layout.dbu = 0.0005
    cell = layout.create_cell(problem.name)
    for shape in problem.frame:
        datatype = 250 if shape.layer == "BOUNDARY" else 0
        cell.shapes(layout.layer(LAYERS[shape.layer], datatype)).insert(
            db.Box(*shape.box)
        )
    for y, net in problem.rails:
        shapes = cell.shapes(layout.layer(LAYERS["BPR"], 251))
        shapes.insert(db.Box(0, int(2 * y - 32), 168, int(2 * y + 32)))
        shapes.insert(db.Text(net, db.Trans(84, int(2 * y))))
    pin_metals = {}
    for route in solution["routes"]:
        for shape in problem.edges[route["edge"]].shapes:
            rectangle = db.Box(*shape.box)
            cell.shapes(layout.layer(LAYERS[shape.layer], 0)).insert(rectangle)
            if any(
                p["net"] == route["net"] and p["layer"] == shape.layer
                for p in problem.port_records
            ):
                pin_metals.setdefault((route["net"], shape.layer), db.Region()).insert(
                    rectangle
                )
    boundary = db.Region(db.Box(0, 0, 168, 2 * problem.height))
    for (net, layer), metal in pin_metals.items():
        pins = (metal & boundary).merged()
        shapes = cell.shapes(layout.layer(LAYERS[layer], 251))
        shapes.insert(pins)
        shapes.insert(db.Text(net, db.Trans(pins.bbox().center())))
    for index in layout.layer_indexes():
        info = layout.get_info(index)
        if info.datatype == 0:
            shapes = cell.shapes(index)
            merged = db.Region(shapes).merged()
            shapes.clear()
            shapes.insert(merged)
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    layout.write(str(path))
    return layout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-file", type=Path, required=True)
    parser.add_argument("--gds-file", type=Path, required=True)
    args = parser.parse_args()
    export_thin(json.loads(args.result_file.read_text()), args.gds_file)


if __name__ == "__main__":
    main()
