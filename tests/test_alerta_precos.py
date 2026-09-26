"""
O alerta de precos congelados — sem rede.

Porque existe. Este script e a unica coisa que transforma "o job teve exito mas
publicou o preco da semana passada" em barulho que chega ao dono. Se ELE estiver
partido, a avaria volta a ser silenciosa e ninguem da por isso — o modo de falha
e exactamente o que ja custou tres semanas em Setembro de 2026.

Sem rede: testa-se a DECISAO e o TEXTO, nao a chamada a API.
"""
import importlib.util, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("ap", ROOT / "alerta_precos.py")
ap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ap)

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1
def true(c, what): eq(bool(c), True, what)

SEIS = ["BIL", "IEF", "LQD", "PDBC", "SPY", "VNQ"]

# ── 1. Semana sa: nada a comunicar ─────────────────────────────────────────
# Um alerta que dispara todas as semanas e um alerta que se aprende a ignorar,
# e ai ja nao serve para a semana em que importa.
g, cong, deg = ap.diagnostica({
    "valuation_frozen": [], "price_sources": {t: "yahoo" for t in SEIS}})
eq(g, None, "com tudo pela fonte principal nao se comunica nada")
eq(cong, [], "e nao ha congelados")
eq(deg, [], "nem degradados")

# ── 2. Preco de uma fonte de recurso: aviso, nao alarme ───────────────────
g, cong, deg = ap.diagnostica({
    "valuation_frozen": [],
    "price_sources": {"SPY": "alphavantage", "IEF": "yahoo"}})
eq(g, "degradado", "a fonte principal em baixo com recurso a servir e 'degradado'")
eq(deg, ["SPY"], "e nomeia so quem veio pela de recurso")
eq(cong, [], "sem congelados: a valorizacao desta semana esta correcta")

# ── 3. Nenhuma fonte deu preco: alarme ───────────────────────────────────
g, cong, deg = ap.diagnostica({
    "valuation_frozen": SEIS, "price_sources": {}})
eq(g, "congelado", "sem fonte nenhuma a valorizacao nao e desta semana")
eq(cong, SEIS, "e nomeia os seis")

# ── 4. Congelado GANHA a degradado ───────────────────────────────────────
# Com as duas condicoes a valer, "nao ha preco nenhum" e o diagnostico mais
# preciso e e esse que tem de ser publicado — a mesma ordem que o motor usa
# entre `valuation_missing` e `valuation_not_credible`.
g, _, _ = ap.diagnostica({
    "valuation_frozen": ["VNQ"], "price_sources": {"SPY": "alphavantage"}})
eq(g, "congelado", "congelado tem precedencia sobre degradado")

# ── 5. O estado REAL de 25 de Setembro de 2026 e diagnosticado ───────────
# O caso que motivou tudo isto, com os campos como o motor os escreve.
cur_real = {
    "date": "2026-09-25",
    "valuation_frozen": SEIS,
    "valuation_frozen_days": {t: 14 for t in SEIS},
    "valuation_frozen_dates": {t: "2026-09-11" for t in SEIS},
    "price_frozen_after_days": 21,
    "price_sources": {},
    "portfolio_value": 10631.42, "portfolio_pnl_pct": 6.31,
    "alpha_vs_benchmark_pct": -9.14,
}
g, cong, _ = ap.diagnostica(cur_real)
eq(g, "congelado", "a corrida de 25 Set 2026 e diagnosticada como congelada")
corpo = ap.corpo_congelado(cur_real, cong, "nunorodrigues007")

# A MENCAO e o que faz a notificacao chegar: o repositorio esta em "Watch:
# Participating and @mentions", e um issue aberto pelo bot nao e participacao de
# ninguem. Sem esta linha o alerta fica no GitHub a espera de ser descoberto —
# que e precisamente a avaria que este ficheiro existe para impedir.
true(corpo.startswith("@nunorodrigues007"), "o corpo comeca com a mencao ao dono")
true("2026-09-11" in corpo, "o corpo diz de quando e o preco")
true("14 dias" in corpo, "e a idade dele")
for t in SEIS:
    true(f"`{t}`" in corpo, f"o corpo nomeia {t}")
true("-9.14" in corpo, "o corpo publica o alpha enganador")
true("ALPHAVANTAGE_API_KEY" in corpo, "e diz o que verificar primeiro")

# A aritmetica da fronteira, derivada do limite e nao escrita a mao ao lado
# dele: o motor dispara em `idade > limite`, portanto aos 21 dias exactos AINDA
# nao disparou e faltam 22-21 = 1 dia. Com `limite - idade` o aviso dizia
# "faltam 0 dias" numa semana em que nada acontece.
def _restam(idade, limite=21):
    c = dict(cur_real, valuation_frozen_days={t: idade for t in SEIS},
             price_frozen_after_days=limite)
    return ap.corpo_congelado(c, SEIS, "d")
true("Faltam **8 dias**" in _restam(14), "aos 14 dias faltam 8 para passar o limite de 21")
true("Faltam **1 dias**" in _restam(21), "aos 21 dias exactos ainda falta 1")
true("JA foi passado" in _restam(22), "aos 22 dias o limite ja foi passado")
true("JA foi passado" in _restam(28), "e aos 28 tambem")

# ── 6. O corpo do aviso degradado nomeia a fonte que serviu ─────────────
corpo_d = ap.corpo_degradado(
    {"date": "2026-10-02", "price_sources": {"SPY": "alphavantage"}},
    ["SPY"], "nunorodrigues007")
true(corpo_d.startswith("@nunorodrigues007"), "o aviso degradado tambem menciona o dono")
true("`alphavantage`" in corpo_d, "e diz qual foi a fonte que serviu")
true("nao e uma avaria na carteira" in corpo_d,
     "e deixa claro que a valorizacao desta semana esta correcta")

# ── 7. As etiquetas sao distintas ───────────────────────────────────────
# A deduplicacao e por etiqueta: com a mesma, um aviso de fonte degradada ia
# comentar no fio de precos congelados e passava por continuacao da avaria.
true(ap.ETIQUETA_CONGELADO != ap.ETIQUETA_DEGRADADO,
     "cada avaria tem a sua etiqueta, senao a deduplicacao junta as duas")

print(f"TODOS OS {ok} TESTES PASSARAM")
