"""Estende os dados do backtest com os meses fechados que ainda faltam.

    python backtest/estender_dados.py              # ate ao ultimo mes fechado
    python backtest/estender_dados.py --ate 2026-10
    python backtest/estender_dados.py --verificar  # mostra o que faria, nao escreve

Corre todos os meses, pelo workflow `backtest-mensal.yml`, mas pode correr-se a
mao ou por qualquer agente: precisa de Python 3.11, do `requirements.txt` e de um
clone com o historico COMPLETO do repositorio (`git clone` normal, sem `--depth`),
porque os valores macro vem das revisoes do `data.json` no git.

O metodo e o fixado a 18 de Novembro de 2026 (doc `USMRM_Backtest_Estendido_18Nov`):

  Macro — da ultima revisao do `data.json` cujos dados sao desse mes. E o vintage
  verdadeiro do mes, o que a producao sabia nessa altura. Consultar o FRED hoje
  traria valores ja revistos, ou seja hindsight. Mapeamento:

      spread          pillars.cycle.metricValue
      buffett / pct   pillars.liquidity.metricValue / .percentileRank
      dsr             pillars.debt.metricValue
      ep_real         pillars.premium.epValue
      erp_real        pillars.premium.metricValue
      y10             epValue - metricValue
      erp_frozen      4,55 - y10            (o E/P congelado do sistema v1)
      sahm            stressGauge.triggers.sahmRealtime (value, asOf)
      dralacbn        pillars.solvency.metricValue, no trimestre de
                      stressGauge.triggers.delinquencyAccel.asOf

  A delinquencia de cada mes no `scores.json` segue a regra declarada do
  backtest — a ultima observacao trimestral com pelo menos 5 meses — e nao o
  valor da producao, porque e essa regra que o `test_scores_reconstruction.py`
  prende. Quando as duas diferem, fica escrito no relatorio desta corrida.

  Precos — fecho AJUSTADO de fim de mes (yfinance, `auto_adjust=True`, retorno
  total), ENCADEADO pelo retorno: preco[M] = preco_commitado[M-1] x
  ajustado[M] / ajustado[M-1], ambos os ajustados da mesma descarga. A historia
  commitada nao e tocada (refaze-la reescreve 19 anos: ate 4% no DBC), e o
  encadeamento evita a junta que a copia directa do preco deixava, porque a
  Yahoo re-ajusta a historia a cada distribuicao.

  Pilares, composto e rotulo de regime saem de `mrm_rules` — score_pillar,
  global_score, score_band — exactamente o que o teste de reconstrucao verifica.

Falha ruidosamente (sys.exit(1)) e NAO escreve nada se faltar um mes de
revisoes, um campo vier nulo, um preco faltar, ou se a aceleracao da
delinquencia calculada nao bater com a que a producao publicou. Ou escreve tudo
ou nao escreve nada.
"""
import argparse
import csv
import datetime as dt
import io
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / "data"
sys.path.insert(0, str(ROOT))
import mrm_rules as rules  # noqa: E402

EP_CONGELADO = 4.55          # o E/P constante do sistema v1 (erp_frozen)
LAG_NPL_MESES = 5            # o mesmo atraso que o test_scores_reconstruction prende
TICKERS = ["SPY", "IEF", "LQD", "DBC", "SHV", "VNQ", "TLT", "GLD",
           "QQQ", "SHY", "HYG", "IWO"]
CAMPO = {"cycle": "spread", "liquidity": "buffett_pct", "solvency": "npl", "debt": "dsr"}


def falha(msg):
    print(f"::error::{msg}")
    sys.exit(1)


def madd(m, n):
    y, mo = int(m[:4]), int(m[5:7]) + n
    y += (mo - 1) // 12
    mo = (mo - 1) % 12 + 1
    return f"{y:04d}-{mo:02d}"


def meses_entre(a, b):
    return (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])


# ── revisoes do data.json ───────────────────────────────────────────────────
def _git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                          capture_output=True, text=True).stdout


def data_dos_dados(d):
    """O dia a que os dados de uma revisao se referem: a observacao do 10Y, que
    e a serie diaria que entra no score. A hora do commit nao serve — a corrida
    de sexta 31 aterra no git no sabado dia 1, ja no mes seguinte."""
    meta = d.get("meta") or {}
    dia = (meta.get("fredSeriesDates") or {}).get("DGS10") or meta.get("generatedAt")
    return (dia or "")[:10]


