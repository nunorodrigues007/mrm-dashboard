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
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf

import mrm_rules as rules

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mrm_portfolio")

# ── Canonical 6 buckets ───────────────────────────────────────────────────────
# As regras vivem todas em mrm_rules.py. Este ficheiro nao redefine nenhuma —
# importa-as. Antes desta refactorizacao o mapa de ETF existia em tres copias
# (aqui, no send_newsletter.py e no index.html) e as copias tinham divergido.
BUCKETS          = rules.BUCKETS
REGIME_ETF_MAP   = rules.REGIME_ETF_MAP
ALL_TICKERS      = rules.ALL_TICKERS
CRITICAL_WEIGHTS = rules.CRITICAL_WEIGHTS

# O mapa de classes de activo, as palavras-chave e o parser da tabela vivem em
# newsletter_parse.py. Quem escreve a newsletter (send_newsletter.py) valida-a
# com o MESMO parser que a le aqui na semana seguinte: se a tabela nao der para
# ler, a edicao nao chega a ser publicada nem enviada. Enquanto o parser existiu
# so deste lado, uma edicao ilegivel so era descoberta ao sabado seguinte, e o
# motor mantinha as posicoes em silencio.
from newsletter_parse import (ASSET_CLASS_BUCKET_MAP, BUCKET_KEYWORDS,
                              map_asset_class, parse_allocation)
# Continuam a ser nomes deste modulo para quem ja os importava daqui.
__all__ = ["ASSET_CLASS_BUCKET_MAP", "BUCKET_KEYWORDS", "map_asset_class",
           "parse_newsletter", "parse_allocation"]

SEMESTRAL_MONTHS    = rules.SEMESTRAL_MONTHS
EMERGENCY_SCORE_LOW = rules.RESILIENT_MAX
CONSECUTIVE_WEEKS   = rules.CONSECUTIVE_WEEKS

PORTFOLIO_PATH = Path("portfolio.json")
DATA_PATH      = Path("data.json")
NEWSLETTER_DIR = Path(".")

# ── Force-rebalance override (set FORCE_REBALANCE=true in env to bypass date check) ──
FORCE_REBALANCE = os.environ.get("FORCE_REBALANCE", "").lower() in ("1", "true", "yes")


# Os limiares de frescura e a conta da idade vivem em data_freshness.py, que o
# send_newsletter.py tambem importa: quem DECIDE sobre o data.json e quem o
# PUBLICA aos subscritores tem de fazer a mesma pergunta e obter a mesma
# resposta. Enquanto isto viveu aqui, o gerador da newsletter nao podia usa-lo
# sem arrastar o yfinance consigo — e publicava sem verificar.
from data_freshness import (DATA_WARN_AFTER_HOURS, DATA_REFUSE_AFTER_HOURS,
                            data_age_hours, limiar_declarado)


DATA_REFUSED = {"value": False}   # ultima leitura foi recusada?


def _read_data_doc(path=DATA_PATH, now=None):
    """(documento, motivo). O documento vem None quando nao existe, nao le, ou e
    velho demais para se decidir sobre ele. A verificacao de idade fica aqui, uma
    vez so, para que tudo o que se le do data.json a herde.

    Regista tambem, em DATA_REFUSED, se a leitura foi recusada — para o snapshot
    poder declarar a semana como decidida sem dados. Isso era inferido de
    `"old" in gauge_basis`, que so apanhava um dos cinco caminhos de recusa:
    ficheiro ausente, sem carimbo, carimbo no futuro e stressGauge ausente
    ficavam registados como semana normal."""
    try:
        with open(path) as f:
            doc = json.load(f)
    except Exception as e:
        log.warning(f"data.json indisponivel ({e}) — a manter o estado anterior.")
        DATA_REFUSED["value"] = True
        return None, "data.json unavailable"

    meta = doc.get("meta") or {}
    fresh = meta.get("freshness") or {}

    # Os limiares declarados PELO FICHEIRO nunca podem ser mais permissivos do
    # que os do leitor. Um data.json velho ou corrompido que declarasse
    # refuseAfterHours enorme desligava a proteccao que existe para o travar —
    # o documento nao pode ser juiz da sua propria validade. Pode ser mais
    # apertado; nunca mais frouxo.
    warn   = limiar_declarado(fresh, "warnAfterHours", DATA_WARN_AFTER_HOURS)
    refuse = limiar_declarado(fresh, "refuseAfterHours", DATA_REFUSE_AFTER_HOURS)
    age = data_age_hours(meta, now)

    if age is None:
        log.error("data.json nao declara quando foi gerado (ou o carimbo nao e "
                  "legivel) — tratado como velho. A manter o estado anterior.")
        DATA_REFUSED["value"] = True
        return None, "data.json has no generation timestamp"
    if age < -1:
        # Carimbo no futuro: relogio errado, ficheiro adulterado, ou fuso mal
        # aplicado. Idade negativa passava por "fresco" em todas as comparacoes.
        log.error("data.json diz ter sido gerado %.1f h no FUTURO — carimbo nao "
                  "confiavel. A manter o estado anterior.", -age)
        DATA_REFUSED["value"] = True
        return None, f"data.json timestamp is {-age:.1f}h in the future"
    if age > refuse:
        log.error("data.json tem %.1f h (limite %s h) — dados velhos demais para "
                  "decidir. A manter o estado anterior.", age, refuse)
        DATA_REFUSED["value"] = True
        return None, f"data.json is {age:.1f}h old (refuse above {refuse}h)"
    if age > warn:
        log.warning("data.json tem %.1f h (aviso acima de %s h) — a corrida "
                    "anterior do fetch_data pode ter falhado.", age, warn)
    DATA_REFUSED["value"] = False
    return doc, f"data.json is {age:.1f}h old"


# As duas funcoes que aqui viviam — `e_o_vector_de_crise` e
# `e_eco_do_vector_de_crise` — existiam para responder a uma pergunta que
# deixou de se por: "esta tabela escrita pelo modelo e um eco do vector de
# crise que o prompt lhe mostrou, ou uma alocacao macro genuina?". Punha-se
# porque a resposta decidia o que a carteira ia executar a saida de Critical.
# Com os pesos no `REGIME_WEIGHTS`, a saida de Critical executa o vector de
# Turbulence e nenhuma tabela — eco, genuina ou absurda — muda isso.


def read_resilience_score(path=DATA_PATH, now=None):
    """O Global Resilience Score, da fonte que o calcula.

    Estava a ser lido de uma regex sobre o HTML da newsletter — que e escrito por
    um LLM, e que na altura em que este job corre e ainda o da semana ANTERIOR.
    Duas fontes para o mesmo numero, uma delas errada por uma semana: o data.json
    dizia 6,97 e a regex 7,0. O numero que decide o ramo Resilient passa a vir do
    ficheiro que o calcula, sujeito a mesma verificacao de idade.

    Devolve (score, motivo, pilares_em_nd). O score e None quando nao ha
    ficheiro, quando esta velho, ou quando o proprio score veio em n/d por falta
    de dados — e None mantem o regime anterior. A lista de pilares em n/d vem
    junto porque quem decide uma rotacao de emergencia precisa de saber se o
    composto esta completo: um composto a que falta um pilar mexeu por causa da
    renormalizacao, nao por causa do mercado."""
    doc, motivo = _read_data_doc(path, now)
    if doc is None:
        return None, motivo, []
    score = doc.get("globalResilienceScore")
    if score is None:
        log.warning("data.json com globalResilienceScore em n/d — a manter o regime anterior.")
        return None, "resilience score is n/d this run", []
    return float(score), motivo, list(doc.get("ndPillars") or [])


def agora_utc():
    """O instante desta corrida.

    Existe para poder ser fixado num ensaio. Sem isto, a idade do data.json era
    a unica coisa medida contra o relogio real numa corrida em que tudo o resto
    — a sexta, as observacoes, o relogio do gerador — esta fixado na semana
    simulada; o ensaio de sexta so podia passar pelo ramo dos dados velhos, e o
    caminho da semana saudavel nunca era exercitado."""
    return datetime.utcnow()


def read_stress_gauge(path=DATA_PATH, now=None):
    """(active, subregime, basis) do bloco stressGauge do data.json.

    active e True / False / None. None significa que o medidor nao pode ser
    afirmado nesta corrida: o consumidor mantem o estado anterior em vez de
    assumir OFF. Um data.json em falta, sem o campo, ou VELHO, sao tratados da
    mesma maneira — nunca como calma.

    A idade e a verificacao que faltava. Sem ela, uma corrida falhada do
    fetch_data deixava o ficheiro de ontem no sitio e a carteira decidia sobre
    ele as 22:00 sem um unico sinal de que os dados nao eram de hoje."""
    doc, motivo = _read_data_doc(path, now)
    if doc is None:
        return None, None, motivo
    sg = doc.get("stressGauge")
    if not sg:
        log.warning("data.json sem campo stressGauge — a manter o regime anterior.")
        DATA_REFUSED["value"] = True
        return None, None, "stressGauge field absent"
    return sg.get("active"), sg.get("subregime"), sg.get("basis")


# Decisao de regime, de rebalanceamento e de alocacao: definidas em mrm_rules.py.
classify_regime       = rules.classify_regime
decide_rebalance      = rules.decide_rebalance
effective_bucket_alloc = rules.effective_bucket_alloc
resolve_etf_map_key   = rules.resolve_etf_map_key
get_active_tickers    = rules.get_active_tickers


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
# em publico. A janela de 3 meses tambem se mostrou mais fiavel no backtest 2007-2026:
# 2008 fecha a +1,5% contra -3,3% com uma janela de 1 mes, e o CAGR do periodo e
# 6,60% contra 6,27%.


def determine_critical_subregime(gauge_subregime, was_critical_last_week,
                                 was_subregime=None):
    """Porta assimetrica do sub-regime (mrm_rules.subregime_from_gauge), com log."""
    subregime, note = rules.subregime_from_gauge(gauge_subregime, was_critical_last_week,
                                                 was_subregime)
    log.info(note)
    return subregime, note


is_semestral_rebalance_week = rules.is_semestral_rebalance_week


# ── US market holiday calendar ────────────────────────────────────────────────
# Os feriados do NYSE, calculados pelas regras em vez de escritos a mao para um
# ano so. A tabela anterior tinha 2026 e mais nada: a partir de 1 de Janeiro de
# 2027 o ajuste degradava em silencio para "so fins-de-semana", e uma sexta-feira
# de feriado passava a ser tratada como dia de negociacao. As regras abaixo
# reproduzem exactamente a tabela que ca estava (o teste prova-o), e continuam a
# valer nos anos seguintes.
def _obs_nyse(d, e_ano_novo=False):
    """A data em que o NYSE observa um feriado de data fixa.

    Sabado -> a sexta anterior; Domingo -> a segunda seguinte. A excepcao e o
    Ano Novo: quando cai a sabado, o NYSE nao fecha a sexta anterior."""
    if d.weekday() == 5:
        return None if e_ano_novo else d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _pascoa(ano):
    """Domingo de Pascoa (computus gregoriano)."""
    a = ano % 19; b = ano // 100; c = ano % 100
    d = b // 4; e = b % 4; f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(ano, mes, dia)


