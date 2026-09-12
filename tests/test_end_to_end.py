"""
Teste de ponta a ponta, sem rede.

Corre a cadeia completa — fetch_data → update_portfolio → send_newsletter — em
dois mundos, um calmo e um sob stress, e verifica a propriedade que faltava ao
sistema: as três superfícies contam a mesma história.

Antes desta série de correcções, no mundo sob stress a carteira rodava para
Critical e a newsletter dizia aos subscritores "No structural regime change
detected. Holding current positions." Este teste falha se isso voltar a
acontecer.
"""
import importlib.util, json, os, shutil, sys, tempfile, types, logging
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))
os.environ.setdefault("FRED_API_KEY", "test-key-not-used")

import fetch_data, mrm_gauge_b, mrm_rules as rules
import update_portfolio as up
import test_build_data as T
import fixture_obs as fx

# ── a semana simulada sai do repositorio, nao do calendario ────────────────
#
# Este teste e portao dos dois jobs de sexta. Enquanto fixou a sexta em
# 2026-09-11 e copiou o portfolio.json tal e qual, dependia de duas coisas que
# mudam sozinhas: as observacoes gravadas, que caducam contra os prazos do
# data_freshness, e o regime que a carteira por acaso detinha na semana em que o
# ficheiro foi commitado. Qualquer das duas partia o portao — e um portao
# partido nao da um teste vermelho na segunda-feira: da uma sexta sem carteira e
# sem newsletter.
SEXTA, ANTERIOR, ISSUE = fx.semana_ensaiada(ROOT, rules)


spec = importlib.util.spec_from_file_location("sn", ROOT / "send_newsletter.py")
sn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sn)

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1
def true(cond, what): eq(bool(cond), True, what)

PRICES = {"SPY": 600.0, "IEF": 95.0, "LQD": 108.0, "PDBC": 14.0, "BIL": 91.5, "VNQ": 88.0,
          "USMV": 90.0, "TLT": 95.0, "SHY": 82.0, "SGOV": 100.5, "GLD": 250.0,
          "QQQ": 500.0, "HYG": 79.0, "IWO": 280.0}


