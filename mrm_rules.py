"""
mrm_rules.py — regras canónicas do US MRM
=========================================
Este ficheiro é a ÚNICA definição das regras do sistema. Antes de existir, as
mesmas regras estavam escritas em quatro sítios: o motor de dados
(`fetch_data.py`), o motor da carteira (`update_portfolio.py`), o gerador da
newsletter (`send_newsletter.py`) e o próprio site (`index.html`). As cópias
divergiram — o gerador da newsletter ainda decidia o rebalanceamento pelo score
e não conhecia a divisão FTQ/Stress, e o site usava um limiar diferente do motor
para pintar o badge de regime.

Regra a partir de agora: quem precisa de uma regra importa-a daqui. O site não
pode importar Python, por isso o `fetch_data.py` publica `as_dict()` dentro do
`data.json`, no campo `rules`, e o `index.html` lê de lá.

OS DOIS MEDIDORES
-----------------
Medidor A — o Global Resilience Score, média ponderada de cinco pilares. Mede
fragilidade ANTECIPADA, com horizonte de 6 a 18 meses. Num backtest de 2005 a
2026 nunca atingiu 8,0, nem sequer em 2008, porque numa crise três dos cinco
pilares melhoram mecanicamente: a curva desinverte, as avaliações colapsam, o
prémio de risco abre. Serve para dizer quanto há para correr mal, não para
dizer se está a correr mal agora.

Medidor B — stress concorrente (`mrm_gauge_b.py`), horizonte 0 a 3 meses, feito
de variações e não de níveis. É ele que decide o regime Critical.

A carteira segue o medidor B. O medidor A continua a ser o número publicado e a
espinha da análise semanal, e decide o regime Resilient.
"""

from datetime import date, timedelta

# ─────────────────────────────────────────────────────────────────────────────
# Buckets e universo
# ─────────────────────────────────────────────────────────────────────────────

BUCKETS = ["US_EQUITIES", "US_TREASURIES", "IG_CREDIT", "COMMODITIES", "CASH", "ALTERNATIVES"]

REGIME_ETF_MAP = {
    "Turbulence": {
        "US_EQUITIES": "SPY", "US_TREASURIES": "IEF", "IG_CREDIT": "LQD",
        "COMMODITIES": "PDBC", "CASH": "BIL", "ALTERNATIVES": "VNQ",
    },
    # Critical divide-se em dois sub-regimes — ver docs/critical_subregime.md.
    "Critical_FTQ": {
        # Queda do 10Y confirmada: a duração paga como cobertura (2008, 2020).
        "US_EQUITIES": "USMV", "US_TREASURIES": "TLT", "IG_CREDIT": "SGOV",
        "COMMODITIES": "GLD", "CASH": "BIL", "ALTERNATIVES": "VNQ",
    },
    "Critical_Stress": {
        # 10Y lateral ou a subir: a duração não paga (2022, TLT −31%). TLT → SHY.
        "US_EQUITIES": "USMV", "US_TREASURIES": "SHY", "IG_CREDIT": "SGOV",
        "COMMODITIES": "GLD", "CASH": "BIL", "ALTERNATIVES": "VNQ",
    },
    "Resilient": {
        "US_EQUITIES": "QQQ", "US_TREASURIES": "SHY", "IG_CREDIT": "HYG",
        "COMMODITIES": "PDBC", "CASH": "BIL", "ALTERNATIVES": "IWO",
    },
}

ALL_TICKERS = sorted({t for m in REGIME_ETF_MAP.values() for t in m.values()})

# ─────────────────────────────────────────────────────────────────────────────
# Pesos
# ─────────────────────────────────────────────────────────────────────────────
# Fora de Critical as percentagens vêm da newsletter semanal. Em Critical passam
# a vir daqui: decisão de Set 2026, depois do backtest 2007-2026 (ver
# backtest/README.md), onde trocar só os instrumentos capturava menos de metade
# da protecção — 2008 fechava a −9,8% com a troca de ETF apenas, contra +1,5% com
# instrumentos e pesos, e a quebra máxima ficava em −20,8% em vez de −16,4%.
# Custo declarado: ~0,30 pp de CAGR em 19 anos.

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

