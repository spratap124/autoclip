"""Normalized evidence and candidate contracts for CS2 analysis."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceRecord(BaseModel):
    """A detector observation, never an asserted gameplay event."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)
    evidence_type: str = Field(min_length=1)
    detection_confidence: float = Field(ge=0, le=1)
    supporting_metadata: dict[str, Any] = Field(default_factory=dict)
    explanation: str = Field(min_length=1)
    detector_version: str = "cs2-events-2"

    @model_validator(mode="after")
    def check_interval(self) -> EvidenceRecord:
        if self.end_s < self.start_s:
            raise ValueError("end_s must be greater than or equal to start_s")
        return self


class RegionOfInterest(BaseModel):
    """Normalized source-frame region, independent of capture resolution."""

    model_config = ConfigDict(extra="forbid")

    name: str = "full_frame"
    x: float = Field(default=0, ge=0, le=1)
    y: float = Field(default=0, ge=0, le=1)
    width: float = Field(default=1, gt=0, le=1)
    height: float = Field(default=1, gt=0, le=1)

    @model_validator(mode="after")
    def check_bounds(self) -> RegionOfInterest:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("ROI must fit within normalized frame bounds")
        return self


class CS2Settings(BaseModel):
    """Bounded, configurable sampling and deterministic candidate settings."""

    model_config = ConfigDict(extra="forbid")

    frame_interval_s: float = Field(default=0.5, gt=0.1, le=30)
    audio_window_s: float = Field(default=1.0, gt=0.1, le=10)
    frame_width: int = Field(default=1920, ge=80, le=1920)
    audio_activity_threshold_dbfs: float = Field(default=-32.0, ge=-100, le=0)
    visual_change_threshold: float = Field(default=0.12, gt=0, le=1)
    kill_feed_roi: RegionOfInterest = Field(
        default_factory=lambda: RegionOfInterest(
            name="kill_feed", x=0.70, y=0.035, width=0.295, height=0.22
        )
    )
    round_banner_roi: RegionOfInterest = Field(
        default_factory=lambda: RegionOfInterest(
            name="round_banner", x=0.30, y=0.14, width=0.40, height=0.18
        )
    )
    kill_feed_change_threshold: float = Field(default=7.0, ge=0, le=255)
    round_ocr_interval_s: float = Field(default=1.5, gt=0.25, le=10)
    ocr_enabled: bool = True
    diagnostics_enabled: bool = False
    diagnostics_max_crops: int = Field(default=100, ge=0, le=500)
    local_player_name: str = Field(default="", max_length=64)
    context_before_s: float = Field(default=6.0, ge=0, le=30)
    context_after_s: float = Field(default=10.0, ge=0, le=30)
    merge_gap_s: float = Field(default=8.0, ge=0, le=60)
    min_candidate_s: float = Field(default=15.0, ge=1, le=60)
    max_candidate_s: float = Field(default=60.0, ge=1, le=120)
    max_candidates: int = Field(default=20, ge=1, le=100)
    regions: list[RegionOfInterest] = Field(default_factory=lambda: [RegionOfInterest()])

    @model_validator(mode="after")
    def check_lengths(self) -> CS2Settings:
        if self.min_candidate_s > self.max_candidate_s:
            raise ValueError("min_candidate_s must be no greater than max_candidate_s")
        if not self.regions:
            raise ValueError("at least one region of interest is required")
        return self


class CS2Candidate(BaseModel):
    """Timestamp-based review candidate, independent of transcript word spans."""

    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    category: str
    detection_confidence: float = Field(ge=0, le=1)
    quality_score: int = Field(ge=0, le=100)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    selection_reason: str

    @model_validator(mode="after")
    def check_interval(self) -> CS2Candidate:
        if self.end_s <= self.start_s:
            raise ValueError("candidate end_s must be after start_s")
        return self
