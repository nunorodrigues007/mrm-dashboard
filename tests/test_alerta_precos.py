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

# ── 0. Os nomes das fontes saem da politica ────────────────────────────────
# Este ficheiro escrevia "yahoo" a mao como fonte principal. A ordem vive em
# `FONTES_DE_PRECO`, e a concordancia alerta/smoke/cascata ja e prendida pelo
# test_smoke_precos.py; aqui so se garante que estes casos nao cravam um nome
# que um dia deixe de ser o principal.
import types as _types
sys.modules.setdefault("yfinance", _types.ModuleType("yfinance"))
import update_portfolio as _up
PRINCIPAL = _up.FONTES_DE_PRECO[0][0]
RECURSO = _up.FONTES_DE_PRECO[1][0]
true(PRINCIPAL != RECURSO, "a cascata tem duas fontes distintas")

# ── 1. Semana sa: nada a comunicar ─────────────────────────────────────────
# Um alerta que dispara todas as semanas e um alerta que se aprende a ignorar,
# e ai ja nao serve para a semana em que importa.
g, cong, deg = ap.diagnostica({
    "valuation_frozen": [], "price_sources": {t: PRINCIPAL for t in SEIS}})
eq(g, None, "com tudo pela fonte principal nao se comunica nada")
eq(cong, [], "e nao ha congelados")
eq(deg, [], "nem degradados")

# ── 2. Preco de uma fonte de recurso: aviso, nao alarme ───────────────────
g, cong, deg = ap.diagnostica({
    "valuation_frozen": [],
    "price_sources": {"SPY": RECURSO, "IEF": PRINCIPAL}})
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
    "valuation_frozen": ["VNQ"], "price_sources": {"SPY": RECURSO}})
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


# ── 8. Na estreia, a etiqueta ainda nao existe ───────────────────────────
#
# A procura por um fio ja aberto filtra por etiqueta, e na PRIMEIRA vez que este
# alerta dispara essa etiqueta nao existe no repositorio. Se um 404 nessa procura
# impedisse a abertura do issue, o alerta falhava exactamente na estreia — a
# corrida que mais importa, porque e a que conta a avaria pela primeira vez.
import urllib.error

_chamadas = []
def _api_falsa(metodo, caminho, token, corpo=None):
    _chamadas.append((metodo, caminho))
    if metodo == "GET":
        raise urllib.error.HTTPError(caminho, 404, "Not Found", {}, None)
    return {"number": 12}

_guarda = ap._api
try:
    ap._api = _api_falsa
    r = ap.publica("dono/repo", "t", ap.ETIQUETA_CONGELADO, "titulo", "corpo")
finally:
    ap._api = _guarda
true("aberto o issue #12" in r, "com a etiqueta ainda inexistente, abre-se o issue")
eq([m for m, _ in _chamadas], ["GET", "POST"],
   "procurou fio aberto e, nao achando, abriu um")

# Mas um erro que NAO seja 404 continua a subir: engolir tudo transformava uma
# credencial invalida num alerta que parecia ter saido e nao saiu.
def _api_500(metodo, caminho, token, corpo=None):
    raise urllib.error.HTTPError(caminho, 500, "Server Error", {}, None)
_guarda = ap._api
subiu = False
try:
    ap._api = _api_500
    try:
        ap.publica("dono/repo", "t", ap.ETIQUETA_CONGELADO, "titulo", "corpo")
    except urllib.error.HTTPError:
        subiu = True
finally:
    ap._api = _guarda
true(subiu, "um erro que nao e 404 nao e engolido pela guarda da estreia")

# E com um fio ja aberto, comenta em vez de abrir outro: tres semanas de avaria
# davam tres issues iguais e a terceira ja nao se lia.
def _api_com_fio(metodo, caminho, token, corpo=None):
    _chamadas.append((metodo, caminho))
    return [{"number": 7}] if metodo == "GET" else {"number": 99}
_chamadas.clear()
_guarda = ap._api
try:
    ap._api = _api_com_fio
    r2 = ap.publica("dono/repo", "t", ap.ETIQUETA_CONGELADO, "titulo", "corpo")
finally:
    ap._api = _guarda
true("comentado no issue #7" in r2, "havendo fio aberto, comenta-se nele")
true(all("/issues/7/comments" in c for m, c in _chamadas if m == "POST"),
     "e o comentario vai para esse fio, nao para um issue novo")


