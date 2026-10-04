# Expanded GT2N logic catalog

The single catalog in `examples/gt2n_logic/cells.py` now contains eighteen
complementary static-CMOS cells. Six additions extend the earlier twelve-cell
library; the default command includes all eighteen in one library.

| Addition | Output Y | Transistors | Generated size (nm) |
| --- | --- | ---: | --- |
| AND5 | `A & B & C & D & E` | 12 | 336 × 144 |
| OR5 | `A \| B \| C \| D \| E` | 12 | 336 × 144 |
| AO222 | `(A1 & A2) \| (B1 & B2) \| (C1 & C2)` | 14 | 420 × 144 |
| OA222 | `(A1 \| A2) & (B1 \| B2) & (C1 \| C2)` | 14 | 420 × 144 |
| MAJ3 | `(A & B) \| (A & C) \| (B & C)` | 12 | 336 × 144 |
| MAJI3 | `!((A & B) \| (A & C) \| (B & C))` | 10 | 294 × 144 |

The existing cells are AND4, OR4, NAND4, NOR4, NAND5, NOR5, AO221, OA221,
AOI221, OAI221, AOI222, and OAI222. Physical names retain the
`gt2_6t_<name>_w31_lvt` convention. Every physical device has W=31 nm, L=14 nm,
and the LVT flavor. Series stacks are not upsized; no `x1` drive strength is
claimed.

AND5, OR5, AO222, and OA222 add an output inverter to the corresponding
inverting gate, using internal net Z. MAJI3 uses the factored pull-down
expression `AB + C(A+B)` and its complementary pull-up, requiring five devices
per polarity. MAJ3 adds an output inverter. The independent Boolean specification
uses the symmetric majority expression; switch-network checks enumerate all
input vectors and require a unique rail-driven state for internal gate nets.

For repeated majority inputs A/B, each complementary pair shares a gate column;
the two occurrences remain distinct devices. Placement constraints align one
NMOS with one PMOS at a time. CPCell still chooses column positions, device
order, source/drain orientation, diffusion breaks, and routing. Compound AO/OA
and majority cells allow four extra diffusion-break slots; other cells allow
one. Widths above are generated results under these constraints, not universal
minimum-area claims.

## Generate and validate

```sh
# List all eighteen cells and their functions.
.venv/bin/python examples/gt2n_logic/reproduce.py --list-cells

# Generate the whole catalog.
.venv/bin/python examples/gt2n_logic/reproduce.py

# Select just the six additions.
.venv/bin/python examples/gt2n_logic/reproduce.py \
  --cells AND5 OR5 AO222 OA222 MAJ3 MAJI3

# Generate the full library with cell/array DRC, LVS, and SPICE characterization.
PYTHONDONTWRITEBYTECODE=1 \
../chipforge_gt2n/.venv/bin/python examples/gt2n_logic/reproduce.py --spice
```

Default outputs go to `build/gt2n_logic_library/` and retain the library name
`gt2_cpcell_logic_w31_lvt`. GDS, LEF, CDL, functional Verilog, `catalog.json`,
per-cell reports, and a hashed manifest are generated together. `--spice` adds
`gt2_cpcell_logic_w31_lvt_tt_0p7v25c.lib`. Explicit selections get a distinct
library name and default to `build/gt2n_logic_custom/`. Use `--output-dir` to
choose another destination, and `--verify` for physical checks without SPICE.
The reference GT2N repository and PDK are read-only verification inputs.

The expanded catalog covers 592 static input vectors. The six additions account
for 208 of those. Timing sweeps cover both input edges at 10/30/80 ps slew and
0.2/1/3 fF output load. AND5 and OR5 each have five sensitizations; AO222 and
OA222 each have 54; MAJ3 and MAJI3 each have six. Across the full catalog this
produces 6,264 propagation-delay measurements, taking the worst sensitizing
assignment for each table entry. MAJI3 is negative-unate; the other five
additions are positive-unate.

Verification requires transistor LVS for each cell and its 2 × 2 mirrored
abutment array. Platform DRC permits the established `GATE.2` and `SDCON.ACT.1`
exceptions and records their counts; other categories fail the run. Always
check `report.json` and the manifest status before consuming a snapshot.

Liberty characterization remains experimental, schematic-only TT at 0.7 V and
25 °C. It does not include extracted wiring RC, power tables, other PVT corners,
or variation models.

## Recorded validation

The expanded eighteen-cell snapshot in `build/gt2n_logic_library/` passed
cell/array LVS, platform DRC with the exceptions above, all 592 static SPICE
vectors, and all 6,264 delay measurements. All eighteen solves returned
`OPTIMAL`. The regression suite passed 132 tests and 66 subtests.

`build/gt2n_logic_library_checks/expanded/` records downstream checks: Yosys
evaluates the functional Verilog for all 64 assignments of a shared six-bit
test input and compares every cell output to the catalog's Boolean spec.
OpenROAD loads the LEF/Liberty views, links all eighteen instances, and evaluates
timing arcs. `summary.json` records these results and manifest/source hash
verification; the commands, test netlist, and logs are retained alongside it.
