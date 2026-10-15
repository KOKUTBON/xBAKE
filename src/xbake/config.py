"""
Configuration management with JSON persistence and validated runtime updates.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config.json"


@dataclass
class CameraConfig:
    source: Union[int, str] = 0
    brightness: int = 0
    contrast: int = 32
    saturation: int = 60
    hue: int = 0
    gamma: int = 100
    auto_wb: int = 0
    target_width: int = 640
    target_height: int = 480


@dataclass
class VisionConfig:
    lower_hsv: List[int] = field(default_factory=lambda: [0, 0, 0])
    upper_hsv: List[int] = field(default_factory=lambda: [179, 255, 255])
    morph_op: Tuple[int, int] = (3, 3)
    morph_cl: Tuple[int, int] = (5, 5)
    blur: Tuple[int, int] = (5, 5)
    filter_mode: int = 0  # 0: Morph + Blur, 1: Morph only, 2: Blur only
    # Radius bounds and ROI are expressed on a 0..norm_resolution scale so they
    # stay valid when the camera resolution changes.
    min_radius: int = 30
    max_radius: int = 120
    roi_x: int = 0
    roi_y: int = 0
    roi_w: int = 400
    roi_h: int = 400
    norm_resolution: int = 400


@dataclass
class InspectionConfig:
    # Mean intensity (0-255) between m_color and M_color is classified as burnt.
    m_color: int = 0
    M_color: int = 95
    # Mean intensity at or above this value is classified as undercooked.
    undercooked_min_color: int = 195
    # Minimum circularity 4*pi*area / perimeter^2 for a well-formed product.
    min_circularity: float = 0.65
    # Minimum aspect ratio (short side / long side) for a well-formed product.
    min_aspect_ratio: float = 0.68
    # Consecutive defective frames required before the rejector fires.
    min_frames_to_reject: int = 5
    # Frames a track must be seen before it counts as an inspected product (filters noise).
    min_frames_to_count: int = 3
    # Inscribed rectangle crop margin used for color sampling (fraction of bounding box).
    crop_margin: float = 0.20
    # Tolerance factor for centroid matching radius.
    tracking_distance_factor: float = 1.2
    # Frames a track may be missing before it is closed.
    max_disappeared: int = 15
    # Optional ONNX classifier (outputs: good, burnt, undercooked, deformed).
    onnx_model_path: str = ""


@dataclass
class HardwareConfig:
    # Physical outputs are opt-in. Development and CI always use MockActuator.
    enabled: bool = False
    driver: str = "mock"  # "mock" by default; hardware requires enabled=true plus a driver
    gpio_pin: int = 21
    pulse_duration_s: float = 0.40
    # Minimum idle time between the end of one pulse and the start of the next
    debounce_s: float = 0.10
    conveyor_speed_px_per_s: float = 80.0
    rejector_trigger_line_x: int = 320
    arm_port: str = "/dev/ttyUSB0"
    arm_baudrate: int = 115200


@dataclass
class StorageConfig:
    db_path: str = "data/xbake.db"
    snapshot_dir: str = "data/snapshots"
    save_snapshots: bool = True
    telemetry_interval_seconds: int = 60
    enable_cloud_sync: bool = False
    google_sheet_name: str = "xBAKE_Production_Data"
    google_creds_json: str = "secrets/google-service-account.json"
    sync_interval_seconds: int = 120


@dataclass
class WebConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8000
    stream_fps: int = 20
    allow_cors: bool = False
    # When set, write endpoints require "Authorization: Bearer <token>".
    # Can also be provided with the XBAKE_WEB_TOKEN environment variable.
    auth_token: str = ""


SECTIONS = {
    "camera": CameraConfig,
    "vision": VisionConfig,
    "inspection": InspectionConfig,
    "hardware": HardwareConfig,
    "storage": StorageConfig,
    "web": WebConfig,
}

# Parameters that may be changed at runtime through the web API.
# section -> field -> (kind, min, max)
TUNABLE_PARAMS: Dict[str, Dict[str, Tuple[str, float, float]]] = {
    "camera": {
        "brightness": ("int", -100, 255),
        "contrast": ("int", 0, 255),
        "saturation": ("int", 0, 255),
        "hue": ("int", -180, 180),
        "gamma": ("int", 0, 500),
        "auto_wb": ("int", 0, 1),
    },
    "vision": {
        "lower_hsv": ("hsv", 0, 255),
        "upper_hsv": ("hsv", 0, 255),
        "morph_op": ("kernel", 1, 31),
        "morph_cl": ("kernel", 1, 31),
        "blur": ("kernel", 1, 31),
        "filter_mode": ("int", 0, 2),
        "min_radius": ("int", 1, 400),
        "max_radius": ("int", 1, 400),
        "roi_x": ("int", 0, 399),
        "roi_y": ("int", 0, 399),
        "roi_w": ("int", 10, 400),
        "roi_h": ("int", 10, 400),
    },
    "inspection": {
        "m_color": ("int", 0, 255),
        "M_color": ("int", 0, 255),
        "undercooked_min_color": ("int", 0, 255),
        "min_circularity": ("float", 0.0, 1.0),
        "min_aspect_ratio": ("float", 0.0, 1.0),
        "min_frames_to_reject": ("int", 1, 100),
        "min_frames_to_count": ("int", 1, 100),
    },
    "hardware": {
        "pulse_duration_s": ("float", 0.01, 10.0),
        "debounce_s": ("float", 0.0, 60.0),
        "conveyor_speed_px_per_s": ("float", 0.0, 10000.0),
        "rejector_trigger_line_x": ("int", 0, 10000),
    },
}


class ConfigError(ValueError):
    """Raised when a configuration value is invalid."""


def _coerce(kind: str, value: Any, lo: float, hi: float) -> Any:
    def _num(v: Any, cast):
        if isinstance(v, bool):
            raise ConfigError("boolean is not a number")
        n = cast(v)
        if not (lo <= n <= hi):
            raise ConfigError(f"{n} outside range [{lo}, {hi}]")
        return n

    if kind == "int":
        return _num(value, lambda v: int(float(v)))
    if kind == "float":
        return _num(value, float)
    if kind == "hsv":
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ConfigError("expected a list of 3 integers [H, S, V]")
        h, s, v = (int(x) for x in value)
        if not (0 <= h <= 179 and 0 <= s <= 255 and 0 <= v <= 255):
            raise ConfigError("HSV values must be H 0-179, S 0-255, V 0-255")
        return [h, s, v]
    if kind == "kernel":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = [value, value]
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ConfigError("expected a kernel size [w, h]")
        return tuple(_num(x, lambda v: int(float(v))) for x in value)
    raise ConfigError(f"unknown parameter kind {kind}")


def _build_section(cls, data: Dict[str, Any], name: str):
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        logger.warning(f"Ignoring unknown '{name}' config keys: {sorted(unknown)}")
    kwargs = {k: v for k, v in data.items() if k in known}
    for key in ("morph_op", "morph_cl", "blur"):
        if key in kwargs and isinstance(kwargs[key], list):
            kwargs[key] = tuple(kwargs[key])
    return cls(**kwargs)


@dataclass
class AppConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    vision: VisionConfig = field(default_factory=VisionConfig)
    inspection: InspectionConfig = field(default_factory=InspectionConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    web: WebConfig = field(default_factory=WebConfig)
    # File this configuration was loaded from / will be saved to (not serialized).
    path: Optional[str] = field(default=None, repr=False, compare=False)
    # Token supplied through the environment; never written to disk.
    env_auth_token: str = field(default="", repr=False, compare=False)

    @property
    def effective_auth_token(self) -> str:
        return self.env_auth_token or self.web.auth_token

    def to_dict(self, include_secrets: bool = True) -> Dict[str, Any]:
        data = {name: asdict(getattr(self, name)) for name in SECTIONS}

        def _tuple_to_list(obj):
            if isinstance(obj, dict):
                return {k: _tuple_to_list(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_tuple_to_list(x) for x in obj]
            return obj

        data = _tuple_to_list(data)
        if not include_secrets:
            data["web"]["auth_token"] = "***" if data["web"]["auth_token"] else ""
        return data

    def save(self, file_path: Optional[Union[str, Path]] = None) -> Path:
        """Save configuration as JSON. Defaults to the path it was loaded from."""
        path = Path(file_path or self.path or DEFAULT_CONFIG_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        self.path = str(path)
        return path

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        config = cls()
        for name, section_cls in SECTIONS.items():
            if name in data and isinstance(data[name], dict):
                setattr(config, name, _build_section(section_cls, data[name], name))
        return config

    @classmethod
    def load(cls, file_path: Union[str, Path]) -> "AppConfig":
        """Load configuration from a JSON file, or return defaults if it does not exist."""
        path = Path(file_path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                config = cls.from_dict(json.load(f))
        else:
            config = cls()
        config.path = str(path)
        return config

    def apply_updates(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate and apply runtime updates of the form {"section": {"field": value}}.
        All values are validated before any is applied. Returns the applied values.
        """
        if not isinstance(updates, dict):
            raise ConfigError("expected an object of sections")
        staged: Dict[Tuple[str, str], Any] = {}
        for section, values in updates.items():
            allowed = TUNABLE_PARAMS.get(section)
            if allowed is None:
                raise ConfigError(f"section '{section}' is not tunable")
            if not isinstance(values, dict):
                raise ConfigError(f"section '{section}' must be an object")
            for key, value in values.items():
                if key not in allowed:
                    raise ConfigError(f"'{section}.{key}' is not tunable")
                kind, lo, hi = allowed[key]
                try:
                    staged[(section, key)] = _coerce(kind, value, lo, hi)
                except (TypeError, ValueError) as e:
                    raise ConfigError(f"invalid value for '{section}.{key}': {e}") from e

        applied: Dict[str, Any] = {}
        for (section, key), value in staged.items():
            setattr(getattr(self, section), key, value)
            applied.setdefault(section, {})[key] = list(value) if isinstance(value, tuple) else value
        return applied


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """
    Load configuration from `config_path`, the XBAKE_CONFIG environment variable,
    or ./config.json. Missing files yield defaults bound to that path.
    Environment overrides: XBAKE_WEB_TOKEN.
    """
    path = config_path or os.environ.get("XBAKE_CONFIG") or DEFAULT_CONFIG_PATH
    config = AppConfig.load(path)
    token = os.environ.get("XBAKE_WEB_TOKEN")
    if token:
        config.env_auth_token = token
    return config
