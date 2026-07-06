"""
update_portfolio.py
MRM Portfolio Saturday Price Updater
Runs every Saturday at 10:00 UTC via GitHub Actions (portfolio.yml)

Rebalance rules:
- SEMESTRAL: last Friday of January and June
- EMERGENCY: 2 consecutive weeks with score >= 8.0 (defensive) or <= 4.0 (offensive)
- NO tactical weekly rebalance

ETF universe varies by regime:
- Turbulence (default): SPY, IEF, LQD, PDBC, BIL, VNQ
- Critical  (>= 8.0) : USMV, TLT, SGOV, GLD, BIL, (VNQ reduced)
- Resilient (<= 4.0) : QQQ, SHY, HYG, PDBC, BIL, IWO
"""

import json, os, sys, time, re, math, logging
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mrm_portfolio")

# ── Canonical 6 buckets ───────────────────────────────────────────────────────
BUCKETS = ["US_EQUITIES", "US_TREASURIES", "IG_CREDIT", "COMMODITIES", "CASH", "ALTERNATIVES"]

REGIME_ETF_MAP = {
    "Turbulence": {
        "US_EQUITIES": "SPY", "US_TREASURIES": "IEF", "IG_CREDIT": "LQD",
        "COMMODITIES": "PDBC", "CASH": "BIL", "ALTERNATIVES": "VNQ",
    },
    "Critical": {
        "US_EQUITIES": "USMV", "US_TREASURIES": "TLT", "IG_CREDIT": "SGOV",
        "COMMODITIES": "GLD", "CASH": "BIL", "ALTERNATIVES": "VNQ",
    },
    "Resilient": {
        "US_EQUITIES": "QQQ", "US_TREASURIES": "SHY", "IG_CREDIT": "HYG",
        "COMMODITIES": "PDBC", "CASH": "BIL", "ALTERNATIVES": "IWO",
    },
}

ALL_TICKERS = list(set(t for regime in REGIME_ETF_MAP.values() for t in regime.values()))

ASSET_CLASS_BUCKET_MAP = {
    "US Equities": "US_EQUITIES", "US Equities (Broad)": "US_EQUITIES",
    "Domestic Equity": "US_EQUITIES", "International Developed": "US_EQUITIES",
    "US Large-Cap Equities": "US_EQUITIES", "Large-Cap Equity": "US_EQUITIES",
    "US Large-Cap Equity": "US_EQUITIES",
    "US Treasuries": "US_TREASURIES", "US Treasuries (7": "US_TREASURIES",
    "Sovereign": "US_TREASURIES", "Intermediate Treasuries": "US_TREASURIES",
    "Investment-Grade Credit": "IG_CREDIT", "Investment Grade Credit": "IG_CREDIT",
    "Investment-Grade Fixed": "IG_CREDIT",
    "Commodities": "COMMODITIES", "Real Assets": "COMMODITIES",
    "Commodities Broad Basket": "COMMODITIES",
    "Cash": "CASH", "Cash & Equivalents": "CASH", "Cash / Ultra-Short Bills": "CASH",
    "Short-Duration Bills": "CASH", "Short Duration Bills": "CASH",
    "Alternatives / Real": "ALTERNATIVES", "Alternatives / Hedge": "ALTERNATIVES",
    "Alternatives": "ALTERNATIVES", "Real Estate": "ALTERNATIVES", "REITs": "ALTERNATIVES",
}

SEMESTRAL_MONTHS = {1, 6}
EMERGENCY_SCORE_HIGH = 8.0
EMERGENCY_SCORE_LOW  = 4.0
CONSECUTIVE_WEEKS    = 2

PORTFOLIO_PATH = Path("portfolio.json")
NEWSLETTER_DIR = Path(".")

# ── Force-rebalance override (set FORCE_REBALANCE=true in env to bypass date check) ──
FORCE_REBALANCE = os.environ.get("FORCE_REBALANCE", "").lower() in ("1", "true", "yes")


