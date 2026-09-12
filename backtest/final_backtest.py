"""
Backtest final de validacao — 2007-2026.

Diferenca essencial face aos backtests anteriores: a logica nao esta
reimplementada aqui. Este script importa mrm_rules.py do repositorio e chama
classify_regime, subregime_from_gauge, decide_rebalance e effective_bucket_alloc.
O que e testado e o codigo que esta em producao.
"""
import json, math, csv, sys, statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
sys.path.insert(0, str(HERE.parent))          # mrm_rules.py e mrm_gauge_b.py
import mrm_rules as rules

# ── precos mensais e substituicoes declaradas ────────────────────────────────
def load(p):
    px = {}
    for line in open(p):
        t = line.split()
        if len(t) == 3 and len(t[1]) == 2:
            px[f"{t[0]}-{t[1]}"] = float(t[2]); continue
        for i, v in enumerate(t[1:], 1):
            px[f"{t[0]}-{i:02d}"] = float(v)
    return px

PX = {t: load(DATA / "px" / f"{t}.txt") for t in ("SPY", "IEF", "LQD", "DBC", "SHV", "VNQ", "TLT", "GLD")}
for line in open(DATA / "px" / "RESIL2021.txt"):
    t = line.split(); PX.setdefault(t[0], {})
    for i in range(1, len(t), 2):
        PX[t[0]][t[i]] = float(t[i + 1])

# Nem todos os ETF de producao existem em 2007. As substituicoes sao declaradas,
# nao escondidas, e nenhuma delas favorece o sistema novo:
SUBSTITUTIONS = {
    "PDBC": "DBC",   # PDBC cotado desde 2014-02 -> DBC, mesmo cabaz, desde 2006-02
    "BIL":  "SHV",   # BIL desde 2007-05 -> SHV, bilhetes do Tesouro 0-1 ano
    "SGOV": "SHV",   # SGOV desde 2020-05 -> SHV
    "USMV": "SPY",   # USMV (baixa volatilidade) desde 2011-10 -> SPY, o indice.
                     # Penaliza o sistema novo: em Critical fica com beta total.
    "SHY":  "SHV",   # sem serie completa de SHY -> SHV, ainda mais curto
}
sub = lambda t: SUBSTITUTIONS.get(t, t)

# ── scores reconstruidos e series dos gatilhos ───────────────────────────────
SC = json.load(open(DATA / "scores.json"))
S = {r["month"]: r for r in SC}
MONTHS = [r["month"] for r in SC if r["month"] >= "2007-02"]
mm = {r["month"]: float(r["y10"]) for r in csv.DictReader(open(DATA / "monthly.csv"))}

# Serie real-time da regra de Sahm (vintages, nao revista). 2025-10 nao existe na
# serie publicada e e interpolado — assinalado aqui e nao escondido.
sahm = {}
for line in open(DATA / "sahm_realtime.txt"):
    if not line.strip(): continue
    a, b = line.split(); sahm[a] = float(b)
sahm["2025-10"] = round((sahm["2025-09"] + sahm["2025-11"]) / 2, 2)

q = {r["date"]: float(r["dralacbn"]) for r in csv.DictReader(open(DATA / "fred_quarterly.csv")) if r["dralacbn"]}
qk = sorted(q); d4 = {qk[i]: round(q[qk[i]] - q[qk[i - 4]], 2) for i in range(4, len(qk))}

# ── capital e custos de transaccao ──────────────────────────────────────────
#
# O backtest corria sobre um indice de 10.000 sem custos. Um custo fixo por
# transaccao nao e escalavel: 10 dolares sao 0,01% de 100 mil e 0,003% de 350
# mil, por isso o mesmo sistema paga proporcionalmente menos a medida que
# compoe. Sem um capital declarado a pergunta "quanto custam as transaccoes"
# nao tem resposta.
#
# O capital e composto: os ganhos e as perdas ficam no portfolio e sao
# reinvestidos no rebalanceamento seguinte, e os custos sao deduzidos do valor
# antes de se comprar, pelo que tambem compoem — negativamente.

