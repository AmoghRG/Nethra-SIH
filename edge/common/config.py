"""Configuration loading for the edge pipeline.

Jointly owned by A and B. Every tuned constant in ``edge/`` comes from
``edge/config.yaml`` through here; nothing is hardcoded at a call site.

The ``--set`` override exists so that a suppression rule can be switched off
and the pipeline re-run without editing a tracked file. Showing what each rule
actually removes is an acceptance criterion (edge PRD 6.4), and doing that by
hand-editing YAML on stage is how you end up demoing with a typo in it.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


class Config:
    """Dot-path access over the parsed YAML tree.

    ``cfg.get("conflicts.ttc_severe_s")`` rather than
    ``cfg["conflicts"]["ttc_severe_s"]``, so an override can be expressed as a
    single string on the command line.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    # -- access ------------------------------------------------------------

    def get(self, path: str, default: Any = ...) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is ...:
                    raise KeyError("config key not found: " + repr(path))
                return default
            node = node[part]
        return node

    def section(self, path: str) -> dict[str, Any]:
        node = self.get(path)
        if not isinstance(node, dict):
            raise TypeError("config key is not a section: " + repr(path))
        return node

    def radius_m(self, cls: str) -> float:
        """Ground-plane circle radius for a vehicle class.

        Vehicles are rectangles, not circles. This is a deliberate
        simplification: rectangle intersection is more accurate and
        considerably more code, and it is not where 24 hours should go. Say so
        if asked.
        """
        radii = self.section("radii_m")
        if cls not in radii:
            raise KeyError("no radius configured for class " + repr(cls))
        return float(radii[cls])

    def as_dict(self) -> dict[str, Any]:
        return self._data

    # -- mutation ----------------------------------------------------------

    def override(self, path: str, raw: str) -> None:
        """Apply one ``a.b.c=value`` override, parsing the value as YAML.

        YAML parsing means ``true``, ``0.9`` and ``[a, b]`` all arrive as the
        right type rather than as strings.
        """
        parts = path.split(".")
        node = self._data
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                raise KeyError("cannot override inside missing section: " + repr(path))
            node = node[part]
        leaf = parts[-1]
        if leaf not in node:
            raise KeyError("refusing to create unknown config key: " + repr(path))
        node[leaf] = yaml.safe_load(raw)


def load_config(
    path: str | Path | None = None, overrides: list[str] | None = None
) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        cfg = Config(yaml.safe_load(fh))
    for item in overrides or []:
        if "=" not in item:
            raise ValueError("--set expects key=value, got " + repr(item))
        key, _, raw = item.partition("=")
        cfg.override(key.strip(), raw.strip())
    return cfg


def add_config_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Attach ``--config`` and ``--set`` to any CLI in the edge pipeline."""
    parser.add_argument("--config", default=None, help="path to config.yaml")
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override one config key, e.g. --set suppression.lane_splitting=false",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> Config:
    return load_config(getattr(args, "config", None), getattr(args, "overrides", None))
