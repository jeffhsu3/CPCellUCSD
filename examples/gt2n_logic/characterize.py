"""Nominal schematic characterization, with exhaustive sensitization of each arc.

Uses the GT2N reference project's Xyce model-card adapter. This is TT-only,
with intrinsic device parasitics and explicit output loads; no layout RC.
"""

from itertools import product
import math
import re

VDD = 0.7
SLEWS_PS = (10.0, 30.0, 80.0)  # 20-80% input transition times
LOADS_FF = (0.2, 1.0, 3.0)


def header(cell, pdk):
    from scripts.gt2n_spice import model_card

    return (
        "CPCell GT2N logic characterization\n"
        + model_card(pdk, "w31", "lvt", "nmos")[0]
        + model_card(pdk, "w31", "lvt", "pmos")[0]
        # BSIM-CMG sheet dimensions are in the selected model card. Match
        # the reference project's SRAM benches: omit CDL-only W and M.
        + re.sub(r"\s+(W|M)=\S+", "", cell.cdl(physical=True))
        + "\n.TEMP 25\nVDD vdd 0 0.7\n"
    )


def truth_table(cell, pdk, directory):
    from scripts.gt2n_spice import run_xyce

    vectors = list(product((0, 1), repeat=len(cell.inputs)))
    deck = header(cell, pdk)
    for i, vector in enumerate(vectors):
        nodes = ["vdd" if value else "0" for value in vector]
        deck += f"X{i} {' '.join(nodes)} y{i} vdd 0 {cell.physical_name}\n"
    deck += (
        ".TRAN 1p 10p\n.PRINT TRAN FORMAT=CSV "
        + " ".join(f"V(y{i})" for i in range(len(vectors)))
        + "\n.END\n"
    )
    (directory / "truth.cir").write_text(deck)
    outputs = run_xyce(deck)
    results = []
    for i, vector in enumerate(vectors):
        voltage = outputs[f"V(Y{i})"][-1]
        expected = cell.expected(vector)
        if not math.isfinite(voltage) or not (
            -0.01 <= voltage <= 0.1 * VDD
            if expected == 0
            else 0.9 * VDD <= voltage <= VDD + 0.01
        ):
            raise ValueError(f"{cell.name} truth table failed at {vector}: {voltage} V")
        results.append(
            {"inputs": list(vector), "expected": expected, "output_v": voltage}
        )
    return results


def sensitizations(cell):
    cases = []
    for pin in cell.inputs:
        others = [p for p in cell.inputs if p != pin]
        for values in product((0, 1), repeat=len(others)):
            static = dict(zip(others, values))
            outputs = [
                cell.expected([level if p == pin else static[p] for p in cell.inputs])
                for level in (0, 1)
            ]
            if outputs[0] != outputs[1]:
                if outputs != [1, 0]:
                    raise ValueError("This batch requires negative-unate timing arcs")
                cases.append((pin, static))
    return cases


def crossing(times, values, threshold, start, stop, rising):
    for t0, t1, v0, v1 in zip(times, times[1:], values, values[1:]):
        if t1 < start or t0 > stop or v0 == v1:
            continue
        if (v0 <= threshold < v1) if rising else (v0 >= threshold > v1):
            return t0 + (threshold - v0) * (t1 - t0) / (v1 - v0)
    raise ValueError(
        f"Missing {'rising' if rising else 'falling'} crossing at {threshold:g} V"
    )


