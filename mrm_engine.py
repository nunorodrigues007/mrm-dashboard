import json
from datetime import datetime
import pandas as pd
from fredapi import Fred

# --- CONFIGURATION ---
# Your official US MRM API Key
FRED_API_KEY = '846494f605a628223d8411828d97e7c6'
fred = Fred(api_key=FRED_API_KEY)

def calculate_score(value, thresholds, reverse=False):
    """Translates macro data into a 1-10 Resilience Score."""
    # 10 is always MAXIMUM SYSTEMIC RISK.
    for i, t in enumerate(thresholds):
        if not reverse:
            if value <= t: return i + 1
        else:
            if value >= t: return i + 1
    return 10

def get_mrm_intelligence():
    print("📡 Engine Status: ONLINE")
    print("📡 Fetching real-time macro data from FRED...")
    
    try:
        # I. CYCLE: 10Y-2Y Spread
        cycle_series = fred.get_series('T10Y2Y')
        current_cycle = cycle_series.iloc[-1]
        past_cycle = cycle_series.iloc[-20] # Check 20 days ago for steepening logic
        
        # Logic: If curve is normalizing (going from negative to positive), risk is high (8-9).
        if current_cycle > 0 and past_cycle < 0:
            cycle_score = 9
        else:
            cycle_score = calculate_score(current_cycle, [1.5, 1.0, 0.5, 0.2, 0, -0.1, -0.2, -0.3, -0.4], reverse=True)

        # II. LIQUIDITY: Market Cap (Wilshire 5000) / M2 Money Supply
        m2 = fred.get_series('M2SL').iloc[-1]
        mcap = fred.get_series('WILL5000PRFC').iloc[-1]
        liquidity_ratio = (mcap / m2) * 10 # Scaling for the ratio
        liquidity_score = calculate_score(liquidity_ratio, [1.5, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 3.2])

        # II. PREMIUM: Equity Risk Premium (ERP)
        # Using a fixed 2026 Earnings Yield estimate of 4.65% (approx 21.5x P/E)
        yield_10y = fred.get_series('DGS10').iloc[-1]
        erp = 4.65 - yield_10y
        premium_score = calculate_score(erp, [4.0, 3.5, 3.0, 2.5, 2.0, 1.5, 1.2, 1.0, 0.8], reverse=True)

        # III. SOLVENCY: Delinquency Rate on All Loans (NPL)
        npl = fred.get_series('DRALACBN').iloc[-1]
        solvency_score = calculate_score(npl, [0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6])

        # III. DEBT: Household Debt Service Ratio (DSR)
        dsr = fred.get_series('TDSP').iloc[-1]
        debt_score = calculate_score(dsr, [9.0, 9.2, 9.4, 9.6, 9.8, 10.0, 10.2, 10.4, 10.6])

        # EARLY WARNING SENTINELS (TRIGGERS)
        jobless_claims = fred.get_series('ICSA').iloc[-1]
        
        # GLOBAL ASSEMBLY
        global_score = round((cycle_score + liquidity_score + premium_score + solvency_score + debt_score) / 5, 1)
        status = "STABLE" if global_score < 5 else "TURBULENCE" if global_score < 8 else "CRITICAL"

        # Prepare JSON Structure
        mrm_data = {
            "last_update": datetime.now().strftime("%B %d, %Y"),
            "global_score": global_score,
            "status": status,
            "pillars": [
                {"name": "Cycle", "value": int(cycle_score), "metric": f"{current_cycle:.2f}%"},
                {"name": "Liquidity", "value": int(liquidity_score), "metric": f"{liquidity_ratio:.2f} Ratio"},
                {"name": "Premium", "value": int(premium_score), "metric": f"{erp:.2f}%"},
                {"name": "Solvency", "value": int(solvency_score), "metric": f"{npl:.2f}%"},
                {"name": "Debt", "value": int(debt_score), "metric": f"{dsr:.2f}%"}
            ],
            "triggers": {
                "jobless_claims": {
                    "value": f"{int(jobless_claims):,}", 
                    "status": "CRITICAL" if jobless_claims > 275000 else "SAFE"
                },
                "erp": {
                    "value": f"{erp:.2f}%", 
                    "status": "CRITICAL" if erp < 0.8 else "SAFE"
                }
            }
        }

        # Export to data.json
        with open('data.json', 'w') as f:
            json.dump(mrm_data, f, indent=4)
        
        print(f"✅ MRM Score calculated: {global_score}")
        print("✅ data.json has been updated and is ready for the Dashboard.")

    except Exception as e:
        print(f"❌ Error during data fetch: {e}")

if __name__ == "__main__":
    get_mrm_intelligence()