def run_world(stress):
    """Corre a cadeia inteira num directório temporário e devolve os três
    artefactos: data.json, portfolio.json e o contexto da newsletter."""
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "score_history.json", tmp)
    shutil.copy(ROOT / "portfolio.json", tmp)
    _nome_ant = fx.poe_edicao_anterior(ROOT, tmp, ISSUE - 1, ANTERIOR)
    _html_ant = (tmp / _nome_ant).read_text(encoding="utf-8")

    # A carteira ENTRA na semana em Turbulence, dito aqui e nao herdado do
    # ficheiro commitado: e o mundo calmo que estas asserçoes descrevem.
    _p = json.loads((tmp / "portfolio.json").read_text())
    _mapa = rules.REGIME_ETF_MAP["Turbulence"]
    _p["current"].update({
        "issue": ISSUE - 1, "date": ANTERIOR.isoformat(),
        "regime": "Turbulence", "critical_subregime": None,
        "active_etf_map": dict(_mapa),
        "shares": {t: 10.0 for t in _mapa.values()},
        "last_prices": {t: PRICES[t] for t in _mapa.values()},
        "last_price_dates": {t: ANTERIOR.isoformat() for t in _mapa.values()},
        # O vector de alocacao tambem e construido. Herda-lo do ficheiro
        # commitado fazia a asserçao "a alocacao efectiva nao e o vector de
        # Critical" ficar vermelha na sexta seguinte a carteira entrar em
        # Critical — o portao a fechar-se durante a crise.
        "bucket_allocation_pct": dict(rules.REGIME_WEIGHTS["Turbulence"]),
        # Pos-migracao, como o sistema esta depois da primeira corrida com os
        # pesos nas regras. A adopcao e uma transicao unica, com ensaios proprios.
        "regime_weights_adopted": True,
    })
    # O `newsletter_bucket_allocation_pct` NAO se semeia: e o que o motor le da
    # edicao N-1 com o parser, e ha uma asserçao mais abaixo sobre ele. Semea-lo
    # tornava essa asserçao numa tautologia — o teste a afirmar o que ele proprio
    # tinha acabado de escrever — e a edicao anterior deixava de ser precisa.
    _p["current"].pop("newsletter_bucket_allocation_pct", None)
    _p["history"] = [h for h in _p["history"] if h.get("issue", 0) < ISSUE - 1]
    _p["history"].append({
        "issue": ISSUE - 1, "date": ANTERIOR.isoformat(), "regime": "Turbulence",
        "regime_signalled": "Turbulence", "critical_subregime": None,
        "rebalance_triggered": False, "rebalance_reason": "hold",
        "mrm_score": 6.97, "portfolio_value": 10000.0, "portfolio_pnl_pct": 0.0,
        "shares": dict(_p["current"]["shares"]),
    })
    eq((_p["current"]["issue"], _p["current"]["date"]),
       (ISSUE - 1, ANTERIOR.isoformat()),
       "o estado preparado e o da edicao imediatamente anterior a semana ensaiada")
    (tmp / "portfolio.json").write_text(json.dumps(_p))

    obs = fx.obs_base(SEXTA)
    if stress:
        # Sahm em 0.62: acima do gatilho publicado de 0.50
        obs["SAHMREALTIME"] = fx.sahm(SEXTA, "0.62", "0.55")
        # 10Y a cair 32 bp em 3 meses -> confirma flight-to-quality
        obs["DGS10"] = fx.dgs10(SEXTA, 4.15, 4.47)

    class FakeResp:
        def __init__(self, sid): self.sid = sid
        def raise_for_status(self): pass
        def json(self): return {"observations": list(obs.get(self.sid, []))}

    fetch_data.fetch_fred = lambda sid, limit=12, retries=3, backoff=5: obs.get(sid, [])[:limit]
    fetch_data.fetch_liquidity_percentile = T.fake_liquidity
    mrm_gauge_b.requests.get = lambda url, params=None, timeout=None: FakeResp((params or {}).get("series_id"))

    # Tudo o que se substitui em modulos partilhados e reposto no fim.
    #
    # Sem isto, importar este ficheiro deixava o `update_portfolio` desfigurado
    # no processo: `adjust_for_market_holiday` sem feriados, `get_last_friday`
    # cravado, `FORCE_REBALANCE` ligado. `pytest tests/` ja nao corria por causa
    # disso — dois falsos vermelhos, precisamente no calendario — e bastava um
    # teste futuro fazer `import test_end_to_end`, ou o workflow passar a usar
    # pytest, para dois ficheiros do portao ficarem vermelhos. E a mesma familia
    # de defeito, montada dentro da correccao.
    _guardado = (fetch_data.__file__, fetch_data.fetch_fred,
                 fetch_data.fetch_liquidity_percentile, mrm_gauge_b.requests.get,
                 up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
                 up.FORCE_REBALANCE)
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        fetch_data.__file__ = str(tmp / "fetch_data.py")
        data = fetch_data.build_data(SEXTA)          # escreve data.json em tmp

        up.fetch_prices = lambda tickers, d, retries=3: (
            {t: PRICES[t] for t in tickers}, {t: str(d) for t in tickers}, {t: False for t in tickers})
        up.get_last_friday = lambda: SEXTA
        up.adjust_for_market_holiday = lambda d: d
        up.FORCE_REBALANCE = True
        try:
            up.main()
        except SystemExit:
            pass
        portfolio = json.loads((tmp / "portfolio.json").read_text())
        data = json.loads((tmp / "data.json").read_text())
        ctx = sn.build_context(data, portfolio, {}, SEXTA, ISSUE,
                               agora=datetime(SEXTA.year, SEXTA.month, SEXTA.day, 22, 5))
        prompt = sn.build_prompt(ctx)
    finally:
        os.chdir(cwd)
        (fetch_data.__file__, fetch_data.fetch_fred,
         fetch_data.fetch_liquidity_percentile, mrm_gauge_b.requests.get,
         up.fetch_prices, up.get_last_friday, up.adjust_for_market_holiday,
         up.FORCE_REBALANCE) = _guardado
        shutil.rmtree(tmp, ignore_errors=True)
    return data, portfolio, ctx, prompt, _html_ant


# O fixture nao pode envelhecer: se as observacoes passarem do prazo, o medidor
# passa a n/d e este portao fecha-se sozinho.
fx.verifica_frescura(SEXTA, eq)

logging.disable(logging.INFO)

# ─────────────────────────────────────────────────────────────────────────────
# Mundo calmo
# ─────────────────────────────────────────────────────────────────────────────
data, pf, ctx, prompt, html_ant = run_world(stress=False)
cur = pf["current"]

eq(data["stressGauge"]["active"], False, "calmo: medidor B desligado")
eq(cur["regime"], "Turbulence", "calmo: carteira em Turbulence")
eq(cur["critical_subregime"], None, "calmo: sem sub-regime")
eq(pf["history"][-1]["rebalance_reason"], "hold", "calmo: sem rebalanceamento")
eq(ctx["regime_label"], "Turbulence", "calmo: newsletter diz Turbulence")
eq(ctx["port_etfs"], "SPY | IEF | LQD | PDBC | BIL | VNQ", "calmo: instrumentos coerentes")
true("Operative regime: Turbulence" in prompt, "calmo: o prompt leva o regime certo")
true("State: OFF" in prompt, "calmo: o prompt declara o medidor desligado")

