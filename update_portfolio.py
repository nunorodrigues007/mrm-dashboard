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

import json, os, sys, time, re, logging
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mrm_portfolio")

# ── Canonical 6 buckets ───────────────────────────────────────────────────────
BUCKETS = ["US_EQUITIES", "US_TREASURIES", "IG_CREDIT", "COMMODITIES", "CASH", "ALTERNATIVES"]

# ETF per bucket per regime
REGIME_ETF_MAP = {
    "Turbulence": {
        "US_EQUITIES":   "SPY",
        "US_TREASURIES": "IEF",
        "IG_CREDIT":     "LQD",
        "COMMODITIES":   "PDBC",
        "CASH":          "BIL",
        "ALTERNATIVES":  "VNQ",
    },
    "Critical": {
        "US_EQUITIES":   "USMV",   # Min volatility in crisis
        "US_TREASURIES": "TLT",    # Long duration flight-to-safety
        "IG_CREDIT":     "SGOV",   # Ultra-short sovereign
        "COMMODITIES":   "GLD",    # Gold as safe haven
        "CASH":          "BIL",
        "ALTERNATIVES":  "VNQ",    # Reduced but maintained
    },
    "Resilient": {
        "US_EQUITIES":   "QQQ",    # Growth offensive
        "US_TREASURIES": "SHY",    # Short duration, free up for equities
        "IG_CREDIT":     "HYG",    # High yield when credit healthy
        "COMMODITIES":   "PDBC",
        "CASH":          "BIL",
        "ALTERNATIVES":  "IWO",    # Russell 2000 Growth
    },
}

# All tickers across all regimes
ALL_TICKERS = list(set(t for regime in REGIME_ETF_MAP.values() for t in regime.values()))

# Asset class → bucket mapping (handles both newsletter formats)
ASSET_CLASS_BUCKET_MAP = {
    # Issue #3 format
    "US Equities":             "US_EQUITIES",
    "US Equities (Broad)":     "US_EQUITIES",
    "Domestic Equity":         "US_EQUITIES",
    "International Developed": "US_EQUITIES",  # folded into equity bucket
    "US Treasuries":           "US_TREASURIES",
    "US Treasuries (7":        "US_TREASURIES",
    "Sovereign":               "US_TREASURIES",
    "Investment-Grade Credit": "IG_CREDIT",
    "Investment Grade Credit": "IG_CREDIT",
    "Investment-Grade Fixed":  "IG_CREDIT",
    "Commodities":             "COMMODITIES",
    "Real Assets":             "COMMODITIES",
    "Cash":                    "CASH",
    "Cash & Equivalents":      "CASH",
    "Alternatives / Real":     "ALTERNATIVES",
    "Alternatives / Hedge":    "ALTERNATIVES",
    "Alternatives":            "ALTERNATIVES",
}

SEMESTRAL_MONTHS = {1, 6}   # January and June
EMERGENCY_SCORE_HIGH = 8.0  # Critical regime
EMERGENCY_SCORE_LOW  = 4.0  # Resilient regime
CONSECUTIVE_WEEKS    = 2    # Weeks needed to confirm structural change

PORTFOLIO_PATH  = Path("portfolio.json")
NEWSLETTER_DIR  = Path(".")


# ── Regime classification ─────────────────────────────────────────────────────
def classify_regime(score):
    if score is None: return "Turbulence"
    if score >= EMERGENCY_SCORE_HIGH: return "Critical"
    if score <= EMERGENCY_SCORE_LOW:  return "Resilient"
    return "Turbulence"


def get_active_tickers(regime):
    return list(REGIME_ETF_MAP.get(regime, REGIME_ETF_MAP["Turbulence"]).values())


def get_ticker_for_bucket(bucket, regime):
    return REGIME_ETF_MAP.get(regime, REGIME_ETF_MAP["Turbulence"]).get(bucket, "BIL")


# ── Date helpers ──────────────────────────────────────────────────────────────
def get_last_friday():
    today = date.today()
    days_back = (today.weekday() - 4) % 7
    return today - timedelta(days=days_back)


def is_semestral_rebalance_week(target_date):
    """Last Friday of January or June."""
    if target_date.month not in SEMESTRAL_MONTHS:
        return False
    next_friday = target_date + timedelta(days=7)
    return next_friday.month != target_date.month


# ── Price fetching ────────────────────────────────────────────────────────────
def fetch_prices(tickers, target_date, retries=3):
    prices = {}
    start = target_date - timedelta(days=5)
    end   = target_date + timedelta(days=1)
    for ticker in tickers:
        for attempt in range(retries):
            try:
                hist = yf.Ticker(ticker).history(start=str(start), end=str(end))
                if hist.empty: raise ValueError(f"No data for {ticker}")
                hist.index = hist.index.date
                price = float(hist.loc[target_date]["Close"]) if target_date in hist.index else float(hist["Close"].iloc[-1])
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


# ── Portfolio calculations ────────────────────────────────────────────────────
def calculate_value(shares, prices):
    return round(sum((shares.get(t, 0) or 0) * (prices.get(t) or 0) for t in shares), 2)


