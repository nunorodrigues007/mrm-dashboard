"""
Custos de transaccao no backtest — a aritmetica, verificada a mao.

Um custo mal contabilizado nao rebenta: da um numero ligeiramente melhor e
publica-se. Estes testes verificam cada passo contra uma conta feita a parte,
nao contra o proprio codigo que esta a ser testado.

Sem rede: le apenas os ficheiros em backtest/data/.
"""
import importlib.util, io, contextlib, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("fb", ROOT / "backtest" / "final_backtest.py")
fb = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(fb)

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1
def close(got, want, tol, what):
    global ok
    assert abs(got - want) <= tol, f"{what}: esperado {want} +/- {tol}, obtido {got}"
    ok += 1
def true(c, what): eq(bool(c), True, what)

# ── 1. o capital declarado e o pedido ───────────────────────────────────────
eq(fb.INITIAL_CAPITAL, 100_000.0, "capital inicial de 100.000 USD")
eq(fb.COST_OPEN, 10.0, "10 USD por abertura")
eq(fb.COST_CLOSE, 10.0, "10 USD por fecho")

# ── 2. trade_cost conta o que se ve, contado a mao ──────────────────────────
# Turbulence detem SPY, IEF, LQD, DBC(PDBC), SHV(BIL), VNQ.
# Critical_Stress detem SPY(USMV), SHV(SHY), SHV(SGOV), GLD, SHV(BIL), VNQ
#   -> conjunto {SPY, SHV, GLD, VNQ}.
# Fecham IEF, LQD, DBC (3). Abre GLD (1). Nenhuma outra muda de existencia.
# 4 linhas x 10 USD = 40 USD.
turb = {"SPY": 1.0, "IEF": 1.0, "LQD": 1.0, "DBC": 1.0, "SHV": 1.0, "VNQ": 1.0}
crit = {"SPY": 2.0, "SHV": 3.0, "GLD": 1.0, "VNQ": 1.0}
c, d = fb.trade_cost(turb, crit, "open_close")
eq(sorted(d["closed"]), ["DBC", "IEF", "LQD"], "fecham exactamente tres posicoes")
eq(d["opened"], ["GLD"], "abre exactamente uma posicao")
eq(c, 40.0, "Turbulence -> Critical_Stress custa 40 USD")

# a troca de sub-regime mexe so na manga de duracao: fecha TLT, ja detem SHV
ftq = {"SPY": 1.0, "TLT": 1.0, "SHV": 1.0, "GLD": 1.0, "VNQ": 1.0}
c, d = fb.trade_cost(ftq, crit, "open_close")
eq((d["closed"], d["opened"]), (["TLT"], []), "so o TLT sai na troca de sub-regime")
eq(c, 10.0, "troca de sub-regime custa 10 USD")

# carteira identica: sem aberturas nem fechos
eq(fb.trade_cost(turb, dict(turb), "open_close")[0], 0.0,
   "carteira inalterada nao paga no modelo literal")
# mas no modelo realista um acerto de peso e uma ordem
mexida = dict(turb); mexida["SPY"] = 1.5
eq(fb.trade_cost(turb, mexida, "every_trade")[0], 10.0,
   "um acerto de peso paga uma ordem no modelo realista")
eq(fb.trade_cost(turb, mexida, "open_close")[0], 0.0,
   "e nao paga no modelo literal")

# ── 3. o custo sai do portfolio, nao de dinheiro de fora ────────────────────
m0 = fb.MONTHS[0]
sh_free = fb.rebalance(100_000.0, "Turbulence", None, m0)
sh_paid, cost = fb.rebalance(100_000.0, "Turbulence", None, m0, None, "open_close")
eq(cost, 60.0, "a compra inicial abre seis posicoes: 60 USD")
close(fb.value(sh_free, m0), 100_000.0, 0.01, "sem custos investem-se 100.000")
close(fb.value(sh_paid, m0), 99_940.0, 0.01, "com custos investem-se 99.940")

# ── 4. o ledger fecha: soma dos eventos == total ────────────────────────────
ser, log, led = fb.run_v2("open_close")
close(sum(c for _, _, c in led["events"]), led["total"], 0.01,
      "a soma dos eventos e o total do ledger")
close(led["total"], 400.0, 0.01, "o v2 paga 400 USD de corretagem em 19,5 anos")
eq(led["n_rebal"], 54, "54 rebalanceamentos no v2 (15 mudancas de estado + semestrais)")

ser1, _, led1 = fb.run_v1("open_close")
close(led1["total"], 200.0, 0.01, "o v1 paga 200 USD")
eq(led1["n_rebal"], 42, "42 rebalanceamentos no v1")
true(led["total"] > led1["total"], "o sistema que negoceia mais paga mais")

# ── 5. os custos so podem piorar o resultado, nunca melhora-lo ──────────────
free2, _, _ = fb.run_v2()
free1, _, _ = fb.run_v1()
for nome, com, sem in (("v2", ser, free2), ("v1", ser1, free1)):
    a, b = fb.stats(com), fb.stats(sem)
    true(a["final"] < b["final"], f"{nome}: com custos termina abaixo de sem custos")
    true(a["cagr"] < b["cagr"], f"{nome}: com custos o CAGR e menor")
    # e a diferenca tem de ser da ordem do custo composto, nao arbitraria
    true(0 < b["final"] - a["final"] < 40 * (led["total"] if nome == "v2" else led1["total"]),
         f"{nome}: a diferenca final e compativel com o custo capitalizado")

