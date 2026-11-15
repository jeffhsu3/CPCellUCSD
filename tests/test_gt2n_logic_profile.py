"""The core profile enforces physical rules without example-side patches."""

from copy import deepcopy
import json
from pathlib import Path

import klayout.db as db
from ortools.sat.python import cp_model
import pytest

from cpcell.core.finfet import FinFET
from cpcell.gds.gds_GT2N_SH import GT2NLayout
from cpcell.tech.gt2n_logic import prepare_config
from cpcell.tech.tech import FinFET_Tech
from cpcell.utility import config
from cpcell.utility.entity import LayerStack
from cpcell.utility.util import read_cdl_file


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def inputs(tmp_path):
    config.init()
    cdl = tmp_path / "inv.cdl"
    cdl.write_text(
        ".SUBCKT INV A Y VDD VSS\n"
        "MN Y A VSS VSS nmos w=46n l=14n nfin=2\n"
        "MP Y A VDD VDD pmos w=46n l=14n nfin=2\n"
        ".ENDS INV\n"
    )
    circuit = read_cdl_file(cdl)[0]
    tech = FinFET_Tech(
        "test", 2, 4, 46, LayerStack(str(ROOT / "examples/gt2n_logic/logic.layer"))
    )
    config.generate_config(4, "FinFET", "SH", ["INV"], tmp_path, layout_profile="gt2n_logic")
    path = tmp_path / "config/INV.json"
    return circuit, tech, path, json.loads(path.read_text())


def test_profile_defaults_solve_and_export_without_example_patches(inputs, tmp_path):
    circuit, tech, path, settings = inputs
    settings["max_time"] = {"value": True, "time": 10}
    path.write_text(json.dumps(settings))
    output = tmp_path / "solve"
    cell = FinFET(circuit, tech, cell_config=path, output_dir=output)
    assert cell.cell_config["placement_constraints"]["value"] == []
    n, p = (cell.transistor_vars[name].x_var for name in ("MNS0", "MPS0"))
    assert cell.solver.Value(n) == cell.solver.Value(p)
    for width in (13, 31):
        exported = GT2NLayout(
            output / "result/INV.res", "INV", tmp_path / f"w{width}.gds",
            nanosheet_width=width,
        )
        act = db.Region(exported.cell.shapes(exported.layout.layer(2, 0)))
        vg = db.Region(exported.cell.shapes(exported.layout.layer(12, 0)))
        assert not vg.is_empty()
        assert (vg.sized(0, 12, 2) & act).is_empty()
    cell.opt.Add(n != p)
    assert cp_model.CpSolver().Solve(cell.opt) == cp_model.INFEASIBLE


def test_core_adds_missing_rules_and_preserves_stricter_constraints(inputs):
    circuit, tech, _, settings = inputs
    settings["contact_rows"]["value"] = {"diffusion": [1, 3]}
    settings["eol_c2c_rule"]["value"] = {"M0": 100, "M1": 0, "M2": 0}
    settings["mar_c2c_rule"]["value"] = {"M0": 0, "M1": 144, "M2": 0}
    settings["routing_layers"]["value"] = ["M0", "M1"]
    settings["placement_constraints"]["value"] = [
        {"type": "fixed", "transistor": "MNS0", "column": 1}
    ]
    before = deepcopy(settings)
    result = prepare_config(settings, tech, circuit)
    assert settings == before
    assert result["contact_rows"]["value"] == {"gate": [2], "diffusion": [1, 3]}
    assert result["eol_c2c_rule"]["value"] == {"M0": 100, "M1": 80, "M2": 82}
    assert result["mar_c2c_rule"]["value"]["M1"] == 144
    for key in ("routing_layers", "placement_constraints", "insert_num_db"):
        assert result[key] == settings[key]


@pytest.mark.parametrize(
    "key,value,match",
    [
        ("contact_rows", {"gate": [0, 2]}, "gate contact_rows"),
        ("contact_rows", {"diffusion": [2]}, "diffusion contact_rows"),
        ("contact_rows", {"gate": []}, "gate contact_rows"),
        ("supervia", ["M1"], "via-only M1"),
        ("eol_c2c_rule", {"M0": float("nan")}, "finite and nonnegative"),
    ],
)
def test_core_rejects_incompatible_overrides_before_model_build(
    inputs, tmp_path, key, value, match
):
    circuit, tech, path, settings = inputs
    settings[key]["value"] = value
    path.write_text(json.dumps(settings))
    output = tmp_path / "solve"
    with pytest.raises(ValueError, match=match):
        FinFET(circuit, tech, cell_config=path, output_dir=output)
    assert not output.exists()


def test_profile_rejects_grid_and_topology_it_cannot_export(inputs):
    circuit, tech, _, settings = inputs
    tech.layer_stack.metal_layers[1].tracks = [14, 42, 72, 102, 128]
    with pytest.raises(ValueError, match="supported M0 grid"):
        prepare_config(settings, tech, circuit)
    tech.layer_stack.metal_layers[1].tracks = [16, 42, 72, 102, 128]
    circuit.transistors["MPS0"].gate = "different"
    with pytest.raises(ValueError, match="equal N/P finger counts"):
        prepare_config(settings, tech, circuit)


def test_config_cli_selects_profile(tmp_path):
    config.main([
        "--layout_profile", "gt2n_logic", "--cell_names", "INV",
        "--output_dir", str(tmp_path),
    ])
    settings = json.loads((tmp_path / "config/INV.json").read_text())
    assert settings["layout_profile"]["value"] == "gt2n_logic"
    assert settings["contact_rows"]["value"] == {"gate": [2], "diffusion": [0, 1, 3, 4]}
