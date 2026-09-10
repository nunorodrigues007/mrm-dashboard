"""
US Macro-Resilience Matrix — FRED Data Engine
Fetches live macroeconomic data from FRED API and generates data.json
"""

import json
import requests

import mrm_gauge_b
import mrm_rules as rules
from data_freshness import MAX_OBS_AGE_DAYS, observacao_velha

from datetime import datetime, timedelta
import os
import sys

# A consola do Windows arranca em cp1252 e os emojis dos prints abaixo levantam
# UnicodeEncodeError: o script rebentava a meio quando corrido localmente, e com
# ele dois dos testes. Reconfigurar a saida mantem o log legivel nos dois sitios,
# em vez de empobrecer o output por causa de uma consola.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# ──────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────
FRED_API_KEY = os.environ.get("FRED_API_KEY")
if not FRED_API_KEY:
    raise RuntimeError(
        "FRED_API_KEY nao definida. Este ficheiro e servido publicamente em "
        "usmrm.net/fetch_data.py, pelo que nao pode ter chave embutida."
    )
FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# ──────────────────────────────────────────
# FRED FETCHER
# ──────────────────────────────────────────
def fetch_fred(series_id, limit=12, retries=3, backoff=5):
    """Fetch the most recent observations for a FRED series with retry logic."""
    import time
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
        "observation_start": (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")
    }
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(FRED_BASE, params=params, timeout=15)
            r.raise_for_status()
            observations = r.json().get("observations", [])
            valid = [o for o in observations if o["value"] not in (".", "")]
            # Ordenar aqui em vez de confiar no sort_order=desc da API. O
            # mrm_gauge_b faz isto de proposito e explica porque: se a ordem
            # vier trocada, a variacao entre duas observacoes mede-se ao
            # contrario e o sinal inverte-se sem dar erro. E precisamente o que
            # aconteceu com a janela do 10Y, uma camada acima.
            valid.sort(key=lambda o: o["date"], reverse=True)
            # O prazo de validade aplica-se AQUI, na fonte, e nao so no
            # latest_value.
            #
            # Estava so no acessor, e havia caminhos que nao passam por ele: o
            # `history_values("DGS10", ...)` produz a janela de 3 meses do 10Y
            # que escolhe entre Critical_FTQ (TLT, 35% da carteira) e
            # Critical_Stress (SHY). Com a serie parada, essa janela congela: se
            # estivesse em -12 bp no dia em que a serie morreu, o sistema
            # declarava Flight-to-Quality indefinidamente e o TLT ficava com 35%
            # da carteira por causa de uma leitura morta — exactamente o que a
            # porta assimetrica existe para impedir. A mesma serie estava
            # protegida num caminho e desprotegida no outro.
            if valid:
                velha, idade = observacao_velha(series_id, valid[0]["date"], _HOJE.get("d"))
                if velha:
                    SERIES_STALE[series_id] = idade
                    _ULTIMA_DATA[series_id] = valid[0]["date"]
                    print(f"  [WARN] {series_id}: observacao mais recente e de "
                          f"{valid[0]['date']} ({idade} dias, limite "
                          f"{MAX_OBS_AGE_DAYS.get(series_id)}) — serie parada. "
                          f"Tratada como indisponivel.")
                    return []
                SERIES_STALE.pop(series_id, None)
                _ULTIMA_DATA[series_id] = valid[0]["date"]
            return valid
        except Exception as e:
            print(f"  [R] Failed to fetch {series_id}: {e} (attempt {attempt}/{retries})")
            if attempt < retries:
                time.sleep(backoff * attempt)
    print(f"  [WARN] {series_id} unavailable after {retries} attempts — using fallback.")
    return []

def fetch_fred_full_history(series_id, retries=3, backoff=5):
    """Fetch the FULL available history for a FRED series (ascending, no date window).
    Used for series where we need the whole time series to self-calibrate (percentile
    ranking) rather than just the latest reading."""
    import time
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "asc",
        "limit": 100000,
    }
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(FRED_BASE, params=params, timeout=20)
            r.raise_for_status()
            observations = r.json().get("observations", [])
            valid = [o for o in observations if o["value"] not in (".", "")]
            # Ordenar aqui em vez de confiar no sort_order=desc da API. O
            # mrm_gauge_b faz isto de proposito e explica porque: se a ordem
            # vier trocada, a variacao entre duas observacoes mede-se ao
            # contrario e o sinal inverte-se sem dar erro. E precisamente o que
            # aconteceu com a janela do 10Y, uma camada acima.
            # ASCENDENTE aqui, ao contrario do fetch_fred: esta funcao pede
            # sort_order=asc e o seu contrato, escrito no docstring, e "ascending".
            # O `reverse=True` foi copiado do fetch_fred e contradizia-o — quem
            # lesse obs[0] como "o mais antigo" apanhava o mais recente.
            valid.sort(key=lambda o: o["date"])
            return valid
        except Exception as e:
            print(f"  [R] Failed to fetch full history for {series_id}: {e} (attempt {attempt}/{retries})")
            if attempt < retries:
                time.sleep(backoff * attempt)
    print(f"  [WARN] {series_id} full history unavailable after {retries} attempts.")
    return []

SERIES_STALE = {}    # série -> idade em dias da observação mais recente, quando velha
_ULTIMA_DATA = {}    # série -> data da observação mais recente vista, mesmo se velha
# O "hoje" da corrida, para os testes poderem fixar o relógio. Fica em estado de
# módulo porque o fetch_fred é chamado de dezenas de sítios e passar a data por
# todos eles seria pior do que isto.
_HOJE = {}


def latest_value(series_id, limit=12, hoje=None):
    """Valor mais recente de uma série e a data da observação.

    Uma observação demasiado velha devolve (None, data): o valor não é usado,
    mas a data continua a ser publicada em `meta.fredSeriesDates` para se ver
    porquê. Uma série descontinuada devolve 200 com o último número que teve,
    todas as semanas — sem esta verificação o pilar continuava a pontuar sobre
    uma leitura de há meses ou anos, e nada no ficheiro dizia que era velha.
    O protocolo n/d faz o resto: o pilar entra em `ndPillars` e sai da média."""
    if hoje is not None:
        _HOJE["d"] = hoje
    obs = fetch_fred(series_id, limit)
    if not obs:
        # Pode ser indisponibilidade ou serie parada; o fetch_fred ja distinguiu
        # e registou. A data da ultima observacao conhecida continua a ser
        # publicada, para se ver porque ficou n/d.
        return None, _ULTIMA_DATA.get(series_id)
    return float(obs[0]["value"]), obs[0]["date"]

def history_values(series_id, n=7, limit=12):
    """Return the last n valid float values (oldest first)."""
    obs = fetch_fred(series_id, limit)
    valid = obs[:n]
    valid.reverse()
    return [(float(o["value"]), o["date"]) for o in valid]