# Sem gatilho nao ha rebalanceamento: a alocacao efectiva e a que foi executada
# da ultima vez (o semestral de Junho), e a da newsletter desta semana fica
# guardada a espera do proximo rebalanceamento. Sao diferentes de propriedade.
true(cur["bucket_allocation_pct"] not in rules.CRITICAL_WEIGHTS.values(),
     "calmo: a alocacao efectiva nao e o vector de Critical")
# E o que fica guardado e o que a EDICAO N-1 diz, lido com o mesmo parser que
# o motor usa. A cadeia de recurso do motor
# (`bucket_alloc or newsletter_... or bucket_allocation_pct`) faz com que este
# campo quase nunca fique vazio: afirmar so que ele existe passava na mesma sem
# edicao nenhuma no disco, e a leitura da edicao anterior — que e o que liga a
# newsletter da semana passada a carteira desta — deixava de estar coberta.
import newsletter_parse as _np_e2e
_alloc_ant, _, _notas_ant = _np_e2e.parse_allocation(html_ant)
true(_alloc_ant, f"a edicao N-1 e mesmo legivel pelo parser do motor ({_notas_ant[:2]})")
eq(cur["newsletter_bucket_allocation_pct"], _alloc_ant,
   "calmo: o que fica guardado e a alocacao lida da edicao N-1, nao um recurso")
eq(round(sum(cur["newsletter_bucket_allocation_pct"].values())), 100,
   "calmo: a alocacao guardada soma 100%")
eq(ctx["alloc_line"], " | ".join(f"{b}: {cur['bucket_allocation_pct'].get(b, 0):.0f}%" for b in rules.BUCKETS),
   "calmo: a newsletter mostra a alocacao efectivamente detida")

# ─────────────────────────────────────────────────────────────────────────────
# Mundo sob stress
# ─────────────────────────────────────────────────────────────────────────────
data2, pf2, ctx2, prompt2, html_ant2 = run_world(stress=True)
cur2 = pf2["current"]
hist2 = pf2["history"][-1]

eq(data2["stressGauge"]["active"], True, "stress: medidor B ligado")
eq(data2["stressGauge"]["triggers"]["sahmRealtime"]["fired"], True, "stress: gatilho de Sahm disparou")
eq(cur2["regime"], "Critical", "stress: carteira em Critical")
eq(cur2["critical_subregime"], "Critical_Stress",
   "stress: entrada fresca cai no lado defensivo mesmo com o 10Y a cair")
# E o sinal do medidor tem mesmo de dizer FTQ: o fixture poe o 10Y a cair 32 bp.
# Antes, a janela do 10Y estava invertida e o medidor calculava STRESS; o teste
# passava na mesma porque a porta de entrada fresca forca Critical_Stress de
# qualquer maneira. Passava pela razao errada.
eq(data2["stressGauge"]["subregime"], "FTQ",
   "stress: com o 10Y a cair 32 bp o medidor tem de sinalizar FTQ")
eq(hist2["rebalance_reason"], "stress_on", "stress: motivo do rebalanceamento")
eq(hist2["rebalance_triggered"], True, "stress: houve transaccoes")

# o score nao mudou de banda — quem mudou o regime foi o medidor B
eq(data2["status"], "Turbulence", "stress: o score continua a dizer Turbulence")
true(data2["globalResilienceScore"] < rules.CRITICAL_MIN,
     "stress: o score nem se aproxima do limiar Critical")

# a carteira usa o mapa e os pesos canonicos de Critical_Stress
eq(cur2["active_etf_map"], rules.REGIME_ETF_MAP["Critical_Stress"], "stress: mapa canonico")
eq(cur2["bucket_allocation_pct"], rules.CRITICAL_WEIGHTS["Critical_Stress"],
   "stress: pesos de Critical sobrepoem-se as % da newsletter")