INITIAL_CAPITAL = 100_000.0
# A carteira real tem $10.000, este backtest $100.000, e as comissoes sao FIXAS
# por posicao. Os $10 pesam dez vezes mais na carteira real do que aqui: os $400
# deste backtest sao 0,40% do capital, e a mesma sequencia de transaccoes na
# carteira real seria 4,0%. Publicar os dois valores em dolares lado a lado, como
# se fossem comparaveis, e o erro que este comentario existe para impedir — o
# numero comparavel e a PERCENTAGEM do capital, e e essa que se imprime.
# Importados de mrm_rules: a carteira real cobra os MESMOS. Enquanto viveram so
# aqui, o site publicava lado a lado um backtest com comissoes e uma carteira
# real sem custo nenhum, como se fossem comparaveis.
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
import mrm_rules as _rules
COST_OPEN  = _rules.COST_OPEN     # abrir uma posicao
COST_CLOSE = _rules.COST_CLOSE    # fechar uma posicao

# Uma posicao e um ticker. Dois buckets que partilhem instrumento (SHV serve
# CASH e US_TREASURIES em varios mapas) sao uma posicao so, porque no corretor
# sao uma linha so.
#
# Ha duas leituras honestas de "custo por posicao", e o backtest publica as
# duas em vez de escolher a que da melhor numero:
#
#   "open_close"  Literal. Paga-se ao abrir e ao fechar. Um rebalanceamento
#                 semestral que so mexe nos pesos dos mesmos seis instrumentos
#                 nao abre nem fecha nada, logo nao paga. E o limite inferior.
#
#   "every_trade" Realista. Qualquer linha tocada e uma ordem executada, e uma
#                 ordem tem comissao — incluindo o acerto de peso de um
#                 instrumento que ja se detinha. E o limite superior.
#
# A verdade de um corretor concreto esta entre as duas, mais perto de
# "every_trade" para quem paga por ordem e mais perto de "open_close" para quem
# tem ordens gratuitas e paga so custodia. O sistema tem de aguentar a pior.
COST_MODELS = ("open_close", "every_trade")

# Uma comissao fixa e o custo mais visivel e o menos importante. O que pesa numa
# carteira desta dimensao e o custo PROPORCIONAL: o spread entre compra e venda,
# e o deslize do preco entre a decisao e a execucao. Dez dolares sao dez dolares
# quer se negoceiem mil ou cem mil; cinco pontos base de 60 mil dolares
# transaccionados sao trinta dolares, e o rebalanceamento seguinte volta a
# paga-los.
#
# Publicar so a comissao fixa daria a impressao de que os custos estao
# resolvidos. Nao estao — apenas a parte pequena esta. SLIPPAGE_BP cobra pontos
# base sobre o valor efectivamente transaccionado (a soma dos modulos das
# variacoes de posicao), que e a forma certa de o modelar: um rebalanceamento
# que mexe pouco paga pouco.
SLIPPAGE_BP = 0.0            # base: so a comissao fixa que foi pedida
SLIPPAGE_SCENARIOS = (0.0, 5.0, 10.0, 20.0)


def traded_value(old_shares, new_shares, m):
    """Valor absoluto transaccionado, em dolares."""
    old_shares = old_shares or {}
    tickers = set(old_shares) | set(new_shares)
    return sum(abs(new_shares.get(t, 0.0) - old_shares.get(t, 0.0)) * PX[t][m] for t in tickers)


def trade_cost(old_shares, new_shares, model="open_close", m=None, slippage_bp=None):
    """Custo de passar de uma carteira a outra, e as linhas que o justificam.

    A comissao fixa vem de `mrm_rules.trade_cost` — a MESMA funcao que a carteira
    real usa. Aqui vivia uma copia, com a mesma regra escrita duas vezes: era
    exactamente o defeito que a canonicalizacao existia para eliminar. O que este
    ficheiro acrescenta e o slippage, que so o backtest modela (a carteira real
    ainda nao o cobra, e isso e declarado no README).
    """
    old_shares = old_shares or {}
    cost, detalhe = _rules.trade_cost(old_shares, new_shares, model)
    opened, closed = detalhe["opened"], detalhe["closed"]
    adjusted = detalhe["adjusted"]
    bp = SLIPPAGE_BP if slippage_bp is None else slippage_bp
    slip = 0.0
    if bp and m is not None:
        slip = traded_value(old_shares, new_shares, m) * bp / 10_000
        cost += slip
    return cost, {"opened": opened, "closed": closed, "adjusted": adjusted,
                  "fixed": cost - slip, "slippage": slip}


