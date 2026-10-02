# Boundary ports and placement constraints

This page describes the single-height FinFET solver. The
[GT2N thin-cell example](../examples/gt2n_thin/README.md) uses a separate fixed
four-band placement schema and routing model.

These optional entries belong in a generated per-cell JSON config. Both default
to `{"value": []}`; older configs continue to work. They are hard constraints:
contradictions produce an infeasible solve, rather than being treated as hints.
Explicit ports or placement rules disable the automatic placement symmetry
heuristics, because the requested positions distinguish otherwise equivalent
layouts. Technology, spacing, diffusion-sharing, and routing rules still apply.

## Boundary ports

```json
"boundary_ports": {
  "value": [
    {"name": "WL_L", "net": "WL", "side": "left",  "layer": "M2", "track": 0},
    {"name": "WL_R", "net": "WL", "side": "right", "layer": "M2", "track": 0},
    {"name": "BL_B", "net": "BL", "side": "bottom", "layer": "M1", "track": 1},
    {"name": "BL_T", "net": "BL", "side": "top",    "layer": "M1", "track": 1}
  ]
}
```

Each port has a unique `name`, an existing signal I/O `net`, a `side`, and a
metal `layer`. With the supplied H/V/H stack, M0 and M2 support left/right,
and M1 supports bottom/top. Power nets use the existing power rails and cannot
be configured here. Internal nets cannot be promoted to ports by this option.

`track` is an optional **zero-based routing-track index in that layer**. For
left/right it selects a row, counted from the bottom. For top/bottom it selects
a column, counted from the left. Omit it to let the solver choose. Assign the
same track to opposite ports when they must align; independently chosen tracks
are not guaranteed to match. Layer pitches and offsets determine coordinates.

All listed ports must connect to their net. Multiple ports on one net are
supported and may share routing. Ports must have distinct attachment nodes.
For a configured net, the list replaces its default single interior pin;
unlisted nets retain their interior pins and pin-accessibility heuristics.
Explicit boundary nets use routing and spacing constraints instead of the
interior M1/M0 pin-opening and pin-separation heuristics.

The router attaches ports to the outermost legal routing-grid nodes. The right
edge follows the **solved width**, rather than the maximum canvas width.
`insert_num_db` controls available placement space; increase it if the requested
tracks, placement, or wiring need a wider cell. A track outside the canvas is a
configuration error; a track inside the canvas that cannot be reached under the
remaining constraints makes the solve infeasible.

Results include a `** Boundary Ports **` section with one JSON record per port.
`row` and `col` are solved attachment coordinates in CPCell's doubled-nm units,
not track indices. `cpcell.gds.result.parse_boundary_ports(path)` reads these
records. The existing four-part `parse_result` API remains unchanged.

The **GT2N exporter** extends each attachment to the physical cell edge and adds
pin-purpose metal and the net label. It checks that the attachment belongs to a
route and checks the resulting metal for shorts. Port names remain in `.res`;
GDS labels use electrical net names. Boundary stubs outside the solver grid are
export geometry, so they do not count toward solver wirelength or replace
foundry DRC/LVS and array-abutment checks. The legacy FinFET exporter rejects
results containing boundary ports rather than silently omitting the extensions.

## Placement constraints

Use expanded finger names, such as `MP0S0`, as shown in the parsed netlist and
placement result. Unexpanded names and wildcards are not accepted.

**Placement columns differ from routing tracks.** `column` and `columns` use
CPCell's odd PC indices `1, 3, 5, ...`, as in `inject_placement`. Consecutive
placement columns are one CPP apart. For the supplied single-height four-track
technology, NMOS `row` is `0` and PMOS `row` is `2`. Coordinates are validated
against the current canvas; increase `insert_num_db` to make more columns
available. Placement constraints do not resize the canvas automatically.

```json
"placement_constraints": {
  "value": [
    {"type": "fixed", "transistor": "MP0S0", "column": 1, "row": 2, "flip": false},
    {"type": "fixed", "transistor": "MP1S0", "columns": [7, 9]},
    {"type": "order", "transistors": ["MA0S0", "MN0S0", "MN1S0", "MA1S0"]},
    {"type": "align", "transistors": ["MP0S0", "MN0S0"]},
    {"type": "mirror", "transistors": ["MP0S0", "MP1S0"], "opposite_flip": true}
  ]
}
```

This illustrates the rule syntax; choose a compatible subset for your circuit.

| Type | Behavior |
| --- | --- |
| `fixed` | Constrain any combination of `column` (one location), `columns` (allowed locations), `row`, and boolean `flip`. Specify `column` or `columns`, not both. Unspecified fields remain free. |
| `order` | Place the listed devices strictly left to right. Optional integer `min_spacing` specifies the minimum distance between consecutive entries in CPP, default `1`. Other devices may occupy the gaps. |
| `align` | Give all listed devices the same column, usually across the P/N rows. Same-row devices still cannot overlap. |
| `mirror` | Reflect the two gate centres about the solved physical cell centre. Defaults to opposite source/drain orientations; `opposite_flip: false` leaves orientation unconstrained. Rows are unchanged. |

Mirror uses `x_a + x_b = cpp_cost + 1`, since gate centres are at
`(x + 1) * CPP / 2` and cell width is `(cpp_cost + 3) * CPP / 2`.
The legacy `inject_placement` option still works and is applied alongside these
rules, so conflicting injections also make the solve infeasible.

## Six-transistor example

[The SRAM constraint fixture](../tests/fixtures/sram6t_constraints.json) is a
config overlay for [the 6T netlist](../tests/fixtures/sram6t.cdl). It enables
unequal rows, independent diffusion breaks, extra placement space, six ports
(WL left/right and BL/BLB top/bottom), and access-transistor ordering. Merge its
entries into a generated `SRAM6T.json`, then use the usual solver and GT2N export
commands. The regression suite routes this topology with the
`GT2N_FinFET_2F_4T_4242OF0.layer` stack. A separate four-port inverter regression
checks GT2N boundary geometry. An SRAM solve can still fail the exporter's
physical short checks: the abstract spacing rules do not model every landing
and wire-end extension. Such results require revised routing constraints before
GDS can be produced.

This demonstrates layout constraints on abstract device sizes. Per-device sizing,
electrical SRAM characterization, and array signoff remain separate work.


## Routing grids and contact access

A metal entry in `.layer` may provide a nonempty, strictly increasing `tracks`
list in nm, on the 0.5 nm grid. It replaces pitch/offset-generated rows on a
horizontal metal or columns on a vertical metal. PC placement stays regular;
explicit tracks are not accepted for PC. Horizontal track counts must match
`num_rt_track`; vertical tracks outside the maximum placement canvas are omitted.
Track indices in `boundary_ports` refer to this list when present.

`routing_layers.value` lists the enabled routing metals (default M0/M1/M2).
A boundary port must use an enabled layer. `contact_rows.value` can specify
`{"gate": [0, 2], "diffusion": [1, 3]}` to restrict PC-to-M0 contact access;
these are PC row **indices**, not physical coordinates. Omitted contact types
retain all rows permitted by the other constraints.

`layout_profile.value` defaults to `"default"`. The `"gt2n_sram"` profile is
paired with the reference grid and changes the GT2N physical export conventions.
It is preserved as `LAYOUT_PROFILE` in the result's technology section. See the
[verified 210 × 144 nm 6T example](../examples/gt2n_sram/README.md) for the
complete configuration and reproduction command.
