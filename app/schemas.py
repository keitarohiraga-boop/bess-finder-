from pydantic import BaseModel
from typing import Optional


class JepxMetrics(BaseModel):
    area: str
    data_year: int
    avg_price: float
    peak_avg: float
    offpeak_avg: float
    spread: float
    volatility: float
    jepx_score: int

    model_config = {"from_attributes": True}


class SolarOut(BaseModel):
    prefecture: str
    ghi: float
    solar_score: int
    data_source: str

    model_config = {"from_attributes": True}


class OutageRiskOut(BaseModel):
    area: str
    saidi_min: float
    outage_score: int
    main_cause: str
    data_source: str
    model_config = {"from_attributes": True}


class EVAdoptionOut(BaseModel):
    prefecture: str
    ev_count: int
    ev_rate_pct: float
    ev_score: int
    data_source: str
    model_config = {"from_attributes": True}


class FitSolarOut(BaseModel):
    area: str
    capacity_mw: float
    share_pct: float
    fit_score: int
    data_source: str

    model_config = {"from_attributes": True}


class CurtailmentOut(BaseModel):
    area: str
    rate_2023: float
    rate_2024: float
    curtailment_score: int
    data_source: str

    model_config = {"from_attributes": True}


class SiteOut(BaseModel):
    id: int
    name: str
    address: str
    prefecture: Optional[str]
    area: float
    landuse: str
    landuse_label: str
    flood: str
    flood_label: str
    slope: float
    substation_dist: int
    land_price: int
    farm_class: Optional[str]
    soil_risk: str
    road_width: float
    score: int
    lat: float
    lng: float
    jepx: Optional[JepxMetrics] = None
    solar: Optional[SolarOut] = None
    curtailment: Optional[CurtailmentOut] = None
    fit_solar: Optional[FitSolarOut] = None
    outage: Optional[OutageRiskOut] = None
    ev: Optional[EVAdoptionOut] = None

    model_config = {"from_attributes": True}
