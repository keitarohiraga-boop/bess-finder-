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


class SiteCase(Base):
    """案件管理テーブル - 候補地ごとの社内ワークフロー管理"""
    __tablename__ = "site_cases"

    id           = Column(Integer, primary_key=True, index=True)
    site_id      = Column(Integer, nullable=False, index=True)
    site_name    = Column(String)                        # 表示用（非正規化）
    status       = Column(String, default="発見")         # 発見/精査中/承認待ち/アプローチ開始/交渉中/契約済/見送り
    case_type    = Column(String, default="自社")         # 自社/パートナー依頼
    assignee     = Column(String, default="")            # 担当者名
    partner_name = Column(String, nullable=True)          # パートナー会社名（依頼時）
    slack_thread_url = Column(String, nullable=True)      # 承認依頼紐付けSlackスレッドURL
    pass_reason  = Column(String, nullable=True)          # 見送り理由
    notes        = Column(String, default="[]")           # JSON: [{text, timestamp}]
    created_at   = Column(String)
    updated_at   = Column(String)


class LandPricePoint(Base):
    __tablename__ = "land_price_points"

    id = Column(Integer, primary_key=True, index=True)
    prefecture = Column(String, index=True)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    price_per_m2 = Column(Integer)
    use_type = Column(String)
    address = Column(String)
    data_year = Column(Integer)


class BessFacility(Base):
    __tablename__ = "bess_facilities"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    capacity_mwh = Column(Float, nullable=True)   # 蓄電容量（MWh）
    power_mw = Column(Float, nullable=True)        # 出力（MW）
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    osm_id = Column(String, nullable=True)


class OutageRiskData(Base):
    __tablename__ = "outage_risk_data"

    area = Column(String, primary_key=True)
    saidi_min = Column(Float)       # 年間停電時間（分/需要家）
    outage_score = Column(Integer)  # バックアップ価値スコア（0〜100）
    main_cause = Column(String)     # 主な停電原因
    data_source = Column(String)


class EVAdoptionData(Base):
    __tablename__ = "ev_adoption_data"

    prefecture = Column(String, primary_key=True)
    ev_count = Column(Integer)      # EV登録台数（台）
    ev_rate_pct = Column(Float)     # 普及率（%）
    ev_score = Column(Integer)      # V2G・充電需要スコア（0〜100）
    data_source = Column(String)


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