# A PROPRIEDADE, nao um `!=` entre dois objectos. O que aqui interessa e que o
# motor guardou a alocacao que LEU da edicao N-1, para a poder repor quando
# sair de Critical. O proxy `!=` compara-a com a constante
# `CRITICAL_WEIGHTS["Critical_Stress"]` — e o prompt IMPRIME esse vector ao
# modelo ("Effective allocation now: ...") e manda-o publicar uma tabela de
# seis buckets a somar 100. Um modelo que reproduza o vector que lhe foi
# mostrado — a coisa mais natural a fazer — publica uma edicao valida, enviada
# e commitada em `main`, e na sexta seguinte este `!=` fica FALSO: o portao dos
# dois jobs vermelho sobre um ficheiro que ninguem pode alterar, sem
# recuperacao, porque o numero da edicao vem do portfolio.json que o job travado
# e que escreve. O ramo calmo, doze linhas acima, ja afirma a propriedade certa.
_alloc_ant2, _, _notas_ant2 = _np_e2e.parse_allocation(html_ant2)
true(_alloc_ant2,
     f"stress: a edicao N-1 e mesmo legivel pelo parser do motor ({_notas_ant2[:2]})")
eq(cur2["newsletter_bucket_allocation_pct"], _alloc_ant2,
   "stress: o que fica guardado e a alocacao lida da edicao N-1, para a repor "
   "a saida de Critical — nao um recurso, e nao um proxy sobre a constante")
eq(round(sum(cur2["newsletter_bucket_allocation_pct"].values())), 100,
   "stress: e soma 100%")

# ── a propriedade central: a newsletter conta a mesma historia ───────────────
eq(ctx2["regime_label"], "Critical · No Relief", "stress: newsletter diz Critical · No Relief")
eq(ctx2["rb_alert"], "STRESS_ON", "stress: newsletter reporta a entrada")
eq(ctx2["port_etfs"], " | ".join(rules.REGIME_ETF_MAP["Critical_Stress"][b] for b in rules.BUCKETS),
   "stress: newsletter lista os instrumentos que a carteira comprou")
true("Operative regime: Critical · No Relief" in prompt2, "stress: o prompt leva o regime real")
true("State: ON" in prompt2, "stress: o prompt declara o medidor ligado")
true("Executed: yes" in prompt2, "stress: o prompt declara a execucao")
true("No structural regime change detected" not in prompt2,
     "stress: a frase que contradizia a carteira desapareceu")
true("US_EQUITIES: 15%" in ctx2["alloc_line"], "stress: newsletter mostra o corte para 15% em accoes")

# ── o contrato entre quem PRODUZ o data.json e quem o le ────────────────────
#
# A forma invertida do defeito de sempre: em vez de o teste derivar a
# expectativa do ficheiro, o teste INVENTA o ficheiro. As sentinelas so estavam
# afirmadas contra um fixture escrito a mao no test_newsletter: apagar o bloco
# `sentinels` do fetch_data, renomea-lo, ou tirar-lhe o `alert` deixava a suite
# inteira verde — e a newsletter passava a dizer aos subscritores "alert False"
# sobre uma sentinela que esta a disparar, ou a inventar a tabela inteira. Nao
# da portao vermelho: da falsidade publicada.
#
# Isto ja aconteceu uma vez, e esta escrito no proprio send_newsletter: as
# sentinelas eram lidas por id fixo ("icsa") e o motor escrevia "jobless" —
# durante 26 edicoes a newsletter reportou "ICSA: N/A".
#
# Aqui a cadeia e a real: o `data` saiu do `fetch_data.build_data` e o `ctx` do
# `sn.build_context`. O que se afirma e que cada sentinela PRODUZIDA chega ao
# prompt com o valor, o limiar e o estado de alerta que o produtor lhe deu.
_sentinelas = data.get("sentinels") or []
true(_sentinelas, f"o motor produz sentinelas ({[s.get('id') for s in _sentinelas]})")
# Os campos tem de ESTAR la. Usar a mesma cadeia de recurso do consumidor para
# construir o esperado seria uma tautologia: apagar o campo no produtor mudava
# os dois lados da comparacao ao mesmo tempo e a asserçao continuava verde —
# que e exactamente como estes campos escaparam ate aqui.
for _s in _sentinelas:
    for _campo in ("id", "name", "displayValue", "thresholdDisplay", "alert"):
        true(_campo in _s,
             f"a sentinela {_s.get('id')} traz o campo {_campo}, que a newsletter "
             f"le sem inventar nada (tem: {sorted(_s)})")
for _s in _sentinelas:
    _nome = _s["name"]
    _valor = _s["displayValue"]
    _limiar = _s["thresholdDisplay"]
    _esperado = f"{_nome}: {_valor} (threshold {_limiar}, alert {bool(_s['alert'])})"
    true(_esperado in ctx["sentinel_line"],
         f"a sentinela {_s.get('id')} chega ao prompt como o motor a escreveu "
         f"(esperado {_esperado!r} em {ctx['sentinel_line']!r})")
    true(_esperado in prompt,
         f"e o modelo recebe-a ({_s.get('id')})")