# Envelope de sanidade para as percentagens escritas pela newsletter. Quem
# escreve a tabela de alocação é um LLM, semana a semana, sem limites; o
# `parse_newsletter()` lê-a de volta e o rebalanceamento semestral executa-a.
# Estas bandas não são política de investimento — são o mínimo para que um erro
# do modelo (80% em acções, 0% em treasuries) não chegue à carteira. Foram
# fixadas de modo a aceitar as 25 edições legíveis de Mar a Set 2026, cujo
# intervalo observado foi: acções 10–50, treasuries 18–45, IG 8–28,
# commodities 6–15, cash 0–30, alternativos 0–13.
ALLOCATION_BANDS = {
    "US_EQUITIES":   (5.0, 60.0),
    "US_TREASURIES": (10.0, 50.0),
    "IG_CREDIT":     (0.0, 35.0),
    "COMMODITIES":   (0.0, 25.0),
    "CASH":          (0.0, 40.0),
    "ALTERNATIVES":  (0.0, 20.0),
}
ALLOCATION_TOTAL_TOLERANCE = 5.0   # o total tem de ficar a 100 ± isto

# ─────────────────────────────────────────────────────────────────────────────
# Limiares
# ─────────────────────────────────────────────────────────────────────────────

RESILIENT_MAX = 4.0    # score <= isto e medidor B desligado -> Resilient
CRITICAL_MIN  = 8.0    # apenas para rotular o score; NÃO decide o regime
CONSECUTIVE_WEEKS = 2  # confirmação exigida ao ramo Resilient
SEMESTRAL_MONTHS = {1, 6}

# ─────────────────────────────────────────────────────────────────────────────
# Rótulos — a mesma redacção no site e na newsletter
# ─────────────────────────────────────────────────────────────────────────────

REGIME_LABELS = {
    "Resilient":  "Resilient",
    "Turbulence": "Turbulence",
    "Critical":   "Critical",
    "nd":         "n/d",
}

SUBREGIME_LABELS = {
    "Critical_FTQ":    "Flight to Quality",
    "Critical_Stress": "No Relief",
}

GAUGE_A_CAPTION = (
    "Leading fragility, 6–18 month horizon. Measures how much there is to go "
    "wrong, not whether it is going wrong now."
)

GAUGE_B_CAPTION = (
    "Concurrent stress, 0–3 month horizon. Two published triggers: the Sahm "
    "rule on real-time vintages, and the 4-quarter change in bank delinquency. "
    "This is what decides the Critical regime."
)

