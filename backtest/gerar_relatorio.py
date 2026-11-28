"""Gera o backtest.html (separador Backtest do usmrm.net) a partir do backtest.

    python backtest/gerar_relatorio.py            # escreve backtest.html na raiz
    python backtest/gerar_relatorio.py --saida x.html

Todos os numeros da pagina saem daqui: corre o `final_backtest.py` sobre todos os
dados que existem em `backtest/data/` e preenche o `backtest/relatorio_modelo.html`.
Nenhum numero esta escrito a mao no modelo — para mudar o texto ou o desenho,
edita-se o modelo; para mudar os numeros, mudam os dados ou as regras.

Recusa escrever (sys.exit(1)) se sobrar algum marcador {{...}} por preencher.
Corre com Python 3.11 (o que os workflows pinam): no 3.12+ o sum() compensado do
CPython move alguns compostos uma centesima.
"""
import argparse
import contextlib
import datetime as dt
import importlib.util
import io
import os
import platform
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MODELO = HERE / "relatorio_modelo.html"

MESES_PT = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
            "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


def carrega_backtest():
    os.environ.pop("BACKTEST_FIM", None)          # a pagina usa todos os dados
    spec = importlib.util.spec_from_file_location("fb_relatorio", HERE / "final_backtest.py")
    fb = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(fb)
    return fb


# ── formatacao portuguesa ───────────────────────────────────────────────────
def num(x, casas=2):
    s = f"{x:,.{casas}f}"
    s = s.replace(",", "§").replace(".", ",").replace("§", ".")
    return s.replace("-", "−")


def pct(x, casas=2):
    return num(x * 100, casas) + "%"


def inteiro(x):
    return num(round(x), 0)


def cls_sinal(x):
    return "neg" if x < 0 else "pos"


def mes_pt(m):
    return f"{MESES_PT[int(m[5:7]) - 1]} {m[:4]}"


# ── estado mes a mes do v2, a partir do registo de mudancas ────────────────
def estados_v2(fb, log):
    """{mes: 'Turbulence' | 'Critical_FTQ' | 'Critical_Stress' | 'Resilient'}"""
    mudancas = {}
    for m, txt in log:
        destino = txt.split("->", 1)[1].split("[", 1)[0].strip()
        mudancas[m] = destino.split("/")[-1]
    estado, out = "Turbulence", {}
    for m in fb.MONTHS:
        estado = mudancas.get(m, estado)
        out[m] = estado
    return out


def episodios(fb, est):
    """[(primeiro mes em Critical, mes de saida ou None se em curso)]"""
    eps, ini = [], None
    for m in fb.MONTHS:
        crit = est[m].startswith("Critical")
        if crit and ini is None:
            ini = m
        elif not crit and ini is not None:
            eps.append((ini, m))
            ini = None
    if ini is not None:
        eps.append((ini, None))
    return eps


def svg_regime(fb, est, eps, n_mudancas):
    W, n = 960.0, len(fb.MONTHS)
    pxm = W / n
    rects, x0, atual = [], 0, None
    for i, m in enumerate(fb.MONTHS + [None]):
        e = est[m] if m else None
        if e != atual:
            if atual and atual.startswith("Critical"):
                op = '' if atual == "Critical_FTQ" else ' opacity=".45"'
                rects.append(f'          <rect x="{x0 * pxm:.1f}" y="34" width="{(i - x0) * pxm:.1f}" '
                             f'height="30" fill="var(--v2)"{op}/>')
            atual, x0 = e, i
    anos = sorted({int(m[:4]) for m in fb.MONTHS})
    marcas = list(range(anos[0], anos[-1], 4))
    tx = []
    for a in marcas:
        i = next(k for k, m in enumerate(fb.MONTHS) if int(m[:4]) == a)
        tx.append(f'          <text x="{i * pxm:.1f}" y="80">{a}</text>')
    tx.append(f'          <text x="{W - 25:.0f}" y="80">{anos[-1]}</text>')
    lab, lin = [], []
    for ini, _ in eps:
        i = fb.MONTHS.index(ini)
        x = i * pxm
        xt = min(x, W - 48)
        lab.append(f'          <text x="{xt:.1f}" y="26">{ini}</text>')
        lin.append(f'          <line x1="{x:.1f}" y1="29" x2="{x:.1f}" y2="34"/>')
    descr = (f"Linha temporal dos regimes de {fb.MONTHS[0]} a {fb.MONTHS[-1]}: "
             f"{len(eps)} episódio(s) Critical.")
    return "\n".join([
        '      <svg viewBox="0 0 960 118" width="100%" style="max-width:100%;height:auto"',
        f'           role="img" aria-label="{descr}">',
        '        <rect x="0" y="34" width="960" height="30" fill="var(--line-soft)"/>',
        '        <g>', *rects, '        </g>',
        '        <g font-family="var(--mono)" font-size="10" fill="var(--fg-faint)">', *tx, '        </g>',
        '        <g font-family="var(--mono)" font-size="10" fill="var(--fg-dim)">', *lab, '        </g>',
        '        <g stroke="var(--fg-faint)" stroke-width="1" opacity=".5">', *lin, '        </g>',
        '        <g font-family="var(--mono)" font-size="10" fill="var(--fg-faint)">',
        f'          <text x="0" y="108">Turbulence a cinzento · {n_mudancas} mudanças de estado '
        f'em {n} meses</text>',
        '        </g>',
        '      </svg>'])