def _enesima(ano, mes, dia_semana, n):
    d = date(ano, mes, 1)
    while d.weekday() != dia_semana:
        d += timedelta(days=1)
    return d + timedelta(weeks=n - 1)


def _ultima(ano, mes, dia_semana):
    """A ultima ocorrencia desse dia da semana no mes.

    A versao anterior partia do dia 28 e avancava de 7 em 7 enquanto `d+7`
    coubesse no mes — ou seja, nunca chegava aos dias 29-31 — e so depois
    recuava. O Memorial Day saia uma semana adiantado sempre que a ultima
    segunda-feira de Maio cai depois do dia 28: 2027, 2028, 2032, 2033 e 2034.
    Parte-se agora do ULTIMO dia do mes."""
    if mes == 12:
        d = date(ano, 12, 31)
    else:
        d = date(ano, mes + 1, 1) - timedelta(days=1)
    while d.weekday() != dia_semana:
        d -= timedelta(days=1)
    return d


def feriados_nyse(ano):
    """Os dez feriados do NYSE nesse ano, ja com a observancia aplicada."""
    fer = [
        _obs_nyse(date(ano, 1, 1), e_ano_novo=True),   # Ano Novo
        _enesima(ano, 1, 0, 3),                        # MLK
        _enesima(ano, 2, 0, 3),                        # Presidents' Day
        _pascoa(ano) - timedelta(days=2),              # Sexta-feira Santa
        _ultima(ano, 5, 0),                            # Memorial Day
        _obs_nyse(date(ano, 6, 19)),                   # Juneteenth
        _obs_nyse(date(ano, 7, 4)),                    # Independence Day
        _enesima(ano, 9, 0, 1),                        # Labor Day
        _enesima(ano, 11, 3, 4),                       # Thanksgiving
        _obs_nyse(date(ano, 12, 25)),                  # Natal
    ]
    return {d for d in fer if d is not None}


def adjust_for_market_holiday(target_date):
    adjusted = target_date
    fer = feriados_nyse(target_date.year) | feriados_nyse(target_date.year - 1)
    while adjusted in fer or adjusted.weekday() >= 5:
        adjusted -= timedelta(days=1)
    if adjusted != target_date:
        log.warning(f"{target_date} is a market holiday — using {adjusted}")
    return adjusted


# Um fecho CONFIRMADO com mais dias do que isto face a target_date e rejeitado
# como velho, em vez de aceite em silencio — protege contra o yfinance devolver
# uma janela em cache sem nada mais recente (rate-limit, avaria) que ainda assim
# vem nao-vazia e sem NaN.
MAX_STALE_DAYS = 4

# Idade maxima do PRECO DE RECURSO (o ultimo fecho conhecido, guardado em
# `current["last_prices"]`) para poder substituir uma cotacao que falhou.
#
# Isto era o mesmo MAX_STALE_DAYS de 4 dias — e a cadencia e SEMANAL, logo o
# ultimo fecho conhecido tem sempre 7 dias e o recurso era SEMPRE descartado. O
# mecanismo de recurso, o campo `stale`, e o aviso "pelo menos um preco e de
# recurso" eram todos inalcancaveis. Dez dias aceitam a semana anterior e
# recusam duas semanas seguidas em falta.
FALLBACK_MAX_AGE_DAYS = 10

# Alem disto, o preco esta congelado ha tempo a mais para a valorizacao ser
# credivel. Nao para o sistema — declara. Um instrumento renomeado ou retirado
# de bolsa nunca mais tem cotacao, e parar a publicacao todas as semanas para
# sempre e pior do que publicar com o aviso.
PRICE_FROZEN_AFTER_DAYS = 21


def _preco_utilizavel(p):
    """Um preco que se pode usar: numero finito e positivo.

    Escrito UMA vez. Estava escrito tres — duas correctas e uma com `if p:`, que
    aceita `float("nan")` por ser truthy — e era a terceira que decidia o
    benchmark. Duas copias da mesma ideia divergem sempre; tres, mais depressa.
    """
    if p is None or isinstance(p, bool) or not isinstance(p, (int, float)):
        return False
    return not (math.isnan(p) or math.isinf(p)) and p > 0


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
                # Um preco tem de ser positivo. Toda a arquitectura de
                # declaracao assume "preco EM FALTA"; "preco presente e
                # absurdo" nao tinha guarda nenhuma. Um 0.0 passava por bom,
                # `calculate_value` somava `qty * 0`, e a posicao desaparecia
                # da carteira com `valuation_complete: true`, `data_stale:
                # false` e zero avisos — 10.605 -> 9.109, +6,06% -> -8,90%.
                if not (price > 0):
                    raise ValueError(
                        f"{ticker} devolveu um preco nao positivo ({price!r}) — "
                        f"tratado como falha de cotacao, nao como valor real")
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
            if not (p > 0):
                # Nunca deve chegar aqui — fetch_prices e o recurso ja recusam
                # precos nao positivos. Se chegar, e um erro a montante e nao
                # se soma zero em silencio.
                raise ValueError(f"calculate_value: preco nao positivo para {t}: {p!r}")
            total += qty * p
    return round(total, 2)


def rebalance_shares(portfolio_value, bucket_alloc_pct, regime, prices):
    """As accoes que realizam esta alocacao, com os pesos RENORMALIZADOS a 100.

    `validate_allocation` aceita 100 +/- 5 — uma tolerancia razoavel para a
    verificacao, porque quem escreve a tabela e um LLM e arredondamentos
    acontecem. Mas os pesos aceites eram depois multiplicados LITERALMENTE pelo
    valor da carteira, bucket a bucket, sem renormalizacao nenhuma: uma tabela
    que somasse 95 deixava 5% do capital por colocar — $500 numa carteira de
    $10.000 — e esse dinheiro nao ficava em caixa, desaparecia da conta, para
    reaparecer na semana seguinte como uma perda de desempenho de -5%. Uma que
    somasse 105 criava dinheiro do nada.
    A tolerancia e para ACEITAR a tabela; o que se EXECUTA tem de somar 100."""
    total_pedido = sum(bucket_alloc_pct.values())
    if total_pedido <= 0:
        raise ValueError("rebalanceamento abortado: alocacao vazia")
    if abs(total_pedido - 100.0) > 0.001:
        log.info("Alocacao pedida soma %.2f%%; renormalizada a 100%% antes de "
                 "dimensionar (a tolerancia serve para aceitar a tabela, nao "
                 "para deixar capital por colocar).", total_pedido)
        bucket_alloc_pct = {b: pct * 100.0 / total_pedido
                            for b, pct in bucket_alloc_pct.items()}
    shares = {}
    etf_map = REGIME_ETF_MAP.get(regime, REGIME_ETF_MAP["Turbulence"])
    for r in REGIME_ETF_MAP.values():
        for t in r.values():
            shares[t] = 0.0
    # Um preco em falta nao pode fazer desaparecer o dinheiro desse bucket em
    # silencio. Ao entrar em Critical, o TLT e um ticker NOVO — o fallback de
    # precos da semana anterior nao o tem — e vale 35% da carteira. A guarda de
    # valor a jusante so aborta abaixo de 50%, por isso 35% evaporavam-se e
    # apareciam na semana seguinte como performance.
    #
    # A verificacao certa nao e sobre o valor: e sobre os pesos. Ou se consegue
    # colocar toda a alocacao pedida, ou nao se negoceia.
    placed = 0.0
    for bucket, pct in bucket_alloc_pct.items():
        ticker = etf_map.get(bucket, "BIL")
        dollar = portfolio_value * (pct / 100.0)
        price  = prices.get(ticker)
        if price and price > 0 and not (isinstance(price, float) and math.isnan(price)):
            shares[ticker] = shares.get(ticker, 0.0) + round(dollar / price, 4)
            placed += pct
        else:
            log.error("rebalance_shares: sem preco valido para %s (%s, %.1f%%)",
                      ticker, bucket, pct)
    requested = sum(bucket_alloc_pct.values())
    if requested > 0 and placed < requested - 0.01:
        raise ValueError(
            f"rebalanceamento abortado: so foi possivel colocar {placed:.1f}% de "
            f"{requested:.1f}% pedidos — faltam precos. Manter as posicoes e melhor "
            f"do que executar uma alocacao incompleta.")
    return {t: v for t, v in shares.items() if v > 0}


def issue_do_ficheiro(path):
    """O numero de edicao no nome do ficheiro, ou 1 para a edicao inaugural."""
    m = re.search(r'Issue(\d+)', getattr(path, "name", str(path)))
    return int(m.group(1)) if m else 1


def find_latest_newsletter():
    """
    Find the most recent newsletter by ISSUE NUMBER extracted from filename.
    Avoids lexicographic sort bug where 'Issue9' > 'Issue15' as text.
    """
    candidates = list(NEWSLETTER_DIR.glob("MRM_Newsletter*.html"))
    if not candidates:
        return None

    candidates.sort(key=issue_do_ficheiro, reverse=True)
    log.info(f"Latest newsletter (by issue number): {candidates[0].name}")
    return candidates[0]


def parse_newsletter(newsletter_path):
    """Le a newsletter do disco e delega a leitura em newsletter_parse.

    A logica vive no modulo partilhado; aqui fica so o ficheiro e o registo."""
    try:
        content = newsletter_path.read_text(encoding="utf-8")
    except Exception as e:
        log.error(f"Cannot read newsletter: {e}")
        return {}, None

    bucket_alloc, mrm_score, notas = parse_allocation(content)
    for nivel, msg in notas:
        getattr(log, nivel)(msg)
    return bucket_alloc, mrm_score


