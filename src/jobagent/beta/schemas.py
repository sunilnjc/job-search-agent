"""Validated, user-owned request shapes for the multi-user beta API."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class ProfileUpdateRequest(BaseModel):
    """User-editable career identity; never reads or derives founder data."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    headline: Optional[str] = Field(default=None, max_length=180)
    location: Optional[str] = Field(default=None, max_length=120)
    country_code: Optional[str] = Field(default=None, min_length=2, max_length=2)
    phone: Optional[str] = Field(default=None, max_length=40)
    linkedin_url: Optional[HttpUrl] = None
    portfolio_url: Optional[HttpUrl] = None

    @field_validator("country_code")
    @classmethod
    def normalize_country_code(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


class PreferencesUpdateRequest(BaseModel):
    """Application-search preferences owned by the authenticated user."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_roles: List[str] = Field(default_factory=list, max_length=20)
    preferred_countries: List[str] = Field(default_factory=list, max_length=30)
    preferred_regions: List[str] = Field(default_factory=list, max_length=12)
    remote_preference: Literal["remote_only", "remote_preferred", "hybrid_ok", "onsite_ok"] = "remote_preferred"
    sponsorship_required: bool = False
    relocation_open: bool = False
    minimum_match_score: int = Field(default=8, ge=1, le=10)
    salary_currency: Optional[str] = Field(default=None, min_length=3, max_length=3)
    salary_minimum: Optional[int] = Field(default=None, ge=0, le=10_000_000)

    @field_validator("target_roles", "preferred_regions")
    @classmethod
    def normalize_unique_values(cls, values: List[str]) -> List[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(value.casefold() for value in normalized)):
            raise ValueError("Values must be unique")
        return normalized

    @field_validator("preferred_countries")
    @classmethod
    def normalize_country_codes(cls, values: List[str]) -> List[str]:
        normalized = [value.strip().upper() for value in values if value.strip()]
        if any(len(value) != 2 for value in normalized):
            raise ValueError("Preferred countries must use ISO 3166-1 alpha-2 codes")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Preferred countries must be unique")
        return normalized

    @field_validator("salary_currency")
    @classmethod
    def normalize_currency(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value