# Uma sentinela em n/d so pode se-lo porque o motor a DECLAROU em n/d — nao
# porque um campo desapareceu pelo caminho.
#
# A versao anterior exigia que nenhuma sentinela chegasse em n/d, e isso deixou
# de ser uma propriedade do contrato no dia em que passou a existir um segundo
# motivo legitimo para uma delas o ser: a referencia dos earnings, posta a mao,
# expira, e a sentinela do ERP entra em n/d por decisao do produtor. Como o
# ensaio avanca no calendario e a referencia nao, a partir de certa altura TODAS
# as semanas ensaiadas ficavam vermelhas — a familia de sempre: um portao a
# derivar a sua expectativa de um estado que envelhece sozinho. O que o contrato
# afirma e a ligacao entre os dois lados.
_nd_declaradas = {_s["name"] for _s in _sentinelas
                  if str(_s.get("displayValue", "")).strip().lower() in ("n/d", "nd")}
for _pedaco in ctx["sentinel_line"].split(" | "):
    if "n/d" not in _pedaco:
        continue
    true(any(_n in _pedaco for _n in _nd_declaradas),
         f"a sentinela que chega em n/d ao prompt e uma que o motor declarou em "
         f"n/d ({_pedaco!r}; declaradas: {sorted(_nd_declaradas)})")

# O mesmo para os campos do medidor B e dos pilares que o gerador le do
# documento produzido: se o produtor deixar de os escrever, isto tem de dizer.
eq(ctx["gauge_b_line"].split(" (")[0],
   "ON" if data["stressGauge"]["active"] else "OFF",
   "o estado do medidor no prompt e o que o motor produziu")
true(data["stressGauge"]["basis"] in ctx["gauge_b_line"],
     f"e a base do medidor chega inteira ({ctx['gauge_b_line']!r} vs "
     f"{data['stressGauge']['basis']!r})")
for _pil in data["pillars"]:
    true(str(_pil["value"]) in prompt,
         f"o valor do pilar {_pil['id']} chega ao prompt ({_pil['value']!r})")
    true(str(_pil["status"]) in prompt.lower() or str(_pil["status"]) in prompt,
         f"e o estado do pilar {_pil['id']} tambem ({_pil['status']!r})")

# ── o mesmo contrato para o portfolio.json ─────────────────────────────────
#
# O contrato acima cobria o data.json. O portfolio.json — que o `update_portfolio`
# escreve e o gerador e o site leem — nao estava coberto em direccao nenhuma: o
# motor podia deixar de escrever o P&L, o alpha ou o valor da carteira, e o
# gerador podia passar a le-los por outro nome, sem uma unica asserçao vermelha.
# O prompt passava a levar "Portfolio Value: $N/A" aos subscritores com a suite
# verde. Sao os numeros de dinheiro do produto.
#
# E nao se enumeram os campos aqui: enumerar ja falhou uma vez. As chaves saem
# do PROPRIO consumidor, lidas do codigo-fonte do `build_context`. Assim, tanto
# um campo que o motor deixe de escrever como uma leitura que o gerador renomeie
# dao vermelho — as duas direccoes do mesmo contrato.
import re as _re_ct
# As chaves saem da EXECUCAO, nao do texto do codigo.
#
# A versao anterior procurava `var.get("x")` no codigo-fonte do build_context.
# Fechava a leitura escrita dessa maneira e mais nenhuma: um alias
# (`_h = hist; _h.get(...)`), uma chave em variavel (`k = "data_refused";
# hist.get(k)`) ou um destructuring saem do contrato sem uma asserçao vermelha —
# e por ai os avisos de qualidade de dados voltavam a poder desaparecer da
# edicao. Uma asserçao sobre a FORMA da leitura protege a forma; o que se quer
# proteger e a leitura.
#
# Um dicionario que regista o que lhe pedem responde a pergunta certa: correr o
# build_context sobre ele diz exactamente que chaves o consumidor foi buscar,
# escritas como estiverem escritas.
class _Espia(dict):
    """Um dicionario que anota as chaves que lhe pedem, a qualquer profundidade."""
    def __init__(self, base, lidas):
        super().__init__(base)
        self._lidas = lidas

    def _anota(self, chave):
        self._lidas.add(chave)

    def _anota_tudo(self):
        # Uma copia em bloco — `{**cur}`, `dict(cur)`, `cur.items()`, um ciclo
        # sobre as chaves — le TUDO. Anotar so o que passa pelo `get` deixava
        # essa porta aberta: bastava o consumidor ler `{**cur}.get("x")` para o
        # campo sair do contrato sem uma asserçao vermelha.
        self._lidas.update(dict.keys(self))

    def get(self, chave, *a, **kw):
        self._anota(chave)
        return dict.get(self, chave, *a, **kw)

    def __getitem__(self, chave):
        self._anota(chave)
        return dict.__getitem__(self, chave)

    def __contains__(self, chave):
        self._anota(chave)
        return dict.__contains__(self, chave)

    def __iter__(self):
        self._anota_tudo()
        return dict.__iter__(self)

    def keys(self):
        self._anota_tudo()
        return dict.keys(self)

    def items(self):
        self._anota_tudo()
        return dict.items(self)

    def values(self):
        self._anota_tudo()
        return dict.values(self)

    def copy(self):
        self._anota_tudo()
        return dict.copy(self)