def check_emergency(portfolio, mrm_score, target_date=None, issue_number=None,
                    nd_pillars=None):
    """Entrada em Resilient exige CONSECUTIVE_WEEKS leituras seguidas <= 4,0.

    Duas coisas tem de ser verdade para uma leitura contar como "seguida":

    - Tem de ser de uma semana ANTERIOR. Numa re-corrida, `history[-1]` e a
      entrada que a primeira passagem acabou de escrever, e o score desta semana
      confirmava-se a si proprio: uma unica leitura <= 4,0 rodava 100% da
      carteira para QQQ/HYG/IWO, saltando por cima da confirmacao que e a unica
      defesa dessa rotacao. A entrada do proprio issue e excluida.
    - Tem de estar dentro da janela de datas. "Seguidas" era contado por posicao
      no historico: se uma corrida falhasse, duas leituras separadas por um mes
      contavam como consecutivas."""
    if mrm_score is None:
        return False, None
    # E um composto a que FALTA um pilar nao dispara uma rotacao de 100%.
    #
    # O protocolo n/d do sistema diz que a ausencia de sinal nao e um sinal.
    # Quando um pilar sai do composto, os pesos renormalizam e o score MEXE —
    # para baixo, se o pilar que saiu estava alto. Um pilar cravado no 10,0 que
    # sai leva o composto de 5,05 para 3,40 e isso, sozinho, faz `emergency`:
    # 100% da carteira para QQQ/HYG/IWO, sem a janela de confirmacao, por causa
    # de um pilar que DESAPARECEU. E quando ele volta — na sexta em que o
    # operador actualiza a referencia dos earnings, que e uma accao de
    # manutencao — a carteira roda outra vez ao contrario. Duas rotacoes
    # completas governadas pela cadencia de uma pessoa, nao por um evento de
    # mercado.
    #
    # Isto e a guarda de baixo nivel. A de cima — o score nao decidir o regime
    # enquanto houver um pilar fora — esta no `main()`, e cobre as duas
    # direccoes. Enquanto um pilar estiver em n/d NAO ha entrada em Resilient de
    # todo: `emergency_resilient_*` E a entrada confirmada (a janela de
    # CONSECUTIVE_WEEKS e aplicada aqui), e nao ha outro caminho.
    if nd_pillars:
        log.warning("check_emergency: %s fora do composto — a rotacao de "
                    "emergencia NAO dispara sobre um composto incompleto. A "
                    "ausencia de um sinal nao e um sinal.",
                    ", ".join(sorted(nd_pillars)))
        return False, None
    history = portfolio.get("history", [])
    if issue_number is not None:
        history = [h for h in history if h.get("issue") != issue_number]
    if len(history) < CONSECUTIVE_WEEKS - 1:
        return False, None

    anteriores = history[-(CONSECUTIVE_WEEKS - 1):]
    if target_date is not None:
        # Uma leitura da semana N-1 tem de ter data dentro de N-1 semanas, com
        # folga de 3 dias para feriados e para o dia em que o job corre.
        limite = target_date - timedelta(weeks=CONSECUTIVE_WEEKS - 1, days=3)
        dentro = []
        for h in anteriores:
            try:
                d = date.fromisoformat(str(h.get("date", "")))
            except (ValueError, TypeError):
                log.warning("check_emergency: entrada do historico sem data legivel "
                            "(issue %s) — nao conta como semana consecutiva.", h.get("issue"))
                return False, None
            if d < limite:
                log.info("check_emergency: a leitura de %s esta fora da janela de %d "
                         "semanas — a confirmacao recomeca.", d, CONSECUTIVE_WEEKS - 1)
                return False, None
            dentro.append(h)
        anteriores = dentro

    # Uma leitura ANTERIOR sobre um composto incompleto vale o mesmo que uma
    # leitura em falta: reinicia a janela. E o mesmo tratamento que ja se da a
    # uma entrada com data ilegivel, e pela mesma razao — nao se confirma uma
    # rotacao de 100% da carteira com um numero que nao mede o que diz medir.
    #
    # Entradas gravadas ANTES deste campo existir nao trazem `score_complete`;
    # nesse caso nao ha como saber, e nao saber nao pode contar como confirmado.
    # Sao no maximo `CONSECUTIVE_WEEKS - 1` semanas de espera, uma so vez.
    for h in anteriores:
        if h.get("score_complete") is not True:
            log.info("check_emergency: a leitura de %s foi calculada sobre um "
                     "composto incompleto (ou anterior ao registo da "
                     "completude) — a confirmacao recomeca.", h.get("date"))
            return False, None
    recent_scores = [h.get("mrm_score") for h in anteriores]
    recent_scores.append(mrm_score)
    if any(s is None for s in recent_scores):
        return False, None
    # O ramo defensivo (score >= 8,0) saiu daqui: nunca disparou em 20 anos e o seu
    # trabalho passou para o medidor B, que decide Critical sem janela de confirmacao.
    if all(s <= EMERGENCY_SCORE_LOW for s in recent_scores):
        return True, f"emergency_resilient_{mrm_score}"
    return False, None


