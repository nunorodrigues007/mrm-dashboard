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
    }