def madd(m, n):
    y, mo = int(m[:4]), int(m[5:7]); mo += n; y += (mo - 1) // 12; mo = (mo - 1) % 12 + 1
    return f"{y:04d}-{mo:02d}"

def npl_accel(m, lag=5):
    ok = [d for d in sorted(d4) if madd(d[:7], lag) <= m]
    return d4[ok[-1]] if ok else None

from mrm_gauge_b import SAHM_TRIGGER, NPL_ACCEL_TRIGGER, TENY_FTQ_BP

def gauge_b(m):
    """Os dois gatilhos publicados, com os limiares do modulo de producao."""
    s = sahm.get(madd(m, -1))                      # relatorio do emprego sai em M+1
    n = npl_accel(m)
    if s is None and n is None:
        return None
    return (s is not None and s >= SAHM_TRIGGER) or (n is not None and n >= NPL_ACCEL_TRIGGER)

def gauge_b_subregime(m):
    a, b = mm.get(m), mm.get(madd(m, -3))
    if a is None or b is None:
        return None
    return "FTQ" if (a - b) <= TENY_FTQ_BP else "STRESS"

# ── alocacao por regime ─────────────────────────────────────────────────────
# Estes vectores estavam AQUI, escritos a mao, enquanto o sistema vivo executava
# a tabela da newsletter: o backtest publicava 6,56% de CAGR sobre uma carteira
# que a producao nunca correu, e ninguem podia dar por isso porque a constante
# existia em dois sitios. Agora ha um sitio so — `rules.REGIME_WEIGHTS` — e este
# ficheiro le de la. Se os pesos mudarem, mudam nos dois ao mesmo tempo ou nao
# mudam.
MACRO_ALLOC     = dict(rules.REGIME_WEIGHTS["Turbulence"])
RESILIENT_ALLOC = dict(rules.REGIME_WEIGHTS["Resilient"])

def alloc_for(regime, subregime):
    a, _ = rules.effective_bucket_alloc(regime, subregime)
    return a

def value(shares, m):
    return sum(n * PX[t][m] for t, n in shares.items())

def target_shares(v, regime, subregime, m):
    key = rules.resolve_etf_map_key(regime, subregime)
    etfs = rules.REGIME_ETF_MAP[key]
    w = alloc_for(regime, subregime)
    sh = {}
    for bucket, pct in w.items():
        t = sub(etfs[bucket])
        sh[t] = sh.get(t, 0.0) + v * pct / 100 / PX[t][m]   # buckets partilham ticker: somar
    return sh


def rebalance(v, regime, subregime, m, old_shares=None, model=None, slippage_bp=None):
    """Rebalanceia e devolve (accoes, custo). O custo sai do valor ANTES de se
    comprar: paga-se a corretagem com dinheiro do portfolio, nao com dinheiro
    que aparece de fora."""
    if model is None:
        return target_shares(v, regime, subregime, m)      # sem custos, para comparacao
    provisional = target_shares(v, regime, subregime, m)
    cost, _ = trade_cost(old_shares, provisional, model, m, slippage_bp)
    return target_shares(v - cost, regime, subregime, m), cost

def is_semestral(m):
    return m[5:7] in ("01", "06")

# ── v2: o sistema tal como esta em producao ─────────────────────────────────
# O v2 usa o score com E/P marcado a mercado (`score_real`) porque e isso que a
# producao passou a fazer em Set 2026: os earnings ficam ancorados numa referencia
# trimestral e o preco e marcado ao fecho diario. O v1 usa `score_frozen`, o E/P
# congelado do sistema anterior. A comparacao e, por isso, antes contra depois —
# incluindo esta diferenca, e nao apesar dela.
V2_SCORE_KEY = "score_real"
V1_SCORE_KEY = "score_frozen"


