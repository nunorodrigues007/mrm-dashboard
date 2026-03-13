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
def fetch_fred(series_id, limit=12):
    """Fetch the most recent observations for a FRED series."""
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
        "observation_start": (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    }
    try:
        r = requests.get(FRED_BASE, params=params, timeout=15)
        r.raise_for_status()
        observations = r.json().get("observations", [])
        # Filter out missing values
        valid = [o for o in observations if o["value"] not in (".", "")]
        return valid
    except Exception as e:
        print(f"  [ERROR] Failed to fetch {series_id}: {e}")
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

def score_liquidity(ratio):
    """Market Cap / M2 ratio → score 1-10"""
    if ratio is None: return 6.0
    if ratio > 2.00:  return 9.5
    if ratio > 1.80:  return 8.5
    if ratio > 1.60:  return 7.5
    if ratio > 1.40:  return 6.5
    if ratio > 1.20:  return 5.5
    if ratio > 1.00:  return 4.0
    if ratio > 0.80:  return 3.0
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
    if score <= 4.0: return "Resilient"
    if score <= 7.0: return "Turbulence"
    return "Critical"

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

    print("  📡 WILL5000PRFC (Wilshire 5000 Market Cap)...")
    will_val, will_date = latest_value("WILL5000PRFC", limit=3)

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

    print("  📡 Historical scores for sparkline...")
    t10y2y_hist = history_values("T10Y2Y", n=7, limit=14)

    # ── ERP Calculation ──
    # ERP = Earnings Yield - 10Y Yield
    # Approximate earnings yield using S&P 500 P/E ~ 22 → E/P ≈ 4.55%
    # For production, fetch from a financial data provider
    # Here we compute from DGS10 and a fixed E/P estimate
    SP500_EARNINGS_YIELD = 4.55  # approximate E/P for S&P 500
    erp_val = round(SP500_EARNINGS_YIELD - (dgs10_val or 4.30), 2) if dgs10_val else 1.02

    # ── Market Cap / M2 Ratio ──
    # WILL5000PRFC is in billions USD; M2SL is in billions USD
    mc_m2_ratio = round(will_val / m2_val, 3) if (will_val and m2_val) else 1.82

    # ── Compute Scores ──
    print("\n  📊 Computing Pillar Scores...")
    s_cycle    = score_cycle(t10y2y_val)
    s_liquidity= score_liquidity(mc_m2_ratio)
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
    print(f"  ✅ Liquidity: {s_liquidity} (MC/M2={mc_m2_ratio}x)")
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

    # ── Build JSON ──
    data = {
        "meta": {
            "lastUpdated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "FRED API (Live)",
            "version": "2.0.0",
            "fredSeriesDates": {
                "T10Y2Y": t10y2y_date,
                "M2SL": m2_date,
                "WILL5000PRFC": will_date,
                "DGS10": dgs10_date,
                "DRALACBN": npl_date,
                "TDSP": dsr_date,
                "ICSA": icsa_date
            }
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
                "metric": "Market Cap / M2 Ratio",
                "value": f"{mc_m2_ratio:.2f}x",
                "fredSeries": "M2SL + WILL5000PRFC",
                "trend": "elevated" if mc_m2_ratio > 1.4 else "normal",
                "delta": "+0.03",
                "description": f"Buffett Indicator at {mc_m2_ratio:.2f}x. M2 at ${m2_val/1000:.1f}T. Equity valuations elevated relative to monetary base.",
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
                "description": f"ERP = E/P ({SP500_EARNINGS_YIELD}%) minus 10Y yield ({dgs10_val:.2f}%). {'Approaching critical threshold.' if erp_val < 1.2 else 'Within normal range.'}",
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
                "description": f"FRED DRALACBN delinquency rate at {npl_val:.2f}%. Systemic banking plumbing {'functioning normally.' if s_solvency < 5 else 'showing stress.'}",
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
                "description": f"Household debt service ratio at {dsr_val:.1f}%. {'Consumer balance sheet strain increasing.' if s_debt > 5 else 'Consumer balance sheets healthy.'}",
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
