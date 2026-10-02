# Reproduce the GT2N standard-cell 6T with CPCell

This example reproduces the **210 × 144 nm** standard-row 6T in
`chipforge_gt2n/scripts/gt2n_sram_bitcell.py`, for the w13/lvt and w31/lvt flavors.
It fixes the six transistor positions/orientations and the external ports, then
lets CP-SAT find the routes. No reference routing segments or reference cell
polygons are injected into the generated layout. The resulting cross-coupling
route can differ from the hand-drawn reference.

The reference placement is:

| Row | Gate x=42 nm | Gate x=84 nm | Gate x=126 nm | Gate x=168 nm |
| --- | --- | --- | --- | --- |
| NMOS | PGL: BL–Q, gate WL | PDL: Q–VSS, gate QB | PDR: VSS–QB, gate Q | PGR: QB–BLN, gate WL |
| PMOS | Empty | PUL: Q–VDD, gate QB | PUR: VDD–QB, gate Q | Empty |

WL crosses the cell on M0 at y=14 nm. BL and BLN cross it on M1 at x=21 and
189 nm. The cell uses no M2. The boundary dummy gates and backside power rails
allow mirrored abutment, as in the reference.

## Generate

From the CPCellUCSD repository root, using its installed Python environment:

```bash
.venv/bin/python examples/gt2n_sram/reproduce.py
```

Outputs default to `build/gt2n_sram_reference/`:

- `config/SRAM6T_REF.json`: complete per-cell solver config.
- `result/SRAM6T_REF.res` and `.var`: CP-SAT placement/routing and solved variables.
- `solve.log`: solver output (the constrained reference case reaches OPTIMAL).
- `w13/cpcell.gds`, `w31/cpcell.gds`: physical GT2N layouts.
- `w13/cpcell.cdl`, `w31/cpcell.cdl`: corresponding physically sized schematics.
- `report.json`: generation status and input hashes. Without `--verify`, external
  validation is explicitly reported as `not_run`.

Use `--output-dir PATH` for a separate run or `--widths 31` for one flavor.
The `.cdl` input uses CPCell's abstract two-fin width (46 nm) to obtain one
solver finger per device. The generated physical schematics and GDS use the
requested **13 or 31 nm sheet width**, with 14 nm gate length.

## DRC, LVS and SPICE

Use a Python environment containing both projects' dependencies. The sibling
repository's environment works in this workspace:

```bash
PYTHONDONTWRITEBYTECODE=1 \
../chipforge_gt2n/.venv/bin/python examples/gt2n_sram/reproduce.py \
  --reference-repo ../chipforge_gt2n --spice
```

`--verify` runs DRC/LVS only; `--spice` includes verification and Xyce nominal
hold/read/write characterization. KLayout, Xyce, and the GT2N PDK must be
available through the reference project's existing discovery mechanisms
(`GT2N_ROOT`, `XYCE`, etc.). Xyce's MPI runtime needs local process/socket access.
All generated artifacts stay under `--output-dir`; the reference repository is
read-only input.

Verification generates the hand-drawn reference in a separate GDS. It imports
the **CPCell-generated** cell into the reference's mirrored **3 × 4** array
scaffold, then runs the same full platform DRC and transistor LVS decks on both
single cells and arrays. LVS compares against the reference generator's CDL,
independently of the CPCell input. The report records source-file hashes,
individual DRC counts and LVS statuses; a mismatch makes the command fail.

For both w13/lvt and w31/lvt, the reproduced cells and arrays pass LVS. DRC
findings exactly match the hand-drawn references:

| Scope | GATE.2 | SDCON.ACT.1 | Other findings |
| --- | ---: | ---: | ---: |
| Single cell | 2 | 10 | 0 |
| Mirrored 3 × 4 array | 2 | 40 | 0 |

These are the reference project's documented findings: the outer ends of the
gate grid and the platform deck's SDCON enclosure convention. This is parity
with that reference under the available KLayout deck, not a claim of zero
findings or proprietary ICV signoff.

The nominal Xyce results at **TT, 0.7 V, 25 °C** are:

| Flavor | Hold SNM | Read SNM | Bit-line write trip | Read current |
| --- | ---: | ---: | ---: | ---: |
| w13/lvt | 256 mV | 106 mV | 258 mV | 63 µA |
| w31/lvt | 250 mV | 102 mV | 266 mV | 128 µA |

The full hold/read/write acceptance checks pass, including repeated reads and
finite write time. SPICE uses the **LVS-matched reference schematic**, with the
reference's estimated 0.25 fF storage-node loads. It does not extract RC from
the CPCell layout and does not establish mismatch yield.

## Configuration and export details

- `reference.cdl` supplies the six-transistor topology.
- `reference.layer` supplies nonuniform routing tracks via optional `tracks`
  arrays in **nm**: M0/M2 rows `[14, 42, 72, 102]` and M1 columns
  `[21, 84, 126, 189]`. PC placement remains on the regular 42 nm gate grid.
- `constraints.json` overlays the generated config. It enables independent
  unequal rows, fixes placement/ports, permits only M0/M1, and restricts gate
  contacts to rows 0/2 and diffusion contacts to rows 1/3. This keeps gate
  contacts off the active nanosheets.
- `supervia: ["M0"]` is CPCell's existing switch allowing a via-only M0 landing.
  It does not create a physical skip-layer via. The exporter emits the required
  24 × 12 nm M0 landing, and all layer transitions remain adjacent-layer vias.
- `layout_profile: "gt2n_sram"` is written into `.res`. The GT2N exporter uses
  direct physical routing coordinates (doubled in the solver), the reference
  cuts at missing P/N devices, sheet-dependent SDCON lengths, shorter M1 via
  landings, and rail contacts suitable for mirrored abutment. Backside labels
  are `vdd`/`vss`, matching the reference LVS deck's implicit bulk nets.

The default layout profile and regular layer grids retain their existing
behavior. Placement row indices and `.res` transistor Y/height fields remain
CPCell's abstract row coordinates; routing coordinates in this profile are
physical coordinates scaled by two. Explicit boundary nets receive GDS pin
markers only on their configured port layers.

The regression suite checks both sheet widths, the fixed placement, legal
contact rows, layer restrictions, and exact boundary-pin geometry without
requiring the sibling repository. External DRC/LVS/SPICE checks use the command
above.