_lidas_cur, _lidas_hist_bc = set(), set()
_pf_espiado = {
    **pf,
    "current": _Espia(pf["current"], _lidas_cur),
    "history": [*pf["history"][:-1], _Espia(pf["history"][-1], _lidas_hist_bc)],
}
sn.build_context(data, _pf_espiado, {}, SEXTA, ISSUE,
                 agora=datetime(SEXTA.year, SEXTA.month, SEXTA.day, 22, 5))
# O proprio gravador tem de estar preso. As defesas contra copias em bloco —
# `{**cur}`, `dict(cur)`, um ciclo sobre as chaves — nao mudam nada hoje, porque
# nenhum consumidor faz uma copia em bloco: podiam ser revertidas por engano e
# so se dava por isso no dia em que o codigo passasse a faze-lo. Um consumidor
# de mentira, que so faz a copia, prende-as.
_lidas_prova = set()
_espia_prova = _Espia({"a": 1, "b": 2, "c": 3}, _lidas_prova)
_ = {**_espia_prova}
eq(_lidas_prova, {"a", "b", "c"},
   f"o gravador anota uma copia em bloco `{{**x}}` ({_lidas_prova})")
_lidas_prova2 = set()
_espia_prova2 = _Espia({"a": 1, "b": 2}, _lidas_prova2)
for _k_prova in _espia_prova2:
    pass
eq(_lidas_prova2, {"a", "b"},
   f"e um ciclo sobre as chaves ({_lidas_prova2})")
_lidas_prova3 = set()
_espia_prova3 = _Espia({"a": 1, "b": 2}, _lidas_prova3)
dict(_espia_prova3.items())
eq(_lidas_prova3, {"a", "b"}, f"e um `items()` ({_lidas_prova3})")

true(len(_lidas_cur) >= 6,
     f"a corrida do build_context revela as chaves que ele le do bloco current "
     f"({sorted(_lidas_cur)})")
_cur_produzido = pf["current"]
for _k in sorted(_lidas_cur):
    true(_k in _cur_produzido,
         f"o motor escreve '{_k}' no portfolio.json — o build_context le-o "
         f"(tem: {sorted(_cur_produzido)})")

# E o que o gerador le da LINHA DO HISTORICO — que e onde vivem os avisos de
# qualidade de dados. Um deles a desaparecer nao da portao vermelho: da uma
# edicao que apresenta um P&L calculado sobre precos velhos, ou uma semana
# decidida sem dados frescos, como se fosse uma semana normal.
true(len(_lidas_hist_bc) >= 5,
     f"a corrida do build_context revela as chaves que ele le da linha do "
     f"historico ({sorted(_lidas_hist_bc)})")
_linha_hist_produzida = pf["history"][-1]
for _k in sorted(_lidas_hist_bc):
    true(_k in _linha_hist_produzida,
         f"o motor escreve '{_k}' na linha do historico — o build_context le-o "
         f"(tem: {sorted(_linha_hist_produzida)})")

