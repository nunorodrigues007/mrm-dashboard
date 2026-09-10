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
# TODOS os pesos vivem aqui, um vector por regime. Até Set 2026 só os de Critical
# viviam: fora de Critical as percentagens vinham da tabela da newsletter, escrita
# semana a semana por um LLM e lida de volta pelo motor. Isso punha um modelo de
# linguagem dentro da cadeia de decisão no único momento em que os pesos mudam, e
# explica a maioria dos defeitos com dinheiro em risco que as auditorias 7-14
# encontraram: a alocação a somar 95, a coluna escolhida por palavra-chave, o
# "45% → 30%" lido como 45, a classe de activo não reconhecida. Nenhum era um
# defeito de regime — eram todos falhas a ler aquela tabela.
#
# Os vectores de Resilient e Turbulence são os que o backtest 2007-2026 sempre
# usou (ver backtest/final_backtest.py, que agora os importa daqui em vez de os
# repetir). Estavam nos dois sítios com valores DIFERENTES: o backtest publicava
# 6,56% de CAGR e −16,4% de quebra máxima sobre um vector que o sistema vivo
# nunca executou. Uma constante em dois sítios diverge sempre, e esta divergia
# em 22 pontos percentuais de acções.
#
# Os de Critical: decisão de Set 2026, depois do mesmo backtest, onde trocar só
# os instrumentos capturava menos de metade da protecção — 2008 fechava a −9,8%
# com a troca de ETF apenas, contra +1,5% com instrumentos e pesos, e a quebra
# máxima ficava em −20,8% em vez de −16,4%. Custo declarado: ~0,30 pp de CAGR.

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


def _para_percentagem(bruto):
    """Normaliza um vector de pesos a 100%. Os pesos de Turbulence estão escritos
    como as proporções do backtest (somam 96,5), e é a normalização — e não uma
    segunda cópia arredondada — que os torna percentagens. Arredondar aqui era
    mudar os números que o backtest publica."""
    total = sum(bruto.values())
    return {b: v / total * 100 for b, v in bruto.items()}


_RESILIENT_RAW  = {"US_EQUITIES": 55.0, "US_TREASURIES": 5.0, "IG_CREDIT": 15.0,
                   "COMMODITIES": 5.0, "CASH": 5.0, "ALTERNATIVES": 15.0}
_TURBULENCE_RAW = {"US_EQUITIES": 40.0, "US_TREASURIES": 19.0, "IG_CREDIT": 15.0,
                   "COMMODITIES": 6.0, "CASH": 14.0, "ALTERNATIVES": 2.5}

