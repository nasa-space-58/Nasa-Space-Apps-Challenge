"""Bounded settings for a MOON_ME, spherical lunar terrain horizon."""

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
        raise NotImplementedError