def classify_regime(score):
    if score is None: return "Turbulence"
    if score >= EMERGENCY_SCORE_HIGH: return "Critical"
    if score <= EMERGENCY_SCORE_LOW:  return "Resilient"
    return "Turbulence"


def get_active_tickers(regime):
    return list(REGIME_ETF_MAP.get(regime, REGIME_ETF_MAP["Turbulence"]).values())


def get_last_friday():
    today = date.today()
    days_back = (today.weekday() - 4) % 7
    return today - timedelta(days=days_back)


def is_semestral_rebalance_week(target_date):
    if target_date.month not in SEMESTRAL_MONTHS:
        return False
    next_friday = target_date + timedelta(days=7)
    return next_friday.month != target_date.month


# ── US market holiday calendar ────────────────────────────────────────────────
US_MARKET_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents' Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # Independence Day (observed)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
}


def adjust_for_market_holiday(target_date):
    adjusted = target_date
    while adjusted in US_MARKET_HOLIDAYS_2026 or adjusted.weekday() >= 5:
        adjusted -= timedelta(days=1)
    if adjusted != target_date:
        log.warning(f"{target_date} is a market holiday — using {adjusted}")
    return adjusted


def fetch_prices(tickers, target_date, retries=3):
    prices = {}
    start = target_date - timedelta(days=7)
    end   = target_date + timedelta(days=1)
    for ticker in tickers:
        for attempt in range(retries):
            try:
                hist = yf.Ticker(ticker).history(start=str(start), end=str(end))
                if hist.empty:
                    raise ValueError(f"No data for {ticker}")
                hist.index = hist.index.date
                if target_date in hist.index:
                    price = float(hist.loc[target_date]["Close"])
                else:
                    price = float(hist["Close"].iloc[-1])
                if math.isnan(price) or math.isinf(price):
                    raise ValueError(f"Invalid price for {ticker}")
                prices[ticker] = round(price, 4)
                log.info(f"  {ticker}: ${price:.4f}")
                break
            except Exception as e:
                log.warning(f"  {ticker} attempt {attempt+1} failed: {e}")
                time.sleep(2 ** attempt)
        else:
            prices[ticker] = None
            log.error(f"  {ticker}: all retries failed")
    return prices


def calculate_value(shares, prices):
    total = 0.0
    for t, qty in shares.items():
        p = prices.get(t)
        if qty and p is not None and not (isinstance(p, float) and (math.isnan(p) or math.isinf(p))):
            total += qty * p
    return round(total, 2)


def rebalance_shares(portfolio_value, bucket_alloc_pct, regime, prices):
    shares = {}
    etf_map = REGIME_ETF_MAP.get(regime, REGIME_ETF_MAP["Turbulence"])
    for r in REGIME_ETF_MAP.values():
        for t in r.values():
            shares[t] = 0.0
    for bucket, pct in bucket_alloc_pct.items():
        ticker = etf_map.get(bucket, "BIL")
        dollar = portfolio_value * (pct / 100.0)
        price  = prices.get(ticker)
        if price and price > 0 and not (isinstance(price, float) and math.isnan(price)):
            shares[ticker] = shares.get(ticker, 0.0) + round(dollar / price, 4)
    return {t: v for t, v in shares.items() if v > 0}


def find_latest_newsletter():
    """
    Find the most recent newsletter by ISSUE NUMBER extracted from filename.
    Avoids lexicographic sort bug where 'Issue9' > 'Issue15' as text.
    """
    candidates = list(NEWSLETTER_DIR.glob("MRM_Newsletter*.html"))
    if not candidates:
        return None

    def extract_issue_num(path):
        m = re.search(r'Issue(\d+)', path.name)
        return int(m.group(1)) if m else 1

    candidates.sort(key=extract_issue_num, reverse=True)
    log.info(f"Latest newsletter (by issue number): {candidates[0].name}")
    return candidates[0]


