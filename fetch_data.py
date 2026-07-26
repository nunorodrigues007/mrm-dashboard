"""
US Macro-Resilience Matrix — FRED Data Engine
Fetches live macroeconomic data from FRED API and generates data.json
"""

import json
import requests
from datetime import datetime, timedelta
import os

# ──────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────
FRED_API_KEY = os.environ.get("FRED_API_KEY", "846494f605a628223d8411828d97e7c6")
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# ──────────────────────────────────────────
# FRED FETCHER
# ──────────────────────────────────────────
def fetch_fred(series_id, limit=12, retries=3, backoff=5):
    """Fetch the most recent observations for a FRED series with retry logic."""
    import time
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
        "observation_start": (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    }
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(FRED_BASE, params=params, timeout=15)
            r.raise_for_status()
            observations = r.json().get("observations", [])
            valid = [o for o in observations if o["value"] not in (".", "")]
            return valid
        except Exception as e:
            print(f"  [R] Failed to fetch {series_id}: {e} (attempt {attempt}/{retries})")
            if attempt < retries:
                time.sleep(backoff * attempt)
    print(f"  [WARN] {series_id} unavailable after {retries} attempts — using fallback.")
    return []

def fetch_fred_full_history(series_id, retries=3, backoff=5):
    """Fetch the FULL available history for a FRED series (ascending, no date window).
    Used for series where we need the whole time series to self-calibrate (percentile
    ranking) rather than just the latest reading."""
    import time
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "asc",
        "limit": 100000,
    }
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(FRED_BASE, params=params, timeout=20)
            r.raise_for_status()
            observations = r.json().get("observations", [])
            valid = [o for o in observations if o["value"] not in (".", "")]
            return valid
        except Exception as e:
            print(f"  [R] Failed to fetch full history for {series_id}: {e} (attempt {attempt}/{retries})")
            if attempt < retries:
                time.sleep(backoff * attempt)
    print(f"  [WARN] {series_id} full history unavailable after {retries} attempts.")
    return []

def latest_value(series_id, limit=12):
    """Return the most recent valid float value for a series."""
    obs = fetch_fred(series_id, limit)
    if obs:
        return float(obs[0]["value"]), obs[0]["date"]
    return None, None

def history_values(series_id, n=7, limit=12):
    """Return the last n valid float values (oldest first)."""
    obs = fetch_fred(series_id, limit)
    valid = obs[:n]
    valid.reverse()
    return [(float(o["value"]), o["date"]) for o in valid]

# ──────────────────────────────────────────
# LIQUIDITY PILLAR — REAL BUFFETT INDICATOR
# ──────────────────────────────────────────
# Background: the original Liquidity pillar divided the FRED series WILL5000PRFC
# (Wilshire 5000 index level) by M2SL. FRED discontinued ALL Wilshire Index data on
# 3 Jun 2024 (https://news.research.stlouisfed.org/2024/04/fred-will-remove-wilshire-index-data-on-june-3-2024/)
# — a year and a half BEFORE this project's inception (Mar 2026) — so that call never
# once returned real data; the pillar has been silently running on a hardcoded
# placeholder (1.82x) since Issue #1. A Yahoo Finance fallback (^W5000) was evaluated
# and also rejected: as of Jul 2026 its feed is itself stale (9+ days), consistent with
# the Wilshire 5000 index's 2026 provider transition (Wilshire Advisors LLC acquiring
# Wilshire Indexes' assets) disrupting downstream distribution.
#
# Redefinition (going forward only — no historical data or past issues are touched):
# the Liquidity pillar now tracks the textbook Buffett Indicator — Total US Corporate
# Equities / GDP — built entirely from Fed/BEA national-accounts data that cannot be
# "discontinued" the way a single index vendor's feed can:
#   NCBEILQ027S  Nonfinancial corporate business; corporate equities, liability level
#   FBCELLQ027S  Domestic financial sectors; corporate equities, liability level
#   GDP          Gross Domestic Product (nominal, SAAR)
# All three are quarterly Z.1 / NIPA series published by the Fed/BEA — the same
# institutional-grade source already used for TDSP, DRALACBN, M2SL. Because the ratio's
# "normal" range drifts over 80 years of nominal growth, the score is NOT based on
# hardcoded dollar thresholds (which is what produced the original 1.82x guess) — it's
# the ratio's own percentile rank against its full available history, so it self-
# calibrates and needs no re-tuning as the economy grows.
LIQUIDITY_SERIES = {
    "nonfinancial_equities": "NCBEILQ027S",
    "financial_equities": "FBCELLQ027S",
    "gdp": "GDP",
}


