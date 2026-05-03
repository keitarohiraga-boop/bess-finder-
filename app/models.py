from sqlalchemy import Column, Integer, String, Float
from app.database import Base


class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(String)
    area = Column(Float)
    landuse = Column(String)
    landuse_label = Column(String)
    flood = Column(String)
    flood_label = Column(String)
    slope = Column(Float)
    substation_dist = Column(Integer)
    land_price = Column(Integer)
    farm_class = Column(String, nullable=True)
    soil_risk = Column(String)
    road_width = Column(Float)
    score = Column(Integer)
    lat = Column(Float)
    lng = Column(Float)