# A tabela única: regime (ou sub-regime de Critical) -> percentagens. As chaves
# são as mesmas do REGIME_ETF_MAP, e o `resolve_etf_map_key` resolve as duas.
REGIME_WEIGHTS = {
    "Resilient":  _para_percentagem(_RESILIENT_RAW),
    "Turbulence": _para_percentagem(_TURBULENCE_RAW),
    **{k: dict(v) for k, v in CRITICAL_WEIGHTS.items()},
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

# Um bucket sem banda declarada era um `KeyError` la dentro do
# `validate_allocation` — no meio do job da carteira, com a causa a tres
# ficheiros de distancia. Se as duas listas divergirem, que divirjam aqui.
assert set(BUCKETS) == set(ALLOCATION_BANDS), (
    "BUCKETS e ALLOCATION_BANDS tem de cobrir exactamente os mesmos buckets: "
    f"{sorted(set(BUCKETS) ^ set(ALLOCATION_BANDS))}")

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

# ── Custos de transacção ─────────────────────────────────────────────────────
#
# Vivem aqui porque o backtest e a carteira real têm de cobrar o mesmo. Enquanto
# estiveram só no backtest, o site publicava lado a lado um histórico com
# comissões deduzidas e uma carteira real sem custo nenhum, como se fossem
# comparáveis: cinco rebalanceamentos × seis posições ≈ $300 sobre $10.000, quase
# metade do P&L publicado de +6,18%.
#
# Uma posição é um ticker: dois buckets que partilhem instrumento são uma linha
# só no corretor, e pagam uma vez.
COST_OPEN  = 10.0
COST_CLOSE = 10.0


def trade_cost(old_shares, new_shares, model="open_close"):
    """(custo em dólares, detalhe) de passar de uma carteira à outra.

    `open_close` é a leitura literal — paga-se ao abrir e ao fechar uma posição.
    `every_trade` acrescenta o acerto de peso de uma posição já detida, que num
    corretor que cobre por ordem também é uma ordem.

    A carteira real cobra a literal, e convém dizer com franqueza o que isso
    significa: é o limite INFERIOR, não o superior. Um rebalanceamento semestral
    dentro do mesmo mapa não abre nem fecha linha nenhuma — só acerta pesos — e
    sob `open_close` custa $0, quando um corretor por ordem cobraria $60. O P&L
    publicado é, nessas semanas, o mais favorável dos dois. A escolha é uma
    convenção declarada — o backtest publica os dois modelos lado a lado, para
    que se veja a diferença — e não uma afirmação de que os custos estão
    resolvidos."""
    old_shares = {t: q for t, q in (old_shares or {}).items() if q}
    new_shares = {t: q for t, q in (new_shares or {}).items() if q}
    abertas  = [t for t in new_shares if t not in old_shares]
    fechadas = [t for t in old_shares if t not in new_shares]
    ajustadas = [t for t in new_shares if t in old_shares
                 and abs(new_shares[t] - old_shares[t]) > 1e-9]
    custo = COST_OPEN * len(abertas) + COST_CLOSE * len(fechadas)
    if model == "every_trade":
        custo += COST_OPEN * len(ajustadas)
    return round(custo, 2), {"opened": sorted(abertas), "closed": sorted(fechadas),
                             "adjusted": sorted(ajustadas), "model": model}


REBALANCE_COPY = {
    "stress_on": (
        "Gauge B fired. The portfolio moved to the Critical map: defensive "
        "instruments and the declared Critical weights, which override this "
        "week's newsletter allocation for as long as stress persists."
    ),
    "stress_off_to_resilient": (
        "Gauge B stood down and the Resilience Score has held at or below 4.0 for "
        "two consecutive readings. The portfolio moved directly from the Critical "
        "map to the Resilient map, without passing through Turbulence."
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
    "valuation_incomplete_held": (
        "A rebalance was due, but at least one held instrument could not be priced "
        "at all. Sizing new positions against an incomplete portfolio value would "
        "have lost that capital, so positions were held instead."
    ),
    "valuation_not_credible_held": (
        "A rebalance was due, but the price used for at least one held instrument "
        "has been frozen for longer than the declared limit. That valuation is no "
        "longer credible, and sizing new positions against it would have committed "
        "capital that may not exist, so positions were held instead."
    ),
    "missing_prices_held": (
        "A rebalance was due but at least one instrument had no usable price. "
        "Positions were held rather than executing a partial allocation that "
        "would have left part of the portfolio unassigned."
    ),
    "aborted_invalid_shares": (
        "A rebalance was computed but failed its value check before execution. "
        "Positions were held."
    ),
    "stale_allocation_held": (
        "The scheduled semi-annual rebalance was due, but this week's allocation "
        "table could not be read. The semi-annual rebalance exists to apply THIS "
        "week's published percentages, so positions were held rather than traded "
        "on an allocation from an earlier edition that nobody published this week."
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
    if score is None:
        # Sem score, uma avaria de dados nao pode gerar transaccoes: estando em
        # Resilient, o codigo devolvia "Turbulence" e isso produzia um
        # `resilient_off` — uma rotacao completa da carteira por falha de rede.
        #
        # A excepcao e Critical: o medidor disse explicitamente OFF, e quem
        # decide Critical e o medidor, nao o score. Sair e obrigatorio, e sem
        # score nao ha como confirmar Resilient — logo, Turbulence.
        if previous_regime == "Critical":
            return "Turbulence"
        return previous_regime or "Turbulence"
    if score <= RESILIENT_MAX:
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


# Os regimes que a carteira pode deter. Declarados, para que quem lê um regime
# de um ficheiro possa perguntar se é um deles em vez de supor que sim.
REGIMES = ("Resilient", "Turbulence", "Critical")


def subregime_do_mapa(active_etf_map):
    """Qual dos dois vectores de Critical um mapa de ETFs É, ou None.

    Não é uma adivinha: os dois vectores diferem no instrumento da manga de
    duração (TLT contra SHY), e o mapa está escrito no `portfolio.json`. Quando
    o campo `critical_subregime` se perde — um ficheiro reconstruído à mão, uma
    versão anterior — a carteira continua a DIZER o que detém; lê-se dela, em
    vez de se inventar um valor por omissão ou de se congelar para sempre."""
    if not isinstance(active_etf_map, dict) or not active_etf_map:
        return None
    for chave in CRITICAL_WEIGHTS:
        if all(active_etf_map.get(b) == t
               for b, t in REGIME_ETF_MAP[chave].items()):
            return chave
    return None


def normaliza_regime(regime, critical_subregime=None, active_etf_map=None):
    """(regime, sub-regime, nota) a partir do que estiver escrito no ficheiro.

    O `critical_subregime` já era validado; o `regime` não, e é o campo com a
    confusão mais convidativa de todas: o site imprime "Portfolio on the
    Critical_FTQ map", o RUNBOOK fala em `Critical_FTQ`/`Critical_Stress` como
    o estado da carteira, e o Caso 4 manda o operador reconstruir estado à mão.
    Quem escrever `"regime": "Critical_FTQ"` fazia
    `was_critical_last_week = (was_regime == "Critical")` ficar FALSO: a porta
    assimétrica via uma entrada fresca e o motor vendia todo o TLT — 35% da
    carteira — numa semana em que o medidor tinha confirmado a descida do 10Y.
    E `resolve_etf_map_key` devolvia a string intacta, que caía no mapa de
    Turbulence por omissão: risk-on, com o ficheiro a dizer Critical.

    Um sub-vector de Critical no campo do regime NÃO é uma adivinha: a string
    nomeia um dos dois vectores de crise, portanto a carteira está em Critical
    e é esse o sub-regime. O resto — maiúsculas trocadas, um regime que não
    existe — é ilegível, e devolve-se `None` para quem chama decidir o que faz
    com isso. Nunca se devolve em silêncio um regime que ninguém escreveu."""
    # O sub-regime leva a MESMA tolerância que o regime. Repará-la só num dos
    # campos era pior do que não a ter: o mesmo texto — "critical_ftq",
    # "Critical_FTQ " — escrito no campo do regime era reparado, e escrito no
    # campo a que pertence era deitado fora. E deitá-lo fora custava a
    # transacção: com o sub-regime anterior apagado, o ramo "corrida sem dados"
    # (que existe precisamente para RETER) não tinha nada para reter, degradava
    # para o vector defensivo, e `decide_rebalance` via uma troca — vendia o
    # TLT, 35% da carteira, numa semana em que o medidor não leu nada, e a nota
    # gravada ao lado dizia "previous sub-regime retained".
    #
    # "FTQ"/"STRESS" entram aqui porque são o vocabulário que o PRÓPRIO produtor
    # publica em `stressGauge.subregime`, e é o que o site mostra a quem
    # reconstrói estado à mão.
    _mapa_sub = {k.casefold(): k for k in CRITICAL_WEIGHTS}
    _mapa_sub.update({"ftq": "Critical_FTQ", "stress": "Critical_Stress"})
    sub, _nota_sub = None, None
    if isinstance(critical_subregime, str):
        sub = _mapa_sub.get(critical_subregime.strip().casefold())
        if sub is None:
            _nota_sub = f"critical_subregime ilegivel: {critical_subregime!r}"
        elif sub != critical_subregime:
            _nota_sub = (f"critical_subregime com grafia diferente "
                         f"({critical_subregime!r}); lido como {sub}")
    elif critical_subregime is not None:
        _nota_sub = f"critical_subregime ilegivel: {critical_subregime!r}"

    def _junta(*partes):
        # As duas queixas acumulam-se. A versao anterior descartava a do
        # sub-regime sempre que o regime tambem fosse ilegivel — e era esse o
        # caso em que quem le mais precisava de saber das duas.
        return "; ".join(x for x in partes if x) or None

    def _sai(reg_, sub_, *notas):
        # UM so ponto de saida decide "Critical sem sub-regime le-se do mapa".
        # Com a regra espalhada por ramo, o ramo da grafia diferente ficou sem
        # ela: `"critical"` em minusculas era reparado como regime mas nao
        # perguntava nada ao mapa, e a jusante forcava-se o lado defensivo sobre
        # uma carteira que detem o vector FTQ — uma rotacao completa da carteira
        # publicada sob um motivo que descreve uma troca a partir de um
        # sub-regime em que ela nunca esteve.
        if reg_ == "Critical" and sub_ is None:
            sub_ = subregime_do_mapa(active_etf_map)
            if sub_:
                notas = notas + (f"sub-regime lido do mapa detido: {sub_}",)
        return reg_, sub_, _junta(*notas)

    nota = _nota_sub
    if isinstance(regime, str):
        if regime in REGIMES:
            return _sai(regime, sub, nota)
        # Um so ramo por caso, e a comparacao insensivel a grafia SUBSUME a
        # exacta: manter as duas deixava um par de mutantes equivalentes, cada
        # um a tapar o outro, que e outra maneira de dizer que uma delas nao
        # estava testada.
        _casado = {r.casefold(): r for r in REGIMES}.get(regime.strip().casefold())
        if _casado:
            return _sai(_casado, sub,
                        None if regime == _casado else
                        f"regime com grafia diferente ({regime!r})", _nota_sub)
        # "Critical_FTQ" no campo do regime: a carteira ESTA em Critical, e a
        # string diz em que vector. Adopta-se, e o sub-regime lido so prevalece
        # se ele proprio for legivel.
        # A MESMA tabela que o sub-regime usa: "FTQ" e "STRESS" sao o
        # vocabulario que o produtor publica, e reparar essa grafia num campo e
        # deita-la fora no outro foi exactamente a queixa que custou uma venda
        # do TLT — nao se repete com os campos trocados.
        _casado_sub = _mapa_sub.get(regime.strip().casefold())
        if _casado_sub:
            return _sai("Critical", (sub or _casado_sub),
                        f"regime escrito como sub-vector ({regime!r}); lido como "
                        f"Critical/{sub or _casado_sub}", _nota_sub)

    # Nem o campo do regime nem o do sub-regime se leram. Antes de declarar o
    # regime ilegivel, pergunta-se ao MAPA que a carteira detem — que e o mesmo
    # principio que ja se aplica ao sub-regime perdido, e e mais forte do que
    # qualquer string: os dois vectores de Critical diferem no instrumento da
    # manga de duracao, e o mapa esta escrito no ficheiro. Sem isto, uma
    # carteira com TLT e o campo mal escrito era declarada Turbulence, a porta
    # assimetrica via uma entrada fresca, e vendiam-se 35% dela; e com o medidor
    # calmo, a edicao publicava aos subscritores os ETFs de Turbulence sobre uma
    # carteira que detem os de Critical, semana apos semana.
    # Um SUB-REGIME legivel diz, por si so, que a carteira esta em Critical: os
    # dois vectores so existem la dentro. E informacao do proprio ficheiro, nao
    # uma adivinha, e vale mais do que uma string do regime que ninguem
    # consegue ler.
    if sub:
        return _sai("Critical", sub, _junta(
            f"regime ilegivel ({regime!r}); o sub-regime {sub} so existe dentro "
            f"de Critical, e e esse o estado", _nota_sub))
    _do_mapa = subregime_do_mapa(active_etf_map)
    if _do_mapa:
        return _sai("Critical", (sub or _do_mapa),
                    f"regime ilegivel ({regime!r}); a carteira detem o mapa "
                    f"{_do_mapa}, e e esse o estado", _nota_sub)
    return None, sub, _junta(f"regime ilegivel: {regime!r}", _nota_sub)


def numero_de_edicao(valor):
    """O numero de edicao que `valor` representa, ou None.

    Aceita `27`, `27.0`, `"27"` e `"#27"` (a forma que sai de copiar a linha
    "Issue #27: 412 enviados" do log, que e o que o RUNBOOK manda procurar).
    NAO aceita a string `"27.0"`: ai nao se sabe se o autor queria a edicao 27
    ou escreveu outra coisa, e uma marca inventada e pior do que uma marca
    ilegivel — esta ultima e preservada e corrigida a mao. As tres formas saem de sitios diferentes — o
    codigo escreve int, um JSON reconstruido a mao escreve string, um editor
    distraido escreve float — e os leitores tratavam-nas de maneiras diferentes:
    o `already_sent` comparava com `==`, portanto `"27" == 27` era False e a
    marca ficava INERTE. A edicao era gerada de novo, com texto novo, publicada
    por cima e enviada a lista toda uma segunda vez — que e exactamente o
    desastre que este ficheiro existe para impedir. O `mark_sent`, por sua vez,
    descartava `27.0` que o `already_sent` honrava. Um so normalizador para os
    tres.
    """
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float):
        return int(valor) if valor.is_integer() else None
    if isinstance(valor, str):
        # O `#` tolera-se: a linha que o RUNBOOK manda o operador procurar no
        # log e "Issue #N: X enviados", e copia-la da `"#27"`. Sem esta linha,
        # o `already_sent` nao reconhecia a marca, a edicao era gerada de novo e
        # enviada a lista toda uma segunda vez.
        v = valor.strip().lstrip("#").strip()
        # `isdigit()` nao chega: `"\u00b2".isdigit()` e True e `int("\u00b2")` levanta.
        # E o `sanear_marca` e a PRIMEIRA coisa que o `main()` faz — uma
        # excepcao aqui mata o job antes de gerar seja o que for, que e
        # exactamente o que aquela funcao nao pode ser.
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return None


def resolve_etf_map_key(regime, critical_subregime=None):
    """Critical tem duas chaves de mapa; os outros regimes mapeiam 1:1. Um
    sub-regime que não seja uma das duas chaves cai no lado defensivo.

    `critical_subregime or "Critical_Stress"` só protegia contra None e "". Um
    valor QUALQUER — a forma curta que o medidor publica ("FTQ"), um
    `portfolio.json` reconstruído à mão, um estado de uma versão anterior —
    passava intacto, e a jusante degradava em silêncio para o lado errado:
    `get_active_tickers` faz `REGIME_ETF_MAP.get(key, REGIME_ETF_MAP["Turbulence"])`
    e `effective_bucket_alloc` vê que a chave não está em `CRITICAL_WEIGHTS` e
    devolve a alocação da NEWSLETTER. Resultado: com o regime a dizer Critical,
    a carteira ia para o mapa risk-on e para as percentagens macro — a rotação
    exactamente oposta à que Critical existe para fazer, publicada sob a
    etiqueta "Critical". Um regime de crise não pode resolver para o mapa de
    Turbulence por causa de uma string."""
    if regime == "Critical":
        # `isinstance(str)` antes do `in`: um portfolio.json corrompido com uma
        # lista ou um dicionario neste campo faz `x in dict` levantar
        # TypeError, e um TypeError aqui e uma sexta sem carteira e sem
        # newsletter. Um valor absurdo tem de cair no lado defensivo, nao
        # rebentar o motor.
        if isinstance(critical_subregime, str) and critical_subregime in CRITICAL_WEIGHTS:
            return critical_subregime
        return "Critical_Stress"
    if regime in REGIMES:
        return regime
    # Um regime que ninguem escreveu nao pode resolver em silencio para o mapa
    # risk-on. O `get(key, REGIME_ETF_MAP["Turbulence"])` a jusante fazia
    # exactamente isso — inclusive com "Critical_FTQ" ou "critical" no campo, que
    # e o oposto do que a carteira detem. Aqui a duvida cai no lado defensivo.
    if isinstance(regime, str) and regime in CRITICAL_WEIGHTS:
        return regime
    return "Critical_Stress"


def get_active_tickers(regime, critical_subregime=None):
    key = resolve_etf_map_key(regime, critical_subregime)
    return list(REGIME_ETF_MAP.get(key, REGIME_ETF_MAP["Turbulence"]).values())


def subregime_from_gauge(gauge_subregime, was_critical_last_week,
                         was_subregime=None):
    """(sub-regime, nota). Porta assimétrica confirmada em Jul 2026: o TLT só se
    reconquista com uma queda do 10Y confirmada, e entrada fresca ou uma leitura
    que não confirma a descida caem no lado defensivo. `gauge_subregime` é o
    campo `stressGauge.subregime` — "FTQ", "STRESS" ou None.

    None é AUSÊNCIA de leitura, não uma leitura negativa, e as duas não podem
    dar o mesmo resultado. A janela de 3 meses do 10Y é a única coisa que separa
    35% da carteira em TLT de 20% em SHY: tratar a ausência como "não há
    descida" faz uma falha de rede vender o TLT — a transacção que o motor já se
    recusa a fazer quando o medidor inteiro fica sem dados. Já dentro de
    Critical, a ausência mantém o que a carteira detém; a entrada fresca
    continua a ir para o lado defensivo, porque aí não há nada para manter e o
    TLT teria de ser CONQUISTADO por uma medição que não houve."""
    if not was_critical_last_week:
        return ("Critical_Stress",
                "Fresh entry into Critical — defaulting to Stress-without-relief "
                "until a 10Y decline is confirmed.")
    if gauge_subregime is None:
        anterior = was_subregime if was_subregime in CRITICAL_WEIGHTS else "Critical_Stress"
        return (anterior,
                "Gauge B could not measure the 3-month 10Y window this run — the "
                "sub-regime in force was retained. Absence of a signal is not a "
                "signal, and a data outage does not move the portfolio.")
    if gauge_subregime == "FTQ":
        return ("Critical_FTQ",
                "Gauge B confirms a 10Y decline of at least 10bp over 3 months — "
                "Flight-to-Quality, TLT retained.")
    return ("Critical_Stress",
            "Gauge B reports no confirmed decline on the 10Y — "
            "Stress-without-relief (defensive).")


def decide_rebalance(regime, was_regime, critical_subregime, was_subregime,
                     semestral, emergency_reason=None):
    """Motivo do rebalanceamento, ou None para manter as posições.

    Precedência: entrada e saída de Critical primeiro — é o evento a que a
    carteira existe para responder — depois a saída de Resilient, depois a
    emergência por score baixo, depois a troca de sub-regime dentro de Critical,
    e só por fim o calendário semestral. Os factos específicos vêm antes da
    data: quando coincidem, o rebalanceamento acontece na mesma e o que muda é
    a etiqueta publicada, que passa a dizer o que realmente mudou."""
    # Entrada e saída são imediatas, sem janela de confirmação: os gatilhos do
    # medidor B já são séries publicadas com atraso (Sahm mensal com um mês de
    # lag, delinquência trimestral com cinco) e, no backtest 2007-2026, uma
    # histerese de 1 a 6 meses custou ~0,3 pp de CAGR sem melhorar a quebra
    # máxima nem reduzir o número de trocas.
    if (regime == "Critical") != (was_regime == "Critical"):
        if regime == "Critical":
            return "stress_on"
        # A saida de Critical pode ir para Turbulence OU para Resilient (medidor
        # OFF com a confirmacao de score baixo ja feita). O texto de "stress_off"
        # fala do mapa de Turbulence, e era publicado numa semana em que a
        # carteira tinha ido para QQQ/HYG/IWO.
        return "stress_off_to_resilient" if regime == "Resilient" else "stress_off"
    # Saida de Resilient, pela mesma razao e com a mesma simetria: a entrada exige
    # confirmacao de duas semanas (o ramo de emergencia), a saida e imediata. Sem
    # isto, entrava-se em Resilient e so se saia no rebalanceamento semestral
    # seguinte — o mesmo buraco que Critical tinha, encontrado ao correr o backtest
    # final contra este modulo.
    if was_regime == "Resilient" and regime != "Resilient":
        return "resilient_off"
    # A emergencia e a troca de sub-regime vem ANTES do calendario. Sao factos
    # especificos sobre o que mudou; "semestral_rebalance" e so a data. Quando
    # coincidiam, o texto publicado dizia "rebalanceamento de calendario" numa
    # semana em que o instrumento da manga de duracao tinha mudado.
    # A emergencia e a ENTRADA confirmada em Resilient. Dentro de Critical nao
    # tem sentido: o medidor B esta ON, a carteira esta no mapa defensivo, e o
    # score baixo e esperado — o proprio framework declara que tres dos cinco
    # pilares melhoram mecanicamente numa crise. Sem esta guarda, uma crise com
    # score <= 4,0 duas semanas seguidas devolvia `emergency_resilient`, a
    # newsletter dizia "the portfolio rotated to the Resilient map" enquanto o
    # cabecalho dizia "Critical · No Relief", e executava um rebalanceamento
    # TODAS as semanas enquanto a condicao se mantivesse.
    # `regime == "Resilient"`, nao `!= "Critical"`.
    #
    # A emergencia E a entrada confirmada em Resilient — so faz sentido quando o
    # regime resultante e Resilient. Com `!= "Critical"` disparava tambem em
    # Turbulence (rebalanceando o mapa de Turbulence e publicando "the portfolio
    # rotated to the Resilient map", que e falso) e voltava a disparar TODAS as
    # semanas ja dentro de Resilient, contra a regra declarada de nao haver
    # rebalanceamento tactico semanal.
    if emergency_reason and regime == "Resilient" and was_regime != "Resilient":
        return emergency_reason
    if regime == "Critical" and critical_subregime != was_subregime:
        return f"critical_subregime_switch:{was_subregime or 'none'}->{critical_subregime}"
    if semestral:
        return "semestral_rebalance"
    return None


def confirm_regime(want, was_regime, emergency_reason=None):
    """O regime que a carteira PODE deter, dado o que foi sinalizado.

    A entrada em Resilient exige confirmacao de duas leituras — e o ramo de
    emergencia que a concede. Sem ela, o regime sinalizado nao pode tornar-se
    operativo, e isso tem de ser decidido aqui e nao no motivo do
    rebalanceamento: bastava a semana calhar na ultima sexta de Janeiro ou Junho
    para um unico score <= 4,0 rodar a carteira para QQQ/HYG/IWO sob a etiqueta
    "semestral_rebalance", saltando por cima da confirmacao.

    Critical nao passa por aqui: e decidido pelo medidor B e e imediato por
    desenho, com o custo dessa escolha medido no backtest."""
    if want == "Resilient" and was_regime != "Resilient" and not emergency_reason:
        # Turbulence, NAO `was_regime`. Devolver o regime anterior prendia a
        # carteira em Critical: com o medidor a dizer OFF e o score <= 4,0, o
        # Resilient nao se confirma e o codigo devolvia "Critical", pelo que
        # decide_rebalance nao via mudanca nenhuma e nao havia sequer gatilho de
        # saida. Bastava uma corrida falhada para a janela de datas do
        # check_emergency nunca confirmar e o bloqueio ser indefinido.
        #
        # O regime nao confirmado cai no do meio, que e o unico que nao afirma
        # nada: Critical so vem do medidor B, e o medidor disse OFF.
        return "Turbulence"
    return want


def rebalance_copy(reason):
    """Texto publicável para um motivo de rebalanceamento, incluindo os motivos
    que trazem sufixo (`critical_subregime_switch:a->b`, `emergency_resilient_3.8`)."""
    if not reason:
        return REBALANCE_COPY["hold"]
    for key, text in REBALANCE_COPY.items():
        if reason == key or reason.startswith(key):
            return text
    return reason


def effective_bucket_alloc(regime, critical_subregime, newsletter_alloc=None):
    """(alocação por bucket, origem). Sai sempre do `REGIME_WEIGHTS`: o regime
    escolhe o vector, e nada mais o escolhe.

    `newsletter_alloc` continua na assinatura e é DELIBERADAMENTE ignorado — o
    parâmetro sobrevive para que um chamador antigo não rebente em silêncio, e
    para que este comentário apanhe quem o for procurar. Uma tabela escrita por
    um modelo pode ser publicada e verificada; não pode ser executada."""
    key = resolve_etf_map_key(regime, critical_subregime)
    return dict(REGIME_WEIGHTS[key]), f"rules ({key})"


def allocation_matches_rules(publicada, regime, critical_subregime=None,
                             tolerancia_pp=1.0):
    """(bate certo, problemas). Compara uma tabela PUBLICADA com o vector que o
    motor executou. Não decide nada: serve para a edição e a carteira dizerem o
    mesmo, e para dar o alarme quando não dizem.

    A tolerância é em pontos percentuais e existe porque a tabela é escrita para
    ser lida por uma pessoa — 41,45% aparece como 41%, e arredondar ao inteiro
    chega a desviar meio ponto."""
    esperada = REGIME_WEIGHTS[resolve_etf_map_key(regime, critical_subregime)]
    if not publicada:
        return False, ["a edição não publicou uma tabela de alocação legível"]
    problemas = []
    for bucket in BUCKETS:
        if bucket not in publicada:
            problemas.append(f"{bucket} não aparece na tabela publicada")
            continue
        desvio = abs(publicada[bucket] - esperada[bucket])
        if desvio > tolerancia_pp:
            problemas.append(
                f"{bucket}: a edição diz {publicada[bucket]:.1f}%, o motor "
                f"executou {esperada[bucket]:.1f}% ({desvio:.1f} pp de desvio)")
    for bucket in publicada:
        if bucket not in BUCKETS:
            problemas.append(f"a tabela publicada tem um bucket desconhecido: {bucket!r}")
    return (not problemas), problemas


def validate_allocation(alloc):
    """(ok, problemas). Verifica o total e as bandas por bucket. Uma alocação
    reprovada não é corrigida — é rejeitada, e quem chama mantém as posições."""
    problems = []
    if not alloc:
        return False, ["allocation is empty"]
    total = sum(alloc.values())
    if abs(total - 100.0) > ALLOCATION_TOTAL_TOLERANCE:
        problems.append(f"total {total:.1f}% is outside 100 ± {ALLOCATION_TOTAL_TOLERANCE:.0f}")
    # Um bucket em falta e o modo de falha mais provavel do parser: uma linha
    # cujo nome de classe de activo o LLM inventou e o mapeamento nao apanhou.
    # Sem esta verificacao, {40, 25, 15, 10, 8} soma 98 e passa na tolerancia,
    # com ALTERNATIVES a zero e sem um unico aviso. Um bucket ausente e uma
    # leitura falhada, nao uma decisao de alocar zero.
    missing = [b for b in BUCKETS if b not in alloc]
    if missing:
        problems.append(f"missing bucket(s): {', '.join(missing)}")
    for bucket, pct in alloc.items():
        if bucket not in BUCKETS:
            problems.append(f"unknown bucket {bucket!r}")
            continue
        lo, hi = ALLOCATION_BANDS[bucket]
        if not (lo <= pct <= hi):
            # `:g`, nao `:.0f`: com uma banda de 5,4 a mensagem dizia "outside
            # its 5–60% band" para um 5,0 rejeitado, e o operador lia um numero
            # que nao explicava a recusa.
            problems.append(f"{bucket} at {pct:.1f}% is outside its {lo:g}–{hi:g}% band")
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

# Séries que ENTRAM num pilar sem serem a série que lhe dá o nome.
#
# O E/P do pilar Premium é earnings/preço: os earnings vêm da âncora posta à
# mão, o preço vem do SP500. Uma SP500 parada não põe o Premium em n/d — ele
# continua a pontuar — mas pontua sobre o preço de outro dia, e o composto com
# ele. Enquanto esta dependência não estava declarada em lado nenhum, o
# consumidor via "SP500 não alimenta pilar nenhum" e publicava, como aviso
# obrigatório copiado à letra, "so the Resilience Score is unchanged" — numa
# semana em que o score está construído sobre um preço parado.
PILLAR_EXTRA_SERIES = {"premium": ["SP500"]}

# Uma série indirecta não pode ser, ao mesmo tempo, a série nomeada do pilar:
# se alguém a promover a `fredSeries`, o consumidor passaria a ter dois ramos
# para o mesmo caso e o mais fraco venceria.
for _pid_x, _sx in PILLAR_EXTRA_SERIES.items():
    assert _pid_x in PILLAR_ORDER, f"pilar desconhecido em PILLAR_EXTRA_SERIES: {_pid_x}"
    _nomeadas = str(PILLAR_SCORING[_pid_x].get("fredSeries") or "")
    for _s_x in _sx:
        assert _s_x not in _nomeadas, (
            f"{_s_x} já é a série nomeada do pilar {_pid_x}")
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


# Peso mínimo dos pilares vivos para o composto poder DECIDIR alguma coisa.
#
# A renormalização sobre os pilares que restam é o comportamento certo — um
# pilar em n/d não pode entrar com um valor inventado — mas não tinha chão: com
# quatro dos cinco em n/d, o composto passava a ser um único pilar com peso 1,0,
# e duas leituras seguidas ≤ 4,0 desse pilar rodavam a carteira inteira para o
# mapa Resilient. Todo o resto do sistema falha para o lado seguro quando os
# dados degradam — mantém posições e declara-o; era este o único sítio onde a
# degradação produzia a acção máxima, e na direcção risk-on, precisamente
# durante a cegueira.
#
# Três dos cinco pilares vivem de séries trimestrais: uma suspensão prolongada
# das publicações do BEA ou do Fed Z.1 deixa dois pilares de pé, e isso não é um
# cenário exótico.
#
# Metade do peso total. Com estes pesos (0,20 / 0,20 / 0,25 / 0,15 / 0,20) a
# fronteira cai limpa entre dois e três pilares: os dois mais pesados somam 0,45
# e os três mais leves somam 0,55. O limiar diz portanto, na prática, "pelo menos
# três dos cinco, sejam quais forem" — e continua a dizê-lo se os pesos mudarem,
# porque o que se exige é evidência, não uma contagem.
#
# Abaixo disso o composto é n/d, e o protocolo n/d que já existe mantém o regime
# anterior em vez de decidir.
MIN_PILLAR_WEIGHT = 0.50


def global_score(scores):
    """Composto ponderado dos cinco pilares.

    Pilares em n/d são EXCLUÍDOS e os pesos renormalizados sobre os restantes,
    em vez de entrarem no composto com um score inventado. Mas só há composto
    enquanto os pilares vivos valerem pelo menos `MIN_PILLAR_WEIGHT` do peso
    total: abaixo disso não há evidência suficiente para uma decisão, e o
    composto é n/d.

    Devolve (score, lista_de_pilares_em_nd).
    """
    nd = sorted(pid for pid in PILLAR_WEIGHTS if scores.get(pid) is None)
    ok = {pid: w for pid, w in PILLAR_WEIGHTS.items() if scores.get(pid) is not None}
    if not ok:
        return None, nd
    total = sum(ok.values())
    if total < MIN_PILLAR_WEIGHT * sum(PILLAR_WEIGHTS.values()) - 1e-9:
        return None, nd
    return round(sum(scores[pid] * w / total for pid, w in ok.items()), 2), nd


def as_dict():
    """Bloco `rules` do data.json. O index.html lê os limiares e os rótulos
    daqui em vez de os ter escritos no JavaScript."""
    return {
        # A lista canonica dos motivos de rebalanceamento. O index.html tinha
        # o seu proprio vocabulario escrito a mao e ficou sem o
        # `valuation_incomplete_held` desde o dia em que este foi criado: a
        # semana em que o motor recusou rebalancar por nao conseguir valorizar
        # a carteira aparecia no site como "Valuation incomplete held", sem
        # icone nem cor de aviso. Publicando-a aqui, um motivo novo passa a ser
        # detectavel do lado do JavaScript em vez de silenciosamente ignorado.
        "rebalanceReasons": sorted(REBALANCE_COPY),
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
