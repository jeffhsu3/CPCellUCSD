# CP-SAT based Standard Cell Layout Generator
This GitHub repo provides the Python version CP-SAT based standard cell layout generator.

The layout generation flow is shown in the below flow chart.

| <img src="figure/CPCellFlow.png" width=800px> |
|:--:|

## What's New
- Added the CV32E40P core as a third block-level benchmark, alongside the AES and JPEG Encoder.
- Added the Fixed Netlist/Independent Netlist Ablation Study, which checks whether synthesizing and routing a design under different gear-ratio libraries changes the resulting PPA. See [Fixed Netlist/Independent Netlist Ablation Study](evaluation/README.md#fixed-netlistindependent-netlist-ablation-study).
- Added the 2:1 GR (gear ratio) setting to the PPA sweep. See [Power, Performance and Area Under Different Gear Ratio Settings](evaluation/README.md#power-performance-and-area-under-different-gear-ratio-settings).
- Added PPA results for the AES and CV32E40P block-level benchmarks. See [Additional Block-Level Benchmark (AES)](evaluation/README.md#additional-block-level-benchmark-aes) and [Additional Block-Level Benchmark (CV32E40P)](evaluation/README.md#additional-block-level-benchmark-cv32e40p).

# Run Guide

### System/Hardware Prerequisites

- **OS:** Linux 
- **Python:** 3.10+ (Recommended but not strictly required)
- **CPU:** ≥ 4 cores recommended for parallel solving

### 1. Install dependencies

Using **[uv](https://docs.astral.sh/uv/)** (recommended):

```bash
# Create virtual environment and install all dependencies
uv sync

# Or create a venv explicitly:
uv venv
source .venv/bin/activate
uv pip install -e .
```

Alternatively, with traditional `pip`:

```bash
pip install -r requirements.txt
```

Key packages: `ortools ≥ 9.14`, `klayout ≥ 0.30`, `numpy`, `networkx`, `matplotlib`, `scikit-learn`.

To install the Python package and the `cpcell` / `cpcell-config` commands, run
`pip install -e .` (or `uv sync`). The import package is `cpcell`; for example,
`from cpcell.gds.gds_GT2N_SH import GT2NLayout`. Module commands use
`python -m cpcell` for solving and `python -m cpcell.utility.config` for config
generation. Existing callers using `src.*` must switch to `cpcell.*` and
reinstall the package to refresh the console commands.

### 2. Run the cell generation flow

```bash
# Step 1 — Generate per-cell JSON config files
make smtcell_config

# Step 2 — Solve (simultaneous place-and-route)
make smtcell_spnr

# Step 3 — Export GDS
make smtcell_gds
```

That's it. Your layouts will appear in `output/<library>/<height>/gds/`.

Both Python entry points (`cpcell.utility.config` and `cpcell.main`) accept absolute
or relative `--output_dir` paths and create their output directories as needed.
Wrappers can pass a new output path directly; no pre-created `config/`,
`result/`, `constraint/`, or `view/` directories are required. For Make, set
`OUT_DIR=/absolute/path/to/run` to use the same location across stages.

### GT2N GDS export

The GT2N backend consumes the same `.res` format as `gds_FinFET_SH.py` and
exports native GT2N layers. It supports single-height cells with four signal
tracks, a 42 nm contacted gate pitch, a 144 nm cell height, W13/W31 nanosheet
widths, and LVT/ELVT/ULVT/SVT/HVT flavors. The default is W13 LVT.

Generate results on the supplied GT2N grid, then select the GDS backend:

```bash
make smtcell_config CELL_PREFIX=GT2N CPP=42 M1P=42 CELL_NAME=INV_X1
make smtcell_spnr CELL_PREFIX=GT2N CPP=42 M1P=42 CELL_NAME=INV_X1
make gt2n_gds CELL_PREFIX=GT2N CPP=42 M1P=42 CELL_NAME=INV_X1
```

`TECH=FinFET` remains the placement model; `gt2n_gds` selects `GDS_TECH=GT2N`
only for export. The included `.layer` file supplies the GT2N routing pitches
and widths. Existing PROBE3 results must be regenerated on that grid; the
exporter rejects incompatible dimensions instead of rescaling them.

For an existing result, the equivalent Python entry point is:

```bash
python -m cpcell.gds.gds_GT2N_SH \
  --result_file output/GT2N_FinFET_2F_4T_4242OF0/SH/result/INV_X1.res \
  --subckt_name INV_X1 \
  --gds_file output/GT2N_FinFET_2F_4T_4242OF0/SH/gds/gt2n_w31_svt.gds \
  --nanosheet_width 31 --vt svt
```

For Make, use `GT2N_WIDTH=31 GT2N_VT=svt` and optionally `GDS_FILE=path.gds`.
Use separate files for different flavors: re-exporting a cell replaces that
cell by name while preserving the other cells and references in the library.
The Python API is `GT2NLayout(result_file, subckt_name, gds_file,
nanosheet_width=13, vt="lvt")` in `cpcell.gds.gds_GT2N_SH`.

The backend emits ACT/GATE/DUMMY/GCUT, SDCON, distinct VG/VSD contacts,
BPR/VBPR supply connections, M0–M2 routing, datatype-251 pin rectangles and
labels, and the GT2N `235/250` boundary. It uses a 0.5 nm database unit.
GT2N exports skip `relocatePin.py`, which uses PROBE3 layer numbers. The
existing LEF and evaluation scripts also target PROBE3 and are not a GT2N
signoff flow.

This is an **experimental export backend**, not a GT2N-aware constraint model.
It retains CPCell's four signal-track centers at 36, 60, 84, and 108 nm and
maps abstract transistor fingers to the selected physical nanosheet width.
The solver's FinFET device sizing and DRC constraints have not been replaced.
In particular, GT2N gate-contact clearance, SDCON spacing, route-end spacing,
and cell-abutment rules need PDK DRC/LVS validation; W31 gate contacts on the
inner tracks can violate ACT clearance. Basic input validation and checks
for shorts between exported metal nets do not establish DRC/LVS cleanliness.

Layer assignments and device dimensions are based on the
[GT2N PDK](https://github.com/azadnaeemi/GT2N), specifically
`techlib/gt2_techfile.layermap`, `icv_runset/Include`, and the released
`gt2_6t_inv_x1_w13_lvt` / `gt2_6t_inv_x1_w31_lvt` reference layouts.
The PDK is not required at export time.

Run the geometry, library-update, and CLI checks with:

```bash
python -m unittest discover -s tests -v
```

### 3. Debugging tools

| Command | Purpose |
|:--|:--|
| `make viewcell` | Generate a `.png` canvas visualization with placement, routing, and net labels |
| `make check_duplicate_vars` | Detect accidental duplicate Boolean variable names in CP-SAT |
---

## Workflow Overview

```
┌──────────────┐     ┌───────────────┐     ┌──────────────┐     ┌───────────┐
│  .layer file │────▶│ smtcell_config│────▶│ smtcell_spnr │────▶│smtcell_gds│
│  (global)    │     │  (per-cell    │     │  (CP-SAT     │     │ (export)  │
│              │     │   .json)      │     │   solving)   │     │           │
└──────────────┘     └───────────────┘     └──────┬───────┘     └───────────┘
                                                  │
                                           FEASIBLE / OPTIMAL ?
                                           ├─ Yes → proceed to GDS
                                           └─ No  → disable speedups
                                                    & retry, or adjust
                                                    canvas size
```

## Configuration Reference

SMTCell uses two configuration files per run:

### Layer Configuration (`.layer`)

Defines the standard-cell canvas — applied **globally** across a technology and architecture. Each layer is either a **metal** or a **via**:

```jsonc
// Metal layer
"M1": {
  "layer_type": "metal",
  "layer_name": "M1",
  "direction": "V",        // "V" (vertical) or "H" (horizontal)
  "offset": 0.0,
  "pitch": 45.0,
  "width": 2.0,
  "info": "M1 in PROBE3"
}

// Via layer
"V0": {
  "layer_type": "via",
  "layer_name": "V0",
  "upper_layer": "M1",
  "lower_layer": "M0",
  "info": "V0 in PROBE3"
}
```

Layers must be ordered **bottom → top**, with a via between each adjacent metal pair.

### Cell Configuration (`.json`)

Generated automatically by `make smtcell_config`, then customizable per cell. Parameters fall into four groups:

<summary><strong>🔧 TECH — Technology Rules</strong></summary>

| Parameter | Type | Description |
|:--|:--:|:--|
| `minimum_gate_cut_length` | int | Min gate-cut length in #CPP. Diffusion breaks count as legal cuts. |
| `via_c2c_rule` | dict | Center-to-center via separation between layer pairs. |
| `mar_c2c_rule` | dict | Center-to-center minimum-area rule per metal layer. |
| `eol_c2c_rule` | dict | Center-to-center end-of-line rule per metal layer. |
| `insert_num_db` | int | Extra CPP columns to enlarge the canvas (helps with INFEASIBLE results). |
| `allow_unequal_rows` | bool | Allow different PMOS/NMOS finger counts and size the canvas from the larger row. Default: `false`. |
| `enforce_diffusion_alignment` | bool | Require identical diffusion-break columns in both rows. Set `false` for independent breaks. Default: `true`. |
| `MPO` | int | Minimum pin opening at M2. Recommended: 2 for 4T. |
| `m0_pin_separation` | bool | Enforce that no two M0 pins share the same row. Reduces coupling risk between adjacent power/signal wires. Default: `false`. |
| `m0_pin_extension` | bool/int | Extend each M0 pin outward to `vacancy_edges` empty track segments. Improves routability for M0-pinned nets. Default: `true`, `vacancy_edges: 2`. |

<summary><strong>🧮 SOLVER — CP-SAT Solver Settings</strong></summary>

| Parameter | Type | Description |
|:--|:--:|:--|
| `seed` | int | Random seed for deterministic results. |
| `model_preset` | int | Solver hyperparameter preset. Default `2` (disables LP relaxation for Boolean-heavy models). |
| `num_search_workers` | int | CPU cores for parallel solving. Recommended: 4–10. |
| `use_relative_gap` | bool/float | Early stopping when optimality gap ≤ threshold. 0.5–1% for large DFFs. |

<summary><strong>🚀 SPEEDUP — Acceleration Settings</strong></summary>

| Parameter | Type | Description |
|:--|:--:|:--|
| `use_placement_order_for_identical_transistors` | bool | Pre-determine placement order for identical transistors (high-D cells). |
| `use_break_symmetry_for_placement` | bool | Break placement symmetry to prune the search space. |
| `inject_cluster` | obj | Automatic transistor clustering for large DFFs. Safe at cluster size 2; sizes 4–6 give more speedup but risk INFEASIBLE. |

### Unequal rows and independent diffusion breaks

For a single-height cell with different PMOS/NMOS finger counts, set both
options in its generated cell JSON:

```json
"allow_unequal_rows": {"value": true},
"enforce_diffusion_alignment": {"value": false}
```

Counts refer to the fingers produced by netlist parsing, including `nfin`
splitting. Automatic canvas sizing uses the larger row and adds the usual
`insert_num_db` allowance. The smaller row can leave columns empty without
inserting extra transistors. Here “unequal rows” means different device counts,
not different row heights or a multi-height layout.

Independent diffusion breaks also work with equal device counts. A break
blocks device-layer routing only in its own row; a full-column break requires
both rows to be empty. The first column may contain a transistor in just one
row. Existing diffusion-sharing, gate-cut-length, metal, and via constraints
still apply, so additional canvas space may be necessary.

Old JSON configs keep the default aligned-row behavior. Unequal counts with
alignment enabled, or without `allow_unequal_rows`, produce an explanatory
error before model construction. Python callers can also set these options on
`FinFET_Tech`; explicit cell JSON values take precedence and do not modify the
shared technology object.

The regression fixture `tests/fixtures/sram6t.cdl` exercises a two-PMOS,
four-NMOS topology with both options enabled and `insert_num_db=2`. It is a
solver test using abstract device sizes, not an electrically characterized
SRAM. Configurable array boundary ports and placement rules are described
below; per-device GT2N sizing remains separate work.
The legacy FinFET GDS exporter still assumes paired P/N placements; use the
GT2N backend for its support of separate row placement, subject to the GT2N
export limitations described above.


### Boundary ports and placement constraints

Per-cell `boundary_ports` and `placement_constraints` options support multiple
named ports per signal, selectable sides/layers/tracks, and fixed, ordered,
aligned, or mirrored transistor placement. Both default to empty lists.
GT2N GDS export extends the solved port attachments to the physical cell edges.

See [the constraint reference](docs/constraints.md) for coordinate conventions,
JSON examples, and a routed six-port SRAM fixture.

The [GT2N standard-row 6T reproduction](examples/gt2n_sram/README.md) fixes the
reference placement and lets CPCell route a 210 × 144 nm cell. Both w13 and w31
versions match the reference's cell/array DRC findings, pass LVS, and pass its
nominal SPICE checks on the LVS-matched schematic.

The [GT2N thin 6T reproduction](examples/gt2n_thin/README.md) adds a fixed
four-band N/P/P/N profile and CP-SAT routing for the backside-powered reference.
The n31/p31, n13/p13, and n31/p13 variants reproduce 84 × 292/288 nm footprints,
pass cell/array LVS and nominal SPICE checks, and match reference DRC findings.
Run `.venv/bin/python examples/gt2n_thin/reproduce.py` to generate them.

The [GT2N logic extension](examples/gt2n_logic/README.md) generates NAND4,
NOR4, AOI221 and OAI221 with automatic placement/routing in a 144 nm row.
It exports GDS, CDL, LEF and Verilog; `--spice` adds nominal schematic-only
Liberty characterization after cell and mirrored-abutment DRC/LVS checks.
Run `.venv/bin/python examples/gt2n_logic/reproduce.py` to generate the batch.


---

## Project Structure

```
workdir/
├── input/
│   ├── cdl/                  # Input netlists (.cdl)
│   ├── config/               # Layer configuration files (.layer)
│   ├── pin_input_collection.json
│   └── pin_output_collection.json
├── output/<library>/<height>/
│   ├── config/               # Per-cell JSON configs
│   ├── constraint/           # Constraint logs (debug)
│   ├── gds/                  # Generated layouts
│   ├── logs/                 # Solver logs
│   ├── result/               # .res (solution) + .var (variables)
│   └── view/                 # Canvas visualizations (.png)
├── cpcell/
│   ├── core/                 # Constraint modeling
│   ├── gds/                  # GDS generation
│   ├── solve/                # CP-SAT solver wrappers
│   ├── tech/                 # Technology database
│   ├── utility/              # Config, helpers
│   └── visual/               # Canvas visualization
├── evaluation/
│   ├── libgen/               # Library characterization (LVS/PEX, Liberty, DB, NDM)
│   └── blockeval/            # Block-level P&R and IR-drop evaluation
├── miscellaneous/            # KLayout .lyp tech files
├── Makefile
├── pyproject.toml
└── requirements.txt
```

## Evaluation

Post-layout evaluation scripts live under [`evaluation/`](evaluation/). The flow covers library characterization and block-level PPA / IR-drop assessment using the JPEG Encoder benchmark.

| Stage | Tool | Output |
|:--|:--|:--|
| LVS & PEX | Cadence Pegasus / Quantus | RC-extracted netlists (`.sp`) |
| Characterization | Cadence Liberate | Liberty files (`.lib`) |
| DB conversion | Synopsys Library Compiler | `.db` for ICC2 |
| Synthesis & P&R | Synopsys DC / IC Compiler II | PPA metrics |
| IR-drop | Cadence Voltus | IR-drop maps |

See [`evaluation/README.md`](evaluation/README.md) for full setup and commands.

---

# Past & Related Repository
- **Previous Work: Z3 Solver + FinFET Based**
  - SMT-based-STDCELL-Layout-Generator \[[Link](https://github.com/ckchengucsd/SMTCellUCSD)\]
  - SMT-based-STDCELL-Layout-Generator-Multi-Height \[[Link](https://github.com/ckchengucsd/SMTCellUCSD-MH)\]
- **PROBE3.0**
  - Design-Technology pathfinding framework incorporating the SMT based cell layout generator \[[Link](https://github.com/ABKGroup/PROBE3.0)\]

# Knowledge Reference
- Park, Dong Won Dissertation: [Logical Reasoning Techniques for Physical Layout in Deep Nanometer Technologies](https://escholarship.org/content/qt9mv5653s/qt9mv5653s.pdf)
- Ho, Chia-Tung Dissertation: [Novel Computer Aided Design (CAD) Methodology for Emerging Technologies to Fight the
Stagnation of Moore's Law](https://escholarship.org/content/qt2ts172zd/qt2ts172zd.pdf)
- C.-K. Cheng, C.-T. Ho, D. Lee and B. Lin, "Multi-row Complementary-FET (CFET) Standard Cell Synthesis Framework using Satisfiability Modulo Theories (SMT)", IEEE Journal of Exploratory Solid-State Computational Devices and Circuits, 2021, Open Access. \[[Paper](https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9390403)\]
- C.-K. Cheng, C.-T. Ho, D. Lee and D. Park, "A Routability-Driven Complimentary-FET (CFET) Standard Cell Synthesis Framework using SMT", ACM/IEEE Int. Conf. on Computer-Aided Design, pp. 1-8, 2020. \[[Paper](https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9256570)\]
- C.-K. Cheng, A. B. Kahng, B. Kang, S. Kang, J. Lee and B. Lin, "SO3-Cell: Standard Cell Layout Synthesis Framework for Simultaneous Optimization of Topology, Placement, and Routing", Proc. ACM/IEEE Intl. Conf. on Computer-Aided Design, 2025. \[[Paper](https://vlsicad.ucsd.edu/Publications/Conferences/418/c418.pdf)\] \[[Slides](https://view.officeapps.live.com/op/view.aspx?src=https%3A%2F%2Fvlsicad.ucsd.edu%2FPublications%2FConferences%2F418%2Fc418.pptx&wdOrigin=BROWSELINK)\]
- C.-K. Cheng, A. B. Kahng, B. Lin, Y. Wang and D. Yoon, "Gear-Ratio-Aware Standard Cell Layout Framework for DTCO Exploration", Proc. ACM/IEEE International Workshop on System-Level Interconnect Problems and Pathfinding, 2023. \[[Paper](https://vlsicad.ucsd.edu/Publications/Conferences/402/c402.pdf)\]
- D. Park, I. Kang, Y. Kim, S. Gao, B. Lin and C.-K. Cheng, "ROAD: Routability Analysis and Diagnosis Framework Based on SAT Techniques", ACM/IEEE Int. Symp. on Physical Design, pp. 65-72, 2019. \[[Paper](https://dl.acm.org/doi/pdf/10.1145/3299902.3309752)\] \[[Slides](https://cseweb.ucsd.edu//~kuan/talk/placeroute18/routability.pdf)\]
- D. Park, D. Lee, I. Kang, S. Gao, B. Lin and C.-K. Cheng, "SP&R: Simultaneous Placement and Routing Framework for Standard Cell Synthesis in Sub-7nm", IEEE Asia and South Pacific Design Automation, pp. 345-350, 2020. \[[Paper](https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9045729)\] 
- D. Lee, C.-T. Ho, I. Kang, S. Gao, B. Lin and C.-K. Cheng, "Many-Tier Vertical Gate-All-Around Nanowire FET Standard Cell Synthesis for Advanced Technology Nodes", IEEE Journal of Exploratory Solid-State Computational Devices and Circuits, 2021, Open Access. \[[Paper](https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9454552)\]
- A. B. Kahng, S. Kang, S. Kim, J. Lee and D. Yoon, "Au-MEDAL: Adaptable Grid Router with Metal Edge Detection And Layer Integration", Proc. Asia and South Pacific Design Automation Conference, 2026. \[[Paper](https://vlsicad.ucsd.edu/Publications/Conferences/420/c420.pdf)\] \[[Slides](https://view.officeapps.live.com/op/view.aspx?src=https%3A%2F%2Fvlsicad.ucsd.edu%2FPublications%2FConferences%2F420%2Fc420.pptx&wdOrigin=BROWSELINK)\]
