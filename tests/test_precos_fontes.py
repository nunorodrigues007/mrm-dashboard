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

print(f"TODOS OS {ok} TESTES PASSARAM")