# E a linha do HISTORICO tambem: e a tabela que o site publica semana a semana,
# com o valor, o P&L, o custo e o motivo do rebalanceamento. Renomear um destes
# campos no motor deixava a coluna vazia sem uma asserçao vermelha — e o `alpha`
# tem duas escritas, uma no snapshot e outra no corrente, das quais so uma
# estava coberta.
_index = (ROOT / "index.html").read_text(encoding="utf-8")
# Um campo que o site guarda explicitamente e opcional por desenho. A guarda
# conta em qualquer sitio do ficheiro — um `s.x || 0` numa funcao de depuracao
# tirava `x` do contrato em todo o lado, e o contrato encolhia sozinho. Por isso
# os pisos abaixo tem folga: sao um minimo, nao a contagem de hoje.
def _opcionais_de(bloco):
    """Os campos que o proprio bloco guarda — e so esse bloco.

    Varrer o ficheiro inteiro fazia uma guarda defensiva escrita em qualquer
    sitio de quase cinco mil linhas retirar o campo do contrato em TODO o lado:
    uma linha `p.roman !== undefined` numa funcao de depuracao deixava o motor
    parar de escrever o numeral romano com a suite verde. A porta de saida tem
    de estar onde a leitura esta.
    """
    return set(_re_ct.findall(
        r'\b[psch]\.([a-zA-Z_][a-zA-Z0-9_]*)\s*'
        r'(?:!==\s*undefined|!=\s*null|\?\?|\|\|)', bloco))

# ── os ids das sentinelas que o site procura sao os que o motor escreve ─────
#
# O contrato dos campos prende o NOME dos campos, nao o VALOR do id. E o
# index.html procura sentinelas por id fixo: `find(s => s.id === 'unemployment')`.
# Renomear o id de um dos lados fazia o painel desaparecer em silencio — que e,
# a letra, o defeito historico: durante 26 edicoes a newsletter reportou
# "ICSA: N/A" porque era lida por 'icsa' e o motor escrevia 'jobless'.
_ids_procurados = set(_re_ct.findall(r"s\.id\s*===\s*'([a-z_]+)'", _index)) | \
                  set(_re_ct.findall(r's\.id\s*===\s*"([a-z_]+)"', _index))
_ids_produzidos = {x.get("id") for x in _sentinelas}
true(_ids_procurados,
     f"o site procura sentinelas por id ({sorted(_ids_procurados)})")
eq(sorted(_ids_procurados - _ids_produzidos), [],
   f"e todos os ids que o site procura sao escritos pelo motor "
   f"(procurados {sorted(_ids_procurados)}, produzidos {sorted(_ids_produzidos)})")

# As regioes que consomem a linha do historico. Uma janela de caracteres a
# partir do `map` nao chegava: a `logReason(s)` recebe a linha inteira e le o
# custo e o motivo, e a `renderEquityCurve` le o benchmark — as duas fora da
# janela. Uma funcao auxiliar que recebe o objecto inteiro e um ponto cego
# estrutural, e foi por ai que o `benchmark_spy_pnl_pct` podia desaparecer do
# grafico de equity publicado sem uma asserçao vermelha.
def _regiao(marca, tamanho):
    """A regiao do index.html que comeca em `marca`.

    Se a marca desaparecer, o que se quer NAO e um `ValueError: substring not
    found` a fechar os dois jobs de sexta por causa de uma refactorizacao
    inocua: e uma mensagem que diga o que aconteceu e o que fazer. Uma linha de
    codigo citada a letra e ela propria um acoplamento ao que o sistema
    reescreve — so que agora ao texto do codigo.
    """
    i = _index.find(marca)
    true(i >= 0,
         f"o index.html ainda tem a regiao que este contrato le "
         f"({marca[:60]!r}). Se o codigo do site foi reorganizado, actualizar "
         f"esta marca em tests/test_end_to_end.py — o contrato precisa de saber "
         f"onde e que o site le a linha do historico.")
    return _index[i:i + tamanho] if i >= 0 else ""

_bloco_hist = "".join((
    _regiao("tbody.innerHTML = [...data.history].reverse().map(s => {", 2500),
    _regiao("function logReason(s) {", 1200),
    _regiao("function renderEquityCurve(data) {", 1800),
))
_campos_hist = {m for m in _re_ct.findall(r'\bs\.([a-zA-Z_][a-zA-Z0-9_]*)', _bloco_hist)}
_campos_hist -= _opcionais_de(_bloco_hist)
true(len(_campos_hist) >= 4,
     f"a leitura do index.html encontra os campos do historico ({sorted(_campos_hist)})")
_linha_hist = pf["history"][-1]
_faltam_h = sorted(_campos_hist - set(_linha_hist))
eq(_faltam_h, [],
   f"a linha do historico traz todos os campos que a tabela do site le "
   f"(faltam {_faltam_h}; tem {sorted(_linha_hist)})")