def write_json_atomic(path, obj):
    """Escreve para um ficheiro temporario no mesmo directorio e so depois o move
    por cima do destino. os.replace e atomico no mesmo sistema de ficheiros, por
    isso ou o ficheiro antigo fica intacto ou o novo fica completo — nunca um
    portfolio.json truncado a meio de um json.dump, que era o estado do sistema
    se o runner morresse durante a escrita."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        # Nao deixar o temporario para tras: um .tmp orfao no directorio do
        # repositorio acaba num commit ou confunde quem esta a diagnosticar.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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

    # ── O numero da edicao normaliza-se a ENTRADA, em todo o ficheiro ────────
    #
    # `history.sort(key=lambda h: h.get("issue", 0))` levanta TypeError com uma
    # string, e o TypeError acontece ANTES da escrita atomica: o ficheiro fica
    # como estava, o job morre, a newsletter recusa-se a publicar por nao
    # encontrar a edicao N, e a sexta seguinte morre da mesma maneira. Nao ha
    # recuperacao automatica nenhuma — o unico caminho que faria o ficheiro
    # avancar e o que esta travado. E a mesma familia de tres formas (`27`,
    # `"27"`, `27.0`) que ja foi fechada no `sent_issues.json`, com o mesmo
    # normalizador; o `portfolio.json` tem cinco leitores deste campo e nenhum
    # o normalizava.
    # E as entradas que NAO sao dicionarios saem da lista sobre que os leitores
    # trabalham — preservadas a parte, como o registo de envios ja faz com uma
    # linha solta escrita a mao. A guarda `isinstance` existia so aqui: cinco
    # leitores a jusante fazem `h.get(...)` sem ela, e a primeira coisa que
    # acontece com uma linha de texto no historico e um AttributeError ANTES da
    # escrita atomica — o ficheiro fica como estava, o job morre, o envio e
    # saltado, e a sexta seguinte morre da mesma maneira. E o mesmo defeito que
    # o `"issue": "26"` da ronda anterior, pela forma vizinha.
    _hist_bruto = portfolio.get("history")
    if not isinstance(_hist_bruto, list):
        if _hist_bruto is not None:
            log.error(f"history nao e uma lista ({type(_hist_bruto).__name__}): "
                      f"tratado como vazio.")
        _hist_bruto = []
    # As que ja tinham sido postas de parte numa corrida anterior vem com elas:
    # senao a primeira sexta preservava-as e a segunda apagava-as em silencio.
    _hist_soltas = [h for h in _hist_bruto if not isinstance(h, dict)]
    _guardadas = portfolio.get("history_unparsed")
    if isinstance(_guardadas, list):
        _hist_soltas = _guardadas + _hist_soltas
    elif _guardadas is not None:
        _hist_soltas = [_guardadas] + _hist_soltas
    if _hist_soltas:
        log.error(f"{len(_hist_soltas)} entrada(s) do historico nao sao "
                  f"registos ({[type(x).__name__ for x in _hist_soltas]}): "
                  f"preservadas fora da lista de trabalho, para nao matarem o "
                  f"job. Corrigir o portfolio.json a mao.")
    _h_sem_numero = []
    portfolio["history"] = [h for h in _hist_bruto if isinstance(h, dict)]
    for _h_norm in list(portfolio["history"]):
        _n_norm = rules.numero_de_edicao(_h_norm.get("issue"))
        if _n_norm is None:
            # Tambem sai da serie. Empurra-lo para o FIM punha-o exactamente em
            # `history[-1]`, que e o registo que o cartao de topo da carteira e
            # a primeira linha do log do site leem: na semana em que a carteira
            # rodou 100% para o mapa de Critical, a pagina publicava "Hold — no
            # rebalance", o score de outra semana, e uma linha `#null`. Um
            # registo que nao casa com semana nenhuma nao pertence a serie que
            # os consumidores iteram, pela MESMA razao por que as linhas que nao
            # sao registos ja nao pertencem — e o argumento do `history[0]`
            # (reconstrucao do benchmark) resolve-se por o tirar da lista, nao
            # por o empurrar para a outra ponta dela.
            log.error(f"entrada do historico com issue ilegivel "
                      f"({_h_norm.get('issue')!r}): nao casa com semana nenhuma, "
                      f"e preservada fora da serie.")
            _h_sem_numero.append(_h_norm)
            continue
        if not isinstance(_h_norm.get("issue"), int) or isinstance(
                _h_norm.get("issue"), bool):
            # A comparacao tem de ser de TIPO, nao de valor: `1.0 != 1` e False
            # em Python, portanto um float atravessava a normalizacao intacto e
            # o campo ficava a alternar entre duas formas no mesmo ficheiro.
            log.error(f"entrada do historico com issue em forma inesperada "
                      f"({_h_norm.get('issue')!r}) — lida como {_n_norm}.")
            _h_norm["issue"] = _n_norm
    if _h_sem_numero:
        portfolio["history"] = [h for h in portfolio["history"]
                                if h not in _h_sem_numero]

    current       = portfolio["current"]
    inception_val = portfolio["meta"]["inception_value"]
    raw_target    = get_last_friday()
    target_date   = adjust_for_market_holiday(raw_target)

    log.info(f"Target date: {raw_target} → adjusted: {target_date}")

    if not FORCE_REBALANCE and current["date"] >= str(target_date):
        log.info(f"Already up to date ({current['date']} >= {target_date}). Exiting.")
        sys.exit(0)

    # A newsletter da as PERCENTAGENS de alocacao. O score vem do data.json, que
    # e quem o calcula; o numero que a newsletter carrega e o da semana anterior
    # (este job corre antes de a desta semana ser escrita) e vem de uma regex
    # sobre HTML gerado por um LLM. Servem so para comparar.
    newsletter_path = find_latest_newsletter()
    bucket_alloc, newsletter_score = ({}, None)
    issue_lido = None
    if newsletter_path:
        bucket_alloc, newsletter_score = parse_newsletter(newsletter_path)
        issue_lido = issue_do_ficheiro(newsletter_path)

    # issue_number usa raw_target (a sexta de calendario), nao target_date
    # (ajustado a feriado): 3 Jul (sexta, feriado) -> precos de 2 Jul, mas a
    # edicao continua a ser a da semana de 3 Jul.
    inception_date = date(2026, 3, 13)
    issue_number   = ((raw_target - inception_date).days // 7) + 1

    # ── O estado lido do ficheiro normaliza-se UMA vez, a entrada ────────────
    #
    # E escreve-se de volta no `current`, que e a fonte de todos os leitores
    # deste modulo. Normalizar so em variaveis locais nao chega: ha tres sitios
    # que voltam a ler `current.get("regime")` e `current.get("critical_
    # subregime")` crus — o `held_sub_atual` da re-corrida e o
    # `held_regime`/`held_subregime` do snapshot — e o snapshot REESCREVE o
    # valor mau no portfolio.json, semana apos semana: o sistema nunca se
    # curava. Com a correccao aqui, a primeira sexta a seguir a um ficheiro
    # reconstruido a mao deixa-o limpo.
    #
    # Os dois campos sao vocabularios que se confundem com facilidade: o medidor
    # publica "FTQ"/"STRESS", o portfolio.json guarda "Critical_FTQ", o site
    # imprime "Portfolio on the Critical_FTQ map", o RUNBOOK fala nos dois
    # vectores como o estado da carteira, e o Caso 4 manda reconstruir estado a
    # mao. Com `"regime": "Critical_FTQ"`, `was_critical_last_week` ficava FALSO,
    # a porta assimetrica via uma entrada fresca, e o motor vendia todo o TLT —
    # 35% da carteira — numa semana em que o medidor confirmou a descida do 10Y.
    # SEM default. `get(..., "Turbulence")` devolvia um regime LEGIVEL para uma
    # chave AUSENTE — que e a forma natural de uma reconstrucao a mao e o que
    # uma versao anterior escrevia — e o `normaliza_regime` saia no primeiro
    # ramo sem chegar a perguntar ao mapa. A carteira detinha TLT, o motor via
    # uma entrada fresca em Critical, forcava o lado defensivo e VENDIA 35% dela
    # na semana em que o medidor confirmou a descida do 10Y. A decisao "nao sei
    # -> Turbulence sem sub-regime, porta fechada" ja existe LOGO A SEGUIR, e e
    # ela que deve decidir — nao um default que curto-circuita a leitura.
    _reg_lido_ficheiro = current.get("regime")
    _sub_lido_ficheiro = current.get("critical_subregime")
    was_regime, was_subregime, _nota_norm = rules.normaliza_regime(
        _reg_lido_ficheiro, _sub_lido_ficheiro, current.get("active_etf_map"))
    if _nota_norm:
        log.error(f"estado ilegivel no portfolio.json: {_nota_norm}")
    if was_regime is None:
        # Nao se adivinha um regime que ninguem escreveu. Sem saber em que
        # regime a carteira estava, nao ha entrada nem saida para calcular: a
        # unica decisao segura e a que o motor ja toma quando os dados sao
        # recusados — manter, e deixar o registo do porque.
        log.error(f"regime ilegivel ({_reg_lido_ficheiro!r}): a carteira e "
                  f"tratada como Turbulence SEM sub-regime, e a porta "
                  f"assimetrica fecha-se — uma entrada fresca em Critical vai "
                  f"para o lado defensivo, que e onde a duvida pertence.")
        was_regime, was_subregime = "Turbulence", None
    # O sub-regime perdido ja foi lido do mapa pelo `normaliza_regime`. Se nem o
    # campo nem o mapa identificam o vector, o lado por omissao e o DEFENSIVO —
    # a porta assimetrica diz que o TLT so se ganha com uma descida do 10Y
    # confirmada, e uma avaria de dados nao e uma descida confirmada. E adopcao
    # de REGISTO, nao uma transaccao: o valor passa a ser legivel, e como o ramo
    # "corrida sem dados" retem o mesmo valor, `decide_rebalance` nao ve troca
    # nenhuma. Sem isto, ou se fabricava uma venda do TLT sem sinal, ou o campo
    # ficava a None para sempre.
    if was_regime == "Critical" and was_subregime is None:
        log.error("critical_subregime em falta e o mapa detido nao identifica "
                  "nenhum dos dois vectores de Critical: adopta-se o lado "
                  "defensivo, sem transaccao.")
        was_subregime = "Critical_Stress"

    # E o ficheiro fica curado: todos os leitores a jusante veem o valor sao.
    current["regime"] = was_regime
    if was_subregime is None:
        current.pop("critical_subregime", None)
    else:
        current["critical_subregime"] = was_subregime

    # ── Re-corrida da MESMA semana ────────────────────────────────────────────
    # `current` ja reflecte o que a primeira corrida escreveu, por isso uma
    # segunda passagem via `was_regime = "Critical"` e concluia que a semana
    # anterior ja estava em Critical. Isso ABRIA a porta assimetrica FTQ: a
    # primeira corrida forcava Critical_Stress (entrada fresca), a segunda
    # deixava entrar o TLT com 35% da carteira — anulando exactamente a
    # proteccao que a porta existe para dar, so por se correr o job outra vez.
    #
    # Numa re-corrida, o estado "anterior" e o da semana ANTES desta, nao o que
    # esta semana ja escreveu. Assim uma segunda passagem sobre os mesmos dados
    # toma a mesma decisao que a primeira.
    # A porta assimetrica FTQ decide-se por `was_critical_last_week`, que e
    # derivado de `was_regime` — nao de `was_subregime`. Numa re-corrida sem a
    # edicao N-1, repor so o sub-regime nao fechava a porta.
    was_critical_last_week = (was_regime == "Critical")
    held_sub_atual = current.get("critical_subregime")
    subregime_congelado = None    # so numa re-corrida sem a edicao N-1

    ja_corrida = next((h for h in portfolio.get("history", [])
                       if h.get("issue") == issue_number), None)
    if ja_corrida is not None:
        base = next((h for h in portfolio.get("history", [])
                     if h.get("issue") == issue_number - 1), None)
        if base is not None:
            # Pelo MESMO normalizador que guarda a entrada do `current`. Este
            # ramo lia os dois campos crus, e e o caminho que o RUNBOOK manda
            # percorrer no Caso 3: com `"regime": "Critical_FTQ"` na entrada do
            # historico, `was_critical_last_week` ficava falso, a porta
            # assimetrica via uma entrada fresca e o motor vendia o TLT.
            was_regime, was_subregime, _nota_rr = rules.normaliza_regime(
                base.get("regime"), base.get("critical_subregime"),
                base.get("active_etf_map"))
            if _nota_rr:
                log.error(f"estado ilegivel na entrada {base.get('issue')} do "
                          f"historico: {_nota_rr}")
            if was_regime is None:
                was_regime, was_subregime = "Turbulence", None
            if was_regime == "Critical" and was_subregime is None:
                was_subregime = "Critical_Stress"
            was_critical_last_week = (was_regime == "Critical")
            log.warning("Re-corrida do issue %s: o estado anterior e o da edicao %s "
                        "(%s/%s), nao o que esta semana ja escreveu. A decisao vai "
                        "ser a mesma que na primeira passagem.",
                        issue_number, base.get("issue"), was_regime, was_subregime)
        else:
            # A edicao N-1 nao esta no historico (push falhado, ficheiro
            # truncado). Ir buscar a ultima que la esteja daria um estado
            # anterior ERRADO — e se essa fosse Critical, a porta assimetrica
            # abria-se e entrava TLT com 35% da carteira, que e exactamente o
            # defeito que esta rebobinagem existe para fechar.
            #
            # Sem saber o estado anterior, assume-se o lado defensivo: sem
            # sub-regime, o que forca Critical_Stress numa entrada em Critical.
            # `was_subregime = None` fechava a porta mas fabricava um evento: com
            # o regime a manter-se Critical, decide_rebalance via `None` != o
            # sub-regime calculado e devolvia
            # `critical_subregime_switch:none->Critical_Stress` — uma transaccao
            # que nunca existiu, e que por ser `rebalance_triggered=True` passava
            # por cima da preservacao e APAGAVA o `stress_on` original.
            #
            # O que fecha a porta e `was_critical_last_week`. O sub-regime
            # anterior mantem-se o que a carteira detem, para nao inventar uma
            # troca onde nao houve nenhuma.
            was_subregime = held_sub_atual
            was_critical_last_week = False   # fecha a porta assimetrica
            # E mais do que fechar a porta: sem saber o estado anterior, esta
            # corrida nao tem informacao nova sobre o sub-regime. Recalcula-lo
            # forcava Critical_Stress, e se a carteira detivesse Critical_FTQ
            # isso fabricava uma `critical_subregime_switch` — uma venda real de
            # 35% da carteira sem gatilho, que por vir com
            # `rebalance_triggered=True` passava por cima da preservacao e
            # apagava o registo original.
            #
            # Sem informacao nova, mantem-se o que esta.
            subregime_congelado = held_sub_atual
            log.error("Re-corrida do issue %s mas a edicao %s nao esta no "
                      "historico. Nao e possivel reconstruir o estado anterior: "
                      "assume-se entrada fresca (sem sub-regime), que e o lado "
                      "defensivo. Verificar o portfolio.json.",
                      issue_number, issue_number - 1)

    mrm_score, score_basis, nd_pillars_data = read_resilience_score(now=agora_utc())
    log.info(f"Resilience score: {mrm_score} (do data.json — {score_basis})")
    if newsletter_score is not None and mrm_score is not None and abs(newsletter_score - mrm_score) > 0.15:
        log.warning("O score da newsletter (%.2f) diverge do publicado (%.2f). "
                    "Decide o publicado; isto e so um aviso de que a edicao "
                    "anterior ficou desalinhada.", newsletter_score, mrm_score)

    # Um composto INCOMPLETO nao decide o regime — em nenhuma das direccoes.
    #
    # Quando um pilar sai, os pesos renormalizam e o score MEXE. Para baixo se o
    # pilar que saiu estava alto, para CIMA se estava baixo — e essa subida
    # bastava para `resilient_off`: rotacao de 100% da carteira, imediata, sem
    # janela de confirmacao, porque uma serie da FRED nao publicou naquela
    # semana. Quando a serie voltava, a re-entrada exigia duas leituras
    # consecutivas: saia-se num instante por causa de um buraco de dados e
    # demorava-se duas semanas a voltar. A guarda anterior so cobria a ENTRADA,
    # que e o lado que NAO gera a transaccao.
    #
    # O protocolo n/d ja tem a resposta: a ausencia de sinal nao e um sinal. Um
    # score calculado sobre quatro pilares em vez de cinco nao e o score — e um
    # numero diferente —, portanto para efeitos de REGIME vale como n/d, e n/d
    # mantem o regime anterior. O medidor B nao passa por aqui: ele decide
    # Critical sozinho, e a saida obrigatoria de Critical quando ele diz OFF
    # tambem, porque `classify_regime` trata o medidor antes do score.
    _score_regime = mrm_score
    if nd_pillars_data:
        log.warning("Pilares fora do composto (%s): o score de %s foi calculado "
                    "sobre os restantes e NAO decide o regime esta semana — o "
                    "regime anterior mantem-se. Uma renormalizacao nao e um "
                    "sinal de mercado.",
                    ", ".join(sorted(nd_pillars_data)), mrm_score)
        _score_regime = None

    stress_active, gauge_subregime, gauge_basis = read_stress_gauge(now=agora_utc())
    signalled_regime = classify_regime(_score_regime, stress_active, was_regime)

    # A entrada em Resilient exige confirmacao de duas leituras, e essa
    # confirmacao tem de ser aplicada AQUI — nao no motivo do rebalanceamento.
    # Bastava a semana calhar na ultima sexta de Janeiro ou Junho para um unico
    # score <= 4,0 rodar a carteira para QQQ/HYG/IWO sob a etiqueta
    # "semestral_rebalance", saltando por cima da confirmacao.
    emerg, emerg_why = check_emergency(portfolio, _score_regime, target_date,
                                       issue_number, nd_pillars=nd_pillars_data)
    regime = rules.confirm_regime(signalled_regime, was_regime, emerg_why)
    if regime != signalled_regime:
        log.info("Regime sinalizado %s ainda nao confirmado (faltam leituras "
                 "consecutivas) — mantem-se %s.", signalled_regime, regime)
    log.info(f"Regime: {regime} (sinalizado={signalled_regime}, score={mrm_score}, "
             f"gauge B active={stress_active} — {gauge_basis})")

    critical_subregime = None
    critical_subregime_note = None
    if regime == "Critical":
        if stress_active is None:
            # Corrida sem dados: mantem-se o sub-regime anterior. Deixar a porta decidir
            # aqui degradaria FTQ para Stress e obrigaria a vender o TLT por causa de uma
            # falha de rede — uma avaria de dados nao deve gerar uma transaccao.
            critical_subregime = was_subregime or "Critical_Stress"
            # `both triggers without data` era so um dos dois casos de
            # `active is None`: o outro e um gatilho em falta ao lado de um
            # gatilho quieto, e ai um deles foi lido. Esta nota e desenhada no
            # site (`renderSubregimeStatus`), portanto a imprecisao publicava-se.
            critical_subregime_note = (
                f"Gauge B returned no usable reading this run "
                f"({gauge_basis or 'no basis declared'}) — previous sub-regime "
                f"retained.")
            log.warning(critical_subregime_note)
        else:
            if subregime_congelado is not None:
                critical_subregime = subregime_congelado
                critical_subregime_note = ("Re-run without the previous week's record: "
                                           "the sub-regime in force was retained rather "
                                           "than recomputed, to avoid a switch that no "
                                           "signal called for.")
                log.warning(critical_subregime_note)
            else:
                critical_subregime, critical_subregime_note = determine_critical_subregime(
                    gauge_subregime, was_critical_last_week=was_critical_last_week,
                    was_subregime=was_subregime)

    current_shares = current.get("shares", {})
    tickers_needed = list(set(get_active_tickers(regime, critical_subregime)) | set(current_shares.keys()) | {"SPY"})
    log.info(f"Fetching prices for: {tickers_needed}")
    prices, price_dates, stale = fetch_prices(tickers_needed, target_date)

    # A idade de um preco de recurso mede-se pela data DESSE PRECO, guardada em
    # `last_price_dates`. Media-se por `current["date"]` — a data da CORRIDA —
    # que avanca sete dias por semana quer o preco avance quer nao. Com a
    # cadencia semanal, `target_date - 10 dias` ficava sempre atras dela, o
    # recurso nunca expirava, e um feed morto era re-abencoado indefinidamente:
    # nove semanas com o mesmo preco e o mesmo P&L ao centimo, sem um aviso.
    #
    # Sem a data do preco (primeira corrida depois desta alteracao), usa-se a
    # data da corrida como aproximacao — e uma vez so, porque a partir dai a
    # data real fica guardada.
    prev_price_dates = current.get("last_price_dates") or {}
    last_known_date = current.get("date")
    limite_recurso = str(target_date - timedelta(days=FALLBACK_MAX_AGE_DAYS))

    for t in tickers_needed:
        p = prices.get(t)
        is_invalid = p is None or (isinstance(p, float) and (math.isnan(p) or math.isinf(p)))
        if is_invalid:
            fallback = current.get("last_prices", {}).get(t)
            fb_valid = _preco_utilizavel(fallback)
            d_fb = prev_price_dates.get(t) or last_known_date
            fallback_is_stale = d_fb is not None and str(d_fb) < limite_recurso
            if fb_valid and not fallback_is_stale:
                prices[t] = fallback
                stale[t] = True
                # A data ACOMPANHA o preco. Sem isto, a semana seguinte volta a
                # gravar `None` e a idade deixa de ser computavel para sempre —
                # a proteccao destruia o dado de que precisava.
                price_dates[t] = d_fb
                log.warning("  %s: cotacao falhou — a usar o ultimo preco conhecido "
                            "$%s (de %s).", t, fallback, d_fb)
            else:
                prices[t] = None
                price_dates[t] = d_fb
                log.error("  %s: sem preco fresco nem recurso suficientemente recente "
                          "(o ultimo conhecido e de %s, limite %s).", t, d_fb, limite_recurso)

    # Um instrumento DETIDO sem preco desaparece silenciosamente da valorizacao:
    # calculate_value ignora-o, e o P&L publicado inclui a queda. Reproduzido com
    # o LQD (15% da carteira): valor 10.037 -> 8.498, P&L +0,38% -> -15,01%, sem
    # um unico aviso. So o SPY tinha guarda.
    #
    # Nota sobre o fallback: `MAX_STALE_DAYS = 4` com cadencia SEMANAL significa
    # que o ultimo fecho conhecido tem sempre 7 dias, logo o recurso e sempre
    # descartado. E conservador de proposito — mas entao a alternativa nao pode
    # ser valorizar como se a posicao nao existisse.
    #
    # E se, mesmo com o recurso, faltar o preco de uma posicao DETIDA: usa-se o
    # ultimo conhecido a qualquer idade e DECLARA-SE. A versao anterior desta
    # guarda abortava com exit 1, o que parava a semana inteira — sem carteira e
    # sem newsletter — e num instrumento retirado de bolsa parava-a todas as
    # semanas para sempre, sem saida a nao ser editar o portfolio.json a mao.
    #
    # Um preco congelado e uma aproximacao que se pode declarar. Omitir a
    # posicao da conta e um erro silencioso, e nao publicar nada e pior do que
    # os dois. A ordem de preferencia e: fresco > recurso recente > congelado e
    # declarado > nao publicar.
    # Duas avarias diferentes, que nao podem partilhar a mesma etiqueta:
    #
    #   valuation_frozen  — ha preco, mas e antigo. O valor e aproximado.
    #   valuation_missing — nao ha preco nenhum. O valor esta INCOMPLETO, e um
    #                       P&L calculado sobre ele e falso, nao aproximado.
    #
    # A versao anterior punha as duas na mesma lista e a newsletter dizia "usou o
    # ultimo preco conhecido" tambem no caso em que nao usou preco nenhum —
    # publicando -16,43% de P&L onde o real era -1,04%.
    #
    # E a idade do preco mede-se pela DATA DO PRECO. Media-se contra
    # current["date"], que avanca todas as semanas mesmo quando o preco nao
    # avanca, e `last_prices` era reescrito com o proprio preco congelado: um
    # feed morto ficava indefinidamente a 7 dias de idade e nunca era declarado.
    valuation_frozen, valuation_missing = [], []
    # A IDADE de cada preco congelado, e quais deles ja passaram o limite. O
    # `PRICE_FROZEN_AFTER_DAYS` declara "nao para o sistema — DECLARA", e o
    # unico efeito que tinha era um `log.error` no runner: nao entrava no
    # portfolio.json, nao chegava ao gerador, nao chegava ao site. Ninguem le o
    # log do Actions antes de a edicao sair, portanto declarar para um log e nao
    # declarar. Um ETF renomeado ou retirado de bolsa produzia, semana apos
    # semana, o mesmo aviso morno de "aproximacao" — o mesmo texto para um preco
    # congelado ha 7 dias e para um congelado ha 300.
    valuation_frozen_days, valuation_not_credible = {}, []
    # E a DATA do preco, nao so a idade. A idade cresce sete dias por semana
    # enquanto a avaria durar, e o aviso da edicao tem de aparecer PALAVRA POR
    # PALAVRA sob pena de FALHA_FORMA: uma idade la dentro era uma frase nova
    # todas as sextas para o modelo copiar sem falhar, durante todas as semanas
    # em que a avaria durasse — tres tentativas falhadas e nao ha edicao nenhuma,
    # exactamente durante a avaria que o aviso existe para contar. E a mesma
    # correccao que o aviso da serie parada e o da ancora do E/P ja levaram.
    valuation_frozen_dates = {}
    for t, n in current_shares.items():
        if not n or prices.get(t) is not None:
            continue
        congelado = (current.get("last_prices") or {}).get(t)
        # A MESMA regra dos outros tres sitios: aqui aceitava-se um preco de
        # 0.0 — nao e NaN nem infinito, mas tambem nao e um preco — e o
        # `calculate_value` levantava `ValueError` a meio do job, matando a
        # semana com a causa a quatro chamadas de distancia.
        if _preco_utilizavel(congelado):
            prices[t] = congelado
            stale[t] = True
            d_preco = price_dates.get(t) or prev_price_dates.get(t) or last_known_date
            price_dates[t] = d_preco
            try:
                idade = (target_date - date.fromisoformat(str(d_preco))).days
            except (ValueError, TypeError):
                idade = None
            valuation_frozen.append(t)
            if idade is not None:
                valuation_frozen_days[t] = idade
            if d_preco:
                valuation_frozen_dates[t] = str(d_preco)
            log.error("%s: sem cotacao — valorizado ao ultimo preco conhecido "
                      "(%s, de %s, idade %s dias).", t, congelado, d_preco, idade)
            if idade is not None and idade > PRICE_FROZEN_AFTER_DAYS:
                valuation_not_credible.append(t)
                log.error("%s: o preco esta congelado ha %s dias (limite %s). A "
                          "valorizacao ja nao e credivel — corrigir a fonte.",
                          t, idade, PRICE_FROZEN_AFTER_DAYS)
        else:
            valuation_missing.append(t)
            log.error("%s: detido e sem preco nenhum, nem recurso. A posicao NAO "
                      "entra na valorizacao: o valor da carteira fica incompleto "
                      "e o P&L desta semana nao e publicavel.", t)

    # O SPY passa pelo MESMO laco que os outros. Estava tratado ANTES dele, e
    # por isso um SPY detido com o preco de recurso ja tinha `prices["SPY"]`
    # preenchido quando o laco corria — saltava-o, nunca entrava em
    # `valuation_frozen`, e o aviso de idade nunca disparava para o instrumento
    # que e 10% da carteira. Era o buraco que este laco fechou para os outros,
    # reaberto so para ele.
    benchmark_frozen = bool(stale.get("SPY"))
    if prices.get("SPY") is None:
        fb_spy = (current.get("last_prices") or {}).get("SPY")
        # A MESMA guarda que o laco irmao: `if fb_spy:` aceitava um `float("nan")`,
        # que e truthy. Um NaN em `last_prices["SPY"]` — vindo de um ficheiro
        # editado a mao, que e o que o RUNBOOK manda fazer numa recuperacao —
        # propagava-se para o `bench_value` e para o alpha; o `_has_invalid_float`
        # apanha-o mais a frente e mata o job, portanto nao se publica falsidade,
        # mas perde-se a semana por uma assimetria que o irmao ja nao tem.
        if _preco_utilizavel(fb_spy):
            prices["SPY"] = fb_spy
            stale["SPY"] = True
            price_dates["SPY"] = prev_price_dates.get("SPY") or last_known_date
            benchmark_frozen = True
            if "SPY" not in valuation_frozen:
                valuation_frozen.append("SPY")
            # E a idade do benchmark conta como a dos outros: o principio
            # aplicado a um ramo e nao ao irmao e o defeito que este laco fechou.
            try:
                _id_spy = (target_date - date.fromisoformat(
                    str(price_dates["SPY"]))).days
            except (ValueError, TypeError):
                _id_spy = None
            if price_dates.get("SPY"):
                valuation_frozen_dates["SPY"] = str(price_dates["SPY"])
            if _id_spy is not None:
                valuation_frozen_days["SPY"] = _id_spy
                if (_id_spy > PRICE_FROZEN_AFTER_DAYS
                        and "SPY" not in valuation_not_credible):
                    valuation_not_credible.append("SPY")
            log.error("SPY sem cotacao — benchmark valorizado ao ultimo preco "
                      "conhecido ($%s, de %s). O alpha desta semana e aproximado.",
                      fb_spy, price_dates["SPY"])
        else:
            benchmark_frozen = True
            log.error("SPY sem cotacao nem recurso: o benchmark nao pode ser "
                      "valorizado. A semana e publicada com o alpha suprimido.")
    elif benchmark_frozen:
        log.error("SPY a preco de recurso (de %s): o alpha desta semana e "
                  "aproximado.", price_dates.get("SPY"))

    if valuation_frozen or valuation_missing:
        log.error("Valorizacao degradada — congelados: %s | sem preco: %s.",
                  ", ".join(sorted(valuation_frozen)) or "nenhum",
                  ", ".join(sorted(valuation_missing)) or "nenhum")

    if any(stale.values()):
        stale_tickers = [t for t, v in stale.items() if v]
        log.warning(f"⚠️ Proceeding with STALE fallback prices for: {stale_tickers}. "
                     f"This week's portfolio figures may not reflect this week's actual market close.")

    portfolio_value = calculate_value(current_shares, prices)
    pnl_pct      = round((portfolio_value - inception_val) / inception_val * 100, 2)
    bench_shares = current.get("benchmark_spy_shares")
    if bench_shares is None:
        # O benchmark e o capital de arranque comprado em SPY NA DATA DE
        # ARRANQUE. A versao anterior usava uma constante de 100.000 (que e o
        # capital do BACKTEST, nao deste portfolio, cujo inception_value e
        # 10.000) e o preco de SPY de HOJE — o que daria +900% de benchmark e
        # −894% de alpha publicados aos subscritores.
        #
        # Os dois numeros existem no ficheiro. Se faltarem, isto para: um
        # benchmark inventado e pior do que nenhum.
        # E tem de ser mesmo a entrada de ARRANQUE. O historico cresce 52
        # entradas por ano e um dia sera compactado; a partir dai `history[0]` e
        # uma semana qualquer, e reconstruir dela dava um benchmark — e um alpha
        # — errados, publicados aos subscritores sem um sinal. Um benchmark
        # inventado e pior do que nenhum, e isso vale tambem para um
        # reconstruido a partir da entrada errada.
        capital = (portfolio.get("meta") or {}).get("inception_value")
        primeira = (portfolio.get("history") or [{}])[0]
        spy_inicial = (primeira.get("prices") or {}).get("SPY")
        # `!= 1`, nao `not in (None, 1)`: `None` nunca pode significar "esta e a
        # edicao 1". Ele significa exactamente o contrario — que o numero nao se
        # conseguiu ler — e era por essa porta que a entrada ilegivel entrava
        # como edicao de arranque. O historico VAZIO continua apanhado pela
        # guarda seguinte, que exige o preco do SPY de arranque.
        if primeira.get("issue") != 1:
            log.error("benchmark_spy_shares ausente e o historico ja nao comeca na "
                      "edicao 1 (comeca na %s): reconstrui-lo daria o capital de "
                      "arranque comprado ao preco de uma semana qualquer. A abortar "
                      "em vez de publicar um benchmark errado.", primeira.get("issue"))
            sys.exit(1)
        if not capital or not spy_inicial:
            log.error("benchmark_spy_shares ausente e nao ha como reconstrui-lo "
                      "(inception_value=%r, SPY de arranque=%r). A abortar em vez "
                      "de publicar um benchmark inventado.", capital, spy_inicial)
            sys.exit(1)
        bench_shares = round(capital / spy_inicial, 4)
        log.warning("benchmark_spy_shares ausente — reconstruido de %s USD ao SPY "
                    "de arranque (%.2f) = %.4f accoes.", capital, spy_inicial, bench_shares)
    if not prices.get("SPY"):
        bench_value, bench_pnl, alpha = None, None, None
        log.error("Benchmark e alpha em n/d esta semana.")
    else:
        bench_value  = round(bench_shares * prices["SPY"], 2)
        bench_pnl    = round((bench_value - inception_val) / inception_val * 100, 2)
        alpha        = round(pnl_pct - bench_pnl, 2) if pnl_pct is not None else None

    # Com uma posicao sem preco nenhum, o valor da carteira esta INCOMPLETO: o
    # P&L calculado sobre ele nao e uma aproximacao, e um numero falso. Publicar
    # -16,4% quando o real e -1,0% e pior do que nao publicar P&L nenhum.
    if valuation_missing:
        log.error("P&L e alpha suprimidos: %s sem preco, o valor da carteira nao "
                  "esta completo. O valor bruto e publicado com a ressalva.",
                  ", ".join(sorted(valuation_missing)))
        pnl_pct = None
        alpha   = None

    # Sem `%+.2f` sobre um valor que pode ser None. Numa semana em que o SPY nao
    # tem cotacao nem recurso, `bench_pnl` vem None e o logging nao levanta:
    # imprime "--- Logging error ---" e DESCARTA a mensagem. O operador perdia a
    # linha que resume a semana precisamente na semana em que precisa de a ler.
    _n_d = lambda v, s_="%": "n/d" if v is None else f"{v:+.2f}{s_}"
    _val = lambda v: "n/d" if v is None else f"${v:.2f}"
    log.info("Portfolio: %s (%s) | SPY: %s (%s) | Alpha: %s",
             _val(portfolio_value), _n_d(pnl_pct),
             _val(bench_value), _n_d(bench_pnl), _n_d(alpha))

    # ── a edicao publicada CONFRONTA-SE com o motor; nao o instrui ──────────
    #
    # Aqui viviam ~190 linhas cujo proposito unico era escolher, de entre as
    # tabelas que o modelo tinha escrito, uma alocacao macro em que se pudesse
    # confiar: deteccao do eco do vector de crise, reconstrucao da macro pelo
    # historico, recusa de tabelas obsoletas, guardas contra envenenamento pela
    # porta lateral. Cada uma dessas linhas era uma auditoria a fechar um buraco
    # DENTRO de uma decisao que nunca devia ter sido do modelo.
    #
    # Com o `REGIME_WEIGHTS`, o regime escolhe o vector e mais nada o escolhe. A
    # tabela da edicao deixa de ser uma instrucao e passa a ser uma AFIRMACAO
    # sobre o que a carteira fez — que pode estar certa ou errada, e e aqui que
    # se verifica. Uma edicao que diga uma coisa diferente do que o motor
    # executou continua a ser um defeito grave (os subscritores leem-na), mas e
    # um defeito de publicacao, nao de carteira: nenhum dolar se move por causa
    # dele.
    issue_esperado   = issue_number - 1
    newsletter_alloc = dict(bucket_alloc or {})   # registo, nao instrucao
    _executada_entao = current.get("bucket_allocation_pct") or {}
    if newsletter_alloc and issue_lido != issue_esperado:
        log.info("A tabela legivel vem da edicao %s e a semana a decidir esperava "
                 "a %s. Fica registada como o que essa edicao publicou; nao "
                 "instrui nada.", issue_lido, issue_esperado)
    elif newsletter_alloc and _executada_entao:
        _desvios = [
            f"{b}: edicao {newsletter_alloc.get(b, float('nan')):.1f}% vs motor "
            f"{_executada_entao.get(b, 0.0):.1f}%"
            for b in BUCKETS
            if abs(newsletter_alloc.get(b, -999.0) - _executada_entao.get(b, 0.0)) > 1.0
        ]
        if _desvios:
            log.error("A edicao %s publicou uma alocacao diferente da que a "
                      "carteira executava nessa semana: %s. A carteira esta "
                      "certa por construcao — quem esta errado e o texto que "
                      "foi enviado aos subscritores. Verificar o gerador da "
                      "newsletter.", issue_lido, "; ".join(_desvios))
        else:
            log.info("A edicao %s publica a mesma alocacao que a carteira "
                     "executa.", issue_lido)

    semestral        = is_semestral_rebalance_week(target_date)
    # (check_emergency ja correu acima, antes de confirmar o regime)
    trigger = decide_rebalance(regime, was_regime, critical_subregime, was_subregime,
                               semestral, emerg_why if emerg else None)

    rebalance_triggered = False
    rebalance_reason    = "hold"
    # Sem gatilho, a carteira fica com o que ja tinha — e "o que ja tinha" e o
    # que o `current` diz, os tres campos da mesma fonte e da mesma passagem.
    #
    # `final_regime` vinha de `was_regime`, que numa re-corrida e rebobinado para
    # a edicao N-1, enquanto `final_bucket_alloc` vinha de `current` (pos-primeira
    # passagem). O snapshot misturava duas eras: regime Turbulence, mapa de
    # Turbulence, pesos de Critical e accoes de Critical — e as semanas seguintes
    # diziam "hold" sobre posicoes que o ficheiro afirmava nao ter.
    held_regime    = current.get("regime", "Turbulence")
    held_subregime = current.get("critical_subregime")
    final_bucket_alloc  = current.get("bucket_allocation_pct", {}) or {}
    final_regime        = held_regime
    final_critical_subregime = held_subregime

    # O semestral cancelava-se quando a edicao da semana nao era legivel: existia
    # para aplicar as percentagens publicadas NESSA edicao, e aplicar as de outra
    # era negociar sobre uma instrucao que ninguem deu. Deixou de haver instrucao
    # nenhuma para ler — o semestral aplica o vector do regime, e uma newsletter
    # que falhe nao adia nem altera o que a carteira faz.
    if trigger:
        candidate_alloc, alloc_source = effective_bucket_alloc(regime, critical_subregime)
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

    # Uma carteira que nao se consegue valorizar nao se pode rebalancear: o
    # `portfolio_value` sobre o qual as novas posicoes sao dimensionadas ja vem
    # incompleto, e se o instrumento sem preco nao pertencer ao mapa novo o
    # dinheiro dele desaparece — e na semana seguinte a perda aparece como
    # performance limpa. Suprimir so o P&L adiava a mentira sete dias.
    #
    # E um preco DECLARADO nao-credivel conta pela mesma razao, palavra por
    # palavra. A guarda existia so para `valuation_missing`; o irmao —
    # `valuation_not_credible`, um preco congelado ha mais dias do que o
    # `PRICE_FROZEN_AFTER_DAYS` declara — ficava de fora, e o sistema passou a
    # publicar na edicao "the valuation of that position, and therefore this
    # week's profit and loss, is not credible" e a LIQUIDAR essa posicao ao
    # preco morto na mesma corrida, pagando custos reais e dimensionando seis
    # posicoes novas sobre um valor que ele proprio acabara de recusar. Um
    # veredicto publicado que nao muda o comportamento e pior do que nao o
    # publicar.
    _nao_valorizavel = sorted(set(valuation_missing) | set(valuation_not_credible))
    if _nao_valorizavel and trigger:
        log.error("Rebalanceamento cancelado: %s sem preco utilizavel (ausente ou "
                  "congelado alem do limite), o valor da carteira nao esta "
                  "credivel e dimensionar posicoes sobre ele perderia esse "
                  "capital. Posicoes mantidas.", ", ".join(_nao_valorizavel))
        rebalance_triggered = False
        rebalance_reason = ("valuation_incomplete_held" if valuation_missing
                            else "valuation_not_credible_held")
        final_regime = held_regime
        final_critical_subregime = held_subregime
        final_bucket_alloc = current.get("bucket_allocation_pct", {}) or {}

    custo_transaccao, custo_detalhe = 0.0, {}
    if rebalance_triggered and final_bucket_alloc:
        # rebalance_shares recusa executar uma alocacao incompleta. Essa recusa
        # tem de MANTER AS POSICOES — que e o que o comentario dela promete —
        # e nao derrubar a corrida: sem snapshot, a data nao avanca, o job
        # falha, a newsletter e saltada, e tudo isto na semana em que a
        # carteira mais precisava de se mexer.
        # Os custos de transaccao saem do valor ANTES de se comprar.
        #
        # A carteira real nao cobrava nada, enquanto o backtest publicado no
        # mesmo site cobra $10 a abrir e $10 a fechar por posicao.
        #
        # A ordem de grandeza, contada sobre o historico real e nao de cabeca:
        # sob o modelo `open_close`, das 26 semanas so quatro abriram ou
        # fecharam linhas — $40 sobre $10.000, 0,4%. Pequeno, mas nao zero, e o
        # que o torna importante nao e o passado: e uma entrada em Critical, que
        # troca quatro dos seis instrumentos ($80 = 0,8%) e que a histerese
        # assimetrica do Resilient pode repetir. E, sobretudo, e a diferenca
        # entre publicar dois numeros comparaveis e dois que nao o sao. O custo e estimado sobre a carteira ALVO (que se obtem
        # dimensionando sem custo) e depois deduzido, para nao haver
        # circularidade: a diferenca entre as duas contas e de centimos, e o
        # sentido do erro e conservador (paga-se sobre a carteira maior).
        try:
            alvo_sem_custo = rebalance_shares(
                portfolio_value, final_bucket_alloc,
                resolve_etf_map_key(final_regime, final_critical_subregime), prices)
            custo_transaccao, custo_detalhe = rules.trade_cost(
                current_shares, alvo_sem_custo)
            if custo_transaccao:
                log.info("Custos de transaccao: $%.2f (%d abertas, %d fechadas) — "
                         "deduzidos do valor antes de dimensionar.",
                         custo_transaccao, len(custo_detalhe["opened"]),
                         len(custo_detalhe["closed"]))
            candidate = rebalance_shares(
                portfolio_value - custo_transaccao, final_bucket_alloc,
                resolve_etf_map_key(final_regime, final_critical_subregime), prices)
        except ValueError as e:
            log.error("Rebalanceamento impossivel (%s). A manter as posicoes e a "
                      "publicar a semana com o motivo declarado.", e)
            new_shares = current_shares.copy()
            rebalance_triggered = False
            rebalance_reason = "missing_prices_held"
            final_regime = held_regime
            final_critical_subregime = held_subregime
            final_bucket_alloc = current.get("bucket_allocation_pct", {}) or {}
            candidate = None
            custo_transaccao, custo_detalhe = 0.0, {}
        if candidate is None:
            candidate_value = None
        else:
            candidate_value = calculate_value(candidate, prices)
        if candidate_value is None:
            pass                      # ja tratado acima: posicoes mantidas
        elif candidate_value < portfolio_value * 0.5:
            # Rede de ultimo recurso. Hoje e inalcancavel: rebalance_shares ja
            # levanta se nao colocar 100% do pedido, e validate_allocation exige
            # 100 ± 5, logo o candidato vale sempre >= 95% do valor. Fica na
            # mesma — e repoe o estado detido, como o ramo acima, para nunca
            # gravar um snapshot que misture eras.
            log.error("Valor rebalanceado ${%s} < 50%% do actual — a abortar e "
                      "manter as posicoes.", candidate_value)
            new_shares = current_shares.copy()
            rebalance_triggered = False
            rebalance_reason = "aborted_invalid_shares"
            final_regime = held_regime
            final_critical_subregime = held_subregime
            final_bucket_alloc = current.get("bucket_allocation_pct", {}) or {}
        else:
            new_shares = candidate
            log.info(f"New shares: {new_shares}")
    else:
        new_shares = current_shares.copy()

    # O P&L e o alpha da semana sao LIQUIDOS do custo. Foram calculados la em
    # cima sobre o valor bruto, antes de se saber se havia transaccoes; agora
    # sabe-se. Publicar o P&L bruto ao lado de uma carteira que ja pagou o custo
    # era publicar um ganho que a carteira nao tem.
    if custo_transaccao and pnl_pct is not None:
        pnl_pct = round((portfolio_value - custo_transaccao - inception_val)
                        / inception_val * 100, 2)
        if bench_pnl is not None:
            alpha = round(pnl_pct - bench_pnl, 2)
        log.info("P&L liquido do custo de $%.2f: %+.2f%%", custo_transaccao, pnl_pct)

    # A alocacao que se PUBLICA e a que foi executada, nao a tabela em bruto.
    #
    # `rebalance_shares` renormaliza os pesos a 100 antes de dimensionar — a
    # tolerancia de 100 +/- 5 serve para aceitar a tabela, nao para deixar
    # capital por colocar. Mas o snapshot continuava a gravar a tabela crua: o
    # dinheiro ficava certo e o registo dele nao, e o site desenhava barras e um
    # donut a somar 96% ao lado de uma carteira que estava a 100.
    if rebalance_triggered and final_bucket_alloc:
        _total_alloc = sum(final_bucket_alloc.values())
        if _total_alloc > 0 and abs(_total_alloc - 100.0) > 0.001:
            final_bucket_alloc = {b: round(pct * 100.0 / _total_alloc, 4)
                                  for b, pct in final_bucket_alloc.items()}

    etf_map   = REGIME_ETF_MAP.get(resolve_etf_map_key(final_regime, final_critical_subregime), REGIME_ETF_MAP["Turbulence"])
    alloc_pct = {t: 0.0 for t in ALL_TICKERS}
    for bucket, pct in final_bucket_alloc.items():
        ticker = etf_map.get(bucket, "BIL")
        alloc_pct[ticker] = alloc_pct.get(ticker, 0.0) + pct

    snapshot = {
        "issue":                         issue_number,
        "date":                          str(target_date),
        "mrm_score":                     mrm_score,
        # E se esse score foi calculado sobre um composto INCOMPLETO.
        #
        # O motor ja declara, para a semana corrente, que um score sobre quatro
        # pilares em vez de cinco nao e o score — e um numero diferente — e
        # recusa-lhe valor de regime. Mas gravava-o cru, sem uma palavra sobre a
        # incompletude: na semana seguinte, com os cinco pilares de volta, essa
        # leitura entrava na janela de confirmacao como se fosse boa. E a janela
        # e a UNICA defesa de uma rotacao de 100% da carteira. O numero
        # renormalizado nem sequer e uma leitura de mercado: com um pilar cravado
        # no 10,0 fora do composto, ele desce sozinho — que e exactamente a
        # aritmetica que a guarda desta semana existe para nao confundir com um
        # sinal.
        "score_nd_pillars":              sorted(nd_pillars_data or []),
        "score_complete":                not (nd_pillars_data or []),
        # `regime` e o que a carteira PASSOU A DETER. Antes guardava o regime
        # sinalizado, que sem gatilho difere do detido — e a tabela de historico
        # do site mostrava "Resilient" numa semana em que a carteira nunca saiu
        # do mapa de Turbulence. O sinal continua registado, com nome proprio.
        "regime":                        final_regime,
        "regime_signalled":              signalled_regime,
        "critical_subregime":            final_critical_subregime,
        "critical_subregime_signalled":  critical_subregime,
        "critical_subregime_note":       critical_subregime_note,
        "prices":                        {t: prices[t] for t in tickers_needed if prices.get(t) is not None},
        "prices_confirmed":              {t: not stale.get(t, False) for t in tickers_needed if prices.get(t) is not None},
        "data_stale":                    any(stale.get(t, False) for t in tickers_needed),
        # O valor ANTES do rebalanceamento e o valor DEPOIS sao numeros
        # diferentes na semana em que ha transaccoes: as posicoes novas foram
        # dimensionadas sobre `valor - custo`, e publicar o valor pre-custo como
        # se fosse o da carteira sobrestima-a exactamente no custo — na semana
        # em que o evento acontece, que e a semana que se le.
        "portfolio_value_pre_rebalance": round(portfolio_value, 2),
        "bucket_allocation_pct":         final_bucket_alloc,
        "newsletter_bucket_allocation_pct": dict(newsletter_alloc or {}),
        # O eco tem de ser DECLARADO ao leitor, nao so ao log. A
        # edicao afirma, por ordem da regra 8, que a tabela publicada e
        # a alocacao que retoma; se o motor a descartou, a semana
        # seguinte executa percentagens que nenhuma edicao publicou. O
        # caminho gemeo — a tabela que o parser recusa — ja publica uma
        # De que edicao veio a tabela registada: "a semana passada" pode ser
        # de ha tres semanas, porque `issue_lido` e a edicao de maior numero
        # no disco e nao necessariamente a N-1.
        "newsletter_alloc_issue": issue_lido,
        "stress_gauge_active":           stress_active,
        "stress_gauge_basis":            gauge_basis,
        # Uma semana decidida sobre dados recusados por idade tem de ser
        # distinguivel de uma semana normal ao olhar para o ficheiro.
        "data_refused":                  DATA_REFUSED["value"],
        "valuation_frozen":              sorted(valuation_frozen),
        # A idade e o veredicto, publicados: sem eles o limiar declarado nao
        # tinha consumidor nenhum e a sua fronteira nao era exercitavel.
        "valuation_frozen_days":         dict(sorted(valuation_frozen_days.items())),
        "valuation_frozen_dates":         dict(sorted(valuation_frozen_dates.items())),
        "valuation_not_credible":        sorted(valuation_not_credible),
        "price_frozen_after_days":       PRICE_FROZEN_AFTER_DAYS,
        "valuation_missing":             sorted(valuation_missing),
        "valuation_complete":            not valuation_missing,
        "benchmark_frozen":              benchmark_frozen,
        "score_basis":                   score_basis,
        "allocation_pct":                {t: v for t, v in alloc_pct.items() if v > 0},
        "active_etf_map":                etf_map,
        "shares":                        new_shares,
        # Declarado mesmo quando e zero: uma semana sem transaccoes nao paga
        # nada, e isso e um facto da semana, nao uma ausencia de informacao.
        "transaction_cost_usd":          round(custo_transaccao, 2),
        "transaction_cost_detail":       custo_detalhe or {},
        "portfolio_value":               round(portfolio_value - custo_transaccao, 2),
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
        # A caixa "Live Status" do site le esta nota do `current`. Tem de
        # descrever o sub-regime que a carteira DETEM, nao o que foi sinalizado:
        # numa semana em que o rebalanceamento nao se executou, a nota dizia
        # "Flight-to-Quality, TLT retained" ao lado de um cabecalho "Stress
        # without Relief" e de uma carteira com SHY.
        "critical_subregime_note": (critical_subregime_note
                                    if final_critical_subregime == critical_subregime
                                    else None),
        # Sem zeros: um ticker com quantidade 0.0 persistia indefinidamente e
        # aparecia nas listas de instrumentos como se fosse detido.
        "shares":                 {t: n for t, n in new_shares.items() if n},
        "transaction_cost_usd":   round(custo_transaccao, 2),
        "bucket_allocation_pct":  final_bucket_alloc,
        "newsletter_bucket_allocation_pct": dict(newsletter_alloc or {}),
        # O eco tem de ser DECLARADO ao leitor, nao so ao log. A
        # edicao afirma, por ordem da regra 8, que a tabela publicada e
        # a alocacao que retoma; se o motor a descartou, a semana
        # seguinte executa percentagens que nenhuma edicao publicou. O
        # caminho gemeo — a tabela que o parser recusa — ja publica uma
        # De que edicao veio a tabela registada: "a semana passada" pode ser
        # de ha tres semanas, porque `issue_lido` e a edicao de maior numero
        # no disco e nao necessariamente a N-1.
        "newsletter_alloc_issue": issue_lido,
        "allocation_pct":         {t: v for t, v in alloc_pct.items() if v > 0},
        # A QUALIDADE da valorizacao acompanha o valor. Estes campos so iam para
        # `history[-1]`, e o site le `data.current`: `renderPortfolioKPIs`
        # desenhava `portfolio_value`, `portfolio_pnl_pct` e o alpha como
        # numeros saos, e `renderPortfolioHoldings` desenhava o preco congelado
        # como o preco da linha — enquanto a newsletter da mesma semana dizia,
        # por escrito, que aquela valorizacao ja nao e credivel. Um numero
        # publicado sem a sua qualidade e uma afirmacao que o estado nao
        # sustenta, e o consumidor nao pode ter de a ir procurar ao historico.
        "valuation_frozen":       sorted(valuation_frozen),
        "valuation_frozen_days":  dict(sorted(valuation_frozen_days.items())),
        "valuation_frozen_dates":  dict(sorted(valuation_frozen_dates.items())),
        "valuation_not_credible": sorted(valuation_not_credible),
        "price_frozen_after_days": PRICE_FROZEN_AFTER_DAYS,
        "valuation_missing":      sorted(valuation_missing),
        "valuation_complete":     not valuation_missing,
        "active_etf_map":         etf_map,
        # O recurso tem de SOBREVIVER a semana em que faltou. Escrevia-se so
        # para os tickers com preco, portanto o ticker sem preco era apagado do
        # recurso e na semana seguinte voltava a estar em falta — um bloqueio
        # permanente do rebalanceamento, com o medidor ligado e a carteira no
        # mapa errado, sem saida a nao ser editar o ficheiro a mao.
        # E o recurso so guarda precos UTILIZAVEIS. Um NaN ou um zero — vindo de
        # um ficheiro editado a mao numa recuperacao, que e o que o RUNBOOK manda
        # fazer — era carregado de semana para semana indefinidamente, e cada
        # leitor a jusante tinha de o rejeitar por sua conta. Dois ja o faziam e
        # um nao (o ramo do SPY, com um `if fb_spy:` que aceita NaN por ser
        # truthy): o principio aplicado a dois ramos e nao ao terceiro. Rejeitado
        # UMA vez, aqui, na porta por onde entra no ficheiro.
        "last_prices":            {t: v for t, v in {
                                       **{t: v for t, v in (current.get("last_prices") or {}).items()
                                          if t in tickers_needed},
                                       **{t: prices[t] for t in tickers_needed
                                          if prices.get(t) is not None}}.items()
                                   if _preco_utilizavel(v)},
        # A DATA de cada preco, nao so o preco. Sem isto nao ha como saber ha
        # quanto tempo uma cotacao esta parada: a data da corrida avanca todas as
        # semanas mesmo quando o preco nao avanca.
        "last_price_dates":       {**{t: v for t, v in (current.get("last_price_dates") or {}).items()
                                      if t in tickers_needed},
                                   **{t: price_dates.get(t) for t in tickers_needed
                                      if prices.get(t) is not None}},
        "portfolio_value":        round(portfolio_value - custo_transaccao, 2),
        "portfolio_pnl_pct":      pnl_pct,
        "benchmark_spy_shares":   bench_shares,
        "benchmark_spy_value":    bench_value,
        "benchmark_spy_pnl_pct":  bench_pnl,
        "alpha_vs_benchmark_pct": alpha,
    }

    if _has_invalid_float(snapshot) or _has_invalid_float(new_current):
        log.error("NaN/Inf detected — ABORTING WRITE.")
        sys.exit(1)

    # Substitui a entrada existente deste issue em vez de duplicar — importa nas
    # re-corridas correctivas (FORCE_REBALANCE=true depois de uma falha).
    #
    # Mas uma re-corrida NAO pode apagar o que aconteceu. Ao correr outra vez,
    # `was_regime` ja e o regime novo, portanto decide_rebalance devolve None e
    # o motivo desta passagem fica "hold". Se isso substituisse a entrada
    # original, o stress_on desaparecia do historico e a newsletter — que le
    # history[-1] — dizia aos subscritores que nao houve transaccoes na semana
    # em que a carteira rodou. O input de recuperacao de erros apagava o registo
    # do evento.
    anterior = next((h for h in portfolio["history"] if h.get("issue") == issue_number), None)
    if anterior is not None:
        snapshot["rerun_count"] = anterior.get("rerun_count", 0) + 1
        # O custo da semana e o que ja foi pago MAIS o que esta passagem paga.
        #
        # Na segunda passagem as posicoes ja estao no sitio, portanto
        # `trade_cost` devolve 0. Se o snapshot novo levasse esse 0, a semana
        # perdia os dolares que de facto pagou e a soma publicada de custos
        # passava a subestimar — e nao so no caminho em que a re-corrida decide
        # "hold": quando a edicao N-1 esta no historico, a rebobinagem faz a
        # re-corrida decidir OUTRA VEZ `stress_on`, `rebalance_triggered` volta
        # a ser True, e a guarda que existia so cobria o outro caminho. O valor
        # da carteira ja vem liquido do custo pago, por isso acumular aqui nao
        # o desconta duas vezes.
        _custo_ant = anterior.get("transaction_cost_usd") or 0.0
        if _custo_ant:
            snapshot["transaction_cost_usd"] = round(_custo_ant + custo_transaccao, 2)
            _det_ant = anterior.get("transaction_cost_detail") or {}
            if custo_transaccao:
                # Funde as duas passagens sem perder o resto do detalhe: fundir
                # so `opened`/`closed` deitava fora as outras chaves que o
                # caminho normal escreve, e o registo que alguem le para
                # perceber uma semana ficava mais pobre do que o de uma corrida
                # unica. Um instrumento pode aparecer nas duas listas — foi
                # aberto numa passagem e fechado noutra — e isso e o que
                # aconteceu.
                _det_novo = dict(custo_detalhe or {})
                snapshot["transaction_cost_detail"] = {
                    **_det_ant, **_det_novo,
                    "opened": sorted(set(_det_ant.get("opened") or []) |
                                     set(_det_novo.get("opened") or [])),
                    "closed": sorted(set(_det_ant.get("closed") or []) |
                                     set(_det_novo.get("closed") or [])),
                }
            else:
                snapshot["transaction_cost_detail"] = _det_ant
            # E o valor PRE-rebalanceamento e o da passagem que executou. Nesta
            # passagem a carteira ja vem liquida do custo pago, portanto gravar o
            # valor de agora ao lado de um custo cumulativo deixava o registo a
            # dizer duas coisas incompativeis (valor = pre - custo deixava de
            # bater). Nada disto e publicado, mas e o registo que se le quando
            # alguem vai perceber uma semana.
            if anterior.get("portfolio_value_pre_rebalance") is not None:
                snapshot["portfolio_value_pre_rebalance"] = anterior[
                    "portfolio_value_pre_rebalance"]
        if anterior.get("rebalance_triggered") and not rebalance_triggered:
            log.warning("Re-corrida do issue %s: o rebalanceamento '%s' ja tinha sido "
                        "executado. O registo original e preservado.",
                        issue_number, anterior.get("rebalance_reason"))
            snapshot["rebalance_triggered"] = True
            snapshot["rebalance_reason"]    = anterior.get("rebalance_reason")
            snapshot["rebalance_executed_on_run"] = anterior.get("rebalance_executed_on_run", 1)
            snapshot["rerun_no_further_action"] = True
    # Em que passagem a transaccao foi de facto executada. Ficava inconsistente
    # (corrida 1 = 1, corrida 2 = ausente) e nunca era escrito numa re-corrida
    # que rebalanceasse de facto.
    if rebalance_triggered:
        snapshot["rebalance_executed_on_run"] = (
            anterior.get("rebalance_executed_on_run", 1) if anterior is not None else 1)

    portfolio["history"] = [h for h in portfolio["history"] if h.get("issue") != issue_number]
    portfolio["history"].append(snapshot)
    # A entrada com o numero ilegivel fica mesmo no FIM.
    #
    # A chave `-1` punha-a no PRINCIPIO — menor do que qualquer edicao real — e
    # o comentario, o `log.error` que o operador le, e o unico leitor que
    # depende de `history[0]` diziam todos o contrario. Esse leitor e a
    # reconstrucao do benchmark, e a guarda dele aceita `None` como "o historico
    # comeca na edicao 1": o motor reconstruia o capital de arranque ao preco do
    # SPY de uma semana qualquer e publicava um benchmark e um alpha errados,
    # aos subscritores, sem um sinal. Ordena-se por (0, issue) e (1, 0): os
    # inteiros por ordem, e os ilegiveis todos no fim.
    portfolio["history"].sort(key=lambda h: (
        (0, h.get("issue")) if isinstance(h.get("issue"), int)
        and not isinstance(h.get("issue"), bool) else (1, 0)))
    # E as entradas que nao sao registos voltam ao ficheiro — mas NAO ao
    # `history`, que e uma serie que o site desenha.
    #
    # Reposta la no fim, uma linha escrita a mao passava a ser o `history[-1]`
    # que o `renderPortfolioKPIs` e o `renderRebalanceLog` leem: na semana em que
    # a carteira rodou 100% para o mapa de Critical, o cartao de topo publicava
    # "Hold — no rebalance" e o score "—", e a tabela ganhava uma primeira linha
    # `#undefined`. Preservar era certo; preservar DENTRO da lista que os
    # consumidores iteram era criar um estado novo que nenhum deles conhece.
    # Vao para um campo proprio, visiveis ao operador e fora do caminho —
    # exactamente como o registo de envios ja faz com uma linha solta.
    _hist_soltas = _hist_soltas + _h_sem_numero
    if _hist_soltas:
        portfolio["history_unparsed"] = _hist_soltas
    else:
        portfolio.pop("history_unparsed", None)
    portfolio["current"] = new_current

    write_json_atomic(PORTFOLIO_PATH, portfolio)
    log.info("portfolio.json updated successfully.")
    _f = lambda v: "n/d" if v is None else f"{v:+.2f}%"
    # Idem para os valores em dolares: `bench_value` e `portfolio_value` tambem
    # podem vir None numa semana de valorizacao degradada, e `%.2f` sobre None
    # apagava esta linha inteira do log.
    _d = lambda v: "n/d" if v is None else f"${v:.2f}"
    log.info("Summary: %s (%s) | SPY %s (%s) | Alpha %s",
             _d(portfolio_value), _f(pnl_pct), _d(bench_value), _f(bench_pnl), _f(alpha))


if __name__ == "__main__":
    main()