def characterize(cell, pdk, directory):
    from scripts.gt2n_spice import run_xyce

    cases = sensitizations(cell)
    arcs = {
        pin: {
            metric: [[-math.inf for _ in LOADS_FF] for _ in SLEWS_PS]
            for metric in (
                "cell_rise",
                "cell_fall",
                "rise_transition",
                "fall_transition",
            )
        }
        for pin in cell.inputs
    }
    caps = {pin: 0.0 for pin in cell.inputs}
    details = []
    for si, slew in enumerate(SLEWS_PS):
        ramp = slew * 1e-12 / 0.6
        up, down = 200e-12, 600e-12
        for li, load in enumerate(LOADS_FF):
            deck = header(cell, pdk)
            for i, (pin, static) in enumerate(cases):
                deck += f"VIN{i} in{i} 0 PWL(0 0 {up:g} 0 {up + ramp:g} {VDD} {down:g} {VDD} {down + ramp:g} 0)\n"
                nodes = [
                    f"in{i}" if p == pin else ("vdd" if static[p] else "0")
                    for p in cell.inputs
                ]
                deck += f"X{i} {' '.join(nodes)} y{i} vdd 0 {cell.physical_name}\nCL{i} y{i} 0 {load:g}f\n"
            deck += (
                ".TRAN 0.5p 1n 0 1p\n.PRINT TRAN FORMAT=CSV "
                + " ".join(f"V(in{i}) V(y{i}) I(VIN{i})" for i in range(len(cases)))
                + "\n.END\n"
            )
            (directory / f"timing_s{slew:g}_c{load:g}.cir").write_text(deck)
            data = run_xyce(deck)
            times = data["TIME"]
            for i, (pin, static) in enumerate(cases):
                inputs, outputs = data[f"V(IN{i})"], data[f"V(Y{i})"]
                metrics = {}
                for name, start, stop, rise in [
                    ("fall", up, down, False),
                    ("rise", down, 1e-9, True),
                ]:
                    t_in = crossing(times, inputs, VDD / 2, start, stop, not rise)
                    t_out = crossing(times, outputs, VDD / 2, start, stop, rise)
                    t_lo = crossing(times, outputs, VDD * 0.2, start, stop, rise)
                    t_hi = crossing(times, outputs, VDD * 0.8, start, stop, rise)
                    metrics["cell_" + name] = (t_out - t_in) * 1e12
                    metrics[name + "_transition"] = abs(t_hi - t_lo) * 1e12
                for metric, value in metrics.items():
                    # A 50%-to-50% propagation delay may be negative for a
                    # slow input and light load. Transition durations may not.
                    if not math.isfinite(value) or (
                        metric.endswith("transition") and value <= 0
                    ):
                        raise ValueError(f"Invalid {pin} {metric}: {value}")
                    arcs[pin][metric][si][li] = max(arcs[pin][metric][si][li], value)
                # Charge entering the input during its rising transition,
                # including Miller coupling. Keep the largest effective value.
                currents = data[f"I(VIN{i})"]
                charge = sum(
                    -0.5 * (i0 + i1) * (t1 - t0)
                    for t0, t1, i0, i1 in zip(times, times[1:], currents, currents[1:])
                    if up <= t0 and t1 <= up + ramp + 100e-12
                )
                caps[pin] = max(caps[pin], charge / VDD * 1e15)
                details.append(
                    {
                        "pin": pin,
                        "static": static,
                        "slew_ps": slew,
                        "load_ff": load,
                        **metrics,
                    }
                )
    if any(not math.isfinite(v) or v <= 0 for v in caps.values()):
        raise ValueError("Invalid measured input capacitance")
    return {
        "slews_ps": SLEWS_PS,
        "loads_ff": LOADS_FF,
        "arcs": arcs,
        "input_capacitance_ff": caps,
        "sensitizations": len(cases),
        "samples": details,
        "scope": "TT 0.7 V 25 C, schematic-only, no extracted RC; worst sensitizing vector per table entry",
    }


def liberty(cells, report):
    lines = [
        "/* Experimental schematic-only TT characterization; no extracted layout RC. */",
        "library (gt2_cpcell_w31_lvt_tt_0p7v25c) {",
        "  delay_model : table_lookup;",
        '  time_unit : "1ps";',
        '  voltage_unit : "1V";',
        '  current_unit : "1mA";',
        '  pulling_resistance_unit : "1kohm";',
        '  leakage_power_unit : "1nW";',
        "  capacitive_load_unit (1,ff);",
        "  nom_voltage : 0.7;",
        "  nom_temperature : 25;",
        "  nom_process : 1;",
        "  input_threshold_pct_rise : 50;",
        "  input_threshold_pct_fall : 50;",
        "  output_threshold_pct_rise : 50;",
        "  output_threshold_pct_fall : 50;",
        "  slew_lower_threshold_pct_rise : 20;",
        "  slew_upper_threshold_pct_rise : 80;",
        "  slew_lower_threshold_pct_fall : 20;",
        "  slew_upper_threshold_pct_fall : 80;",
        "  operating_conditions (tt_0p7v25c) { process : 1; voltage : 0.7; temperature : 25; }",
        "  default_operating_conditions : tt_0p7v25c;",
        "  lu_table_template (delay_3x3) {",
        "    variable_1 : input_net_transition;",
        "    variable_2 : total_output_net_capacitance;",
        f'    index_1 ("{", ".join(map(str, SLEWS_PS))}");',
        f'    index_2 ("{", ".join(map(str, LOADS_FF))}");',
        "  }",
    ]
    for cell in cells:
        entry = report["cells"][cell.name]
        char = entry["characterization"]
        lines += [
            f"  cell ({cell.physical_name}) {{",
            f"    area : {entry['area_um2']:.9g};",
            '    pg_pin (vdd) { pg_type : primary_power; voltage_name : "vdd"; }',
            '    pg_pin (vss) { pg_type : primary_ground; voltage_name : "vss"; }',
        ]
        for pin in cell.inputs:
            lines += [
                f'    pin ({pin}) {{ direction : input; capacitance : {char["input_capacitance_ff"][pin]:.9g}; related_power_pin : "vdd"; related_ground_pin : "vss"; }}'
            ]
        lines += [
            "    pin (Y) {",
            "      direction : output;",
            f'      function : "{cell.function}";',
            f"      max_capacitance : {max(LOADS_FF)};",
            '      related_power_pin : "vdd"; related_ground_pin : "vss";',
        ]
        for pin in cell.inputs:
            lines += [
                "      timing () {",
                f'        related_pin : "{pin}";',
                "        timing_sense : negative_unate;",
                "        timing_type : combinational;",
            ]
            for metric, matrix in char["arcs"][pin].items():
                rows = [f'"{", ".join(f"{v:.9g}" for v in row)}"' for row in matrix]
                lines += [
                    f"        {metric} (delay_3x3) {{",
                    "          values (" + ", ".join(rows) + ");",
                    "        }",
                ]
            lines += ["      }"]
        lines += ["    }", "  }"]
    return "\n".join(lines + ["}", ""])
