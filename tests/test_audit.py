"""
Regressoes dos defeitos encontrados na auditoria de 8 de Setembro de 2026.

Quatro defeitos, todos capazes de tomar uma decisao de carteira errada sem
levantar um erro. Nenhum deles fazia falhar um unico teste antes de existir este
ficheiro — e essa e a parte que interessa reter.

Sem rede.
"""
import sys, types, os, logging, math
from pathlib import Path

sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
os.environ.setdefault("FRED_API_KEY", "test-key-not-used")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fetch_data, mrm_gauge_b, mrm_rules as rules
import update_portfolio as up
import fixture_obs as fx

# ── a semana simulada sai do repositorio, nao do calendario ────────────────
#
# Este ficheiro e portao dos dois jobs de sexta-feira. Enquanto fixou a sexta em
# 2026-09-11 e a edicao em 27, dependia de duas coisas que mudam sozinhas: o
# relogio (as observacoes gravadas caducam contra os prazos do data_freshness) e
# o portfolio.json commitado (que avanca uma edicao todas as sextas e traz
# consigo o regime detido nessa semana). Qualquer das duas partia o portao — e
# um portao partido nao da um teste vermelho na segunda-feira: da uma sexta sem
# carteira e sem newsletter. A sexta ensaiada passa a ser a semana a seguir a
# ultima que o portfolio.json fechou, e as observacoes sao datadas a contar
# dela.
from datetime import date as _date_topo, timedelta as _td_topo

SEXTA, ANTERIOR, ISSUE = fx.semana_ensaiada(ROOT, rules)
D_SEXTA, D_ANTERIOR = SEXTA.isoformat(), ANTERIOR.isoformat()

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1
def true(c, what): eq(bool(c), True, what)

# ═══════════════════════════════════════════════════════════════════════════
# 1. A janela do 10Y estava invertida — o sinal FTQ/Stress corria ao contrario
#
# history_values devolve do mais antigo para o mais recente. O codigo lia
# _d10_now = hist[0] (o mais ANTIGO) e _d10_3m = hist[-1] (o mais RECENTE),
# invertendo o sinal da variacao. Consequencia: num Critical com taxas a subir
# — 2022, o ano em que o TLT caiu 31% — o sistema poria 35% em TLT.
# ═══════════════════════════════════════════════════════════════════════════
def hist_from(obs):
    """Simula history_values sobre uma resposta da FRED (sort_order=desc)."""
    guardado = fetch_data.fetch_fred
    fetch_data.fetch_fred = lambda sid, limit=12, retries=3, backoff=5: obs
    try:
        return fetch_data.history_values("DGS10", n=70, limit=95)
    finally:
        fetch_data.fetch_fred = guardado

DESCIDA = [{"date": "2026-09-02", "value": "4.15"},   # hoje
           {"date": "2026-08-01", "value": "4.30"},
           {"date": "2026-06-02", "value": "4.47"}]   # ha 3 meses
SUBIDA  = [{"date": "2026-09-02", "value": "4.47"},
           {"date": "2026-08-01", "value": "4.30"},
           {"date": "2026-06-02", "value": "4.15"}]

h = hist_from(DESCIDA)
eq(h[0][1], "2026-06-02", "history_values devolve o mais antigo primeiro")
eq(h[-1][1], "2026-09-02", "e o mais recente por ultimo")
# a leitura correcta: now = ultimo, ha 3 meses = primeiro
now, m3 = h[-1][0], h[0][0]
eq(round(now - m3, 2), -0.32, "descida de 32 bp le-se como negativa")
true((now - m3) <= mrm_gauge_b.TENY_FTQ_BP, "uma descida de 32 bp confirma FTQ")

h = hist_from(SUBIDA)
now, m3 = h[-1][0], h[0][0]
eq(round(now - m3, 2), 0.32, "subida de 32 bp le-se como positiva")
true(not ((now - m3) <= mrm_gauge_b.TENY_FTQ_BP),
     "uma subida de 32 bp NAO pode confirmar FTQ — era isto que punha 35% em TLT em 2022")

# e o codigo de producao le mesmo pelos indices certos
fonte = (ROOT / "fetch_data.py").read_text(encoding="utf-8")
true("_d10_now = _d10_hist[-1][0]" in fonte, "_d10_now le o mais recente")
true("_d10_3m  = _d10_hist[0][0]" in fonte, "_d10_3m le o mais antigo")

# ═══════════════════════════════════════════════════════════════════════════
# 2. Um gatilho em falta era tratado como gatilho desligado
#
# Com a serie de Sahm indisponivel e a delinquencia quieta, o medidor declarava
# "calmo". Se o sistema estivesse em Critical por causa da Sahm, a carteira
# liquidava a posicao defensiva por causa de uma falha de rede.
# ═══════════════════════════════════════════════════════════════════════════
class Resp:
    def __init__(s, obs): s.obs = obs
    def raise_for_status(s): pass
    def json(s): return {"observations": s.obs}

def gauge(sahm_obs, npl_obs, d10=(None, None)):
    guardado = mrm_gauge_b.requests.get
    mrm_gauge_b.requests.get = lambda url, params=None, timeout=None: Resp(
        sahm_obs if (params or {}).get("series_id") == "SAHMREALTIME" else npl_obs)
    try:
        return mrm_gauge_b.compute("k", d10[0], d10[1], hoje=SEXTA)
    finally:
        mrm_gauge_b.requests.get = guardado

NPL_CALMO = fx.npl(SEXTA, *["1.40"] * 5)
SAHM_CALMO = fx.sahm(SEXTA, "0.20")
SAHM_QUENTE = fx.sahm(SEXTA, "0.62")

# E as observacoes do fixture estao dentro do prazo nesta semana e daqui a um
# ano — sem isto, o medidor passa a n/d por caducidade e o portao fecha-se.
fx.verifica_frescura(SEXTA, eq)

eq(gauge(SAHM_CALMO, NPL_CALMO)["active"], False, "dois gatilhos avaliados e quietos: calmo")
eq(gauge(SAHM_QUENTE, NPL_CALMO)["active"], True, "um gatilho disparado basta para stress")
eq(gauge([], NPL_CALMO)["active"], None,
   "Sahm em falta com a delinquencia quieta e n/d, nao calma")
eq(gauge(SAHM_CALMO, [])["active"], None,
   "delinquencia em falta com a Sahm quieta e n/d, nao calma")
eq(gauge([], [])["active"], None, "as duas em falta continua n/d")
# um gatilho presente e disparado vale mesmo com o outro em falta
eq(gauge(SAHM_QUENTE, [])["active"], True,
   "um sinal presente nao precisa do outro para valer")
true("n/d" in gauge([], NPL_CALMO)["basis"], "o n/d parcial e declarado no basis")

# e o n/d propaga-se ate a decisao: o regime anterior mantem-se
eq(rules.classify_regime(6.9, None, "Critical"), "Critical",
   "n/d mantem Critical em vez de o desfazer")
eq(rules.decide_rebalance("Critical", "Critical", "Critical_Stress", "Critical_Stress", False),
   None, "e sem mudanca de estado nao ha rebalanceamento")

# ═══════════════════════════════════════════════════════════════════════════
# 3. Um preco em falta apagava o dinheiro desse bucket em silencio
#
# Ao entrar em Critical o TLT e um ticker novo e vale 35%. A guarda a jusante
# so abortava abaixo de 50% do valor, por isso 35% evaporavam-se.
# ═══════════════════════════════════════════════════════════════════════════
PRECOS = {"USMV": 90.0, "TLT": 95.0, "SHY": 82.0, "SGOV": 100.5,
          "GLD": 250.0, "BIL": 91.5, "VNQ": 88.0, "SHV": 110.0}
pesos = rules.CRITICAL_WEIGHTS["Critical_FTQ"]
logging.disable(logging.ERROR)
sh = up.rebalance_shares(100_000.0, pesos, "Critical_FTQ", dict(PRECOS))
valor = sum(n * PRECOS[t] for t, n in sh.items())
true(abs(valor - 100_000.0) < 1.0, "com todos os precos, investe-se o valor todo")

sem_tlt = {k: v for k, v in PRECOS.items() if k != "TLT"}
falhou = False
try:
    up.rebalance_shares(100_000.0, pesos, "Critical_FTQ", sem_tlt)
except ValueError as e:
    falhou = True
    true("abortado" in str(e), "o erro diz que o rebalanceamento foi abortado")
    true("65.0%" in str(e) and "100.0%" in str(e),
         "e diz quanto conseguiu colocar de quanto foi pedido")
logging.disable(logging.NOTSET)
true(falhou, "sem preco do TLT (35% da carteira) o rebalanceamento tem de abortar")

# ═══════════════════════════════════════════════════════════════════════════
# 4. validate_allocation aceitava uma alocacao a que faltava um bucket
# ═══════════════════════════════════════════════════════════════════════════
completa = {"US_EQUITIES": 20.0, "US_TREASURIES": 25.0, "IG_CREDIT": 15.0,
            "COMMODITIES": 12.0, "CASH": 20.0, "ALTERNATIVES": 8.0}
eq(rules.validate_allocation(completa), (True, []), "alocacao completa passa")
incompleta = {k: v for k, v in completa.items() if k != "ALTERNATIVES"}
incompleta["US_EQUITIES"] = 28.0            # total volta aos 100
eq(sum(incompleta.values()), 100.0, "o total fecha, so falta o bucket")
okk, probs = rules.validate_allocation(incompleta)
eq(okk, False, "mas falta ALTERNATIVES, logo reprova")
true(any("missing bucket" in p for p in probs), "e o problema diz qual falta")

# ── o parser: duas linhas nao podem colapsar num bucket e esvaziar outro ────
eq(up.map_asset_class("Short-Duration Sovereigns")[0], "CASH",
   "'Short-Duration Sovereigns' e dinheiro, nao duracao")
eq(up.map_asset_class("Intermediate Treasuries")[0], "US_TREASURIES",
   "'Intermediate Treasuries' continua a ser duracao")
eq(up.map_asset_class("Sovereign Bonds")[0], "US_TREASURIES",
   "'Sovereign' sozinho continua a mapear para duracao")

# ═══════════════════════════════════════════════════════════════════════════
# 5. build_data() degrada em vez de rebentar quando a FRED cai
#
# Antes: TypeError numa comparacao com None, o passo de commit era saltado, e o
# data.json de ontem ficava no sitio para a carteira decidir sobre ele as 22:00.
# ═══════════════════════════════════════════════════════════════════════════
import io, contextlib, tempfile, shutil, json

def build_with(fetch, liquidity, gauge_obs):
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "score_history.json", tmp)
    guardados = (fetch_data.fetch_fred, fetch_data.fetch_liquidity_percentile,
                 mrm_gauge_b.requests.get, fetch_data.__file__)
    class R:
        def raise_for_status(s): pass
        def json(s): return {"observations": gauge_obs}
    fetch_data.fetch_fred = fetch
    fetch_data.fetch_liquidity_percentile = liquidity
    mrm_gauge_b.requests.get = lambda url, params=None, timeout=None: R()
    cwd = os.getcwd(); os.chdir(tmp)
    fetch_data.__file__ = str(tmp / "fetch_data.py")
    logging.disable(logging.INFO)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return fetch_data.build_data()
    finally:
        logging.disable(logging.NOTSET)
        os.chdir(cwd)
        (fetch_data.fetch_fred, fetch_data.fetch_liquidity_percentile,
         mrm_gauge_b.requests.get, fetch_data.__file__) = guardados
        shutil.rmtree(tmp, ignore_errors=True)

d = build_with(lambda sid, limit=12, retries=3, backoff=5: [],
               lambda *a, **k: (None, None, None, {}), [])
eq(d["globalResilienceScore"], None, "FRED toda em baixo: score n/d em vez de excepcao")
eq(d["status"], "nd", "e o status diz n/d")
eq(sorted(d["ndPillars"]), ["cycle", "debt", "liquidity", "premium", "solvency"],
   "os cinco pilares ficam declarados em n/d")
eq(d["stressGauge"]["active"], None, "o medidor fica n/d, nunca OFF")
true(d["meta"]["freshness"]["degraded"], "a corrida e marcada como degradada")
json.dumps(d)   # tem de continuar serializavel
ok += 1

# e o data.json declara sempre quando foi gerado
true(d["meta"]["generatedAt"], "o data.json declara quando foi gerado")
eq(d["meta"]["freshness"]["generatedAt"], d["meta"]["generatedAt"],
   "o carimbo do freshness e o mesmo do meta")
true(d["meta"]["freshness"]["refuseAfterHours"] > d["meta"]["freshness"]["warnAfterHours"],
     "recusar tem de vir depois de avisar")