def rebalance_shares(portfolio_value, bucket_alloc_pct, regime, prices):
    """Calculate new shares given bucket allocations and current regime ETFs."""
    shares = {}
    etf_map = REGIME_ETF_MAP.get(regime, REGIME_ETF_MAP["Turbulence"])
    # Zero out all known tickers first
    for r in REGIME_ETF_MAP.values():
        for t in r.values():
            shares[t] = 0.0
    # Allocate
    for bucket, pct in bucket_alloc_pct.items():
        ticker = etf_map.get(bucket, "BIL")
        dollar = portfolio_value * (pct / 100.0)
        price  = prices.get(ticker)
        if price and price > 0:
            shares[ticker] = shares.get(ticker, 0.0) + round(dollar / price, 4)
    return {t: v for t, v in shares.items() if v > 0}


# ── Newsletter parsing ────────────────────────────────────────────────────────
def find_latest_newsletter():
    candidates = sorted(NEWSLETTER_DIR.glob("MRM_Newsletter*.html"), reverse=True)
    if candidates:
        log.info(f"Latest newsletter: {candidates[0]}")
        return candidates[0]
    return None


def parse_newsletter(newsletter_path):
    """Returns (bucket_alloc_pct dict, mrm_score float|None)."""
    try:
        content = newsletter_path.read_text(encoding="utf-8")
    except Exception as e:
        log.error(f"Cannot read newsletter: {e}")
        return {}, None

    # Parse MRM score
    mrm_score = None
    score_match = re.search(r'<[^>]*>\s*(\d\.\d)\s*</[^>]*>', content)
    if score_match:
        mrm_score = float(score_match.group(1))
    log.info(f"MRM Score: {mrm_score}")

    # Parse allocation table — first column must be text (letter-starting, no %)
    pattern = re.compile(
        r'\|\s*([A-Za-z][^|%]{2,50}?)\s*\|\s*(\d+(?:\.\d+)?)\s*%\s*\|',
        re.IGNORECASE
    )
    bucket_alloc = {}
    for asset_class_raw, pct_str in pattern.findall(content):
        asset_class = asset_class_raw.strip()
        pct = float(pct_str)
        for key, bucket in ASSET_CLASS_BUCKET_MAP.items():
            if key.lower() in asset_class.lower():
                bucket_alloc[bucket] = bucket_alloc.get(bucket, 0.0) + pct
                break

    # Validate
    total = sum(bucket_alloc.values())
    if total > 0 and abs(total - 100.0) <= 5.0:
        log.info(f"Parsed bucket allocation: {bucket_alloc} (total={total:.1f}%)")
        return bucket_alloc, mrm_score
    else:
        log.error(f"Allocation total={total:.1f}% invalid — aborting rebalance")
        return {}, mrm_score


