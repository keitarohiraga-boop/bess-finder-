from sqlalchemy import Column, Integer, String, Float
from geoalchemy2 import Geometry
from app.database import Base


class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(String)
    area = Column(Float)           # 面積（㎡）
    landuse = Column(String)       # industrial / quasi-industrial / unzoned / commercial
    landuse_label = Column(String)
    flood = Column(String)         # none / low / mid / high
    flood_label = Column(String)
    slope = Column(Float)          # 傾斜（度）
    substation_dist = Column(Integer)  # 変電所までの距離（m）
    land_price = Column(Integer)   # 地価（円/㎡）
    farm_class = Column(String, nullable=True)  # class1-farm / class2-farm / class3-farm
    soil_risk = Column(String)
    road_width = Column(Float)     # 接道幅員（m）
    score = Column(Integer)
    geom = Column(Geometry("POINT", srid=4326))
