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

    model_config = {"from_attributes": True}