# ── e o site le os mesmos campos que o motor escreve ───────────────────────
#
# Enumerar cinco campos das sentinelas nao chegou: outros vinte e tres, todos
# lidos pelo index.html, podiam desaparecer ou mudar de nome com a suite verde —
# sem `value`/`threshold` a barra do limiar fica `width:NaN%`, sem `status` toda
# a sentinela diz "Within Normal Range" a beira do gatilho, sem `description`
# sai "undefined" na caixa de alerta. As chaves saem outra vez do consumidor.
# Um campo que o site guarda explicitamente (`!== undefined`) e opcional por
# desenho — o `percentileRank` so existe no pilar da liquidez — e nao entra no
# contrato. A lista sai da propria guarda, nao de uma excepcao escrita a mao.
# A varredura restringe-se a regiao que desenha os pilares. Varrer o ficheiro
# inteiro a procura de `p.` fazia com que qualquer variavel de uma letra chamada
# `p` — numa funcao sem relacao nenhuma com pilares, num ficheiro de quase cinco
# mil linhas — fechasse a sexta com uma mensagem a mandar investigar o produtor
# de pilares.
_bloco_pilar = "".join((
    _regiao("const pillarsHTML = ", 6000),
    _regiao("(data.pillars || []).forEach(p => add(p.fredSeries));", 200),
    _regiao("    const live = p && p.band ? p.band : null;", 2600),
))
_campos_pilar = {m for m in _re_ct.findall(r'\bp\.([a-zA-Z_][a-zA-Z0-9_]*)', _bloco_pilar)}
_campos_pilar -= {"x", "y"} | _opcionais_de(_bloco_pilar)   # x/y: sparkline
true(len(_campos_pilar) >= 8,
     f"a leitura do index.html encontra os campos dos pilares ({sorted(_campos_pilar)})")
for _pil in data["pillars"]:
    _faltam = sorted(_campos_pilar - set(_pil))
    eq(_faltam, [],
       f"o pilar {_pil.get('id')} traz todos os campos que o site le dele "
       f"(faltam {_faltam})")

# Para as sentinelas a leitura restringe-se ao bloco que as desenha: o `s` do
# index.html tambem serve as entradas do historico noutras funcoes.
_bloco_sent = _regiao("const sentinelsHTML = data.sentinels.map(s => {", 3000)
_campos_sent = {m for m in _re_ct.findall(r'\bs\.([a-zA-Z_][a-zA-Z0-9_]*)', _bloco_sent)}
_campos_sent -= _opcionais_de(_bloco_sent)
true(len(_campos_sent) >= 5,
     f"a leitura do index.html encontra os campos das sentinelas ({sorted(_campos_sent)})")
for _sen in _sentinelas:
    _faltam = sorted(_campos_sent - set(_sen))
    eq(_faltam, [],
       f"a sentinela {_sen.get('id')} traz todos os campos que o site le dela "
       f"(faltam {_faltam})")

# ── o estado produzido fica onde o teste do site o possa ler ───────────────
#
# O `test_frontend.js` corre os renderers do site. Ate aqui alimentava-os com
# objectos escritos a mao — a forma invertida do defeito de sempre. Passa a
# correr sobre o que o motor ACABOU de escrever, e por isso o motor deixa-o
# aqui. Nao se usa o portfolio.json commitado porque ele e mais antigo do que o
# esquema actual: nao tem, por exemplo, o custo de transaccao, que o motor
# escreve hoje em todas as linhas.
_produzido = ROOT / "tests" / ".produzido"
_produzido.mkdir(exist_ok=True)
(_produzido / "portfolio.json").write_text(json.dumps(pf, indent=2), encoding="utf-8")
(_produzido / "data.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
# E a carteira em CRITICAL tambem: ha caminhos do site que so correm nesse
# regime — a caixa que explica qual dos dois sub-portfolios esta activo, que e a
# decisao sobre a manga de duracao. Com so o mundo calmo, esses caminhos nunca
# eram exercitados e os campos que eles leem nao entravam em contrato nenhum.
(_produzido / "portfolio_critical.json").write_text(json.dumps(pf2, indent=2),
                                                    encoding="utf-8")
(_produzido / "data_critical.json").write_text(json.dumps(data2, indent=2),
                                               encoding="utf-8")

# ── as regras publicadas sao as mesmas em todo o lado ────────────────────────
eq(data["rules"]["etfMap"], rules.as_dict()["etfMap"], "data.json publica o mapa canonico")
eq(data["rules"]["criticalWeights"]["Critical_Stress"], rules.CRITICAL_WEIGHTS["Critical_Stress"],
   "data.json publica os pesos canonicos")
eq(data["rules"]["resilientMax"], rules.RESILIENT_MAX, "data.json publica o limiar Resilient")
eq(data["rules"]["criticalMin"], rules.CRITICAL_MIN, "data.json publica o limiar Critical")

logging.disable(logging.NOTSET)
print(f"TODOS OS {ok} TESTES PASSARAM")