def run_v2(model=None, capital=INITIAL_CAPITAL, slippage_bp=None):
    """model=None corre sem custos (comparacao); "open_close" ou "every_trade"
    aplicam a corretagem. Devolve (serie, log, ledger)."""
    m0 = MONTHS[0]
    regime, subregime = "Turbulence", None
    sh, c0 = _first(capital, regime, subregime, m0, model, slippage_bp)
    ledger = {"total": c0, "n_rebal": 1 if model else 0, "events": ([(m0, "abertura inicial", c0)] if model else [])}
    ser = [(m0, capital - c0)]; log = []; low_streak = 0
    for m in MONTHS[1:]:
        v = value(sh, m)
        score = S[m][V2_SCORE_KEY]
        stress = gauge_b(m)
        want = rules.classify_regime(score, stress, regime)

        want_sub = None
        if want == "Critical":
            want_sub, _ = rules.subregime_from_gauge(gauge_b_subregime(m), regime == "Critical")

        low_streak = low_streak + 1 if (score is not None and score <= rules.RESILIENT_MAX) else 0
        # A producao calcula a emergencia sem olhar ao regime sinalizado — e
        # `decide_rebalance` que a ignora dentro de Critical. O backtest
        # condicionava a `want == "Resilient"`, o que o protegia de um defeito
        # que a producao tinha: mesma classe de divergencia que a janela do 10Y.
        # Agora as duas correm o mesmo caminho.
        emergency = (f"emergency_resilient_{score}"
                     if low_streak >= rules.CONSECUTIVE_WEEKS else None)

        # A confirmacao de duas leituras para entrar em Resilient e aplicada
        # com a mesma funcao que a producao usa. Sem isto, o backtest validava
        # uma regra que a producao nao executa — a mesma classe de divergencia
        # que a janela do 10Y tinha.
        want = rules.confirm_regime(want, regime, emergency)
        if want != "Critical":
            want_sub = None

        reason = rules.decide_rebalance(want, regime, want_sub, subregime, is_semestral(m), emergency)
        if reason:
            if want != regime or want_sub != subregime:
                log.append((m, f"{regime}{'/' + subregime if subregime else ''} -> "
                               f"{want}{'/' + want_sub if want_sub else ''}  [{reason}]"))
            regime = want
            subregime = want_sub if want == "Critical" else None
            if model:
                sh, c = rebalance(v, regime, subregime, m, sh, model, slippage_bp)
                ledger["total"] += c; ledger["n_rebal"] += 1
                if c: ledger["events"].append((m, reason, c))
                v -= c
            else:
                sh = rebalance(v, regime, subregime, m)
        ser.append((m, v))
    return ser, log, ledger


def _first(capital, regime, subregime, m0, model, slippage_bp=None):
    """A compra inicial tambem e uma transaccao: seis posicoes abertas."""
    if not model:
        return rebalance(capital, regime, subregime, m0), 0.0
    return rebalance(capital, regime, subregime, m0, None, model, slippage_bp)

# ── v1: o sistema anterior — regime pelo score, confirmacao de 2 meses ──────
def run_v1(model=None, capital=INITIAL_CAPITAL, slippage_bp=None):
    """O sistema anterior paga a mesma tabela de custos. Comparar um com
    corretagem e outro sem seria falsear a diferenca."""
    m0 = MONTHS[0]
    cur = "Turbulence"
    sh, c0 = _first(capital, cur, None, m0, model, slippage_bp)
    ledger = {"total": c0, "n_rebal": 1 if model else 0, "events": []}
    ser = [(m0, capital - c0)]; pend = None; streak = 0
    for m in MONTHS[1:]:
        v = value(sh, m)
        want = S[m]["regime_" + V1_SCORE_KEY.split("_")[1]]
        if want != cur:
            streak = streak + 1 if want == pend else 1; pend = want
        else:
            streak = 0; pend = None
        do = streak >= rules.CONSECUTIVE_WEEKS or is_semestral(m)
        if streak >= rules.CONSECUTIVE_WEEKS:
            cur = want; streak = 0; pend = None
        if do:
            if model:
                sh, c = rebalance(v, cur, None if cur != "Critical" else "Critical_Stress", m, sh, model, slippage_bp)
                ledger["total"] += c; ledger["n_rebal"] += 1
                v -= c
            else:
                sh = rebalance(v, cur, None if cur != "Critical" else "Critical_Stress", m)
        ser.append((m, v))
    return ser, [], ledger

def buy_hold(w, capital=INITIAL_CAPITAL):
    sh = {t: capital * x / PX[t][MONTHS[0]] for t, x in w.items()}
    return [(m, sum(n * PX[t][m] for t, n in sh.items())) for m in MONTHS]