def vintage_do_mes(mes):
    """(sha, data.json) da ultima revisao cujos dados sao do mes."""
    if _git("rev-parse", "--is-shallow-repository").strip() == "true":
        falha("o clone e raso: as revisoes do data.json nao estao todas aqui. "
              "Usar um clone completo (no Actions: fetch-depth: 0).")
    ini = f"{mes}-01"
    fim = f"{madd(mes, 1)}-10"            # folga para a corrida que aterra a dia 1
    shas = _git("log", f"--since={ini}T00:00:00Z", f"--until={fim}T00:00:00Z",
                "--format=%H", "--", "data.json").split()
    escolhida = None
    for sha in shas:                         # do mais recente para o mais antigo
        try:
            d = json.loads(_git("show", f"{sha}:data.json"))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        if data_dos_dados(d)[:7] == mes:
            escolhida = (sha, d)
            break
    if escolhida is None:
        falha(f"{mes}: nenhuma revisao do data.json com dados desse mes")
    return escolhida


def _pilar(d, pid):
    for p in d.get("pillars") or []:
        if p.get("id") == pid:
            return p
    return {}


def _num(v, o_que, mes):
    if v is None:
        falha(f"{mes}: {o_que} vem nulo no data.json")
    return float(v)


def macro_do_mes(mes, d):
    cyc, liq, prem = _pilar(d, "cycle"), _pilar(d, "liquidity"), _pilar(d, "premium")
    sol, debt = _pilar(d, "solvency"), _pilar(d, "debt")
    trig = ((d.get("stressGauge") or {}).get("triggers") or {})
    sahm, dacc = trig.get("sahmRealtime") or {}, trig.get("delinquencyAccel") or {}
    ep = _num(prem.get("epValue"), "premium.epValue", mes)
    erp = _num(prem.get("metricValue"), "premium.metricValue", mes)
    y10 = round(ep - erp, 2)
    spread = _num(cyc.get("metricValue"), "cycle.metricValue", mes)
    fds = (d.get("meta") or {}).get("fredSeriesDates") or {}
    return {
        "spread": spread,
        "buffett": _num(liq.get("metricValue"), "liquidity.metricValue", mes),
        "buffett_pct": _num(liq.get("percentileRank"), "liquidity.percentileRank", mes),
        "npl_producao": _num(sol.get("metricValue"), "solvency.metricValue", mes),
        "dsr": _num(debt.get("metricValue"), "debt.metricValue", mes),
        "ep_real": ep, "erp_real": erp, "y10": y10, "y2": round(y10 - spread, 2),
        "sahm": (str(sahm.get("asOf") or "")[:7], sahm.get("value")),
        "dralacbn_data": str(dacc.get("asOf") or "")[:10],
        "dralacbn_accel": dacc.get("value"),
        "dralacbn_fred": str(fds.get("DRALACBN") or "")[:10],
        "tdsp_fred": str(fds.get("TDSP") or "")[:10],
    }


# ── ficheiros existentes ────────────────────────────────────────────────────
def le_quarterly():
    with open(DATA / "fred_quarterly.csv", newline="") as f:
        linhas = list(csv.DictReader(f))
    return linhas


def npl_por_regra(quarterly, mes):
    q = {r["date"]: float(r["dralacbn"]) for r in quarterly if r["dralacbn"]}
    visiveis = [d for d in sorted(q) if meses_entre(d[:7], mes) >= LAG_NPL_MESES]
    return q[visiveis[-1]] if visiveis else None


def linha_score(mes, mc, npl):
    r = {"month": mes, "spread": mc["spread"], "buffett_pct": mc["buffett_pct"],
         "buffett": mc["buffett"], "npl": npl, "dsr": mc["dsr"], "y10": mc["y10"],
         "ep_real": mc["ep_real"], "erp_frozen": round(EP_CONGELADO - mc["y10"], 2)}
    for sufixo in ("frozen", "real"):
        erp = r["erp_frozen"] if sufixo == "frozen" else mc["erp_real"]
        if sufixo == "real":
            r["erp_real"] = erp
        p = {pid: rules.score_pillar(pid, erp if pid == "premium" else r[CAMPO[pid]])
             for pid in rules.PILLAR_ORDER}
        score, _ = rules.global_score(p)
        r[f"score_{sufixo}"] = score
        r[f"regime_{sufixo}"] = rules.score_band(score)
        r[f"pillars_{sufixo}"] = p
    # a mesma ordem de chaves das linhas que ja la estao
    ordem = ["month", "spread", "buffett_pct", "buffett", "npl", "dsr", "y10", "ep_real",
             "erp_frozen", "score_frozen", "regime_frozen", "pillars_frozen",
             "erp_real", "score_real", "regime_real", "pillars_real"]
    return {k: r[k] for k in ordem}