# ──────────────────────────────────────────
# PREMIUM PILLAR — E/P ANCORADO NOS EARNINGS
# ──────────────────────────────────────────
# O E/P do S&P 500 era uma constante mantida a mao, e por isso o pilar Premium
# so se movia com o 10Y. O erro nao era a constante envelhecer: era estar fixada
# na variavel errada. O E/P tem duas partes — os earnings, que so mudam quando
# saem resultados, uma vez por trimestre, e o preco, que muda todos os dias.
# Fixar o E/P fixava as duas: numa queda de 20% do mercado os earnings seriam os
# mesmos e o E/P real subiria um quarto, mas o pilar nao mexia. E este e um dos
# tres pilares que devem melhorar numa crise.
#
# Passa a ancorar-se nos EARNINGS: guarda-se E = (E/P de referencia) x (indice na
# data de referencia) e calcula-se E/P = E / indice de hoje. O indice diario vem
# da FRED (serie SP500), que ja usamos. O input manual continua a ser o mesmo par
# de numeros, uma vez por trimestre, mas passa a significar o que deve significar.
#
# Limitacao declarada, e o simetrico do erro anterior: entre actualizacoes os
# earnings ficam congelados, pelo que numa recessao com lucros a cair o E/P fica
# ─────────────────────────────────────────────────────────────────────────────
# Frescura do proprio data.json
# ─────────────────────────────────────────────────────────────────────────────
#
# Nada no sistema verificava a idade deste ficheiro. Se a FRED caisse, o
# build_data() rebentava, o passo de commit era saltado, e o data.json de ontem
# ficava no sitio — e as 22:00 a carteira decidia sobre ele sem um unico sinal
# de que era velho. O ficheiro passa a declarar quando foi gerado e a quantas
# horas se considera velho, e quem o le decide o que fazer com isso.
#
# Dois limiares, como no E/P: um avisa, o outro recusa. Trinta horas toleram
# exactamente uma corrida falhada (o update diario e as 18:00, o pipeline as
# 22:00 de sexta); quarenta e oito nao toleram duas.
#
# IMPORTADOS, nao redefinidos. O `data_freshness.py` existe porque a mesma
# pergunta nao pode ter duas respostas em dois ficheiros — o motor e o gerador
# ja os importam de la, e o PRODUTOR, que e quem os escreve no `data.json`,
# tinha a sua propria copia. Alargar o limite no modulo partilhado nao tinha
# efeito nenhum, porque o `limiar_declarado` faz `min(declarado, leitor)` e o
# documento continuava a declarar o numero antigo.
from data_freshness import (  # noqa: E402
    DATA_WARN_AFTER_HOURS, DATA_REFUSE_AFTER_HOURS)

# sobrestimado. Por isso ha um prazo de validade — ver EP_STALE_AFTER_DAYS.

# ── A referencia manual do E/P. Actualizar uma vez por trimestre, quando saem os
# resultados agregados: pos-se aqui o E/P observado e a data da observacao. Entre
# actualizacoes, o preco do indice faz o resto.
SP500_EARNINGS_YIELD = 3.84
SP500_EARNINGS_YIELD_ASOF = "2026-08-31"

# Acima disto o data.json declara a referencia velha e a newsletter leva um aviso
# de qualidade de dados.
EP_STALE_AFTER_DAYS = 100

# E acima DISTO deixa de ser uma medicao.
#
# Nao e um alarme que para o pipeline — houve um `EP_MAX_AGE_DAYS` que dizia
# "acima disto os testes falham", e um portao que fecha a sexta e pior do que o
# problema que assinala. E o protocolo n/d, que ja existe: o pilar sai do
# composto e os pesos renormalizam, exactamente como quando uma serie da FRED
# para. A razao e de dinheiro: entre actualizacoes os earnings estao congelados,
# e numa recessao — em que os lucros caem — o E/P fica sobrestimado e o Premium
# crava-se no 10,0. Um pilar cravado no extremo mantem o composto alto e SUPRIME
# a entrada em Resilient, que roda 100% da carteira. Isso nao pode depender de
# um [WARN] num log verde: passados seis meses sem actualizacao, a leitura nao
# vale como leitura e o sistema di-lo.
EP_ND_AFTER_DAYS = 180


def earnings_yield_now(ref_yield_pct, ref_date, index_obs):
    """(E/P de hoje em %, detalhe). Mantem os earnings da data de referencia e
    deixa o preco mexer. Sem serie do indice devolve a referencia inalterada, com
    o motivo declarado — nunca inventa."""
    detail = {
        "refYieldPct": ref_yield_pct,
        "refDate": ref_date,
        "indexRef": None, "indexNow": None, "indexNowDate": None,
        "basis": "reference only — S&P 500 index series unavailable",
    }
    if not index_obs:
        return ref_yield_pct, detail

    # o fecho na data de referencia, ou o ultimo anterior a ela
    at_or_before = [o for o in index_obs if o["date"] <= ref_date]
    if not at_or_before:
        detail["basis"] = f"reference only — no index close on or before {ref_date}"
        return ref_yield_pct, detail

    index_ref = float(at_or_before[0]["value"])
    index_now = float(index_obs[0]["value"])
    earnings = ref_yield_pct / 100.0 * index_ref      # earnings por unidade de indice
    detail.update({
        "indexRef": round(index_ref, 2),
        "indexRefDate": at_or_before[0]["date"],
        "indexNow": round(index_now, 2),
        "indexNowDate": index_obs[0]["date"],
        "earningsPerIndexUnit": round(earnings, 2),
        "basis": "earnings held from the reference date, price marked to the latest close",
    })
    return round(earnings / index_now * 100.0, 2), detail


def days_since(date_str, hoje=None):
    """Dias desde uma data, contados a partir de `hoje`.

    Recebe `hoje` como todo o resto do build_data: era a unica leitura do
    relogio real la dentro, o que fazia a corrida depender de quando corre em
    vez de da data que lhe e dada."""
    try:
        base = hoje or _HOJE.get("d") or datetime.utcnow().date()
        return (base - datetime.strptime(date_str, "%Y-%m-%d").date()).days
    except Exception:
        return None


# ──────────────────────────────────────────
# LIQUIDITY PILLAR — REAL BUFFETT INDICATOR
# ──────────────────────────────────────────
# Background: the original Liquidity pillar divided the FRED series WILL5000PRFC
# (Wilshire 5000 index level) by M2SL. FRED discontinued ALL Wilshire Index data on
# 3 Jun 2024 (https://news.research.stlouisfed.org/2024/04/fred-will-remove-wilshire-index-data-on-june-3-2024/)
# — a year and a half BEFORE this project's inception (Mar 2026) — so that call never
# once returned real data; the pillar has been silently running on a hardcoded
# placeholder (1.82x) since Issue #1. A Yahoo Finance fallback (^W5000) was evaluated
# and also rejected: as of Jul 2026 its feed is itself stale (9+ days), consistent with
# the Wilshire 5000 index's 2026 provider transition (Wilshire Advisors LLC acquiring
# Wilshire Indexes' assets) disrupting downstream distribution.
#
# Redefinition (going forward only — no historical data or past issues are touched):
# the Liquidity pillar now tracks the textbook Buffett Indicator — Total US Corporate
# Equities / GDP — built entirely from Fed/BEA national-accounts data that cannot be
# "discontinued" the way a single index vendor's feed can:
#   NCBEILQ027S  Nonfinancial corporate business; corporate equities, liability level
#   FBCELLQ027S  Domestic financial sectors; corporate equities, liability level
#   GDP          Gross Domestic Product (nominal, SAAR)
# All three are quarterly Z.1 / NIPA series published by the Fed/BEA — the same
# institutional-grade source already used for TDSP, DRALACBN, M2SL. Because the ratio's
# "normal" range drifts over 80 years of nominal growth, the score is NOT based on
# hardcoded dollar thresholds (which is what produced the original 1.82x guess) — it's
# the ratio's own percentile rank against its full available history, so it self-
# calibrates and needs no re-tuning as the economy grows.
LIQUIDITY_SERIES = {
    "nonfinancial_equities": "NCBEILQ027S",
    "financial_equities": "FBCELLQ027S",
    "gdp": "GDP",
}


