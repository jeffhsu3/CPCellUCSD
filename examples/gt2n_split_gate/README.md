# Split-gate GT2N decap

See [the split-gate adapter documentation](../../docs/gt2n_split_gate.md) for
geometry, feasibility scope, configuration, and physical verification.

```sh
python examples/gt2n_split_gate/reproduce.py --output-dir build/split_w31_144
python examples/gt2n_split_gate/reproduce.py --height-nm 168 --output-dir build/split_w31_168
python examples/gt2n_split_gate/reproduce.py --arrangement staggered --output-dir build/staggered_w31_144
```

The original footprint returns exit code 2 (`INFEASIBLE` under the default
constraints). The taller control and 126 × 144 nm staggered candidate export
layouts and return 0. The staggered candidate keeps the original row height,
uses adjacent N/P gate columns, and is 25% narrower than the 168 nm prototype.