def annual_rebal(w, capital=INITIAL_CAPITAL):
    v = capital; sh = {t: v * x / PX[t][MONTHS[0]] for t, x in w.items()}; out = [(MONTHS[0], v)]
    for m in MONTHS[1:]:
        v = sum(n * PX[t][m] for t, n in sh.items()); out.append((m, v))
        if m[5:7] == "01":
            sh = {t: v * x / PX[t][m] for t, x in w.items()}
    return out

shv = [PX["SHV"][MONTHS[i]] / PX["SHV"][MONTHS[i - 1]] - 1 for i in range(1, len(MONTHS))]

def stats(ser):
    v = [x for _, x in ser]; r = [v[i] / v[i - 1] - 1 for i in range(1, len(v))]
    yrs = len(r) / 12
    cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    vol = st.stdev(r) * math.sqrt(12)
    ex = [r[i] - shv[i] for i in range(len(r))]
    sharpe = (st.mean(ex) * 12) / vol
    # Downside deviation pela definicao padrao: raiz da media dos quadrados dos
    # retornos abaixo do alvo sobre TODOS os periodos, nao o desvio-padrao da
    # sub-amostra negativa. O que aqui estava era o segundo, que subestimava o
    # Sortino em cerca de 0,12 — de forma igual em todas as estrategias, por isso
    # a comparacao mantinha-se, mas o numero nao era o que o nome dizia.
    dd = math.sqrt(sum(min(x, 0.0) ** 2 for x in ex) / len(ex)) * math.sqrt(12)
    sortino = (st.mean(ex) * 12) / dd if dd else float("inf")
    pk = v[0]; mdd = 0; mdd_m = None
    for m, x in ser:
        pk = max(pk, x)
        if x / pk - 1 < mdd: mdd = x / pk - 1; mdd_m = m
    return dict(cagr=cagr, vol=vol, sharpe=sharpe, sortino=sortino, mdd=mdd, mdd_m=mdd_m, final=v[-1])