def fetch_liquidity_percentile():
    """Returns (ratio, percentile, as_of_date, detail_dict).
    ratio = (NCBEILQ027S + FBCELLQ027S) / GDP for the latest quarter common to all
    three series. percentile = that ratio's rank (0-100) within its own full history.
    Returns (None, None, None, {}) if any series is unavailable."""
    ncb = fetch_fred_full_history(LIQUIDITY_SERIES["nonfinancial_equities"])
    fbc = fetch_fred_full_history(LIQUIDITY_SERIES["financial_equities"])
    gdp = fetch_fred_full_history(LIQUIDITY_SERIES["gdp"])
    if not (ncb and fbc and gdp):
        print("  [WARN] Could not fetch one or more Z.1/GDP series for Liquidity pillar.")
        return None, None, None, {}

    ncb_by_date = {o["date"]: float(o["value"]) for o in ncb}
    fbc_by_date = {o["date"]: float(o["value"]) for o in fbc}
    gdp_by_date = {o["date"]: float(o["value"]) for o in gdp}

    common_dates = sorted(set(ncb_by_date) & set(fbc_by_date) & set(gdp_by_date))
    if not common_dates:
        print("  [WARN] No overlapping quarters across NCBEILQ027S/FBCELLQ027S/GDP.")
        return None, None, None, {}

    ratios = []
    for d in common_dates:
        total_equities_b = (ncb_by_date[d] + fbc_by_date[d]) / 1000.0  # millions -> billions
        gdp_b = gdp_by_date[d]
        if gdp_b:
            ratios.append((d, total_equities_b / gdp_b))

    if not ratios:
        return None, None, None, {}

    latest_date, latest_ratio = ratios[-1]
    all_values = [r for _, r in ratios]
    rank = sum(1 for v in all_values if v <= latest_ratio)
    percentile = round(rank / len(all_values) * 100, 1)

    detail = {
        "totalEquitiesB": round((ncb_by_date[latest_date] + fbc_by_date[latest_date]) / 1000.0, 1),
        "gdpB": round(gdp_by_date[latest_date], 1),
        "historyPoints": len(all_values),
        "historyStart": ratios[0][0],
    }
    return round(latest_ratio, 4), percentile, latest_date, detail

# ──────────────────────────────────────────
# SCORING LOGIC
# ──────────────────────────────────────────
def score_cycle(spread):
    """10Y-2Y Yield Curve spread → score 1-10"""
    if spread is None: return 6.0
    if spread < -0.75:  return 9.5
    if spread < -0.50:  return 8.5
    if spread < -0.25:  return 7.5
    if spread < 0.00:   return 6.5
    if spread < 0.50:   return 5.5
    if spread < 0.75:   return 4.5
    if spread < 1.25:   return 3.5
    if spread < 2.00:   return 2.5
    return 1.5

def score_liquidity(percentile):
    """Buffett Indicator (Total US Corporate Equities / GDP) percentile rank (0-100)
    against its own full history (1945-present) → score 1-10. Percentile-based rather
    than fixed dollar thresholds because the ratio's nominal scale drifts over decades —
    self-calibrating, no re-tuning needed as the economy grows."""
    if percentile is None: return 6.0
    if percentile > 95: return 9.5
    if percentile > 90: return 8.5
    if percentile > 80: return 7.5
    if percentile > 65: return 6.5
    if percentile > 50: return 5.5
    if percentile > 35: return 4.0
    if percentile > 20: return 3.0
    return 1.5

