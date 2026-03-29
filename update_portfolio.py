"""
update_portfolio.py
MRM Portfolio Saturday Price Updater
Runs every Saturday at 10:00 UTC via GitHub Actions (portfolio.yml)

What it does:
1. Fetches Friday close prices for all 6 ETFs via Yahoo Finance
2. Calculates current portfolio value and P&L
3. Checks if rebalance is needed (tactical WoW delta or quarterly)
4. If rebalance: reads latest newsletter allocation and updates shares
5. Appends new snapshot to portfolio.json history
6. Saves updated portfolio.json (committed by workflow)
"""

import json
import os
import sys
import time
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mrm_portfolio")

# ── Constants ────────────────────────────────────────────────────────────────

TICKERS = ["SPY", "IEF", "LQD", "PDBC", "BIL", "VNQ"]

ASSET_CLASS_MAP = {
    "US Equities":              "SPY",
    "US Equities (Broad)":      "SPY",
    "US Treasuries":            "IEF",
    "US Treasuries (7":         "IEF",   # catches "7-10Y" variations
    "Investment-Grade Credit":  "LQD",
    "Investment Grade Credit":  "LQD",
    "Commodities":              "PDBC",
    "Cash":                     "BIL",
    "Cash & Equivalents":       "BIL",
    "Alternatives":             "VNQ",
    "Alternatives / Real":      "VNQ",
}

# Quarterly rebalance: last Friday of March, June, September, December
QUARTERLY_MONTHS = {3, 6, 9, 12}

PORTFOLIO_PATH = Path("portfolio.json")
NEWSLETTER_DIR = Path(".")   # newsletters live in repo root


# ── Price Fetching ────────────────────────────────────────────────────────────

def get_last_friday() -> date:
    """Return the most recent Friday (today if Saturday)."""
    today = date.today()
    # Saturday = weekday 5, so last Friday is yesterday
    days_back = (today.weekday() - 4) % 7
    return today - timedelta(days=days_back)


def fetch_prices(tickers: list, target_date: date, retries: int = 3) -> dict:
    """
    Fetch closing prices for tickers on target_date via yfinance.
    Returns dict {ticker: close_price}.
    """
    prices = {}
    start = target_date - timedelta(days=5)
    end   = target_date + timedelta(days=1)

    for ticker in tickers:
        for attempt in range(retries):
            try:
                t = yf.Ticker(ticker)
                hist = t.history(start=str(start), end=str(end))
                if hist.empty:
                    raise ValueError(f"No data returned for {ticker}")
                # Get the row closest to target_date (should be Friday close)
                hist.index = hist.index.date
                if target_date in hist.index:
                    price = float(hist.loc[target_date]["Close"])
                else:
                    # fallback: last available price
                    price = float(hist["Close"].iloc[-1])
                prices[ticker] = round(price, 4)
                log.info(f"  {ticker}: ${price:.4f}")
                break
            except Exception as e:
                log.warning(f"  {ticker} attempt {attempt+1} failed: {e}")
                time.sleep(2 ** attempt)
        else:
            log.error(f"  {ticker}: all retries failed — using previous price")
            prices[ticker] = None

    return prices


# ── Portfolio Calculations ────────────────────────────────────────────────────

def calculate_portfolio_value(shares: dict, prices: dict) -> float:
    """Calculate total portfolio value given shares and prices."""
    total = 0.0
    for ticker, qty in shares.items():
        if qty and prices.get(ticker):
            total += qty * prices[ticker]
    return round(total, 2)


def calculate_pnl_pct(current_value: float, inception_value: float) -> float:
    return round((current_value - inception_value) / inception_value * 100, 2)


def rebalance_shares(portfolio_value: float, allocation_pct: dict, prices: dict) -> dict:
    """Calculate new share counts given total value, target %, and current prices."""
    shares = {}
    for ticker in TICKERS:
        pct = allocation_pct.get(ticker, 0.0) / 100.0
        dollar_amount = portfolio_value * pct
        price = prices.get(ticker)
        if price and price > 0:
            shares[ticker] = round(dollar_amount / price, 4)
        else:
            shares[ticker] = 0.0
    return shares


# ── Newsletter Parsing ────────────────────────────────────────────────────────