if __name__ == "__main__":
    print(f"Periodo: {MONTHS[0]} a {MONTHS[-1]}  ({len(MONTHS)} meses)")
    print(f"Capital inicial: {INITIAL_CAPITAL:,.0f} USD, composto")
    print(f"Corretagem: {COST_OPEN:.0f} USD por abertura, {COST_CLOSE:.0f} USD por fecho de posicao")
    print(f"Gatilhos: Sahm >= {SAHM_TRIGGER} | delinquencia 4T >= {NPL_ACCEL_TRIGGER} pp | FTQ: 10Y <= {TENY_FTQ_BP} em 3 meses\n")

    v2_free, log, _ = run_v2()
    v1_free, _, _   = run_v1()

    print(f"Mudancas de estado no v2 ({len(log)}):")
    for m, t in log: print(f"   {m}  {t}")

    runs = {}
    for model in COST_MODELS:
        runs[model] = {"v2": run_v2(model), "v1": run_v1(model)}

    # ── quadro principal, com o modelo literal pedido ────────────────────────
    base = runs["open_close"]
    rows = [("v1 — sistema anterior", stats(base["v1"][0])),
            ("v2 — sistema actual",   stats(base["v2"][0])),
            ("SPY buy & hold",        stats(buy_hold({"SPY": 1.0}))),
            ("60/40 SPY-IEF anual",   stats(annual_rebal({"SPY": 0.6, "IEF": 0.4})))]
    print("\nCom corretagem, modelo 'open_close' (abertura e fecho de posicao):")
    print(f"{'':24}{'CAGR':>8}{'Vol':>8}{'Sharpe':>8}{'Sortino':>9}{'MaxDD':>9}{'(mes)':>9}{'Final':>12}")
    for n, s in rows:
        print(f"{n:24}{s['cagr']*100:7.2f}%{s['vol']*100:7.2f}%{s['sharpe']:8.3f}{s['sortino']:9.3f}"
              f"{s['mdd']*100:8.1f}%{str(s['mdd_m']):>9}{s['final']:12,.0f}")

    # ── o que a corretagem custa, nos dois modelos ───────────────────────────
    # A coluna "% cap." e a que se pode comparar com a carteira real: as
    # comissoes sao fixas por posicao, e $10 pesam dez vezes mais numa carteira
    # de $10.000 do que nesta de $100.000.
    print(f"\n{'':24}{'rebal.':>8}{'custo total':>14}{'% cap.':>9}{'CAGR':>9}{'vs sem custos':>15}")
    s2f = stats(v2_free); s1f = stats(v1_free)
    print(f"{'v2 sem custos':24}{'—':>8}{'—':>14}{'—':>9}{s2f['cagr']*100:8.2f}%{'—':>15}")
    for model in COST_MODELS:
        ser, _, led = runs[model]["v2"]
        s = stats(ser)
        print(f"{'v2 · ' + model:24}{led['n_rebal']:8d}{led['total']:14,.0f}"
              f"{led['total']/INITIAL_CAPITAL*100:8.2f}%{s['cagr']*100:8.2f}%"
              f"{(s['cagr']-s2f['cagr'])*100:14.2f}pp")
    print(f"{'v1 sem custos':24}{'—':>8}{'—':>14}{'—':>9}{s1f['cagr']*100:8.2f}%{'—':>15}")
    for model in COST_MODELS:
        ser, _, led = runs[model]["v1"]
        s = stats(ser)
        print(f"{'v1 · ' + model:24}{led['n_rebal']:8d}{led['total']:14,.0f}"
              f"{led['total']/INITIAL_CAPITAL*100:8.2f}%{s['cagr']*100:8.2f}%"
              f"{(s['cagr']-s1f['cagr'])*100:14.2f}pp")

    print("\nCusto do seguro (v1 - v2), em pp de CAGR:")
    print(f"   sem custos            {(s1f['cagr']-s2f['cagr'])*100:5.2f} pp")
    for model in COST_MODELS:
        a = stats(runs[model]["v1"][0])["cagr"]; b = stats(runs[model]["v2"][0])["cagr"]
        print(f"   {model:22}{(a-b)*100:5.2f} pp")

    # ── as transaccoes que a corretagem paga no v2 ───────────────────────────
    print("\nTransaccoes do v2 que abrem ou fecham posicoes (modelo 'open_close'):")
    for m, reason, c in runs["open_close"]["v2"][2]["events"]:
        print(f"   {m}  {c:6,.0f} USD   {reason}")

    # ── o que realmente pesa: o custo proporcional ───────────────────────────
    # A comissao fixa e o custo mais visivel e o menos importante. Estes pontos
    # base sobre o valor transaccionado sao spread mais deslize de execucao, e
    # e aqui que um sistema que rebalanceia frequentemente perde dinheiro.
    print("\nSensibilidade ao custo proporcional (spread + deslize), sobre 'every_trade':")
    print(f"{'bp':>6}{'v2 CAGR':>10}{'v2 custo':>12}{'v1 CAGR':>10}{'v1 custo':>12}{'seguro':>10}")
    for bp in SLIPPAGE_SCENARIOS:
        s2, _, l2 = run_v2("every_trade", slippage_bp=bp)
        s1, _, l1 = run_v1("every_trade", slippage_bp=bp)
        a, b = stats(s1), stats(s2)
        print(f"{bp:6.0f}{b['cagr']*100:9.2f}%{l2['total']:12,.0f}{a['cagr']*100:9.2f}%"
              f"{l1['total']:12,.0f}{(a['cagr']-b['cagr'])*100:9.2f}pp")

    # ── ano a ano, ja com custos ─────────────────────────────────────────────
    d1, d2 = dict(base["v1"][0]), dict(base["v2"][0]); dspy = dict(buy_hold({"SPY": 1.0}))
    print(f"\n{'ano':>6}{'v1':>9}{'v2':>9}{'SPY':>9}   medidor B")
    prev = MONTHS[0]
    for y in range(2007, 2027):
        ms = [m for m in MONTHS if m[:4] == str(y)]
        if not ms: continue
        f = lambda d: d[ms[-1]] / d[prev] - 1
        nb = sum(1 for m in ms if gauge_b(m))
        print(f"{y:>6}{f(d1)*100:8.1f}%{f(d2)*100:8.1f}%{f(dspy)*100:8.1f}%   {'—' if nb == 0 else f'ON {nb}/{len(ms)} meses'}")
        prev = ms[-1]
