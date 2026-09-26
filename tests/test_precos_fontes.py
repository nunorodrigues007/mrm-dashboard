"""
A cascata de fontes de preco do `fetch_prices` — sem rede.

Porque este ficheiro existe: a 11 de Setembro de 2026 a Yahoo deixou de
responder aos runners do GitHub e o sistema publicou TRES SEMANAS o mesmo preco
ao centimo com o job verde. A correccao foi uma segunda fonte. Uma segunda fonte
sem ensaio e uma segunda fonte que se descobre partida na sexta-feira em que a
primeira cair — ou seja, precisamente quando ja nao ha alternativa.

O que se afirma aqui:
 1. as guardas (frescura e preco absurdo) valem IGUAL para todas as fontes;
 2. a fonte de recurso entra quando a principal falha, e nao antes;
 3. quando todas falham, o resultado e `None` + `stale` — nunca um preco
    inventado nem uma excepcao a subir para o motor;
 4. quem serviu cada ticker fica registado.

Sem rede: as fontes sao substituidas por funcoes locais.
"""
import importlib.util, sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("up", ROOT / "update_portfolio.py")
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1
def true(c, what): eq(bool(c), True, what)

ALVO = date(2026, 9, 25)

def _fonte(observacoes, chamadas=None, nome="x"):
    """Uma fonte de teste: devolve o que lhe dermos e conta as chamadas."""
    def f(ticker, start, end):
        if chamadas is not None:
            chamadas.append(nome)
        if isinstance(observacoes, Exception):
            raise observacoes
        return dict(observacoes)
    return f

def _com_fontes(fontes, fn):
    guarda = up.FONTES_DE_PRECO
    try:
        up.FONTES_DE_PRECO = tuple(fontes)
        return fn()
    finally:
        up.FONTES_DE_PRECO = guarda


# ── 1. A segunda fonte entra quando a primeira falha ────────────────────────
chamadas = []
pr, dt, st = _com_fontes(
    [("principal", _fonte(ValueError("em baixo"), chamadas, "principal")),
     ("recurso",   _fonte({ALVO: 771.35}, chamadas, "recurso"))],
    lambda: up.fetch_prices(["SPY"], ALVO, retries=1))
eq(pr["SPY"], 771.35, "a fonte de recurso da o preco quando a principal falha")
eq(st["SPY"], False, "e o preco NAO se declara velho")
eq(dt["SPY"], str(ALVO), "com a data do fecho usado")
eq(up.ULTIMAS_FONTES["SPY"], "recurso", "e fica registado quem serviu")
eq(chamadas, ["principal", "recurso"], "a principal foi tentada ANTES da de recurso")

# ── 2. A segunda fonte NAO e chamada quando a primeira serve ───────────────
# Sem isto, a cascata gastava quota da fonte com limite diario em todas as
# corridas, incluindo as semanas em que nada estava avariado.
chamadas = []
pr, _, st = _com_fontes(
    [("principal", _fonte({ALVO: 100.0}, chamadas, "principal")),
     ("recurso",   _fonte({ALVO: 999.0}, chamadas, "recurso"))],
    lambda: up.fetch_prices(["LQD"], ALVO, retries=1))
eq(pr["LQD"], 100.0, "o preco e o da fonte principal")
eq(chamadas, ["principal"], "a fonte de recurso nem foi chamada")
eq(up.ULTIMAS_FONTES["LQD"], "principal", "e o registo diz principal")

# ── 3. As guardas valem IGUAL na fonte de recurso ──────────────────────────
# A fronteira sai da constante, nao de um numero escrito a mao ao lado dela:
# `>` trocado por `>=` tem de morrer aqui.
for idade, serve in ((0, True), (up.MAX_STALE_DAYS, True),
                     (up.MAX_STALE_DAYS + 1, False)):
    d = ALVO - timedelta(days=idade)
    pr, dt, st = _com_fontes(
        [("principal", _fonte(ValueError("em baixo"))),
         ("recurso",   _fonte({d: 90.0}))],
        lambda: up.fetch_prices(["IEF"], ALVO, retries=1))
    if serve:
        eq(pr["IEF"], 90.0, f"recurso: um fecho de ha {idade} dias serve")
        eq(st["IEF"], False, f"recurso: e nao se declara velho ({idade} dias)")
    else:
        eq(pr["IEF"], None,
           f"recurso: um fecho de ha {idade} dias (limite {up.MAX_STALE_DAYS}) "
           f"NAO passa por preco desta semana")
        eq(st["IEF"], True, "recurso: e a corrida declara-o velho")

