# Historical generation milestones

The cells were developed and validated in three batches before consolidation
into one catalog. These are historical results and commands; the current CLI
uses the complete catalog by default and `--cells` for subsets. See [current
instructions](README.md). Existing snapshots retain their original provenance.

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

## Validated second batch

`--batch 2 --spice` produced all four cells with `OPTIMAL` solver status:

| Cell | Width × height (nm) | Area (µm²) | SPICE vectors |
| --- | --- | ---: | ---: |
| AND4 | 294 × 144 | 0.042336 | 16 |
| OR4 | 294 × 144 | 0.042336 | 16 |
| AO221 | 336 × 144 | 0.048384 | 32 |
| OA221 | 336 × 144 | 0.048384 | 32 |

Each footprint is one 42 nm gate pitch wider than its first-batch counterpart.
All cell and mirrored-array LVS checks passed, with only the same two DRC
categories. All 96 SPICE vectors and 900 timing measurements passed. OpenROAD
loaded both batches together and evaluated the positive-unate timing arcs;
Yosys loaded both Liberty files and checked the functional Verilog. The full
Python suite passed with 44 tests and 66 subtests.

## Validated third batch

`--batch 3 --spice --timeout 180` produced all four cells with `OPTIMAL`
status within the configured search model:

| Cell | Width × height (nm) | Area (µm²) | SPICE vectors |
| --- | --- | ---: | ---: |
| NAND5 | 294 × 144 | 0.042336 | 32 |
| NOR5 | 294 × 144 | 0.042336 | 32 |
| AOI222 | 336 × 144 | 0.048384 | 64 |
| OAI222 | 336 × 144 | 0.048384 | 64 |

All cells and mirrored 2 × 2 arrays passed transistor LVS, with only the
documented `GATE.2` and `SDCON.ACT.1` DRC categories. All 192 static SPICE
vectors and 2,124 propagation-delay measurements passed. OpenROAD and Yosys
loaded all three batches together; OpenROAD evaluated batch 3's timing arcs
and Yosys checked its functional Verilog. The full regression suite passed
with 45 tests and 66 subtests. As for the earlier batches, the Liberty is
nominal schematic characterization, without extracted wiring RC or power.