def parse_newsletter(newsletter_path):
    """
    Parse MRM score and allocation table from the newsletter HTML file.

    Uses a TR-based approach: finds the 'Regime-Based Asset Allocation' table
    section, then extracts cells row-by-row. This avoids cross-cell regex
    matching bugs that occurred when using a single regex with re.DOTALL.
    """
    try:
        content = newsletter_path.read_text(encoding="utf-8")
    except Exception as e:
        log.error(f"Cannot read newsletter: {e}")
        return {}, None

    # ── Extract MRM Score ─────────────────────────────────────────────────────
    mrm_score = None
    score_match = re.search(r'<[^>]*>\s*(\d+\.\d+)\s*</[^>]*>', content)
    if score_match:
        try:
            mrm_score = float(score_match.group(1))
        except ValueError:
            pass
    log.info(f"MRM Score parsed: {mrm_score}")

    # ── Extract Allocation Table (TR-based) ───────────────────────────────────
    # Step 1: isolate the allocation table section to avoid matching other tables
    alloc_section_match = re.search(
        r'Regime-Based Asset Allocation.*?</table>',
        content, re.IGNORECASE | re.DOTALL
    )
    if not alloc_section_match:
        log.error("Allocation table section not found in newsletter HTML")
        return {}, mrm_score

    alloc_section = alloc_section_match.group(0)
    log.info(f"Allocation section found ({len(alloc_section)} chars)")

    # Step 2: extract each <tr> within that section
    row_matches = re.findall(
        r'<tr[^>]*>(.*?)</tr>',
        alloc_section, re.IGNORECASE | re.DOTALL
    )

    # Step 3: for each row, extract cells and map asset class → bucket
    bucket_alloc = {}
    for row_html in row_matches:
        # Skip header rows
        if '<th' in row_html.lower():
            continue

        cells = re.findall(
            r'<td[^>]*>(.*?)</td>',
            row_html, re.IGNORECASE | re.DOTALL
        )
        if len(cells) < 2:
            continue

        # Strip all HTML tags to get plain text
        asset_class = re.sub(r'<[^>]+>', '', cells[0]).strip()
        pct_match   = re.search(r'(\d+(?:\.\d+)?)\s*%', cells[1])
        if not pct_match or not asset_class:
            continue

        pct = float(pct_match.group(1))

        matched = False
        for key, bucket in ASSET_CLASS_BUCKET_MAP.items():
            if key.lower() in asset_class.lower():
                bucket_alloc[bucket] = bucket_alloc.get(bucket, 0.0) + pct
                log.info(f"  Mapped '{asset_class}' → {bucket} ({pct}%)")
                matched = True
                break
        if not matched:
            log.warning(f"  Unmatched asset class: '{asset_class}' ({pct}%) — skipped")

    total = sum(bucket_alloc.values())
    if total > 0 and abs(total - 100.0) <= 5.0:
        log.info(f"Allocation parsed OK: {bucket_alloc} (total={total:.1f}%)")
        return bucket_alloc, mrm_score
    else:
        log.error(f"Allocation total={total:.1f}% invalid — aborting rebalance")
        return {}, mrm_score


def check_emergency(portfolio, mrm_score):
    if mrm_score is None:
        return False, None
    history = portfolio.get("history", [])
    if len(history) < CONSECUTIVE_WEEKS - 1:
        return False, None
    recent_scores = [h.get("mrm_score") for h in history[-(CONSECUTIVE_WEEKS-1):]]
    recent_scores.append(mrm_score)
    if any(s is None for s in recent_scores):
        return False, None
    if all(s >= EMERGENCY_SCORE_HIGH for s in recent_scores):
        return True, f"emergency_critical_{mrm_score}"
    if all(s <= EMERGENCY_SCORE_LOW for s in recent_scores):
        return True, f"emergency_resilient_{mrm_score}"
    return False, None