def linha_res(nome, s, classe=""):
    c = f' class="{classe}"' if classe else ""
    return (f'          <tr{c}><td>{nome}</td><td class="num">{pct(s["cagr"])}</td>'
            f'<td class="num">{pct(s["vol"])}</td><td class="num">{num(s["sharpe"], 3)}</td>'
            f'<td class="num">{num(s["sortino"], 3)}</td>'
            f'<td class="num neg">{pct(s["mdd"], 1)}</td><td class="num">{s["mdd_m"]}</td>'
            f'<td class="num">{inteiro(s["final"])}</td></tr>')


def calcula(fb):
    v = {}
    M = fb.MONTHS
    v["mes_ini"], v["mes_fim"], v["n_meses"] = M[0], M[-1], str(len(M))
    v["ano_ini"], v["ano_fim"] = M[0][:4], M[-1][:4]
    v["anos"] = num((len(M) - 1) / 12, 1)
    v["cost_open"], v["cost_close"] = f"{fb.COST_OPEN:.0f}", f"{fb.COST_CLOSE:.0f}"
    v["gerado_em"] = dt.date.today().isoformat()
    v["python"] = platform.python_version()

    v2f, log, _ = fb.run_v2()
    v1f, _, _ = fb.run_v1()
    runs = {mod: {"v2": fb.run_v2(mod), "v1": fb.run_v1(mod)} for mod in fb.COST_MODELS}
    base = runs["open_close"]
    s2, s1 = fb.stats(base["v2"][0]), fb.stats(base["v1"][0])
    spy = fb.buy_hold({"SPY": 1.0})
    sspy = fb.stats(spy)
    s64 = fb.stats(fb.annual_rebal({"SPY": 0.6, "IEF": 0.4}))
    v["linhas_resultado"] = "\n".join([
        linha_res("v2 — sistema actual", s2, "focus"),
        linha_res("v1 — sistema anterior", s1, "prev"),
        linha_res("SPY buy &amp; hold", sspy),
        linha_res("60/40 SPY-IEF, anual", s64)])
    v["seguro_pp"] = num((s1["cagr"] - s2["cagr"]) * 100)
    v["quebra_poupada"] = num((s2["mdd"] - s1["mdd"]) * 100, 1)
    v["v2_mdd"], v["v1_mdd"] = pct(s2["mdd"], 1), pct(s1["mdd"], 1)
    v["v2_mdd_m"] = s2["mdd_m"]
    v["spy_mdd"] = pct(sspy["mdd"], 1)
    v["v2_final"], v["v1_final"] = inteiro(s2["final"]), inteiro(s1["final"])
    v["v2_cagr"], v["v2_sharpe"] = pct(s2["cagr"]), num(s2["sharpe"], 3)
    v["b6040_cagr"], v["b6040_sharpe"] = pct(s64["cagr"]), num(s64["sharpe"], 3)
    v["b6040_mdd"], v["b6040_mdd_m"] = pct(s64["mdd"], 1), s64["mdd_m"]
    led2 = base["v2"][2]
    v["custo_v2"] = inteiro(led2["total"])
    v["custo_v2_pct"] = pct(led2["total"] / fb.INITIAL_CAPITAL)

    # regime
    est = estados_v2(fb, log)
    eps = episodios(fb, est)
    v["meses_b"] = str(sum(1 for m in M if fb.gauge_b(m)))
    v["n_episodios"] = str(len(eps))
    v["v1_critical"] = str(sum(1 for r in fb.SC if r["regime_frozen"] == "Critical"))
    v["n_scores"] = str(len(fb.SC))
    v["svg_regime"] = svg_regime(fb, est, eps, len(log))
    partes = []
    for ini, fim in eps:
        partes.append(f"de {mes_pt(ini)} à saída em {mes_pt(fim)}" if fim
                      else f"desde {mes_pt(ini)}, em curso")
    v["legenda_episodios"] = (f"{len(eps)} episódio(s) Critical: " + "; ".join(partes) + "."
                              if eps else "Nenhum episódio Critical no período.")

    # ano a ano
    d1, d2, ds = dict(base["v1"][0]), dict(base["v2"][0]), dict(spy)
    linhas, anos, prev = [], [], M[0]
    for y in sorted({m[:4] for m in M}):
        ms = [m for m in M if m[:4] == y]
        f = lambda d: d[ms[-1]] / d[prev] - 1
        a1, a2, asp = f(d1), f(d2), f(ds)
        nb = sum(1 for m in ms if fb.gauge_b(m))
        anos.append((y, a1, a2, asp, nb, len(ms)))
        prev = ms[-1]
    melhor = max(anos, key=lambda t: t[2] - t[1])
    for y, a1, a2, asp, nb, k in anos:
        c = ' class="focus"' if y == melhor[0] and melhor[2] > melhor[1] else ""
        b = "—" if nb == 0 else f"ON {nb}/{k}"
        linhas.append(f'          <tr{c}><td class="num">{y}</td>'
                      f'<td class="num {cls_sinal(a1)}">{pct(a1, 1)}</td>'
                      f'<td class="num {cls_sinal(a2)}">{pct(a2, 1)}</td>'
                      f'<td class="num {cls_sinal(asp)}">{pct(asp, 1)}</td><td>{b}</td></tr>')
    v["linhas_anos"] = "\n".join(linhas)
    # quadro resumo: o ano de maior diferenca a favor do v2, escolhido pelos dados
    v["ano_chave"] = melhor[0]
    v["ano_chave_v2"] = ("+" if melhor[2] > 0 else "") + pct(melhor[2], 1)
    v["ano_chave_v1"], v["ano_chave_spy"] = pct(melhor[1], 1), pct(melhor[3], 1)
    pior = min(anos, key=lambda t: t[2])
    parcial = f" ({anos[-1][0]} vai até {M[-1]}.)" if anos[-1][5] < 12 else ""
    v["nota_anos"] = (
        f"<strong>Maior diferença a favor do v2: {melhor[0]}</strong>, {pct(melhor[2], 1)} "
        f"contra {pct(melhor[1], 1)} do v1 e {pct(melhor[3], 1)} do SPY. "
        f"<strong>Pior ano do v2: {pior[0]}</strong>, {pct(pior[2], 1)}, com o Medidor B "
        f"{'ligado ' + str(pior[4]) + ' meses' if pior[4] else 'desligado o ano todo'} "
        f"(v1 {pct(pior[1], 1)}, SPY {pct(pior[3], 1)}). Fora dos episódios de stress "
        f"o v1 e o v2 detêm a mesma carteira.{parcial}")

    # custos
    s2f, s1f = fb.stats(v2f), fb.stats(v1f)
    nomes = {"open_close": "abertura e fecho", "every_trade": "cada transacção"}
    lc = [f'          <tr class="focus"><td>v2 sem custos</td><td class="num">—</td>'
          f'<td class="num">—</td><td class="num">—</td><td class="num">{pct(s2f["cagr"])}</td>'
          f'<td class="num">—</td></tr>']
    for quem, sf, cl in (("v2", s2f, ""), ("v1", s1f, ' class="prev"')):
        for mod in fb.COST_MODELS:
            if quem == "v1" and mod != "open_close":
                continue
            ser, _, led = runs[mod][quem]
            s = fb.stats(ser)
            lc.append(f'          <tr{cl}><td>{quem} · {nomes[mod]}</td>'
                      f'<td class="num">{led["n_rebal"]}</td><td class="num">${inteiro(led["total"])}</td>'
                      f'<td class="num">{pct(led["total"] / fb.INITIAL_CAPITAL)}</td>'
                      f'<td class="num">{pct(s["cagr"])}</td>'
                      f'<td class="num">{num((s["cagr"] - sf["cagr"]) * 100)} pp</td></tr>')
    v["linhas_custos"] = "\n".join(lc)
    ld, seg = [], []
    for bp in fb.SLIPPAGE_SCENARIOS:
        a2, _, l2 = fb.run_v2("every_trade", slippage_bp=bp)
        a1, _, l1 = fb.run_v1("every_trade", slippage_bp=bp)
        b, a = fb.stats(a2), fb.stats(a1)
        seg.append((a["cagr"] - b["cagr"]) * 100)
        ld.append(f'          <tr><td class="num">{bp:.0f} bp</td><td class="num">{pct(b["cagr"])}</td>'
                  f'<td class="num">${inteiro(l2["total"])}</td><td class="num">{pct(a["cagr"])}</td>'
                  f'<td class="num">${inteiro(l1["total"])}</td><td class="num">{num(seg[-1])} pp</td></tr>')
    v["linhas_deslize"] = "\n".join(ld)
    v["bp_min"], v["bp_max"] = f"{fb.SLIPPAGE_SCENARIOS[0]:.0f}", f"{fb.SLIPPAGE_SCENARIOS[-1]:.0f}"
    v["seguro_bp_min"], v["seguro_bp_max"] = num(seg[0]), num(seg[-1])
    return v


def preenche(modelo, v):
    out = re.sub(r"\{\{(\w+)\}\}", lambda m: v[m.group(1)] if m.group(1) in v else m.group(0), modelo)
    sobra = sorted(set(re.findall(r"\{\{(\w+)\}\}", out)))
    if sobra:
        print(f"::error::marcadores por preencher no modelo: {sobra}")
        sys.exit(1)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--saida", default=str(ROOT / "backtest.html"))
    a = ap.parse_args(argv)
    fb = carrega_backtest()
    html = preenche(MODELO.read_text(encoding="utf-8"), calcula(fb))
    Path(a.saida).write_text(html, encoding="utf-8")
    print(f"Escrito {a.saida}: {fb.MONTHS[0]} a {fb.MONTHS[-1]} ({len(fb.MONTHS)} meses)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