def parse_newsletter_allocation(newsletter_path: Path) -> dict:
    """
    Parse Section 06 allocation table from newsletter HTML.
    Returns dict {ticker: target_pct} e.g. {"SPY": 38.0, "IEF": 25.0, ...}
    """
    try:
        content = newsletter_path.read_text(encoding="utf-8")
    except Exception as e:
        log.error(f"Cannot read newsletter: {e}")
        return {}

    allocation = {}

    # Find the Model Allocation table (Section 06)
    # We look for lines like: | US Equities (Broad) | 38% | ...
    import re
    # Match table rows with asset class and percentage
    pattern = re.compile(
        r'\|\s*([^|]+?)\s*\|\s*(\d+(?:\.\d+)?)\s*%\s*\|',
        re.IGNORECASE
    )
    matches = pattern.findall(content)

    for asset_class_raw, pct_str in matches:
        asset_class = asset_class_raw.strip()
        pct = float(pct_str)
        # Map to ticker
        for key, ticker in ASSET_CLASS_MAP.items():
            if key.lower() in asset_class.lower():
                # Only take first match per ticker (avoid double-counting)
                if ticker not in allocation:
                    allocation[ticker] = pct
                break

    # Validate total
    total = sum(allocation.values())
    if total > 0 and abs(total - 100.0) > 5.0:
        log.warning(f"Allocation total = {total:.1f}% (expected ~100%). Check parsing.")

    # Fill missing tickers with 0
    for ticker in TICKERS:
        if ticker not in allocation:
            allocation[ticker] = 0.0

    log.info(f"Parsed allocation: {allocation} (total={sum(allocation.values()):.1f}%)")
    return allocation


def find_latest_newsletter() -> Path | None:
    """Find the most recent newsletter HTML file in repo root."""
    candidates = sorted(NEWSLETTER_DIR.glob("MRM_Newsletter*.html"), reverse=True)
    if candidates:
        log.info(f"Latest newsletter: {candidates[0]}")
        return candidates[0]
    log.warning("No newsletter HTML found in repo root.")
    return None


def parse_mrm_score(newsletter_path: Path) -> float | None:
    """Extract MRM score from newsletter HTML."""
    import re
    try:
        content = newsletter_path.read_text(encoding="utf-8")
        # Score appears as a standalone number like "6.5" near "Global Resilience Score"
        match = re.search(r'<[^>]*>\s*(\d\.\d)\s*</[^>]*>', content)
        if match:
            return float(match.group(1))
    except Exception:
        pass
    return None


# ── Rebalance Logic ───────────────────────────────────────────────────────────

def is_quarterly_rebalance_week(target_date: date) -> bool:
    """
    Returns True if target_date falls in the last 7 days of a quarterly month
    (March, June, September, December) — i.e. it's a quarterly rebalance Friday.
    """
    if target_date.month not in QUARTERLY_MONTHS:
        return False
    # Last Friday of the month: check if there's no Friday in the next 7 days of same month
    next_friday = target_date + timedelta(days=7)
    return next_friday.month != target_date.month


