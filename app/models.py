from sqlalchemy import Column, Integer, String, Float
from app.database import Base


class Site(Base):
    __tablename__ = "sites"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    address = Column(String)
    prefecture = Column(String)        # 都道府県（JEPXエリア判定に使用）
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


class FitSolarData(Base):
    __tablename__ = "fit_solar_data"

    area = Column(String, primary_key=True)    # JEPXエリア名
    capacity_mw = Column(Float)                # 太陽光FIT導入量（概算MW）
    share_pct = Column(Float)                  # 全国シェア（%）
    fit_score = Column(Integer)                # BESSポテンシャルスコア（0〜100）
    data_source = Column(String)


class CurtailmentData(Base):
    __tablename__ = "curtailment_data"

    area = Column(String, primary_key=True)   # JEPXエリア名
    rate_2023 = Column(Float)                  # 2023年度出力制御率（%）
    rate_2024 = Column(Float)                  # 2024年度見通し（%）
    curtailment_score = Column(Integer)        # BESSポテンシャルスコア（0〜100）
    data_source = Column(String)


class SolarPotential(Base):
    __tablename__ = "solar_potential"

    prefecture = Column(String, primary_key=True)
    ghi = Column(Float)          # 年間平均日射量（kWh/m²/day）
    solar_score = Column(Integer)  # BESSポテンシャルスコア（0〜100）
    data_source = Column(String)   # データ出典


class Substation(Base):
    __tablename__ = "substations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    prefecture = Column(String)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    voltage_class = Column(String, nullable=True)  # 高圧 / 特別高圧 / 不明


class JepxAreaMetrics(Base):
    __tablename__ = "jepx_area_metrics"

    area = Column(String, primary_key=True)  # エリア名
    data_year = Column(Integer)
    avg_price = Column(Float)       # 年間平均価格（円/kWh）
    peak_avg = Column(Float)        # ピーク時間帯平均
    offpeak_avg = Column(Float)     # オフピーク時間帯平均
    spread = Column(Float)          # ピーク・オフピーク価格差
    volatility = Column(Float)      # 価格変動率（標準偏差）
    jepx_score = Column(Integer)    # BESS収益性スコア（0〜100）
