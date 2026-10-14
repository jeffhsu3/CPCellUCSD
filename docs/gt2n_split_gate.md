# GT2N split-gate decap support

`SplitGateDecapProblem` models the cross-coupled NMOS/PMOS decap with independently
contacted gate segments. It supports a split gate at x=42 nm in an 84 nm cell,
and staggered gates at x=42/84 nm in a 126 nm cell. It uses `BandRouter` to choose
contact sites and M0/M1/M2 routes. No routed metal is copied from GT2N. The
profiles support this two-device circuit,
W13/W31 sheets, L=14 nm, and all five threshold flavors.

## Run

From the CPCell checkout:

```sh
# Original 84 × 144 nm W31 footprint. Exit 2 means scoped INFEASIBLE.
python examples/gt2n_split_gate/reproduce.py --output-dir build/split_w31_144

# A taller, feasible control on the same 24 nm M0 grid.
python examples/gt2n_split_gate/reproduce.py --height-nm 168 \
  --output-dir build/split_w31_168

# Feasible 126 × 144 nm W31 candidate on the standard grid and row height.
python examples/gt2n_split_gate/reproduce.py --arrangement staggered \
  --output-dir build/staggered_w31_144
```

Outputs always include `solution.json`, `report.json`, and a hash manifest.
Feasible results also include GDS, four-terminal CDL, and `CLASS CORE` LEF.
Exit codes distinguish a generated candidate (0), proved model infeasibility
(2), and an unresolved result such as timeout (3). Invalid input raises an
error. Rerunning removes stale generated layouts and verification status.

`solution.json` retains every proposed VG/VSD site, its rejection reasons,
and terminals with no legal access. A missing contact never removes the
device or terminal from the circuit: it makes the connectivity constraints
infeasible. GDS export refuses infeasible/unresolved results and independently
checks reconstructed routes before writing.

## Geometry and scope

- Width is fixed per arrangement: 84 nm for `split`, 126 nm for `staggered`.
  This adapter does not optimize placement or minimum width.
- The gate is partitioned using actual `GATE − GCUT − DUMMY` geometry.
  N/P segments are independent intrinsic conductors even though their
  x-coordinates coincide. A cut that shorts or removes a gate is rejected.
- In the staggered arrangement both ACT bands span the full 126 nm width,
  as in every released multi-pitch cell, so the released one-pitch tap and
  filler (42 nm of active each) can merge with them to meet ACT.2 at row
  ends. Each gate crosses the other device's band as a tied-off isolation
  gate under a partial dummy marker (upper x=35–49, y=82–144 nm and lower
  x=77–91, y=0–62 nm at default height), as gate x=84 does in the released
  mux2; the diffusion beyond it is contacted and tied to the source rail.
  There is no middle GCUT. Both VG contacts fit at y=66–78 nm, with 9 nm ACT
  clearance and 4 nm clearance to the partial dummy markers. The released
  deck and transistor extraction accept this geometry, including abutment
  against released cells and one-pitch cells at row ends.
- Cells at the 144 nm row height are named like the released library,
  `gt2_6t_decapcc_sg_w<sheet>_<vt>` (staggered) or `gt2_6t_decapcc_sp_...`
  (split), and their LEF carries the `FOREIGN` line every released macro
  has. Taller controls keep the `gt2n_<arrangement>_decap_w<sheet>_h<height>_<vt>`
  research name.
- Default ACT matches the released bands: W31 N at y=26–57 nm and P at
  y=H−57 through H−26 nm; W13 N at y=35–48 and P at y=H−48 through H−35 nm.
  The 10 nm gate cut defaults to the cell center and must clear ACT by 10 nm.
  In the staggered case this setting positions the partial dummy endpoints;
  the same conservative ACT separation constraint is retained.
- VG is 14 × 12 nm. Candidates must be entirely within their own gate
  segment, avoid touching GCUT/DUMMY, and clear ACT by at least 6 nm.
  VSD must fit its own diffusion contact. VBPR is fully inside BPR/SDCON
  at y=5–16 and H−16 through H−5 nm.
- Default M0 candidates have centers at 24 nm multiples. Horizontal routing
  uses 21 nm x increments inside the footprint, with M1 and inter-metal vias
  every 42 nm starting at x=21 nm. Each selected M1 via requires an adjacent
  wire segment to satisfy minimum metal length.
- Candidate metals retain boundary clearance for mirrored neighbors. This
  finite routing graph and its spacing constraints define the solver's scope;
  physical checking remains necessary for a generated candidate.
