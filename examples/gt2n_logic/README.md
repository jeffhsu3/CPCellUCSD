# GT2N NAND4 generation example

This standalone example demonstrates automatic placement/routing and W13/W31
GDS export on the five-track GT2N logic grid in a 144 nm row. Its eight-device
NAND4 input is explicit in `reference.cdl`; no PDK or neighboring checkout is
needed to generate the layout.

```sh
.venv/bin/python examples/gt2n_logic/reproduce.py
.venv/bin/python examples/gt2n_logic/reproduce.py --widths 31 13
```

Outputs default to `build/gt2n_nand4/`. Use `--output-dir PATH` to choose another
directory and `--timeout SECONDS` to set the solve limit. One solution is
exported at every requested width, retaining the same footprint and pins.
The output includes `result/NAND4.res`, the config, solve log, per-width GDS
and physical CDL, and `report.json`. Generation does not claim external
DRC/LVS or electrical qualification.

The full eighteen-cell catalog, LEF/Verilog/Liberty views, manifests, library
tests and qualification workflows now live in
[`chipforge_gt2n`](../../../chipforge_gt2n/docs/cpcell_logic.md), under
`chipforge_gt2n.cells.logic`. From that checkout:

```sh
uv run python -m chipforge_gt2n.cells.logic --list-cells
uv run python -m chipforge_gt2n.cells.logic --widths 31 13
uv run python -m chipforge_gt2n.cells.logic --cells NAND4 AOI222 --spice
```

The previous `--cells`, `--list-cells`, `--verify` and `--spice` options belong
to that downstream command. Existing `build/gt2n_logic_library*` snapshots
remain untouched in the CPCell checkout; select their absolute path downstream
only when intentionally regenerating them. Recorded validation and history
moved with the catalog.