def fetch_liquidity_percentile(hoje=None):
    """Returns (ratio, percentile, as_of_date, detail_dict).
    ratio = (NCBEILQ027S + FBCELLQ027S) / GDP for the latest quarter common to all
    three series. percentile = that ratio's rank (0-100) within its own full history.
    Returns (None, None, None, {}) if any series is unavailable."""
    ncb = fetch_fred_full_history(LIQUIDITY_SERIES["nonfinancial_equities"])
    fbc = fetch_fred_full_history(LIQUIDITY_SERIES["financial_equities"])
    gdp = fetch_fred_full_history(LIQUIDITY_SERIES["gdp"])
    if not (ncb and fbc and gdp):
        print("  [WARN] Could not fetch one or more Z.1/GDP series for Liquidity pillar.")
        return None, None, None, {}

    ncb_by_date = {o["date"]: float(o["value"]) for o in ncb}
    fbc_by_date = {o["date"]: float(o["value"]) for o in fbc}
    gdp_by_date = {o["date"]: float(o["value"]) for o in gdp}

    common_dates = sorted(set(ncb_by_date) & set(fbc_by_date) & set(gdp_by_date))
    if not common_dates:
        print("  [WARN] No overlapping quarters across NCBEILQ027S/FBCELLQ027S/GDP.")
        return None, None, None, {}

    ratios = []
    for d in common_dates:
        total_equities_b = (ncb_by_date[d] + fbc_by_date[d]) / 1000.0  # millions -> billions
        gdp_b = gdp_by_date[d]
        if gdp_b:
            ratios.append((d, total_equities_b / gdp_b))

    if not ratios:
        return None, None, None, {}

    latest_date, latest_ratio = ratios[-1]
    # O mesmo prazo de validade das outras séries. Estas três são trimestrais e
    # publicadas com meses de atraso, mas nada impede que uma delas pare — e o
    # pilar da Liquidez continuaria a pontuar um rácio de há anos como se fosse
    # a leitura do trimestre.
    velha, idade = observacao_velha(LIQUIDITY_SERIES["gdp"], latest_date, hoje)
    if velha:
        SERIES_STALE["NCBEILQ027S_FBCELLQ027S_GDP"] = idade
        print(f"  [WARN] Liquidez: o trimestre mais recente comum às três séries "
              f"é {latest_date} ({idade} dias) — parado. Tratado como n/d.")
        return None, None, latest_date, {}
    all_values = [r for _, r in ratios]
    rank = sum(1 for v in all_values if v <= latest_ratio)
    percentile = round(rank / len(all_values) * 100, 1)

    detail = {
        "totalEquitiesB": round((ncb_by_date[latest_date] + fbc_by_date[latest_date]) / 1000.0, 1),
        "gdpB": round(gdp_by_date[latest_date], 1),
        "historyPoints": len(all_values),
        "historyStart": ratios[0][0],
    }
    return round(latest_ratio, 4), percentile, latest_date, detail

# ──────────────────────────────────────────
# SCORING LOGIC
# ──────────────────────────────────────────
# As bandas de scoring dos cinco pilares, os pesos do composto e os limiares de
# cor dos pilares vivem no mrm_rules.py, ao lado das regras do portfolio, e vao
# no data.json para o site desenhar as tabelas da Academia a partir delas. As
# funcoes que aqui estavam escritas a mao foram substituidas por chamadas ao
# modulo canonico: um limiar mexido muda o score, a tabela publicada e a frase
# que o leitor ve, tudo na mesma passagem.
#
# Um pilar sem dados devolve None e e EXCLUIDO do composto com os pesos
# renormalizados sobre os restantes — nunca substituido por um valor inventado
# a meio da escala.

def score_cycle(spread):      return rules.score_pillar("cycle", spread)
def score_liquidity(pct):     return rules.score_pillar("liquidity", pct)
def score_premium(erp):       return rules.score_pillar("premium", erp)
def score_solvency(npl):      return rules.score_pillar("solvency", npl)
def score_debt(dsr):          return rules.score_pillar("debt", dsr)

global_score  = rules.global_score
pillar_status = rules.pillar_status


def status_label(score):
    """Rotulo do composto. Tem de ficar coerente com score_band() do mrm_rules,
    que e o que o resto do sistema usa."""
    return rules.score_band(score)


def _meses_contiguos(pontos):
    """Os pontos cobrem meses consecutivos, sem lacunas?

    O grafico desenha-os equidistantes; se houver meses em falta, a distancia
    desenhada nao e a distancia real. Em vez de o esconder, o data.json
    declara-o e o site di-lo — a mesma regra do protocolo n/d: o que nao se sabe
    aparece, nao se aproxima.
    """
    meses = [p.get("month") for p in pontos if isinstance(p, dict) and p.get("month")]
    if len(meses) != len(pontos) or len(meses) < 2:
        return len(meses) == len(pontos)
    for anterior, seguinte in zip(meses, meses[1:]):
        try:
            a_, m_ = (int(x) for x in anterior.split("-"))
            b_, n_ = (int(x) for x in seguinte.split("-"))
        except Exception:
            return False
        if (b_ - a_) * 12 + (n_ - m_) != 1:
            return False
    return True


# Dois dias de folga para o relogio do runner. A janela e UMA SO, exportada,
# porque quem escreve e quem verifica tem de usar exactamente a mesma: a versao
# anterior dava um mes de folga na escrita e zero na verificacao, e o unico
# ponto que o portao acusava era precisamente o unico que o produtor se recusava
# a limpar — sexta sem carteira e sem newsletter durante semanas, com o job que
# faria a limpeza travado pelo proprio portao. Uma folga de um lado sem a folga
# do outro nao e uma proteccao, e uma armadilha.
TOLERANCIA_RELOGIO_DIAS = 2


def mes_limite(dia):
    """O mes mais recente que um ponto do historico do score pode ter."""
    return (dia + timedelta(days=TOLERANCIA_RELOGIO_DIAS)).strftime("%Y-%m")