# ── 9. A PRIMEIRA sexta de avaria nao pode ser silenciosa ────────────────
#
# Reproduzido a 3 de Outubro de 2026 sobre o estado de producao, com as duas
# fontes em baixo na sexta seguinte: o motor publicou `valuation_frozen: []`,
# `price_sources: {}`, `valuation_complete: true`, o pipeline ficou todo verde,
# e este script respondeu "vieram todos da fonte principal — nada a comunicar".
# A edicao saia com os numeros da semana anterior e ninguem sabia.
#
# Os casos 3 e 5 acima passam `valuation_frozen` ja preenchido — que e o que o
# motor escreve so a partir da SEGUNDA sexta. Na primeira, a camada de cima do
# recurso (`FALLBACK_MAX_AGE_DAYS`, 10 dias) aceita o fecho da semana anterior
# sem declarar nada. O sinal que esta sempre presente e a AUSENCIA de fonte: uma
# posicao detida que nao aparece em `price_sources` foi valorizada a recurso.
cego = {
    "date": "2026-10-09",
    "shares": {t: 1.0 for t in SEIS},
    "price_sources": {},
    "last_price_dates": {t: "2026-10-02" for t in SEIS},
    "valuation_frozen": [], "valuation_frozen_days": {}, "valuation_frozen_dates": {},
    "price_frozen_after_days": 21,
    "portfolio_value": 10549.48, "portfolio_pnl_pct": 5.49,
    "alpha_vs_benchmark_pct": -10.77,
}
g, cong, _ = ap.diagnostica(cego)
eq(g, "congelado",
   "primeira sexta sem fonte nenhuma: congelado — era aqui que se dizia 'nada a comunicar'")
eq(cong, SEIS, "e os seis entram na lista")

# O corpo tem de levar a data e a idade reais, que o motor nao declarou em
# `valuation_frozen_dates`: estao no `last_price_dates`. Sem isto a tabela saia
# com "? / ? dias" precisamente na semana que este caso passou a cobrir.
corpo9 = ap.corpo_congelado(cego, cong, "nunorodrigues007")
true(corpo9.startswith("@nunorodrigues007"), "a primeira sexta tambem menciona o dono")
true("2026-10-02" in corpo9, "o corpo diz de que dia e o preco usado")
true("7 dias" in corpo9, "e a idade, derivada do last_price_dates")
_tabela9 = corpo9.split("| Instrumento |")[1].split("- Valor publicado")[0]
true("?" not in _tabela9, "a tabela nao leva nenhum '?'")

# Uma posicao isolada sem fonte tambem e valorizacao velha — o LQD e 15% da
# carteira, e cinco fontes de seis nao fazem uma semana boa.
g, cong, _ = ap.diagnostica(dict(cego, price_sources={t: PRINCIPAL for t in SEIS if t != "LQD"}))
eq(g, "congelado", "uma unica posicao detida sem fonte ja e congelado")
eq(cong, ["LQD"], "e e nomeada")

# Posicao a zero nao e posicao: um ETF que sai da alocacao nao pode abrir um
# issue todas as semanas por nao ter cotacao.
g, _, _ = ap.diagnostica(dict(cego, shares={**{t: 1.0 for t in SEIS}, "GLD": 0.0},
                              price_sources={t: PRINCIPAL for t in SEIS}))
eq(g, None, "um ticker com zero accoes nao gera aviso")

# `price_sources` AUSENTE (nao vazio) e um snapshot anterior a existencia do
# campo: nao se conclui nada, para nao inventar uma avaria retroactiva. Os casos
# 1 a 4 acima nao levam `shares`, e por isso tambem nao sao afectados.
_sem_campo = {k: v for k, v in cego.items() if k != "price_sources"}
eq(ap.sem_fonte_nenhuma(_sem_campo), [], "campo ausente != campo vazio")
eq(ap.sem_fonte_nenhuma(cego), SEIS, "campo vazio com posicoes detidas: nenhuma fonte serviu")

# E o portfolio.json que esta em main nao pode ser acusado: se a regra nova
# disparasse sobre a semana publicada, o aviso nascia a mentir.
import json as _json
_cur = (_json.loads((ROOT / "portfolio.json").read_text(encoding="utf-8")) or {}).get("current") or {}
eq(ap.sem_fonte_nenhuma(_cur), [], "o portfolio.json em disco nao tem posicoes sem fonte")

print(f"TODOS OS {ok} TESTES PASSARAM")