def score_premium(erp):
    """Equity Risk Premium % → score 1-10"""
    if erp is None: return 6.0
    if erp < 0.00:  return 10.0
    if erp < 0.50:  return 9.0
    if erp < 0.80:  return 8.0
    if erp < 1.20:  return 7.0
    if erp < 2.00:  return 5.5
    if erp < 3.00:  return 4.0
    if erp < 4.00:  return 2.5
    return 1.5

def score_solvency(npl):
    """Bank NPL / Delinquency Rate % → score 1-10"""
    if npl is None: return 4.0
    if npl > 5.00:  return 9.5
    if npl > 4.00:  return 8.0
    if npl > 3.00:  return 6.5
    if npl > 2.50:  return 5.5
    if npl > 2.00:  return 4.5
    if npl > 1.50:  return 3.5
    if npl > 1.00:  return 2.5
    return 1.5

def score_debt(dsr):
    """Household Debt Service Ratio % → score 1-10"""
    if dsr is None: return 5.0
    if dsr > 13.00: return 9.5
    if dsr > 12.50: return 8.5
    if dsr > 12.00: return 7.5
    if dsr > 11.50: return 6.5
    if dsr > 11.00: return 5.5
    if dsr > 10.50: return 4.5
    if dsr > 10.00: return 3.5
    return 2.0

def global_score(scores):
    """Weighted composite score. Premium and Liquidity weighted higher."""
    weights = {
        "cycle":    0.20,
        "liquidity":0.20,
        "premium":  0.25,
        "solvency": 0.15,
        "debt":     0.20,
    }
    return round(sum(scores[k] * weights[k] for k in weights), 2)

def status_label(score):
    # Must stay in sync with classify_regime() in update_portfolio.py — that script
    # decides actual ETF selection (and, for Critical, the FTQ/Stress sub-regime)
    # using >=8.0 / <=4.0. A mismatch here would show a "Critical" badge on the
    # site while the portfolio itself is still operating in Turbulence, or vice versa.
    if score <= 4.0: return "Resilient"
    if score >= 8.0: return "Critical"
    return "Turbulence"

def pillar_status(score):
    if score <= 4.0: return "stable"
    if score <= 6.0: return "caution"
    if score <= 7.5: return "warning"
    return "critical"

def delta_str(current, previous, unit=""):
    if previous is None: return "—"
    diff = current - previous
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:.2f}{unit}"