# ── 6. o capital compoe: a serie e o produto dos retornos ───────────────────
v = [x for _, x in ser]
prod = v[0]
for i in range(1, len(v)):
    prod *= v[i] / v[i - 1]
close(prod, v[-1], 0.01, "a serie e multiplicativa — o capital compoe")
close(fb.stats(ser)["final"], v[-1], 0.01, "o valor final e o ultimo da serie")
yrs = (len(v) - 1) / 12
close((v[-1] / v[0]) ** (1 / yrs) - 1, fb.stats(ser)["cagr"], 1e-9,
      "o CAGR e o do capital composto, nao uma media de retornos")

# ── 7. o custo proporcional cresce com os pontos base, e monotonamente ──────
totais = []
for bp in (0.0, 5.0, 10.0, 20.0):
    _, _, l = fb.run_v2("every_trade", slippage_bp=bp)
    totais.append(l["total"])
eq(totais, sorted(totais), "mais pontos base, mais custo")
true(totais[-1] > totais[0] * 2, "20 bp custam bem mais do que so a comissao fixa")
# e o valor transaccionado e coerente: 5 bp devem custar metade de 10 bp, so na parte proporcional
close((totais[2] - totais[0]) / (totais[1] - totais[0]), 2.0, 0.02,
      "a parte proporcional e linear nos pontos base")

# ── 8. sem custos, os numeros publicados antes mantem-se ────────────────────
s2 = fb.stats(free2); s1 = fb.stats(free1)
close(s2["cagr"] * 100, 6.57, 0.05, "sem custos o v2 continua em 6,57%")
close(s1["cagr"] * 100, 6.93, 0.05, "sem custos o v1 continua em 6,93%")
close(s2["mdd"] * 100, -16.4, 0.1, "a quebra maxima nao depende dos custos")
d2 = dict(ser)
close((d2["2008-12"] / d2["2007-12"] - 1) * 100, 1.5, 0.2, "2008 com custos continua positivo")

# ── A carteira REAL cobra os mesmos custos que o backtest ────────────────
# Enquanto os custos viveram so no backtest, o site publicava lado a lado um
# historico com comissoes deduzidas e uma carteira real sem custo nenhum, como
# se fossem comparaveis: cinco rebalanceamentos x seis posicoes sao cerca de
# $300 sobre $10.000 — perto de metade do P&L publicado de +6,18%.
import mrm_rules as _r
eq(_r.COST_OPEN, 10.0, "abrir uma posicao custa $10")
eq(_r.COST_CLOSE, 10.0, "fechar custa $10")
eq(_r.trade_cost({}, {"SPY": 1, "IEF": 2})[0], 20.0, "duas posicoes novas: $20")
eq(_r.trade_cost({"SPY": 1, "IEF": 2}, {})[0], 20.0, "fechar duas: $20")
eq(_r.trade_cost({"SPY": 1}, {"SPY": 2})[0], 0.0,
   "acertar o peso de uma posicao ja detida nao abre nem fecha nada")
eq(_r.trade_cost({"SPY": 1}, {"SPY": 2}, "every_trade")[0], 10.0,
   "mas no modelo por ordem, paga")
eq(_r.trade_cost({"SPY": 1, "IEF": 2}, {"SPY": 1, "TLT": 3})[0], 20.0,
   "uma troca de instrumento e uma abertura mais um fecho")
eq(_r.trade_cost({"SPY": 0.0}, {"SPY": 0.0})[0], 0.0,
   "posicoes a zero nao sao posicoes")
# O backtest usa exactamente estas constantes, e nao copias suas.
#
# Comparar `_fb.COST_OPEN == _r.COST_OPEN` nao provava nada: as duas ja estavam
# fixadas a 10.0 noutro sitio deste ficheiro, logo a igualdade era tautologica e
# uma constante escrita a mao no backtest passava. A prova e MUDAR a de
# mrm_rules e ver se a do backtest a segue.
import importlib.util as _iu, pathlib as _pl
def _recarrega_backtest():
    _sp = _iu.spec_from_file_location("_fb", _pl.Path(__file__).resolve().parent.parent
                                      / "backtest" / "final_backtest.py")
    _m = _iu.module_from_spec(_sp); _sp.loader.exec_module(_m)
    return _m

_guardado = (_r.COST_OPEN, _r.COST_CLOSE)
try:
    _r.COST_OPEN, _r.COST_CLOSE = 99.0, 77.0
    _fb = _recarrega_backtest()
    eq(_fb.COST_OPEN, 99.0, "o backtest SEGUE a constante de abertura de mrm_rules")
    eq(_fb.COST_CLOSE, 77.0, "e a de fecho — nao tem copias suas")
    # E a funcao tambem: o custo fixo do backtest e o de mrm_rules.
    _c, _d = _fb.trade_cost({}, {"SPY": 1, "IEF": 1})
    eq(_c, 198.0, "e a funcao de custo do backtest delega na de mrm_rules")
finally:
    _r.COST_OPEN, _r.COST_CLOSE = _guardado
    _fb = _recarrega_backtest()
eq(_fb.COST_OPEN, _r.COST_OPEN, "restaurado")

print(f"TODOS OS {ok} TESTES PASSARAM")