REBALANCE_COPY = {
    "stress_on": (
        "Gauge B fired. The portfolio moved to the Critical map: defensive "
        "instruments and the declared Critical weights, which override this "
        "week's newsletter allocation for as long as stress persists."
    ),
    "stress_off": (
        "Gauge B stood down. The portfolio returned to the Turbulence map and "
        "to the macro allocation percentages from the newsletter."
    ),
    "semestral_rebalance": (
        "Scheduled semi-annual rebalance. This week's allocation percentages "
        "are applied to the portfolio."
    ),
    "critical_subregime_switch": (
        "Still under concurrent stress, but the 10-year signal changed. Same "
        "bucket weights, different instrument in the duration sleeve."
    ),
    "resilient_off": (
        "The Resilience Score rose back above 4.0. The portfolio returned to the "
        "Turbulence map and to the macro allocation percentages."
    ),
    "emergency_resilient": (
        "The Resilience Score held at or below 4.0 for two consecutive weeks. "
        "The portfolio rotated to the Resilient map."
    ),
    "hold": (
        "No trigger this week. Positions held; no transactions."
    ),
    "no_allocation_available": (
        "A rebalance was due but this week's allocation could not be read. "
        "Positions held rather than traded on an unverified allocation."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# Funções de decisão
# ─────────────────────────────────────────────────────────────────────────────


def classify_regime(score, stress_active=None, previous_regime="Turbulence"):
    """Regime a partir do medidor B e, subsidiariamente, do score.

    `stress_active` é o `stressGauge.active` do data.json: True, False ou None.
    None significa que ambos os gatilhos ficaram sem dados nessa corrida — o
    estado anterior mantém-se, nunca se assume calma."""
    if stress_active is None:
        return previous_regime or "Turbulence"
    if stress_active:
        return "Critical"
    if score is not None and score <= RESILIENT_MAX:
        return "Resilient"
    return "Turbulence"


def score_band(score):
    """Rótulo do medidor A isolado, para colorir o número publicado. Não é o
    regime da carteira: um score de 8,5 com o medidor B desligado continua a
    ser Turbulence para efeitos de carteira."""
    if score is None:
        return "nd"
    if score <= RESILIENT_MAX:
        return "Resilient"
    if score >= CRITICAL_MIN:
        return "Critical"
    return "Turbulence"


def resolve_etf_map_key(regime, critical_subregime=None):
    """Critical tem duas chaves de mapa; os outros regimes mapeiam 1:1. Sem
    sub-regime resolvido, cai no lado defensivo."""
    if regime == "Critical":
        return critical_subregime or "Critical_Stress"
    return regime


def get_active_tickers(regime, critical_subregime=None):
    key = resolve_etf_map_key(regime, critical_subregime)
    return list(REGIME_ETF_MAP.get(key, REGIME_ETF_MAP["Turbulence"]).values())


def subregime_from_gauge(gauge_subregime, was_critical_last_week):
    """(sub-regime, nota). Porta assimétrica confirmada em Jul 2026: o TLT só se
    reconquista com uma queda do 10Y confirmada, e entrada fresca, sinal ausente
    ou qualquer dúvida caem no lado defensivo. `gauge_subregime` é o campo
    `stressGauge.subregime` — "FTQ", "STRESS" ou None."""
    if not was_critical_last_week:
        return ("Critical_Stress",
                "Fresh entry into Critical — defaulting to Stress-without-relief "
                "until a 10Y decline is confirmed.")
    if gauge_subregime == "FTQ":
        return ("Critical_FTQ",
                "Gauge B confirms a 10Y decline of at least 10bp over 3 months — "
                "Flight-to-Quality, TLT retained.")
    trend = "unavailable" if gauge_subregime is None else "no confirmed decline"
    return ("Critical_Stress",
            f"Gauge B reports {trend} on the 10Y — Stress-without-relief (defensive).")


def decide_rebalance(regime, was_regime, critical_subregime, was_subregime,
                     semestral, emergency_reason=None):
    """Motivo do rebalanceamento, ou None para manter as posições.

    Precedência: entrada e saída de Critical primeiro — é o evento a que a
    carteira existe para responder — depois o calendário semestral, depois a
    emergência por score baixo, e por fim a troca de sub-regime dentro de
    Critical (mesmas percentagens, outro instrumento)."""
    # Entrada e saída são imediatas, sem janela de confirmação: os gatilhos do
    # medidor B já são séries publicadas com atraso (Sahm mensal com um mês de
    # lag, delinquência trimestral com cinco) e, no backtest 2007-2026, uma
    # histerese de 1 a 6 meses custou ~0,3 pp de CAGR sem melhorar a quebra
    # máxima nem reduzir o número de trocas.
    if (regime == "Critical") != (was_regime == "Critical"):
        return "stress_on" if regime == "Critical" else "stress_off"
    # Saida de Resilient, pela mesma razao e com a mesma simetria: a entrada exige
    # confirmacao de duas semanas (o ramo de emergencia), a saida e imediata. Sem
    # isto, entrava-se em Resilient e so se saia no rebalanceamento semestral
    # seguinte — o mesmo buraco que Critical tinha, encontrado ao correr o backtest
    # final contra este modulo.
    if was_regime == "Resilient" and regime != "Resilient":
        return "resilient_off"
    if semestral:
        return "semestral_rebalance"
    if emergency_reason:
        return emergency_reason
    if regime == "Critical" and critical_subregime != was_subregime:
        return f"critical_subregime_switch:{was_subregime or 'none'}->{critical_subregime}"
    return None


def rebalance_copy(reason):
    """Texto publicável para um motivo de rebalanceamento, incluindo os motivos
    que trazem sufixo (`critical_subregime_switch:a->b`, `emergency_resilient_3.8`)."""
    if not reason:
        return REBALANCE_COPY["hold"]
    for key, text in REBALANCE_COPY.items():
        if reason == key or reason.startswith(key):
            return text
    return reason


def effective_bucket_alloc(regime, critical_subregime, newsletter_alloc):
    """(alocação por bucket, origem). Em Critical o vector fixo passa à frente
    das percentagens da newsletter; fora de Critical mandam as da newsletter."""
    key = resolve_etf_map_key(regime, critical_subregime)
    if key in CRITICAL_WEIGHTS:
        return dict(CRITICAL_WEIGHTS[key]), f"critical override ({key})"
    return dict(newsletter_alloc or {}), "newsletter"


def validate_allocation(alloc):
    """(ok, problemas). Verifica o total e as bandas por bucket. Uma alocação
    reprovada não é corrigida — é rejeitada, e quem chama mantém as posições."""
    problems = []
    if not alloc:
        return False, ["allocation is empty"]
    total = sum(alloc.values())
    if abs(total - 100.0) > ALLOCATION_TOTAL_TOLERANCE:
        problems.append(f"total {total:.1f}% is outside 100 ± {ALLOCATION_TOTAL_TOLERANCE:.0f}")
    for bucket, pct in alloc.items():
        if bucket not in BUCKETS:
            problems.append(f"unknown bucket {bucket!r}")
            continue
        lo, hi = ALLOCATION_BANDS[bucket]
        if not (lo <= pct <= hi):
            problems.append(f"{bucket} at {pct:.1f}% is outside its {lo:.0f}–{hi:.0f}% band")
    return (not problems), problems


def is_semestral_rebalance_week(target_date):
    """Última sexta-feira de Janeiro ou Junho."""
    if target_date.month not in SEMESTRAL_MONTHS or target_date.weekday() != 4:
        return False
    return (target_date + timedelta(days=7)).month != target_date.month


def next_semestral_date(from_date=None):
    """Próxima data de rebalanceamento semestral, como objecto date."""
    d = from_date or date.today()
    for year in (d.year, d.year + 1):
        for month in sorted(SEMESTRAL_MONTHS):
            for day in range(31, 24, -1):
                try:
                    cand = date(year, month, day)
                except ValueError:
                    continue
                if cand.weekday() == 4 and cand >= d:
                    return cand
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Exportação para o front-end
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Bandas de scoring dos cinco pilares — o medidor A
# ─────────────────────────────────────────────────────────────────────────────
#
# Isto era o último sítio onde as regras estavam escritas duas vezes: as funções
# score_* no fetch_data.py decidiam o score, e as tabelas da Academia diziam ao
# leitor outra coisa, escritas à mão no lançamento e nunca mais tocadas. As
# tabelas do site tinham 4 ou 5 linhas onde o código tem 8 ou 9, com limiares
# que já não batiam certo, e uma delas descrevia uma métrica que o framework
# tinha deixado de usar.
#
# Agora há um sítio só. O fetch_data.py pontua a partir daqui, o data.json
# publica isto, e o index.html desenha as tabelas e as caixas "Current Reading"
# a partir do que o data.json trouxer. Mexer numa banda muda o score, a tabela
# publicada e a frase que o leitor lê, na mesma passagem.
#
# Convenção das arestas, que não é detalhe — decide o score de quem cai
# exactamente em cima do limiar:
#   "hi_exclusive"  banda = [lo, hi)   testa x < hi   (Cycle, Premium)
#   "lo_exclusive"  banda = (lo, hi]   testa x <= hi  (Liquidity, Solvency, Debt)
# São as duas convenções que já estavam implícitas nas funções originais; ficam
# declaradas em vez de dependerem de quem lê o código.
#
# "worseWhen" diz de que lado da métrica está o risco, e é o que permite gerar
# a frase da caixa sem a escrever: a banda seguinte na direcção do risco é a
# distância que interessa publicar.

PILLAR_SCORING = {
    "cycle": {
        "id": "cycle",
        "coreQuestion": "Where are we in the credit cycle?",
        "roman": "I",
        "name": "Cycle",
        "metric": "10Y–2Y Yield Curve",
        "shortMetric": "10Y–2Y spread",
        "unit": "%",
        "digits": 2,
        "signed": True,
        "fredSeries": "T10Y2Y",
        "weight": 0.20,
        "scoredOn": "value",
        "scoredOnLabel": "the spread itself, in percentage points",
        "edges": "hi_exclusive",
        "worseWhen": "lower",
        "rangeHeader": "10Y–2Y Spread",
        "contextHeader": "Historical Reference",
        "bands": [
            {"lo": None,  "hi": -0.75, "score": 9.5, "label": "Deeply inverted",
             "context": "2000, 2006–07, 2023",
             "reading": "Peak monetary tightening. Credit contraction has historically followed within 6–18 months."},
            {"lo": -0.75, "hi": -0.50, "score": 8.5, "label": "Inverted",
             "context": "2019, mid-2022",
             "reading": "Inversion established. Bank net interest margins compressed; lending incentive falling."},
            {"lo": -0.50, "hi": -0.25, "score": 7.5, "label": "Moderately inverted",
             "context": "1998, 2005",
             "reading": "Cycle peak probability elevated. Credit still flowing, but on worsening terms."},
            {"lo": -0.25, "hi": 0.00,  "score": 6.5, "label": "Marginally inverted",
             "context": "The entry and exit of every inversion",
             "reading": "The curve sits on the boundary. Direction matters more than level here."},
            {"lo": 0.00,  "hi": 0.50,  "score": 5.5, "label": "Normalizing",
             "context": "2007–08, 2024–25",
             "reading": "Post-inversion steepening — historically the phase immediately preceding recession onset, not an all-clear."},
            {"lo": 0.50,  "hi": 0.75,  "score": 4.5, "label": "Late normalization",
             "context": "2008, 2020",
             "reading": "Steepening well under way, usually because the front end is being cut into weakness."},
            {"lo": 0.75,  "hi": 1.25,  "score": 3.5, "label": "Normal",
             "context": "2017–2018",
             "reading": "Mid-cycle expansion. Credit conditions supportive."},
            {"lo": 1.25,  "hi": 2.00,  "score": 2.5, "label": "Comfortably positive",
             "context": "2004, 2014",
             "reading": "Expansion with a healthy term premium. Banks well compensated for maturity transformation."},
            {"lo": 2.00,  "hi": None,  "score": 1.5, "label": "Steep",
             "context": "1992, 2010–2013",
             "reading": "Early-cycle recovery. Maximum credit incentive for banks."},
        ],
    },
    "liquidity": {
        "id": "liquidity",
        "coreQuestion": "How large are equity claims against the economy that services them?",
        "roman": "II",
        "name": "Liquidity",
        "metric": "Buffett Indicator (Total Equities / GDP)",
        "shortMetric": "Buffett Indicator",
        "unit": "%",
        "digits": 1,
        "signed": False,
        "fredSeries": "NCBEILQ027S + FBCELLQ027S + GDP",
        "weight": 0.20,
        # Único pilar pontuado por percentil e não pelo nível. A escala nominal
        # do rácio sobe com a economia ao longo de décadas, por isso um limiar
        # fixo em pontos percentuais envelhecia sozinho. O percentil recalibra-se.
        "scoredOn": "percentile",
        "scoredOnLabel": "the percentile rank of the ratio against its own history since 1945",
        "edges": "lo_exclusive",
        "worseWhen": "higher",
        "rangeHeader": "Percentile Rank",
        "contextHeader": "Historical Reference",
        "bands": [
            {"lo": None, "hi": 20,   "score": 1.5, "label": "Bottom quintile",
             "context": "1974–1982, 2009",
             "reading": "Equity capitalisation deeply depressed relative to output. Historically the best entry conditions on record."},
            {"lo": 20,   "hi": 35,   "score": 3.0, "label": "Well below average",
             "context": "Early 1990s, 2011",
             "reading": "Valuations undemanding against the size of the economy."},
            {"lo": 35,   "hi": 50,   "score": 4.0, "label": "Below median",
             "context": "1994, 2016",
             "reading": "Normal. Equity claims broadly proportionate to output."},
            {"lo": 50,   "hi": 65,   "score": 5.5, "label": "Above median",
             "context": "1997, 2005",
             "reading": "Beginning to run ahead of the real economy. Sensitivity to earnings disappointment rising."},
            {"lo": 65,   "hi": 80,   "score": 6.5, "label": "Elevated",
             "context": "1998, 2006, 2017",
             "reading": "Late-cycle territory. Returns increasingly dependent on multiple expansion rather than output growth."},
            {"lo": 80,   "hi": 90,   "score": 7.5, "label": "High",
             "context": "1999, 2019",
             "reading": "The claim on output is large enough that a normal recession implies a large repricing."},
            {"lo": 90,   "hi": 95,   "score": 8.5, "label": "Very high",
             "context": "2000, 2020",
             "reading": "Only the extremes of the record sit above here. Fragile to any liquidity withdrawal."},
            {"lo": 95,   "hi": None, "score": 9.5, "label": "Extreme",
             "context": "1999–2000 peak, 2021, present",
             "reading": "Top 5% of eighty years of history. Every prior visit to this band was followed by a major drawdown."},
        ],
    },
    "premium": {
        "id": "premium",
        "coreQuestion": "Are investors being compensated for taking equity risk?",
        "roman": "II",
        "name": "Premium",
        "metric": "Equity Risk Premium",
        "shortMetric": "ERP",
        "unit": "%",
        "digits": 2,
        "signed": False,
        "fredSeries": "DGS10",
        "weight": 0.25,
        "scoredOn": "value",
        "scoredOnLabel": "E/P minus the 10-year Treasury yield, in percentage points",
        "edges": "hi_exclusive",
        "worseWhen": "lower",
        "rangeHeader": "ERP Level",
        "contextHeader": "Interpretation",
        "sentinelThreshold": 0.80,
        "bands": [
            {"lo": None, "hi": 0.00, "score": 10.0, "label": "Negative",
             "context": "Equities yield less than the 10-year Treasury",
             "reading": "No compensation whatsoever for holding equity risk. The bond is the higher-yielding asset."},
            {"lo": 0.00, "hi": 0.50, "score": 9.0,  "label": "Critically compressed",
             "context": "1999–2000, 2018 briefly",
             "reading": "Effective risk parity with Treasuries. Sentinel alert active."},
            {"lo": 0.50, "hi": 0.80, "score": 8.0,  "label": "Below the Sentinel threshold",
             "context": "2021",
             "reading": "Inside the Red Alert band. Any rate or earnings shock lands on no cushion."},
            {"lo": 0.80, "hi": 1.20, "score": 7.0,  "label": "Compressed",
             "context": "2007, 2018",
             "reading": "Above the alert line but with little margin. Risk/return trade-off deteriorating."},
            {"lo": 1.20, "hi": 2.00, "score": 5.5,  "label": "Thin",
             "context": "2004–2006",
             "reading": "Compensation present but slim. Sensitive to rate moves."},
            {"lo": 2.00, "hi": 3.00, "score": 4.0,  "label": "Adequate",
             "context": "2014–2017 range",
             "reading": "Normal market functioning. Equities paid for their risk."},
            {"lo": 3.00, "hi": 4.00, "score": 2.5,  "label": "Comfortable",
             "context": "2012–2013",
             "reading": "Equities clearly favoured over bonds on a forward basis."},
            {"lo": 4.00, "hi": None, "score": 1.5,  "label": "Wide",
             "context": "2009–2011",
             "reading": "Deeply attractive against bonds. Large margin of safety."},
        ],
    },
    "solvency": {
        "id": "solvency",
        "coreQuestion": "Is the banking system functionally sound?",
        "roman": "III",
        "name": "Solvency",
        "metric": "Bank Delinquency Rate",
        "shortMetric": "delinquency rate",
        "unit": "%",
        "digits": 2,
        "signed": False,
        "fredSeries": "DRALACBN",
        "weight": 0.15,
        "scoredOn": "value",
        "scoredOnLabel": "the share of all bank loans 30+ days past due",
        "edges": "lo_exclusive",
        "worseWhen": "higher",
        "rangeHeader": "Delinquency Rate",
        "contextHeader": "Historical Reference",
        "bands": [
            {"lo": None, "hi": 1.00, "score": 1.5, "label": "Very low",
             "context": "2022–2023 trough",
             "reading": "Loan books performing better than at any point in the series."},
            {"lo": 1.00, "hi": 1.50, "score": 2.5, "label": "Low",
             "context": "2016–2019",
             "reading": "Excellent credit quality. Banking plumbing unobstructed."},
            {"lo": 1.50, "hi": 2.00, "score": 3.5, "label": "Long-run normal, lower half",
             "context": "2014–2015",
             "reading": "Normal. Losses within the range banks provision for as a matter of course."},
            {"lo": 2.00, "hi": 2.50, "score": 4.5, "label": "Long-run normal, upper half",
             "context": "1997–2000, 2005",
             "reading": "Still normal, but the direction of travel starts to matter."},
            {"lo": 2.50, "hi": 3.00, "score": 5.5, "label": "Elevated",
             "context": "2002, 2020 spike",
             "reading": "Credit quality deteriorating. Acceleration is the signal, not the level."},
            {"lo": 3.00, "hi": 4.00, "score": 6.5, "label": "Deteriorating",
             "context": "2003, early 2009",
             "reading": "Provisioning rising fast enough to constrain new lending."},
            {"lo": 4.00, "hi": 5.00, "score": 8.0, "label": "Severe",
             "context": "2009",
             "reading": "Balance-sheet repair displaces credit extension. Systemic stress."},
            {"lo": 5.00, "hi": None, "score": 9.5, "label": "Systemic",
             "context": "2009–2011 peak, near 5.0%",
             "reading": "Government intervention has historically been required from here."},
        ],
    },
    "debt": {
        "id": "debt",
        "coreQuestion": "Can households service their obligations?",
        "roman": "III",
        "name": "Debt",
        "metric": "Household DSR",
        "shortMetric": "household debt service ratio",
        "unit": "%",
        "digits": 2,
        "signed": False,
        "fredSeries": "TDSP",
        "weight": 0.20,
        "scoredOn": "value",
        "scoredOnLabel": "debt payments as a share of disposable personal income",
        "edges": "lo_exclusive",
        "worseWhen": "higher",
        "rangeHeader": "DSR Level",
        "contextHeader": "Historical Reference",
        "bands": [
            {"lo": None,  "hi": 10.00, "score": 2.0, "label": "Low",
             "context": "2020–2021 pandemic lows",
             "reading": "Ample disposable income after debt service. Consumer can absorb an income shock."},
            {"lo": 10.00, "hi": 10.50, "score": 3.5, "label": "Healthy",
             "context": "2012–2016 recovery",
             "reading": "Balance sheets well positioned."},
            {"lo": 10.50, "hi": 11.00, "score": 4.5, "label": "Normal",
             "context": "2017–2018",
             "reading": "Within the post-crisis range. No constraint on consumption."},
            {"lo": 11.00, "hi": 11.50, "score": 5.5, "label": "Elevated",
             "context": "2019, 2024–25",
             "reading": "Sensitivity to income shocks rising. Marginal consumption starts to depend on credit."},
            {"lo": 11.50, "hi": 12.00, "score": 6.5, "label": "High",
             "context": "2001, 2019 peak",
             "reading": "Debt service claims enough income that a labour market wobble transmits quickly to spending."},
            {"lo": 12.00, "hi": 12.50, "score": 7.5, "label": "Approaching the critical band",
             "context": "2005–2006",
             "reading": "The last two visits to this level preceded consumer-led downturns."},
            {"lo": 12.50, "hi": 13.00, "score": 8.5, "label": "Critical",
             "context": "2006–2008",
             "reading": "Household cash flow severely constrained. Recession amplifier."},
            {"lo": 13.00, "hi": None,  "score": 9.5, "label": "Peak-2007 territory",
             "context": "2007–2009, peaked near 13.2%",
             "reading": "Deleveraging becomes involuntary. Consumption contracts regardless of policy."},
        ],
    },
}

PILLAR_ORDER = ["cycle", "liquidity", "premium", "solvency", "debt"]
PILLAR_WEIGHTS = {pid: PILLAR_SCORING[pid]["weight"] for pid in PILLAR_ORDER}

PILLAR_STATUS_BANDS = [(4.0, "stable"), (6.0, "caution"), (7.5, "warning")]


def score_pillar(pillar_id, x):
    """Score 1–10 de um pilar a partir do valor que ele pontua.

    Devolve None quando x é None: um pilar sem dados fica em n/d e é excluído
    do composto, nunca substituído por um valor a meio da escala.
    """
    spec = PILLAR_SCORING[pillar_id]
    if x is None:
        return None
    hi_exclusive = spec["edges"] == "hi_exclusive"
    for band in spec["bands"]:
        hi = band["hi"]
        if hi is None:
            return band["score"]
        if (x < hi) if hi_exclusive else (x <= hi):
            return band["score"]
    return spec["bands"][-1]["score"]


def pillar_band(pillar_id, x):
    """A banda em que o valor cai — o dicionário completo, para quem precisa do
    rótulo e do texto e não só do número."""
    spec = PILLAR_SCORING[pillar_id]
    if x is None:
        return None
    hi_exclusive = spec["edges"] == "hi_exclusive"
    for band in spec["bands"]:
        hi = band["hi"]
        if hi is None:
            return band
        if (x < hi) if hi_exclusive else (x <= hi):
            return band
    return spec["bands"][-1]


def pillar_status(score):
    """Rótulo de cor de um pilar. Não confundir com score_band(), que rotula o
    composto e decide o regime."""
    if score is None:
        return "nd"
    for limit, label in PILLAR_STATUS_BANDS:
        if score <= limit:
            return label
    return "critical"


def global_score(scores):
    """Composto ponderado dos cinco pilares.

    Pilares em n/d são EXCLUÍDOS e os pesos renormalizados sobre os restantes,
    em vez de entrarem no composto com um score inventado.

    Devolve (score, lista_de_pilares_em_nd).
    """
    nd = sorted(pid for pid in PILLAR_WEIGHTS if scores.get(pid) is None)
    ok = {pid: w for pid, w in PILLAR_WEIGHTS.items() if scores.get(pid) is not None}
    if not ok:
        return None, nd
    total = sum(ok.values())
    return round(sum(scores[pid] * w / total for pid, w in ok.items()), 2), nd


def as_dict():
    """Bloco `rules` do data.json. O index.html lê os limiares e os rótulos
    daqui em vez de os ter escritos no JavaScript."""
    return {
        "resilientMax": RESILIENT_MAX,
        "criticalMin": CRITICAL_MIN,
        "regimeDecidedBy": "gaugeB",
        "regimeLabels": dict(REGIME_LABELS),
        "subregimeLabels": dict(SUBREGIME_LABELS),
        "gaugeACaption": GAUGE_A_CAPTION,
        "gaugeBCaption": GAUGE_B_CAPTION,
        "buckets": list(BUCKETS),
        "etfMap": {k: dict(v) for k, v in REGIME_ETF_MAP.items()},
        "criticalWeights": {k: dict(v) for k, v in CRITICAL_WEIGHTS.items()},
        "allocationBands": {k: list(v) for k, v in ALLOCATION_BANDS.items()},
        # As bandas dos pilares vão para o site inteiras: o index.html desenha
        # as tabelas de scoring da Academia e as caixas "Current Reading" a
        # partir daqui, em vez de as ter escritas à mão no HTML.
        "pillarOrder": list(PILLAR_ORDER),
        "pillarScoring": {
            pid: {**{k: v for k, v in spec.items() if k != "bands"},
                  "bands": [dict(b) for b in spec["bands"]]}
            for pid, spec in PILLAR_SCORING.items()
        },
        "pillarStatusBands": [list(b) for b in PILLAR_STATUS_BANDS],
    }
