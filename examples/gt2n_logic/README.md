# GT2N logic-library extension with CPCell

This batch adds **NAND4, NOR4, AOI221 and OAI221**, initially with 31 nm sheets,
LVT devices, 14 nm gates, and backside power in a 144 nm standard-cell row.
The complementary static-CMOS networks are authored in `cells.py`; no reference
layout polygons or fixed transistor ordering are supplied to CPCell.

| Cell | Function Y | Transistors |
| --- | --- | ---: |
| NAND4 | `!(A & B & C & D)` | 8 |
| NOR4 | `!(A | B | C | D)` | 8 |
| AOI221 | `!((A1 & A2) | (B1 & B2) | C)` | 10 |
| OAI221 | `!((A1 | A2) & (B1 | B2) & C)` | 10 |

Every device uses one three-sheet GT2N transistor. Series devices have not been
upsized to compensate for stack resistance. Names such as
`gt2_6t_nand4_w31_lvt` intentionally omit an `x1` drive-strength claim.

## Validated first batch

The complete `--spice` run produced these footprints, with `OPTIMAL` status
within the configured search model:

| Cell | Width × height (nm) | Area (µm²) | SPICE vectors |
| --- | --- | ---: | ---: |
| NAND4 | 252 × 144 | 0.036288 | 16 |
| NOR4 | 252 × 144 | 0.036288 | 16 |
| AOI221 | 294 × 144 | 0.042336 | 32 |
| OAI221 | 294 × 144 | 0.042336 | 32 |

All four passed transistor LVS individually and in mirrored 2 × 2 arrays.
DRC contained only the two documented categories discussed below. All 96
SPICE truth-table vectors and 900 propagation-delay measurements passed.
OpenROAD loaded the generated LEF and Liberty alongside the released GT2N
LEFs, linked all four cells, and evaluated their timing arcs. Yosys also loaded
the Liberty and linked a four-cell smoke design. The Python regression suite
passed with 41 tests and 66 subtests.

## Generate and verify

```bash
# Generate using this repository's dependencies only.
.venv/bin/python examples/gt2n_logic/reproduce.py

# Full physical checks and nominal schematic characterization.
PYTHONDONTWRITEBYTECODE=1 \
../chipforge_gt2n/.venv/bin/python examples/gt2n_logic/reproduce.py --spice
```

`--verify` runs DRC/LVS without SPICE. `--spice` implies `--verify` and generates
an experimental Liberty file. External verification reuses the sibling
project's full platform DRC deck, transistor LVS deck, and Xyce model-card
adapter; it requires KLayout, Xyce and the GT2N PDK. Xyce's MPI runtime needs
local daemon/socket access. The sibling repositories are read-only inputs.

Use `--cells NAND4 AOI221` for a subset, `--timeout 180` to allow more solve time
per cell, `--reference-repo PATH` to locate the verification helpers, and
`--output-dir PATH` to select another output directory. Relative and absolute
output paths work; the script also works outside the repository directory.

The default output directory is `build/gt2n_logic_batch/`:

- `solver.cdl`, `config/*.json`, `result/*.res`, and per-cell `solve.log` preserve
  the actual topology, constraints, placement and routing.
- `cells.gds`, `cells.cdl`, `cells.lef`, and `cells.v` form the physical and
  functional views. Individual GDS/CDL files are also saved under each cell.
- `cells_tt.lib` is generated only by a successful `--spice` run.
- Each cell's `*.lyrdb`/`*.lvsdb` files contain the physical check results.
- Each cell's `truth.cir` and `timing_*.cir` reproduce the SPICE benches.
- `report.json` records dimensions, solver status, input/result/model hashes,
  DRC counts, LVS status, every truth-table result and timing sample.

Regeneration removes superseded combined GDS/LEF/Liberty views. A failed solve
cannot reuse an old `.res` file. Check the report status before using artifacts
from a failed or interrupted run.

## Placement, routing and pin access

The `gt2n_logic` profile retains CPCell's two abstract placement bands and
four-track device dimensions, while supplying **five physical routing rows**:
M0/M2 at y=`[16, 42, 72, 102, 128]` nm. `TRACK=4` in `.res` describes the abstract
placement model; `LAYOUT_PROFILE=gt2n_logic` selects the physical grid.

Complementary devices driven by the same input are constrained to align, so
that they share a continuous gate with access in the central gap at y=72 nm.
CPCell chooses their order, source/drain orientations, diffusion breaks, cell
width, contacts and wires. There are no fixed placement coordinates.

Diffusion access uses the outer tracks and the tracks through each active band.
The upper outer track allows independent PMOS internal-net connections without
extending SDCON into the narrow N/P gap. M1 access is prohibited at y=16/128 nm:
its end extensions there would violate the 20 nm end spacing across mirrored
rows. M1 pins and routing use the inner tracks, and the LEF exposes actual
solved M1 metal. M0 and internal M1/M2 shapes are obstructions.

The search canvas permits one extra diffusion-break slot for NAND4/NOR4 and
four for AOI221/OAI221. `OPTIMAL` means optimal for this constrained model and
its weighted objective; it does not establish a globally minimum-area GT2N
implementation or optimized electrical sizing.

## Verification scope

Before placement, an independent switch-network connectivity check exhausts
all **96 Boolean input vectors**, rejecting both floating outputs and supply
shorts. The generated cells are checked against their physical-size CDL.
Each is also placed in a **2 × 2 mirrored abutment fixture** with independent
signal nets and shared supply rails, and the complete fixture receives DRC/LVS.
Top-level signal labels establish the fixture's external connections without
adding metal that could conceal an internal routing problem.

The flow fails on any LVS mismatch or DRC category other than the reference
project's documented `GATE.2` and `SDCON.ACT.1` findings. These are respectively
the outer gate-grid ends and the platform deck's contact-enclosure convention.
Counts are reported explicitly; acceptance is not a claim of zero DRC findings
or proprietary signoff.

## Experimental Liberty characterization

`--spice` runs **TT, 0.7 V, 25 °C** using the reference project's GT2N-to-Xyce
BSIM-CMG conversion. Sheet dimensions and the three-sheet count come from the
w31 model card. CDL-only `W`/`M` instance parameters are removed for Xyce, as in
the SRAM characterization flow.

- All 96 static vectors must settle below 10% VDD or above 90% VDD as appropriate.
- Timing tables use 20–80% input slews of **10, 30, 80 ps** and explicit output
  loads of **0.2, 1, 3 fF**.
- Every input arc is checked under **every sensitizing assignment** of the
  other inputs: 4 assignments per NAND4/NOR4 and 21 per AOI221/OAI221.
- Each table entry is the worst measured value across those assignments, for
  both output polarities. Delays use 50% crossings; output slews use 20–80%.
  A negative propagation delay at light load is retained when the output's
  50% crossing precedes the slowly changing input's 50% crossing.
- Input capacitance is the largest measured rising-transition charge divided
  by VDD across the sweeps, including intrinsic Miller coupling.

This Liberty is for **initial mapping and timing experiments**. It has no
extracted wiring RC, no internal/leakage power tables, and no additional PVT or
variation corners. Timing and capacitance therefore do not yet represent a
post-layout characterized library. The supplied load/slew grid bounds its
measured range; further sweeps and extraction are needed before broader use.

Load `cells.lef` alongside the GT2N technology and released standard-cell LEFs;
it references their `gt2_6t` site. Load `cells_tt.lib` alongside a compatible TT
library, and supply `cells.v` for functional simulation.
