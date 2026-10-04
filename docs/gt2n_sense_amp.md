# GT2N current-latched sense amplifier

CPCell can generate the 294 × 288 nm, W31 LVT sense amplifier used by
`chipforge_gt2n`. The dedicated `SenseAmpProblem` adapter fixes the device frame
and lets CP-SAT choose the metal routes and contact access sites. Generation
does not import the sibling repository or copy its routed metal.

```sh
python examples/gt2n_sense_amp/reproduce.py
python examples/gt2n_sense_amp/reproduce.py --spice
```

The first command requires only CPCell's dependencies. The second also runs
platform DRC, transistor LVS against both the generated and reference CDL, and
the reference transient SPICE benches. `--verify` runs just DRC/LVS. These
optional checks default to sibling `chipforge_gt2n` and `GT2N` checkouts and the
reference repository's `.venv/bin/python`. Override them with `--reference-repo`,
`--pdk-root`, and `--reference-python`; the latter environment must have the
reference generator dependencies, KLayout, and access to Xyce for SPICE.

Outputs default to `build/gt2n_sense_amp/`; `--output-dir` accepts absolute paths
and creates missing directories. Outputs include `cell.gds`, `cell.cdl`,
`solution.json`, `report.json`, and a SHA-256 `manifest.json`. Optional checks
also write `checks.json`, the reference CDL, and KLayout DRC/LVS databases.
Reports distinguish generation from physical and electrical verification.

## Exact device geometry

This initial profile has twelve explicit W=31 nm, L=14 nm LVT fingers. The tail
and each precharge device use two parallel fingers, recorded in `finger_groups`.
Six hard-coded mirrored device pairs preserve the reference's shared diffusion
and gate-cut arrangement. All six signal ports are on M1 in the upper row.

This is a fixed placement and sizing template for this topology. The adapter
rejects changes to width, length, threshold flavor, finger count, placement, or
connectivity that it cannot physically implement. It bypasses the single-height
GT2N exporter, whose sizing assumptions do not apply to this cell. Arbitrary
analog netlists, automatic matched placement, and other sizes are not supported
by this profile.

## Matching constraints

`BandRouter(..., matching=...)` accepts reusable hard routing constraints:

| Option | Constraint |
| --- | --- |
| `mirror_x` | Reflection axis in integer half-nanometre coordinates. |
| `mirror_nets` | Pairs of net names whose selected routing rectangles must reflect about the axis; pairing a net with itself imposes symmetry. |
| `mirror_contact_nets` | Reflection restricted to edges containing VG or VSD access geometry. |
| `balanced_nets` | Equal routing-grid centerline lengths on each metal and equal counts of each via type. |

Routing nodes use `(layer, y, x)` coordinates; intrinsic conductors use
`("T", name)`. Boundary attachments may use opaque nodes. Length metrics cover
grid-to-grid segments, excluding landing extensions and fixed boundary stubs;
those shapes still participate in spacing and reflection checks. The adapter
provides equal-length output pin stubs. Matching lengths is not an extracted
resistance/capacitance constraint.

For the sense amplifier, the axis is x=147 nm (`mirror_x=294`). SA/SAN are full
mirror pairs; SAE and the common tail net T are self-mirrored. QA/QAN contacts
are mirrored, with equal per-layer wire lengths and via counts. Cross-coupled
QA/QAN routing is not fully mirrored: it uses different routing rows to avoid
shorts. The generated regression layout also has equal merged metal area and
perimeter per layer for QA/QAN; these are checked in tests, not separately
constrained by the solver. SAPRECHN is a shared control net with a left-side pin.

Every selected routing component must connect to its net's root. This prevents
detached loops from satisfying a length balance. Export independently checks
connectivity, geometry spacing, and matching before writing GDS. The router
supports separate horizontal, vertical, and Euclidean corner spacing thresholds
for adapters that need directional metal spacing.

## Verification scope

Physical checks permit the reference platform's known `GATE.2` dummy-gate and
`SDCON.ACT.1` contact-extension exceptions; the output is not zero-marker DRC.
LVS must match both schematics. SPICE compares generated and reference delay
and sensing polarity at +20, +50, +100, and −50 mV input differences, plus input
offset after a +20 mV threshold shift on INA. The bench uses 0.7 V, 25 °C, and
1 fF output loads.

These simulations use the generated schematic after LVS, without extracted
routing parasitics. Geometric balance is useful evidence, but does not establish
matched extracted RC, PVT coverage, statistical mismatch yield, or array-level
integration performance. Those remain subsequent analog verification work.