for mau in (0.0, -3.0, float("nan"), float("inf"), None, "764.29"):
    pr, _, st = _com_fontes(
        [("principal", _fonte(ValueError("em baixo"))),
         ("recurso",   _fonte({ALVO: mau}))],
        lambda: up.fetch_prices(["VNQ"], ALVO, retries=1))
    eq(pr["VNQ"], None,
       f"recurso: um preco {mau!r} e falha de cotacao, nao um valor")
    eq(st["VNQ"], True, f"recurso: e declara-se velho com {mau!r}")

# ── 4. Todas em baixo: None + stale, e nada rebenta ────────────────────────
# O motor tem um caminho inteiro para "sem cotacao" — preco de recurso, aviso na
# edicao, `valuation_frozen`. Uma excepcao a subir daqui salta esse caminho todo
# e mata a corrida, que e o oposto do que o sistema promete fazer numa avaria.
pr, dt, st = _com_fontes(
    [("a", _fonte(ValueError("em baixo"))),
     ("b", _fonte(RuntimeError("tambem em baixo"))),
     ("c", _fonte({}))],
    lambda: up.fetch_prices(["SPY", "BIL"], ALVO, retries=1))
for t in ("SPY", "BIL"):
    eq(pr[t], None, f"{t}: sem fonte nenhuma nao ha preco")
    eq(dt[t], None, f"{t}: nem data")
    eq(st[t], True, f"{t}: e declara-se velho")
    true(t not in up.ULTIMAS_FONTES, f"{t}: e nao se registou fonte nenhuma")

# ── 5. `ULTIMAS_FONTES` e desta corrida, nao da anterior ──────────────────
# Sem o `clear()`, um ticker que deixasse de ter preco mantinha no ficheiro a
# fonte que o servira na semana passada — exactamente o tipo de resto que faz
# uma avaria parecer resolvida.
_com_fontes([("boa", _fonte({ALVO: 91.62}))],
            lambda: up.fetch_prices(["BIL"], ALVO, retries=1))
eq(sorted(up.ULTIMAS_FONTES), ["BIL"], "o registo tem so os tickers desta corrida")

# ── 6. A Alpha Vantage sem chave nao e uma falha, e uma fonte desligada ───
# Devolve vazio em vez de levantar, e sem tocar na rede: a cascata degrada para
# o comportamento antigo em vez de partir num ambiente sem segredo configurado.
import os
_guard_chave = os.environ.pop("ALPHAVANTAGE_API_KEY", None)
try:
    eq(up._serie_alphavantage("SPY", ALVO - timedelta(days=10), ALVO), {},
       "sem ALPHAVANTAGE_API_KEY a fonte devolve vazio sem ir a rede")
finally:
    if _guard_chave is not None:
        os.environ["ALPHAVANTAGE_API_KEY"] = _guard_chave

# ── 7. A ordem declarada e a que a politica exige ─────────────────────────
eq([n for n, _ in up.FONTES_DE_PRECO], ["yahoo", "alphavantage"],
   "a Yahoo primeiro (nao consome quota), a Alpha Vantage a seguir")


