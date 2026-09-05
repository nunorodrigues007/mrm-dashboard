"""
update_portfolio.py
MRM Portfolio Saturday Price Updater
Runs every Saturday at 10:00 UTC via GitHub Actions (portfolio.yml)

Rebalance rules:
- STRESS ON/OFF: Gauge B (data.json -> stressGauge) enters or leaves Critical. Immediate,
  no confirmation window — the triggers are already lagging published series (Sahm is
  monthly with a month of publication lag, delinquency is quarterly with five).
- SEMESTRAL: last Friday of January and June
- EMERGENCY: 2 consecutive weeks with score <= 4.0 (offensive). The old score >= 8.0
  defensive branch is gone: the five-pillar score is a LEADING fragility measure and
  never reached 8.0 in 2005-2026, not even in 2008. Critical is Gauge B's call now.
- NO tactical weekly rebalance

ETF universe varies by regime:
- Turbulence (default): SPY, IEF, LQD, PDBC, BIL, VNQ
- Critical  (Gauge B): USMV, TLT/SHY, SGOV, GLD, BIL, VNQ
- Resilient (<= 4.0) : QQQ, SHY, HYG, PDBC, BIL, IWO

In Critical the bucket percentages come from CRITICAL_WEIGHTS, not from the newsletter.
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
    # Critical (score 8-10) splits into two sub-regimes — see docs/critical_subregime.md
    # (Nuno Rodrigues, Jul 2026): the same composite score can mean either a genuine
    # flight-to-quality (10Y falling, TLT hedges the equity drawdown — 2008, 2020) or
    # persistent stress with no rate relief (10Y flat/rising, TLT loses its hedge value
    # and can fall alongside equities — 2022, where TLT lost ~31%). Which one is live is
    # decided each week by determine_critical_subregime(), never assumed from the score.
    "Critical_FTQ": {
        # Confirmed 10Y decline — duration pays as a hedge. Matches the original Critical map.
        "US_EQUITIES": "USMV", "US_TREASURIES": "TLT", "IG_CREDIT": "SGOV",
        "COMMODITIES": "GLD", "CASH": "BIL", "ALTERNATIVES": "VNQ",
    },
    "Critical_Stress": {
        # 10Y flat/rising, or freshly entering Critical with no confirmation yet (the
        # conservative default). TLT is swapped for SHY — no rate-cut tailwind to
        # compensate for duration risk while stress persists.
        "US_EQUITIES": "USMV", "US_TREASURIES": "SHY", "IG_CREDIT": "SGOV",
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

# Palavras-chave, tentadas por ordem, so quando o mapa explicito acima falha.
# Quem escreve a newsletter e um LLM e inventa nomes novos todas as semanas — em 26
# edicoes apareceram "Broad Equities", "Intermediate Govt Bonds", "Short-Term Bills",
# "Fixed Income (Duration)", "Credit (IG/HY)". Cada nome nao reconhecido tirava a sua
# linha do total, o total falhava a validacao dos 100% e o rebalanceamento dessa semana
# nao acontecia, em silencio. A ordem importa: dinheiro antes de duracao (senao
# "Short-Duration / T-Bills" ia parar a treasuries) e credito antes de accoes.
BUCKET_KEYWORDS = [
    ("CASH",          ("t-bill", "tbill", "bills", "cash", "money market",
                       "short-term", "short term", "short-duration", "short duration", "ultra-short")),
    ("US_TREASURIES", ("treasur", "govt bond", "govt. bond", "government bond", "sovereign", "duration")),
    ("IG_CREDIT",     ("investment grade", "investment-grade", "ig credit", "credit")),
    ("US_EQUITIES",   ("equit", "stocks", "large-cap", "large cap", "small-cap", "small cap")),
    ("COMMODITIES",   ("commodit", "real asset", "gold", "energy")),
    ("ALTERNATIVES",  ("reit", "real estate", "alternative", "hedge", "infrastructure")),
]


def map_asset_class(asset_class):
    """(bucket, como) para um nome de classe de activo. `como` e 'exacto', a palavra-chave
    que apanhou, ou None se nao houver correspondencia — nesse caso a linha e ignorada e
    o total nao fecha em 100%, que e o sinal de que a alocacao nao e de confianca."""
    name = asset_class.lower()
    for key, bucket in ASSET_CLASS_BUCKET_MAP.items():
        if key.lower() in name:
            return bucket, "exacto"
    for bucket, words in BUCKET_KEYWORDS:
        for w in words:
            if w in name:
                return bucket, w
    return None, None

SEMESTRAL_MONTHS = {1, 6}
EMERGENCY_SCORE_LOW  = 4.0
CONSECUTIVE_WEEKS    = 2

PORTFOLIO_PATH = Path("portfolio.json")
DATA_PATH      = Path("data.json")
NEWSLETTER_DIR = Path(".")

# ── Vector de pesos para Critical ─────────────────────────────────────────────
# Enquanto o medidor B estiver ligado, estes pesos sobrepoem-se as % da newsletter.
# Decisao de Set 2026, depois do backtest 2007-2026: trocar apenas os instrumentos
# captura menos de metade da proteccao (2008: -10.1% so com a troca de ETF, contra
# +0.7% com instrumentos + pesos; MaxDD -20.9% contra -16.4%). O custo declarado e
# ~0.30 pp de CAGR ao longo de 19 anos — e o premio do seguro, nao um almoco gratis.
CRITICAL_WEIGHTS = {
    "Critical_FTQ": {
        "US_EQUITIES": 15.0, "US_TREASURIES": 35.0, "IG_CREDIT": 15.0,
        "COMMODITIES": 15.0, "CASH": 15.0, "ALTERNATIVES": 5.0,
    },
    "Critical_Stress": {
        "US_EQUITIES": 15.0, "US_TREASURIES": 20.0, "IG_CREDIT": 20.0,
        "COMMODITIES": 15.0, "CASH": 25.0, "ALTERNATIVES": 5.0,
    },
}

# ── Force-rebalance override (set FORCE_REBALANCE=true in env to bypass date check) ──
FORCE_REBALANCE = os.environ.get("FORCE_REBALANCE", "").lower() in ("1", "true", "yes")


def read_stress_gauge(path=DATA_PATH):
    """(active, subregime, basis) do bloco stressGauge do data.json.

    active e True / False / None. None significa que ambos os gatilhos ficaram sem
    dados nessa corrida: o consumidor mantem o estado anterior em vez de assumir OFF.
    Um data.json em falta ou sem o campo e tratado da mesma maneira — nunca como calma."""
    try:
        with open(path) as f:
            sg = json.load(f).get("stressGauge")
    except Exception as e:
        log.warning(f"stressGauge indisponivel ({e}) — a manter o regime anterior.")
        return None, None, "data.json unavailable"
    if not sg:
        log.warning("data.json sem campo stressGauge — a manter o regime anterior.")
        return None, None, "stressGauge field absent"
    return sg.get("active"), sg.get("subregime"), sg.get("basis")


def classify_regime(score, stress_active=None, previous_regime="Turbulence"):
    """Critical e decidido pelo medidor B, nao pelo score.

    O score de cinco pilares mede fragilidade ANTECIPADA (6-18 meses) e numa crise
    tres dos seus pilares melhoram mecanicamente; em 2005-2026 nunca chegou a 8,0,
    nem em 2008, pelo que o ramo Critical do classificador era codigo morto."""
    if stress_active is None:
        return previous_regime or "Turbulence"
    if stress_active:
        return "Critical"
    if score is not None and score <= EMERGENCY_SCORE_LOW:
        return "Resilient"
    return "Turbulence"


def decide_rebalance(regime, was_regime, critical_subregime, was_subregime,
                     semestral, emergency_reason=None):
    """Motivo do rebalanceamento, ou None para manter as posicoes.

    Precedencia: entrada/saida de Critical primeiro — e o evento que a carteira existe
    para responder — depois o calendario semestral, depois a emergencia por score baixo,
    e por fim a troca de sub-regime dentro de Critical (mesmas %, outro instrumento)."""
    # Entrada e saida de Critical sao imediatas, sem janela de confirmacao: os gatilhos
    # do medidor B ja sao series publicadas com atraso (Sahm mensal com um mes de lag,
    # delinquencia trimestral com cinco), e no backtest 2007-2026 a histerese de 1 a 6
    # meses custou ~0,3 pp de CAGR sem melhorar a quebra maxima nem reduzir as trocas.
    if (regime == "Critical") != (was_regime == "Critical"):
        return "stress_on" if regime == "Critical" else "stress_off"
    if semestral:
        return "semestral_rebalance"
    if emergency_reason:
        return emergency_reason
    if regime == "Critical" and critical_subregime != was_subregime:
        return f"critical_subregime_switch:{was_subregime or 'none'}->{critical_subregime}"
    return None


def effective_bucket_alloc(regime, critical_subregime, newsletter_alloc):
    """(alocacao por bucket, origem). Em Critical o vector fixo passa a frente das
    % da newsletter; fora de Critical mandam as % da newsletter."""
    key = resolve_etf_map_key(regime, critical_subregime)
    if key in CRITICAL_WEIGHTS:
        return dict(CRITICAL_WEIGHTS[key]), f"critical override ({key})"
    return dict(newsletter_alloc or {}), "newsletter"


def resolve_etf_map_key(regime, critical_subregime=None):
    """REGIME_ETF_MAP keys for Critical are split into two sub-regimes; every other
    regime maps 1:1. Falls back to the conservative Critical_Stress map if regime is
    Critical but no sub-regime was resolved (should not happen in normal operation)."""
    if regime == "Critical":
        return critical_subregime or "Critical_Stress"
    return regime


def get_active_tickers(regime, critical_subregime=None):
    key = resolve_etf_map_key(regime, critical_subregime)
    return list(REGIME_ETF_MAP.get(key, REGIME_ETF_MAP["Turbulence"]).values())


def get_last_friday():
    today = date.today()
    days_back = (today.weekday() - 4) % 7
    return today - timedelta(days=days_back)


# ── Critical sub-regime: Flight-to-Quality vs Stress-without-relief ──────────────
# Decision confirmed with Nuno, Jul 2026 — see docs/critical_subregime.md.
# Asymmetric, conservative gate: TLT is only "re-earned" on a clear, confirmed 10Y
# decline. Anything else — flat, rising, a fresh entry into Critical, or a failed
# data fetch — defaults to the defensive (Stress-without-relief) path. The two real
# historical FTQ episodes (2008, 2020) both saw fast, unambiguous declines well past
# this threshold within weeks, so the conservative gate costs little upside while
# fully avoiding a repeat of 2022 (TLT -31%, no rate relief the entire year).
# O 10Y passa a vir do medidor B (FRED DGS10, janela de 3 meses), calculado uma so vez
# no fetch_data.py e publicado no data.json. Antes era lido aqui do yfinance (^TNX) numa
# janela de 28 dias: duas fontes e duas janelas para a mesma medida, que podiam discordar
# em publico. A janela de 3 meses tambem se mostrou mais fiavel no backtest 2007-2026
# (2008: +0,7% contra -4,1% com 1 mes; 8 trocas de sub-regime em vez de 16).


def determine_critical_subregime(gauge_subregime, was_critical_last_week):
    """Returns (subregime, note). subregime is 'Critical_FTQ' or 'Critical_Stress'.

    Porta assimetrica e conservadora, confirmada em Jul 2026: o TLT so se reconquista
    com uma queda do 10Y confirmada. Entrada fresca em Critical, sinal ausente ou
    qualquer duvida caem no lado defensivo (Stress-without-relief). Foi o que evitou
    repetir 2022, em que o TLT perdeu ~31% sem alivio de taxas."""
    if not was_critical_last_week:
        note = "Fresh entry into Critical — defaulting to Stress-without-relief until a 10Y decline is confirmed."
        log.info(note)
        return "Critical_Stress", note

    if gauge_subregime == "FTQ":
        note = "Gauge B confirms a 10Y decline of at least 10bp over 3 months — Flight-to-Quality, TLT retained."
        log.info(note)
        return "Critical_FTQ", note

    trend_desc = "unavailable" if gauge_subregime is None else "no confirmed decline"
    note = f"Gauge B reports {trend_desc} on the 10Y — Stress-without-relief (defensive)."
    log.info(note)
    return "Critical_Stress", note


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


MAX_STALE_DAYS = 4  # A confirmed close more than this many days before target_date
                     # is rejected as stale rather than silently accepted (guards against
                     # yfinance returning an old cached window with nothing newer, e.g.
                     # a rate limit or outage that still returns a non-empty, non-NaN result).


def fetch_prices(tickers, target_date, retries=3):
    """Returns (prices, price_dates, stale). `stale[ticker]` is True whenever the
    close actually used is older than MAX_STALE_DAYS relative to target_date — in
    that case prices[ticker] is set to None so the caller's existing last_prices
    fallback (and its staleness check) takes over, instead of silently treating
    a week-old close as if it were fresh."""
    prices = {}
    price_dates = {}
    stale = {}
    start = target_date - timedelta(days=10)
    end   = target_date + timedelta(days=1)
    for ticker in tickers:
        for attempt in range(retries):
            try:
                hist = yf.Ticker(ticker).history(start=str(start), end=str(end))
                if hist.empty:
                    raise ValueError(f"No data for {ticker}")
                hist.index = hist.index.date
                if target_date in hist.index:
                    used_date = target_date
                else:
                    used_date = max(hist.index)
                price = float(hist.loc[used_date]["Close"])
                if math.isnan(price) or math.isinf(price):
                    raise ValueError(f"Invalid price for {ticker}")
                staleness_days = (target_date - used_date).days
                if staleness_days > MAX_STALE_DAYS:
                    raise ValueError(
                        f"Latest available close for {ticker} is {used_date} "
                        f"({staleness_days}d before target {target_date}) — treating as fetch failure"
                    )
                prices[ticker] = round(price, 4)
                price_dates[ticker] = str(used_date)
                stale[ticker] = False
                log.info(f"  {ticker}: ${price:.4f} (as of {used_date})")
                break
            except Exception as e:
                log.warning(f"  {ticker} attempt {attempt+1} failed: {e}")
                time.sleep(2 ** attempt)
        else:
            prices[ticker] = None
            price_dates[ticker] = None
            stale[ticker] = True
            log.error(f"  {ticker}: all retries failed or only stale data available")
    return prices, price_dates, stale


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
    # Step 1: isolate the allocation table.
    # A ancora era o titulo "Regime-Based Asset Allocation", mas quem escreve a
    # newsletter e um LLM e o titulo varia: das 26 edicoes ate Set 2026 so 6 o usam
    # (a de 4 Set diz "Regime-Based Allocation Framework"). Nas outras a alocacao
    # nao era lida e o rebalanceamento ficava silenciosamente por fazer — o semestral
    # de Junho so passou porque calhou uma semana com o titulo certo.
    # A ancora passa a ser a propria tabela: aquela cujo cabecalho tem "Asset Class",
    # presente nas 26 edicoes. O titulo fica como recurso, se algum dia a tabela mudar.
    alloc_section = None
    for table_html in re.findall(r'<table[^>]*>.*?</table>', content, re.IGNORECASE | re.DOTALL):
        if re.search(r'<th[^>]*>\s*Asset Class\s*</th>', table_html, re.IGNORECASE):
            alloc_section = table_html
            break

    if alloc_section is None:
        fallback = re.search(r'Regime-Based\s+(?:\w+\s+)?Allocation.*?</table>',
                             content, re.IGNORECASE | re.DOTALL)
        alloc_section = fallback.group(0) if fallback else None

    if alloc_section is None:
        log.error("Allocation table not found in newsletter HTML")
        return {}, mrm_score

    log.info(f"Allocation table found ({len(alloc_section)} chars)")

    # Step 2: descobrir em que coluna esta a percentagem. A ordem das colunas muda
    # entre edicoes — "Asset Class | Regime Target | Rationale | WoW" numa, mas
    # "Asset Class | Role | Allocation | WoW" noutra. Ler sempre a segunda celula
    # dava a coluna errada e a alocacao inteira saia a zero (foi o caso da edicao 20).
    headers = [re.sub(r'<[^>]+>', '', h).strip().lower()
               for h in re.findall(r'<th[^>]*>(.*?)</th>', alloc_section, re.IGNORECASE | re.DOTALL)]
    pct_col = next((i for i, h in enumerate(headers)
                    if re.search(r'alloc|target|weight|%', h)), None)
    if pct_col:
        log.info(f"Percentage column: {pct_col} ('{headers[pct_col]}')")

    # Step 3: extract each <tr> within that section
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
        # Coluna do cabecalho quando existe; caso contrario, a primeira celula a
        # direita do nome que traga um % — a coluna WoW so tem numeros sem sinal de %.
        pct_match = None
        if pct_col is not None and pct_col < len(cells):
            pct_match = re.search(r'(\d+(?:\.\d+)?)\s*%', cells[pct_col])
        if not pct_match:
            for cell in cells[1:]:
                pct_match = re.search(r'(\d+(?:\.\d+)?)\s*%', cell)
                if pct_match:
                    break
        if not pct_match or not asset_class:
            continue

        pct = float(pct_match.group(1))

        bucket, how = map_asset_class(asset_class)
        if bucket:
            bucket_alloc[bucket] = bucket_alloc.get(bucket, 0.0) + pct
            log.info(f"  Mapped '{asset_class}' → {bucket} ({pct}%) [{how}]")
        else:
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
    # O ramo defensivo (score >= 8,0) saiu daqui: nunca disparou em 20 anos e o seu
    # trabalho passou para o medidor B, que decide Critical sem janela de confirmacao.
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

    was_regime     = current.get("regime", "Turbulence")
    was_subregime  = current.get("critical_subregime")

    stress_active, gauge_subregime, gauge_basis = read_stress_gauge()
    regime = classify_regime(mrm_score, stress_active, was_regime)
    log.info(f"Regime: {regime} (score={mrm_score}, gauge B active={stress_active} — {gauge_basis})")

    critical_subregime = None
    critical_subregime_note = None
    if regime == "Critical":
        if stress_active is None:
            # Corrida sem dados: mantem-se o sub-regime anterior. Deixar a porta decidir
            # aqui degradaria FTQ para Stress e obrigaria a vender o TLT por causa de uma
            # falha de rede — uma avaria de dados nao deve gerar uma transaccao.
            critical_subregime = was_subregime or "Critical_Stress"
            critical_subregime_note = "Gauge B unavailable this run — previous sub-regime retained."
            log.warning(critical_subregime_note)
        else:
            critical_subregime, critical_subregime_note = determine_critical_subregime(
                gauge_subregime, was_critical_last_week=(was_regime == "Critical"))

    current_shares = current.get("shares", {})
    tickers_needed = list(set(get_active_tickers(regime, critical_subregime)) | set(current_shares.keys()) | {"SPY"})
    log.info(f"Fetching prices for: {tickers_needed}")
    prices, price_dates, stale = fetch_prices(tickers_needed, target_date)

    # current["date"] is the date of the *last successful* update — if the last known
    # price is from that same run, it's exactly as stale as the failed fresh fetch would
    # have been, so falling back to it would just reproduce this week's bug silently.
    last_known_date = current.get("date")

    for t in tickers_needed:
        p = prices.get(t)
        is_invalid = p is None or (isinstance(p, float) and (math.isnan(p) or math.isinf(p)))
        if is_invalid:
            fallback = current.get("last_prices", {}).get(t)
            fb_valid = fallback is not None and not (isinstance(fallback, float) and (math.isnan(fallback) or math.isinf(fallback)))
            fallback_is_stale = last_known_date is not None and last_known_date < str(target_date - timedelta(days=MAX_STALE_DAYS))
            if fb_valid and not fallback_is_stale:
                prices[t] = fallback
                stale[t] = True
                log.warning(f"  {t}: fresh fetch failed — using last known ${fallback} ({last_known_date})")
            else:
                prices[t] = None
                log.error(f"  {t}: no valid, sufficiently-fresh price available (fallback dated {last_known_date})")

    if not prices.get("SPY"):
        log.error("SPY price unavailable or too stale (fresh fetch failed and fallback is also stale). Aborting rather than publish incorrect data.")
        sys.exit(1)

    if any(stale.values()):
        stale_tickers = [t for t, v in stale.items() if v]
        log.warning(f"⚠️ Proceeding with STALE fallback prices for: {stale_tickers}. "
                     f"This week's portfolio figures may not reflect this week's actual market close.")

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

    # As % da newsletter sao a alocacao macro de base. Guarda-se a ultima valida num
    # campo proprio porque bucket_allocation_pct passa a guardar a alocacao EFECTIVA,
    # que em Critical e o vector fixo — sem isto, a saida de Critical nao saberia a
    # que percentagens voltar e ficaria congelada no vector defensivo.
    newsletter_alloc = (bucket_alloc
                        or current.get("newsletter_bucket_allocation_pct")
                        or current.get("bucket_allocation_pct", {}))

    semestral        = is_semestral_rebalance_week(target_date)
    emerg, emerg_why = check_emergency(portfolio, mrm_score)
    trigger = decide_rebalance(regime, was_regime, critical_subregime, was_subregime,
                               semestral, emerg_why if emerg else None)

    rebalance_triggered = False
    rebalance_reason    = "hold"
    final_bucket_alloc  = current.get("bucket_allocation_pct", {})
    final_regime        = was_regime
    final_critical_subregime = was_subregime

    if trigger:
        candidate_alloc, alloc_source = effective_bucket_alloc(regime, critical_subregime, newsletter_alloc)
        if candidate_alloc:
            rebalance_triggered = True
            rebalance_reason    = trigger
            final_bucket_alloc  = candidate_alloc
            final_regime        = regime
            final_critical_subregime = critical_subregime
            log.info(f"REBALANCE: {trigger} — allocation from {alloc_source}")
        else:
            rebalance_reason = "no_allocation_available"
            log.warning(f"{trigger} but no valid allocation — holding current positions.")
    else:
        log.info(f"No rebalance — regime={regime}, score={mrm_score}. Next semestral: Jan or Jun.")

    if rebalance_triggered and final_bucket_alloc:
        candidate = rebalance_shares(portfolio_value, final_bucket_alloc, resolve_etf_map_key(final_regime, final_critical_subregime), prices)
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

    etf_map   = REGIME_ETF_MAP.get(resolve_etf_map_key(final_regime, final_critical_subregime), REGIME_ETF_MAP["Turbulence"])
    alloc_pct = {t: 0.0 for t in ALL_TICKERS}
    for bucket, pct in final_bucket_alloc.items():
        ticker = etf_map.get(bucket, "BIL")
        alloc_pct[ticker] = alloc_pct.get(ticker, 0.0) + pct

    snapshot = {
        "issue":                         issue_number,
        "date":                          str(target_date),
        "mrm_score":                     mrm_score,
        "regime":                        regime,
        "critical_subregime":            critical_subregime,
        "critical_subregime_note":       critical_subregime_note,
        "prices":                        {t: prices[t] for t in tickers_needed if prices.get(t) is not None},
        "prices_confirmed":              {t: not stale.get(t, False) for t in tickers_needed if prices.get(t) is not None},
        "data_stale":                    any(stale.get(t, False) for t in tickers_needed),
        "portfolio_value_pre_rebalance": round(portfolio_value, 2),
        "bucket_allocation_pct":         final_bucket_alloc,
        "newsletter_bucket_allocation_pct": dict(newsletter_alloc or {}),
        "stress_gauge_active":           stress_active,
        "stress_gauge_basis":            gauge_basis,
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
        "critical_subregime":     final_critical_subregime,
        "shares":                 new_shares,
        "bucket_allocation_pct":  final_bucket_alloc,
        "newsletter_bucket_allocation_pct": dict(newsletter_alloc or {}),
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

    # Replace any existing entry for this issue number instead of appending a
    # duplicate — matters for corrective re-runs (e.g. FORCE_REBALANCE=true after
    # a stale-data fetch failure) where the same issue is legitimately re-processed.
    portfolio["history"] = [h for h in portfolio["history"] if h.get("issue") != issue_number]
    portfolio["history"].append(snapshot)
    portfolio["history"].sort(key=lambda h: h.get("issue", 0))
    portfolio["current"] = new_current

    with open(PORTFOLIO_PATH, "w") as f:
        json.dump(portfolio, f, indent=2, allow_nan=False)

    log.info("portfolio.json updated successfully.")
    log.info(f"Summary: ${portfolio_value:.2f} ({pnl_pct:+.2f}%) | SPY ${bench_value:.2f} ({bench_pnl:+.2f}%) | Alpha {alpha:+.2f}%")


if __name__ == "__main__":
    main()