- Taller cells change row height. They are feasibility controls and do not
  declare the 144 nm `gt2_6t` LEF site or provide a replacement site definition.

For W31 at 84 × 144 nm, y=24/48/96/120 VG sites intersect ACT or lack clearance,
and the y=72 site touches the separating gate cut. Both gate terminals have
zero legal contacts. CP-SAT reports `INFEASIBLE` for all four combinations
of N/P source/drain orientation. This establishes impossibility for the
specified original frame and track grid, not every conceivable layout.

At 84 × 168 nm, VG sites at y=72 and 96 nm become available and the router
finds a solution. Its smaller area does not make it compatible with 144 nm
standard-cell rows.

At 126 × 144 nm, the staggered arrangement is feasible with the original W31
devices, standard tracks, fully enclosed supply vias, and `CLASS CORE` LEF.
It is 25% narrower than the 168 × 144 nm full-dummy prototype, and 50% wider
than the original 84 nm cell. This uses two adjacent active gate columns;
it is not a solution to the original one-column split-gate frame. No global
minimum-width claim follows from these fixed-placement experiments.

## Explicit frame settings

The Python API returns the design schema; save it as JSON and pass `--design`:

```python
from cpcell.tech.gt2n_split_gate import SplitGateDecapProblem, default_design

design = default_design(sheet_width_nm=31, height_nm=144, vt="lvt")
design["n_flip"] = True       # Swap physical diffusion sides; preserve CDL.
result = SplitGateDecapProblem(design).solve(seconds=30)
print(result["status"], result["blocked_terminals"])
```

Settings include separate N/P sheet widths, threshold flavor, independent
diffusion orientations, arrangement, cut center, and explicit M0 track centers. Tracks
and cut coordinates use the half-nanometer manufacturing grid. JSON settings
replace CLI sizing/flavor defaults. Unknown fields, unsupported sizes, invalid
cuts, and malformed tracks fail validation.

Explicit tracks can model a deliberate deviation from the library grid.
For example, W13 with `[23, 48, 96, 121]` reproduces the 1 nm contact movement
needed for exactly 6 nm VG/ACT clearance. Reports flag it with
`standard_m0_grid: false`. W13 with the default tracks is infeasible in this
frame. Custom tracks are diagnostic and do not silently relax the default
24 nm grid.

Older JSON designs without `arrangement` retain `split` behavior and the same
routing graph. `default_design(arrangement="staggered")` defaults to an N source
on the left and a P source on the right; explicit orientation flags override
these positions without changing the schematic.

## Physical verification

The evaluator now lives in `chipforge_gt2n`. Run the following from that
checkout, pointing `--output-dir` at the CPCell output directory.

An optional standalone verifier requires GT2N and compatible KLayout decks:

```sh
uv run python -m evals.cpcell_split_gate \
  --output-dir ../CPCellUCSD/build/split_w31_168 --pdk-root ../GT2N \
  --drc-deck ../GT2N/experimental/cpcell/decapcc/w31/decks/gt2n_overlap.lydrc \
  --lvs-deck ../GT2N/experimental/cpcell/decapcc/w31/decks/gt2n_transistor.lylvs
```

It checks both the standalone cell and a 3×3 mirrored array against the
released four-terminal decap CDL. For 144 nm cells it also checks three mirrored
rows of released inverter / generated decap / released inverter. The only
accepted DRC markers are two
`GATE.2` edges whose coordinates must match the outer gate-array boundaries.
The DRC deck must contain the VG/ACT overlap guard. `checks.json` records
results and hashes; `report.json` records the verification status. This
released-CDL verifier currently requires equal N/P sheet widths.

Validated cases include all five W31 flavors at 126 × 144 nm (standalone,
mirrored, mixed), plus the W31 84 × 168 nm and shifted-track W13 controls
(standalone and mirrored). Neither ICV nor electrical
characterization is part of this feature's verification.

## Router API compatibility

`BandRouter.solve_result(seconds=30)` returns a structured solver status even
when no solution exists. `INFEASIBLE` and `UNKNOWN` results contain no routes or
objective. Invalid models still raise an error. Existing `BandRouter.solve()`
callers retain their previous behavior: they receive a feasible solution or
a `ValueError` naming the unsuccessful status.

Run the relevant regressions with:

```sh
python -m pytest tests/test_gt2n_split_gate.py tests/test_gt2n_thin.py \
  tests/test_band_matching.py tests/test_gt2n_sense_amp.py
```