# ── 8. O parser da Alpha Vantage, contra um payload REAL ──────────────────
#
# Este era o unico ponto por afirmar, e e o que decide a sexta-feira em que a
# Yahoo estiver em baixo: as outras afirmacoes usam fontes de teste, e uma fonte
# de teste concorda sempre com o parser que a le. O corpo abaixo e uma resposta
# verdadeira da API (SPY, 2026-09-25), com a forma que ela usa mesmo: chaves
# numeradas, precos como TEXTO e datas como chaves do dicionario.
_PAYLOAD_REAL = {
    "Meta Data": {"1. Information": "Daily Prices (open, high, low, close) and Volumes",
                  "2. Symbol": "SPY", "3. Last Refreshed": "2026-09-25",
                  "4. Output Size": "Compact", "5. Time Zone": "US/Eastern"},
    "Time Series (Daily)": {
        "2026-09-25": {"1. open": "768.7800", "2. high": "772.2800",
                       "3. low": "766.2900", "4. close": "771.3500",
                       "5. volume": "36666733"},
        "2026-09-24": {"1. open": "764.0650", "2. high": "768.9500",
                       "3. low": "763.2450", "4. close": "767.1800",
                       "5. volume": "43983659"},
        "2026-09-18": {"1. open": "761.3100", "2. high": "762.0000",
                       "3. low": "757.9710", "4. close": "761.6900",
                       "5. volume": "65395148"},
        # Fora da janela pedida: tem de ser descartado, senao o `max()` do
        # `_escolhe_fecho` podia eleger um fecho que nao foi pedido.
        "2026-05-05": {"1. open": "721.7700", "2. high": "725.0400",
                       "3. low": "721.4898", "4. close": "723.7700",
                       "5. volume": "36933226"},
    },
}

class _RespostaFalsa:
    def __init__(self, corpo): self._c = corpo
    def raise_for_status(self): pass
    def json(self): return self._c

def _com_resposta(corpo, fn):
    guarda_get = up.requests.get
    guarda_chave = os.environ.get("ALPHAVANTAGE_API_KEY")
    try:
        os.environ["ALPHAVANTAGE_API_KEY"] = "chave-de-ensaio"
        up.requests.get = lambda *a, **k: _RespostaFalsa(corpo)
        return fn()
    finally:
        up.requests.get = guarda_get
        if guarda_chave is None:
            os.environ.pop("ALPHAVANTAGE_API_KEY", None)
        else:
            os.environ["ALPHAVANTAGE_API_KEY"] = guarda_chave

_inicio, _fim = ALVO - timedelta(days=10), ALVO + timedelta(days=1)
serie = _com_resposta(_PAYLOAD_REAL,
                      lambda: up._serie_alphavantage("SPY", _inicio, _fim))
eq(serie, {date(2026, 9, 25): 771.35, date(2026, 9, 24): 767.18,
           date(2026, 9, 18): 761.69},
   "o payload real da Alpha Vantage e lido, convertido para float e recortado "
   "a janela pedida")
eq(serie[ALVO], 771.35,
   "e o fecho de 25 Set 2026 e o mesmo que a Yahoo deu (771.35) — duas fontes "
   "independentes a concordar ao centimo")

# E o preco vem como TEXTO no JSON: se a fonte nao o converter, o
# `_escolhe_fecho` recusa-o (guarda 3 acima) e a cascata perde a segunda fonte
# exactamente quando precisa dela. Esta afirmacao prende a conversao na FONTE.
for _v in serie.values():
    true(isinstance(_v, float), "a fonte devolve float, nao o texto do JSON")

# ── 9. Limite de pedidos e simbolo inexistente sao FALHA, nao serie vazia ──
# A Alpha Vantage responde 200 com uma mensagem. Tratar isso por "nao ha dados"
# seria dar o limite de pedidos por resposta legitima e nunca voltar a tentar.
for _corpo, _que in (({"Note": "call frequency"}, "limite de pedidos"),
                     ({"Information": "premium endpoint"}, "endpoint pago"),
                     ({"Error Message": "Invalid API call"}, "simbolo invalido"),
                     ({}, "corpo vazio"),
                     ({"Time Series (Daily)": {}}, "serie vazia")):
    levantou = False
    try:
        _com_resposta(_corpo, lambda: up._serie_alphavantage("SPY", _inicio, _fim))
    except Exception:
        levantou = True
    true(levantou, f"{_que}: a fonte levanta em vez de devolver vazio")

