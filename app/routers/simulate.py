"""
収益シミュレーター（バックエンド版）
フロントの calcIRR / calc20YearCF と同じロジックをPythonで実装。
Agent が IRR/NPV を計算する際に使用する。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app import models

router = APIRouter(prefix="/simulate", tags=["simulate"])

# ===== 計算ロジック =====

def calc_20year_cf(net_rev: float, capex: float, deg_rate: float = 0.02) -> list[float]:
    flows = [-capex]
    for y in range(1, 21):
        flows.append(net_rev * ((1 - deg_rate) ** (y - 1)))
    return flows


def calc_npv(flows: list[float], rate: float) -> float:
    return sum(cf / ((1 + rate) ** i) for i, cf in enumerate(flows))


def calc_irr(flows: list[float]) -> float:
    lo, hi = -0.5, 5.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if calc_npv(flows, mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def estimate_net_revenue(
    jepx_spread: float,
    capacity_mwh: float,
    power_mw: float,
    cap_price_per_kw: float = 14000,
    ancillary_per_kw: float = 2000,
    cycles_per_year: int = 300,
    efficiency: float = 0.88,
) -> float:
    """年間純収益（円）を試算"""
    cap_kwh   = capacity_mwh * 1000                              # MWh → kWh
    power_kw  = power_mw * 1000                                  # MW  → kW
    arbitrage = jepx_spread * cap_kwh * cycles_per_year * efficiency
    capacity  = power_kw * cap_price_per_kw
    ancillary = power_kw * ancillary_per_kw
    return arbitrage + capacity + ancillary


def estimate_capex(
    capacity_mwh: float,
    unit_price_per_kwh: float = 25,  # 万円/kWh（市場標準：20〜40万円/kWh）
) -> float:
    """設備投資額（円）を試算"""
    cap_kwh = capacity_mwh * 1000   # MWh → kWh
    return unit_price_per_kwh * 10000 * cap_kwh


# ===== エンドポイント =====

class SimulateRequest(BaseModel):
    site_id: Optional[int] = None
    capacity_mwh: float = 20.0
    power_mw: float = 5.0
    unit_price_per_kwh: float = 25.0   # 万円/kWh（設備単価・EPC込み市場標準：15〜30万円/kWh）
    jepx_spread: Optional[float] = None  # 未指定時はDBから取得
    land_cost_per_m2: Optional[int] = None
    area_m2: Optional[float] = None
    discount_rate: float = 0.08
    deg_rate: float = 0.02


@router.post("", summary="収益シミュレーション（IRR/NPV/20年CF）")
def simulate(body: SimulateRequest, db: Session = Depends(get_db)):
    # JEPXスプレッドの取得（指定がなければDBからサイトのエリアデータを使用）
    jepx_spread = body.jepx_spread
    if jepx_spread is None and body.site_id:
        site = db.get(models.Site, body.site_id)
        if site and site.prefecture:
            from app.area_mapping import PREFECTURE_TO_AREA
            area = PREFECTURE_TO_AREA.get(site.prefecture, "東京")
            jepx = db.get(models.JepxAreaMetrics, area)
            jepx_spread = jepx.spread if jepx else 5.0
    if jepx_spread is None:
        jepx_spread = 5.0  # デフォルト

    net_rev = estimate_net_revenue(
        jepx_spread=jepx_spread,
        capacity_mwh=body.capacity_mwh,
        power_mw=body.power_mw,
    )
    capex = estimate_capex(body.capacity_mwh, body.unit_price_per_kwh)

    # 土地コストが指定されている場合はCAPEXに加算
    if body.land_cost_per_m2 and body.area_m2:
        capex += body.land_cost_per_m2 * body.area_m2

    opex = capex * 0.02  # 年間維持費（CAPEX の2%）
    net_rev_after_opex = net_rev - opex

    flows = calc_20year_cf(net_rev_after_opex, capex, body.deg_rate)
    irr   = calc_irr(flows)
    npv   = calc_npv(flows, body.discount_rate)
    total_cf = sum(flows[1:]) - capex
    payback  = next((y for y, cf in enumerate(
        [sum(flows[:i+1]) for i in range(len(flows))], 0
    ) if cf >= 0), None)

    def fmt_oku(n: float) -> str:
        if abs(n) >= 1e8:
            return f"{n/1e8:.1f}億円"
        return f"{n/1e6:.1f}百万円"

    return {
        "irr_pct":          round(irr * 100, 2),
        "npv":              round(npv),
        "npv_label":        fmt_oku(npv),
        "annual_revenue":   round(net_rev),
        "annual_revenue_label": fmt_oku(net_rev),
        "capex":            round(capex),
        "capex_label":      fmt_oku(capex),
        "payback_years":    payback,
        "jepx_spread_used": jepx_spread,
        "capacity_mwh":     body.capacity_mwh,
        "power_mw":         body.power_mw,
    }
