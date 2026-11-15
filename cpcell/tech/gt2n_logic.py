"""Physical contract for the single-height GT2N logic solver profile.

Coordinates in solver spacing settings are doubled nanometres. These rules
apply whenever the profile is selected, including callers outside examples.
They supplement (and never relax) caller routing and placement constraints.
"""

from collections import Counter
from copy import deepcopy
import math

from cpcell.utility.entity import Model


TRACKS_NM = (16, 42, 72, 102, 128)
CONTACT_ROWS = {"gate": [2], "diffusion": [0, 1, 3, 4]}
# Include both physical end extensions and the required edge separation.
EOL_C2C = {
    "M0": 2 * (12 + 17 + 12),
    "M1": 2 * (10 + 20 + 10),
    "M2": 2 * (12 + 17 + 12),
}
# M1 landing extensions add 20 nm to a wire's centreline length.
MAR_C2C = {"M0": 0, "M1": 2 * (28 - 20), "M2": 0}


def config_defaults():
    """Technology defaults for newly generated configs, before user edits.

    These include search/pin choices that make the five-row profile usable.
    Unlike prepare_config's physical rules, they do not override existing input.
    """
    return {
        "layout_profile": "gt2n_logic",
        "minimum_gate_cut_length": 1,
        "supervia": ["M0"],
        "MPO": 1,
        "enforce_diffusion_alignment": False,
        "routing_tolerance": False,
        "m0_pin_extension": False,
        "insert_num_db": 1,
        "contact_rows": deepcopy(CONTACT_ROWS),
        "eol_c2c_rule": dict(EOL_C2C),
        "mar_c2c_rule": {"M0": 20, "M1": 60, "M2": 90},
    }


def prepare_config(settings, technology, circuit):
    """Return a private configuration with mandatory GT2N logic rules applied.

    Missing contact restrictions use the profile's legal sites; explicit lists
    must be nonempty subsets. Numeric spacing settings are additional lower
    bounds, combined with physical minima using max. Stricter user values and
    all placement constraints, layer selections, and canvas limits survive.
    """
    _validate_technology(technology)
    counts = {}
    for model in (Model.NMOS, Model.PMOS):
        counts[model] = Counter(
            t.gate for t in circuit.transistors.values() if t.model == model
        )
    if not counts[Model.NMOS] or counts[Model.NMOS] != counts[Model.PMOS]:
        raise ValueError("gt2n_logic requires equal N/P finger counts on each gate net")
    if any(t.get_width() != technology.unit_width for t in circuit.transistors.values()):
        raise ValueError("gt2n_logic requires one abstract unit-width finger per device")

    result = deepcopy(settings)
    rows = result.setdefault("contact_rows", {"value": {}})["value"]
    if not isinstance(rows, dict) or rows.keys() - CONTACT_ROWS.keys():
        raise ValueError("gt2n_logic contact_rows accepts gate and diffusion lists")
    for kind, legal in CONTACT_ROWS.items():
        if kind not in rows:
            rows[kind] = list(legal)
        if (
            not isinstance(rows[kind], list)
            or not rows[kind]
            or any(type(i) is not int or i not in legal for i in rows[kind])
        ):
            raise ValueError(
                f"gt2n_logic {kind} contact_rows must be a nonempty subset of {legal}"
            )
    for option, minima in (("eol_c2c_rule", EOL_C2C), ("mar_c2c_rule", MAR_C2C)):
        values = result.setdefault(option, {"value": {}})["value"]
        if not isinstance(values, dict):
            raise ValueError(f"gt2n_logic {option}.value must be an object")
        for layer, minimum in minima.items():
            value = values.get(layer, minimum)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(
                    f"gt2n_logic {option}.{layer} must be finite and nonnegative"
                )
            values[layer] = max(value, minimum)
    if "M1" in result.get("supervia", {"value": []})["value"]:
        raise ValueError("gt2n_logic does not allow via-only M1 landings (supervia M1)")
    return result


def _validate_technology(technology):
    if (technology.height_config, technology.num_rt_track) != ("SH", 4):
        raise ValueError("gt2n_logic requires the single-height four-track placement model")
    expected = {
        "PC": ("V", 0, 42, 14, None),
        "M0": ("H", 0, 24, 12, list(TRACKS_NM)),
        "M1": ("V", 42, 42, 14, None),
        "M2": ("H", 0, 24, 12, list(TRACKS_NM)),
    }
    layers = technology.layer_stack.metal_layers
    if [layer.layer_name for layer in layers] != list(expected):
        raise ValueError("gt2n_logic requires the PC/M0/M1/M2 stack")
    for layer in layers:
        actual = (layer.direction, layer.offset, layer.pitch, layer.width, layer.tracks)
        if actual != expected[layer.layer_name]:
            raise ValueError(
                f"gt2n_logic requires the supported {layer.layer_name} grid from logic.layer"
            )


def constrain_gate_columns(cell):
    """Every occupied gate column has aligned same-net N/P devices.

    Constrain physical sharing rather than choosing an arbitrary bijection of
    repeated input fingers. User align/order/fixed rules remain active alongside it.
    """
    for shared in cell.gate_share_at_col_vars.values():
        cell.opt.Add(shared == 1)
