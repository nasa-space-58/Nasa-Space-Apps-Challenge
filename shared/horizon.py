"""Bounded settings for a MOON_ME, spherical lunar terrain horizon."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HorizonSettings(BaseModel):
    """Sampling inputs in degrees/meters; invalid or excessive work raises ValueError."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    azimuth_step_deg: float = Field(default=1.0, ge=0.25, le=90, strict=True)
    search_distance_m: int = Field(default=100000, ge=80, le=300000, strict=True)
    radial_step_m: int = Field(default=40, ge=10, le=80, strict=True)
    source_extent: Literal["raw", "crop"] = "raw"

    @model_validator(mode="after")
    def bounded_grid(self):
        """Require a closed azimuth grid and at most five million ray samples."""
        count = 360 / self.azimuth_step_deg
        if not math.isclose(count, round(count), abs_tol=1e-9):
            raise ValueError("azimuth_step_deg must divide 360 evenly")
        if (
            count
            * (
                math.ceil(self.search_distance_m / self.radial_step_m)
                + min(1000, self.search_distance_m)
                + 1
            )
            > 5_000_000
        ):
            raise ValueError("Horizon request exceeds five million samples")
        return self