def actualiza_score_history(score, hoje, path):
    """Acrescenta (ou corrige) o ponto do mes corrente no score_history.json.

    Devolve True se o ficheiro mudou. O historico do score alimenta a sparkline
    e o grafico de 24 meses do site, que desenham os pontos EQUIDISTANTES: se o
    ficheiro parar no tempo, a distancia entre o ultimo ponto e o ponto corrente
    cresce um mes por mes e continua a ser desenhada como um passo mensal. O
    grafico mente cada vez mais, sozinho, e nenhum teste sobre o data.json o ve.

    Uma semana n/d NAO escreve ponto nenhum: o protocolo n/d diz que a ausencia
    de sinal nao e um sinal, e inventar um ponto no historico e pior do que uma
    lacuna. Uma segunda corrida no mesmo mes CORRIGE o ponto em vez de o
    duplicar — ha quatro ou cinco sextas por mes.
    """
    dia = hoje or datetime.utcnow().date()
    mes = dia.strftime("%Y-%m")
    _mes_limite = mes_limite(dia)

    def _no_futuro(p_):
        """Um ponto de um mes POSTERIOR ao da corrida nao existe ainda.

        Um ensaio com uma data futura — o proprio `test_build_data` verifica a
        idade da referencia do E/P a 400 dias — deixava o seu ponto no ficheiro,
        e a corrida seguinte nao lhe tocava: a janela `[-24:]` puxava-o para o
        grafico e o site publicava, como ultimo ponto do historico, um mes que
        ainda nao aconteceu, com o score de outro mes.
        """
        if not isinstance(p_, dict):
            return False
        m_ = p_.get("month")
        # Sem `month` PRESERVA-SE: um ponto sem etiqueta e um ponto por
        # etiquetar, nao um ponto errado, e apagar historico e sempre pior do
        # que o deixar por arrumar. (A janela da leitura filtra-o de qualquer
        # maneira.)
        if not m_:
            return True
        # A folga do relogio, e so ela: dois dias chegam para o runner estar
        # adiantado na viragem do mes, e nao abrem uma janela de um mes inteiro
        # em que o produtor preserva o que o portao recusa.
        return m_ <= _mes_limite
    try:
        with open(path) as f:
            historico = json.load(f)
        if not isinstance(historico, list):
            raise ValueError("o historico nao e uma lista")
    except FileNotFoundError:
        historico = []
    except Exception as e:
        # Nao se reescreve o que nao se percebe: um ficheiro ilegivel e um
        # problema a resolver a mao, nao uma razao para o substituir por um
        # ponto so — isso apagaria vinte anos de historico.
        print(f"  [WARN] score_history.json ilegivel ({e}) — NAO foi actualizado")
        return False
    # A limpeza dos pontos futuros acontece MESMO numa semana n/d: um ponto que
    # nao devia la estar e um erro a corrigir, e nao ha nada de n/d nisso. So a
    # ESCRITA do ponto da semana e que depende de haver score.
    _limpo = [p_ for p_ in historico if _no_futuro(p_)]
    _mudou = len(_limpo) != len(historico)
    if _mudou:
        # Apagar em silencio nunca: o operador tem de poder saber o que saiu.
        _fora = [p_.get("month") for p_ in historico
                 if isinstance(p_, dict) and p_ not in _limpo]
        print(f"  [WARN] score_history.json tinha {len(historico) - len(_limpo)} "
              f"ponto(s) de meses posteriores a {mes} ({_fora}) — removidos.")
    historico = _limpo
    if score is None:
        if not _mudou:
            return False
    else:
        ponto = {"date": dia.strftime("%b '%y"), "score": round(float(score), 2),
                 "month": mes}
        for i, p_ in enumerate(historico):
            if isinstance(p_, dict) and p_.get("month") == mes:
                if p_ == ponto and not _mudou:
                    return False
                historico[i] = ponto
                break
        else:
            historico.append(ponto)
    # Os pontos sem etiqueta ficam onde estao, a cabeca: sao os mais antigos
    # (o esquema com `month` e recente) e ordena-los por uma chave que nao tem
    # rebentava a corrida — que e o primeiro job da sexta.
    historico.sort(key=lambda p_: p_.get("month") or "")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(historico, f, indent=2)
    os.replace(tmp, path)
    return True


def pillar_identity(pillar_id):
    """id/roman/name/metric/fredSeries vindos do mrm_rules, para nao existir uma
    terceira copia destes campos no data.json."""
    spec = rules.PILLAR_SCORING[pillar_id]
    return {k: spec[k] for k in ("id", "roman", "name", "metric", "fredSeries")}


def pillar_band_fields(pillar_id, scored_value):
    """A banda em que a leitura caiu e a distancia ao limiar seguinte na
    direccao do risco. E isto que permite ao site escrever a frase da caixa
    "Current Reading" sem ter numeros escritos a mao."""
    spec = rules.PILLAR_SCORING[pillar_id]
    band = rules.pillar_band(pillar_id, scored_value)
    if band is None:
        return {"band": None, "distanceToNextBand": None}
    edge = band["lo"] if spec["worseWhen"] == "lower" else band["hi"]
    return {
        "band": {k: band[k] for k in ("lo", "hi", "score", "label", "context", "reading")},
        "scoredValue": scored_value,
        "nextBandEdge": edge,
        "distanceToNextBand": (round(abs(scored_value - edge), 4)
                               if edge is not None and scored_value is not None else None),
    }


