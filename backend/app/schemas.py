from pydantic import BaseModel
from typing import Optional


class SiteOut(BaseModel):
    id: int
    name: str
    address: str
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

    model_config = {"from_attributes": True}