# ──────────────────────────────────────────
# MAIN ENGINE
# ──────────────────────────────────────────
def build_data():
    print("\n🔄 US MRM — Fetching live FRED data...\n")

    # ── Fetch all series ──
    print("  📡 T10Y2Y  (Yield Curve Spread)...")
    t10y2y_val, t10y2y_date = latest_value("T10Y2Y")
    t10y2y_prev, _ = latest_value("T10Y2Y", limit=20)  # approximate prev

    print("  📡 M2SL    (M2 Money Supply, billions)...")
    m2_val, m2_date = latest_value("M2SL", limit=3)

    print("  📡 M2SL YoY history (BDC Golden Rule filter)...")
    m2_hist_obs = fetch_fred("M2SL", limit=14)  # monthly series — 14 points covers just over a year
    m2_yoy_growth_pct = None
    if len(m2_hist_obs) >= 13:
        _latest_m2 = float(m2_hist_obs[0]["value"])
        _year_ago_m2 = float(m2_hist_obs[12]["value"])
        if _year_ago_m2:
            m2_yoy_growth_pct = round((_latest_m2 - _year_ago_m2) / _year_ago_m2 * 100, 2)

    print("  📡 Liquidity — Buffett Indicator (Total Corp. Equities / GDP, Fed Z.1 + BEA)...")
    buffett_ratio, buffett_pct, buffett_date, buffett_detail = fetch_liquidity_percentile()
    if buffett_ratio is not None:
        print(f"  ✅ Buffett Indicator: {buffett_ratio*100:.1f}% of GDP ({buffett_date}) — "
              f"{buffett_pct}th percentile of {buffett_detail.get('historyPoints')} quarters since {buffett_detail.get('historyStart')}")
    else:
        print("  [WARN] Liquidity pillar data unavailable this run.")

    print("  📡 DGS10   (10Y Treasury Yield)...")
    dgs10_val, dgs10_date = latest_value("DGS10")

    print("  📡 DRALACBN (Bank Delinquency Rate)...")
    npl_val, npl_date = latest_value("DRALACBN", limit=5)

    print("  📡 TDSP    (Household Debt Service Ratio)...")
    dsr_val, dsr_date = latest_value("TDSP", limit=5)

    print("  📡 ICSA    (Initial Jobless Claims)...")
    icsa_val, icsa_date = latest_value("ICSA")
    icsa_obs = fetch_fred("ICSA", limit=3)
    icsa_prev = float(icsa_obs[1]["value"]) if len(icsa_obs) > 1 else icsa_val

    print("  📡 UNRATE  (Unemployment Rate)...")
    unrate_val, unrate_date = latest_value("UNRATE", limit=3)
    unrate_obs = fetch_fred("UNRATE", limit=3)
    unrate_prev = float(unrate_obs[1]["value"]) if len(unrate_obs) > 1 else unrate_val

    print("  📡 Historical scores for sparkline...")
    t10y2y_hist = history_values("T10Y2Y", n=7, limit=14)

    # ── ERP Calculation ──
    # ERP = Earnings Yield - 10Y Yield
    # Approximate earnings yield using S&P 500 P/E ~ 22 → E/P ≈ 4.55%
    # For production, fetch from a financial data provider
    # Here we compute from DGS10 and a fixed E/P estimate
    SP500_EARNINGS_YIELD = 4.55  # approximate E/P for S&P 500
    erp_val = round(SP500_EARNINGS_YIELD - (dgs10_val or 4.30), 2) if dgs10_val else 1.02

    # ── Compute Scores ──
    print("\n  📊 Computing Pillar Scores...")
    s_cycle    = score_cycle(t10y2y_val)
    s_liquidity= score_liquidity(buffett_pct)
    s_premium  = score_premium(erp_val)
    s_solvency = score_solvency(npl_val)
    s_debt     = score_debt(dsr_val)

    scores = {
        "cycle": s_cycle,
        "liquidity": s_liquidity,
        "premium": s_premium,
        "solvency": s_solvency,
        "debt": s_debt
    }
    g_score = global_score(scores)

    print(f"\n  ✅ Cycle:     {s_cycle} (T10Y2Y={t10y2y_val}%)")
    print(f"  ✅ Liquidity: {s_liquidity} (Buffett={f'{buffett_ratio*100:.1f}' if buffett_ratio is not None else 'N/A'}%, pctile={buffett_pct})")
    print(f"  ✅ Premium:   {s_premium} (ERP={erp_val}%)")
    print(f"  ✅ Solvency:  {s_solvency} (NPL={npl_val}%)")
    print(f"  ✅ Debt:      {s_debt} (DSR={dsr_val}%)")
    print(f"\n  🌐 GLOBAL RESILIENCE SCORE: {g_score} — {status_label(g_score)}\n")

    # ── Historical Global Scores (proxy from yield curve history) ──
    hist_scores = []
    for val, date in t10y2y_hist:
        approx_s = score_cycle(val)
        approx_global = round(approx_s * 0.3 + g_score * 0.7, 1)  # blended
        dt = datetime.strptime(date, "%Y-%m-%d")
        hist_scores.append({
            "date": dt.strftime("%b '%y"),
            "score": approx_global
        })

    # ── ICSA Sentinel ──
    icsa_display = f"{int(icsa_val/1000)}K" if icsa_val else "—"
    icsa_delta_val = icsa_val - icsa_prev if (icsa_val and icsa_prev) else 0
    icsa_delta_str = f"{'+' if icsa_delta_val >= 0 else ''}{int(icsa_delta_val/1000)}K"
    icsa_alert = icsa_val > 275000 if icsa_val else False
    icsa_status = "alert" if icsa_alert else ("caution" if icsa_val > 240000 else "normal")

    # ── ERP Sentinel ──
    erp_alert = erp_val < 0.80 if erp_val is not None else False
    erp_status = "alert" if erp_alert else ("caution" if erp_val < 1.20 else "normal")

    # ── UNRATE Sentinel ──
    # BDC "Stagflation Scenario" crisis trigger (see Watchlist/Crisis section):
    # threshold set at 5.2%, per the BDC macro-cycle framework.
    unrate_delta_val = (unrate_val - unrate_prev) if (unrate_val is not None and unrate_prev is not None) else 0
    unrate_alert = unrate_val >= 5.2 if unrate_val is not None else False
    unrate_status = "alert" if unrate_alert else ("caution" if unrate_val is not None and unrate_val >= 4.7 else "normal")

    # ── Liquidity pillar display fields ──
    if buffett_ratio is not None:
        buffett_display = f"{buffett_ratio*100:.1f}%"
        liquidity_trend = "elevated" if buffett_pct > 65 else ("compressed" if buffett_pct < 35 else "normal")
        liquidity_desc = (
            f"Buffett Indicator (Total US Corporate Equities / GDP) at {buffett_ratio*100:.1f}% — "
            f"{buffett_pct}th percentile vs. its own history since {buffett_detail.get('historyStart')} "
            f"({buffett_detail.get('historyPoints')} quarters). Total corporate equities "
            f"${buffett_detail.get('totalEquitiesB', 0)/1000:.1f}T vs. GDP ${buffett_detail.get('gdpB', 0)/1000:.1f}T "
            f"(quarter ending {buffett_date}). M2 YoY growth: {f'{m2_yoy_growth_pct:+.1f}' if m2_yoy_growth_pct is not None else 'N/A'}%. "
            f"Updates quarterly with each Fed Z.1 / BEA GDP release, not weekly — a change from the prior "
            f"(non-functional) daily Wilshire proxy."
        )
    else:
        buffett_display = "N/A"
        liquidity_trend = "normal"
        liquidity_desc = "Buffett Indicator data unavailable this run (Fed Z.1 / BEA GDP fetch failed)."

    # ── Build JSON ──
    data = {
        "meta": {
            "lastUpdated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "FRED API (Live)",
            "version": "3.0.0",
            "fredSeriesDates": {
                "T10Y2Y": t10y2y_date,
                "M2SL": m2_date,
                "NCBEILQ027S_FBCELLQ027S_GDP": buffett_date,
                "DGS10": dgs10_date,
                "DRALACBN": npl_date,
                "TDSP": dsr_date,
                "ICSA": icsa_date,
                "UNRATE": unrate_date
            },
            "liquidityNote": "Liquidity pillar redefined from discontinued Wilshire 5000 proxy to Buffett Indicator (Total Corp. Equities / GDP), percentile-scored against full history. See fetch_data.py comments."
        },
        "globalResilienceScore": g_score,
        "status": status_label(g_score),
        "pillars": [
            {
                "id": "cycle",
                "roman": "I",
                "name": "Cycle",
                "score": s_cycle,
                "metric": "10Y-2Y Yield Curve",
                "value": f"{t10y2y_val:+.2f}%" if t10y2y_val else "N/A",
                "fredSeries": "T10Y2Y",
                "trend": "steepening" if (t10y2y_val or 0) > 0 else "inverted",
                "delta": delta_str(t10y2y_val or 0, 0.22, "%"),
                "description": "Yield curve spread between 10Y and 2Y Treasuries. Normalizing from inversion historically precedes credit stress by 6–18 months.",
                "status": pillar_status(s_cycle)
            },
            {
                "id": "liquidity",
                "roman": "II",
                "name": "Liquidity",
                "score": s_liquidity,
                "metric": "Buffett Indicator (Total Equities / GDP)",
                "value": buffett_display,
                "fredSeries": "NCBEILQ027S + FBCELLQ027S + GDP",
                "trend": liquidity_trend,
                "delta": "—",
                "m2YoyGrowthPct": m2_yoy_growth_pct,
                "percentileRank": buffett_pct,
                "description": liquidity_desc,
                "status": pillar_status(s_liquidity)
            },
            {
                "id": "premium",
                "roman": "II",
                "name": "Premium",
                "score": s_premium,
                "metric": "Equity Risk Premium",
                "value": f"{erp_val:.2f}%",
                "fredSeries": "DGS10",
                "trend": "compressed" if erp_val < 2.0 else "adequate",
                "delta": f"{erp_val - 1.20:+.2f}",
                "description": f"ERP = E/P ({SP500_EARNINGS_YIELD}%) minus 10Y yield ({f'{dgs10_val:.2f}' if dgs10_val is not None else 'N/A'}%). {'Approaching critical threshold.' if erp_val < 1.2 else 'Within normal range.'}",
                "status": pillar_status(s_premium)
            },
            {
                "id": "solvency",
                "roman": "III",
                "name": "Solvency",
                "score": s_solvency,
                "metric": "Bank Delinquency Rate",
                "value": f"{npl_val:.1f}%" if npl_val else "N/A",
                "fredSeries": "DRALACBN",
                "trend": "stable" if s_solvency < 5 else "rising",
                "delta": "+0.02",
                "description": f"FRED DRALACBN delinquency rate at {f'{npl_val:.2f}' if npl_val is not None else 'N/A'}%. Systemic banking plumbing {'functioning normally.' if s_solvency < 5 else 'showing stress.'}",
                "status": pillar_status(s_solvency)
            },
            {
                "id": "debt",
                "roman": "III",
                "name": "Debt",
                "score": s_debt,
                "metric": "Household DSR",
                "value": f"{dsr_val:.1f}%" if dsr_val else "N/A",
                "fredSeries": "TDSP",
                "trend": "rising" if s_debt > 5 else "stable",
                "delta": "+0.3",
                "description": f"Household debt service ratio at {f'{dsr_val:.1f}' if dsr_val is not None else 'N/A'}%. {'Consumer balance sheet strain increasing.' if s_debt > 5 else 'Consumer balance sheets healthy.'}",
                "status": pillar_status(s_debt)
            }
        ],
        "sentinels": [
            {
                "id": "jobless",
                "name": "Initial Jobless Claims",
                "fredSeries": "ICSA",
                "value": int(icsa_val) if icsa_val else 0,
                "unit": "claims",
                "displayValue": icsa_display,
                "threshold": 275000,
                "thresholdDisplay": "275K",
                "status": icsa_status,
                "trend": "rising" if icsa_delta_val > 0 else "falling",
                "delta": icsa_delta_str,
                "alert": icsa_alert,
                "description": f"Weekly initial jobless claims at {icsa_display}. Red alert triggers above 275,000."
            },
            {
                "id": "erp",
                "name": "Equity Risk Premium",
                "fredSeries": "DGS10",
                "value": erp_val,
                "unit": "%",
                "displayValue": f"{erp_val:.2f}%",
                "threshold": 0.8,
                "thresholdDisplay": "0.80%",
                "status": erp_status,
                "trend": "falling" if erp_val < 1.5 else "stable",
                "delta": f"{erp_val - 1.20:+.2f}%",
                "alert": erp_alert,
                "description": f"ERP at {erp_val:.2f}%. Red alert triggers below 0.80%."
            },
            {
                "id": "unemployment",
                "name": "Unemployment Rate",
                "fredSeries": "UNRATE",
                "value": unrate_val,
                "unit": "%",
                "displayValue": f"{unrate_val:.1f}%" if unrate_val is not None else "N/A",
                "threshold": 5.2,
                "thresholdDisplay": "5.2%",
                "status": unrate_status,
                "trend": "rising" if unrate_delta_val > 0 else ("falling" if unrate_delta_val < 0 else "stable"),
                "delta": f"{'+' if unrate_delta_val >= 0 else ''}{unrate_delta_val:.1f}%",
                "alert": unrate_alert,
                "description": f"US unemployment rate at {f'{unrate_val:.1f}' if unrate_val is not None else 'N/A'}%. BDC stagflation stress trigger activates at 5.2%."
            }
        ],
        "historicalScores": hist_scores
    }

    # ── Write JSON ──
    output_path = os.path.join(os.path.dirname(__file__), "data.json")
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    print(f"  💾 data.json saved → {output_path}")
    print(f"  🕐 Timestamp: {data['meta']['lastUpdated']}\n")
    return data

if __name__ == "__main__":
    build_data()