# E na cascata, isso faz o ticket cair para `stale` em vez de passar por bom.
pr, _, st = _com_resposta({"Note": "call frequency"}, lambda: _com_fontes(
    [("yahoo", _fonte(ValueError("em baixo"))),
     ("alphavantage", up._serie_alphavantage)],
    lambda: up.fetch_prices(["SPY"], ALVO, retries=1)))
eq(pr["SPY"], None, "com as duas fontes em baixo nao ha preco")
eq(st["SPY"], True, "e declara-se velho")


# ── 10. O espacamento dos pedidos a Alpha Vantage ────────────────────────
#
# Encontrado pelo ensaio de 26 de Setembro de 2026, nao por leitura do codigo:
# com a Yahoo desligada, os seis instrumentos foram pedidos seguidos e o sexto
# levou com `Burst pattern detected`. A repeticao salvou a corrida — mas
# depender da repeticao para nao falhar e diferente de nao provocar o limite.
#
# A aritmetica e afirmada sobre a funcao PURA, com um relogio de mentira: um
# ensaio que dormisse a serio ou era lento ou nao existia, e um espacamento por
# afirmar e um espacamento que se descobre errado no dia em que a Yahoo cair.
I = up.ALPHAVANTAGE_INTERVALO_S
true(I > 0, "ha um intervalo declarado entre pedidos a Alpha Vantage")

eq(up._quanto_esperar_av(100.0, None), 0.0,
   "o primeiro pedido nao espera por nada")
eq(up._quanto_esperar_av(100.0, 100.0), I,
   "dois pedidos no mesmo instante esperam o intervalo inteiro")
eq(up._quanto_esperar_av(100.0 + I, 100.0), 0.0,
   "passado o intervalo exacto ja nao se espera — a fronteira, nao so a volta dela")
eq(up._quanto_esperar_av(100.0 + I + 5, 100.0), 0.0,
   "e muito depois tambem nao")
eq(up._quanto_esperar_av(100.0 + I / 2, 100.0), I / 2,
   "a meio do intervalo espera-se a outra metade")
true(up._quanto_esperar_av(100.0, 100.0, intervalo=9.0) == 9.0,
     "o intervalo e um parametro, nao um numero preso no corpo da funcao")

# Nunca um negativo: um `sleep` de negativo levanta, e um `max(0, ...)` mal
# posto so se ve quando ja e tarde.
for _agora, _ultimo in ((100.0, 100.0), (100.0, 50.0), (100.0, 99.999),
                        (0.0, 0.0), (1e9, 1e9 - 1)):
    true(up._quanto_esperar_av(_agora, _ultimo) >= 0.0,
         f"a espera nunca e negativa ({_agora}, {_ultimo})")

# E se o relogio andar para tras — acerto de hora, monotonic trocado — espera-se
# o intervalo INTEIRO. Devolver o negativo seria nao esperar nada exactamente
# quando nao se sabe ha quanto tempo foi o ultimo pedido.
eq(up._quanto_esperar_av(100.0, 200.0), I,
   "com o relogio para tras espera-se o intervalo inteiro, nao zero")

# A fonte chama o espacamento. Sem isto, a funcao existia e nao era usada — que
# e o modo de falha mais silencioso que ha.
import inspect
true("_espera_a_vez_da_alphavantage" in inspect.getsource(up._serie_alphavantage),
     "a fonte da Alpha Vantage espaca os pedidos antes de os fazer")

# Mas NAO antes de verificar a chave: sem chave nao ha pedido nenhum, e dormir
# antes de nao fazer nada e so tornar a suite e as corridas mais lentas.
_src = inspect.getsource(up._serie_alphavantage)
true(_src.index("if not chave") < _src.index("_espera_a_vez_da_alphavantage"),
     "e a fonte desligada sai ANTES de esperar — sem chave nao ha pedido a espacar")

print(f"TODOS OS {ok} TESTES PASSARAM")