def _has_invalid_float(obj):
    if isinstance(obj, dict):
        return any(_has_invalid_float(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_invalid_float(v) for v in obj)
    if isinstance(obj, float):
        return math.isnan(obj) or math.isinf(obj)
    return False


def main():
    log.info("=== MRM Portfolio Saturday Update ===")
    if FORCE_REBALANCE:
        log.info("FORCE_REBALANCE=true — bypassing date guard")

    if not PORTFOLIO_PATH.exists():
        log.error("portfolio.json not found.")
        sys.exit(1)

    with open(PORTFOLIO_PATH) as f:
        portfolio = json.load(f)

    current       = portfolio["current"]
    inception_val = portfolio["meta"]["inception_value"]
    raw_target    = get_last_friday()
    target_date   = adjust_for_market_holiday(raw_target)

    log.info(f"Target date: {raw_target} → adjusted: {target_date}")

    if not FORCE_REBALANCE and current["date"] >= str(target_date):
        log.info(f"Already up to date ({current['date']} >= {target_date}). Exiting.")
        sys.exit(0)

    newsletter_path = find_latest_newsletter()
    bucket_alloc, mrm_score = ({}, None)
    if newsletter_path:
        bucket_alloc, mrm_score = parse_newsletter(newsletter_path)

    regime = classify_regime(mrm_score)
    log.info(f"Regime: {regime} (score={mrm_score})")

    current_shares = current.get("shares", {})
    tickers_needed = list(set(get_active_tickers(regime)) | set(current_shares.keys()) | {"SPY"})
    log.info(f"Fetching prices for: {tickers_needed}")
    prices = fetch_prices(tickers_needed, target_date)

    for t in tickers_needed:
        p = prices.get(t)
        is_invalid = p is None or (isinstance(p, float) and (math.isnan(p) or math.isinf(p)))
        if is_invalid:
            fallback = current.get("last_prices", {}).get(t)
            fb_valid = fallback is not None and not (isinstance(fallback, float) and (math.isnan(fallback) or math.isinf(fallback)))
            if fb_valid:
                prices[t] = fallback
                log.warning(f"  {t}: using last known ${fallback}")
            else:
                prices[t] = None
                log.error(f"  {t}: no valid price")

    if not prices.get("SPY"):
        log.error("SPY price unavailable. Aborting.")
        sys.exit(1)

    portfolio_value = calculate_value(current_shares, prices)
    pnl_pct      = round((portfolio_value - inception_val) / inception_val * 100, 2)
    bench_shares = current.get("benchmark_spy_shares", 15.1057)
    bench_value  = round(bench_shares * prices["SPY"], 2)
    bench_pnl    = round((bench_value - inception_val) / inception_val * 100, 2)
    alpha        = round(pnl_pct - bench_pnl, 2)

    log.info(f"Portfolio: ${portfolio_value} ({pnl_pct:+.2f}%) | SPY: ${bench_value} ({bench_pnl:+.2f}%) | Alpha: {alpha:+.2f}%")

    # issue_number uses raw_target (the calendar Friday), not target_date (holiday-adjusted).
    # e.g. Jul 3 (Friday, holiday) -> target_date=Jul 2 for prices, but issue = week of Jul 3 = #17.
    inception_date = date(2026, 3, 13)
    issue_number   = ((raw_target - inception_date).days // 7) + 1

    rebalance_triggered = False
    rebalance_reason    = "hold"
    final_bucket_alloc  = current.get("bucket_allocation_pct", {})
    final_regime        = current.get("regime", "Turbulence")

    if bucket_alloc:
        semestral        = is_semestral_rebalance_week(target_date)
        emerg, emerg_why = check_emergency(portfolio, mrm_score)

        if semestral:
            rebalance_triggered = True
            rebalance_reason    = "semestral_rebalance"
            final_bucket_alloc  = bucket_alloc
            final_regime        = regime
            log.info("REBALANCE: semestral")
        elif emerg:
            rebalance_triggered = True
            rebalance_reason    = emerg_why
            final_bucket_alloc  = bucket_alloc
            final_regime        = regime
            log.info(f"REBALANCE: emergency — {emerg_why}")
        else:
            log.info(f"No rebalance — next semestral: Jan or Jun. Score={mrm_score}")
    else:
        log.warning("No valid newsletter allocation — holding current positions.")

    if rebalance_triggered and final_bucket_alloc:
        candidate = rebalance_shares(portfolio_value, final_bucket_alloc, final_regime, prices)
        candidate_value = calculate_value(candidate, prices)
        if candidate_value < portfolio_value * 0.5:
            log.error(f"Rebalanced value ${candidate_value} < 50% — aborting.")
            new_shares = current_shares.copy()
            rebalance_triggered = False
            rebalance_reason = "aborted_invalid_shares"
        else:
            new_shares = candidate
            log.info(f"New shares: {new_shares}")
    else:
        new_shares = current_shares.copy()

    etf_map   = REGIME_ETF_MAP.get(final_regime, REGIME_ETF_MAP["Turbulence"])
    alloc_pct = {t: 0.0 for t in ALL_TICKERS}
    for bucket, pct in final_bucket_alloc.items():
        ticker = etf_map.get(bucket, "BIL")
        alloc_pct[ticker] = alloc_pct.get(ticker, 0.0) + pct

    snapshot = {
        "issue":                         issue_number,
        "date":                          str(target_date),
        "mrm_score":                     mrm_score,
        "regime":                        regime,
        "prices":                        {t: prices[t] for t in tickers_needed if prices.get(t) is not None},
        "prices_confirmed":              {t: True for t in tickers_needed if prices.get(t) is not None},
        "portfolio_value_pre_rebalance": round(portfolio_value, 2),
        "bucket_allocation_pct":         final_bucket_alloc,
        "allocation_pct":                {t: v for t, v in alloc_pct.items() if v > 0},
        "active_etf_map":                etf_map,
        "shares":                        new_shares,
        "portfolio_value":               round(portfolio_value, 2),
        "portfolio_pnl_pct":             pnl_pct,
        "benchmark_spy_value":           bench_value,
        "benchmark_spy_pnl_pct":         bench_pnl,
        "alpha_vs_benchmark_pct":        alpha,
        "rebalance_triggered":           rebalance_triggered,
        "rebalance_reason":              rebalance_reason,
    }

    new_current = {
        "issue":                  issue_number,
        "date":                   str(target_date),
        "regime":                 final_regime,
        "shares":                 new_shares,
        "bucket_allocation_pct":  final_bucket_alloc,
        "allocation_pct":         {t: v for t, v in alloc_pct.items() if v > 0},
        "active_etf_map":         etf_map,
        "last_prices":            {t: prices[t] for t in tickers_needed if prices.get(t) is not None},
        "portfolio_value":        round(portfolio_value, 2),
        "portfolio_pnl_pct":      pnl_pct,
        "benchmark_spy_shares":   bench_shares,
        "benchmark_spy_value":    bench_value,
        "benchmark_spy_pnl_pct":  bench_pnl,
        "alpha_vs_benchmark_pct": alpha,
    }

    if _has_invalid_float(snapshot) or _has_invalid_float(new_current):
        log.error("NaN/Inf detected — ABORTING WRITE.")
        sys.exit(1)

    portfolio["history"].append(snapshot)
    portfolio["current"] = new_current

    with open(PORTFOLIO_PATH, "w") as f:
        json.dump(portfolio, f, indent=2, allow_nan=False)

    log.info("portfolio.json updated successfully.")
    log.info(f"Summary: ${portfolio_value:.2f} ({pnl_pct:+.2f}%) | SPY ${bench_value:.2f} ({bench_pnl:+.2f}%) | Alpha {alpha:+.2f}%")


if __name__ == "__main__":
    main()