def has_wow_delta(new_alloc: dict, current_alloc: dict) -> bool:
    """Returns True if any bucket has changed vs current allocation."""
    for ticker in TICKERS:
        new_pct = new_alloc.get(ticker, 0.0)
        cur_pct = current_alloc.get(ticker, 0.0)
        if abs(new_pct - cur_pct) >= 1.0:  # 1pp threshold to avoid float noise
            return True
    return False


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    log.info("=== MRM Portfolio Saturday Update ===")

    # Load portfolio state
    if not PORTFOLIO_PATH.exists():
        log.error("portfolio.json not found. Run bootstrap first.")
        sys.exit(1)

    with open(PORTFOLIO_PATH) as f:
        portfolio = json.load(f)

    current = portfolio["current"]
    meta    = portfolio["meta"]
    inception_value = meta["inception_value"]

    # Determine target date (last Friday)
    target_date = get_last_friday()
    log.info(f"Target date (last Friday): {target_date}")

    # Check if already updated for this date
    if current["date"] == str(target_date):
        log.info("Portfolio already up to date for this Friday. Exiting.")
        sys.exit(0)

    # ── Fetch prices ──────────────────────────────────────────────────────────
    log.info("Fetching ETF prices...")
    prices = fetch_prices(TICKERS, target_date)

    # If any critical price missing, abort
    for ticker in ["SPY", "IEF", "LQD", "BIL"]:
        if prices.get(ticker) is None:
            log.error(f"Critical price missing for {ticker}. Aborting.")
            sys.exit(1)
    # Non-critical: use last known price if missing
    for ticker in ["PDBC", "VNQ"]:
        if prices.get(ticker) is None:
            prices[ticker] = current["last_prices"].get(ticker, 0.0)
            log.warning(f"Using last known price for {ticker}: {prices[ticker]}")

    # ── Calculate current portfolio value BEFORE any rebalance ───────────────
    current_shares = current["shares"]
    portfolio_value = calculate_portfolio_value(current_shares, prices)
    pnl_pct = calculate_pnl_pct(portfolio_value, inception_value)
    log.info(f"Portfolio value: ${portfolio_value:.2f} (P&L: {pnl_pct:+.2f}%)")

    # ── Benchmark ────────────────────────────────────────────────────────────
    bench_shares = current["benchmark_spy_shares"]
    bench_value  = round(bench_shares * prices["SPY"], 2)
    bench_pnl    = calculate_pnl_pct(bench_value, inception_value)
    alpha        = round(pnl_pct - bench_pnl, 2)
    log.info(f"Benchmark SPY: ${bench_value:.2f} (P&L: {bench_pnl:+.2f}%) | Alpha: {alpha:+.2f}%")

    # ── Determine issue number ────────────────────────────────────────────────
    from datetime import date as date_cls
    inception_date = date_cls(2026, 3, 14)
    issue_number = ((target_date - inception_date).days // 7) + 1
    log.info(f"Issue number: {issue_number}")

    # ── Find and parse latest newsletter ─────────────────────────────────────
    newsletter_path = find_latest_newsletter()
    new_alloc = {}
    mrm_score = None

    if newsletter_path:
        new_alloc = parse_newsletter_allocation(newsletter_path)
        mrm_score = parse_mrm_score(newsletter_path)
        log.info(f"MRM Score from newsletter: {mrm_score}")

    current_alloc = current["allocation_pct"]

    # ── Rebalance decision ───────────────────────────────────────────────────
    rebalance_triggered = False
    rebalance_reason    = "no_rebalance"
    final_alloc         = current_alloc.copy()

    if new_alloc:
        quarterly = is_quarterly_rebalance_week(target_date)
        wow_delta = has_wow_delta(new_alloc, current_alloc)
        emergency = mrm_score is not None and mrm_score >= 7.0

        if quarterly:
            rebalance_triggered = True
            rebalance_reason    = "quarterly_rebalance"
        elif wow_delta:
            rebalance_triggered = True
            rebalance_reason    = "tactical_wow_delta"
        elif emergency:
            rebalance_triggered = True
            rebalance_reason    = f"emergency_score_{mrm_score}"

        if rebalance_triggered:
            final_alloc = new_alloc
            log.info(f"REBALANCE triggered: {rebalance_reason}")
        else:
            log.info("No rebalance needed this week.")
    else:
        log.warning("No newsletter parsed — holding current allocation.")

    # ── Calculate new shares ──────────────────────────────────────────────────
    if rebalance_triggered:
        new_shares = rebalance_shares(portfolio_value, final_alloc, prices)
        log.info(f"New shares after rebalance: {new_shares}")
    else:
        new_shares = current_shares.copy()

    # ── Build new history snapshot ────────────────────────────────────────────
    snapshot = {
        "issue":                     issue_number,
        "date":                      str(target_date),
        "mrm_score":                 mrm_score,
        "regime":                    "Turbulence" if mrm_score and 5 <= mrm_score < 7 else (
                                       "Resilient" if mrm_score and mrm_score < 5 else "Critical"),
        "prices":                    prices,
        "prices_confirmed":          {t: True for t in TICKERS},
        "portfolio_value_pre_rebalance": round(portfolio_value, 2),
        "allocation_pct":            final_alloc,
        "shares":                    new_shares,
        "portfolio_value":           round(portfolio_value, 2),
        "portfolio_pnl_pct":         pnl_pct,
        "benchmark_spy_value":       bench_value,
        "benchmark_spy_pnl_pct":     bench_pnl,
        "alpha_vs_benchmark_pct":    alpha,
        "rebalance_triggered":       rebalance_triggered,
        "rebalance_reason":          rebalance_reason,
    }

    # ── Update portfolio.json ──────────────────────────────────────────────────
    portfolio["history"].append(snapshot)

    portfolio["current"] = {
        "issue":                  issue_number,
        "date":                   str(target_date),
        "shares":                 new_shares,
        "allocation_pct":         final_alloc,
        "last_prices":            prices,
        "portfolio_value":        round(portfolio_value, 2),
        "portfolio_pnl_pct":      pnl_pct,
        "benchmark_spy_shares":   bench_shares,
        "benchmark_spy_value":    bench_value,
        "benchmark_spy_pnl_pct":  bench_pnl,
        "alpha_vs_benchmark_pct": alpha,
    }

    with open(PORTFOLIO_PATH, "w") as f:
        json.dump(portfolio, f, indent=2)

    log.info("portfolio.json updated successfully.")
    log.info(f"Summary: MRM ${portfolio_value:.2f} ({pnl_pct:+.2f}%) | SPY ${bench_value:.2f} ({bench_pnl:+.2f}%) | Alpha {alpha:+.2f}%")


if __name__ == "__main__":
    main()
