"""Shared reader for the CPCell single-height placement/routing result format."""

from typing import NamedTuple, List, Tuple

from src.utility.entity import Model


class TechData(NamedTuple):
    """Fields emitted by the CPCell result writer."""

    col: int
    track: int
    cp_pitch: float
    m0_pitch: float
    m1_pitch: float
    m2_pitch: float
    cp_width: float
    m0_width: float
    m1_width: float
    m2_width: float
    active_gap: float
    power_rail_thickness: float
    power_config: str
    io_pins: list


class TransistorData(NamedTuple):
    """Fields emitted by the CPCell result writer."""

    name: str
    x: float
    y: float
    flip: bool
    width: float
    height: float
    source_col: float
    source_net: str
    drain_col: float
    drain_net: str
    gate_col: float
    gate_net: str
    model: Model


class MetalData(NamedTuple):
    """Fields emitted by the CPCell result writer."""

    metal_0: int
    metal_1: int
    row_0: float
    row_1: float
    col_0: float
    col_1: float
    net: str


def parse_result(
    path: str,
) -> Tuple[TechData, List[TransistorData], List[TransistorData], List[MetalData]]:
    tech_params = {}
    pmos_transistors: List[TransistorData] = []
    nmos_transistors: List[TransistorData] = []
    metals: List[MetalData] = []

    section = None
    sections = {
        "Technology Parameters": "tech",
        "Placement Result": "place",
        "Routing Result": "route",
        "Cell Information": "cell",
    }
    with open(path) as f:
        for line_number, line in enumerate(f, 1):
            line = line.strip()
            if not line or set(line) == {"-"}:
                continue
            if line.startswith("**"):
                section = next(
                    (value for title, value in sections.items() if title in line), None
                )
                continue
            parts = line.split()
            if parts[0] in ("Name", "MET") or line == "IO Pins":
                continue
            try:
                if section == "tech":
                    key, value = line.split(maxsplit=1)
                    tech_params[key] = value
                elif section == "place":
                    if len(parts) != 13 or parts[3] not in ("F", "NF"):
                        raise ValueError(
                            "expected 13 placement fields and F/NF orientation"
                        )
                    model = Model(parts[12].lower())
                    transistor = TransistorData(
                        parts[0],
                        float(parts[1]),
                        float(parts[2]),
                        parts[3] == "F",
                        float(parts[4]),
                        float(parts[5]),
                        float(parts[6]),
                        parts[7],
                        float(parts[8]),
                        parts[9],
                        float(parts[10]),
                        parts[11],
                        model,
                    )
                    (
                        pmos_transistors if model == Model.PMOS else nmos_transistors
                    ).append(transistor)
                elif section == "route":
                    if len(parts) != 9 or parts[4] != "=>":
                        raise ValueError("expected MET ROW COL NET => MET ROW COL NET")
                    if parts[3] != parts[8]:
                        raise ValueError(
                            f"Net mismatch in routing data: {parts[3]} != {parts[8]}"
                        )
                    metals.append(
                        MetalData(
                            int(parts[0]),
                            int(parts[5]),
                            float(parts[1]),
                            float(parts[6]),
                            float(parts[2]),
                            float(parts[7]),
                            parts[3],
                        )
                    )
                elif section == "cell":
                    tech_params.setdefault("IO_PINS", []).extend(parts)
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc

    required = (
        "COL",
        "TRACK",
        "CPP",
        "M0P",
        "M1P",
        "M2P",
        "CP_WIDTH",
        "M0_WIDTH",
        "M1_WIDTH",
        "M2_WIDTH",
        "ACTIVE_GAP",
        "PWR_RAIL_THICKNESS",
        "PWR_CONFIG",
        "IO_PINS",
    )
    missing = [key for key in required if key not in tech_params]
    if missing:
        raise ValueError(f"{path}: missing result fields: {', '.join(missing)}")

    # build TechData, converting types
    td = TechData(
        col=int(tech_params["COL"]),
        track=int(tech_params["TRACK"]),
        cp_pitch=float(tech_params["CPP"]),
        m0_pitch=float(tech_params["M0P"]),
        m1_pitch=float(tech_params["M1P"]),
        m2_pitch=float(tech_params["M2P"]),
        cp_width=float(tech_params["CP_WIDTH"]),
        m0_width=float(tech_params["M0_WIDTH"]),
        m1_width=float(tech_params["M1_WIDTH"]),
        m2_width=float(tech_params["M2_WIDTH"]),
        active_gap=float(tech_params["ACTIVE_GAP"]),
        power_rail_thickness=float(tech_params["PWR_RAIL_THICKNESS"]),
        power_config=tech_params["PWR_CONFIG"],
        io_pins=tech_params["IO_PINS"],
    )

    return td, pmos_transistors, nmos_transistors, metals
