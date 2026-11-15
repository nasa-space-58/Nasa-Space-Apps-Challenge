"""Offline C3 stress-discovery experiment; no runtime or provider integration."""
from dataclasses import dataclass

import numpy as np

FAMILIES = {
    "shade_link": "development",
    "thermal_radio": "development",
    "energy_camera": "validation",
    "link_thermal": "validation",
    "heater_shade": "test",
    "radio_energy": "test",
}
METHODS = ("random", "sobol", "cross_entropy", "annealing", "kernel", "qcbm", "qcbm_inverse_cdf")
CLASSICAL = METHODS[:5]
QUBITS = 6


@dataclass(frozen=True)
class Budget:
    calls: int = 16
    seconds: float | None = None

    def __post_init__(self):
        raise NotImplementedError


def make_scenario(family: str, index: int | None):
    """None constructs the no-fault qualification case; index encodes six bits."""
    raise NotImplementedError


def summarize_replay(result: dict) -> dict:
    raise NotImplementedError


def born_probabilities(angles: np.ndarray) -> np.ndarray:
    """Little endian RY layer, nearest-neighbor CX chain, RY layer."""
    raise NotImplementedError


def fit_born(history: list[dict], seed: int) -> dict:
    raise NotImplementedError


def run_search(family: str, method: str, seed: int, budget: Budget, *, oracle=None, clock=None) -> dict:
    raise NotImplementedError


def paired_summary(runs: list[dict]) -> dict:
    raise NotImplementedError


def campaign(*, seeds=(11, 29, 47, 71, 101), calls=16, seconds=0.35) -> dict:
    raise NotImplementedError