def load_previous_metrics(path=None):
    """{pilar: metricValue} da ultima publicacao.

    O caminho resolve-se AO LADO do data.json, nao a partir do directorio
    corrente. Enquanto foi relativo ao CWD, esta funcao era a unica leitura de
    estado do build_data que escapava ao isolamento: quem redirigisse o
    `__file__` para um temporario — como a suite faz — continuava a ler o
    data_prev.json do repositorio. E como o job da newsletter roda esse ficheiro
    em todas as sextas bem sucedidas, um teste que dependesse dele partia-se na
    segunda sexta publicada, sem que a dependencia aparecesse em lado nenhum do
    teste. Escrever a saida por `__file__` e ler a entrada pelo CWD e uma
    assimetria que nao tem defesa: agora sao os dois pelo mesmo sitio. Os deltas dos pilares eram
    ficcao: o do Cycle comparava com a constante 0.22, o do Premium com 1.20, e
    os da Solvency e do Debt eram as strings "+0.02" e "+0.3" escritas a mao —
    o site mostrava a mesma seta todas as semanas, e o "▼ NaN" da Liquidez vinha
    de um "—" que nao era numero. Passam a ser variacoes reais contra a ultima
    publicacao guardada em data_prev.json."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_prev.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        return {}, {}, None
    metrics = {p.get("id"): p.get("metricValue")
               for p in prev.get("pillars", []) if p.get("metricValue") is not None}
    scores = {p.get("id"): p.get("score")
              for p in prev.get("pillars", []) if p.get("score") is not None}
    return metrics, scores, (prev.get("meta") or {}).get("lastUpdated")


def metric_delta(pid, current, prev_metrics, unit="", digits=2):
    """(delta em texto, valor numerico ou None). Sem base de comparacao devolve
    um travessao — nunca um numero inventado."""
    prev = prev_metrics.get(pid)
    if current is None or prev is None:
        return "—", None
    d = round(float(current) - float(prev), digits)
    return f"{d:+.{digits}f}{unit}", d


def delta_str(current, previous, unit=""):
    if previous is None: return "—"
    diff = current - previous
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:.2f}{unit}"

# ──────────────────────────────────────────
# MAIN ENGINE
# ──────────────────────────────────────────
def build_data(hoje=None):
    """`hoje` existe para que a idade das observações possa ser fixada nos
    testes: sem ela, um teste com observações gravadas passa hoje e falha daqui
    a um mês, quando essas mesmas observações passarem do prazo."""
    print("\n🔄 US MRM — Fetching live FRED data...\n")
    # Estado de módulo: uma segunda corrida no mesmo processo (os testes) não
    # pode herdar as séries paradas da primeira.
    SERIES_STALE.clear()
    _ULTIMA_DATA.clear()
    _HOJE.clear()
    if hoje is not None:
        _HOJE["d"] = hoje

    # ── Fetch all series ──
    print("  📡 T10Y2Y  (Yield Curve Spread)...")
    t10y2y_val, t10y2y_date = latest_value("T10Y2Y", hoje=hoje)

    print("  📡 M2SL    (M2 Money Supply, billions)...")
    m2_val, m2_date = latest_value("M2SL", limit=3, hoje=hoje)

    print("  📡 M2SL YoY history (BDC Golden Rule filter)...")
    m2_hist_obs = fetch_fred("M2SL", limit=14)  # monthly series — 14 points covers just over a year
    m2_yoy_growth_pct = None
    if len(m2_hist_obs) >= 13:
        _latest_m2 = float(m2_hist_obs[0]["value"])
        _year_ago_m2 = float(m2_hist_obs[12]["value"])
        if _year_ago_m2:
            m2_yoy_growth_pct = round((_latest_m2 - _year_ago_m2) / _year_ago_m2 * 100, 2)

    print("  📡 Liquidity — Buffett Indicator (Total Corp. Equities / GDP, Fed Z.1 + BEA)...")
    buffett_ratio, buffett_pct, buffett_date, buffett_detail = fetch_liquidity_percentile(hoje)
    if buffett_ratio is not None:
        print(f"  ✅ Buffett Indicator: {buffett_ratio*100:.1f}% of GDP ({buffett_date}) — "
              f"{buffett_pct}th percentile of {buffett_detail.get('historyPoints')} quarters since {buffett_detail.get('historyStart')}")
    else:
        print("  [WARN] Liquidity pillar data unavailable this run.")

    print("  📡 DGS10   (10Y Treasury Yield)...")
    dgs10_val, dgs10_date = latest_value("DGS10", hoje=hoje)

    print("  📡 DRALACBN (Bank Delinquency Rate)...")
    npl_val, npl_date = latest_value("DRALACBN", limit=5, hoje=hoje)

    print("  📡 TDSP    (Household Debt Service Ratio)...")
    dsr_val, dsr_date = latest_value("TDSP", limit=5, hoje=hoje)

    print("  📡 ICSA    (Initial Jobless Claims)...")
    icsa_val, icsa_date = latest_value("ICSA", hoje=hoje)
    icsa_obs = fetch_fred("ICSA", limit=3)
    # `else icsa_val` era uma comparacao da leitura consigo propria disfarcada de
    # comparacao com a semana passada: produzia "+0K" e "stable" — uma variacao
    # que ninguem mediu, publicada ao lado do numero como se fosse informacao.
    # Sem segunda observacao nao ha anterior; o `_tendencia` ja sabe o que fazer
    # com isso. Isto tambem torna alcancavel o ramo `None` do helper, que de
    # outro modo era codigo morto: `latest_value` e este `fetch_fred` sao dois
    # pedidos HTTP independentes, e o segundo pode falhar sozinho.
    icsa_prev = float(icsa_obs[1]["value"]) if len(icsa_obs) > 1 else None

    print("  📡 UNRATE  (Unemployment Rate)...")
    unrate_val, unrate_date = latest_value("UNRATE", limit=3, hoje=hoje)
    unrate_obs = fetch_fred("UNRATE", limit=3)
    unrate_prev = float(unrate_obs[1]["value"]) if len(unrate_obs) > 1 else None

    # A sparkline passou a ler o score_history.json; esta chamada a FRED ficou
    # a ser feita e deitada fora todas as noites.

    # ── Medidor B: stress concorrente ──
    # Nao entra na media do Medidor A; e publicado a parte e sobrepoe-se a ele.
    print("  📡 Gauge B (stress: Sahm real-time + aceleracao da delinquencia)...")
    # history_values devolve do MAIS ANTIGO para o MAIS RECENTE (faz reverse() sobre
    # a resposta da FRED, que vem em sort_order=desc). O ultimo elemento e hoje.
    #
    # Isto estava trocado: `now` lia o mais antigo e `3m` o mais recente, o que
    # inverte o sinal da variacao. Uma descida de 32 bp — flight-to-quality
    # genuino, 2008 e 2020 — era lida como subida e caia em Critical_Stress; uma
    # subida de 32 bp — 2022 — era lida como descida e punha 35% em TLT no ano
    # em que o TLT caiu 31%. Exactamente ao contrario do que a porta assimetrica
    # existe para fazer.
    _d10_hist = history_values("DGS10", n=70, limit=95)   # ~3 meses de dias uteis
    _d10_now = _d10_hist[-1][0] if _d10_hist else None
    _d10_3m  = _d10_hist[0][0] if len(_d10_hist) >= 55 else None
    if _d10_now is not None and _d10_3m is not None:
        print(f"  🔟 10Y: {_d10_3m:.2f}% ({_d10_hist[0][1]}) -> {_d10_now:.2f}% "
              f"({_d10_hist[-1][1]}) = {(_d10_now - _d10_3m)*100:+.0f} bp em 3 meses")
    stress_gauge = mrm_gauge_b.compute(
        FRED_API_KEY, _d10_now, _d10_3m, hoje=hoje,
        dgs10_now_date=(_d10_hist[-1][1] if _d10_hist else None),
        dgs10_3m_date=(_d10_hist[0][1] if len(_d10_hist) >= 55 else None))
    print(f"  ✅ {stress_gauge['label']}  ({stress_gauge['basis']})")

    # ── ERP Calculation ──
    # ERP = Earnings Yield - 10Y Yield
    # Approximate earnings yield using S&P 500 P/E ~ 22 → E/P ≈ 4.55%
    # For production, fetch from a financial data provider
    # Here we compute from DGS10 and a fixed E/P estimate
    # O FRED nao publica earnings agregados do S&P 500. O E/P continua a ser uma
    # constante, mas deixa de ser apresentado como medicao: passa a ter data, a ser
    # declarado como estimativa no data.json, e a exigir actualizacao manual.
    # Na pratica o ERP publicado e "constante menos 10Y" — move-se so com o 10Y.
    print("  📡 SP500    (indice, para marcar o E/P a mercado)...")
    sp500_obs = fetch_fred("SP500", limit=400)          # ~18 meses de dias uteis
    ep_now, ep_detail = earnings_yield_now(SP500_EARNINGS_YIELD, SP500_EARNINGS_YIELD_ASOF, sp500_obs)
    ep_age = days_since(SP500_EARNINGS_YIELD_ASOF, hoje)
    ep_stale = ep_age is not None and ep_age > EP_STALE_AFTER_DAYS
    ep_detail["ageDays"] = ep_age
    ep_detail["stale"] = ep_stale
    if ep_stale:
        print(f"  [WARN] referencia do E/P tem {ep_age} dias (limite {EP_STALE_AFTER_DAYS}) — actualizar")
    print(f"  ✅ E/P: {ep_now}% ({ep_detail['basis']})")

    erp_val = round(ep_now - dgs10_val, 2) if dgs10_val is not None else None

    # ── Compute Scores ──
    print("\n  📊 Computing Pillar Scores...")
    s_cycle    = score_cycle(t10y2y_val)
    s_liquidity= score_liquidity(buffett_pct)
    s_premium  = score_premium(erp_val)
    # O MOTIVO de cada n/d, publicado. Enquanto o consumidor teve de adivinhar
    # porque e que um pilar estava em n/d, escrevia sempre a mesma frase — "a
    # serie subjacente nao publicou a tempo" — e com este limiar novo passou a
    # imputar a um fornecedor de dados uma falha que e interna. Qualquer motivo
    # novo produzia uma falsidade nova.
    nd_motivos = {}
    if ep_age is not None and ep_age > EP_ND_AFTER_DAYS:
        print(f"  [WARN] referencia do E/P tem {ep_age} dias (limite n/d "
              f"{EP_ND_AFTER_DAYS}) — o pilar Premium entra em n/d e sai do "
              f"composto. Actualizar SP500_EARNINGS_YIELD.")
        s_premium = None
        nd_motivos["premium"] = "stale-anchor"
        # E o NUMERO tambem: o site diz, a letra, que passado este prazo a
        # leitura "stops counting as a reading" e que "no number nobody has
        # checked is published as a measurement". Publicar o score em n/d e
        # continuar a publicar o ERP, a banda e a sentinela calculados sobre ele
        # e cumprir metade da afirmacao.
        erp_val = None
    s_solvency = score_solvency(npl_val)
    s_debt     = score_debt(dsr_val)

    scores = {
        "cycle": s_cycle,
        "liquidity": s_liquidity,
        "premium": s_premium,
        "solvency": s_solvency,
        "debt": s_debt
    }
    g_score, nd_pillars = global_score(scores)
    for _pid_nd in nd_pillars:
        nd_motivos.setdefault(_pid_nd, "series")
    if nd_pillars:
        print(f"  [WARN] pilares em n/d, excluidos do composto: {', '.join(nd_pillars)}")

    print(f"\n  ✅ Cycle:     {s_cycle} (T10Y2Y={t10y2y_val}%)")
    print(f"  ✅ Liquidity: {s_liquidity} (Buffett={f'{buffett_ratio*100:.1f}' if buffett_ratio is not None else 'N/A'}%, pctile={buffett_pct})")
    print(f"  ✅ Premium:   {s_premium} (ERP={erp_val}%, E/P {ep_now}% ancorado em {SP500_EARNINGS_YIELD}% a {SP500_EARNINGS_YIELD_ASOF})")
    print(f"  ✅ Solvency:  {s_solvency} (NPL={npl_val}%)")
    print(f"  ✅ Debt:      {s_debt} (DSR={dsr_val}%)")
    print(f"\n  🌐 GLOBAL RESILIENCE SCORE: {g_score} — {status_label(g_score)}")
    print(f"  🌡️  GAUGE B: {stress_gauge['label']}\n")

    # ── Historico do score ──
    # A versao anterior NAO era historico: misturava o score ACTUAL em cada ponto
    # passado (approx_s*0.3 + g_score*0.7), pelo que a sparkline era quase plana
    # por construcao. Passa a ler o historico reconstruido (260 meses, 2005-2026)
    # de score_history.json e a acrescentar-lhe o ponto corrente.
    _hist_path = os.path.join(os.path.dirname(__file__), "score_history.json")
    # O ficheiro E ACTUALIZADO, nao so lido.
    #
    # Ate aqui era estatico: 260 pontos a acabar em Ago '26, e nenhum workflow
    # lhe tocava. O grafico desenha os pontos EQUIDISTANTES (indice no eixo x),
    # portanto a distancia entre o ultimo ponto historico e o ponto corrente
    # crescia um mes por mes, desenhada como um passo mensal normal, sob um
    # titulo que dizia "24-Month History". Uma mentira que se agrava sozinha,
    # para sempre, e que nenhum teste via — o unico piso era `len(hs) == 24`.
    # A escrita do historico NAO pode derrubar o `build_data`: ele e o primeiro
    # job da sexta, e um disco cheio ou um ficheiro sem permissao deixaria o
    # data.json por escrever — e os dois jobs seguintes saltados — por causa de
    # um passo que nao e essencial para a decisao da semana.
    try:
        actualiza_score_history(g_score, hoje, _hist_path)
    except Exception as _e_hist:                                  # noqa: BLE001
        print(f"  [WARN] nao foi possivel gravar o score_history.json "
              f"({_e_hist}) — o ponto desta semana entra na mesma, em memoria")
    hist_scores = []
    if os.path.exists(_hist_path):
        try:
            with open(_hist_path) as _hf:
                _mes_corrida = (hoje or datetime.utcnow().date()).strftime("%Y-%m")
                hist_scores = [{"date": p["date"], "score": p["score"],
                                "month": p.get("month")}
                               for p in json.load(_hf)
                               if p.get("month") and p["month"] <= _mes_corrida][-24:]
        except Exception as _e:
            print(f"  [WARN] score_history.json ilegivel ({_e}) — sparkline so com o ponto corrente")
    _mes_agora = (hoje or datetime.utcnow().date()).strftime("%Y-%m")
    if g_score is not None and not any(h.get("month") == _mes_agora for h in hist_scores):
        # O historico nao pode ser gravado (disco read-only numa re-corrida
        # local, por exemplo): o ponto corrente entra na mesma, em memoria.
        # A data da corrida, nao a de quem a corre: numa re-corrida de uma
        # semana antiga o rotulo do ultimo ponto mentia.
        hist_scores.append({"date": (hoje or datetime.utcnow().date()).strftime("%b \'%y"),
                            "score": g_score, "month": _mes_agora})
        hist_scores = hist_scores[-24:]

    # Uma so regra de direccao para as tres sentinelas: sem par de leituras nao
    # ha direccao nenhuma, e zero e "stable", nao "sem leitura".
    def _tendencia(delta):
        if delta is None:
            return "nd"
        return "rising" if delta > 0 else ("falling" if delta < 0 else "stable")

    # ── ICSA Sentinel ──
    # `if (icsa_val and icsa_prev)` era verdade-por-truthiness: uma leitura de
    # zero (ou a ausencia dela) caia no mesmo ramo. E o `trend` dizia "nd"
    # sempre que a variacao fosse zero — ou seja, uma semana genuinamente
    # estavel publicava-se como "sem leitura". Aqui o estado sai da existencia
    # da leitura, e a direccao sai do sinal da variacao.
    _icsa_lido = icsa_val is not None
    _icsa_par = _icsa_lido and icsa_prev is not None
    icsa_display = f"{int(icsa_val/1000)}K" if _icsa_lido else "n/d"
    icsa_delta_val = (icsa_val - icsa_prev) if _icsa_par else 0
    icsa_delta_str = (f"{'+' if icsa_delta_val >= 0 else ''}"
                      f"{int(icsa_delta_val/1000)}K") if _icsa_par else "n/d"
    icsa_alert = icsa_val > 275000 if _icsa_lido else False
    icsa_status = ("alert" if icsa_alert else
                   ("caution" if (icsa_val is not None and icsa_val > 240000) else "normal")) \
                  if _icsa_lido else "nd"
    icsa_trend = _tendencia(icsa_delta_val if _icsa_par else None)

    # ── ERP Sentinel ──
    erp_alert = erp_val < 0.80 if erp_val is not None else False
    erp_status = ("alert" if erp_alert else ("caution" if erp_val < 1.20 else "normal")) if erp_val is not None else "nd"

    # ── UNRATE Sentinel ──
    # BDC "Stagflation Scenario" crisis trigger (see Watchlist/Crisis section):
    # threshold set at 5.2%, per the BDC macro-cycle framework.
    # A UNRATE era a unica das tres sentinelas sem estado de ausencia: sem
    # leitura publicava `status "normal"`, `displayValue "N/A"`, `trend
    # "stable"` e um `delta "+0.0%"` inventado — e o capitulo da Academia lia
    # isso e escrevia, a verde, que a taxa esta "abaixo do gatilho". Nao esta
    # nada: nao ha taxa. Passa a seguir o mesmo protocolo do ERP e da ICSA.
    _unrate_lido = unrate_val is not None
    _unrate_par = _unrate_lido and unrate_prev is not None
    unrate_delta_val = (unrate_val - unrate_prev) if _unrate_par else 0
    unrate_delta_str = (f"{'+' if unrate_delta_val >= 0 else ''}"
                        f"{unrate_delta_val:.1f}%") if _unrate_par else "n/d"
    unrate_alert = unrate_val >= 5.2 if _unrate_lido else False
    unrate_status = ("alert" if unrate_alert else
                     ("caution" if unrate_val >= 4.7 else "normal")) \
                    if _unrate_lido else "nd"
    unrate_trend = _tendencia(unrate_delta_val if _unrate_par else None)

    # ── Liquidity pillar display fields ──
    if buffett_ratio is not None:
        buffett_display = f"{buffett_ratio*100:.1f}%"
        liquidity_trend = "elevated" if buffett_pct > 65 else ("compressed" if buffett_pct < 35 else "normal")
        liquidity_desc = (
            f"Buffett Indicator (Total US Corporate Equities / GDP) at {buffett_ratio*100:.1f}% — "
            f"{buffett_pct}th percentile vs. its own history since {buffett_detail.get('historyStart')} "
            f"({buffett_detail.get('historyPoints')} quarters). Total corporate equities "
            f"${buffett_detail.get('totalEquitiesB', 0)/1000:.1f}T vs. GDP ${buffett_detail.get('gdpB', 0)/1000:.1f}T "
            f"(quarter ending {buffett_date}). M2 YoY growth: {f'{m2_yoy_growth_pct:+.1f}' if m2_yoy_growth_pct is not None else 'N/A'}%. "
            f"Updates quarterly with each Fed Z.1 / BEA GDP release, not weekly — a change from the prior "
            f"(non-functional) daily Wilshire proxy."
        )
    else:
        buffett_display = "N/A"
        liquidity_trend = "normal"
        liquidity_desc = "Buffett Indicator data unavailable this run (Fed Z.1 / BEA GDP fetch failed)."

    # ── Deltas reais contra a ultima publicacao ──
    buffett_val_pct = round(buffett_ratio * 100, 1) if buffett_ratio is not None else None
    prev_metrics, prev_scores, prev_stamp = load_previous_metrics()

    def delta_direction(pid, score, delta_text):
        """A cor da seta segue o SCORE do pilar, nao o sinal do numero: um ERP a
        descer e um numero negativo mas um agravamento. Sem numero para mostrar
        (delta "—") ou sem base de comparacao, devolve None e o site pinta neutro
        em vez de adivinhar."""
        prev = prev_scores.get(pid)
        if prev is None or score is None or delta_text == "—":
            return None
        if score > prev: return "worse"
        if score < prev: return "better"
        return "flat"

    d_cycle,    _ = metric_delta("cycle", t10y2y_val, prev_metrics, "%")
    d_liquidity, _ = metric_delta("liquidity", buffett_val_pct, prev_metrics, " pp", 1)
    d_premium,  _ = metric_delta("premium", erp_val, prev_metrics, "%")
    d_solvency, _ = metric_delta("solvency", npl_val, prev_metrics, " pp")
    d_debt,     _ = metric_delta("debt", dsr_val, prev_metrics, " pp", 1)
    delta_basis = f"vs. {prev_stamp[:10]}" if prev_stamp else "no prior publication"

    # ── Build JSON ──
    _generated_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    data = {
        "meta": {
            "lastUpdated": _generated_at,
            "generatedAt": _generated_at,
            "source": "FRED API (Live)",
            "version": "3.1.0",
            # Quem le este ficheiro nao tem de adivinhar se ele e de hoje.
            "freshness": {
                "generatedAt": _generated_at,
                "warnAfterHours": DATA_WARN_AFTER_HOURS,
                "refuseAfterHours": DATA_REFUSE_AFTER_HOURS,
                # Uma corrida em que faltaram series NAO e uma corrida normal,
                # mesmo que tenha produzido um ficheiro.
                "degraded": (bool(nd_pillars) or stress_gauge.get("active") is None
                             or bool(SERIES_STALE)),
                "staleSeries": sorted(SERIES_STALE),
                "ndPillars": list(nd_pillars),
                "ndReasons": {k: v for k, v in nd_motivos.items() if k in nd_pillars},
                "gaugeActive": stress_gauge.get("active"),
            },
            "fredSeriesDates": {
                "T10Y2Y": t10y2y_date,
                "M2SL": m2_date,
                "NCBEILQ027S_FBCELLQ027S_GDP": buffett_date,
                "DGS10": dgs10_date,
                "DRALACBN": npl_date,
                "TDSP": dsr_date,
                "ICSA": icsa_date,
                "UNRATE": unrate_date,
                # TODAS as series vigiadas, nao so as que dao pilares. O
                # consumidor le a data daqui para nao a ter de reconstruir a
                # partir da idade e do seu proprio relogio — e reconstruir da
                # coisa diferente numa re-corrida ou numa corrida que atravesse
                # a meia-noite UTC. Uma serie vigiada sem data aqui e uma serie
                # cujo aviso volta ao calculo frágil.
                **{_s: _d for _s, _d in _ULTIMA_DATA.items()
                   if _s not in ("T10Y2Y", "M2SL", "NCBEILQ027S_FBCELLQ027S_GDP",
                                 "DGS10", "DRALACBN", "TDSP", "ICSA", "UNRATE")}
            },
            # Séries cuja observação mais recente passou do prazo: o valor não
            # foi usado. Publicado para que a página e a newsletter o possam
            # dizer, em vez de mostrarem um n/d sem explicação.
            "fredSeriesStale": dict(SERIES_STALE),
            "gaugeNote": ("Gauge A (globalResilienceScore) is a LEADING fragility measure on a 6-18 month "
                              "horizon: in a downturn three of its five pillars mechanically improve, so the "
                              "composite cannot signal concurrent stress. Concurrent stress detection lives in "
                              "stressGauge, which is not averaged into Gauge A."),
            "deltaBasis": delta_basis,
            "liquidityNote": "Liquidity pillar redefined from discontinued Wilshire 5000 proxy to Buffett Indicator (Total Corp. Equities / GDP), percentile-scored against full history. See fetch_data.py comments."
        },
        "rules": rules.as_dict(),
        "globalResilienceScore": g_score,
        "status": status_label(g_score),
        "ndPillars": nd_pillars,
        "ndReasons": {k: v for k, v in nd_motivos.items() if k in nd_pillars},
        "stressGauge": stress_gauge,
        "pillars": [
            {
                **pillar_identity("cycle"),
                "score": s_cycle,
                "value": f"{t10y2y_val:+.2f}%" if t10y2y_val is not None else "n/d",
                "trend": "steepening" if (t10y2y_val or 0) > 0 else "inverted",
                "metricValue": t10y2y_val,
                **pillar_band_fields("cycle", t10y2y_val),
                "delta": d_cycle,
                "deltaDirection": delta_direction("cycle", s_cycle, d_cycle),
                "description": "Yield curve spread between 10Y and 2Y Treasuries. Normalizing from inversion historically precedes credit stress by 6–18 months.",
                "status": pillar_status(s_cycle)
            },
            {
                **pillar_identity("liquidity"),
                "score": s_liquidity,
                "value": buffett_display,
                "trend": liquidity_trend,
                "metricValue": buffett_val_pct,
                **pillar_band_fields("liquidity", buffett_pct),
                "delta": d_liquidity,
                "deltaDirection": delta_direction("liquidity", s_liquidity, d_liquidity),
                "m2YoyGrowthPct": m2_yoy_growth_pct,
                "percentileRank": buffett_pct,
                "description": liquidity_desc,
                "status": pillar_status(s_liquidity)
            },
            {
                **pillar_identity("premium"),
                "score": s_premium,
                "value": f"{erp_val:.2f}%" if erp_val is not None else "n/d",
                "epEstimated": True,
                "epValue": ep_now,
                "epAsOf": SP500_EARNINGS_YIELD_ASOF,
                "epAnchor": ep_detail,
                "trend": ("compressed" if erp_val < 2.0 else "adequate") if erp_val is not None else "nd",
                "metricValue": erp_val,
                **pillar_band_fields("premium", erp_val),
                "delta": d_premium,
                "deltaDirection": delta_direction("premium", s_premium, d_premium),
                # "marked to the latest S&P 500 close" era afirmado sempre —
                # inclusive quando o `earnings_yield_now` nao teve indice nenhum
                # e devolveu a referencia inalterada. Nessa semana o pilar nao
                # mexe com o mercado: mexe so com o 10Y, e o site dizia o
                # contrario a letra. A frase sai do `basis`, que e o campo que o
                # produtor ja escreve com a verdade, em vez de uma segunda copia
                # escrita a mao ao lado dele.
                "description": (
                    f"ERP = E/P ({ep_now}%) minus the 10Y yield "
                    f"({f'{dgs10_val:.2f}' if dgs10_val is not None else 'n/d'}%). Earnings are held from the "
                    f"{SP500_EARNINGS_YIELD_ASOF} reference ({SP500_EARNINGS_YIELD}% E/P) and "
                    + ("marked to the latest S&P 500 close, so the pillar moves "
                       "with both the market and the 10Y. "
                       if ep_detail.get("indexNow") is not None else
                       f"NOT marked to market this run ({ep_detail.get('basis')}), "
                       f"so the pillar moves with the 10Y alone until the index "
                       f"series returns. ")
                    + "Aggregate earnings are updated by hand each quarter"
                    + (f" — this reference is {ep_detail['ageDays']} days old and due for an update."
                       if ep_detail.get("stale") else ".")
                ) if erp_val is not None else (
                    "ERP n/d — the hand-set earnings reference is past its "
                    f"{EP_ND_AFTER_DAYS}-day expiry and the reading no longer "
                    "counts as a measurement."
                    if nd_motivos.get("premium") == "stale-anchor"
                    else "ERP n/d — DGS10 unavailable this run."),
                "status": pillar_status(s_premium)
            },
            {
                **pillar_identity("solvency"),
                "score": s_solvency,
                "value": f"{npl_val:.1f}%" if npl_val is not None else "n/d",
                "trend": ("stable" if s_solvency < 5 else "rising") if s_solvency is not None else "nd",
                "metricValue": npl_val,
                **pillar_band_fields("solvency", npl_val),
                "delta": d_solvency,
                "deltaDirection": delta_direction("solvency", s_solvency, d_solvency),
                "description": (f"FRED DRALACBN delinquency rate at {npl_val:.2f}%. Systemic banking plumbing "
                                f"{'functioning normally.' if s_solvency < 5 else 'showing stress.'}")
                               if npl_val is not None and s_solvency is not None
                               else "Delinquency rate n/d this run — pillar excluded from the composite.",
                "status": pillar_status(s_solvency)
            },
            {
                **pillar_identity("debt"),
                "score": s_debt,
                "value": f"{dsr_val:.1f}%" if dsr_val is not None else "n/d",
                "trend": ("rising" if s_debt > 5 else "stable") if s_debt is not None else "nd",
                "metricValue": dsr_val,
                **pillar_band_fields("debt", dsr_val),
                "delta": d_debt,
                "deltaDirection": delta_direction("debt", s_debt, d_debt),
                "description": (f"Household debt service ratio at {dsr_val:.1f}%. "
                                f"{'Consumer balance sheet strain increasing.' if s_debt > 5 else 'Consumer balance sheets healthy.'}")
                               if dsr_val is not None and s_debt is not None
                               else "Household DSR n/d this run — pillar excluded from the composite.",
                "status": pillar_status(s_debt)
            }
        ],
        "sentinels": [
            {
                "id": "jobless",
                "name": "Initial Jobless Claims",
                "fredSeries": "ICSA",
                "value": int(icsa_val) if icsa_val is not None else None,
                "unit": "claims",
                "displayValue": icsa_display,
                "threshold": 275000,
                "thresholdDisplay": "275K",
                "status": icsa_status,
                "trend": icsa_trend,
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
                "displayValue": f"{erp_val:.2f}%" if erp_val is not None else "n/d",
                "threshold": 0.8,
                "thresholdDisplay": "0.80%",
                "status": erp_status,
                "trend": ("falling" if erp_val < 1.5 else "stable") if erp_val is not None else "nd",
                "delta": d_premium,
                "alert": erp_alert,
                "description": (f"ERP at {erp_val:.2f}%. Red alert triggers below 0.80%."
                                if erp_val is not None else
                                ("ERP n/d — the hand-set earnings reference is "
                                 "past its expiry."
                                 if nd_motivos.get("premium") == "stale-anchor"
                                 else "ERP n/d this run."))
            },
            {
                "id": "unemployment",
                "name": "Unemployment Rate",
                "fredSeries": "UNRATE",
                "value": unrate_val,
                "unit": "%",
                "displayValue": f"{unrate_val:.1f}%" if _unrate_lido else "n/d",
                "threshold": 5.2,
                "thresholdDisplay": "5.2%",
                "status": unrate_status,
                "trend": unrate_trend,
                "delta": unrate_delta_str,
                "alert": unrate_alert,
                "description": (f"US unemployment rate at {unrate_val:.1f}%. "
                                f"BDC stagflation stress trigger activates at 5.2%."
                                if _unrate_lido else
                                "US unemployment rate n/d this run — no reading "
                                "from UNRATE. The 5.2% BDC stagflation trigger "
                                "cannot be evaluated without it.")
            }
        ],
        "historicalScores": hist_scores,
        # O rotulo sai do que esta DESENHADO, nao de um numero escrito a mao no
        # HTML. "24-Month History" sobre pontos que cobrem trinta meses e uma
        # afirmacao falsa na capa do produto, e era o que acontecia a medida que
        # o historico ficava para tras.
        "historicalScoresSpan": (
            f"{hist_scores[0]['date']} – {hist_scores[-1]['date']}"
            if hist_scores else "n/d"),
        "historicalScoresContiguo": _meses_contiguos(hist_scores)
    }

    # ── Write JSON ──
    # Escrita atomica: temporario no mesmo directorio, depois os.replace. Ou o
    # data.json antigo fica intacto, ou o novo fica completo — nunca um ficheiro
    # truncado a meio, que e o que o site e o motor da carteira iriam ler a
    # seguir se o runner morresse durante a escrita.
    output_path = os.path.join(os.path.dirname(__file__), "data.json")
    tmp_path = output_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, output_path)

    print(f"  💾 data.json saved → {output_path}")
    print(f"  🕐 Timestamp: {data['meta']['lastUpdated']}\n")
    return data

if __name__ == "__main__":
    build_data()
