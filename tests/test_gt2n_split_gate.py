"""Split-gate connectivity, physical contact access and scoped solver outcomes."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import klayout.db as db
import pytest

from cpcell.core.band_router import BandRouter, RouteEdge, Shape
from cpcell.gds.gds_GT2N_SH import LAYERS
from cpcell.gds.gds_GT2N_split_gate import export_split_gate, write_lef
from cpcell.tech.gt2n_split_gate import SplitGateDecapProblem, default_design

ROOT = Path(__file__).resolve().parents[1]


def access(result, terminal):
    return next(a for a in result["access"] if a["terminal"] == terminal)["candidates"]


@pytest.mark.parametrize(
    "n_flip,p_flip", [(False, False), (False, True), (True, False), (True, True)]
)
def test_original_w31_has_no_gate_access_for_any_diffusion_orientation(n_flip, p_flip):
    design = default_design()
    design.update(n_flip=n_flip, p_flip=p_flip)
    p = SplitGateDecapProblem(design)
    result = p.solve()
    assert result["status"] == "INFEASIBLE"
    assert set(result["blocked_terminals"]) == {"gate_n", "gate_p"}
    assert "routes" not in result and "objective" not in result
    for terminal in ("gate_n", "gate_p"):
        assert not any(c["allowed"] for c in access(result, terminal))
        center = next(c for c in access(result, terminal) if c["y_nm"] == 72)
        assert "touches_gate_cut_or_dummy" in center["rejected_by"]
    assert {a["terminal"] for a in result["access"]} == {
        "drain_n",
        "drain_p",
        "gate_n",
        "gate_p",
    }
    assert len([t for t in p.intrinsic if t[2] == "VG"]) == 2


def test_gate_clearance_rejects_five_nm_and_accepts_exact_six_nm():
    # Reproduce the W13 PR's 1 nm VG movement. This catches KLayout's
    # sized(dx, mode) versus sized(dx, dy, mode) overload ambiguity.
    design = default_design(13)
    result = SplitGateDecapProblem(design).solve()
    assert result["status"] == "INFEASIBLE"
    candidate = next(c for c in access(result, "gate_n") if c["y_nm"] == 24)
    assert "VG_ACT_clearance_below_6nm" in candidate["rejected_by"]
    design["m0_tracks_nm"] = [23, 48, 96, 121]
    result = SplitGateDecapProblem(design).solve()
    assert result["status"] == "OPTIMAL"
    assert [c["y_nm"] for c in access(result, "gate_n") if c["allowed"]] == [23]
    assert [c["y_nm"] for c in access(result, "gate_p") if c["allowed"]] == [121]


@pytest.mark.parametrize("vt", ["lvt", "elvt", "ulvt", "svt", "hvt"])
@pytest.mark.parametrize("arrangement,height", [("split", 168), ("staggered", 144)])
def test_feasible_profiles_export_real_isolated_gates(
    vt, arrangement, height, tmp_path
):
    design = default_design(31, height, vt, arrangement)
    p = SplitGateDecapProblem(design)
    result = p.solve()
    assert result["status"] == "OPTIMAL"
    assert not result["blocked_terminals"]
    result = json.loads(json.dumps(result))
    layout = export_split_gate(result, tmp_path / "cell.gds")
    cell = layout.cell(p.name)

    def reg(name):
        return db.Region(cell.shapes(layout.layer(LAYERS[name], 0))).merged()

    segments = (reg("GATE") - reg("GCUT") - reg("DUMMY")).merged()
    assert segments.count() == 2
    assert {polygon.bbox().height() for polygon in reg("ACT").each()} == {62}
    assert (reg("VBPR") - reg("BPR")).is_empty()
    assert (reg("VBPR") - reg("SDCON")).is_empty()
    assert (reg("VG").sized(0, 12, 2) & reg("ACT")).is_empty()
    assert reg("VG").interacting(reg("GCUT") + reg("DUMMY")).is_empty()
    for polygon in reg("M0").each():
        b = polygon.bbox()
        assert (b.bottom + b.top) % 96 == 0
    write_lef(result, layout, tmp_path / "cell.lef")
    lef = (tmp_path / "cell.lef").read_text()
    assert "CLASS CORE ;" in lef and "SPACER" not in lef
    assert ("SITE gt2_6t" in lef) == (height == 144)
    assert f"FOREIGN {p.name} 0 0 ;" in lef
    if height == 144:
        tag = "sg" if arrangement == "staggered" else "sp"
        assert p.name == f"gt2_6t_decapcc_{tag}_w31_{vt}"
    else:
        assert p.name == f"gt2n_{arrangement}_decap_w31_h{height}_{vt}"
    assert f"SIZE {p.width / 1000:g} BY {height / 1000:g}" in lef
    if arrangement == "staggered":
        assert p.width == 126
        assert {polygon.bbox().center().x for polygon in segments.each()} == {84, 168}
        assert reg("GCUT").count() == 2  # Boundary cuts only.
        # Partial dummy markers keep 4 nm from the centre-track VG contacts.
        assert (reg("VG").sized(0, 8, 2) & reg("DUMMY")).is_empty()
        # Both active bands span the cell, so one-pitch neighbours merge
        # their 42 nm active with ours; the diffusion beyond each isolation
        # gate is contacted and tied to its rail.
        for polygon in reg("ACT").each():
            assert (polygon.bbox().left, polygon.bbox().right) == (0, 2 * p.width)
        assert reg("SDCON").count() == 6 and reg("VBPR").count() == 4
        assert (reg("VBPR") - reg("SDCON")).is_empty()
    assert p.schematic().count("W=0.031u L=0.014u M=1") == 2


def test_legacy_design_without_arrangement_reconstructs_identical_routes():
    current = default_design(31, 168)
    legacy = deepcopy(current)
    legacy.pop("arrangement")
    a, b = SplitGateDecapProblem(current), SplitGateDecapProblem(legacy)
    assert a.design == b.design
    assert a.edges == b.edges
    b.validate(a.solve()["routes"])


def test_infeasible_or_corrupt_result_cannot_overwrite_layout(tmp_path):
    path = tmp_path / "cell.gds"
    path.write_bytes(b"previous layout")
    bad = SplitGateDecapProblem().solve()
    with pytest.raises(ValueError, match="infeasible or unproven"):
        export_split_gate(bad, path)
    good = SplitGateDecapProblem(default_design(31, 168)).solve()
    bad = deepcopy(good)
    bad["height_nm"] = 144
    with pytest.raises(ValueError, match="dimensions"):
        export_split_gate(bad, path)
    bad = deepcopy(good)
    bad["routes"] = [r for r in good["routes"] if r["net"] != "net7"]
    with pytest.raises(ValueError, match="Disconnected"):
        export_split_gate(bad, path)
    assert path.read_bytes() == b"previous layout"


@pytest.mark.parametrize(
    "change",
    [
        {"cut_center_nm": 57},
        {"m0_tracks_nm": [24, 24, 48]},
        {"height_nm": 145},
        {"n_width_nm": 30},
        {"n_flip": 1},
        {"m0_tracks_nm": [float("nan")]},
        {"cut_center_nm": True},
        {"arrangement": "unknown"},
    ],
)
def test_unsupported_or_malformed_frame_is_rejected(change):
    design = default_design()
    design.update(change)
    with pytest.raises(ValueError):
        SplitGateDecapProblem(design)


def test_status_api_preserves_legacy_exception_and_timeout_distinction():
    router = SplitGateDecapProblem().router
    assert router.solve_result()["status"] == "INFEASIBLE"
    with pytest.raises(ValueError, match="INFEASIBLE"):
        router.solve()
    result = SplitGateDecapProblem(default_design(31, 168)).solve(1e-9)
    assert result["status"] == "UNKNOWN"
    assert "routes" not in result
    for timeout in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="finite and positive"):
            router.solve_result(timeout)


def test_routing_conflict_reports_infeasibility_without_missing_contacts():
    # Both terminals have access but different nets would share physical M0.
    terminals = {net: [("T", net)] for net in ("a", "b")}
    ports = {net: [(1, 0, i)] for i, net in enumerate(terminals)}
    edges = [
        RouteEdge(
            terminals[net][0], ports[net][0], (Shape("M0", (0, 0, 24, 24)),), 1, net
        )
        for net in terminals
    ]
    assert BandRouter(edges, terminals, ports).solve_result()["status"] == "INFEASIBLE"


def test_cli_reports_infeasibility_and_removes_stale_exports(tmp_path):
    out = tmp_path / "out"
    cmd = [
        sys.executable,
        str(ROOT / "examples/gt2n_split_gate/reproduce.py"),
        "--output-dir",
        str(out),
    ]
    completed = subprocess.run(
        cmd + ["--arrangement", "staggered"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert (out / "cell.gds").exists()
    assert json.loads((out / "report.json").read_text())["footprint_nm"] == [126, 144]
    (out / "checks.json").write_text('{"status":"passed"}')
    completed = subprocess.run(
        cmd, cwd=tmp_path, capture_output=True, text=True, timeout=30
    )
    assert completed.returncode == 2, completed.stderr
    report = json.loads((out / "report.json").read_text())
    assert report["solver_status"] == "INFEASIBLE"
    assert report["verification_status"] == "not_run"
    assert report["standard_m0_grid"]
    assert report["footprint_nm"] == [84, 144]
    assert all(
        not (out / name).exists()
        for name in ("cell.gds", "cell.cdl", "cell.lef", "checks.json")
    )
    manifest = json.loads((out / "manifest.json").read_text())
    assert set(manifest) == {"solution.json", "report.json"}