# ── precos ──────────────────────────────────────────────────────────────────
def _load_px(caminho):
    """O leitor do proprio backtest — nunca um segundo parser (o defeito de 18
    Nov foi exactamente esse: um leitor proprio que leu o mes como preco)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_fb_load", HERE / "final_backtest.py")
    src = (HERE / "final_backtest.py").read_text(encoding="utf-8")
    import ast
    arv = ast.parse(src)
    fn = next(n for n in arv.body if isinstance(n, ast.FunctionDef) and n.name == "load")
    ns = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(spec.origin), "exec"), ns)
    return ns["load"](caminho)


def ajustados(ticker, desde):
    import yfinance as yf
    df = yf.download(ticker, start=f"{desde}-01", interval="1mo",
                     auto_adjust=True, progress=False)
    if df is None or df.empty:
        falha(f"{ticker}: o yfinance nao devolveu nada")
    fecho = df["Close"]
    if hasattr(fecho, "columns"):
        fecho = fecho.iloc[:, 0]
    return {f"{d.year:04d}-{d.month:02d}": float(v) for d, v in fecho.dropna().items()}


def texto_px_com(texto, novos):
    """Acrescenta precos mensais ao texto de um ficheiro px, sem tocar no resto.
    `novos` e {mes: preco} contiguo e a seguir ao ultimo mes do ficheiro."""
    nl = "\r\n" if "\r\n" in texto else "\n"      # quatro ficheiros sao CRLF
    linhas = texto.rstrip("\r\n").split(nl)
    for mes in sorted(novos):
        ano, mm = mes[:4], int(mes[5:7])
        v = f"{novos[mes]:.4f}"
        ult = linhas[-1].split()
        if mm == 1:
            linhas.append(f"{ano} {v}")
        elif ult and ult[0] == ano:
            linhas[-1] = linhas[-1].rstrip() + " " + v
        else:
            linhas.append(f"{ano} {mm:02d} {v}")
    return nl.join(linhas) + nl


# ── principal ───────────────────────────────────────────────────────────────
def ultimo_mes_fechado(hoje=None):
    hoje = hoje or dt.date.today()
    return madd(f"{hoje.year:04d}-{hoje.month:02d}", -1)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ate", help="ultimo mes a acrescentar (AAAA-MM); por omissao o ultimo fechado")
    ap.add_argument("--verificar", action="store_true", help="nao escreve nada")
    a = ap.parse_args(argv)

    scores = json.load(open(DATA / "scores.json"))
    ultimo = scores[-1]["month"]
    alvo = a.ate or ultimo_mes_fechado()
    if alvo > ultimo_mes_fechado():
        falha(f"{alvo} ainda nao fechou")
    meses = []
    m = madd(ultimo, 1)
    while m <= alvo:
        meses.append(m)
        m = madd(m, 1)
    if not meses:
        print(f"Nada a fazer: os dados ja vao ate {ultimo}.")
        return 0
    print(f"A acrescentar {meses[0]} a {meses[-1]} ({len(meses)} meses) a dados que acabam em {ultimo}")

    # ── macro ────────────────────────────────────────────────────────────────
    quarterly = le_quarterly()
    cab_q = list(quarterly[0].keys())
    sahm_txt = (DATA / "sahm_realtime.txt").read_text()
    sahm = {l.split()[0]: l.split()[1] for l in sahm_txt.splitlines() if l.strip()}
    mensal_txt = (DATA / "monthly.csv").read_text()
    novas_q, novas_sahm, novas_mensal, novas_scores, notas = [], {}, [], [], []

    for mes in meses:
        sha, d = vintage_do_mes(mes)
        mc = macro_do_mes(mes, d)
        print(f"  {mes}: revisao {sha[:7]} (dados de {data_dos_dados(d)})")

        # trimestre novo da delinquencia (e do TDSP, se for do mesmo trimestre)
        qd = mc["dralacbn_data"]
        if not qd or mc["dralacbn_fred"] != qd:
            falha(f"{mes}: data da delinquencia incoerente no data.json "
                  f"(gatilho {qd!r}, serie {mc['dralacbn_fred']!r})")
        datas_q = [r["date"] for r in quarterly]
        if qd not in datas_q:
            if qd < datas_q[-1]:
                falha(f"{mes}: trimestre {qd} da delinquencia esta antes do fim do ficheiro")
            linha = {k: "" for k in cab_q}
            linha["date"], linha["dralacbn"] = qd, f"{mc['npl_producao']:.2f}"
            if mc["tdsp_fred"] == qd:
                linha["tdsp"] = f"{mc['dsr']:.6f}"
            quarterly.append(linha)
            novas_q.append(qd)
        # a aceleracao que a producao publicou tem de sair da serie do backtest
        if mc["dralacbn_accel"] is not None:
            q = [(r["date"], float(r["dralacbn"])) for r in quarterly if r["dralacbn"]]
            i = [k for k, _ in q].index(qd)
            calc = round(q[i][1] - q[i - 4][1], 2)
            if abs(calc - float(mc["dralacbn_accel"])) > 0.011:
                falha(f"{mes}: a aceleracao da delinquencia da serie do backtest ({calc}) "
                      f"nao bate com a da producao ({mc['dralacbn_accel']})")

        sm, sv = mc["sahm"]
        if sm and sv is not None and sm not in sahm:
            sahm[sm] = f"{float(sv):.2f}"
            novas_sahm[sm] = sahm[sm]

        npl = npl_por_regra(quarterly, mes)
        if npl is None:
            falha(f"{mes}: sem delinquencia visivel pela regra dos {LAG_NPL_MESES} meses")
        if abs(npl - mc["npl_producao"]) > 1e-9:
            notas.append(f"{mes}: delinquencia pela regra do backtest {npl} "
                         f"(a producao usava {mc['npl_producao']})")
        novas_scores.append(linha_score(mes, mc, npl))
        novas_mensal.append(f"{mes},{mc['y10']},{mc['y2']},{mc['ep_real']}")

    # o medidor B le a regra de Sahm do mes anterior: tem de existir
    for mes in meses:
        if madd(mes, -1) not in sahm:
            falha(f"{mes}: falta a regra de Sahm de {madd(mes, -1)}")

    # ── precos ───────────────────────────────────────────────────────────────
    novos_px = {}
    for t in TICKERS:
        cam = DATA / "px" / f"{t}.txt"
        hist = _load_px(cam)
        base = max(hist)
        # Alguns ficheiros vao alem do scores.json (QQQ/SHY/HYG/IWO tinham
        # 2026-09 quando o resto acabava em 2026-08): acrescenta-se so o que
        # falta a cada um, e nunca se reescreve um mes que ja la esta.
        if base < ultimo:
            falha(f"{t}: os precos acabam em {base}, antes dos scores ({ultimo})")
        faltam = [m for m in meses if m > base]
        if not faltam:
            continue
        adj = ajustados(t, madd(base, -1))
        px, prev = {}, base
        for mes in faltam:
            if mes not in adj or prev not in adj:
                falha(f"{t}: o yfinance nao tem {mes} ou {prev}")
            ant = hist[prev] if prev == base else px[prev]
            px[mes] = round(ant * adj[mes] / adj[prev], 4)
            prev = mes
        novos_px[t] = px

    # ── relatorio do que vai ser escrito ─────────────────────────────────────
    for r in novas_scores:
        print(f"  {r['month']}: score {r['score_real']} ({r['regime_real']}) "
              f"y10 {r['y10']} ERP {r['erp_real']} npl {r['npl']}")
    print(f"  SPY: " + ", ".join(f"{m} {v}" for m, v in novos_px["SPY"].items()))
    if novas_q:
        print(f"  trimestres novos na delinquencia: {novas_q}")
    if novas_sahm:
        print(f"  Sahm novos: {novas_sahm}")
    for n in notas:
        print(f"  nota: {n}")
    if a.verificar:
        print("--verificar: nada foi escrito.")
        return 0

    # ── escrever tudo, depois de tudo validado ───────────────────────────────
    (DATA / "scores.json").write_text(_dump_scores(scores + novas_scores))
    (DATA / "monthly.csv").write_text(mensal_txt.rstrip("\n") + "\n" + "\n".join(novas_mensal) + "\n")
    (DATA / "sahm_realtime.txt").write_text(
        sahm_txt.rstrip("\n") + "\n" + "".join(f"{k} {v}\n" for k, v in sorted(novas_sahm.items())))
    if novas_q:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cab_q, lineterminator="\n")
        w.writeheader()
        w.writerows(quarterly)
        (DATA / "fred_quarterly.csv").write_text(buf.getvalue())
    for t, px in novos_px.items():
        cam = DATA / "px" / f"{t}.txt"
        antes = _load_px(cam)
        cam.write_bytes(texto_px_com(cam.read_bytes().decode("ascii"), px).encode("ascii"))
        depois = _load_px(cam)
        if {k: v for k, v in depois.items() if k in antes} != antes or \
                any(abs(depois[m] - v) > 1e-9 for m, v in px.items()):
            falha(f"{t}: a escrita alterou a historia ou nao le de volta o que escreveu")
    print(f"Escrito: dados ate {meses[-1]}.")
    return 0


def _dump_scores(linhas):
    """O formato exacto do ficheiro que ja existe: json.dumps com indent=1, sem
    newline final — as linhas antigas ficam byte a byte iguais no diff."""
    return json.dumps(linhas, indent=1)


if __name__ == "__main__":
    sys.exit(main())
