# Reproduce the GT2N thin 6T with CPCell

This example reproduces the **backside-powered** thin 6T reference in
`chipforge_gt2n/scripts/gt2n_thin_bitcell.py`. CPCell builds the devices from the
CDL and fixed placement, derives the shared gate/diffusion conductors, and uses
CP-SAT to select contact sites and M0/M1 routes. Generation is independent of
the reference repository; its generator supplies comparison layouts and the
mirrored array scaffold during verification.

The thin cell needs four device bands rather than the standard-cell solver's
single N/P pair. The new `cpcell.core.band_router.BandRouter` handles routing
between fixed conductor terminals, with a GT2N-specific `ThinProblem` adapter.
This is a **fixed-placement reproduction**, with optimized routing. It does not
search arbitrary multi-band placements, optimize cell dimensions, or implement
the reference's frontside-power variant. The existing single-height solver and
its `.res` format remain separate.

## Placement and ports

Bands are numbered bottom to top; slots 0/1 put gates at x=21/63 nm.

| Band | Type | Slot 0 | Slot 1 |
| --- | --- | --- | --- |
| 0 | N-A | PGL: BL–Q, gate WL | PDL: Q–vss, gate QB |
| 1 | P-A | Dummy | PUL: Q–vdd, gate QB |
| 2 | P-B | PUR: vdd–QB, gate Q | Dummy |
| 3 | N-B | PDR: vss–QB, gate Q | PGR: QB–BLN, gate WL |

Diffusion and its contacts continue through the left/right boundaries. WL gate
contacts are shared across the top/bottom boundaries. Backside rails are vss
at y=0/H and vdd at H/2. The wordline crosses the cell vertically on M1 at
x=42 nm; BL/BLN cross horizontally on M0 through the N bands. No M2 is used.

`constraints.json` contains exact expanded transistor names and `band`, `slot`,
`flip` placements. With `flip: false`, CDL drain/source map to left/right.
Its `ports` records specify `name`, `net`, `side`, `layer`, and required `track`:

- M0 supports left/right ports. Tracks 0–5 are at
  `[0, N-A centre, Q contact, QB contact, N-B centre, H]`.
- M1 supports bottom/top ports. Tracks 0–2 are at x=`[21, 42, 63]` nm.
- Ports must name existing signal I/Os and occupy distinct nodes. Contradictory
  placements or unreachable ports fail rather than being silently relaxed.

This adapter's band/slot schema is specific to the thin profile; it is not the
single-height solver's `placement_constraints` schema. The reference topology
requires the two inward pull-ups and the displayed 2/1/1/2 device pattern.

## Generate

From this repository, with its Python dependencies installed:

```bash
.venv/bin/python examples/gt2n_thin/reproduce.py
```

The default variants are **31x31, 13x13, 31x13**, where each pair is the NMOS and
PMOS sheet width in nm. All use LVT and 14 nm gates. `--variants 31x13` selects
one variant; `--timeout 60` changes each routing solve's time limit;
`--output-dir PATH` changes the output location and creates missing parents.
Absolute output paths and running the script from another working directory
are supported. The optional 13x31 variant is also supported by the adapter and
covered by routing/export regressions; the external results below cover the
three default variants.

Outputs default to `build/gt2n_thin_reference/`:

- `<variant>/solution.json`: self-contained fixed placement, ports, selected
  routing edges, physical dimensions, solver status, cost, and solve time.
- `<variant>/cpcell.gds`: native GT2N layers, 0.5 nm DBU, boundary and pin markers.
- `<variant>/cpcell.cdl`: physically sized schematic from the input devices.
- `report.json`: generation/verification status, input hashes, and per-variant
  results. Verification is explicitly `not_run` without `--verify`/`--spice`.

The CDL input uses CPCell's abstract two-fin width (46 nm) for one solver device
per transistor. Physical sheet widths are selected independently for N and P.

An existing solution can be exported again without solving:

```bash
.venv/bin/python -m cpcell.gds.gds_GT2N_thin \
  --result-file build/gt2n_thin_reference/31x31/solution.json \
  --gds-file build/gt2n_thin_reference/31x31/reexport.gds
```

The exporter rebuilds the candidate graph and independently checks connectivity,
contact ownership/count, node exclusivity, and inter-net geometry before writing.
This JSON uses format `cpcell.fixed_bands.v1`; it is not a single-height `.res`.

## DRC, LVS and SPICE

Use an environment containing both projects' dependencies, plus KLayout, the
GT2N PDK, and Xyce. In this workspace:

```bash
PYTHONDONTWRITEBYTECODE=1 \
../chipforge_gt2n/.venv/bin/python examples/gt2n_thin/reproduce.py \
  --reference-repo ../chipforge_gt2n --spice
```

`--verify` runs DRC/LVS alone; `--spice` also runs nominal hold/read/write checks.
Discovery uses the reference project's mechanisms (`GT2N_ROOT`, `XYCE`, etc.).
Xyce's MPI runtime needs local daemon/socket access. Outputs stay in the chosen
output directory; the sibling repository is read-only input.

Verification checks the CPCell cell against its own physical-size CDL and the
independent reference CDL. It tiles the **actual CPCell GDS cell** in a mirrored
**3 × 4** array with the reference's edge termination strips. Array LVS is
flattened because shared diffusion merges across instances. DRC runs the full
platform deck and requires exact rule-count parity with the reference, for
both the cell and array. Detailed reports and reference source hashes are saved.

All three default variants route to **OPTIMAL**, pass cell/array LVS, and match
these reference DRC counts:

| Scope | GATE.2 | ACT.2 | ACT.6 | SDCON.ACT.1 |
| --- | ---: | ---: | ---: | ---: |
| Single cell | 2 | 2 | 6 | 10 |
| Mirrored 3 × 4 array | 2 | 0 | 0 | 72 |

The isolated-cell ACT findings disappear when neighboring cells and termination
strips continue the bands. The remaining findings are the reference project's
known gate-grid ends and SDCON enclosure convention. These results establish
reference parity under this deck, not zero DRC findings or proprietary signoff.

Nominal Xyce results at **TT, 0.7 V, 25 °C**:

| N/P width | Footprint | Area | Hold SNM | Read SNM | BL write trip |
| --- | --- | ---: | ---: | ---: | ---: |
| 31/31 nm | 84 × 292 nm | 0.024528 µm² | 250 mV | 102 mV | 266 mV |
| 13/13 nm | 84 × 288 nm | 0.024192 µm² | 256 mV | 106 mV | 258 mV |
| 31/13 nm | 84 × 288 nm | 0.024192 µm² | 245 mV | 76 mV | 366 mV |

These footprints use about **19–20% less area** than the 210 × 144 nm standard
cell. Mixed sizing improves nominal writability while reducing read margin.
The acceptance checks require hold SNM >200 mV, read SNM >50 mV, symmetric read
lobes, positive read-retention/write margins, no upset in repeated-read tests,
and finite write times. Exact criteria and all measured values are in the
script and report.

SPICE uses the **LVS-matched reference schematic** with estimated 0.25 fF
storage-node loads. It does not extract RC from the generated layout or measure
mismatch yield. The regression suite exercises all four N/P width combinations,
invalid placement/ports, physical routing conflicts, corrupt solution rejection,
and standalone execution without the sibling repository.
