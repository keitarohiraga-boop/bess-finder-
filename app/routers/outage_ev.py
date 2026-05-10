"""
停電リスク・EV普及率データのルーター
出典: 電気事業連合会 電力供給関連データ / 次世代自動車振興センター（2023年度）
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app import models

router = APIRouter(tags=["outage-ev"])

# SAIDI（年間停電時間・分/需要家）
# 出典: 電気事業連合会「電力供給関連データ集」・各電力会社年次報告
OUTAGE_DATA = [
    ("北海道", 24.0, "積雪・強風（2018年大規模停電の影響含む）",  88),
    ("東北",   18.5, "台風・豪雪",                               68),
    ("東京",    5.5, "都市部整備済み・比較的安定",               20),
    ("中部",    9.0, "台風・落雷",                               33),
    ("北陸",   17.0, "豪雪・強風",                               63),
    ("関西",    7.5, "台風",                                     28),
    ("中国",   11.5, "台風・豪雨",                               42),
    ("四国",   14.5, "台風",                                     53),
    ("九州",   17.5, "台風・豪雨",                               65),
]

# 都道府県別EV普及率（%）・登録台数（台）概算
# 出典: 次世代自動車振興センター 登録台数統計（2023年度末）
EV_DATA = [
    ("北海道", 14500, 1.2, 15), ("青森県", 2800, 1.1, 14),
    ("岩手県", 3200, 1.3, 16), ("宮城県", 8500, 1.8, 22),
    ("秋田県", 2100, 1.0, 13), ("山形県", 2800, 1.1, 14),
    ("福島県", 5500, 1.4, 17), ("茨城県", 12000, 1.9, 23),
    ("栃木県", 8500, 1.8, 22), ("群馬県", 7800, 1.7, 21),
    ("埼玉県", 28000, 2.2, 27), ("千葉県", 25000, 2.1, 26),
    ("東京都", 85000, 3.5, 43), ("神奈川県", 52000, 3.0, 37),
    ("新潟県", 6500, 1.4, 17), ("富山県", 4200, 1.5, 18),
    ("石川県", 4500, 1.6, 20), ("福井県", 3200, 1.5, 18),
    ("山梨県", 4800, 1.8, 22), ("長野県", 9500, 1.9, 23),
    ("岐阜県", 8500, 1.7, 21), ("静岡県", 18000, 2.1, 26),
    ("愛知県", 55000, 2.8, 34), ("三重県", 8000, 1.8, 22),
    ("滋賀県", 7500, 2.0, 25), ("京都府", 12000, 2.2, 27),
    ("大阪府", 45000, 2.5, 31), ("兵庫県", 28000, 2.2, 27),
    ("奈良県", 7000, 2.0, 25), ("和歌山県", 3500, 1.5, 18),
    ("鳥取県", 1800, 1.2, 15), ("島根県", 1900, 1.2, 15),
    ("岡山県", 9500, 1.9, 23), ("広島県", 14000, 2.1, 26),
    ("山口県", 5500, 1.6, 20), ("徳島県", 3200, 1.5, 18),
    ("香川県", 4500, 1.7, 21), ("愛媛県", 5800, 1.7, 21),
    ("高知県", 2800, 1.3, 16), ("福岡県", 28000, 2.2, 27),
    ("佐賀県", 3500, 1.5, 18), ("長崎県", 4800, 1.5, 18),
    ("熊本県", 7500, 1.7, 21), ("大分県", 4500, 1.6, 20),
    ("宮崎県", 4200, 1.5, 18), ("鹿児島県", 6500, 1.5, 18),
    ("沖縄県", 5500, 1.7, 21),
]


@router.post("/outage/seed", summary="停電リスクデータを初期投入")
def seed_outage(db: Session = Depends(get_db)):
    for area, saidi, cause, score in OUTAGE_DATA:
        rec = db.query(models.OutageRiskData).filter_by(area=area).first()
        if rec:
            rec.saidi_min = saidi; rec.outage_score = score; rec.main_cause = cause
        else:
            db.add(models.OutageRiskData(
                area=area, saidi_min=saidi, outage_score=score, main_cause=cause,
                data_source="電気事業連合会 電力供給関連データ集（2023年度）",
            ))
    db.commit()
    return {"message": "停電リスクデータを投入しました", "count": len(OUTAGE_DATA)}


@router.post("/ev/seed", summary="EV普及率データを初期投入")
def seed_ev(db: Session = Depends(get_db)):
    for pref, count, rate, score in EV_DATA:
        rec = db.query(models.EVAdoptionData).filter_by(prefecture=pref).first()
        if rec:
            rec.ev_count = count; rec.ev_rate_pct = rate; rec.ev_score = score
        else:
            db.add(models.EVAdoptionData(
                prefecture=pref, ev_count=count, ev_rate_pct=rate, ev_score=score,
                data_source="次世代自動車振興センター 登録台数統計（2023年度末）・概算",
            ))
    db.commit()
    return {"message": "EV普及率データを投入しました", "count": len(EV_DATA)}


@router.get("/outage/ranking", summary="エリア別停電リスクランキング")
def outage_ranking(db: Session = Depends(get_db)):
    return db.query(models.OutageRiskData).order_by(
        models.OutageRiskData.saidi_min.desc()
    ).all()


@router.get("/ev/ranking", summary="都道府県別EV普及率ランキング")
def ev_ranking(db: Session = Depends(get_db)):
    return db.query(models.EVAdoptionData).order_by(
        models.EVAdoptionData.ev_score.desc()
    ).all()