# ── Emergency detection ───────────────────────────────────────────────────────
def check_emergency(portfolio, mrm_score):
    """
    Returns (triggered, reason) if score has been >= 8.0 or <= 4.0
    for CONSECUTIVE_WEEKS consecutive weeks.
    """
    if mrm_score is None:
        return False, None

    history = portfolio.get("history", [])
    if len(history) < CONSECUTIVE_WEEKS - 1:
        return False, None

    # Get last N-1 scores from history
    recent_scores = [h.get("mrm_score") for h in history[-(CONSECUTIVE_WEEKS-1):]]
    recent_scores.append(mrm_score)  # add current week

    if any(s is None for s in recent_scores):
        return False, None

    if all(s >= EMERGENCY_SCORE_HIGH for s in recent_scores):
        return True, f"emergency_critical_{mrm_score}"
    if all(s <= EMERGENCY_SCORE_LOW for s in recent_scores):
        return True, f"emergency_resilient_{mrm_score}"

    return False, None


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=== MRM Portfolio Saturday Update ===")

    if not PORTFOLIO_PATH.exists():
        log.error("portfolio.json not found.")
        sys.exit(1)

    with open(PORTFOLIO_PATH) as f:
        portfolio = json.load(f)

    current        = portfolio["current"]
    inception_val  = portfolio["meta"]["inception_value"]
    target_date    = get_last_friday()

    log.info(f"Target date: {target_date}")

    if current["date"] >= str(target_date):
        log.info(f"Already up to date ({current['date']} >= {target_date}). Exiting.")
        sys.exit(0)

    # ── Parse newsletter ──────────────────────────────────────────────────────
    newsletter_path = find_latest_newsletter()
    bucket_alloc, mrm_score = ({}, None)
    if newsletter_path:
        bucket_alloc, mrm_score = parse_newsletter(newsletter_path)

    regime = classify_regime(mrm_score)
    log.info(f"Regime: {regime} (score={mrm_score})")

    # ── Fetch prices for current regime tickers + all held tickers ────────────
    current_shares  = current.get("shares", {})
    tickers_needed  = list(set(get_active_tickers(regime)) | set(current_shares.keys()) | {"SPY"})
    log.info(f"Fetching prices for: {tickers_needed}")
    prices = fetch_prices(tickers_needed, target_date)

    # Fallback: use last known price if fetch failed
    for t in tickers_needed:
        if prices.get(t) is None:
            prices[t] = current.get("last_prices", {}).get(t)
            if prices[t]:
                log.warning(f"  {t}: using last known price ${prices[t]}")

    # Abort if SPY missing (needed for benchmark)
    if not prices.get("SPY"):
        log.error("SPY price unavailable. Aborting.")
        sys.exit(1)

    # ── Mark-to-market with CURRENT shares ───────────────────────────────────
    portfolio_value = calculate_value(current_shares, prices)
    pnl_pct         = round((portfolio_value - inception_val) / inception_val * 100, 2)
    bench_shares    = current.get("benchmark_spy_shares", 15.1057)
    bench_value     = round(bench_shares * prices["SPY"], 2)
    bench_pnl       = round((bench_value - inception_val) / inception_val * 100, 2)
    alpha           = round(pnl_pct - bench_pnl, 2)

    log.info(f"Portfolio: ${portfolio_value} ({pnl_pct:+.2f}%) | SPY: ${bench_value} ({bench_pnl:+.2f}%) | Alpha: {alpha:+.2f}%")

    # ── Issue number ──────────────────────────────────────────────────────────
    inception_date = date(2026, 3, 14)
    issue_number   = ((target_date - inception_date).days // 7) + 1

    # ── Rebalance decision ────────────────────────────────────────────────────
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

    # ── Calculate new shares ──────────────────────────────────────────────────
    if rebalance_triggered and final_bucket_alloc:
        candidate = rebalance_shares(portfolio_value, final_bucket_alloc, final_regime, prices)
        candidate_value = calculate_value(candidate, prices)
        if candidate_value < portfolio_value * 0.5:
            log.error(f"Rebalanced value ${candidate_value} < 50% of portfolio — aborting.")
            new_shares = current_shares.copy()
            rebalance_triggered = False
            rebalance_reason = "aborted_invalid_shares"
        else:
            new_shares = candidate
            log.info(f"New shares: {new_shares}")
    else:
        new_shares = current_shares.copy()

    # ── Build active ETF allocation for display ───────────────────────────────
    etf_map     = REGIME_ETF_MAP.get(final_regime, REGIME_ETF_MAP["Turbulence"])
    alloc_pct   = {t: 0.0 for t in ALL_TICKERS}
    for bucket, pct in final_bucket_alloc.items():
        ticker = etf_map.get(bucket, "BIL")
        alloc_pct[ticker] = alloc_pct.get(ticker, 0.0) + pct

    # ── Build snapshot ────────────────────────────────────────────────────────
    snapshot = {
        "issue":                      issue_number,
        "date":                       str(target_date),
        "mrm_score":                  mrm_score,
        "regime":                     regime,
        "prices":                     {t: prices[t] for t in tickers_needed if prices.get(t)},
        "prices_confirmed":           {t: True for t in tickers_needed if prices.get(t)},
        "portfolio_value_pre_rebalance": round(portfolio_value, 2),
        "bucket_allocation_pct":      final_bucket_alloc,
        "allocation_pct":             {t: v for t, v in alloc_pct.items() if v > 0},
        "active_etf_map":             etf_map,
        "shares":                     new_shares,
        "portfolio_value":            round(portfolio_value, 2),
        "portfolio_pnl_pct":          pnl_pct,
        "benchmark_spy_value":        bench_value,
        "benchmark_spy_pnl_pct":      bench_pnl,
        "alpha_vs_benchmark_pct":     alpha,
        "rebalance_triggered":        rebalance_triggered,
        "rebalance_reason":           rebalance_reason,
    }

    # ── Update portfolio.json ─────────────────────────────────────────────────
    portfolio["history"].append(snapshot)

    portfolio["current"] = {
        "issue":                  issue_number,
        "date":                   str(target_date),
        "regime":                 final_regime,
        "shares":                 new_shares,
        "bucket_allocation_pct":  final_bucket_alloc,
        "allocation_pct":         {t: v for t, v in alloc_pct.items() if v > 0},
        "active_etf_map":         etf_map,
        "last_prices":            {t: prices[t] for t in tickers_needed if prices.get(t)},
        "portfolio_value":        round(portfolio_value, 2),
        "portfolio_pnl_pct":      pnl_pct,
        "benchmark_spy_shares":   bench_shares,
        "benchmark_spy_value":    bench_value,
        "benchmark_spy_pnl_pct":  bench_pnl,
        "alpha_vs_benchmark_pct": alpha,
    }

    with open(PORTFOLIO_PATH, "w") as f:
        json.dump(portfolio, f, indent=2)

    log.info("portfolio.json updated.")
    log.info(f"Summary: MRM ${portfolio_value:.2f} ({pnl_pct:+.2f}%) | SPY ${bench_value:.2f} ({bench_pnl:+.2f}%) | Alpha {alpha:+.2f}%")


if __name__ == "__main__":
    main()