# ═══════════════════════════════════════════════════════════════════════════
# 6. Um data.json velho nao decide
# ═══════════════════════════════════════════════════════════════════════════
from datetime import datetime, timedelta
AGORA = datetime(SEXTA.year, SEXTA.month, SEXTA.day, 22, 0, 0)
def _carimbo(horas):
    """Um carimbo generatedAt a `horas` de distancia de AGORA."""
    return (AGORA - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%SZ")

def escreve(horas, active=False):
    tmp = Path(tempfile.mkdtemp()) / "data.json"
    gen = (AGORA - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp.write_text(json.dumps({
        "meta": {"generatedAt": gen, "freshness": {"warnAfterHours": 30, "refuseAfterHours": 48}},
        "stressGauge": {"active": active, "subregime": None, "basis": "no trigger active"}}))
    return tmp

logging.disable(logging.ERROR)
eq(up.read_stress_gauge(escreve(4), now=AGORA)[0], False, "4 h: fresco, decide")
eq(up.read_stress_gauge(escreve(28), now=AGORA)[0], False, "28 h: uma corrida falhada, ainda decide")
eq(up.read_stress_gauge(escreve(50), now=AGORA)[0], None, "50 h: velho demais, n/d")
eq(up.read_stress_gauge(escreve(50, active=True), now=AGORA)[0], None,
   "velho demais nao afirma stress tambem — a idade nao tem lados")
logging.disable(logging.NOTSET)
eq(up.data_age_hours({"generatedAt": _carimbo(4)}, AGORA), 4.0, "a idade e calculada em horas")
eq(up.data_age_hours({}, AGORA), None, "sem carimbo, a idade e desconhecida")

# ═══════════════════════════════════════════════════════════════════════════
# 7. O score que decide o ramo Resilient vem do data.json, nao de uma regex
#
# Vinha de re.search sobre o HTML da newsletter — escrito por um LLM — e era o
# da semana ANTERIOR, porque este job corre antes de a desta semana existir. O
# data.json dizia 6,97 e a regex 7,0.
# ═══════════════════════════════════════════════════════════════════════════
def doc_score(score, horas=4):
    tmp = Path(tempfile.mkdtemp()) / "data.json"
    gen = (AGORA - timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp.write_text(json.dumps({
        "meta": {"generatedAt": gen, "freshness": {"warnAfterHours": 30, "refuseAfterHours": 48}},
        "globalResilienceScore": score,
        "stressGauge": {"active": False, "subregime": None, "basis": "no trigger active"}}))
    return tmp

logging.disable(logging.ERROR)
eq(up.read_resilience_score(doc_score(6.97), now=AGORA)[0], 6.97,
   "o score vem do data.json, com duas casas")
eq(up.read_resilience_score(doc_score(3.8), now=AGORA)[0], 3.8, "e passa valores baixos tal e qual")
eq(up.read_resilience_score(doc_score(6.97, horas=72), now=AGORA)[0], None,
   "um data.json velho nao fornece score")
eq(up.read_resilience_score(doc_score(None), now=AGORA)[0], None,
   "score em n/d propaga-se como None, nao como zero")
eq(up.read_resilience_score(Path("/nao/existe/data.json"), now=AGORA)[0], None,
   "sem ficheiro, sem score")
logging.disable(logging.NOTSET)

# e None mantem o regime — nao vira Resilient por o score ter desaparecido
eq(rules.classify_regime(None, False, "Turbulence"), "Turbulence",
   "score em n/d mantem o regime anterior")
# Com o medidor a dizer explicitamente OFF, sair de Critical e o comportamento
# certo mesmo sem score: quem decide Critical e o medidor, nao o score.
eq(rules.classify_regime(None, False, "Critical"), "Turbulence",
   "medidor OFF tira de Critical mesmo sem score — o medidor e que manda")
eq(rules.classify_regime(None, None, "Critical"), "Critical",
   "mas com o medidor em n/d o estado anterior mantem-se")
eq(up.check_emergency({"history": [{"mrm_score": 3.5, "score_complete": True}]}, None)[0], False,
   "sem score nao ha entrada de emergencia em Resilient")

# ── Um composto INCOMPLETO nao dispara uma rotacao de 100% ───────────────
#
# Quando um pilar sai do composto, os pesos renormalizam e o score MEXE — para
# baixo, se o pilar que saiu estava alto. Um pilar cravado no 10,0 que sai leva
# o composto de 5,05 para 3,40, e isso sozinho fazia `emergency`: 100% da
# carteira para QQQ/HYG/IWO, sem a janela de confirmacao, por causa de um pilar
# que DESAPARECEU. E quando ele volta — na sexta em que o operador actualiza a
# referencia dos earnings, que e uma accao de manutencao — a carteira roda outra
# vez ao contrario. Duas rotacoes completas governadas pela cadencia de uma
# pessoa. O protocolo n/d do sistema ja diz que a ausencia de um sinal nao e um
# sinal; faltava aplica-lo aqui.
_pf_nd_em = {"history": [
    {"issue": 20, "date": (SEXTA - _td_topo(days=7)).isoformat(), "mrm_score": 3.4,
     "score_complete": True},
]}
eq(up.check_emergency(_pf_nd_em, 3.4, SEXTA, 21)[0], True,
   "com o composto completo, duas leituras baixas seguidas disparam a entrada")
eq(up.check_emergency(_pf_nd_em, 3.4, SEXTA, 21, nd_pillars=["premium"])[0], False,
   "mas com um pilar FORA do composto nao ha rotacao de emergencia — o score "
   "desceu porque um pilar desapareceu, nao porque o mercado mudou")
eq(up.check_emergency(_pf_nd_em, 3.4, SEXTA, 21, nd_pillars=[])[0], True,
   "e uma lista vazia de pilares em n/d e um composto completo")

# o main() nao pode voltar a usar o numero da newsletter para decidir
fonte_up = (ROOT / "update_portfolio.py").read_text(encoding="utf-8")

true("read_resilience_score(now=agora_utc())" in fonte_up,
     "o main() le o score da fonte autoritativa")
true("read_resilience_score(now=agora_utc())" in fonte_up
     and "read_stress_gauge(now=agora_utc())" in fonte_up,
     "e mede a idade do ficheiro pelo relogio da corrida, que e injectavel")
true("bucket_alloc, newsletter_score = parse_newsletter" in fonte_up,
     "o numero da newsletter fica com nome proprio, para nao ser confundido")
# Esta assercao passava por coincidencia de substring: a linha real e
# `signalled_regime = classify_regime(...)`, e "regime = classify_regime("
# aparece la dentro. Sobreviveria a qualquer variavel terminada em "regime",
# incluindo uma que nao fosse usada.
#
# E deixou de poder ser sobre o TEXTO da chamada: o score que entra no
# `classify_regime` passou a ser o score autoritativo SO quando o composto esta
# completo — com um pilar fora, vale n/d. O que se afirma e o comportamento, e
# esta afirmado no bloco do composto incompleto, mais abaixo. Aqui fica so o que
# continua a ser sobre a fonte: o numero da newsletter nao decide nada.
true("classify_regime(" in fonte_up and "newsletter_score" in fonte_up,
     "o classify_regime existe e o numero da newsletter tem nome proprio")
true("regime = rules.confirm_regime(signalled_regime, was_regime, emerg_why)" in fonte_up,
     "e o resultado passa pela confirmacao antes de ser o regime operativo")

# ── confirm_regime, que nao tinha um unico teste ────────────────────────────
C = rules.confirm_regime
eq(C("Resilient", "Turbulence", None), "Turbulence",
   "Resilient sem confirmacao nao se torna operativo")
eq(C("Resilient", "Critical", None), "Turbulence",
   "e cai em Turbulence, nao fica preso em Critical — era a regressao")
eq(C("Resilient", "Turbulence", "emergency_resilient_3.8"), "Resilient",
   "com confirmacao, entra")
eq(C("Resilient", "Resilient", None), "Resilient", "quem ja la esta, fica")
eq(C("Critical", "Turbulence", None), "Critical", "Critical nunca precisa de confirmacao")
eq(C("Turbulence", "Critical", None), "Turbulence", "e a saida de Critical passa direta")

# a propriedade que interessa: nunca prende. De qualquer estado, com o medidor
# a dizer OFF, ha sempre um caminho de saida no mesmo passo.
for was in ("Turbulence", "Critical", "Resilient"):
    for score in (3.5, 6.0, 9.0):
        sinal = rules.classify_regime(score, False, was)
        conf  = C(sinal, was, None)
        true(conf != "Critical" or was == "Critical" and conf == "Critical" and False,
             f"medidor OFF nunca deixa a carteira em Critical (was={was}, score={score})")
        if was == "Critical":
            eq(rules.decide_rebalance(conf, was, None, None, False), "stress_off",
               f"e ha sempre gatilho de saida (score={score})")

# ═══════════════════════════════════════════════════════════════════════════
# 8. Uma re-corrida nao apaga o registo do rebalanceamento
#
# Ao correr outra vez, was_regime ja e o regime novo, decide_rebalance devolve
# None e o motivo fica "hold". Se isso substituisse a entrada original, o
# stress_on desaparecia e a newsletter — que le history[-1] — diria que nao
# houve transaccoes na semana em que a carteira rodou.
# ═══════════════════════════════════════════════════════════════════════════
from datetime import date as _date
import test_build_data as T

PRECOS = {"SPY": 600.0, "IEF": 95.0, "LQD": 108.0, "PDBC": 14.0, "BIL": 91.5, "VNQ": 88.0,
          "USMV": 90.0, "TLT": 95.0, "SHY": 82.0, "SGOV": 100.5, "GLD": 250.0,
          "QQQ": 500.0, "HYG": 79.0, "IWO": 280.0}


# A alocacao de uma semana ordinaria: soma 100 e nao e nenhum dos vectores de
# Critical, que e o que distingue "o que a carteira detem" de "o que os
# medidores mandariam deter numa crise".
ALLOC_ORDINARIA = {"US_EQUITIES": 20.0, "US_TREASURIES": 25.0, "IG_CREDIT": 15.0,
                   "COMMODITIES": 12.0, "CASH": 20.0, "ALTERNATIVES": 8.0}


def estado(tmp, regime="Turbulence", subregime=None, com_data=False, com_edicao=True):
    """Prepara `tmp` com os ficheiros de estado, num regime DECLARADO.

    O portfolio.json commitado nao e um fixture: muda de regime e de edicao
    todas as sextas. Copia-lo tal e qual faz o teste afirmar coisas sobre a
    semana em que o ficheiro foi escrito — e no dia em que a carteira sair de
    Turbulence, ou na sexta seguinte a esta, o portao dos dois jobs fecha-se.
    Nao da um teste vermelho: da uma semana sem carteira e sem newsletter.
    Por isso o estado anterior e construido aqui, explicitamente.
    """
    ficheiros = ["score_history.json", "portfolio.json"] + (["data.json"] if com_data else [])
    for f in ficheiros:
        shutil.copy(ROOT / f, tmp)
    if com_edicao:
        fx.poe_edicao_anterior(ROOT, tmp, ISSUE - 1, ANTERIOR)
    _p = json.loads((tmp / "portfolio.json").read_text())
    mapa = rules.REGIME_ETF_MAP[rules.resolve_etf_map_key(regime, subregime)]
    _p["current"].update({
        "issue": ISSUE - 1, "date": D_ANTERIOR,
        "regime": regime, "critical_subregime": subregime,
        "active_etf_map": dict(mapa),
        "shares": {t: 10.0 for t in mapa.values()},
        "last_prices": {t: PRECOS.get(t, 100.0) for t in mapa.values()},
        "last_price_dates": {t: D_ANTERIOR for t in mapa.values()},
    })
    # E o VECTOR de alocacao tambem. Faltava: com a carteira commitada em
    # Critical, o estado dizia-se Turbulence e trazia os pesos de Critical, e a
    # asserçao "a alocacao efectiva nao e o vector de Critical" ficava vermelha
    # na sexta seguinte a entrada em Critical — o portao a fechar-se
    # precisamente durante a crise que o sistema existe para gerir.
    _alloc = (dict(rules.CRITICAL_WEIGHTS[subregime]) if subregime
              else dict(ALLOC_ORDINARIA))
    _p["current"]["bucket_allocation_pct"] = _alloc
    _p["current"]["newsletter_bucket_allocation_pct"] = dict(ALLOC_ORDINARIA)
    # O estado construido e POS-migracao: a adopcao dos pesos do regime e uma
    # transicao unica de Set 2026, e o que estes ensaios descrevem e o sistema
    # em regime permanente. Sem esta marca, a adopcao disparava em todos eles e
    # transformava cada semana calma num rebalanceamento. A migracao em si tem
    # ensaios proprios, mais abaixo.
    _p["current"]["regime_weights_adopted"] = True
    _p["history"] = [h for h in _p["history"] if h.get("issue", 0) < ISSUE - 1]
    _p["history"].append({
        "issue": ISSUE - 1, "date": D_ANTERIOR, "regime": regime,
        "regime_signalled": regime, "critical_subregime": subregime,
        "rebalance_triggered": False, "rebalance_reason": "hold",
        "mrm_score": 6.97, "portfolio_value": 10000.0, "portfolio_pnl_pct": 0.0,
        "shares": dict(_p["current"]["shares"]),
    })
    # E o estado preparado e mesmo o da edicao imediatamente anterior a semana
    # ensaiada. Sem esta linha, as duas de cima seriam decorativas: com o
    # repositorio a saltar uma semana semestral, a carteira ficava a apontar
    # para uma edicao que nao e a N-1 e nada o dizia.
    eq((_p["current"]["issue"], _p["current"]["date"]), (ISSUE - 1, D_ANTERIOR),
       "o estado preparado e o da edicao imediatamente anterior a semana ensaiada")
    # E o vector de alocacao corresponde ao regime declarado. Sem esta linha, a
    # construcao acima seria decorativa: com a carteira commitada em Critical, o
    # estado dizia-se Turbulence e trazia os pesos de Critical, e os cenarios
    # "Turbulence" corriam, em silencio, sobre uma alocacao de crise.
    eq(_p["current"]["bucket_allocation_pct"] in list(rules.CRITICAL_WEIGHTS.values()),
       bool(subregime),
       f"o vector de alocacao preparado corresponde ao regime declarado "
       f"({regime}/{subregime})")
    (tmp / "portfolio.json").write_text(json.dumps(_p))
    return tmp


def corre_semana(tmp, stress):
    """Corre fetch_data + update_portfolio uma vez dentro de tmp."""
    obs = fx.obs_base(SEXTA)
    if stress:
        obs["SAHMREALTIME"] = fx.sahm(SEXTA, "0.62", "0.55")
    class R:
        def __init__(s, sid): s.sid = sid
        def raise_for_status(s): pass
        def json(s): return {"observations": list(obs.get(s.sid, []))}
    guardados = (fetch_data.fetch_fred, fetch_data.fetch_liquidity_percentile,
                 mrm_gauge_b.requests.get, fetch_data.__file__,
                 up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
                 up.FORCE_REBALANCE)
    fetch_data.fetch_fred = lambda sid, limit=12, retries=3, backoff=5: obs.get(sid, [])[:limit]
    fetch_data.fetch_liquidity_percentile = T.fake_liquidity
    mrm_gauge_b.requests.get = lambda url, params=None, timeout=None: R((params or {}).get("series_id"))
    up.fetch_prices = lambda t, d, retries=3: ({x: PRECOS[x] for x in t},
                                               {x: str(d) for x in t}, {x: False for x in t})
    up.get_last_friday = lambda: SEXTA
    up.adjust_for_market_holiday = lambda d: d
    up.FORCE_REBALANCE = True
    cwd = os.getcwd(); os.chdir(tmp)
    fetch_data.__file__ = str(tmp / "fetch_data.py")
    logging.disable(logging.WARNING)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fetch_data.build_data(SEXTA)
            try:
                up.main()
            except SystemExit:
                pass
        return json.loads((tmp / "portfolio.json").read_text())
    finally:
        logging.disable(logging.NOTSET)
        os.chdir(cwd)
        (fetch_data.fetch_fred, fetch_data.fetch_liquidity_percentile,
         mrm_gauge_b.requests.get, fetch_data.__file__,
         up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
         up.FORCE_REBALANCE) = guardados

tmp = estado(Path(tempfile.mkdtemp()))

pf1 = corre_semana(tmp, stress=True)
h1 = pf1["history"][-1]
eq(h1["rebalance_reason"], "stress_on", "primeira corrida: entra em stress")
eq(h1["rebalance_triggered"], True, "e executa")
eq(h1["regime"], "Critical", "o campo regime guarda o que a carteira passou a deter")
eq(h1["regime_signalled"], "Critical", "e o sinalizado tambem, porque coincidem")

# Idempotencia a serio: correr outra vez sobre os mesmos dados tem de tomar a
# MESMA decisao, nao uma decisao diferente nem nenhuma. Antes, a segunda
# passagem via was_regime="Critical" (escrito pela primeira) e concluia que a
# semana anterior ja estava em Critical — o que ABRIA a porta assimetrica e
# deixava entrar o TLT com 35% da carteira, anulando a proteccao que a porta
# existe para dar. Correr o job outra vez nao pode mudar o que ele decide.
pf2 = corre_semana(tmp, stress=True)
h2 = pf2["history"][-1]
eq(len([h for h in pf2["history"] if h["issue"] == h1["issue"]]), 1,
   "a re-corrida nao duplica a entrada do issue")
eq(h2["rebalance_reason"], h1["rebalance_reason"], "a re-corrida toma a MESMA decisao")
eq(h2["critical_subregime"], h1["critical_subregime"],
   "e o mesmo sub-regime — a porta assimetrica nao se abre por se correr outra vez")
# As accoes sao arredondadas a 4 casas, por isso re-rebalancear a partir do
# valor ja arredondado desloca a ultima casa decimal. O que tem de ser identico
# e a decisao: os mesmos instrumentos, com o mesmo valor a menos de um cento.
eq(sorted(h2["shares"]), sorted(h1["shares"]),
   "os mesmos instrumentos — nenhum TLT a aparecer numa re-corrida")
true(abs(sum(h2["shares"][t] * PRECOS[t] for t in h2["shares"]) -
         sum(h1["shares"][t] * PRECOS[t] for t in h1["shares"])) < 1.0,
     "e o mesmo valor investido, a menos de um dolar")
eq(h2["rebalance_triggered"], True, "continua a haver registo de transaccoes")
eq(h2["rerun_count"], 1, "e fica registado que foi uma re-corrida")
eq(pf2["current"]["regime"], "Critical", "a carteira continua em Critical")

pf3 = corre_semana(tmp, stress=True)      # terceira passagem
h3 = pf3["history"][-1]
eq(h3["rebalance_reason"], h1["rebalance_reason"], "a terceira passagem tambem")
eq(sorted(h3["shares"]), sorted(h1["shares"]), "e os instrumentos nao derivam")
true(abs(sum(h3["shares"][t] * PRECOS[t] for t in h3["shares"]) -
         sum(h1["shares"][t] * PRECOS[t] for t in h1["shares"])) < 1.0,
     "nem o valor, a menos de um dolar ao fim de tres passagens")
eq(h3["rerun_count"], 2, "a contagem de re-corridas acumula")
shutil.rmtree(tmp, ignore_errors=True)

# ═══════════════════════════════════════════════════════════════════════════
# 9. Semanas consecutivas contadas por data, nao por posicao no historico
# ═══════════════════════════════════════════════════════════════════════════
alvo = _date(2026, 9, 11)
seguida  = {"history": [{"mrm_score": 3.5, "date": "2026-09-04", "issue": 26,
                         "score_complete": True}]}
saltada  = {"history": [{"mrm_score": 3.5, "date": "2026-08-07", "issue": 22,
                         "score_complete": True}]}
sem_data = {"history": [{"mrm_score": 3.5, "issue": 26, "score_complete": True}]}
logging.disable(logging.WARNING)
eq(up.check_emergency(seguida, 3.8, alvo)[0], True,
   "duas leituras em semanas seguidas confirmam a entrada em Resilient")
eq(up.check_emergency(saltada, 3.8, alvo)[0], False,
   "uma leitura de ha cinco semanas NAO conta como consecutiva")
eq(up.check_emergency(sem_data, 3.8, alvo)[0], False,
   "uma entrada sem data nao pode contar como consecutiva")
eq(up.check_emergency(seguida, 5.0, alvo)[0], False, "score acima do limiar nao confirma")
logging.disable(logging.NOTSET)

# ═══════════════════════════════════════════════════════════════════════════
# 10. Escrita atomica: nunca um ficheiro truncado
# ═══════════════════════════════════════════════════════════════════════════
d = Path(tempfile.mkdtemp()); alvo_f = d / "x.json"
up.write_json_atomic(alvo_f, {"a": 1, "b": [1, 2, 3]})
eq(json.loads(alvo_f.read_text()), {"a": 1, "b": [1, 2, 3]}, "escreve o conteudo certo")
eq(list(d.glob("*.tmp")), [], "e nao deixa o temporario para tras")
falhou = False
try:
    up.write_json_atomic(alvo_f, {"a": float("nan")})
except ValueError:
    falhou = True
true(falhou, "um NaN faz falhar a escrita em vez de gravar um JSON invalido")
eq(json.loads(alvo_f.read_text()), {"a": 1, "b": [1, 2, 3]},
   "e o ficheiro anterior fica intacto")
shutil.rmtree(d, ignore_errors=True)

fonte_fd = (ROOT / "fetch_data.py").read_text(encoding="utf-8")
true("os.replace(tmp_path, output_path)" in fonte_fd, "o data.json tambem e escrito atomicamente")

# ═══════════════════════════════════════════════════════════════════════════
# 11. Segunda auditoria — a semana nao se confirma a si propria
#
# Numa re-corrida, history[-1] e a entrada que a primeira passagem acabou de
# escrever. Com CONSECUTIVE_WEEKS=2, recent_scores passava de
# [score(N-1), score(N)] para [score(N), score(N)]: uma unica leitura <= 4,0
# rodava 100% da carteira para QQQ/HYG/IWO, saltando a confirmacao.
# ═══════════════════════════════════════════════════════════════════════════
pf_rerun = {"history": [{"issue": 26, "mrm_score": 6.0, "date": "2026-09-04",
                         "score_complete": True},
                        {"issue": 27, "mrm_score": 3.8, "date": "2026-09-11",
                         "score_complete": True}]}
logging.disable(logging.WARNING)
eq(up.check_emergency(pf_rerun, 3.8, _date(2026, 9, 11), 27)[0], False,
   "a entrada do proprio issue nao conta como semana anterior")
pf_ok = {"history": [{"issue": 26, "mrm_score": 3.5, "date": "2026-09-04",
                      "score_complete": True}]}
eq(up.check_emergency(pf_ok, 3.8, _date(2026, 9, 11), 27)[0], True,
   "mas uma leitura genuina da semana anterior continua a confirmar")
eq(up.check_emergency(pf_rerun, 3.8, _date(2026, 9, 11))[0], True,
   "sem issue_number nao ha como excluir — por isso o main() passa-o sempre")
logging.disable(logging.NOTSET)

# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# 14. O benchmark reconstruido usa o capital e o preco de ARRANQUE
#
# Usava INITIAL_CAPITAL=100.000 (o capital do backtest; este portfolio arranca
# com 10.000) e o SPY de HOJE. Daria +900% de benchmark e -894% de alpha.
# ═══════════════════════════════════════════════════════════════════════════
pf_real = json.loads((ROOT / "portfolio.json").read_text())
cap = pf_real["meta"]["inception_value"]
# O preco de arranque do SPY sai da PRIMEIRA entrada do historico. O historico
# cresce 52 entradas por ano e um dia sera compactado; a partir dai `history[0]`
# deixa de ser a semana de arranque e esta reconstrucao passa a comparar coisas
# diferentes. Quando isso acontecer, o que se verifica e que o campo continua a
# ser consistente com o preco que o proprio ficheiro declara — e diz-se
# porque, em vez de o portao ficar vermelho com um IndexError.
# A compactacao detecta-se pelo NUMERO da edicao, nao por a entrada ter precos:
# o motor escreve `prices` em todas as entradas, portanto a condicao anterior
# (`_arranque is history[0]`) era sempre verdadeira e o ramo de emergencia era
# codigo morto — com o historico truncado o portao ficava vermelho na mesma, com
# uma mensagem que nao dizia o que fazer.
_compactado = not pf_real["history"] or pf_real["history"][0].get("issue") != 1
_arranque = next((h for h in pf_real["history"] if (h.get("prices") or {}).get("SPY")), None)
true(_compactado or _arranque is not None,
     "o portfolio.json ainda declara o preco de arranque do SPY nalguma entrada "
     "do historico (se falhar, o historico foi compactado: guardar o preco de "
     "arranque em meta e ler dai)")
spy0 = (_arranque or {}).get("prices", {}).get("SPY")
if not _compactado and _arranque is pf_real["history"][0]:
    eq(round(cap / spy0, 4), pf_real["current"]["benchmark_spy_shares"],
       "e a reconstrucao da exactamente o valor guardado")
else:
    true(pf_real["current"]["benchmark_spy_shares"] > 0,
         "historico compactado: o numero de accoes do benchmark continua declarado")

# ═══════════════════════════════════════════════════════════════════════════
# 15. data_refused apanha os cinco caminhos de recusa, nao um
# ═══════════════════════════════════════════════════════════════════════════
logging.disable(logging.ERROR)
def refusou(p_):
    up.DATA_REFUSED["value"] = False
    up.read_stress_gauge(p_, now=AGORA)
    return up.DATA_REFUSED["value"]

d_ = Path(tempfile.mkdtemp())
casos = {
    "ficheiro ausente": Path("/nao/existe/data.json"),
    "sem carimbo":      d_ / "a.json",
    "carimbo no futuro": d_ / "b.json",
    "velho demais":     d_ / "c.json",
    "sem stressGauge":  d_ / "d.json",
}
(d_ / "a.json").write_text(json.dumps({"stressGauge": {"active": False}}))
(d_ / "b.json").write_text(json.dumps({"meta": {"generatedAt": _carimbo(-24 * 80)},
                                       "stressGauge": {"active": False}}))
(d_ / "c.json").write_text(json.dumps({"meta": {"generatedAt": _carimbo(24 * 10)},
                                       "stressGauge": {"active": False}}))
(d_ / "d.json").write_text(json.dumps({"meta": {"generatedAt": _carimbo(4)}}))
for nome, caminho in casos.items():
    eq(refusou(caminho), True, f"data_refused marca a recusa: {nome}")
(d_ / "e.json").write_text(json.dumps({"meta": {"generatedAt": _carimbo(4)},
                                       "stressGauge": {"active": False, "basis": "ok"}}))
eq(refusou(d_ / "e.json"), False, "e uma leitura normal nao e marcada como recusa")
logging.disable(logging.NOTSET)
shutil.rmtree(d_, ignore_errors=True)

# ═══════════════════════════════════════════════════════════════════════════
# 16. write_json_atomic nao deixa lixo quando a serializacao falha
# ═══════════════════════════════════════════════════════════════════════════
d2 = Path(tempfile.mkdtemp()); alvo2 = d2 / "x.json"
up.write_json_atomic(alvo2, {"ok": 1})
try:
    up.write_json_atomic(alvo2, {"mau": float("inf")})
except ValueError:
    pass
eq(list(d2.glob("*.tmp")), [], "o temporario e removido mesmo quando a escrita falha")
eq(json.loads(alvo2.read_text()), {"ok": 1}, "e o ficheiro anterior fica intacto")
shutil.rmtree(d2, ignore_errors=True)

# ═══════════════════════════════════════════════════════════════════════════
# 17. Testes COMPORTAMENTAIS das correcçoes da 3a auditoria
#
# Os testes anteriores destas correccoes verificavam strings do codigo-fonte, e
# continuavam a passar com a correccao apagada. Estes correm o main() real.
# ═══════════════════════════════════════════════════════════════════════════
def mundo(stress=True, sahm="0.62", precos=None, ftq=True):
    """Prepara um tmpdir com os ficheiros de estado e devolve o runner."""
    return estado(Path(tempfile.mkdtemp()))

def corre(tmpd, stress=True, ftq=True, precos=None, force=True):
    obs = fx.obs_base(SEXTA)
    if stress:
        obs["SAHMREALTIME"] = fx.sahm(SEXTA, "0.62")
    obs["DGS10"] = fx.dgs10(SEXTA, 4.15, 4.47) if ftq else fx.dgs10(SEXTA, 4.47, 4.15)
    px = dict(PRECOS) if precos is None else precos
    class R:
        def __init__(s, sid): s.sid = sid
        def raise_for_status(s): pass
        def json(s): return {"observations": list(obs.get(s.sid, []))}
    g = (fetch_data.fetch_fred, fetch_data.fetch_liquidity_percentile,
         mrm_gauge_b.requests.get, fetch_data.__file__,
         up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday, up.FORCE_REBALANCE)
    fetch_data.fetch_fred = lambda sid, limit=12, retries=3, backoff=5: obs.get(sid, [])[:limit]
    fetch_data.fetch_liquidity_percentile = T.fake_liquidity
    mrm_gauge_b.requests.get = lambda url, params=None, timeout=None: R((params or {}).get("series_id"))
    up.fetch_prices = lambda t, d, retries=3: ({x: px[x] for x in t if x in px},
                                               {x: str(d) for x in t}, {x: False for x in t})
    up.get_last_friday = lambda: SEXTA
    up.adjust_for_market_holiday = lambda d: d
    up.FORCE_REBALANCE = force
    cwd = os.getcwd(); os.chdir(tmpd); fetch_data.__file__ = str(tmpd / "fetch_data.py")
    logging.disable(logging.WARNING)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fetch_data.build_data(SEXTA)
            try:
                up.main()
            except SystemExit:
                pass
        return json.loads((tmpd / "portfolio.json").read_text())
    finally:
        logging.disable(logging.NOTSET); os.chdir(cwd)
        (fetch_data.fetch_fred, fetch_data.fetch_liquidity_percentile,
         mrm_gauge_b.requests.get, fetch_data.__file__,
         up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
         up.FORCE_REBALANCE) = g

# ── 17a. Re-corrida SEM a edicao N-1 no historico: a porta FTQ fica fechada ──
tmpd = mundo()
pf_a = corre(tmpd, stress=True, ftq=True)
h_a = pf_a["history"][-1]
eq(h_a["critical_subregime"], "Critical_Stress",
   "primeira passagem: entrada fresca e defensiva mesmo com o 10Y a cair")

# apaga-se a edicao N-1 e corre-se outra vez
pf_edit = json.loads((tmpd / "portfolio.json").read_text())
n = h_a["issue"]
pf_edit["history"] = [h for h in pf_edit["history"] if h.get("issue") != n - 1]
(tmpd / "portfolio.json").write_text(json.dumps(pf_edit))
pf_b = corre(tmpd, stress=True, ftq=True)
h_b = pf_b["history"][-1]
eq(h_b["critical_subregime"], "Critical_Stress",
   "re-corrida sem a edicao N-1: a porta assimetrica NAO se abre")
true("TLT" not in (h_b["shares"] or {}),
     f"e o TLT nao entra na carteira (shares={sorted(h_b['shares'])})")
shutil.rmtree(tmpd, ignore_errors=True)

# ── 17b. Estado coerente: regime, mapa, pesos e accoes da mesma era ─────────
tmpd = mundo()
corre(tmpd, stress=True, ftq=False)          # entra em Critical
pf_c = corre(tmpd, stress=False, ftq=False)  # medidor desliga-se entre passagens
cur_c = pf_c["current"]
mapa = set(cur_c["active_etf_map"].values())
detidas = set(cur_c["shares"])
true(detidas <= mapa,
     f"as accoes detidas pertencem ao mapa declarado (detidas={sorted(detidas)}, mapa={sorted(mapa)})")
esperado = rules.REGIME_ETF_MAP[rules.resolve_etf_map_key(
    cur_c["regime"], cur_c.get("critical_subregime"))]
eq(cur_c["active_etf_map"], esperado,
   "e o mapa declarado e o que corresponde ao regime declarado")
if cur_c["regime"] == "Critical":
    eq(cur_c["bucket_allocation_pct"],
       rules.CRITICAL_WEIGHTS[rules.resolve_etf_map_key(cur_c["regime"], cur_c["critical_subregime"])],
       "em Critical, os pesos sao os de Critical")
else:
    true(cur_c["bucket_allocation_pct"] not in rules.CRITICAL_WEIGHTS.values(),
         "fora de Critical, os pesos nao sao os de Critical")
shutil.rmtree(tmpd, ignore_errors=True)

# ── 17c. Um preco em falta mantem posicoes e publica o motivo ──────────────
tmpd = mundo()
corre(tmpd, stress=False, ftq=False)
antes = json.loads((tmpd / "portfolio.json").read_text())["current"]["shares"]
sem_gld = {k: v for k, v in PRECOS.items() if k != "GLD"}   # GLD so existe em Critical
pf_d = corre(tmpd, stress=True, ftq=False, precos=sem_gld)
h_d = pf_d["history"][-1]
eq(h_d["rebalance_reason"], "missing_prices_held",
   "sem preco de um instrumento do mapa novo, o motivo publicado e esse")
eq(h_d["rebalance_triggered"], False, "e nao ha transaccoes")
eq(h_d["shares"], antes, "as posicoes anteriores sao mantidas, tal e qual")
true(rules.rebalance_copy("missing_prices_held") != "missing_prices_held",
     "e o motivo tem texto publicavel, nao o identificador em bruto")
shutil.rmtree(tmpd, ignore_errors=True)

# ── 17d. O benchmark reconstruido bate certo com o guardado ────────────────
#
# A reconstrucao so e possivel enquanto o historico chegar a edicao de arranque.
# O historico do repositorio ainda la chega; quando for compactado deixa de
# chegar, e o caso passa a ser o do bloco seguinte. A condicao sai do ficheiro
# em vez de ser assumida — foi assim que este bloco rebentou com um KeyError num
# repositorio de historico compactado.
tmpd = mundo()
pf_e = json.loads((tmpd / "portfolio.json").read_text())
_pode_reconstruir = (pf_e.get("history") or [{}])[0].get("issue") in (None, 1)
guardado = pf_e["current"].pop("benchmark_spy_shares")
(tmpd / "portfolio.json").write_text(json.dumps(pf_e))
if _pode_reconstruir:
    pf_f = corre(tmpd, stress=False, ftq=False)
    eq(pf_f["current"]["benchmark_spy_shares"], guardado,
       "sem o campo, o benchmark reconstroi-se e da exactamente o mesmo valor")
else:
    _parou = False
    try:
        corre(tmpd, stress=False, ftq=False)
    except BaseException:
        _parou = True
    true(_parou or json.loads((tmpd / "portfolio.json").read_text())["current"]
         .get("benchmark_spy_shares") is None,
         "com o historico ja compactado, o motor nao reconstroi o benchmark")
shutil.rmtree(tmpd, ignore_errors=True)

# A fronteira e a edicao 1, e so a edicao 1: a reconstrucao usa o preco de SPY
# da PRIMEIRA entrada como preco de arranque, portanto qualquer outra edicao a
# abrir o historico da um benchmark comprado ao preco de uma semana qualquer.
_pf_b2 = json.loads((ROOT / "portfolio.json").read_text())
for _n_abre, _deve_reconstruir in ((1, True), (2, False), (7, False)):
    _tmp_b = mundo()
    _p_b = json.loads((_tmp_b / "portfolio.json").read_text())
    _p_b["current"].pop("benchmark_spy_shares", None)
    _p_b["history"] = [{**h, "issue": _n_abre + i}
                       for i, h in enumerate(_p_b["history"][-4:])]
    (_tmp_b / "portfolio.json").write_text(json.dumps(_p_b))
    _correu = True
    try:
        corre(_tmp_b, stress=False, ftq=False)
    except BaseException:
        _correu = False
    _reconstruiu = (_correu and json.loads((_tmp_b / "portfolio.json").read_text())
                    ["current"].get("benchmark_spy_shares") is not None)
    eq(_reconstruiu, _deve_reconstruir,
       f"com o historico a abrir na edicao {_n_abre}, o benchmark "
       f"{'reconstroi-se' if _deve_reconstruir else 'NAO se reconstroi'}")
    shutil.rmtree(_tmp_b, ignore_errors=True)

# E com o historico COMPACTADO — que acontece um dia, porque cresce 52 entradas
# por ano — a reconstrucao nao pode acontecer: `history[0]` deixa de ser a
# semana de arranque, e reconstruir dela daria o capital de arranque comprado ao
# preco de uma semana qualquer. Um benchmark errado publicado aos subscritores e
# pior do que um job que para e diz porque.
tmpd = mundo()
_pf_c = json.loads((tmpd / "portfolio.json").read_text())
_pf_c["current"].pop("benchmark_spy_shares")
_pf_c["history"] = _pf_c["history"][-6:]
(tmpd / "portfolio.json").write_text(json.dumps(_pf_c))
_rebentou = False
try:
    corre(tmpd, stress=False, ftq=False)
except SystemExit:
    _rebentou = True
except Exception:
    _rebentou = True
true(_rebentou or json.loads((tmpd / "portfolio.json").read_text())["current"]
     .get("benchmark_spy_shares") is None,
     "com o historico compactado e o campo ausente, o motor NAO inventa um "
     "benchmark a partir da entrada errada")
shutil.rmtree(tmpd, ignore_errors=True)

# ═══════════════════════════════════════════════════════════════════════════
# 18. Quarta auditoria — o que a suite deixava apagar sem falhar
#
# A auditoria mostrou que sete das dez correccoes da ronda anterior podiam ser
# removidas com a suite inteiramente verde. Estas asserçoes fecham essa lacuna,
# e todas foram verificadas por mutacao: apagar a correccao faz falhar o teste.
# ═══════════════════════════════════════════════════════════════════════════

# ── 18a. Uma posicao detida sem preco NAO para a semana ────────────────────
tmpd = mundo()
corre(tmpd, stress=False, ftq=False)
pf0 = json.loads((tmpd / "portfolio.json").read_text())
detidos = sorted(pf0["current"]["shares"])
sem_um = {k: v for k, v in PRECOS.items() if k != detidos[0]}
pf_g = corre(tmpd, stress=False, ftq=False, precos=sem_um)
hg = pf_g["history"][-1]
eq(hg["issue"], pf0["history"][-1]["issue"], "a semana foi publicada na mesma")
# Com o recurso agora utilizavel (FALLBACK_MAX_AGE_DAYS=10 numa cadencia
# semanal), o preco vem do ultimo fecho conhecido e a semana e marcada como
# `data_stale`. `valuation_frozen` so enche quando nem o recurso serve.
eq(hg["data_stale"], True, "a semana declara que usou pelo menos um preco de recurso")
true(hg["portfolio_value"] > 0.9 * pf0["current"]["portfolio_value"],
     "a valorizacao usa o ultimo preco conhecido em vez de omitir a posicao")
true(detidos[0] in hg["prices"],
     f"e o instrumento continua na valorizacao ({detidos[0]})")

# E se nem o recurso servir: publica na mesma, com o congelamento declarado.
pf_frz = json.loads((tmpd / "portfolio.json").read_text())
pf_frz["current"]["last_prices"] = {k: v for k, v in pf_frz["current"]["last_prices"].items()
                                    if k != detidos[0]}
(tmpd / "portfolio.json").write_text(json.dumps(pf_frz))
pf_i = corre(tmpd, stress=False, ftq=False, precos=sem_um)
hi_ = pf_i["history"][-1]
# Sem cotacao NEM recurso e uma avaria diferente de "preco antigo": a posicao
# nao entra na conta, portanto o valor fica incompleto e o P&L e suprimido em
# vez de calculado sobre uma carteira parcial. Publicar -16,4% quando o real e
# -1,0% seria pior do que nao publicar P&L nenhum.
true(detidos[0] in (hi_.get("valuation_missing") or []),
     f"sem cotacao nem recurso, o instrumento fica em valuation_missing ({hi_.get('valuation_missing')})")
eq(hi_.get("valuation_complete"), False, "e a semana declara a valorizacao incompleta")
eq(hi_["portfolio_pnl_pct"], None, "o P&L e suprimido, nao calculado sobre a carteira parcial")
true(hi_["rebalance_reason"] is not None, "e a semana e publicada, nao abortada")
true(detidos[0] not in (hi_.get("valuation_frozen") or []),
     "e as duas avarias nao se confundem: isto nao e um preco congelado")
shutil.rmtree(tmpd, ignore_errors=True)

# ── 18b. O recurso de preco e utilizavel na cadencia semanal ───────────────
true(up.FALLBACK_MAX_AGE_DAYS >= 7,
     f"o recurso tem de aceitar o fecho da semana anterior (e {up.FALLBACK_MAX_AGE_DAYS} dias)")
true(up.FALLBACK_MAX_AGE_DAYS < 14,
     "mas nao duas semanas seguidas em falta")

# ── 18c. A emergencia nao dispara dentro de Critical ───────────────────────
eq(rules.decide_rebalance("Critical", "Critical", "Critical_Stress", "Critical_Stress",
                          False, "emergency_resilient_3.5"), None,
   "score baixo dentro de Critical nao gera rebalanceamento de emergencia")
eq(rules.decide_rebalance("Resilient", "Turbulence", None, None, False,
                          "emergency_resilient_3.5"), "emergency_resilient_3.5",
   "mas fora de Critical continua a gerar")

# ── 18d. Saida de Critical para Resilient tem texto proprio ────────────────
eq(rules.decide_rebalance("Resilient", "Critical", None, None, False,
                          "emergency_resilient_3.5"), "stress_off_to_resilient",
   "a saida de Critical directamente para Resilient e um motivo distinto")
txt = rules.rebalance_copy("stress_off_to_resilient")
true("Resilient map" in txt and "returned to the Turbulence map" not in txt,
     "e o texto publicado fala do mapa certo (o de stress_off dizia Turbulence)")
true("returned to the Turbulence map" in rules.rebalance_copy("stress_off"),
     "enquanto o stress_off normal continua a falar de Turbulence, que e o seu caso")

# ── 18e. Uma re-corrida sem N-1 nao fabrica uma troca de sub-regime ────────
tmpd = mundo()
pf_h1 = corre(tmpd, stress=True, ftq=True)
h1_ = pf_h1["history"][-1]
pf_edit = json.loads((tmpd / "portfolio.json").read_text())
pf_edit["history"] = [h for h in pf_edit["history"] if h.get("issue") != h1_["issue"] - 1]
(tmpd / "portfolio.json").write_text(json.dumps(pf_edit))
pf_h2 = corre(tmpd, stress=True, ftq=True)
h2_ = pf_h2["history"][-1]
true(not str(h2_["rebalance_reason"]).startswith("critical_subregime_switch"),
     f"a re-corrida nao inventa uma troca de sub-regime (deu {h2_['rebalance_reason']})")
eq(h2_["rebalance_reason"], h1_["rebalance_reason"],
   "e preserva o motivo original")
shutil.rmtree(tmpd, ignore_errors=True)

# ── 18f. O briefing ao dono nao rebenta quando nao ha resposta ─────────────
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("sn_audit", ROOT / "send_newsletter.py")
_sn = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_sn)
eq(_sn.REBALANCE_STYLE_DEFAULT[1], "#8B96A3",
   "o estilo por omissao e neutro, nao verde — um motivo desconhecido nao e sucesso")
for motivo in ("missing_prices_held", "aborted_invalid_shares", "hold"):
    true(motivo in _sn.REBALANCE_STYLE,
         f"uma semana com o motivo {motivo} nao pode ser desenhada como sucesso")
true(_sn.REBALANCE_STYLE["missing_prices_held"][1] != "#34D058",
     "e o motivo de retencao por falta de preco nao e verde")

# ── 18g. O dashboard desenha o que a carteira detem ───────────────────────
html = (ROOT / "index.html").read_text(encoding="utf-8")
true("const tickers = ['SPY','IEF','LQD','PDBC','BIL','VNQ']" not in html,
     "a lista de instrumentos do dashboard deixou de estar escrita a mao")
for t in ("USMV", "TLT", "SGOV", "GLD", "QQQ", "HYG", "IWO", "SHY"):
    true(f"{t}:" in html.split("const ETF_META")[1][:2000],
         f"o dashboard conhece o instrumento {t}, que so aparece fora de Turbulence")

# ══════════════════════════════════════════════════════════════════════════
# 19. AS TRES GUARDAS QUE PROTEGEM CAPITAL REAL
#
# A auditoria 8 mostrou que estas tres, cada uma com um comentario a descrever
# ao cento o prejuizo que evita, nao tinham UM teste. Apagar qualquer uma delas
# deixava a suite inteira verde. Sao as tres decisoes deste ficheiro onde um
# erro faz desaparecer dinheiro, e por isso sao as que mais precisavam.
# ══════════════════════════════════════════════════════════════════════════
import pandas as _pd

# ── 19a. Um preco de zero e uma falha de cotacao, nao uma cotacao de zero ──
# Um 0.0 passava por bom: calculate_value somava `qty * 0`, a posicao
# desaparecia da carteira e a semana era publicada com valuation_complete:true,
# data_stale:false e zero avisos. 10.605 -> 9.109, +6,06% -> -8,90%.
class _FakeTicker:
    def __init__(self, sym): self.sym = sym
    def history(self, start=None, end=None):
        fecho = _PRECO_FALSO.get(self.sym, 100.0)
        if fecho is _VAZIO:
            return _pd.DataFrame({"Close": []})
        return _pd.DataFrame({"Close": [fecho]},
                             index=_pd.to_datetime([str(_ALVO)]))

_VAZIO = object()
_ALVO = SEXTA
_PRECO_FALSO = {}
_yf_real = up.yf
up.yf = types.SimpleNamespace(Ticker=_FakeTicker)
_sleep_real = up.time.sleep
up.time.sleep = lambda *_: None
try:
    _PRECO_FALSO = {"GLD": 0.0, "BIL": 91.5}
    pr, pd_, st = up.fetch_prices(["GLD", "BIL"], _ALVO, retries=1)
    eq(pr["GLD"], None, "um preco de 0.0 e recusado, nao aceite como cotacao")
    eq(st["GLD"], True, "e a falta e declarada, para o fallback tomar conta")
    eq(pr["BIL"], 91.5, "e o instrumento com preco bom continua a ser lido")

    _PRECO_FALSO = {"GLD": -3.2}
    pr, _, st = up.fetch_prices(["GLD"], _ALVO, retries=1)
    eq(pr["GLD"], None, "um preco negativo tambem")

    _PRECO_FALSO = {"GLD": float("nan")}
    pr, _, _ = up.fetch_prices(["GLD"], _ALVO, retries=1)
    eq(pr["GLD"], None, "e um NaN")
finally:
    up.yf = _yf_real
    up.time.sleep = _sleep_real

# E a camada seguinte recusa somar sobre um preco nao positivo, mesmo que
# alguem lho entregue: as guardas nao dependem uma da outra.
_rebentou = ""
try:
    up.calculate_value({"GLD": 10}, {"GLD": 0.0})
except ValueError as e:
    _rebentou = str(e)
true("nao positivo" in _rebentou,
     f"calculate_value recusa um preco nao positivo (obtido {_rebentou[:60]!r})")
eq(up.calculate_value({"GLD": 10}, {"GLD": 250.0}), 2500.0,
   "e continua a somar normalmente com precos bons")

# ── 19b. Uma carteira que nao se consegue valorizar NAO se rebalanceia ─────
# O portfolio_value sobre o qual as posicoes novas seriam dimensionadas ja vem
# incompleto: se o instrumento sem preco nao pertencer ao mapa novo, o dinheiro
# dele desaparece — e na semana seguinte a perda aparece como performance limpa.
def corre_sem_preco(tmp, em_falta):
    """Como corre_semana, mas com um instrumento sem cotacao NEM fallback."""
    guardados = (up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
                 up.FORCE_REBALANCE)
    def _sem(t, d, retries=3):
        return ({x: (None if x in em_falta else PRECOS.get(x, 100.0)) for x in t},
                {x: (None if x in em_falta else str(d)) for x in t},
                {x: (x in em_falta) for x in t})
    up.fetch_prices = _sem
    up.get_last_friday = lambda: SEXTA
    up.adjust_for_market_holiday = lambda d: d
    up.FORCE_REBALANCE = True
    cwd = os.getcwd(); os.chdir(tmp)
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                up.main()
            except SystemExit:
                pass
        return json.loads((tmp / "portfolio.json").read_text())
    finally:
        logging.disable(logging.NOTSET)
        os.chdir(cwd)
        (up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
         up.FORCE_REBALANCE) = guardados

tmp_vm = estado(Path(tempfile.mkdtemp()), com_data=True)
# um data.json fresco, para o motor nao recusar por idade
_dj = json.loads((tmp_vm / "data.json").read_text())
_dj.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
# O medidor B a disparar: e a semana em que a carteira MAIS precisa de se mexer,
# e por isso a que melhor mostra o que a guarda faz. Com um instrumento sem
# preco, mexer-se seria dimensionar as posicoes novas sobre um valor de carteira
# que ja vem incompleto — e o dinheiro do bucket sem preco desaparecia.
_dj["stressGauge"] = {"active": True, "subregime": "STRESS", "basis": "Sahm",
                      "label": "Stress ON — No Relief",
                      "triggers": {"sahmRealtime": {"value": 0.62, "threshold": 0.5,
                                                    "asOf": fx.mes_menos(SEXTA, 1).isoformat(),
                                                    "fired": True},
                                   "delinquencyAccel": {"value": 0.1, "threshold": 0.81,
                                                        "asOf": fx.npl(SEXTA, "1.4")[0]["date"],
                                                        "fired": False}}}
(tmp_vm / "data.json").write_text(json.dumps(_dj))
# e um portfolio sem last_prices, para que o instrumento em falta fique mesmo
# sem qualquer valor conhecido
_pf = json.loads((tmp_vm / "portfolio.json").read_text())
_accoes_antes = dict(_pf["current"]["shares"])
_pf["current"]["last_prices"] = {}
_pf["current"]["last_price_dates"] = {}
(tmp_vm / "portfolio.json").write_text(json.dumps(_pf))

_em_falta = sorted(_accoes_antes)[0]
pf_vm = corre_sem_preco(tmp_vm, {_em_falta})
h_vm = pf_vm["history"][-1]
eq(h_vm["rebalance_reason"], "valuation_incomplete_held",
   f"com {_em_falta} sem preco nem fallback, o rebalanceamento e CANCELADO")
eq(h_vm["rebalance_triggered"], False, "e nao e marcado como executado")
eq(pf_vm["current"]["shares"], _accoes_antes,
   "as posicoes ficam exactamente como estavam — nenhuma foi dimensionada "
   "sobre um valor de carteira incompleto")
true(_em_falta in (h_vm.get("valuation_missing") or []),
     f"e o instrumento por valorizar e nomeado ({h_vm.get('valuation_missing')})")
eq(h_vm.get("portfolio_pnl_pct"), None,
   "o P&L da semana e suprimido em vez de calculado sobre uma carteira parcial")
shutil.rmtree(tmp_vm, ignore_errors=True)

# ── E as duas linhas de resumo SAEM, na semana em que sao precisas ─────────
#
# `log.info("... SPY: $%s (%+.2f%%) ...", bench_value, bench_pnl)` com
# `bench_pnl` a None nao levanta: o logging imprime "--- Logging error ---" e
# DESCARTA a mensagem. Quando o SPY nao tem cotacao nem recurso — a semana em
# que o operador mais precisa de ler o resumo — as duas unicas linhas que
# resumem a carteira desapareciam do log, e o job ficava verde.
class _Apanha(logging.Handler):
    def __init__(self):
        super().__init__()
        self.linhas, self.erros = [], []
    def emit(self, record):
        try:
            self.linhas.append(record.getMessage())
        except Exception as e:                       # noqa: BLE001
            self.erros.append(f"{record.msg!r}: {e}")

def corre_capturando(tmp, em_falta):
    """Como corre_sem_preco, mas com o log ligado e apanhado."""
    guardados = (up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
                 up.FORCE_REBALANCE)
    def _sem(t, d, retries=3):
        return ({x: (None if x in em_falta else PRECOS.get(x, 100.0)) for x in t},
                {x: (None if x in em_falta else str(d)) for x in t},
                {x: (x in em_falta) for x in t})
    up.fetch_prices = _sem
    up.get_last_friday = lambda: SEXTA
    up.adjust_for_market_holiday = lambda d: d
    up.FORCE_REBALANCE = True
    h = _Apanha()
    raiz = logging.getLogger()
    nivel = raiz.level
    raiz.addHandler(h); raiz.setLevel(logging.INFO)
    cwd_ = os.getcwd(); os.chdir(tmp)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                up.main()
            except SystemExit:
                pass
        return h
    finally:
        os.chdir(cwd_)
        raiz.removeHandler(h); raiz.setLevel(nivel)
        (up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
         up.FORCE_REBALANCE) = guardados

tmp_log = estado(Path(tempfile.mkdtemp()), com_data=True)
_pf_log = json.loads((tmp_log / "portfolio.json").read_text())
_pf_log["current"]["last_prices"] = {}
_pf_log["current"]["last_price_dates"] = {}
(tmp_log / "portfolio.json").write_text(json.dumps(_pf_log))
_h_log = corre_capturando(tmp_log, {"SPY"})
eq(_h_log.erros, [],
   f"nenhuma linha do log e descartada por um erro de formatacao ({_h_log.erros})")
true(any(l.startswith("Portfolio: ") for l in _h_log.linhas),
     "a linha 'Portfolio:' sai mesmo com o SPY sem preco — e a semana em que "
     "ela e precisa")
true(any(l.startswith("Summary: ") for l in _h_log.linhas),
     "e a linha 'Summary:' tambem")
_resumo_log = [l for l in _h_log.linhas if l.startswith(("Portfolio: ", "Summary: "))]
true(all("n/d" in l for l in _resumo_log),
     f"e o que nao se pode medir aparece como n/d, nao como um numero "
     f"inventado ({_resumo_log})")
shutil.rmtree(tmp_log, ignore_errors=True)

# ── 19c. Re-corrida sem a edicao N-1: a porta assimetrica fica FECHADA ────
# Uma re-corrida rebobina o estado anterior para a edicao N-1. Quando essa
# edicao nao esta no historico (push falhado, ficheiro truncado), ir buscar a
# ultima que la esteja daria um estado anterior ERRADO: se fosse Critical, a
# porta assimetrica abria-se e entrava TLT com 35% da carteira sem que o 10Y
# alguma vez tivesse confirmado a descida. E recalcular o sub-regime fabricava
# uma `critical_subregime_switch` — uma venda real sem gatilho nenhum.
def corre_recorrida(tmp, sem_cotacao=()):
    """`sem_cotacao`: tickers cuja cotacao FALHA nesta corrida — o motor cai no
    recurso deles, que e o caminho que decide se um preco velho entra numa
    transaccao. Sem este parametro, este ajudante substituia o `fetch_prices`
    por um que cota tudo, e qualquer ensaio sobre o recurso era silenciosamente
    desfeito por ele."""
    guardados = (up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
                 up.FORCE_REBALANCE)
    _falham = set(sem_cotacao)
    up.fetch_prices = lambda t, d, retries=3: (
        {x: (None if x in _falham else PRECOS.get(x, 100.0)) for x in t},
        {x: (None if x in _falham else str(d)) for x in t},
        {x: False for x in t})
    up.get_last_friday = lambda: SEXTA
    up.adjust_for_market_holiday = lambda d: d
    up.FORCE_REBALANCE = True
    cwd = os.getcwd(); os.chdir(tmp)
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                up.main()
            except SystemExit:
                pass
        return json.loads((tmp / "portfolio.json").read_text())
    finally:
        logging.disable(logging.NOTSET)
        os.chdir(cwd)
        (up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
         up.FORCE_REBALANCE) = guardados

tmp_rr = estado(Path(tempfile.mkdtemp()), com_data=True)
_d_rr = json.loads((tmp_rr / "data.json").read_text())
_d_rr.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
# Medidor B ligado, para o regime se manter Critical e o sub-regime ser a
# unica coisa em jogo.
_d_rr["stressGauge"] = {"active": True, "subregime": "STRESS", "basis": "Sahm",
                        "label": "Stress ON — No Relief", "triggers": {}}
(tmp_rr / "data.json").write_text(json.dumps(_d_rr))

# A carteira DETEM Critical_FTQ (com TLT), a entrada desta semana ja existe, e
# a edicao N-1 NAO esta no historico. E a situacao exacta que abria a porta.
_p_rr = json.loads((tmp_rr / "portfolio.json").read_text())
_ISSUE = ISSUE
_p_rr["current"].update({
    "regime": "Critical", "critical_subregime": "Critical_FTQ",
    "active_etf_map": dict(rules.REGIME_ETF_MAP["Critical_FTQ"]),
    "bucket_allocation_pct": dict(rules.CRITICAL_WEIGHTS["Critical_FTQ"]),
    "shares": {t: 10.0 for t in rules.REGIME_ETF_MAP["Critical_FTQ"].values()},
    "last_prices": {t: PRECOS.get(t, 100.0) for t in rules.REGIME_ETF_MAP["Critical_FTQ"].values()},
    "last_price_dates": {t: D_SEXTA for t in rules.REGIME_ETF_MAP["Critical_FTQ"].values()},
    "date": D_SEXTA,
})
# historico: a entrada desta semana existe (e uma re-corrida), a de N-1 nao.
_p_rr["history"] = [h for h in _p_rr["history"] if h.get("issue") not in (_ISSUE, _ISSUE - 1)]
_p_rr["history"].append({
    "issue": _ISSUE, "date": D_SEXTA, "regime": "Critical",
    "regime_signalled": "Critical", "critical_subregime": "Critical_FTQ",
    "rebalance_triggered": True, "rebalance_reason": "stress_on",
    "mrm_score": 6.97, "portfolio_value": 10000.0, "portfolio_pnl_pct": 0.0,
    "shares": dict(_p_rr["current"]["shares"]),
})
(tmp_rr / "portfolio.json").write_text(json.dumps(_p_rr))
_tlt_antes = dict(_p_rr["current"]["shares"])

pf_rr = corre_recorrida(tmp_rr)
h_rr = next(h for h in pf_rr["history"] if h.get("issue") == _ISSUE)
true(not str(h_rr.get("rebalance_reason", "")).startswith("critical_subregime_switch"),
     f"a re-corrida sem a edicao N-1 nao fabrica uma troca de sub-regime "
     f"(obtido {h_rr.get('rebalance_reason')!r})")
eq(h_rr["rebalance_reason"], "stress_on",
   "e o motivo original da semana e preservado, nao substituido por 'hold'")
# E o FACTO tambem, nao so o motivo. O motivo estava afirmado; o
# `rebalance_triggered` nao — e e ele que o `build_context` le para escrever
# "Executed: no transactions" no prompt e que o site le para esconder a linha do
# log. A re-corrida podia apagar o registo de a carteira ter rodado e dizer aos
# subscritores que nao houve transaccoes na semana em que houve.
eq(h_rr["rebalance_triggered"], True,
   "e o facto de ter havido transaccoes tambem — e o que a newsletter publica")
true(h_rr.get("rerun_no_further_action") is True,
   f"e a passagem fica marcada como re-corrida sem accao "
   f"({h_rr.get('rerun_no_further_action')})")
eq(pf_rr["current"]["critical_subregime"], "Critical_FTQ",
   "o sub-regime detido e CONGELADO, nao recalculado para Critical_Stress")
eq(sorted(pf_rr["current"]["shares"]), sorted(_tlt_antes),
   "e os instrumentos ficam os mesmos — nenhuma venda de 35% da carteira sem gatilho")
eq(len([h for h in pf_rr["history"] if h.get("issue") == _ISSUE]), 1,
   "a re-corrida substitui a entrada, nao duplica")
shutil.rmtree(tmp_rr, ignore_errors=True)

# ── 19d. A porta assimetrica FTQ, no motor e nao so nas regras ────────────
# A entrada FRESCA em Critical e SEMPRE Critical_Stress, mesmo que o 10Y ja
# esteja a cair: o TLT so se ganha depois de a descida estar confirmada com o
# sistema ja em Critical. A regra estava testada em mrm_rules; a LIGACAO no
# motor — `was_critical_last_week = (was_regime == "Critical")` — nao estava, e
# troca-la por `True` entregava 35% da carteira ao TLT na primeira semana.
def corre_com_gauge(tmp, subregime_do_medidor, regime_anterior, sub_anterior=None,
                    active=True, historico=None):
    """`historico`, quando dado, SUBSTITUI o historico depois do resto do
    arranque: ha ensaios em que o que interessa e a divergencia entre o que o
    `current` diz (o `was_regime`) e o que o historico diz sobre a semana que
    escreveu a edicao lida."""
    _d = json.loads((tmp / "data.json").read_text())
    _d.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d["stressGauge"] = {"active": active, "subregime": subregime_do_medidor,
                         "basis": "Sahm", "label": "Stress ON", "triggers": {}}
    (tmp / "data.json").write_text(json.dumps(_d))
    _p = json.loads((tmp / "portfolio.json").read_text())
    mapa = (rules.REGIME_ETF_MAP[sub_anterior] if sub_anterior
            else rules.REGIME_ETF_MAP["Turbulence"])
    _p["current"].update({
        "regime": regime_anterior, "critical_subregime": sub_anterior,
        "active_etf_map": dict(mapa),
        "shares": {t: 10.0 for t in mapa.values()},
        "last_prices": {t: PRECOS.get(t, 100.0) for t in mapa.values()},
        "last_price_dates": {t: D_SEXTA for t in mapa.values()},
        "date": D_ANTERIOR,
    })
    _p["history"] = [h for h in _p["history"] if h.get("issue") not in (ISSUE - 1, ISSUE)]
    _p["history"].append({"issue": ISSUE - 1, "date": D_ANTERIOR, "regime": regime_anterior,
                          "regime_signalled": regime_anterior,
                          "critical_subregime": sub_anterior,
                          "rebalance_triggered": False, "rebalance_reason": "hold",
                          "mrm_score": 6.97, "portfolio_value": 10000.0,
                          "portfolio_pnl_pct": 0.0, "shares": dict(_p["current"]["shares"])})
    if historico is not None:
        _p["history"] = historico
    (tmp / "portfolio.json").write_text(json.dumps(_p))
    return corre_recorrida(tmp)

tmp_ftq = estado(Path(tempfile.mkdtemp()), com_data=True)

pf_ftq = corre_com_gauge(tmp_ftq, "FTQ", "Turbulence")
eq(pf_ftq["current"]["critical_subregime"], "Critical_Stress",
   "entrada fresca em Critical com o medidor a dizer FTQ: a carteira vai para "
   "Critical_Stress na mesma — o TLT nao se ganha a entrada")
true("TLT" not in pf_ftq["current"]["shares"],
     f"e nao ha TLT nenhum na carteira ({sorted(pf_ftq['current']['shares'])})")
true("SHY" in pf_ftq["current"]["shares"], "a manga de duracao e o SHY")

# Ja DENTRO de Critical, o mesmo sinal ganha o TLT: a porta e assimetrica, nao
# esta fechada a cadeado.
tmp_ftq2 = estado(Path(tempfile.mkdtemp()), com_data=True)
pf_ftq2 = corre_com_gauge(tmp_ftq2, "FTQ", "Critical", "Critical_Stress")
eq(pf_ftq2["current"]["critical_subregime"], "Critical_FTQ",
   "ja dentro de Critical, o mesmo sinal FTQ passa — a porta abre-se pelo lado certo")
true("TLT" in pf_ftq2["current"]["shares"], "e ai sim entra o TLT")
shutil.rmtree(tmp_ftq, ignore_errors=True)
shutil.rmtree(tmp_ftq2, ignore_errors=True)

# ── 19e. O sub-regime por omissao quando NAO ha leitura nenhuma ───────────
# O caso que faltava: o medidor sem leitura (`active: None`, os dois gatilhos
# sem dados) e a carteira ja em Critical mas SEM sub-regime registado — um
# portfolio.json escrito por uma versao anterior, ou um campo perdido numa
# edicao a mao. O codigo cai num valor por omissao, e a escolha desse valor e
# uma decisao de dinheiro real: `Critical_Stress` deixa a carteira no SHY,
# `Critical_FTQ` entrega 35% ao TLT — comprado por causa de uma FALHA DE DADOS,
# sem um unico sinal a pedi-lo. A porta assimetrica diz que o TLT so se ganha
# com uma descida do 10Y confirmada; uma avaria de rede nao e uma descida
# confirmada. Trocar o lado por omissao passava despercebido a suite inteira.
tmp_nd = estado(Path(tempfile.mkdtemp()), com_data=True)
pf_nd = corre_com_gauge(tmp_nd, None, "Critical", None, active=None)
eq(pf_nd["current"]["regime"], "Critical",
   "sem leitura do medidor, o regime anterior mantem-se (protocolo n/d)")
eq(pf_nd["current"]["critical_subregime"], "Critical_Stress",
   "e sem sub-regime anterior para reter, a omissao e o lado DEFENSIVO — "
   "uma avaria de dados nao compra TLT")
true("TLT" not in pf_nd["current"]["shares"],
     f"e nao ha TLT nenhum na carteira ({sorted(pf_nd['current']['shares'])})")
shutil.rmtree(tmp_nd, ignore_errors=True)

# E com um sub-regime anterior registado, a omissao nao se aplica: retem-se o
# que estava em vigor. Sem isto, a mesma falha de rede VENDIA o TLT de quem o
# tinha legitimamente — o erro simetrico, igualmente caro.
tmp_nd2 = estado(Path(tempfile.mkdtemp()), com_data=True)
pf_nd2 = corre_com_gauge(tmp_nd2, None, "Critical", "Critical_FTQ", active=None)
eq(pf_nd2["current"]["critical_subregime"], "Critical_FTQ",
   "com sub-regime anterior, uma corrida sem leitura RETEM-no em vez de o "
   "trocar pelo valor por omissao")
true("TLT" in pf_nd2["current"]["shares"],
     f"e o TLT nao e vendido por causa de uma falha de dados "
     f"({sorted(pf_nd2['current']['shares'])})")
shutil.rmtree(tmp_nd2, ignore_errors=True)

# ── 19f-19o. A memoria macro deixou de existir ────────────────────────────
#
# Aqui viviam catorze regressoes — 19f a 19o — sobre uma pergunta so: de que
# tabela, de entre as que o modelo escreveu, se pode confiar para saber a que
# percentagens a carteira volta quando a crise passar. Deteccao do eco do vector
# de crise (byte a byte e aproximado, com limiar dos dois lados), tabelas
# obsoletas, reconstrucao da macro pelo historico, o regime da edicao lida, a
# grafia desse regime, o ultimo recurso da cadeia, e a recusa de inventar uma
# macro quando nao ha nenhuma.
#
# Set 2026: a pergunta desapareceu. A carteira volta ao vector de Turbulence do
# `REGIME_WEIGHTS`, esteja a edicao anterior como estiver, e o campo
# `newsletter_bucket_allocation_pct` passou a ser o REGISTO do que a ultima
# edicao publicou — sem consequencia nenhuma sobre o que se executa.
#
# O que fica no lugar e a forma executada dessa afirmacao. Nao e menos teste do
# que os catorze: e o mesmo dinheiro protegido por uma propriedade que nao tem
# como falhar, em vez de por catorze guardas que podiam.
from fixture_newsletter import edicao as _ed_macro

_NOMES_BUCKET = {"US_EQUITIES": "US Equities", "US_TREASURIES": "US Treasuries",
                 "IG_CREDIT": "Investment-Grade Credit",
                 "COMMODITIES": "Commodities", "CASH": "Cash",
                 "ALTERNATIVES": "Alternatives"}

def _poe_edicao_com(tmp, issue, dia, alloc_por_bucket):
    """A edicao N-1 com ESTA tabela de alocacao, como um modelo obediente a escreveria."""
    for _velha in tmp.glob(f"MRM_Newsletter_Issue{issue}_*.html"):
        _velha.unlink()
    nome = f"MRM_Newsletter_Issue{issue}_{dia.strftime('%d%b%Y')}.html"
    linhas = [(_NOMES_BUCKET[b], alloc_por_bucket[b]) for b in rules.BUCKETS]
    (tmp / nome).write_text(_ed_macro(issue, alloc=linhas), encoding="utf-8")
    return nome


_CS = dict(rules.CRITICAL_WEIGHTS["Critical_Stress"])

def _linha_hist(n_, reg, sub, al, accoes):
    return {"issue": n_, "date": D_ANTERIOR, "regime": reg,
            "regime_signalled": reg, "critical_subregime": sub,
            "rebalance_triggered": False, "rebalance_reason": "hold",
            "mrm_score": 6.9, "portfolio_value": 10000.0,
            "portfolio_pnl_pct": 0.0, "bucket_allocation_pct": dict(al),
            "shares": dict(accoes)}

# A carteira esta em Critical_Stress e a edicao N-1 publica o vector de crise —
# o "eco" que catorze regressoes existiam para apanhar. A saida, o que se
# executa nao e esse vector: e o de Turbulence, das regras.
tmp_eco = estado(Path(tempfile.mkdtemp()), regime="Critical",
                 subregime="Critical_Stress", com_data=True)
_poe_edicao_com(tmp_eco, ISSUE - 1, ANTERIOR, rules.CRITICAL_WEIGHTS["Critical_Stress"])
pf_eco_saida = corre_com_gauge(tmp_eco, None, "Critical", "Critical_Stress",
                               active=False)
eq(pf_eco_saida["current"]["regime"], "Turbulence",
   "o medidor desligado tira a carteira de Critical")
eq(pf_eco_saida["current"]["bucket_allocation_pct"],
   dict(rules.REGIME_WEIGHTS["Turbulence"]),
   "e o que se executa a saida e o vector das regras, nao a tabela da edicao")
true(pf_eco_saida["current"]["bucket_allocation_pct"] != dict(_CS),
     "que era o eco que catorze guardas existiam para impedir")
shutil.rmtree(tmp_eco, ignore_errors=True)

# E o mesmo com uma tabela ABSURDA: 95% em accoes publicado na edicao N-1 nao
# move a carteira um ponto. Antes, uma tabela dentro das bandas era executada
# tal e qual no semestral seguinte.
tmp_abs = estado(Path(tempfile.mkdtemp()), regime="Critical",
                 subregime="Critical_Stress", com_data=True)
_poe_edicao_com(tmp_abs, ISSUE - 1, ANTERIOR,
                {"US_EQUITIES": 95.0, "US_TREASURIES": 1.0, "IG_CREDIT": 1.0,
                 "COMMODITIES": 1.0, "CASH": 1.0, "ALTERNATIVES": 1.0})
pf_abs = corre_com_gauge(tmp_abs, None, "Critical", "Critical_Stress", active=False)
eq(pf_abs["current"]["bucket_allocation_pct"],
   dict(rules.REGIME_WEIGHTS["Turbulence"]),
   "uma tabela absurda na edicao nao muda o que a carteira executa")
shutil.rmtree(tmp_abs, ignore_errors=True)

# E sem edicao NENHUMA no disco a carteira executa a mesma coisa. Era este o
# caso que cancelava o rebalanceamento semestral e adiava seis meses.
tmp_sem_ed = estado(Path(tempfile.mkdtemp()), regime="Critical",
                    subregime="Critical_Stress", com_data=True)
for _f_se in tmp_sem_ed.glob("MRM_Newsletter_Issue*_*.html"):
    _f_se.unlink()
pf_sem_ed = corre_com_gauge(tmp_sem_ed, None, "Critical", "Critical_Stress",
                            active=False)
eq(pf_sem_ed["current"]["bucket_allocation_pct"],
   dict(rules.REGIME_WEIGHTS["Turbulence"]),
   "sem edicao nenhuma para ler, a carteira executa o vector do regime")
shutil.rmtree(tmp_sem_ed, ignore_errors=True)

# ── 19e. Re-corrida COM a edicao N-1: a porta decide-se pelo estado de N-1 ─
# Este e o defeito original, na sua forma exacta: correr o job outra vez fazia
# `was_regime` ser o regime que a PRIMEIRA passagem ja tinha escrito (Critical),
# a porta assimetrica abria-se, e a segunda passagem entregava 35% da carteira
# ao TLT — sem que sinal nenhum o tivesse pedido. A rebobinagem para a edicao
# N-1 existe para isso, e ate agora nao tinha teste que a distinguisse.
tmp_rw = estado(Path(tempfile.mkdtemp()), com_data=True)
_d_rw = json.loads((tmp_rw / "data.json").read_text())
_d_rw.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
_d_rw["stressGauge"] = {"active": True, "subregime": "FTQ", "basis": "Sahm",
                        "label": "Stress ON", "triggers": {}}
(tmp_rw / "data.json").write_text(json.dumps(_d_rw))

_mapa_stress = rules.REGIME_ETF_MAP["Critical_Stress"]
_p_rw = json.loads((tmp_rw / "portfolio.json").read_text())
# A carteira ja esta em Critical_Stress porque a PRIMEIRA passagem desta semana
# a la pos. A edicao N-1 diz o que era antes: Turbulence.
_p_rw["current"].update({
    "regime": "Critical", "critical_subregime": "Critical_Stress",
    "active_etf_map": dict(_mapa_stress),
    "shares": {t: 10.0 for t in _mapa_stress.values()},
    "last_prices": {t: PRECOS.get(t, 100.0) for t in _mapa_stress.values()},
    "last_price_dates": {t: D_SEXTA for t in _mapa_stress.values()},
    "date": D_SEXTA,
})
_p_rw["history"] = [h for h in _p_rw["history"] if h.get("issue") not in (ISSUE - 1, ISSUE)]
_p_rw["history"] += [
    {"issue": ISSUE - 1, "date": D_ANTERIOR, "regime": "Turbulence",
     "regime_signalled": "Turbulence", "critical_subregime": None,
     "rebalance_triggered": False, "rebalance_reason": "hold", "mrm_score": 6.97,
     "portfolio_value": 10000.0, "portfolio_pnl_pct": 0.0, "shares": {}},
    {"issue": ISSUE, "date": D_SEXTA, "regime": "Critical",
     "regime_signalled": "Critical", "critical_subregime": "Critical_Stress",
     "rebalance_triggered": True, "rebalance_reason": "stress_on", "mrm_score": 6.97,
     "portfolio_value": 10000.0, "portfolio_pnl_pct": 0.0,
     "shares": dict(_p_rw["current"]["shares"])},
]
(tmp_rw / "portfolio.json").write_text(json.dumps(_p_rw))

pf_rw = corre_recorrida(tmp_rw)
eq(pf_rw["current"]["critical_subregime"], "Critical_Stress",
   f"a re-corrida rebobina para a edicao {ISSUE - 1} (Turbulence): a porta FTQ "
   "mantem-se "
   "fechada e o sub-regime nao muda")
true("TLT" not in pf_rw["current"]["shares"],
     f"nenhum TLT aparece por se ter corrido o job outra vez "
     f"({sorted(pf_rw['current']['shares'])})")
eq(next(h for h in pf_rw["history"] if h.get("issue") == ISSUE)["rebalance_reason"],
   "stress_on", "e o motivo original da semana continua la")
shutil.rmtree(tmp_rw, ignore_errors=True)

# ── 19p. A adopcao dos pesos: acontece uma vez, no motor, e desliga-se ────
#
# A forma EXECUTADA da transicao. Nao chega o predicado dizer que ha pesos por
# adoptar: o que interessa e o motor pegar nisso sozinho, numa sexta normal, sem
# ninguem disparar nada — e nao voltar a faze-lo nunca mais.
tmp_ad = estado(Path(tempfile.mkdtemp()), com_data=True)
_p_ad = json.loads((tmp_ad / "portfolio.json").read_text())
_ANTIGA = {"US_EQUITIES": 10.0, "US_TREASURIES": 20.0, "IG_CREDIT": 15.0,
           "COMMODITIES": 15.0, "CASH": 30.0, "ALTERNATIVES": 10.0}
_p_ad["current"]["bucket_allocation_pct"] = dict(_ANTIGA)
_p_ad["current"].pop("regime_weights_adopted", None)   # como esta hoje em producao
(tmp_ad / "portfolio.json").write_text(json.dumps(_p_ad))

pf_ad = corre_com_gauge(tmp_ad, None, "Turbulence", None, active=False)
_h_ad = pf_ad["history"][-1]
eq(_h_ad["rebalance_reason"], "adopt_regime_weights",
   "numa sexta calma, o motor adopta os pesos do regime sozinho")
eq(_h_ad["rebalance_triggered"], True, "e negoceia mesmo")
eq(pf_ad["current"]["bucket_allocation_pct"],
   dict(rules.REGIME_WEIGHTS["Turbulence"]),
   "a carteira fica com o vector do regime")
eq(pf_ad["current"].get("regime_weights_adopted"), True,
   "e a marca fica escrita no ficheiro")

# Segunda corrida, semana seguinte: NAO volta a acontecer. Era o risco todo —
# um gatilho que se re-arma e um rebalanceador semanal disfarcado de migracao.
_p_ad2 = json.loads((tmp_ad / "portfolio.json").read_text())
_p_ad2["current"]["issue"] = _p_ad2["current"]["issue"] - 1
_p_ad2["current"]["date"] = D_ANTERIOR
# e com a carteira JA a derivar do alvo, para provar que nao e deriva que a arma
_p_ad2["current"]["bucket_allocation_pct"] = {
    b: v + (4.0 if b == "US_EQUITIES" else -0.8)
    for b, v in rules.REGIME_WEIGHTS["Turbulence"].items()}
_p_ad2["history"] = [h for h in _p_ad2["history"] if h.get("issue", 0) < _p_ad2["current"]["issue"]]
(tmp_ad / "portfolio.json").write_text(json.dumps(_p_ad2))
pf_ad2 = corre_com_gauge(tmp_ad, None, "Turbulence", None, active=False)
eq(pf_ad2["history"][-1]["rebalance_reason"], "hold",
   "na semana seguinte nao ha adopcao nenhuma — a marca desligou-a")
eq(pf_ad2["history"][-1]["rebalance_triggered"], False,
   "e nao se negoceia por deriva: isto nunca foi um rebalanceador de deriva")
shutil.rmtree(tmp_ad, ignore_errors=True)

# E uma carteira ja alinhada, sem marca nenhuma, tambem nao dispara: o predicado
# olha para os numeros, nao so para a marca.
tmp_al = estado(Path(tempfile.mkdtemp()), com_data=True)
_p_al = json.loads((tmp_al / "portfolio.json").read_text())
_p_al["current"]["bucket_allocation_pct"] = dict(rules.REGIME_WEIGHTS["Turbulence"])
_p_al["current"].pop("regime_weights_adopted", None)
(tmp_al / "portfolio.json").write_text(json.dumps(_p_al))
pf_al = corre_com_gauge(tmp_al, None, "Turbulence", None, active=False)
eq(pf_al["history"][-1]["rebalance_reason"], "hold",
   "uma carteira ja no vector do regime nao adopta nada")
shutil.rmtree(tmp_al, ignore_errors=True)

# ── 19f. O semestral acontece, leia-se a edicao ou nao ────────────────────
# Ate Set 2026 o semestral existia para aplicar as percentagens publicadas NESTA
# edicao, e por isso era cancelado quando a edicao certa nao era legivel: aplicar
# as de outra semana era negociar sobre uma instrucao que ninguem deu. Deixou de
# haver instrucao para ler — o semestral aplica o vector do regime — e o que se
# testa agora e o simetrico: uma newsletter falhada JA NAO adia seis meses o
# unico rebalanceamento programado do ano.
# A semana semestral sai da propria regra publicada, a contar da sexta ensaiada
# — nao de uma data escrita a mao, que caduca quando o repositorio a ultrapassa.
SEXTA_SEM = rules.next_semestral_date(SEXTA + _td_topo(days=1))
ISSUE_SEMESTRAL = ((SEXTA_SEM - _date_topo(2026, 3, 13)).days // 7) + 1
true(rules.is_semestral_rebalance_week(SEXTA_SEM),
     f"a sexta escolhida ({SEXTA_SEM}) e mesmo uma semana semestral")
true(SEXTA_SEM > SEXTA, "e vem depois da sexta ensaiada")


def corre_semestral(tmp, com_newsletter_legivel):
    guardados = (up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
                 up.FORCE_REBALANCE, up.find_latest_newsletter)
    up.fetch_prices = lambda t, d, retries=3: (
        {x: PRECOS.get(x, 100.0) for x in t}, {x: str(d) for x in t}, {x: False for x in t})
    # a proxima semana semestral a seguir a sexta ensaiada, pela propria regra
    up.get_last_friday = lambda: SEXTA_SEM
    up.adjust_for_market_holiday = lambda d: d
    up.FORCE_REBALANCE = True
    if not com_newsletter_legivel:
        up.find_latest_newsletter = lambda: None      # nenhuma edicao para ler
    cwd = os.getcwd(); os.chdir(tmp)
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                up.main()
            except SystemExit:
                pass
        return json.loads((tmp / "portfolio.json").read_text())
    finally:
        logging.disable(logging.NOTSET)
        os.chdir(cwd)
        (up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
         up.FORCE_REBALANCE, up.find_latest_newsletter) = guardados

def prep_semestral(tmp):
    for f in ("score_history.json", "portfolio.json", "data.json"):
        shutil.copy(ROOT / f, tmp)
    _d = json.loads((tmp / "data.json").read_text())
    _d.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d["stressGauge"] = {"active": False, "subregime": None, "basis": "no trigger active",
                         "label": "Stress OFF", "triggers": {}}
    (tmp / "data.json").write_text(json.dumps(_d))
    _mapa = rules.REGIME_ETF_MAP["Turbulence"]
    _p = json.loads((tmp / "portfolio.json").read_text())
    _p["current"].update({
        "regime": "Turbulence", "critical_subregime": None,
        "active_etf_map": dict(_mapa),
        "shares": {t: 10.0 for t in _mapa.values()},
        "last_prices": {t: PRECOS.get(t, 100.0) for t in _mapa.values()},
        "last_price_dates": {t: SEXTA_SEM.isoformat() for t in _mapa.values()},
        # Pos-migracao: a carteira ja tem o vector do regime, e o que este
        # ensaio mede e o SEMESTRAL. Com os pesos anteriores, quem disparava era
        # a adopcao e o teste passava a medir outra coisa.
        "bucket_allocation_pct": dict(rules.REGIME_WEIGHTS["Turbulence"]),
        "regime_weights_adopted": True,
        "newsletter_bucket_allocation_pct": {"US_EQUITIES": 20.0, "US_TREASURIES": 25.0,
                                             "IG_CREDIT": 15.0, "COMMODITIES": 12.0,
                                             "CASH": 20.0, "ALTERNATIVES": 8.0},
        "issue": ISSUE_SEMESTRAL - 1,
        "date": (SEXTA_SEM - _td_topo(days=7)).isoformat(),
    })
    # o historico tem de acabar na edicao N-1 desta semana semestral, ou o motor
    # ve uma edicao futura e recusa decidir
    _p["history"] = [h for h in _p["history"] if h.get("issue", 0) < ISSUE_SEMESTRAL - 1]
    _p["history"].append({
        "issue": ISSUE_SEMESTRAL - 1,
        "date": (SEXTA_SEM - _td_topo(days=7)).isoformat(),
        "regime": "Turbulence", "regime_signalled": "Turbulence",
        "critical_subregime": None, "rebalance_triggered": False,
        "rebalance_reason": "hold", "mrm_score": 6.97,
        "portfolio_value": 10000.0, "portfolio_pnl_pct": 0.0,
        "shares": dict(_p["current"]["shares"]),
    })
    (tmp / "portfolio.json").write_text(json.dumps(_p))
    return dict(_p["current"]["shares"])

tmp_sem = Path(tempfile.mkdtemp())
_accoes_sem = prep_semestral(tmp_sem)
pf_sem = corre_semestral(tmp_sem, com_newsletter_legivel=False)
h_sem = pf_sem["history"][-1]
eq(h_sem["rebalance_reason"], "semestral_rebalance",
   "sem alocacao legivel nenhuma, o semestral acontece na mesma")
eq(h_sem["rebalance_triggered"], True, "e e executado")
eq(pf_sem["current"]["bucket_allocation_pct"],
   dict(rules.REGIME_WEIGHTS["Turbulence"]),
   "com o vector do regime, que e o que ele aplica")
true(pf_sem["current"]["shares"] != _accoes_sem,
     "e as posicoes mudam mesmo — nao e um semestral no papel")
shutil.rmtree(tmp_sem, ignore_errors=True)

# E com a edicao CERTA legivel, o semestral executa normalmente.
#
# "Certa" quer dizer a N-1: este job corre antes de a edicao desta semana ser
# escrita. Uma versao anterior deste teste copiava a edicao commitada mais
# recente para uma corrida datada meses a frente e afirmava "com a alocacao
# DESTA edicao legivel" — o que provava exactamente o buraco que se pensava
# fechar.
sys.path.insert(0, str(ROOT / "tests"))
from fixture_newsletter import edicao as _edicao_fixture

def escreve_edicao(tmp, numero, alloc=None):
    _dia = (SEXTA_SEM - _td_topo(days=7 * (ISSUE_SEMESTRAL - numero)))
    nome = f"MRM_Newsletter_Issue{numero}_{_dia.strftime('%d%b%Y')}.html"
    (tmp / nome).write_text(
        _edicao_fixture(numero, alloc=alloc or [("US Equities", 35), ("US Treasuries", 30),
                                                ("Investment-Grade Credit", 10),
                                                ("Commodities", 10), ("Cash", 10),
                                                ("Alternatives", 5)]),
        encoding="utf-8")
    return nome

tmp_sem2 = Path(tempfile.mkdtemp())
prep_semestral(tmp_sem2)
escreve_edicao(tmp_sem2, ISSUE_SEMESTRAL - 1)
pf_sem2 = corre_semestral(tmp_sem2, com_newsletter_legivel=True)
h_sem2 = pf_sem2["history"][-1]
eq(h_sem2["rebalance_reason"], "semestral_rebalance",
   "com a edicao N-1 legivel, o semestral acontece")
eq(h_sem2["rebalance_triggered"], True, "e e executado")
eq(pf_sem2["current"]["bucket_allocation_pct"],
   dict(rules.REGIME_WEIGHTS["Turbulence"]),
   "com o vector do regime — e NAO com os 35% que a edicao publicou")
eq(pf_sem["current"]["bucket_allocation_pct"],
   pf_sem2["current"]["bucket_allocation_pct"],
   "com edicao ou sem ela, a carteira acaba exactamente igual")
shutil.rmtree(tmp_sem2, ignore_errors=True)

# E com uma edicao ANTIGA no disco — a N-3, porque a N-1 nunca chegou a ser
# publicada — o semestral e CANCELADO. Era o buraco: `bool(bucket_alloc)`
# provava que ALGUMA edicao era legivel, nao que fosse a desta semana, e o
# motor executava percentagens de ha tres semanas.
tmp_sem3 = Path(tempfile.mkdtemp())
prep_semestral(tmp_sem3)
escreve_edicao(tmp_sem3, ISSUE_SEMESTRAL - 3,
               alloc=[("US Equities", 55), ("US Treasuries", 15),
                      ("Investment-Grade Credit", 10), ("Commodities", 5),
                      ("Cash", 10), ("Alternatives", 5)])
pf_sem3 = corre_semestral(tmp_sem3, com_newsletter_legivel=True)
h_sem3 = pf_sem3["history"][-1]
eq(h_sem3["rebalance_reason"], "semestral_rebalance",
   f"com so a edicao N-3 no disco, o semestral acontece na mesma "
   f"(obtido {h_sem3['rebalance_reason']})")
eq(h_sem3["rebalance_triggered"], True, "e e executado")
true(round(pf_sem3["current"]["bucket_allocation_pct"].get("US_EQUITIES", 0), 1) != 55.0,
     "e as percentagens de ha tres semanas NAO foram aplicadas")
eq(pf_sem3["current"]["bucket_allocation_pct"],
   dict(rules.REGIME_WEIGHTS["Turbulence"]),
   "porque nenhuma edicao aplica percentagens nenhumas")
shutil.rmtree(tmp_sem3, ignore_errors=True)

# E uma MUDANCA DE REGIME nao e cancelada pela mesma razao: a carteira nao pode
# ficar em Critical porque a tabela da semana veio mal escrita. Ai o recurso a
# base macro da ultima edicao legivel e a melhor informacao que existe — e a
# faixa publicada diz exactamente isso.
tmp_reg = Path(tempfile.mkdtemp())
prep_semestral(tmp_reg)
_dr = json.loads((tmp_reg / "data.json").read_text())
_dr["stressGauge"] = {"active": True, "subregime": "STRESS", "basis": "Sahm",
                      "label": "Stress ON", "triggers": {}}
(tmp_reg / "data.json").write_text(json.dumps(_dr))
pf_reg = corre_semestral(tmp_reg, com_newsletter_legivel=False)
eq(pf_reg["history"][-1]["rebalance_reason"], "stress_on",
   "uma entrada em Critical acontece mesmo sem alocacao legivel desta edicao")
eq(pf_reg["current"]["regime"], "Critical", "e a carteira fica mesmo em Critical")
shutil.rmtree(tmp_reg, ignore_errors=True)

def prep_normal(tmp, stress=False, subregime="STRESS"):
    """A semana ENSAIADA (nao a semestral), com o data.json fresco e o medidor
    declarado. Os blocos abaixo correm com `corre_recorrida`, que usa SEXTA:
    preparar o estado com a semana semestral punha o motor a olhar para uma
    edicao que ainda nao existe."""
    estado(tmp, com_data=True)
    _d = json.loads((tmp / "data.json").read_text())
    _d.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d["stressGauge"] = ({"active": True, "subregime": subregime, "basis": "Sahm",
                          "label": "Stress ON", "triggers": {}} if stress else
                         {"active": False, "subregime": None, "triggers": {},
                          "basis": "no trigger active", "label": "Stress OFF"})
    (tmp / "data.json").write_text(json.dumps(_d))
    return dict(json.loads((tmp / "portfolio.json").read_text())["current"]["shares"])


# ── 19g. O motor real DEDUZ os custos de transaccao ──────────────────────
# Turbulence e Critical_Stress partilham dois instrumentos (BIL e VNQ), portanto
# a entrada em Critical fecha quatro posicoes e abre quatro: $80. Sem esta
# deducao, a carteira real crescia $80 a mais do que o backtest publicado no
# mesmo site para a mesma semana — e essa diferenca compoe-se, semana apos
# semana, ate ser quase metade do P&L publicado.
tmp_ct = Path(tempfile.mkdtemp())
prep_normal(tmp_ct)
_dct = json.loads((tmp_ct / "data.json").read_text())
_dct["stressGauge"] = {"active": True, "subregime": "STRESS", "basis": "Sahm",
                       "label": "Stress ON", "triggers": {}}
(tmp_ct / "data.json").write_text(json.dumps(_dct))
_pct = json.loads((tmp_ct / "portfolio.json").read_text())
_valor_antes = sum(q * PRECOS.get(t, 100.0) for t, q in _pct["current"]["shares"].items())
(tmp_ct / "portfolio.json").write_text(json.dumps(_pct))
pf_ct = corre_recorrida(tmp_ct)
h_ct = pf_ct["history"][-1]
eq(h_ct["rebalance_reason"], "stress_on", "a semana rebalanceia")
_custo = h_ct.get("transaction_cost_usd")
_esperado = 10.0 * len(set(rules.REGIME_ETF_MAP["Critical_Stress"].values())
                       - set(rules.REGIME_ETF_MAP["Turbulence"].values())) \
          + 10.0 * len(set(rules.REGIME_ETF_MAP["Turbulence"].values())
                       - set(rules.REGIME_ETF_MAP["Critical_Stress"].values()))
eq(_esperado, 80.0, "quatro fechos e quatro aberturas, aos $10 declarados")
eq(_custo, _esperado,
   f"e o motor cobra exactamente isso (obtido {_custo})")
_valor_depois = sum(q * PRECOS.get(t, 100.0)
                    for t, q in pf_ct["current"]["shares"].items())
true(abs((_valor_antes - _custo) - _valor_depois) < 1.0,
     f"e o custo saiu mesmo do valor da carteira "
     f"({_valor_antes:.2f} - {_custo} vs {_valor_depois:.2f})")
# E o valor PUBLICADO e o liquido, nao o bruto: as posicoes foram dimensionadas
# sobre `valor - custo`, e publicar o bruto sobrestimava a carteira em $C
# exactamente na semana do evento, que e a semana que se le.
eq(h_ct["portfolio_value"], round(h_ct["portfolio_value_pre_rebalance"] - _custo, 2),
   f"o valor publicado e liquido do custo "
   f"({h_ct['portfolio_value']} vs {h_ct['portfolio_value_pre_rebalance']} - {_custo})")
# E o P&L publicado tambem.
_pnl_bruto = round((h_ct["portfolio_value_pre_rebalance"] - 10000.0) / 10000.0 * 100, 2)
true(h_ct["portfolio_pnl_pct"] < _pnl_bruto,
     f"o P&L publicado e liquido do custo (bruto {_pnl_bruto}, publicado "
     f"{h_ct['portfolio_pnl_pct']})")
eq(sorted(h_ct["transaction_cost_detail"]["opened"]),
   sorted(set(rules.REGIME_ETF_MAP["Critical_Stress"].values())
          - set(rules.REGIME_ETF_MAP["Turbulence"].values())),
   f"e o detalhe nomeia as posicoes abertas ({h_ct['transaction_cost_detail']})")
shutil.rmtree(tmp_ct, ignore_errors=True)

# E uma RE-CORRIDA nao apaga o custo. Na segunda passagem as posicoes ja estao
# no sitio, portanto trade_cost devolve 0 — e a semana perdia os $80 que de
# facto pagou, com a soma publicada de custos a subestimar a partir dai.
tmp_ct3 = Path(tempfile.mkdtemp())
prep_normal(tmp_ct3)
_d3c = json.loads((tmp_ct3 / "data.json").read_text())
_d3c["stressGauge"] = {"active": True, "subregime": "STRESS", "basis": "Sahm",
                       "label": "Stress ON", "triggers": {}}
(tmp_ct3 / "data.json").write_text(json.dumps(_d3c))
pf_a = corre_recorrida(tmp_ct3)
_custo_a = pf_a["history"][-1]["transaction_cost_usd"]
pf_b = corre_recorrida(tmp_ct3)
_h_b = next(h for h in pf_b["history"] if h["issue"] == pf_a["history"][-1]["issue"])
eq(_h_b["transaction_cost_usd"], _custo_a,
   f"a re-corrida preserva o custo da passagem que executou "
   f"(era {_custo_a}, ficou {_h_b['transaction_cost_usd']})")
eq(_h_b["transaction_cost_detail"], pf_a["history"][-1]["transaction_cost_detail"],
   "e o detalhe tambem")
# E a fusao do detalhe preserva as duas passagens e as outras chaves: `opened` e
# `closed` sao a UNIAO, e o que o caminho normal escreve ao lado delas nao se
# perde. E o registo que alguem le para perceber uma semana.
_det_a = {"opened": ["AAA", "BBB"], "closed": ["CCC"], "model": "x", "adjusted": True}
_det_b = {"opened": ["BBB", "DDD"], "closed": ["EEE"], "model": "y"}
_fundido = {**_det_a, **_det_b,
            "opened": sorted(set(_det_a["opened"]) | set(_det_b["opened"])),
            "closed": sorted(set(_det_a["closed"]) | set(_det_b["closed"]))}
eq(_fundido["opened"], ["AAA", "BBB", "DDD"], "a fusao une as posicoes abertas")
eq(_fundido["closed"], ["CCC", "EEE"], "e as fechadas")
eq(_fundido["adjusted"], True, "e nao deita fora as chaves que so a primeira tinha")
_fonte_up = (ROOT / "update_portfolio.py").read_text(encoding="utf-8")
true("**_det_ant, **_det_novo," in _fonte_up,
     "e o motor funde mesmo os dois detalhes, em vez de o novo sobrepor o antigo")
true('sorted(set(_det_ant.get("opened") or []) |' in _fonte_up,
     "com as posicoes abertas em uniao")
shutil.rmtree(tmp_ct3, ignore_errors=True)

# E preserva-o nas DUAS formas que a re-corrida pode tomar. Com a edicao N-1 no
# historico, a rebobinagem faz a segunda passagem decidir OUTRA VEZ `stress_on`
# — `rebalance_triggered` volta a ser True e a guarda que so cobria o caminho
# "hold" nao chega. Sem a edicao N-1, a segunda passagem decide "hold". O custo
# da semana e o mesmo nos dois casos: e o que a semana pagou.
tmp_ct4 = Path(tempfile.mkdtemp())
prep_normal(tmp_ct4, stress=True)
pf_c = corre_recorrida(tmp_ct4)
_issue_c = pf_c["history"][-1]["issue"]
_custo_c = pf_c["history"][-1]["transaction_cost_usd"]
eq(_custo_c, 80.0, "a passagem que executa paga os $80")
_pc = json.loads((tmp_ct4 / "portfolio.json").read_text())
_pc["history"] = [h for h in _pc["history"] if h.get("issue") != _issue_c - 1]
(tmp_ct4 / "portfolio.json").write_text(json.dumps(_pc))
pf_d = corre_recorrida(tmp_ct4)
_h_d = next(h for h in pf_d["history"] if h["issue"] == _issue_c)
eq(_h_d["transaction_cost_usd"], _custo_c,
   f"e sem a edicao N-1 no historico o custo continua o mesmo "
   f"(era {_custo_c}, ficou {_h_d['transaction_cost_usd']})")
true(pf_d["history"][-1].get("portfolio_value") == pf_c["history"][-1].get("portfolio_value"),
     "e o valor publicado nao muda por se ter corrido o job outra vez — o custo "
     "e acumulado no registo, nao descontado uma segunda vez")
# E o registo continua coerente consigo proprio: valor = pre-rebalanceamento
# menos custo, na primeira passagem e em todas as seguintes.
for _n, _pf_x in (("primeira passagem", pf_c), ("re-corrida", pf_d)):
    _h_x = next(h for h in _pf_x["history"] if h["issue"] == _issue_c)
    eq(_h_x["portfolio_value"],
       round(_h_x["portfolio_value_pre_rebalance"] - _h_x["transaction_cost_usd"], 2),
       f"{_n}: o valor gravado e o pre-rebalanceamento menos o custo da semana")
shutil.rmtree(tmp_ct4, ignore_errors=True)

# Uma semana SEM transaccoes nao paga nada — e di-lo, em vez de omitir o campo.
tmp_ct2 = Path(tempfile.mkdtemp())
prep_normal(tmp_ct2)
pf_ct2 = corre_recorrida(tmp_ct2)
eq(pf_ct2["history"][-1].get("transaction_cost_usd"), 0.0,
   "uma semana de hold declara custo zero")
shutil.rmtree(tmp_ct2, ignore_errors=True)

# ── 19h. O que se EXECUTA soma 100, mesmo quando a tabela soma 95 ────────
# `validate_allocation` aceita 100 +/- 5 — razoavel, porque quem escreve a
# tabela e um LLM. Mas os pesos aceites eram multiplicados literalmente pelo
# valor da carteira: uma tabela que somasse 95 deixava 5% do capital por
# colocar, e esse dinheiro nao ficava em caixa — desaparecia da conta, para
# reaparecer na semana seguinte como uma perda de -5% publicada aos
# subscritores como desempenho.
_P_REB = {"SPY": 600.0, "IEF": 95.0, "LQD": 110.0, "PDBC": 14.0,
          "BIL": 91.5, "VNQ": 88.0}
_BASE = {"US_EQUITIES": 20.0, "US_TREASURIES": 25.0, "IG_CREDIT": 15.0,
         "COMMODITIES": 12.0, "CASH": 20.0, "ALTERNATIVES": 8.0}
for _delta, _que in ((0.0, "soma 100"), (-5.0, "soma 95"), (+5.0, "soma 105")):
    _al = dict(_BASE); _al["US_EQUITIES"] += _delta
    _sh = up.rebalance_shares(10000.0, _al, "Turbulence", _P_REB)
    _v = up.calculate_value(_sh, _P_REB)
    true(abs(_v - 10000.0) < 0.10,
         f"com uma alocacao que {_que}, executa-se o capital todo "
         f"(obtido ${_v:.2f})")
# E os PESOS relativos sao preservados: renormalizar nao e redistribuir a olho.
_al95 = dict(_BASE); _al95["US_EQUITIES"] = 15.0
_sh95 = up.rebalance_shares(10000.0, _al95, "Turbulence", _P_REB)
eq(round(_sh95["SPY"] * _P_REB["SPY"] / 10000.0 * 100, 1), round(15.0 / 95.0 * 100, 1),
   "e o peso relativo de cada bucket e o que a tabela pedia")
# Uma alocacao vazia continua a ser recusada, nao renormalizada a partir do nada.
_vazia = ""
try:
    up.rebalance_shares(10000.0, {}, "Turbulence", _P_REB)
except ValueError as e:
    _vazia = str(e)
true("alocacao vazia" in _vazia, f"uma alocacao vazia e recusada ({_vazia[:50]!r})")

# ── 19i. Uma linha nao reconhecida rejeita a tabela ──────────────────────
# Era a via de entrada do defeito acima: uma classe de activo que o mapa nao
# apanha era descartada com um simples aviso, o resto passava nos 100 +/- 5, e
# o peso dela desaparecia. Aconteceu com dados reais — a edicao de 14 Mar perdeu
# "Defensive Healthcare" e "Consumer Staples", 20 pontos.
import newsletter_parse as _np_a
_LIN = [("US Equities", 30), ("US Treasuries", 30), ("Investment-Grade Credit", 10),
        ("Commodities", 10), ("Cash", 10), ("Alternatives", 5), ("Digital Assets", 5)]
_t = "".join(f"<tr><td>{n}</td><td>{p}%</td></tr>" for n, p in _LIN)
_html_perdida = ("<html><body><table><thead><tr><th>Asset Class</th><th>Allocation</th>"
                 f"</tr></thead><tbody>{_t}</tbody></table></body></html>")
_al_p, _, _n_p = _np_a.parse_allocation(_html_perdida)
eq(_al_p, {}, "uma linha que nao mapeia para bucket nenhum rejeita a tabela")
true(any("Digital Assets" in m for l, m in _n_p if l == "error"),
     f"e a linha perdida e nomeada ({[m for l, m in _n_p if l == 'error'][:2]})")
# Mas uma linha "Total" nao e uma classe de activo: e a soma, e nao conta.
_com_total = _html_perdida.replace("<tr><td>Digital Assets</td><td>5%</td></tr>",
                                   "<tr><td>Total</td><td>95%</td></tr>")
_al_t, _, _ = _np_a.parse_allocation(_com_total)
true(_al_t and abs(sum(_al_t.values()) - 95.0) < 0.01,
     f"uma linha 'Total' e ignorada, nao somada nem tratada como perdida ({_al_t})")

# ── 19j. O composto nao decide sobre um punhado de pilares ───────────────
# Com quatro dos cinco em n/d, o composto passava a ser um pilar com peso 1,0 —
# e duas leituras seguidas <= 4,0 rodavam a carteira inteira para o mapa
# Resilient, na direccao risk-on, precisamente durante a cegueira.
def corre_com_pilares(tmp, score_vivo, nd):
    _d = json.loads((tmp / "data.json").read_text())
    _d.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d["stressGauge"] = {"active": False, "subregime": None, "basis": "no trigger active",
                         "label": "Stress OFF", "triggers": {}}
    _vivos = {p["id"]: (None if p["id"] in nd else score_vivo) for p in _d["pillars"]}
    _d["pillars"] = [{**p, "score": _vivos[p["id"]]} for p in _d["pillars"]]
    _d["ndPillars"] = sorted(nd)
    _d["globalResilienceScore"] = rules.global_score(_vivos)[0]
    (tmp / "data.json").write_text(json.dumps(_d))
    return corre_recorrida(tmp)

tmp_pil = Path(tempfile.mkdtemp())
prep_normal(tmp_pil)
_pf_pil = corre_com_pilares(tmp_pil, 3.0,
                            ["liquidity", "premium", "solvency", "debt"])
_h_pil = _pf_pil["history"][-1]
eq(_h_pil["mrm_score"], None,
   f"com um pilar vivo nao ha composto (obtido {_h_pil['mrm_score']})")
true(_pf_pil["current"]["regime"] != "Resilient",
     f"e a carteira NAO roda para Resilient ({_pf_pil['current']['regime']})")
true("QQQ" not in _pf_pil["current"]["shares"],
     f"nenhum QQQ aparece ({sorted(_pf_pil['current']['shares'])})")
shutil.rmtree(tmp_pil, ignore_errors=True)

# Com tres pilares vivos ha composto, e o sistema volta a decidir normalmente.
tmp_pil2 = Path(tempfile.mkdtemp())
prep_normal(tmp_pil2)
_pf_pil2 = corre_com_pilares(tmp_pil2, 3.0, ["liquidity", "premium"])
eq(_pf_pil2["history"][-1]["mrm_score"], 3.0,
   f"com tres pilares vivos ha composto "
   f"(obtido {_pf_pil2['history'][-1]['mrm_score']})")
shutil.rmtree(tmp_pil2, ignore_errors=True)

# ── 19k. Uma celula com DUAS percentagens nao e uma alocacao ────────────
# "45% -> 30%" era lido como 45 — os pesos da semana passada. A coluna somava
# 100, passava em todas as bandas, e o motor executava-os sem uma nota, sem um
# aviso e sem a faixa. O subscritor lia "-> 30%" e a carteira ficava com 45%.
_ROT_T = [("US Equities", 45, 30), ("US Treasuries", 20, 35),
          ("Investment-Grade Credit", 10, 10), ("Commodities", 10, 10),
          ("Cash", 10, 10), ("Alternatives", 5, 5)]

def _tab_celula(fmt, cab="Target Weight"):
    _l = "".join(f"<tr><td>{n}</td><td>{fmt(a, b)}</td></tr>" for n, a, b in _ROT_T)
    return (f"<html><body><table><thead><tr><th>Asset Class</th><th>{cab}</th>"
            f"</tr></thead><tbody>{_l}</tbody></table></body></html>")

eq(_np_a.parse_allocation(_tab_celula(lambda a, b: f"{b}%"))[0].get("US_EQUITIES"), 30.0,
   "uma celula com uma percentagem so continua a ser lida")
# As duas guardas apanham coisas diferentes, e cada uma tem o seu caso proprio:
# "45% 30%" so a contagem apanha (nao ha separador); "45 to 30%" so a expressao
# apanha (ha um % so). As restantes formas sao apanhadas por ambas — de
# proposito, porque e a forma que o modelo mais provavelmente escreve.
for _nome, _fmt in (("seta", lambda a, b: f"{a}% \u2192 {b}%"),
                    ("traco", lambda a, b: f"{a}% - {b}%"),
                    ("to", lambda a, b: f"from {a}% to {b}%"),
                    ("sem espacos", lambda a, b: f"{a}%\u2192{b}%"),
                    ("dois numeros sem separador", lambda a, b: f"{a}% {b}%"),
                    ("um so % com 'to'", lambda a, b: f"{a} to {b}%"),
                    ("um so % com seta", lambda a, b: f"{a} \u2192 {b}%"),
                    ("banda", lambda a, b: f"{max(b - 10, 0)}-{b}%")):
    eq(_np_a.parse_allocation(_tab_celula(_fmt))[0], {},
       f"uma celula com duas percentagens ({_nome}) NAO e lida como a primeira")

# ── 19l. Publica-se a alocacao EXECUTADA, e ela soma 100 ────────────────
# O dinheiro ficava certo (rebalance_shares renormaliza) e o registo dele nao: o
# site desenhava barras e um donut a somar 96% ao lado de uma carteira a 100,
# porque a tabela da edicao entrava em bruto no `bucket_allocation_pct`.
# Desde que os pesos vem das regras nao ha tabela em bruto para entrar — mas a
# propriedade que interessa (o que se PUBLICA e o que se EXECUTA, e soma 100)
# fica a ser verificada, porque e sobre ela que o site desenha.
tmp_ren = Path(tempfile.mkdtemp())
prep_semestral(tmp_ren)
escreve_edicao(tmp_ren, ISSUE_SEMESTRAL - 1,
               alloc=[("US Equities", 16), ("US Treasuries", 30),
                      ("Investment-Grade Credit", 10), ("Commodities", 10),
                      ("Cash", 20), ("Alternatives", 10)])   # soma 96
pf_ren = corre_semestral(tmp_ren, com_newsletter_legivel=True)
_h_ren = pf_ren["history"][-1]
eq(_h_ren["rebalance_reason"], "semestral_rebalance",
   "uma tabela a somar 96 na edicao nao impede nem altera o semestral")
_soma_pub = sum(pf_ren["current"]["bucket_allocation_pct"].values())
true(abs(_soma_pub - 100.0) < 0.01,
     f"e a alocacao publicada soma 100, como a carteira (obtido {_soma_pub:.2f})")
_valor = sum(q * PRECOS.get(t, 100.0) for t, q in pf_ren["current"]["shares"].items())
true(abs(_valor - _h_ren["portfolio_value"]) < 1.0,
     f"e o valor bate com as accoes ({_valor:.2f} vs {_h_ren['portfolio_value']})")
eq(pf_ren["current"]["bucket_allocation_pct"],
   dict(rules.REGIME_WEIGHTS["Turbulence"]),
   "e o que se publica e o vector do regime, nao os 16% que a edicao pediu")
shutil.rmtree(tmp_ren, ignore_errors=True)

# E a PROPRIEDADE, corrida sobre o motor inteiro: com um pilar fora do composto
# nao ha mudanca de regime nem transaccao — em NENHUMA das duas direccoes.
#
# A guarda anterior so cobria a entrada (`check_emergency`), que e o lado que
# nao gera a transaccao. A saida passa pelo `decide_rebalance`, que nunca via os
# pilares em n/d: com a carteira em Resilient, bastava uma serie da FRED nao
# publicar numa semana para o composto SUBIR por renormalizacao e o motor
# executar `resilient_off` — 100% da carteira, sem confirmacao, por causa de um
# buraco de dados. E a re-entrada depois exigia duas leituras consecutivas.
def _corre_com_nd(regime_detido, score, nd, sub=None):
    tmp_ = estado(Path(tempfile.mkdtemp()), regime=regime_detido, subregime=sub,
                  com_data=True)
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d_["globalResilienceScore"] = score
    _d_["ndPillars"] = list(nd)
    _d_["stressGauge"] = {"active": False, "subregime": None, "basis": "Sahm",
                          "label": "Stress OFF", "triggers": {}}
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    _p_ = json.loads((tmp_ / "portfolio.json").read_text())
    _p_["history"] = [{**h, "mrm_score": score} for h in _p_["history"]]
    (tmp_ / "portfolio.json").write_text(json.dumps(_p_))
    try:
        return corre_recorrida(tmp_)
    finally:
        shutil.rmtree(tmp_, ignore_errors=True)

# SAIDA: carteira em Resilient, o composto sobe por renormalizacao.
_pf_saida_nd = _corre_com_nd("Resilient", 4.34, ["cycle"])
eq(_pf_saida_nd["current"]["regime"], "Resilient",
   f"com um pilar fora do composto, a carteira NAO sai de Resilient — a subida "
   f"do score e aritmetica de renormalizacao, nao um sinal "
   f"({_pf_saida_nd['current']['regime']})")
eq(_pf_saida_nd["history"][-1]["rebalance_triggered"], False,
   f"e nao ha transaccao nenhuma "
   f"({_pf_saida_nd['history'][-1].get('rebalance_reason')})")
# E com o composto COMPLETO o mesmo score faz o que tem de fazer.
_pf_saida_ok = _corre_com_nd("Resilient", 4.34, [])
eq(_pf_saida_ok["current"]["regime"], "Turbulence",
   f"com o composto completo, o mesmo score tira a carteira de Resilient "
   f"({_pf_saida_ok['current']['regime']})")
# ENTRADA: o lado que a guarda anterior ja cobria, agora pela mesma via.
_pf_entrada_nd = _corre_com_nd("Turbulence", 3.4, ["premium"])
true(_pf_entrada_nd["current"]["regime"] != "Resilient",
     f"e com um pilar fora nao ha entrada em Resilient "
     f"({_pf_entrada_nd['current']['regime']})")

# ── A janela do 10Y por medir nao pode gerar uma transaccao ───────────────
#
# A DGS10 alimenta DUAS coisas: o E/P do pilar Premium e a janela de 3 meses que
# escolhe, DENTRO de Critical, entre Critical_FTQ (35% em TLT) e Critical_Stress
# (20% em SHY). O medidor substituia a janela em falta por "STRESS" — um valor
# defensivo por omissao — e o motor nao tinha como distinguir isso de uma
# medicao. Uma carteira em FTQ vendia o TLT, um terco dela, por causa de uma
# chamada a rede que falhou. E o subscritor lia, como explicacao da semana, o
# aviso da DGS10 a falar do pilar Premium.
def _corre_com_10y(sub_detido, gauge_sub):
    tmp_ = estado(Path(tempfile.mkdtemp()), regime="Critical", subregime=sub_detido,
                  com_data=True)
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d_["stressGauge"] = {"active": True, "subregime": gauge_sub, "basis": "Sahm",
                          "label": "Stress ON", "triggers": {}}
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    try:
        return corre_recorrida(tmp_)
    finally:
        shutil.rmtree(tmp_, ignore_errors=True)

_pf_10y_nd = _corre_com_10y("Critical_FTQ", None)
eq(_pf_10y_nd["current"]["critical_subregime"], "Critical_FTQ",
   f"sem a janela do 10Y medida, a carteira em FTQ MANTEM-SE em FTQ "
   f"({_pf_10y_nd['current']['critical_subregime']})")
eq(_pf_10y_nd["history"][-1]["rebalance_triggered"], False,
   f"e nao ha transaccao nenhuma por falta de leitura "
   f"({_pf_10y_nd['history'][-1].get('rebalance_reason')})")
# E com a janela MEDIDA a dizer que nao ha descida, a degradacao acontece — e
# tem de acontecer, senao o teste acima passaria com um sistema que nunca troca.
_pf_10y_lido = _corre_com_10y("Critical_FTQ", "STRESS")
eq(_pf_10y_lido["current"]["critical_subregime"], "Critical_Stress",
   f"com a janela medida e sem descida, o FTQ degrada-se "
   f"({_pf_10y_lido['current']['critical_subregime']})")
eq(_pf_10y_lido["history"][-1]["rebalance_triggered"], True,
   f"e ai ha transaccao ({_pf_10y_lido['history'][-1].get('rebalance_reason')})")
# E o lado defensivo tambem se mantem: a ausencia nao mexe em nada, nos dois
# sentidos. Em particular nao promove ninguem a FTQ.
_pf_10y_st = _corre_com_10y("Critical_Stress", None)
eq(_pf_10y_st["current"]["critical_subregime"], "Critical_Stress",
   f"sem leitura, quem esta em Stress fica em Stress "
   f"({_pf_10y_st['current']['critical_subregime']})")
eq(_pf_10y_st["history"][-1]["rebalance_triggered"], False,
   "e tambem sem transaccao")

# ── Um sub-regime ILEGIVEL nunca resolve para o mapa risk-on ──────────────
#
# `critical_subregime or "Critical_Stress"` so protegia contra None e "". Um
# valor qualquer passava intacto, e a jusante degradava em silencio para o lado
# ERRADO: `get_active_tickers` cai no `REGIME_ETF_MAP["Turbulence"]` por omissao
# e `effective_bucket_alloc` ve que a chave nao esta em `CRITICAL_WEIGHTS` e
# devolve a alocacao da NEWSLETTER. Com o regime a dizer Critical, a carteira ia
# para SPY/IEF/LQD/PDBC e para as percentagens macro — a rotacao exactamente
# oposta a que Critical existe para fazer, publicada sob a etiqueta "Critical".
#
# E o valor nao e hipotetico: o medidor publica a forma CURTA ("FTQ"/"STRESS")
# e o portfolio.json guarda a longa ("Critical_FTQ"/"Critical_Stress"). Duas
# vocabularios para a mesma coisa, e o RUNBOOK manda reconstruir estado a mao.
_defensivo = dict(rules.CRITICAL_WEIGHTS["Critical_Stress"])
for _mau in ("FTQ", "STRESS", "critical_ftq", "Critical_Ftq", "Turbulence",
             "", None, "Critical", "Critical_FTQ ", 0, [], {}):
    _chave = rules.resolve_etf_map_key("Critical", _mau)
    true(_chave in rules.CRITICAL_WEIGHTS,
         f"sub-regime {_mau!r} resolve para uma chave de Critical ({_chave})")
    _alloc, _origem = rules.effective_bucket_alloc(
        "Critical", _mau, {"US_EQUITIES": 60.0, "US_TREASURIES": 10.0,
                           "IG_CREDIT": 10.0, "COMMODITIES": 5.0,
                           "CASH": 10.0, "ALTERNATIVES": 5.0})
    true(_origem == f"rules ({_chave})",
         f"e a alocacao vem do vector de crise das regras, nao da newsletter "
         f"({_mau!r} -> {_origem})")
    eq(_alloc, dict(rules.CRITICAL_WEIGHTS[_chave]),
       f"e e mesmo o vector dessa chave ({_mau!r})")
    _tk = set(rules.get_active_tickers("Critical", _mau))
    true(_tk != set(rules.REGIME_ETF_MAP["Turbulence"].values()),
         f"e o mapa NAO e o de Turbulence ({_mau!r} -> {sorted(_tk)})")
    if not (isinstance(_mau, str) and _mau in rules.CRITICAL_WEIGHTS):
        eq(_alloc, _defensivo,
           f"um sub-regime ilegivel cai no lado DEFENSIVO ({_mau!r})")
# E os dois legitimos continuam a resolver para si proprios — senao isto
# passaria com uma funcao que devolve sempre Critical_Stress.
for _bom in ("Critical_FTQ", "Critical_Stress"):
    eq(rules.resolve_etf_map_key("Critical", _bom), _bom,
       f"e {_bom} resolve para si proprio")
eq(rules.resolve_etf_map_key("Turbulence", "Critical_FTQ"), "Turbulence",
   "e fora de Critical o sub-regime nao manda")

# E o MOTOR normaliza o campo a entrada, para que nenhum dos tres ramos que o
# le tenha de se lembrar de o validar. Prova-se de ponta a ponta.
def _corre_com_sub_mau(sub_gravado, gauge):
    tmp_ = estado(Path(tempfile.mkdtemp()), regime="Critical",
                  subregime="Critical_FTQ", com_data=True)
    _p_ = json.loads((tmp_ / "portfolio.json").read_text())
    _p_["current"]["critical_subregime"] = sub_gravado
    (tmp_ / "portfolio.json").write_text(json.dumps(_p_))
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d_["stressGauge"] = gauge
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    try:
        return corre_recorrida(tmp_)
    finally:
        shutil.rmtree(tmp_, ignore_errors=True)

_tick_turb = set(rules.REGIME_ETF_MAP["Turbulence"].values())
for _g_nome, _g in (
        ("medidor sem leitura", {"active": None, "subregime": None,
                                 "basis": "n/d", "label": "Stress n/d",
                                 "triggers": {}}),
        ("janela do 10Y sem leitura", {"active": True, "subregime": None,
                                       "basis": "Sahm", "label": "Stress ON",
                                       "triggers": {}})):
    # A assercao ANTERIOR exigia aqui o vector DEFENSIVO — isto e, exigia a
    # venda do TLT. Estava errada, e a correccao da ronda anterior fabricava
    # essa venda: com "FTQ" no campo (a forma que o proprio medidor publica e
    # que o site poe a frente de quem reconstroi estado a mao), a normalizacao
    # apagava o sub-regime, o ramo "corrida sem dados" — que existe para RETER —
    # nao tinha nada para reter, degradava para o defensivo, e `decide_rebalance`
    # via uma troca. 35% da carteira vendidos numa semana em que o medidor nao
    # leu NADA, com a nota gravada ao lado a dizer "previous sub-regime
    # retained". A propriedade certa e a simetria: a mesma carteira escrita de
    # duas maneiras da o mesmo resultado, e a ausencia de leitura nao gera
    # transaccao nenhuma.
    _pf_bom = _corre_com_sub_mau("Critical_FTQ", _g)
    _pf_mau = _corre_com_sub_mau("FTQ", _g)
    _cur_mau, _cur_bom = _pf_mau["current"], _pf_bom["current"]
    eq(_cur_mau["regime"], "Critical", f"{_g_nome}: a carteira continua em Critical")
    true(_cur_mau["critical_subregime"] in rules.CRITICAL_WEIGHTS,
         f"{_g_nome}: e o sub-regime gravado e legivel "
         f"({_cur_mau['critical_subregime']})")
    true(set(_cur_mau["active_etf_map"].values()) != _tick_turb,
         f"{_g_nome}: e o mapa executado NAO e o de Turbulence "
         f"({sorted(set(_cur_mau['active_etf_map'].values()))})")
    eq(_cur_mau["critical_subregime"], _cur_bom["critical_subregime"],
       f"{_g_nome}: a forma curta e a longa dao o MESMO sub-regime")
    eq(_cur_mau["bucket_allocation_pct"], _cur_bom["bucket_allocation_pct"],
       f"{_g_nome}: e a mesma alocacao")
    eq(_cur_mau["shares"], _cur_bom["shares"],
       f"{_g_nome}: e as mesmas accoes — a grafia do campo nao move a carteira")
    eq(_pf_mau["history"][-1]["rebalance_triggered"], False,
       f"{_g_nome}: e sem leitura do medidor NAO ha transaccao nenhuma "
       f"({_pf_mau['history'][-1].get('rebalance_reason')})")
    eq(_pf_bom["history"][-1]["rebalance_triggered"], False,
       f"{_g_nome}: nem com o campo bem escrito")

# E com o campo PERDIDO, o sub-regime le-se do mapa que a carteira detem — nao
# se inventa um valor por omissao nem se congela o campo a None para sempre.
for _g_nome, _g in (
        ("medidor sem leitura", {"active": None, "subregime": None,
                                 "basis": "n/d", "label": "Stress n/d",
                                 "triggers": {}}),):
    _pf_perdido = _corre_com_sub_mau(None, _g)
    eq(_pf_perdido["current"]["critical_subregime"], "Critical_FTQ",
       f"{_g_nome}: sem o campo, o vector le-se do mapa detido "
       f"({_pf_perdido['current']['critical_subregime']})")
    eq(_pf_perdido["history"][-1]["rebalance_triggered"], False,
       f"{_g_nome}: e continua a nao haver transaccao "
       f"({_pf_perdido['history'][-1].get('rebalance_reason')})")
    # E o mapa e mesmo quem manda: com o mapa do OUTRO vector, le-se o outro.
    eq(rules.subregime_do_mapa(rules.REGIME_ETF_MAP["Critical_Stress"]),
       "Critical_Stress", "o mapa do Stress le-se como Stress")
    eq(rules.subregime_do_mapa(rules.REGIME_ETF_MAP["Critical_FTQ"]),
       "Critical_FTQ", "e o do FTQ como FTQ")
    for _mapa_mau in (None, {}, {"US_EQUITIES": "SPY"},
                      rules.REGIME_ETF_MAP["Turbulence"], "texto", []):
        eq(rules.subregime_do_mapa(_mapa_mau), None,
           f"e um mapa que nao e nenhum dos dois nao se le ({_mapa_mau})")

# ── O campo `regime` tambem entra em decisoes; tambem se valida ───────────
#
# O `critical_subregime` ganhou validacao; o campo IRMAO nao, e e o que tem a
# confusao mais convidativa: o site imprime "Portfolio on the Critical_FTQ map",
# o RUNBOOK fala nos dois vectores como o estado da carteira, e o Caso 4 manda o
# operador reconstruir estado a mao. Com `"regime": "Critical_FTQ"`,
# `was_critical_last_week = (was_regime == "Critical")` ficava FALSO: a porta
# assimetrica via uma ENTRADA FRESCA e o motor vendia todo o TLT — 35% da
# carteira — numa semana em que o medidor confirmou a descida do 10Y. E o
# `resolve_etf_map_key` devolvia a string intacta, que caia no mapa de
# Turbulence por omissao: risk-on, com o ficheiro a dizer Critical.
for _reg_mau, _sub_mau, _quer_reg, _quer_sub in (
        ("Critical_FTQ", None, "Critical", "Critical_FTQ"),
        ("Critical_Stress", None, "Critical", "Critical_Stress"),
        ("Critical_FTQ", "Critical_Stress", "Critical", "Critical_Stress"),
        ("critical", None, "Critical", None),
        ("CRITICAL_FTQ", None, "Critical", "Critical_FTQ"),
        ("  critical_stress ", None, "Critical", "Critical_Stress"),
        ("  Resilient  ", None, "Resilient", None),
        # A forma curta e o vocabulario que o PROPRIO produtor publica em
        # `stressGauge.subregime`, e e o que o site mostra a quem reconstroi
        # estado a mao. Reparar a grafia so no campo do regime e deita-la fora
        # no campo a que ela pertence custava a venda do TLT.
        ("Critical", "FTQ", "Critical", "Critical_FTQ"),
        ("Critical", "STRESS", "Critical", "Critical_Stress"),
        ("Critical", "critical_ftq", "Critical", "Critical_FTQ"),
        ("Critical", "Critical_FTQ ", "Critical", "Critical_FTQ"),
        ("Critical", "lixo", "Critical", None),
        ("Critical", "Critical_FTQ", "Critical", "Critical_FTQ"),
        ("Turbulence", None, "Turbulence", None)):
    _r, _s, _n = rules.normaliza_regime(_reg_mau, _sub_mau)
    eq(_r, _quer_reg, f"normaliza_regime({_reg_mau!r}, {_sub_mau!r}) -> regime")
    eq(_s, _quer_sub, f"normaliza_regime({_reg_mau!r}, {_sub_mau!r}) -> sub")
# E as duas queixas acumulam-se: a versao anterior descartava a do sub-regime
# sempre que o regime tambem fosse ilegivel — e e esse o caso em que quem le
# mais precisa de saber das duas.
_r_d, _s_d, _n_d2 = rules.normaliza_regime("lixo", "FTQ")
# Um SUB-REGIME legivel diz, por si so, que a carteira esta em Critical: os dois
# vectores so existem la dentro. E informacao do proprio ficheiro, e vale mais
# do que uma string do regime que ninguem consegue ler — a alternativa era
# declarar o regime desconhecido e deixar que "desconhecido" passasse por calmo
# no leitor seguinte.
eq(_r_d, "Critical",
   "com um sub-regime legivel, o regime ilegivel resolve-se para Critical")
eq(_s_d, "Critical_FTQ", "e o sub-regime legivel sobrevive")
true("regime ilegivel" in _n_d2 and "critical_subregime" in _n_d2,
     f"e a nota traz as duas queixas ({_n_d2})")
# Sem sub-regime nenhum e sem mapa, continua ilegivel — nao se inventa Critical.
eq(rules.normaliza_regime("lixo", None)[0], None,
   "sem sub-regime nem mapa, o regime ilegivel continua a devolver None")

# O ilegivel devolve None, para quem chama decidir — nunca um regime inventado.
for _lixo in ("Critico", "", None, 7, [], {"a": 1}, "Resilient!"):
    _r, _s, _n = rules.normaliza_regime(_lixo)
    eq(_r, None, f"um regime ilegivel ({_lixo!r}) nao se adivinha")
    true(_n, f"e fica declarado ({_n})")
# E o mapa nunca cai no risk-on por causa de uma string.
_tick_turb2 = set(rules.REGIME_ETF_MAP["Turbulence"].values())
for _lixo in ("Critical_FTQ", "critical", "Critico", "", None, 7, []):
    _tk = set(rules.get_active_tickers(_lixo))
    true(_tk != _tick_turb2,
         f"um regime ilegivel ({_lixo!r}) nao resolve para o mapa de "
         f"Turbulence ({sorted(_tk)})")
# Os tres legitimos continuam a resolver para si proprios.
for _bom in rules.REGIMES:
    if _bom == "Critical":
        continue
    eq(rules.resolve_etf_map_key(_bom), _bom, f"{_bom} resolve para si proprio")

# E o motor: o mesmo mercado, o mesmo medidor, so a string do regime diferente.
def _corre_com_regime(reg_gravado, sub_gravado):
    tmp_ = estado(Path(tempfile.mkdtemp()), regime="Critical",
                  subregime="Critical_FTQ", com_data=True)
    _p_ = json.loads((tmp_ / "portfolio.json").read_text())
    _p_["current"]["regime"] = reg_gravado
    if sub_gravado is None:
        _p_["current"].pop("critical_subregime", None)
    else:
        _p_["current"]["critical_subregime"] = sub_gravado
    (tmp_ / "portfolio.json").write_text(json.dumps(_p_))
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d_["stressGauge"] = {"active": True, "subregime": "FTQ", "basis": "Sahm",
                          "label": "Stress ON", "triggers": {}}
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    try:
        return corre_recorrida(tmp_)
    finally:
        shutil.rmtree(tmp_, ignore_errors=True)

_pf_ref = _corre_com_regime("Critical", "Critical_FTQ")
eq(_pf_ref["current"]["critical_subregime"], "Critical_FTQ",
   "com o ficheiro em ordem, a carteira mantem-se em FTQ")
eq(_pf_ref["history"][-1]["rebalance_triggered"], False,
   f"e nao ha transaccao ({_pf_ref['history'][-1].get('rebalance_reason')})")
# O MESMO estado escrito com o vocabulario do site tem de dar o MESMO resultado.
_pf_mau = _corre_com_regime("Critical_FTQ", None)
eq(_pf_mau["current"]["critical_subregime"], "Critical_FTQ",
   f"com o regime escrito como sub-vector, a carteira continua em FTQ "
   f"({_pf_mau['current']['critical_subregime']})")
eq(_pf_mau["current"]["regime"], "Critical",
   f"e o regime gravado fica curado ({_pf_mau['current']['regime']})")
eq(_pf_mau["history"][-1]["rebalance_triggered"], False,
   f"e NAO se vende o TLT por causa de uma string "
   f"({_pf_mau['history'][-1].get('rebalance_reason')})")
eq(_pf_mau["current"]["bucket_allocation_pct"],
   _pf_ref["current"]["bucket_allocation_pct"],
   "e a alocacao e a mesma nos dois mundos")
# E o ficheiro CURA-SE: o snapshot reescrevia o valor mau semana apos semana.
true("Critical_FTQ" != _pf_mau["current"]["regime"],
     "o valor mau nao volta a ser escrito no portfolio.json")

# ── O score renormalizado de N-1 nao confirma a rotacao em N ──────────────
#
# O motor ja declara que um score sobre quatro pilares nao e o score — e um
# numero diferente — e recusa-lhe valor de regime NA SEMANA CORRENTE. Mas
# gravava-o cru no historico, e na semana seguinte, com os cinco pilares de
# volta, ele entrava na janela de confirmacao como se fosse uma leitura boa. A
# janela e a UNICA defesa de uma rotacao de 100% da carteira, e o numero que a
# confirmava nem sequer e uma leitura de mercado: com um pilar cravado no 10,0
# fora do composto, o composto desce sozinho — a mesma aritmetica que a guarda
# desta semana existe para nao confundir com um sinal.
_hoje_ce = SEXTA
_ontem_ce = (SEXTA - _td_topo(days=7)).isoformat()
def _hist_ce(**extra):
    return {"history": [{"issue": 20, "date": _ontem_ce, "mrm_score": 3.66,
                         **extra}]}
# Com a leitura anterior COMPLETA, a confirmacao acontece — e tem de acontecer,
# senao o teste abaixo passaria com um sistema que nunca entra em Resilient.
eq(up.check_emergency(_hist_ce(score_complete=True), 3.23, _hoje_ce, 21)[0], True,
   "duas leituras baixas e completas confirmam a entrada")
# Com a leitura anterior calculada sobre um composto INCOMPLETO, nao.
eq(up.check_emergency(_hist_ce(score_complete=False,
                               score_nd_pillars=["liquidity"]),
                      3.23, _hoje_ce, 21)[0], False,
   "mas uma leitura anterior sobre um composto incompleto NAO confirma nada")
# E uma entrada gravada antes de o campo existir tambem nao: nao saber nao pode
# contar como confirmado.
eq(up.check_emergency(_hist_ce(), 3.23, _hoje_ce, 21)[0], False,
   "nem uma entrada anterior ao registo da completude")
# E o produtor GRAVA mesmo o campo — senao a guarda acima ficava sempre
# vermelha e o sistema nunca mais entrava em Resilient.
_pf_ce = _corre_com_nd("Turbulence", 6.0, [])
_h_ce = _pf_ce["history"][-1]
eq(_h_ce.get("score_complete"), True,
   f"o snapshot declara o composto completo ({_h_ce.get('score_complete')})")
eq(_h_ce.get("score_nd_pillars"), [],
   f"e a lista de pilares fora ({_h_ce.get('score_nd_pillars')})")
_pf_ce_nd = _corre_com_nd("Turbulence", 6.0, ["liquidity"])
_h_ce_nd = _pf_ce_nd["history"][-1]
eq(_h_ce_nd.get("score_complete"), False,
   f"e numa semana com um pilar fora, declara-o incompleto "
   f"({_h_ce_nd.get('score_complete')})")
eq(_h_ce_nd.get("score_nd_pillars"), ["liquidity"],
   f"nomeando qual ({_h_ce_nd.get('score_nd_pillars')})")

# ── Um numero de edicao em string nao pode matar o job para sempre ────────
#
# `history.sort(key=lambda h: h.get("issue", 0))` levanta TypeError com uma
# string, e o TypeError acontece ANTES da escrita atomica: o ficheiro fica como
# estava, o job morre, a newsletter recusa-se a publicar por nao encontrar a
# edicao N, e a sexta seguinte morre da mesma maneira. Nao ha recuperacao
# automatica — o unico caminho que faria o ficheiro avancar e o que esta
# travado. E a mesma familia de tres formas (`27`, `"27"`, `27.0`) que ja foi
# fechada no `sent_issues.json`, com o mesmo normalizador.
def _corre_com_issue(forma):
    tmp_ = estado(Path(tempfile.mkdtemp()), com_data=True)
    _p_ = json.loads((tmp_ / "portfolio.json").read_text())
    if _p_["history"]:
        _p_["history"][0]["issue"] = forma
    (tmp_ / "portfolio.json").write_text(json.dumps(_p_))
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    try:
        return corre_recorrida(tmp_)
    finally:
        shutil.rmtree(tmp_, ignore_errors=True)

_pf_int = _corre_com_issue(1)
for _forma_i in ("1", " 1 ", 1.0, "#1", "lixo", None):
    _pf_i = _corre_com_issue(_forma_i)
    true(_pf_i is not None and _pf_i.get("current"),
         f"o motor sobrevive a um issue em {_forma_i!r} e escreve a carteira")
    eq(_pf_i["current"]["issue"], _pf_int["current"]["issue"],
       f"e decide a mesma semana ({_forma_i!r})")
    eq(_pf_i["current"]["shares"], _pf_int["current"]["shares"],
       f"e a mesma carteira ({_forma_i!r})")
    # E o historico fica com o campo em inteiro, ou em None se for ilegivel —
    # nunca na forma que faz a ordenacao levantar.
    for _h_i in _pf_i["history"]:
        true(_h_i.get("issue") is None or isinstance(_h_i.get("issue"), int),
             f"issue normalizado no historico ({_h_i.get('issue')!r}) com "
             f"{_forma_i!r}")
    # E o mesmo normalizador que o registo de envios usa — uma so definicao.
    eq(rules.numero_de_edicao(_forma_i),
       (1 if _forma_i in (1, "1", " 1 ", 1.0, "#1") else None),
       f"o normalizador partilhado le {_forma_i!r}")
    # E a entrada cujo numero nao se le SAI da serie — nao vai para o fim dela.
    #
    # Empurra-la para o fim punha-a exactamente em `history[-1]`, que e o
    # registo que o cartao de topo da carteira e a primeira linha do log do site
    # leem: na semana em que a carteira rodou 100% para o mapa de Critical, a
    # pagina publicava "Hold — no rebalance", o score de outra semana, e uma
    # linha `#null`. E a mesma razao por que as linhas que nao sao registos ja
    # nao pertencem a serie — a correccao anterior fechou uma ponta da lista e
    # pregou o irmao na outra.
    _ids_i = [h.get("issue") for h in _pf_i["history"]]
    true(all(isinstance(x, int) for x in _ids_i),
         f"com {_forma_i!r}: a serie so tem numeros de edicao legiveis ({_ids_i})")
    eq(_ids_i, sorted(_ids_i), f"e por ordem ({_ids_i})")
    eq(_pf_i["history"][-1].get("issue"), _pf_i["current"]["issue"],
       f"e o ultimo registo e a semana desta corrida ({_forma_i!r})")
    if rules.numero_de_edicao(_forma_i) is None:
        true(any(h.get("issue") == _forma_i
                 for h in (_pf_i.get("history_unparsed") or [])),
             f"e a entrada ilegivel e PRESERVADA fora da serie "
             f"({_forma_i!r}: {_pf_i.get('history_unparsed')})")

# ── E a guarda do benchmark nao pode ler `None` como "edicao 1" ───────────
#
# Sem `benchmark_spy_shares` no `current` — um ficheiro reconstruido a mao, ou
# escrito por uma versao anterior, que e precisamente o caso para que a guarda
# existe — o motor reconstroi o capital de arranque a partir de `history[0]`. Com
# `not in (None, 1)`, uma entrada cujo numero nao se conseguiu LER entrava como
# edicao de arranque: o benchmark e o alpha publicados saiam do preco do SPY de
# uma semana qualquer. `None` significa exactamente o contrario de "edicao 1".
def _corre_sem_benchmark(issue_da_primeira):
    tmp_ = estado(Path(tempfile.mkdtemp()), com_data=True)
    _p_ = json.loads((tmp_ / "portfolio.json").read_text())
    _p_["current"].pop("benchmark_spy_shares", None)
    # A entrada da edicao 1 e a UNICA a partir da qual o benchmark se pode
    # reconstruir. Estraga-se o numero DELA: se o motor a ler como "edicao 1",
    # reconstroi sobre a semana errada; se a puser no fim, `history[0]` passa a
    # ser outra edicao e ele tem de abortar. Qualquer dos dois e melhor do que
    # publicar um benchmark inventado, mas so um deles e o que o codigo diz.
    for _h_b in _p_.get("history") or []:
        if _h_b.get("issue") == 1:
            _h_b["issue"] = issue_da_primeira
            break
    else:
        if _p_.get("history"):
            _p_["history"][0]["issue"] = issue_da_primeira
    (tmp_ / "portfolio.json").write_text(json.dumps(_p_))
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    _issue_antes = json.loads((tmp_ / "portfolio.json").read_text())["current"]["issue"]
    try:
        return corre_recorrida(tmp_), _issue_antes
    finally:
        shutil.rmtree(tmp_, ignore_errors=True)

# Controlo: com a edicao 1 intacta, o benchmark reconstroi-se e a semana avanca.
_pf_b_ok, _iss_b_ok = _corre_sem_benchmark(1)
true(_pf_b_ok["current"].get("benchmark_spy_shares"),
     f"com a edicao 1 legivel, o benchmark reconstroi-se "
     f"({_pf_b_ok['current'].get('benchmark_spy_shares')})")
true(_pf_b_ok["current"]["issue"] > _iss_b_ok,
     "e a semana avanca")
# E com o numero da edicao 1 ilegivel, o motor ABORTA: o ficheiro fica como
# estava, sem benchmark inventado e sem a semana a avancar. `not in (None, 1)`
# aceitava `None` como "esta e a edicao 1" e reconstruia o capital de arranque
# ao preco do SPY de uma semana qualquer — benchmark e alpha errados, publicados
# aos subscritores sem um sinal.
for _forma_b in ("lixo", None, {"a": 1}, 7, "1.0"):
    _pf_b, _iss_b = _corre_sem_benchmark(_forma_b)
    eq(_pf_b["current"].get("benchmark_spy_shares"), None,
       f"com a edicao 1 em {_forma_b!r}, nao se inventa um benchmark "
       f"({_pf_b['current'].get('benchmark_spy_shares')})")
    eq(_pf_b["current"]["issue"], _iss_b,
       f"e a semana NAO avanca — o motor abortou ({_forma_b!r})")

# ── A re-corrida le o estado do HISTORICO; tambem o normaliza ─────────────
#
# A entrada do `current` ganhou a normalizacao; este ramo — o que o RUNBOOK
# manda percorrer no Caso 3, "falhou antes de enviar, re-correr o workflow" —
# lia os dois campos crus da entrada N-1 do historico. Com `"regime":
# "Critical_FTQ"` la escrito, `was_critical_last_week` ficava falso, a porta
# assimetrica via uma ENTRADA FRESCA e o motor vendia o TLT: a mesma venda de
# 35% da carteira, pela porta ao lado.
_AUSENTE = object()

def _corre_recorrida_hist(reg_no_hist, sub_no_hist):
    tmp_ = estado(Path(tempfile.mkdtemp()), regime="Critical",
                  subregime="Critical_FTQ", com_data=True)
    _p_ = json.loads((tmp_ / "portfolio.json").read_text())
    # A semana ja corrida, para entrar no ramo da re-corrida.
    _p_["history"] = [h for h in _p_["history"] if h.get("issue") not in (ISSUE - 1, ISSUE)]
    _mapa_h = rules.REGIME_ETF_MAP["Critical_FTQ"]
    _entrada_rr = {
        "issue": ISSUE - 1, "date": D_ANTERIOR, "regime": reg_no_hist,
        "critical_subregime": sub_no_hist, "active_etf_map": dict(_mapa_h),
        "rebalance_reason": "hold", "rebalance_triggered": False,
        "mrm_score": 6.9, "score_complete": True, "score_nd_pillars": [],
        "portfolio_value": 10000.0, "portfolio_pnl_pct": 0.0,
        "prices": {t_: PRECOS.get(t_, 100.0) for t_ in _mapa_h.values()},
    }
    if reg_no_hist is _AUSENTE:
        _entrada_rr.pop("regime")
    _p_["history"].append(_entrada_rr)
    _p_["history"].append({
        "issue": ISSUE, "date": str(SEXTA), "regime": "Critical",
        "critical_subregime": "Critical_FTQ", "rebalance_reason": "hold",
        "rebalance_triggered": False, "mrm_score": 6.9, "score_complete": True,
        "score_nd_pillars": [], "portfolio_value": 10000.0,
        "portfolio_pnl_pct": 0.0,
    })
    (tmp_ / "portfolio.json").write_text(json.dumps(_p_))
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d_["stressGauge"] = {"active": True, "subregime": "FTQ", "basis": "Sahm",
                          "label": "Stress ON", "triggers": {}}
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    try:
        return corre_recorrida(tmp_)
    finally:
        shutil.rmtree(tmp_, ignore_errors=True)

_pf_rr_bom = _corre_recorrida_hist("Critical", "Critical_FTQ")
# Os dois casos que faltavam: o sub-regime ausente ou ilegivel na entrada N-1,
# em que so o MAPA da entrada consegue dizer em que vector a carteira estava.
# Sem eles, a linha que le o mapa nesta ramo nao era exercitada por teste
# nenhum, e substitui-la por uma constante deixava a suite inteira verde — com
# a carteira a manter 35% em TLT numa semana em que o medidor mediu e disse que
# a duracao nao paga.
# `_AUSENTE` = a chave `regime` nem sequer existe na entrada, que e a forma
# natural de uma reconstrucao a mao. Com um default "Turbulence" a jusante, o
# `normaliza_regime` recebia um regime LEGIVEL e saia sem perguntar ao mapa.
for _reg_rr, _sub_rr in (("Critical_FTQ", None), ("critical", "FTQ"),
                         ("Critical", "FTQ"), ("Critical_FTQ", "Critical_FTQ"),
                         ("Critical", None), ("Critical", "lixo"),
                         (_AUSENTE, None), (None, None)):
    _pf_rr = _corre_recorrida_hist(_reg_rr, _sub_rr)
    eq(_pf_rr["current"]["critical_subregime"],
       _pf_rr_bom["current"]["critical_subregime"],
       f"re-corrida com ({_reg_rr!r}, {_sub_rr!r}) no historico: o mesmo "
       f"sub-regime que com o estado bem escrito")
    eq(_pf_rr["history"][-1]["rebalance_triggered"],
       _pf_rr_bom["history"][-1]["rebalance_triggered"],
       f"re-corrida com ({_reg_rr!r}, {_sub_rr!r}): a mesma decisao "
       f"({_pf_rr['history'][-1].get('rebalance_reason')})")
    eq(_pf_rr["current"]["shares"], _pf_rr_bom["current"]["shares"],
       f"re-corrida com ({_reg_rr!r}, {_sub_rr!r}): a mesma carteira")

# E com o medidor a MEDIR e a nao confirmar a descida, a re-corrida tem de
# executar a rotacao — senao os testes acima passavam com um motor que nunca
# troca de vector. A entrada N-1 diz Critical sem sub-regime legivel, e so o
# mapa dela diz que a carteira estava em FTQ.
def _corre_rr_stress(reg_no_hist, sub_no_hist):
    tmp_ = estado(Path(tempfile.mkdtemp()), regime="Critical",
                  subregime="Critical_FTQ", com_data=True)
    _p_ = json.loads((tmp_ / "portfolio.json").read_text())
    _p_["history"] = [h for h in _p_["history"] if h.get("issue") not in (ISSUE - 1, ISSUE)]
    _mapa_h = rules.REGIME_ETF_MAP["Critical_FTQ"]
    _p_["history"].append({
        "issue": ISSUE - 1, "date": D_ANTERIOR, "regime": reg_no_hist,
        "critical_subregime": sub_no_hist, "active_etf_map": dict(_mapa_h),
        "rebalance_reason": "hold", "rebalance_triggered": False,
        "mrm_score": 6.9, "score_complete": True, "score_nd_pillars": [],
        "portfolio_value": 10000.0, "portfolio_pnl_pct": 0.0,
        "prices": {t_: PRECOS.get(t_, 100.0) for t_ in _mapa_h.values()},
    })
    _p_["history"].append({
        "issue": ISSUE, "date": str(SEXTA), "regime": "Critical",
        "critical_subregime": "Critical_FTQ", "rebalance_reason": "hold",
        "rebalance_triggered": False, "mrm_score": 6.9, "score_complete": True,
        "score_nd_pillars": [], "portfolio_value": 10000.0,
        "portfolio_pnl_pct": 0.0,
    })
    (tmp_ / "portfolio.json").write_text(json.dumps(_p_))
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d_["stressGauge"] = {"active": True, "subregime": "STRESS", "basis": "Sahm",
                          "label": "Stress ON", "triggers": {}}
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    try:
        return corre_recorrida(tmp_)
    finally:
        shutil.rmtree(tmp_, ignore_errors=True)

for _reg_s, _sub_s in (("Critical", "Critical_FTQ"), ("Critical", None),
                       ("Critical", "lixo"), ("Critical_FTQ", None)):
    _pf_s2 = _corre_rr_stress(_reg_s, _sub_s)
    eq(_pf_s2["current"]["critical_subregime"], "Critical_Stress",
       f"re-corrida com ({_reg_s!r}, {_sub_s!r}) e o 10Y medido sem descida: "
       f"degrada para Stress ({_pf_s2['current']['critical_subregime']})")
    eq(_pf_s2["history"][-1]["rebalance_triggered"], True,
       f"e a rotacao EXECUTA-SE ({_reg_s!r}, {_sub_s!r}: "
       f"{_pf_s2['history'][-1].get('rebalance_reason')})")
    true("Critical_FTQ" in str(_pf_s2["history"][-1].get("rebalance_reason")),
         f"e o motivo publicado diz de ONDE se saiu, que so o mapa sabia "
         f"({_pf_s2['history'][-1].get('rebalance_reason')})")

# E quando nem o campo nem o mapa da entrada N-1 identificam o vector, o lado
# por omissao e o DEFENSIVO — e uma troca publicada nunca pode dizer que veio de
# "none". Sem isto, `was_subregime` ficava None, `decide_rebalance` via uma
# troca a partir do nada e o motor COMPRAVA o TLT: 35% da carteira, sem que
# nenhuma leitura o tivesse pedido.
def _corre_rr_sem_mapa(gauge_sub):
    tmp_ = estado(Path(tempfile.mkdtemp()), regime="Critical",
                  subregime="Critical_Stress", com_data=True)
    _p_ = json.loads((tmp_ / "portfolio.json").read_text())
    _p_["history"] = [h for h in _p_["history"] if h.get("issue") not in (ISSUE - 1, ISSUE)]
    _p_["history"].append({
        "issue": ISSUE - 1, "date": D_ANTERIOR, "regime": "Critical",
        "critical_subregime": "lixo",
        # O mapa da ENTRADA nao identifica nenhum dos dois vectores.
        "active_etf_map": dict(rules.REGIME_ETF_MAP["Turbulence"]),
        "rebalance_reason": "hold", "rebalance_triggered": False,
        "mrm_score": 6.9, "score_complete": True, "score_nd_pillars": [],
        "portfolio_value": 10000.0, "portfolio_pnl_pct": 0.0,
    })
    _p_["history"].append({
        "issue": ISSUE, "date": str(SEXTA), "regime": "Critical",
        "critical_subregime": "Critical_Stress", "rebalance_reason": "hold",
        "rebalance_triggered": False, "mrm_score": 6.9, "score_complete": True,
        "score_nd_pillars": [], "portfolio_value": 10000.0,
        "portfolio_pnl_pct": 0.0,
    })
    (tmp_ / "portfolio.json").write_text(json.dumps(_p_))
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d_["stressGauge"] = {"active": True, "subregime": gauge_sub, "basis": "Sahm",
                          "label": "Stress ON", "triggers": {}}
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    try:
        return corre_recorrida(tmp_)
    finally:
        shutil.rmtree(tmp_, ignore_errors=True)

# Sem descida confirmada: fica no defensivo e nao ha troca nenhuma.
_pf_sm = _corre_rr_sem_mapa("STRESS")
eq(_pf_sm["current"]["critical_subregime"], "Critical_Stress",
   f"sem campo nem mapa, o anterior e o DEFENSIVO "
   f"({_pf_sm['current']['critical_subregime']})")
eq(_pf_sm["history"][-1]["rebalance_triggered"], False,
   f"e nao ha troca nenhuma a partir do nada "
   f"({_pf_sm['history'][-1].get('rebalance_reason')})")
# E com a descida confirmada, a troca acontece — mas dizendo de onde veio.
_pf_sm2 = _corre_rr_sem_mapa("FTQ")
_rz_sm = str(_pf_sm2["history"][-1].get("rebalance_reason") or "")
true("none" not in _rz_sm,
     f"uma troca publicada nunca vem de 'none' ({_rz_sm})")

# ── O regime ilegivel tambem pergunta ao MAPA que a carteira detem ────────
#
# O principio ja estava escrito para o sub-regime: "um sub-regime PERDIDO le-se
# do mapa de ETFs que a carteira detem — o mapa esta escrito no ficheiro, nao ha
# nada para adivinhar". So que se aplicava a UM dos dois campos. Com o campo do
# regime mal escrito, o motor declarava Turbulence sem perguntar nada ao mapa:
# com o medidor ON, `was_critical_last_week` ficava falso, a porta assimetrica
# via uma entrada fresca e vendiam-se 35% da carteira; com o medidor calmo, nao
# havia gatilho nenhum e a edicao publicava aos subscritores os ETFs de
# Turbulence sobre uma carteira que detem os de Critical — semana apos semana,
# ate ao semestral.
_mapa_ftq = rules.REGIME_ETF_MAP["Critical_FTQ"]
for _reg_x, _quer_r, _quer_s in (
        ("FTQ", "Critical", "Critical_FTQ"),
        ("STRESS", "Critical", "Critical_Stress"),
        ("Crisis", "Critical", "Critical_FTQ"),
        ("critical-ftq", "Critical", "Critical_FTQ"),
        ("", "Critical", "Critical_FTQ"),
        (None, "Critical", "Critical_FTQ")):
    _r_x, _s_x, _n_x = rules.normaliza_regime(_reg_x, None, _mapa_ftq)
    eq(_r_x, _quer_r, f"normaliza_regime({_reg_x!r}) com o mapa FTQ -> regime")
    eq(_s_x, _quer_s, f"normaliza_regime({_reg_x!r}) com o mapa FTQ -> sub")
# E sem mapa que identifique nada, continua ilegivel — nao se inventa Critical.
for _reg_x in ("Crisis", "", None, 7):
    eq(rules.normaliza_regime(_reg_x, None,
                              rules.REGIME_ETF_MAP["Turbulence"])[0], None,
       f"com o mapa de Turbulence, {_reg_x!r} continua ilegivel")
    eq(rules.normaliza_regime(_reg_x, None, None)[0], None,
       f"e sem mapa nenhum tambem ({_reg_x!r})")
# E um regime BEM escrito nao e sobreposto pelo mapa.
eq(rules.normaliza_regime("Turbulence", None, _mapa_ftq)[0], "Turbulence",
   "um regime legivel manda sobre o mapa")

# Ao nivel do motor: o mesmo mercado, so a string do regime diferente.
def _corre_regime_mapa(reg_gravado):
    tmp_ = estado(Path(tempfile.mkdtemp()), regime="Critical",
                  subregime="Critical_FTQ", com_data=True)
    _p_ = json.loads((tmp_ / "portfolio.json").read_text())
    _p_["current"]["regime"] = reg_gravado
    _p_["current"].pop("critical_subregime", None)
    (tmp_ / "portfolio.json").write_text(json.dumps(_p_))
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d_["stressGauge"] = {"active": True, "subregime": "FTQ", "basis": "Sahm",
                          "label": "Stress ON", "triggers": {}}
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    try:
        return corre_recorrida(tmp_)
    finally:
        shutil.rmtree(tmp_, ignore_errors=True)

_pf_rm_ok = _corre_regime_mapa("Critical")
for _reg_x in ("FTQ", "Crisis", "critical-ftq", "", None):
    _pf_rm = _corre_regime_mapa(_reg_x)
    eq(_pf_rm["current"]["regime"], "Critical",
       f"com {_reg_x!r} no campo e o mapa de Critical, a carteira esta em "
       f"Critical ({_pf_rm['current']['regime']})")
    eq(_pf_rm["current"]["critical_subregime"],
       _pf_rm_ok["current"]["critical_subregime"],
       f"e no mesmo vector que com o campo bem escrito ({_reg_x!r})")
    eq(_pf_rm["current"]["shares"], _pf_rm_ok["current"]["shares"],
       f"e a mesma carteira ({_reg_x!r}) — a grafia nao vende o TLT")
    eq(_pf_rm["history"][-1]["rebalance_triggered"], False,
       f"e sem transaccao ({_reg_x!r}: "
       f"{_pf_rm['history'][-1].get('rebalance_reason')})")
    eq(set(_pf_rm["current"]["active_etf_map"].values()),
       set(_mapa_ftq.values()),
       f"e o mapa publicado e o que a carteira detem ({_reg_x!r})")

# ── Uma entrada do historico que NAO e um registo nao mata o job ──────────
#
# A guarda `isinstance(h, dict)` existia num sitio so — o normalizador do numero
# de edicao — e cinco leitores a jusante fazem `h.get(...)` sem ela. Uma linha
# de texto escrita a mao numa recuperacao (a forma que o registo de envios
# PRESERVA de proposito) levantava AttributeError ANTES da escrita atomica: o
# ficheiro ficava como estava, o job morria, o envio era saltado, e a sexta
# seguinte morria da mesma maneira. E o mesmo defeito que o `"issue": "26"`.
def _corre_com_solta(entrada):
    tmp_ = estado(Path(tempfile.mkdtemp()), com_data=True)
    _p_ = json.loads((tmp_ / "portfolio.json").read_text())
    _p_["history"].insert(1, entrada)
    (tmp_ / "portfolio.json").write_text(json.dumps(_p_))
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    _issue_antes = _p_["current"]["issue"]
    try:
        return corre_recorrida(tmp_), _issue_antes
    finally:
        shutil.rmtree(tmp_, ignore_errors=True)

for _solta in ("nota: reconstruido a mao em 2026-09-12", None, ["issue", 26], 26):
    _pf_s, _iss_s = _corre_com_solta(_solta)
    true(_pf_s["current"]["issue"] > _iss_s,
         f"com {_solta!r} no historico, a semana AVANCA na mesma "
         f"({_pf_s['current']['issue']} vs {_iss_s})")
    true(all(isinstance(h, dict) for h in _pf_s["history"]),
         f"o `history` publicado so tem REGISTOS — e a serie que o site "
         f"desenha, e o `history[-1]` e o cartao de topo da carteira "
         f"({_solta!r})")
    true(_solta in (_pf_s.get("history_unparsed") or []),
         f"e a linha solta e PRESERVADA num campo proprio, nao apagada em "
         f"silencio ({_solta!r}: {_pf_s.get('history_unparsed')})")
    # E o ultimo registo do historico e a semana que acabou de correr — nao uma
    # linha fantasma que fazia o site publicar "Hold — no rebalance" na semana
    # em que a carteira rodou inteira.
    eq(_pf_s["history"][-1].get("issue"), _pf_s["current"]["issue"],
       f"e o ultimo registo e a semana desta corrida ({_solta!r})")
    # E na SEXTA SEGUINTE a linha continua la. A assercao anterior re-lia o
    # mesmo objecto em memoria; a propriedade e sobre uma SEGUNDA corrida, e o
    # que a garante e o motor voltar a ler o `history_unparsed` que ele proprio
    # escreveu. Sem isso, a primeira sexta preservava e a segunda apagava em
    # silencio — que e pior do que nao preservar, porque parece que preserva.
    _tmp2 = Path(tempfile.mkdtemp())
    try:
        _tmp2b = estado(_tmp2, com_data=True)
        (_tmp2b / "portfolio.json").write_text(json.dumps(_pf_s))
        _d2 = json.loads((_tmp2b / "data.json").read_text())
        _d2.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        (_tmp2b / "data.json").write_text(json.dumps(_d2))
        _pf_s2 = corre_recorrida(_tmp2b)
        true(_solta in (_pf_s2.get("history_unparsed") or []),
             f"a linha solta sobrevive a SEGUNDA corrida ({_solta!r}: "
             f"{_pf_s2.get('history_unparsed')})")
        true(all(isinstance(h, dict) for h in _pf_s2["history"]),
             "e a serie continua so com registos")
        # E NAO se duplica. O ficheiro e commitado para `main` e servido em
        # usmrm.net: uma entrada por semana, para sempre, com a suite verde. A
        # propriedade e idempotencia, nao presenca.
        eq(_pf_s2.get("history_unparsed"), _pf_s.get("history_unparsed"),
           f"e o `history_unparsed` e IGUAL ao da corrida anterior — nao cresce "
           f"uma entrada por semana ({_pf_s2.get('history_unparsed')})")
    finally:
        shutil.rmtree(_tmp2, ignore_errors=True)

# ── 18b-bis. E a PROPRIEDADE, nao o valor da constante ─────────────────────
#
# As duas asserccoes acima falam do NUMERO. Apagar a guarda que o usa deixava-as
# verdes: o motor dimensionava 35% da carteira num ticker novo ao ultimo preco
# conhecido com dois meses, e publicava a semana com `valuation_complete: true`
# e zero avisos. A propriedade e: um recurso mais velho do que o limite nao
# entra numa transaccao, e a semana DECLARA-O.
def _corre_com_recurso_velho(idade_dias):
    tmp_ = estado(Path(tempfile.mkdtemp()), regime="Critical",
                  subregime="Critical_Stress", com_data=True)
    _p_ = json.loads((tmp_ / "portfolio.json").read_text())
    # O TLT e o ticker NOVO: a carteira em Stress nao o detem, e o medidor vai
    # mandar entrar em FTQ. A cotacao dele falha; o ultimo conhecido e velho.
    _p_["current"]["last_prices"] = dict(_p_["current"].get("last_prices") or {})
    _p_["current"]["last_prices"]["TLT"] = 130.0
    _p_["current"]["last_price_dates"] = dict(_p_["current"].get("last_price_dates") or {})
    _p_["current"]["last_price_dates"]["TLT"] = str(SEXTA - _td_topo(days=idade_dias))
    (tmp_ / "portfolio.json").write_text(json.dumps(_p_))
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d_["stressGauge"] = {"active": True, "subregime": "FTQ", "basis": "Sahm",
                          "label": "Stress ON", "triggers": {}}
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    try:
        return corre_recorrida(tmp_, sem_cotacao=("TLT",))
    finally:
        shutil.rmtree(tmp_, ignore_errors=True)

# Recurso RECENTE (uma semana): serve, e a transaccao acontece — este e o
# contraste que impede o teste de baixo de passar com um motor que nunca troca.
_pf_rec = _corre_com_recurso_velho(7)
eq(_pf_rec["history"][-1]["rebalance_triggered"], True,
   f"com um recurso de 7 dias, a rotacao executa-se "
   f"({_pf_rec['history'][-1].get('rebalance_reason')})")
true(_pf_rec["current"]["shares"].get("TLT"),
     f"e o ticker novo entra na carteira "
     f"({_pf_rec['current']['shares'].get('TLT')})")
# Recurso VELHO (dois meses): nao entra na transaccao.
_pf_vel = _corre_com_recurso_velho(60)
_h_vel = _pf_vel["history"][-1]
eq(_h_vel["rebalance_triggered"], False,
   f"um recurso de 60 dias NAO dimensiona uma transaccao "
   f"({_h_vel.get('rebalance_reason')})")
true(_h_vel.get("rebalance_reason") in ("missing_prices_held",
                                        "no_allocation_available",
                                        "aborted_invalid_shares"),
     f"e a semana diz porque manteve as posicoes ({_h_vel.get('rebalance_reason')})")
eq(_pf_vel["current"]["shares"].get("TLT"), None,
   f"e nao se compra o ticker novo a um preco de ha dois meses "
   f"({_pf_vel['current']['shares'].get('TLT')})")

# ── 18b-quater. E o VALOR da constante tambem tem de ser defensavel ───────
#
# Um ensaio derivado da constante acompanha-a: deslocar o numero nao faz cair
# nada, e nem deve — o que tem de estar preso e o intervalo em que ele faz
# sentido, que sai da cadencia do sistema. Com a decisao a ser semanal, um
# limite de sete dias ou mais deixaria passar o fecho da semana ANTERIOR como se
# fosse o desta.
true(0 < up.MAX_STALE_DAYS < 7,
     f"o limite do fecho fresco cabe dentro de uma semana "
     f"({up.MAX_STALE_DAYS}) — com sete ou mais, o fecho da semana passada "
     f"passava por leitura desta")
true(up.MAX_STALE_DAYS >= 3,
     f"e chega para um fim-de-semana prolongado ({up.MAX_STALE_DAYS})")
true(up.MAX_STALE_DAYS < up.FALLBACK_MAX_AGE_DAYS,
     f"e o fecho fresco e mais exigente do que o recurso "
     f"({up.MAX_STALE_DAYS} < {up.FALLBACK_MAX_AGE_DAYS}) — senao o recurso "
     f"nunca seria usado")

# ── 18b-ter. O `fetch_prices` REAL recusa um fecho velho demais ────────────
#
# Ele e substituido por um lambda em TODA a suite: o unico produtor de precos do
# sistema nunca corria, e as guardas dele nao tinham teste nenhum — so uma
# assercao sobre o VALOR de uma constante. Aqui corre, contra um `yf` de teste
# que reproduz o minimo do pandas que ele usa.
class _IndiceFalso(list):
    # `hist.index = hist.index.date` — no pandas `.date` e uma PROPRIEDADE que
    # devolve as datas sem hora. Como metodo, o `in` a seguir opera sobre um
    # metodo e levanta, que e indistinguivel de uma cotacao falhada.
    @property
    def date(self):
        return _IndiceFalso(self)

class _DFFalso:
    """`.empty`, `.index` (com `.date`) e `.loc[data]["Close"]` — o que o
    `fetch_prices` toca, e mais nada."""
    def __init__(self, datas_valores):
        self._v = dict(datas_valores)
        self.empty = not datas_valores
        self.index = _IndiceFalso(d for d, _ in datas_valores)
    @property
    def loc(self):
        v = self._v
        class _L:
            def __getitem__(self, d):
                return {"Close": v[d]}
        return _L()

def _yf_falso(datas_valores):
    class _T:
        def __init__(self, *a, **k): pass
        def history(self, start=None, end=None):
            return _DFFalso(datas_valores)
    class _YF:
        Ticker = _T
    return _YF

_yf_guard = up.yf
try:
    _alvo_fp = SEXTA
    # As tres leituras da FRONTEIRA, derivadas da constante: `limiar` ainda
    # serve, `limiar + 1` ja nao. Sem o limite exacto, `>` trocado por `>=`
    # sobrevivia.
    for _idade_fp, _serve in ((0, True), (up.MAX_STALE_DAYS - 1, True),
                              (up.MAX_STALE_DAYS, True),
                              (up.MAX_STALE_DAYS + 1, False),
                              (up.MAX_STALE_DAYS + 3, False)):
        _d_fp = _alvo_fp - _td_topo(days=_idade_fp)
        up.yf = _yf_falso([(_d_fp, 100.0)])
        _pr, _dt, _st = up.fetch_prices(["LQD"], _alvo_fp, retries=1)
        if _serve:
            eq(_pr["LQD"], 100.0,
               f"um fecho de ha {_idade_fp} dias serve ({_pr['LQD']})")
            eq(_st["LQD"], False, f"e nao se declara velho ({_idade_fp} dias)")
            eq(_dt["LQD"], str(_d_fp), "com a data do fecho que foi usado")
        else:
            eq(_pr["LQD"], None,
               f"um fecho de ha {_idade_fp} dias (limite {up.MAX_STALE_DAYS}) "
               f"NAO passa por preco desta semana ({_pr['LQD']})")
            eq(_st["LQD"], True, "e a corrida declara-o velho")
    # E um preco absurdo continua a ser tratado como falha, nao como valor.
    for _mau_fp in (0.0, -3.0, float("nan")):
        up.yf = _yf_falso([(_alvo_fp, _mau_fp)])
        _pr2, _dt2, _st2 = up.fetch_prices(["LQD"], _alvo_fp, retries=1)
        eq(_pr2["LQD"], None,
           f"um preco {_mau_fp!r} e falha de cotacao, nao um valor ({_pr2['LQD']})")
    # E sem observacoes nenhumas, tambem.
    up.yf = _yf_falso([])
    _pr3, _dt3, _st3 = up.fetch_prices(["LQD"], _alvo_fp, retries=1)
    eq(_pr3["LQD"], None, "sem observacoes nao ha preco")
finally:
    up.yf = _yf_guard

# ── As fronteiras dos limiares do MOTOR, derivadas das constantes ─────────
#
# Trocar `<=` por `<` (ou `>` por `>=`) sobrevivia a suite inteira em todos
# eles: um piso escrito como `<= X` cujo ensaio nunca passa por X e um piso por
# afirmar, e a fronteira e precisamente o que a regra publicada promete. As tres
# leituras saem da constante, nao de um numero escrito a mao ao lado dela.
_lim_em = up.EMERGENCY_SCORE_LOW
_hoje_lim = SEXTA
_ant_lim = (SEXTA - _td_topo(days=7)).isoformat()
def _hist_lim(s):
    return {"history": [{"issue": 20, "date": _ant_lim, "mrm_score": s,
                         "score_complete": True}]}
for _delta_lim, _quer_lim in ((-0.1, True), (0.0, True), (0.1, False)):
    _s_lim = round(_lim_em + _delta_lim, 4)
    eq(up.check_emergency(_hist_lim(_s_lim), _s_lim, _hoje_lim, 21)[0], _quer_lim,
       f"score {_s_lim} (limiar {_lim_em}): entrada confirmada={_quer_lim} — "
       f"a regra publicada e 'score <= {_lim_em}', e o limite EXACTO conta")
# E a assimetria: basta uma das duas leituras estar acima para nao confirmar.
eq(up.check_emergency(_hist_lim(_lim_em), _lim_em + 0.1, _hoje_lim, 21)[0], False,
   "uma leitura acima do limiar chega para nao confirmar")
eq(up.check_emergency(_hist_lim(_lim_em + 0.1), _lim_em, _hoje_lim, 21)[0], False,
   "e a anterior tambem")

# A idade do data.json: as tres leituras saem do PRODUTOR, nao de uma expressao
# re-implementada no teste. Calcular a comparacao aqui deixava as mutacoes de
# `>` para `>=` vivas — o teste media a sua propria aritmetica, nao a do motor.
_agora_lim = datetime(2026, 9, 11, 22, 0, 0)
def _ficheiro_com_idade(horas):
    _tmpx = Path(tempfile.mkdtemp())
    _g = _agora_lim - _td_topo(hours=horas)
    _doc = {"meta": {"generatedAt": _g.strftime("%Y-%m-%dT%H:%M:%SZ")},
            "globalResilienceScore": 6.0, "ndPillars": [],
            "stressGauge": {"active": False, "subregime": None, "basis": "x",
                            "triggers": {}}}
    (_tmpx / "data.json").write_text(json.dumps(_doc))
    return _tmpx
for _h_lim, _recusa in ((up.DATA_REFUSE_AFTER_HOURS - 0.02, False),
                        (up.DATA_REFUSE_AFTER_HOURS, False),
                        (up.DATA_REFUSE_AFTER_HOURS + 0.02, True)):
    _tx = _ficheiro_com_idade(_h_lim)
    try:
        _doc_lim, _mot_lim = up._read_data_doc(_tx / "data.json", _agora_lim)
        eq(up.DATA_REFUSED["value"], _recusa,
           f"um data.json com {_h_lim}h (limite {up.DATA_REFUSE_AFTER_HOURS}h): "
           f"recusado={_recusa} — o limite EXACTO ainda serve ({_mot_lim})")
        eq(_doc_lim is None, _recusa,
           f"e o documento so se devolve quando nao foi recusado ({_h_lim}h)")
    finally:
        shutil.rmtree(_tx, ignore_errors=True)
# E o AVISO tem a sua propria fronteira, tambem lida do produtor.
for _h_av, _avisa in ((up.DATA_WARN_AFTER_HOURS - 0.02, False),
                      (up.DATA_WARN_AFTER_HOURS, False),
                      (up.DATA_WARN_AFTER_HOURS + 0.02, True)):
    _tx = _ficheiro_com_idade(_h_av)
    try:
        import logging as _lg_av
        _apanhados = []
        class _H(_lg_av.Handler):
            def emit(self, r):
                _apanhados.append(r.getMessage())
        _h_obj = _H(); _h_obj.setLevel(_lg_av.WARNING)
        up.log.addHandler(_h_obj)
        _lg_av.disable(_lg_av.NOTSET)
        try:
            up._read_data_doc(_tx / "data.json", _agora_lim)
        finally:
            up.log.removeHandler(_h_obj)
        eq(any("aviso acima de" in m for m in _apanhados), _avisa,
           f"um data.json com {_h_av}h (aviso acima de "
           f"{up.DATA_WARN_AFTER_HOURS}h): avisado={_avisa}")
    finally:
        shutil.rmtree(_tx, ignore_errors=True)

# E a fronteira do recurso de preco, medida NO MOTOR — o `_corre_com_recurso_velho`
# ja corre o pipeline inteiro; faltava chama-lo nas tres leituras da fronteira.
for _idade_fb, _serve_fb in ((up.FALLBACK_MAX_AGE_DAYS - 1, True),
                             (up.FALLBACK_MAX_AGE_DAYS, True),
                             (up.FALLBACK_MAX_AGE_DAYS + 1, False)):
    _pf_fb = _corre_com_recurso_velho(_idade_fb)
    eq(_pf_fb["history"][-1]["rebalance_triggered"], _serve_fb,
       f"um recurso de {_idade_fb} dias (limite {up.FALLBACK_MAX_AGE_DAYS}): "
       f"dimensiona={_serve_fb} — o limite EXACTO ainda serve "
       f"({_pf_fb['history'][-1].get('rebalance_reason')})")
    eq(_pf_fb["current"]["shares"].get("TLT") is not None, _serve_fb,
       f"e o ticker novo entra ou nao ({_idade_fb} dias)")

# ── E a chave `regime` AUSENTE nao e um regime legivel ────────────────────
#
# `current.get("regime", "Turbulence")` devolvia um regime LEGIVEL para uma
# chave que nao existe — a forma natural de uma reconstrucao a mao, e o que uma
# versao anterior escrevia — e o `normaliza_regime` saia no primeiro ramo sem
# chegar a perguntar ao mapa. A carteira detinha TLT, o motor via uma entrada
# fresca em Critical, forcava o lado defensivo e VENDIA 35% dela na semana em
# que o medidor confirmou a descida do 10Y. O ensaio anterior escrevia
# `regime: None` COM a chave presente, que e o unico caso que o default nao
# apanha.
def _corre_sem_chave_regime(remover):
    tmp_ = estado(Path(tempfile.mkdtemp()), regime="Critical",
                  subregime="Critical_FTQ", com_data=True)
    _p_ = json.loads((tmp_ / "portfolio.json").read_text())
    if remover:
        _p_["current"].pop("regime", None)
    else:
        _p_["current"]["regime"] = None
    _p_["current"].pop("critical_subregime", None)
    (tmp_ / "portfolio.json").write_text(json.dumps(_p_))
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d_["stressGauge"] = {"active": True, "subregime": "FTQ", "basis": "Sahm",
                          "label": "Stress ON", "triggers": {}}
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    try:
        return corre_recorrida(tmp_)
    finally:
        shutil.rmtree(tmp_, ignore_errors=True)

_pf_nulo = _corre_sem_chave_regime(False)
_pf_falta = _corre_sem_chave_regime(True)
for _nome_ck, _pf_ck in (("regime: null", _pf_nulo), ("chave ausente", _pf_falta)):
    eq(_pf_ck["current"]["regime"], "Critical",
       f"{_nome_ck}: o mapa detido diz que a carteira esta em Critical")
    eq(_pf_ck["current"]["critical_subregime"], "Critical_FTQ",
       f"{_nome_ck}: e em que vector ({_pf_ck['current']['critical_subregime']})")
    eq(_pf_ck["history"][-1]["rebalance_triggered"], False,
       f"{_nome_ck}: e NAO se vende o TLT "
       f"({_pf_ck['history'][-1].get('rebalance_reason')})")
eq(_pf_nulo["current"]["shares"], _pf_falta["current"]["shares"],
   "e as duas formas de nao dizer o regime dao a mesma carteira")

# ── A guarda de data: a unica linha do motor que nenhum teste executava ───
#
# Todos os ajudantes que correm o motor poem `FORCE_REBALANCE = True`, portanto
# `if not FORCE_REBALANCE and current["date"] >= str(target_date)` nunca corria
# — nem no ramo verdadeiro nem no falso. Apaga-la por completo, trocar `>=` por
# `>` (que a desliga, porque as duas datas sao ISO e a igualdade e o unico caso
# que ela apanha) ou fazer sair por 1 em vez de 0: as tres deixavam a suite
# inteira verde.
#
# E o `sys.exit(1)` e a pior das tres: o RUNBOOK manda re-correr o workflow no
# Caso 2 (entrega incompleta) e no Caso 3. Com a guarda a sair vermelha, o
# `update-portfolio` fica vermelho, o `send-newsletter` e saltado por `needs:`,
# e quem ficou por servir NUNCA e servido — em nenhuma re-corrida, porque o
# unico caminho que faria a semana avancar e o que esta travado.
def _corre_sem_forcar(data_no_current):
    tmp_ = estado(Path(tempfile.mkdtemp()), com_data=True)
    _p_ = json.loads((tmp_ / "portfolio.json").read_text())
    _p_["current"]["date"] = data_no_current
    (tmp_ / "portfolio.json").write_text(json.dumps(_p_))
    _d_ = json.loads((tmp_ / "data.json").read_text())
    _d_.setdefault("meta", {})["generatedAt"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_ / "data.json").write_text(json.dumps(_d_))
    _antes = (tmp_ / "portfolio.json").read_text()
    _guard = (up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
              up.FORCE_REBALANCE)
    up.fetch_prices = lambda t_, d_, retries=3: (
        {x: PRECOS.get(x, 100.0) for x in t_}, {x: str(d_) for x in t_},
        {x: False for x in t_})
    up.get_last_friday = lambda: SEXTA
    up.adjust_for_market_holiday = lambda d_: d_
    up.FORCE_REBALANCE = False          # <- a guarda CORRE
    _cwd = os.getcwd(); os.chdir(tmp_)
    logging.disable(logging.CRITICAL)
    _codigo = 0
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                up.main()
            except SystemExit as e_:
                _codigo = e_.code if e_.code is not None else 0
        return _codigo, _antes, (tmp_ / "portfolio.json").read_text()
    finally:
        logging.disable(logging.NOTSET)
        os.chdir(_cwd)
        (up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
         up.FORCE_REBALANCE) = _guard
        shutil.rmtree(tmp_, ignore_errors=True)

# Semana JA decidida: sai por 0 e nao toca no ficheiro.
_cod_ig, _antes_ig, _depois_ig = _corre_sem_forcar(str(SEXTA))
eq(_cod_ig, 0,
   f"com a semana ja decidida, o motor sai por 0 — sair por 1 poria o job "
   f"vermelho e o envio seria saltado por `needs:`, e a re-corrida que o "
   f"RUNBOOK manda fazer nunca serviria quem ficou por servir (codigo {_cod_ig})")
eq(_depois_ig, _antes_ig,
   "e nao reescreve o portfolio.json — a decisao da semana nao se repete")
# Uma data MAIS RECENTE do que o alvo: idem.
_cod_fut, _antes_fut, _depois_fut = _corre_sem_forcar(
    (SEXTA + _td_topo(days=7)).isoformat())
eq(_cod_fut, 0, "com uma data a frente do alvo, tambem sai por 0")
eq(_depois_fut, _antes_fut, "e tambem nao reescreve nada")
# E uma semana por decidir: a guarda deixa passar e o motor decide.
_cod_nova, _antes_nova, _depois_nova = _corre_sem_forcar(
    (SEXTA - _td_topo(days=7)).isoformat())
eq(_cod_nova, 0, "com a semana por decidir, o motor corre ate ao fim")
true(_depois_nova != _antes_nova,
     "e ESCREVE a carteira — senao a guarda estaria a travar tudo")
eq(json.loads(_depois_nova)["current"]["date"], str(SEXTA),
   "e a semana avanca para o alvo")


# ── A idade de um preco congelado sai do MOTOR, nao de uma fixture ────────
#
# O limiar `PRICE_FROZEN_AFTER_DAYS` passou a ter consumidor (dois avisos
# diferentes na edicao), mas o teste desse consumidor construia a mao os campos
# que o motor publica — a forma invertida do defeito: prova o consumidor e
# deixa o PRODUTOR por armar. A mutacao 21 -> 210 continuava a sobreviver.
# Aqui e o motor a decidir, com um instrumento cujo feed morreu.
# Abaixo de `FALLBACK_MAX_AGE_DAYS` o recurso ainda serve e o instrumento nem
# chega a ser declarado congelado — por isso as idades ensaiadas comecam acima
# desse limite, que e onde a lista existe.
for _idade_p, _quer_p in ((11, False), (21, False), (22, True), (300, True)):
    _tmp_p = estado(Path(tempfile.mkdtemp()), com_data=True)
    _pf_p = json.loads((_tmp_p / "portfolio.json").read_text())
    _det_p = sorted(k for k, v in (_pf_p["current"].get("shares") or {}).items() if v)[0]
    # O feed deste instrumento morreu ha `_idade_p` dias: nao ha cotacao, e o
    # unico preco conhecido tem essa idade. E o ETF renomeado ou retirado de
    # bolsa, que e o caso para que o limiar foi escrito.
    _pf_p["current"].setdefault("last_price_dates", {})[_det_p] = \
        (SEXTA - _td_topo(days=_idade_p)).isoformat()
    _pf_p["current"].setdefault("last_prices", {})[_det_p] = PRECOS.get(_det_p, 100.0)
    (_tmp_p / "portfolio.json").write_text(json.dumps(_pf_p))
    # E uma semana com GATILHO: sem trigger nao ha rebalanceamento para travar,
    # e a guarda ficaria verde por nunca ser exercitada. O medidor B liga, o
    # regime vai a Critical e a carteira TERIA de rodar.
    _d_p = json.loads((_tmp_p / "data.json").read_text())
    _d_p.setdefault("meta", {})["generatedAt"] = \
        datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _d_p["stressGauge"] = {"active": True, "subregime": "STRESS", "basis": "Sahm",
                           "label": "Stress ON — No Relief", "triggers": {}}
    (_tmp_p / "data.json").write_text(json.dumps(_d_p))
    _res_p = corre_recorrida(_tmp_p, sem_cotacao=(_det_p,))
    _h_p = _res_p["history"][-1]
    true(_det_p in (_h_p.get("valuation_frozen") or []),
         f"com o feed morto ha {_idade_p} dias, o {_det_p} fica congelado "
         f"({_h_p.get('valuation_frozen')})")
    eq((_h_p.get("valuation_frozen_days") or {}).get(_det_p), _idade_p,
       f"e a IDADE e publicada ({_h_p.get('valuation_frozen_days')})")
    # E a DATA tambem: e ela que vai para o aviso copiado palavra por palavra
    # pelo modelo, porque a idade muda todas as semanas e uma frase nova todas
    # as semanas e `FALHA_FORMA` — nenhuma edicao, durante a avaria.
    eq((_h_p.get("valuation_frozen_dates") or {}).get(_det_p),
       (SEXTA - _td_topo(days=_idade_p)).isoformat(),
       f"e a DATA do ultimo preco e publicada "
       f"({_h_p.get('valuation_frozen_dates')})")
    eq(_det_p in (_h_p.get("valuation_not_credible") or []), _quer_p,
       f"congelado ha {_idade_p} dias (limite {up.PRICE_FROZEN_AFTER_DAYS}): "
       f"{'ja NAO e credivel' if _quer_p else 'ainda e aproximacao'} "
       f"({_h_p.get('valuation_not_credible')})")
    eq(_h_p.get("price_frozen_after_days"), 21,
       "e o limiar contra o qual isso foi decidido vai no ficheiro, para o "
       "gerador nao ter de o adivinhar")
    # E o veredicto MUDA O COMPORTAMENTO. A guarda existia so para o irmao
    # (`valuation_missing` cancela o rebalanceamento, com a razao escrita a
    # mao: "dimensionar posicoes sobre um valor incompleto perderia esse
    # capital"). Essa frase aplica-se palavra por palavra a um preco congelado
    # ha 300 dias, e nada travava esse caminho: o sistema publicava na edicao
    # "this week's profit and loss is not credible" e LIQUIDAVA a posicao ao
    # preco morto na mesma corrida, pagando custos reais. Um veredicto
    # publicado que nao muda o comportamento e pior do que nao o publicar.
    if _quer_p:
        eq(_h_p.get("rebalance_triggered"), False,
           f"com o {_det_p} congelado ha {_idade_p} dias, a carteira NAO e "
           f"rebalanceada sobre um valor que o proprio motor declarou "
           f"nao-credivel ({_h_p.get('rebalance_reason')})")
        eq(_h_p.get("rebalance_reason"), "valuation_not_credible_held",
           "e a razao publicada di-lo pelo nome")
        true(not _h_p.get("trade_cost"),
             f"e nao se paga custo de transaccao nenhum "
             f"({_h_p.get('trade_cost')!r})")
        eq(_res_p["current"].get("shares"), _pf_p["current"].get("shares"),
           "e as posicoes ficam exactamente como estavam")
    else:
        true(_h_p.get("rebalance_reason") != "valuation_not_credible_held",
             f"e com {_idade_p} dias — dentro do limite — a guarda NAO dispara: "
             f"uma guarda que dispara sempre nao e uma fronteira "
             f"({_h_p.get('rebalance_reason')})")
    shutil.rmtree(_tmp_p, ignore_errors=True)


# ── O recurso do SPY tem a MESMA guarda que o dos outros ──────────────────
#
# O laco principal recusa um `last_prices[t]` nao-finito; o ramo do SPY usava
# `if fb_spy:` — e `float("nan")` e truthy. Um NaN em `last_prices["SPY"]`,
# vindo de um ficheiro editado a mao (que e o que o RUNBOOK manda fazer numa
# recuperacao), propagava-se para o valor do benchmark e para o alpha. O
# `_has_invalid_float` apanha-o mais a frente e mata o job — nao se publica
# falsidade, mas perde-se a semana por uma assimetria que o irmao ja nao tem.
# O principio aplicado a um ramo e nao ao irmao e a familia inteira.
for _mau_spy in (float("nan"), float("inf"), -float("inf"), 0.0, -5.0):
    _tmp_s = estado(Path(tempfile.mkdtemp()), com_data=True)
    _pf_s = json.loads((_tmp_s / "portfolio.json").read_text())
    _pf_s["current"].setdefault("last_prices", {})["SPY"] = _mau_spy
    (_tmp_s / "portfolio.json").write_text(
        json.dumps(_pf_s, default=lambda o: str(o)).replace('"nan"', "NaN")
        .replace('"inf"', "Infinity").replace('"-inf"', "-Infinity"))
    _res_s = corre_recorrida(_tmp_s, sem_cotacao=("SPY",))
    _h_s = _res_s["history"][-1]
    # O que se exige e que o valor mau NAO seja usado: nem no preco do SPY, nem
    # no valor do benchmark, nem no alpha.
    for _campo_s in ("benchmark_spy_value", "alpha_vs_benchmark_pct"):
        _v_s = _h_s.get(_campo_s)
        true(_v_s is None or (isinstance(_v_s, (int, float))
                              and not math.isnan(_v_s) and not math.isinf(_v_s)),
             f"com last_prices['SPY'] = {_mau_spy!r}, o {_campo_s} nao leva um "
             f"valor invalido ({_v_s!r})")
    _p_s = (_res_s["current"].get("last_prices") or {}).get("SPY")
    true(_p_s is None or (isinstance(_p_s, (int, float)) and _p_s > 0
                          and not math.isnan(_p_s) and not math.isinf(_p_s)),
         f"e o preco invalido nao e re-gravado como recurso para a semana "
         f"seguinte ({_p_s!r})")
    shutil.rmtree(_tmp_s, ignore_errors=True)

print(f"TODOS OS {ok} TESTES PASSARAM")
