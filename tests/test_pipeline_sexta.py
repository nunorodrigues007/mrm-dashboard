"""
A corrida de sexta-feira inteira, a partir do estado COMMITADO do repositorio.

Os outros testes exercitam pecas: as regras, o motor, o gerador, o JavaScript do
site. Este exercita a corrida — fetch_data -> update_portfolio -> send_newsletter
— sobre os ficheiros que estao mesmo no repositorio, nos dois mundos que
importam: a semana calma e a semana em que o medidor B dispara.

Existe por uma razao concreta. Um ensaio manual desta cadeia, feito antes da
primeira corrida real, apanhou a guarda do `build_context` a recusar publicar
porque o numero da edicao nao batia certo — e foi preciso ir verificar se era
defeito ou artefacto do ensaio (era artefacto: `get_last_friday()` e
`issue_number_for()` concordam em todos os dias da semana). Uma verificacao que
so existe quando alguem se lembra de a fazer nao e uma verificacao.

As asserçoes sao sobre INVARIANTES, nao sobre os numeros da semana: o
portfolio.json avanca todas as sextas, e um teste que fixasse o score ou o valor
comecaria a falhar sozinho.

Sem rede: FRED, yfinance, o modelo, o git e a Brevo sao todos substituidos.
"""
import contextlib, io, json, logging, os, shutil, sys, tempfile, types
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("FRED_API_KEY", "ensaio")
os.environ.setdefault("ANTHROPIC_API_KEY", "ensaio")
os.environ.setdefault("BREVO_API_KEY", "ensaio")

import fetch_data, mrm_gauge_b, mrm_rules as rules
import update_portfolio as up
import importlib.util as _iu
_sp = _iu.spec_from_file_location("sn_sexta", ROOT / "send_newsletter.py")
sn = _iu.module_from_spec(_sp); _sp.loader.exec_module(sn)
import test_build_data as T
from fixture_newsletter import edicao

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1
def true(cond, what): eq(bool(cond), True, what)

# A sexta a ensaiar sai do proprio repositorio: a semana a seguir a ultima que o
# portfolio.json tem. Assim o teste acompanha o ficheiro em vez de envelhecer.
_pf_repo = json.loads((ROOT / "portfolio.json").read_text(encoding="utf-8"))
_ultima = date.fromisoformat(_pf_repo["current"]["date"])
SEXTA = _ultima + timedelta(days=7)
while SEXTA.weekday() != 4:
    SEXTA += timedelta(days=1)
