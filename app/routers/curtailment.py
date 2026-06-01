from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models

router = APIRouter(prefix="/curtailment", tags=["curtailment"])

# ===== 出力制御データ =====
# 出典: 資源エネルギー庁・OCCTO公表データ（エネルギーベース年間制御率）
#
# rate_2023: 2023年度実績（太陽光+風力合算）
# rate_2024: 2024年度実績（太陽光+風力合算）
# rate_2025: 2025年度見通し（資エネ庁 2025年12月）
# wind_rate_2024: 風力専用制御率2024年度（推定値 - OCCTOの風力別実績で更新予定）
#
# solar_score: 太陽光BESS機会スコア（出力制御の多さ = BESSで吸収できる余地）
# wind_score: 風力BESS機会スコア（北海道・東北の風力余剰吸収機会）
# curtailment_score: 後方互換スコア（= max(solar_score, wind_score)）
#
# 風力スコアの根拠:
#   北海道: 風力大量導入・系統が細く制御増加傾向。風況最良エリア。score=70
#   東北:   風力+太陽光両方で制御増加中。2024実績4.0%で全国2位。score=55
#   九州:   制御は多いが太陽光主体。風力は少ない。score=20
#   その他: 風力導入量が少なくBESS機会薄。score=5〜15

CURTAILMENT_DATA = [
    # (area, rate_2023, rate_2024, rate_2025, wind_rate_2024, solar_score, wind_score)
    ("北海道", 0.9,  0.9,  0.3,  5.0,  12,  70),  # 太陽光制御は少ないが風力余剰BESS機会大
    ("東北",   2.8,  4.0,  2.2,  2.5,  55,  55),  # 太陽光+風力両方で制御増加
    ("東京",   0.0,  0.01, 0.009, None,  0,   5),
    ("中部",   0.3,  0.5,  0.4,  None, 10,   5),
    ("北陸",   0.8,  1.5,  2.1,  None, 18,  15),
    ("関西",   0.1,  0.3,  0.4,  None,  5,   5),
    ("中国",   4.2,  3.5,  2.8,  None, 65,  10),
    ("四国",   2.5,  2.8,  2.4,  None, 50,  10),
    ("九州",   6.5,  8.3,  6.1,  0.5, 100,  20),  # 太陽光制御全国最大。風力は少ない
    ("沖縄",   0.1,  0.2,  None, None,  3,   2),
]


@router.post("/seed", summary="出力制御データを初期投入・更新")
def seed_curtailment(db: Session = Depends(get_db)):
    for row in CURTAILMENT_DATA:
        area, r23, r24, r25, wind24, sol_s, wind_s = row
        combined_score = max(sol_s, wind_s)

        rec = db.query(models.CurtailmentData).filter_by(area=area).first()
        if rec:
            rec.rate_2023 = r23
            rec.rate_2024 = r24
            rec.rate_2025 = r25
            rec.wind_rate_2024 = wind24
            rec.solar_score = sol_s
            rec.wind_score = wind_s
            rec.curtailment_score = combined_score
            rec.data_source = "資源エネルギー庁 2025年12月公表・OCCTO 2024年度実績（風力スコアは推定）"
        else:
            db.add(models.CurtailmentData(
                area=area,
                rate_2023=r23,
                rate_2024=r24,
                rate_2025=r25,
                wind_rate_2024=wind24,
                solar_score=sol_s,
                wind_score=wind_s,
                curtailment_score=combined_score,
                data_source="資源エネルギー庁 2025年12月公表・OCCTO 2024年度実績（風力スコアは推定）",
            ))
    db.commit()
    return {"message": "出力制御データを更新しました", "count": len(CURTAILMENT_DATA)}


@router.get("/ranking", summary="エリア別出力制御率ランキング")
def get_ranking(db: Session = Depends(get_db)):
    rows = db.query(models.CurtailmentData).order_by(
        models.CurtailmentData.curtailment_score.desc()
    ).all()
    return [
        {
            "area": r.area,
            "rate_2024": r.rate_2024,
            "rate_2025": r.rate_2025,
            "wind_rate_2024": r.wind_rate_2024,
            "solar_score": r.solar_score,
            "wind_score": r.wind_score,
            "curtailment_score": r.curtailment_score,
        }
        for r in rows
    ]