ISSUE = ((SEXTA - date(2026, 3, 13)).days // 7) + 1
# O dia em que a bolsa fechou nesta semana. Numa sexta de feriado — Juneteenth,
# Good Friday, 3 de Julho — nao e a sexta, e o motor data a carteira por ele.
NEGOCIACAO = up.adjust_for_market_holiday(SEXTA)
eq(ISSUE, _pf_repo["current"]["issue"] + 1,
   "a sexta a ensaiar e a semana seguinte a ultima do portfolio.json")

# ── observacoes construidas RELATIVAMENTE a sexta ensaiada ──────────────────
#
# As datas vivem no fixture_obs, partilhado com os outros testes-portao: cada
# serie e datada pela sua propria cadencia a contar da sexta simulada. Datas
# gravadas caducariam contra os prazos do data_freshness — e um portao caducado
# nao da um teste vermelho, da uma semana sem carteira e sem newsletter.
from fixture_obs import (obs_base as _obs_base_de, mes_menos as _mes_menos,
                         verifica_frescura, poe_edicao_anterior)

def _obs_base():
    return _obs_base_de(SEXTA)

# O fixture nao pode envelhecer: verifica-se que esta dentro do prazo nesta
# semana e daqui a um ano.
verifica_frescura(SEXTA, eq)

PRECOS = {"SPY": 662.0, "IEF": 95.0, "LQD": 110.0, "PDBC": 14.0, "BIL": 91.5,
          "VNQ": 88.0, "USMV": 90.0, "SHY": 82.0, "SGOV": 100.5, "GLD": 250.0,
          "TLT": 88.0, "QQQ": 560.0, "HYG": 79.0, "IWO": 260.0}


def corre(stress, sem_preco=None, regime_detido=None, carteira=None,
          sem_cotacao=None):
    """Uma corrida completa num directorio temporario. Devolve o que aconteceu.

    `sem_preco` tira a cotacao — e o preco de recurso — a um instrumento, que e
    como se constroi a semana em que os medidores pedem uma coisa e o motor
    recusa faze-la."""
    OBS = _obs_base()
    if stress:
        OBS["SAHMREALTIME"] = [{"date": _mes_menos(SEXTA, i).isoformat(), "value": v}
                               for i, v in enumerate(("0.62", "0.55", "0.40"), start=1)]

    class R:
        def __init__(s, sid, limit=None, asc=False):
            s.sid, s.limit, s.asc = sid, limit, asc
        def raise_for_status(s): pass
        def json(s):
            obs = sorted(OBS.get(s.sid, []), key=lambda o: o["date"], reverse=not s.asc)
            return {"observations": obs[:int(s.limit)] if s.limit else obs}

    def fake_get(url, params=None, timeout=None):
        p = params or {}
        return R(p.get("series_id"), p.get("limit"), p.get("sort_order") == "asc")

    tmp = Path(tempfile.mkdtemp())
    # O score_history e o sent_issues sao opcionais para o motor; copiam-se se
    # existirem, em vez de rebentar com FileNotFoundError onde o codigo tolera.
    for f in ("data.json", "portfolio.json", "index.html"):
        shutil.copy(ROOT / f, tmp)
    if carteira is not None:
        # A carteira de entrada e a SAIDA de uma corrida anterior, nao um estado
        # construido a mao: e assim que se exercita o que o motor consome de si
        # proprio na semana seguinte.
        (tmp / "portfolio.json").write_text(json.dumps(carteira), encoding="utf-8")
    # O `sent_issues.json` NAO se copia. E estado que o proprio job reescreve, e
    # o RUNBOOK manda edita-lo a mao na recuperacao de erros: se a marca
    # commitada nomear a semana ensaiada, o `main()` devolve cedo ("ja foi
    # enviada") e o ensaio — que e portao dos dois jobs — fica vermelho por uma
    # razao que nada tem a ver com o que ele verifica. O ensaio comeca sem marca
    # nenhuma, que e o estado de uma sexta que ainda nao correu.
    if (ROOT / "score_history.json").exists():
        shutil.copy(ROOT / "score_history.json", tmp)
    if sem_preco:
        # Sem cotacao E sem preco de recurso: e assim que a valorizacao fica
        # mesmo incompleta.
        _p = json.loads((tmp / "portfolio.json").read_text())
        _p["current"]["last_prices"] = {
            t: v for t, v in (_p["current"].get("last_prices") or {}).items()
            if t not in sem_preco}
        _p["current"]["last_price_dates"] = {
            t: v for t, v in (_p["current"].get("last_price_dates") or {}).items()
            if t not in sem_preco}
        (tmp / "portfolio.json").write_text(json.dumps(_p))
    if regime_detido:
        # A carteira JA esta noutro regime. Nao e um caso exotico: e o estado do
        # sistema durante uma crise, e a primeira versao deste teste travava o
        # pipeline exactamente ai — afirmava "hold" numa semana em que a
        # carteira sai de Critical.
        _p = json.loads((tmp / "portfolio.json").read_text())
        _sub = "Critical_Stress" if regime_detido == "Critical" else None
        _mapa = rules.REGIME_ETF_MAP[rules.resolve_etf_map_key(regime_detido, _sub)]
        _p["current"].update({
            "regime": regime_detido, "critical_subregime": _sub,
            "active_etf_map": dict(_mapa),
            "shares": {t: 10.0 for t in _mapa.values()},
            "last_prices": {t: PRECOS[t] for t in _mapa.values()},
            "last_price_dates": {t: _p["current"]["date"] for t in _mapa.values()},
            "bucket_allocation_pct": (dict(rules.CRITICAL_WEIGHTS[_sub]) if _sub
                                      else _p["current"]["bucket_allocation_pct"])})
        if _p.get("history"):
            _p["history"][-1].update({"regime": regime_detido,
                                      "critical_subregime": _sub})
        (tmp / "portfolio.json").write_text(json.dumps(_p))
    shutil.copy(ROOT / "data.json", tmp / "data_prev.json")
    # A edicao N-1 vem pelo mesmo caminho que os outros testes-portao usam: a
    # publicada quando o parser a le, o fixture quando nao ha nenhuma. Este
    # ficheiro tinha um glob proprio, sem rede: numa semana em que a edicao
    # anterior nao chegou a ser publicada, o ensaio corria sem alocacao nenhuma
    # para ler.
    poe_edicao_anterior(ROOT, tmp, ISSUE - 1, SEXTA - timedelta(days=7))

    class _Data(date):
        @classmethod
        def today(cls): return SEXTA
    class _DataHora(datetime):
        @classmethod
        def utcnow(cls): return datetime(SEXTA.year, SEXTA.month, SEXTA.day, 22, 5)

    guard = (fetch_data.requests.get, fetch_data.fetch_liquidity_percentile,
             mrm_gauge_b.requests.get, fetch_data.__file__,
             up.fetch_prices, up.date, up.agora_utc, sn.datetime,
             sn.call_model, sn.git_publish, sn.brevo_subscribers, sn.brevo_send)
    fetch_data.requests.get = fake_get
    fetch_data.fetch_liquidity_percentile = lambda hoje=None: T.fake_liquidity()
    mrm_gauge_b.requests.get = fake_get
    # `sem_preco` = sem cotacao E sem recurso. `sem_cotacao` = a cotacao
    # falhou mas o recurso da semana anterior existe — que e o caso normal
    # de uma falha do yfinance, e o que exercita a sobrevivencia do recurso.
    _falta = set(sem_preco or ()) | set(sem_cotacao or ())
    up.fetch_prices = lambda t, d, retries=3: (
        {x: (None if x in _falta else PRECOS[x]) for x in t},
        {x: (None if x in _falta else str(d)) for x in t},
        {x: (x in _falta) for x in t})
    up.date = _Data
    # O motor mede a idade do data.json pelo mesmo relogio simulado que tudo o
    # resto desta corrida. Sem isto, media-a contra o relogio real: a idade que
    # o ensaio via era `SEXTA - agora_real`, dezenas de horas a mudar com o dia
    # em que a suite corre, e o ensaio passava sempre pelo ramo "dados velhos" —
    # o caminho da semana saudavel, que e o que corre 51 vezes por ano, nunca
    # era exercitado.
    up.agora_utc = lambda: datetime(SEXTA.year, SEXTA.month, SEXTA.day, 22, 0)
    sn.datetime = _DataHora
    enviados, publicados, prompts = [], [], []
    sn.git_publish = lambda files, msg, **kw: (publicados.append(list(files)), True)[1]
    sn.brevo_subscribers = lambda k: ["a@exemplo.com", "b@exemplo.com"]
    sn.brevo_send = lambda k, s_, to, subj, html, **kw: (
        enviados.append((to, subj, html)), (True, types.SimpleNamespace(status_code=200)))[1]

    cwd = os.getcwd(); os.chdir(tmp)
    fetch_data.__file__ = str(tmp / "fetch_data.py")
    logging.disable(logging.CRITICAL)
    erro = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fetch_data.build_data(SEXTA)
            # O `build_data` carimba o `generatedAt` com o relogio REAL, mas o
            # resto da corrida esta fixado na semana simulada. A idade que o
            # gerador via era `SEXTA - agora_real` — dezenas de horas, a mudar
            # com o dia em que a suite corre — e o ensaio passava sempre pelo
            # ramo "dados velhos", nunca pelo da semana saudavel. O ensaio tem
            # de ser o da sexta que vai correr, em que o data.json acabou de ser
            # escrito uma hora antes do job da newsletter.
            _dj = json.loads((tmp / "data.json").read_text())
            _dj.setdefault("meta", {})["generatedAt"] = datetime(
                SEXTA.year, SEXTA.month, SEXTA.day, 21, 0).strftime("%Y-%m-%dT%H:%M:%SZ")
            (tmp / "data.json").write_text(json.dumps(_dj))
            try:
                up.main()
            except SystemExit:
                pass
            _score = json.loads((tmp / "data.json").read_text())["globalResilienceScore"]

            def _modelo(p_, k_):
                """Um modelo que faz o que lhe e pedido: escreve o score do
                motor e reproduz os avisos de qualidade de dados. Extrai-os do
                proprio prompt, que e onde o gerador os poe."""
                prompts.append(p_)
                avisos = [l.strip()[2:].strip() for l in p_.splitlines()
                          if l.strip().startswith("! DATA QUALITY")]
                return edicao(ISSUE, score=_score, avisos=avisos)

            sn.call_model = _modelo
            sn.main()
    except BaseException as e:
        erro = e
    finally:
        logging.disable(logging.NOTSET)
        os.chdir(cwd)
        (fetch_data.requests.get, fetch_data.fetch_liquidity_percentile,
         mrm_gauge_b.requests.get, fetch_data.__file__,
         up.fetch_prices, up.date, up.agora_utc, sn.datetime,
         sn.call_model, sn.git_publish, sn.brevo_subscribers, sn.brevo_send) = guard

    # Dentro do finally do bloco acima nao da: o que se le aqui e o resultado da
    # corrida. Mas o directorio temporario tem de desaparecer mesmo que a
    # leitura rebente.
    try:
        _marca = tmp / "sent_issues.json"
        r = {"data": json.loads((tmp / "data.json").read_text()),
             "pf": json.loads((tmp / "portfolio.json").read_text()),
             "marca": (json.loads(_marca.read_text()) if _marca.exists()
                       else {"sent": []}),
             "edicoes": sorted(p.name for p in tmp.glob("MRM_Newsletter_Issue*.html")),
             "index": (tmp / "index.html").read_text(encoding="utf-8"),
             "enviados": enviados, "publicados": publicados, "prompts": prompts,
             "erro": erro}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return r


def verifica(r, nome, stress, antes):
    """As invariantes da corrida.

    Invariante quer dizer: verdadeiro em qualquer semana, com qualquer estado
    commitado. A primeira versao deste bloco afirmava RESULTADOS — "o motivo e
    hold", "o custo e zero", "o sub-regime e Stress" — e chamava-lhes
    invariantes. Não são: numa semana semestral o ramo calmo rebalanceia, e na
    semana a seguir a carteira entrar em Critical o ramo calmo sai de Critical.
    Como este teste e portao dos dois jobs de sexta, cada uma dessas asserçoes
    falsas nao daria um teste vermelho — daria uma semana sem carteira e sem
    newsletter, exactamente na semana em que o sistema faz o que existe para
    fazer.

    O que se afirma agora e a coerencia: o motor concorda com as regras, e o que
    se publica descreve o que se detem.
    """
    true(r["erro"] is None, f"{nome}: a corrida termina sem excepcao ({r['erro']!r})")

    h = r["pf"]["history"][-1]
    cur = r["pf"]["current"]
    eq(h["issue"], ISSUE, f"{nome}: o motor escreve a edicao desta semana")
    # A data gravada e o ultimo dia de NEGOCIACAO da semana, que nem sempre e a
    # sexta: em 19 de Junho de 2026 — Juneteenth, uma sexta com a bolsa fechada
    # — o motor data a carteira de quinta, e a versao anterior desta linha
    # afirmava a sexta. Como este teste e portao dos dois jobs, isso nao daria
    # um teste vermelho nessa semana: daria uma semana sem carteira e sem
    # newsletter, num feriado que volta todos os anos.
    eq(cur["date"], NEGOCIACAO.isoformat(),
       f"{nome}: com a data do ultimo dia de negociacao da semana")
    # E o NUMERO da edicao continua a vir da sexta, nao do dia ajustado: um
    # feriado nao pode saltar nem repetir uma edicao.
    eq(h["issue"], ((SEXTA - date(2026, 3, 13)).days // 7) + 1,
       f"{nome}: e o numero da edicao sai da sexta, nao do dia de negociacao")

    # ── a carteira descreve-se a si propria ──────────────────────────────────
    _soma = sum(cur["bucket_allocation_pct"].values())
    true(abs(_soma - 100.0) < 0.01,
         f"{nome}: a alocacao publicada soma 100 (obtido {_soma:.2f})")
    _fora = sorted(t for t in cur["shares"] if t not in PRECOS)
    eq(_fora, [], f"{nome}: o teste conhece o preco de todos os instrumentos detidos")
    _valor = sum(q * PRECOS[t] for t, q in cur["shares"].items() if q)
    for _campo in ("portfolio_value",):
        true(abs(_valor - h[_campo]) < 1.0,
             f"{nome}: o {_campo} do historico bate com as accoes "
             f"({_valor:.2f} vs {h[_campo]})")
        true(abs(_valor - cur[_campo]) < 1.0,
             f"{nome}: e o do `current`, que e o que o site le "
             f"({_valor:.2f} vs {cur[_campo]})")
    true(h.get("transaction_cost_usd") is not None,
         f"{nome}: o custo de transaccao e declarado")
    # Detem-se um subconjunto do mapa activo: um bucket a 0% — legitimo, as
    # bandas permitem-no em quatro dos seis — nao tem instrumento nenhum.
    true(set(cur["shares"]) <= set(cur["active_etf_map"].values()),
         f"{nome}: nao detem nada fora do mapa activo "
         f"({sorted(set(cur['shares']) - set(cur['active_etf_map'].values()))})")

    # ── o motivo publicado explica o que aconteceu ──────────────────────────
    #
    # Nao se tenta replicar a chamada interna do motor: ele chama
    # `decide_rebalance` com o regime CONFIRMADO e com o motivo de emergencia,
    # nao com o sinalizado e None. Replicar mal era ficar com uma asserçao que
    # mente no dia em que a entrada confirmada em Resilient acontecer.
    #
    # O que se afirma e a coerencia entre a transiçao observada e o motivo
    # declarado — verdadeiro em qualquer semana, sem conhecer as entranhas.
    _motivo = h["rebalance_reason"]
    true(_motivo in rules.REBALANCE_COPY
         or _motivo.startswith("critical_subregime_switch")
         or _motivo.startswith("emergency_resilient"),
         f"{nome}: o motivo publicado e do vocabulario canonico ({_motivo})")

    _mudou_regime = cur["regime"] != antes["regime"]
    _mudou_sub = cur.get("critical_subregime") != antes["critical_subregime"]
    if h["rebalance_triggered"]:
        _transicao = ("stress_on", "stress_off", "stress_off_to_resilient",
                      "resilient_off", "semestral_rebalance")
        true(_motivo in _transicao or _motivo.startswith("critical_subregime_switch")
             or _motivo.startswith("emergency_resilient"),
             f"{nome}: uma semana que negoceia declara um motivo de transiccao "
             f"ou o semestral ({_motivo})")
        if _mudou_regime:
            true(_motivo != "semestral_rebalance",
                 f"{nome}: uma mudanca de regime nao se chama semestral ({_motivo})")
        else:
            true(_motivo == "semestral_rebalance"
                 or _motivo.startswith("critical_subregime_switch"),
                 f"{nome}: sem mudanca de regime, so o semestral ou uma troca de "
                 f"sub-regime justificam negociar ({_motivo})")
    else:
        # Nao negociou: nada se pode ter mexido.
        eq(cur["shares"], antes["shares"],
           f"{nome}: sem transaccoes, as posicoes ficam como estavam")
        eq(h["transaction_cost_usd"], 0.0,
           f"{nome}: e uma semana sem transaccoes nao paga custos")
        eq(cur["regime"], antes["regime"],
           f"{nome}: nem o regime ({antes['regime']} -> {cur['regime']})")
        eq(cur.get("critical_subregime"), antes["critical_subregime"],
           f"{nome}: nem o sub-regime")
    if _mudou_sub and not _mudou_regime and h["rebalance_triggered"]:
        true(_motivo.startswith("critical_subregime_switch"),
             f"{nome}: uma troca de sub-regime declara-se como tal ({_motivo})")

    # ── a newsletter ────────────────────────────────────────────────────────
    _nova = f"MRM_Newsletter_Issue{ISSUE}_{SEXTA.strftime('%d%b%Y')}.html"
    true(_nova in r["edicoes"], f"{nome}: gravou a edicao desta semana ({r['edicoes']})")
    eq([e for e in r["edicoes"] if f"Issue{ISSUE}_" in e], [_nova],
       f"{nome}: e so uma, sem duplicados com outra data")
    _dest = [e[0] for e in r["enviados"]]
    eq(sorted(_dest), sorted(["a@exemplo.com", "b@exemplo.com", "usmrm@proton.me"]),
       f"{nome}: cada subscritor recebeu uma vez, mais o briefing ao dono")
    eq(len(_dest), len(set(_dest)), f"{nome}: ninguem recebeu duas vezes")
    _m = r["marca"]["sent"][-1]
    eq(_m["issue"], ISSUE, f"{nome}: a marca de envio e desta edicao")
    eq(_m["complete"], True, f"{nome}: e a entrega ficou completa")
    eq(r["publicados"],
       [[_nova, "index.html"], ["sent_issues.json"], ["data_prev.json"]],
       f"{nome}: tres commits, pela ordem certa (obtido {r['publicados']})")
    true(_nova in r["index"], f"{nome}: o cartao entrou no arquivo do site")

    # O assunto leva o regime DETIDO, nao o sinalizado — e a carteira que se
    # relata, nao o que os medidores pediram.
    # O rotulo completo, sub-regime incluido — e o mesmo que o build_context
    # monta a partir do `current`, e nao o do regime sinalizado.
    _assunto = r["enviados"][0][1]
    _rot = rules.REGIME_LABELS.get(cur["regime"], cur["regime"])
    if cur["regime"] == "Critical" and cur["critical_subregime"]:
        _rot += " \u00b7 " + rules.SUBREGIME_LABELS.get(cur["critical_subregime"],
                                                        cur["critical_subregime"])
    true(_assunto.rstrip().endswith(_rot),
         f"{nome}: o assunto termina no regime da carteira ({_assunto!r}, "
         f"esperado terminar em {_rot!r})")
    true(f"#{ISSUE}" in _assunto, f"{nome}: e o numero da edicao")
    eq(r["enviados"][0][2], r["enviados"][1][2],
       f"{nome}: os dois subscritores receberam a MESMA edicao")

    # ── e o medidor mandou no regime ────────────────────────────────────────
    _sg = r["data"]["stressGauge"]
    eq(_sg["active"], stress, f"{nome}: o medidor esta como o cenario pede")
    if stress:
        eq(cur["regime"], "Critical", f"{nome}: com o medidor ligado, a carteira e Critical")
        true(cur["critical_subregime"] in ("Critical_FTQ", "Critical_Stress"),
             f"{nome}: com um sub-regime declarado ({cur['critical_subregime']})")
        # A porta assimetrica: o TLT so existe no mapa FTQ.
        eq("TLT" in cur["shares"], cur["critical_subregime"] == "Critical_FTQ",
           f"{nome}: o TLT so aparece em Critical_FTQ")
        if antes["regime"] != "Critical":
            eq(cur["critical_subregime"], "Critical_Stress",
               f"{nome}: uma entrada FRESCA em Critical e sempre Stress")
    else:
        true(cur["regime"] != "Critical",
             f"{nome}: com o medidor desligado, a carteira nao fica em Critical")
        true(not cur["critical_subregime"],
             f"{nome}: nem com sub-regime ({cur['critical_subregime']})")


# O estado ANTERIOR — o que entra na corrida — sai do repositorio.
ANTES = {"regime": _pf_repo["current"].get("regime", "Turbulence"),
         "critical_subregime": _pf_repo["current"].get("critical_subregime"),
         "shares": dict(_pf_repo["current"].get("shares") or {})}

for _nome, _stress in (("sexta normal", False), ("sexta com o medidor B a disparar", True)):
    _r = corre(_stress)
    verifica(_r, _nome, _stress, ANTES)
    # A semana ensaiada e uma semana SAUDAVEL: dados frescos, precos todos, nada
    # a avisar. Ate esta ronda o ensaio corria sempre pelo ramo dos dados
    # velhos, porque o carimbo do data.json ficava no relogio real enquanto o
    # resto da corrida estava fixado na semana simulada — e o caminho que corre
    # 51 vezes por ano nunca era exercitado. O ramo dos dados velhos tem os seus
    # proprios testes; este e o da semana normal.
    # O que esta linha existe para apanhar e uma coisa so: o ensaio a medir a
    # idade do data.json contra o relogio errado. Ate esta ronda media-a contra
    # o relogio real enquanto tudo o resto estava fixado na semana simulada, e a
    # corrida passava sempre pelo ramo dos dados velhos — o caminho que corre 51
    # vezes por ano nunca era exercitado.
    #
    # E so isso que se afirma. A primeira versao afirmava "nenhum aviso excepto o
    # da ancora dos earnings", o que era um portao novo pelo mesmo defeito de
    # sempre: numa semana semestral com a edicao anterior ilegivel, o motor faz o
    # que deve, a newsletter leva o aviso verdadeiro, e o portao fechava-se.
    _linhas = [l.strip()[2:].strip() for l in "".join(_r["prompts"]).splitlines()
               if l.strip().startswith("! DATA QUALITY")]
    _idade = [l for l in _linhas
              if "readings in this edition are" in l or "does not say when it was" in l]
    eq(_idade, [],
       f"{_nome}: o ensaio mede a idade do data.json pelo relogio da semana "
       f"simulada, nao pelo relogio real (obtidos {_idade})")

# ── a carteira noutro regime: nao e caso exotico, e a crise ─────────────────
#
# A primeira versao deste teste travava o pipeline exactamente aqui: afirmava
# "hold" no ramo calmo, e com a carteira em Critical o ramo calmo devolve
# `stress_off`. Ou seja, no momento em que o sistema faz o que existe para
# fazer, o portao fechava e a semana ficava sem carteira e sem newsletter.
for _detido in ("Critical", "Resilient"):
    _antes_d = {"regime": _detido,
                "critical_subregime": "Critical_Stress" if _detido == "Critical" else None,
                "shares": {t: 10.0 for t in rules.REGIME_ETF_MAP[
                    rules.resolve_etf_map_key(
                        _detido, "Critical_Stress" if _detido == "Critical" else None)].values()}}
    for _nome_d, _stress_d in ((f"medidor desligado, carteira em {_detido}", False),
                               (f"medidor ligado, carteira em {_detido}", True)):
        verifica(corre(_stress_d, regime_detido=_detido), _nome_d, _stress_d, _antes_d)

# ── terceiro mundo: os medidores pedem, o motor recusa ──────────────────────
#
# O medidor B dispara, mas um instrumento detido nao tem cotacao nem preco de
# recurso. O valor da carteira esta incompleto, e dimensionar posicoes novas
# sobre ele perderia esse capital — por isso o rebalanceamento e CANCELADO.
#
# E a unica semana em que o regime SINALIZADO e o DETIDO divergem, e por isso a
# unica que distingue os dois. Nos outros dois mundos coincidem, e uma asserçao
# sobre "o assunto leva o regime da carteira" passava na mesma se levasse o
# sinalizado.
_alvo = sorted(ANTES["shares"])[0]
_r3 = corre(stress=True, sem_preco={_alvo})
_h3 = _r3["pf"]["history"][-1]
_c3 = _r3["pf"]["current"]

true(_r3["erro"] is None, f"a corrida termina sem excepcao ({_r3['erro']!r})")
eq(_h3["regime_signalled"], "Critical", "os medidores pedem Critical")
eq(_c3["regime"], ANTES["regime"], "mas a carteira fica onde estava")
eq(_h3["rebalance_triggered"], False, "e nao ha transaccoes")
# O MOTIVO depende de ter havido gatilho: com a carteira ja em Critical nao ha
# rotacao a cancelar, e a semana e um `hold` normal. Afirmar
# "valuation_incomplete_held" seria repetir o erro que este ficheiro corrigiu —
# afirmar um resultado e chamar-lhe invariante.
_havia_gatilho = rules.decide_rebalance(
    "Critical", ANTES["regime"], _h3.get("critical_subregime_signalled"),
    ANTES["critical_subregime"], rules.is_semestral_rebalance_week(SEXTA), None)
eq(_h3["rebalance_reason"],
   "valuation_incomplete_held" if _havia_gatilho else "hold",
   f"o motivo declarado corresponde a ter havido gatilho ou nao "
   f"(gatilho={_havia_gatilho!r})")
eq(_c3["shares"], ANTES["shares"], "as posicoes ficam exactamente como estavam")
eq(_h3["portfolio_pnl_pct"], None,
   "o P&L e suprimido em vez de calculado sobre uma carteira parcial")
true(_alvo in (_h3.get("valuation_missing") or []),
     f"e o instrumento por valorizar e nomeado ({_h3.get('valuation_missing')})")

# O que chega aos subscritores tem de ser a CARTEIRA, nao o sinal.
_ass3 = _r3["enviados"][0][1]
_rot3 = rules.REGIME_LABELS.get(_c3["regime"], _c3["regime"])
if _c3["regime"] == "Critical" and _c3.get("critical_subregime"):
    _rot3 += " \u00b7 " + rules.SUBREGIME_LABELS.get(_c3["critical_subregime"],
                                                     _c3["critical_subregime"])
true(_ass3.rstrip().endswith(_rot3),
     f"o assunto leva o regime DETIDO, nao o sinalizado "
     f"({_ass3!r}, esperado terminar em {_rot3!r})")
if _c3["regime"] != "Critical":
    true("Critical" not in _ass3,
         f"e nao anuncia uma rotacao que nao aconteceu ({_ass3!r})")
_html3 = _r3["enviados"][0][2]
true("DATA QUALITY" in _html3,
     "e a edicao leva o aviso de qualidade de dados")
ok += 1

# ── as duas contas do numero da edicao concordam, em todos os dias ───────────
# O motor conta a partir da ultima sexta de calendario; o gerador a partir de
# hoje. Sao dois calculos do mesmo numero, e enquanto concordarem esta bem — mas
# nada o garantia. Se divergirem, a guarda do build_context recusa publicar e a
# semana fica sem newsletter.
for _delta in range(0, 21):
    _d = SEXTA + timedelta(days=_delta)
    class _D(date):
        @classmethod
        def today(cls): return _d
    _real, up.date = up.date, _D
    try:
        _lf = up.get_last_friday()
    finally:
        up.date = _real
    _motor = ((_lf - date(2026, 3, 13)).days // 7) + 1
    eq(_motor, sn.issue_number_for(_d),
       f"a {_d} ({_d.strftime('%a')}) o motor e o gerador contam a mesma edicao")

# ── Duas semanas seguidas: o motor le o que ele proprio escreveu ───────────
#
# Todos os cenarios acima constroem o estado de ENTRADA a mao. Nenhum encadeia
# duas corridas — e e ai que vive uma classe inteira de defeito: o que o motor
# consome de si proprio na semana seguinte nao tinha contrato nenhum.
#
# Dois exemplos concretos, os dois com comentario no codigo a dizer que estao
# corrigidos e nenhum com teste: se o `last_prices` do snapshot deixar de fundir
# o recurso da semana anterior, um instrumento sem cotacao desaparece do recurso
# e na semana seguinte volta a estar em falta — "um bloqueio permanente do
# rebalanceamento, com o medidor ligado e a carteira no mapa errado". E se o
# `last_price_dates` nao for escrito, o recurso deixa de ter idade e nunca
# expira: "nove semanas com o mesmo preco e o mesmo P&L ao centimo, sem um
# aviso".
_falta = sorted(rules.REGIME_ETF_MAP["Turbulence"].values())[0]
_semana1 = corre(False, sem_cotacao={_falta})
_pf1 = _semana1["pf"]["current"]
true(_falta in (_pf1.get("last_prices") or {}),
     f"o instrumento sem cotacao MANTEM o preco de recurso da semana anterior "
     f"({_falta} em {sorted(_pf1.get('last_prices') or {})})")
true(_falta in (_pf1.get("last_price_dates") or {}),
     f"e a data do recurso e escrita, para ele poder expirar "
     f"({_falta} em {sorted(_pf1.get('last_price_dates') or {})})")
true((_pf1.get("last_price_dates") or {}).get(_falta) < SEXTA.isoformat(),
     f"e a data e a do fecho antigo, nao a da corrida — senao o recurso nunca "
     f"envelhece ({(_pf1.get('last_price_dates') or {}).get(_falta)} vs {SEXTA})")

# E a semana SEGUINTE corre sobre esse estado, sem ninguem lho construir.
_estado2 = json.loads(json.dumps(_semana1["pf"]))
_SEXTA1 = SEXTA
try:
    globals()["SEXTA"] = SEXTA + timedelta(days=7)
    globals()["ISSUE"] = ISSUE + 1
    _semana2 = corre(False, sem_cotacao={_falta}, carteira=_estado2)
finally:
    globals()["SEXTA"], globals()["ISSUE"] = _SEXTA1, ISSUE - 1
_pf2 = _semana2["pf"]["current"]
true(_semana2["erro"] is None,
     f"a semana seguinte corre sobre o que o motor escreveu ({_semana2['erro']!r})")
true(_falta in (_pf2.get("last_prices") or {}),
     f"e o recurso do instrumento sem cotacao sobrevive a segunda semana "
     f"({_falta} em {sorted(_pf2.get('last_prices') or {})})")
eq((_pf2.get("last_price_dates") or {}).get(_falta),
   (_pf1.get("last_price_dates") or {}).get(_falta),
   "com a data do fecho original — o recurso envelhece, nao se renova sozinho")

print(f"TODOS OS {ok} TESTES PASSARAM")
