"""
Regressão do backtest — os números publicados têm de continuar a sair do código.

O backtest importa mrm_rules.py, por isso qualquer alteração às regras muda estes
valores. É isso que se pretende: se alguém mexer nos pesos, nos limiares ou na
lógica de rebalanceamento, este teste falha e obriga a republicar os números em
vez de os deixar desactualizados no site e na Academy.

Sem rede: lê apenas os ficheiros em backtest/data/.
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
def close(got, want, tol, what):
    global ok
    assert abs(got - want) <= tol, f"{what}: esperado {want} ± {tol}, obtido {got:.4f}"
    ok += 1
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1

# Sem custos: e esta a serie que sustenta a comparacao "antes contra depois",
# porque isola o efeito do medidor B da corretagem.
v2, log, _ = fb.run_v2()
v1, _, _   = fb.run_v1()
# Com a corretagem pedida — 10 USD por abertura, 10 por fecho, capital 100.000
# composto. E esta a serie que um investidor real teria tido.
v2c, _, led2 = fb.run_v2("open_close")
v1c, _, led1 = fb.run_v1("open_close")
s2, s1 = fb.stats(v2), fb.stats(v1)
s2c, s1c = fb.stats(v2c), fb.stats(v1c)
d2, d1 = dict(v2), dict(v1)

eq(len(fb.MONTHS), 235, "periodo de 235 meses (2007-02 a 2026-08)")

# ── os numeros publicados na Academia e no relatorio ────────────────────────
close(s1["cagr"] * 100, 6.92, 0.05, "v1 CAGR")
close(s1["mdd"] * 100, -23.9, 0.1, "v1 quebra maxima")
close(s2["cagr"] * 100, 6.59, 0.05, "v2 CAGR")
close(s2["mdd"] * 100, -16.4, 0.1, "v2 quebra maxima")
close(s2["sortino"], 1.034, 0.01, "v2 Sortino (downside deviation padrao)")
close(s2["sharpe"], 0.683, 0.01, "v2 Sharpe")
close((s1["cagr"] - s2["cagr"]) * 100, 0.33, 0.05, "custo do seguro em pp de CAGR")

y = lambda d, a, b: (d[b] / d[a] - 1) * 100
close(y(d1, "2007-12", "2008-12"), -13.8, 0.2, "2008 no sistema anterior")
close(y(d2, "2007-12", "2008-12"), 1.4, 0.2, "2008 no sistema actual")
close(y(d2, "2021-12", "2022-12"), -12.8, 0.2, "2022 — o medidor B fica calado de proposito")

# ── com a corretagem aplicada ────────────────────────────────────────────────
eq(fb.INITIAL_CAPITAL, 100_000.0, "capital inicial declarado")
close(s2c["cagr"] * 100, 6.58, 0.05, "v2 CAGR com corretagem")
close(s1c["cagr"] * 100, 6.92, 0.05, "v1 CAGR com corretagem")
close(s2c["final"], 346_055, 2_000, "v2 termina em ~346 mil USD")
close(s1c["final"], 368_554, 2_000, "v1 termina em ~369 mil USD")
close(led2["total"], 540, 1, "o v2 paga 540 USD de corretagem em 19,5 anos")
close(led1["total"], 220, 1, "o v1 paga 220 USD")
close((s1c["cagr"] - s2c["cagr"]) * 100, 0.34, 0.05, "custo do seguro com corretagem")
# A comissao fixa e desprezavel a esta escala; o custo proporcional nao e.
# Este par de asserçoes existe para que essa conclusao nao se perca.
close((s2["cagr"] - s2c["cagr"]) * 100, 0.01, 0.02, "a comissao fixa custa ~0,01 pp ao v2")
_, _, led20 = fb.run_v2("every_trade", slippage_bp=20.0)
close(led20["total"], 9_343, 300, "a 20 bp o custo total sobe para ~9.300 USD")

# ── comportamento do medidor, nao so o retorno ──────────────────────────────
fired = [m for m in fb.MONTHS if fb.gauge_b(m)]
eq(len(fired), 48, "48 meses com o medidor B ligado em 235")
for ano in ("2008", "2020", "2024"):
    assert any(m.startswith(ano) for m in fired), f"o medidor tinha de disparar em {ano}"
    ok += 1
for ano in ("2011", "2018", "2022"):
    assert not any(m.startswith(ano) for m in fired), f"o medidor nao pode disparar em {ano}"
    ok += 1
assert any(m.startswith("2008-0") and m <= "2008-05" for m in fired), "2008: disparo antes de Lehman"
ok += 1

# ── entradas e saidas ────────────────────────────────────────────────────────
reasons = [t.split("[")[1].rstrip("]") for _, t in log]
eq(reasons.count("stress_on"), 3, "tres entradas em stress (2008, 2020, 2024)")
eq(reasons.count("stress_off"), 3, "tres saidas de stress")
# Com o E/P marcado a mercado o score nunca desce a 4,0 nesta amostra, por isso o
# episodio Resilient de 2021 desaparece. A regra continua testada em test_rules.py.
eq(reasons.count("resilient_off"), 0, "sem episodio Resilient com o E/P a mercado")
eq(len(log), 15, "quinze mudancas de estado")

# As trocas de sub-regime sao um numero PUBLICADO (README e capitulo 8 do site).
# Estava escrito "sete / setenta dolares" e a corrida real da nove. O preco por
# troca passou de 10 para 20 quando o SHY deixou de ser substituido pelo SHV: a
# substituicao fazia o SHY coincidir com o ticker do CASH e do IG_CREDIT, e a
# posicao ja existia. Com a serie real, a troca FTQ<->Stress fecha o TLT e abre o
# SHY — duas posicoes, nao uma. Fica preso aqui para nao voltar a divergir.
trocas = [(m, c) for m, r, c in led2["events"] if r.startswith("critical_subregime_switch")]
eq(len(trocas), 9, "nove trocas de sub-regime no periodo todo")
eq(len([t for t in trocas if "2008" <= t[0][:4] <= "2010"]), 6,
   "seis delas entre 2008 e 2010")
eq(sum(c for _, c in trocas), 180, "cento e oitenta dolares no total")
eq(sorted({c for _, c in trocas}), [20],
   "vinte dolares cada — fecha o TLT e abre o SHY, so a manga de duracao muda")
assert all(r.startswith("critical_subregime_switch") or r in
           ("stress_on", "stress_off", "resilient_off", "semestral_rebalance")
           or r.startswith("emergency_resilient") for r in reasons), "motivos conhecidos"
ok += 1

# a primeira entrada em Critical e sempre defensiva
first_on = next(t for _, t in log if "stress_on" in t)
assert "Critical_Stress" in first_on, f"entrada fresca defensiva ({first_on})"
ok += 1

# ── as series de precos: formato e cobertura ────────────────────────────────
#
# O load() aceita duas formas de linha e a segunda so era reconhecida quando o
# ano tinha um unico mes. O SHV comeca em "2007 02 81.14 81.45 ..." — onze
# precos a partir de Fevereiro — e entrava como se comecasse em Janeiro, pondo o
# numero 2 no lugar do preco de 2007-01. Nunca deu erro porque o backtest comeca
# em 2007-02 e esse mes nunca era lido.
assert "2007-01" not in fb.PX["SHV"], "o SHV nao tem Janeiro de 2007 — e nao inventa um"
ok += 1
close(fb.PX["SHV"]["2007-02"], 81.1469, 0.0001, "o primeiro preco do SHV e o de Fevereiro")
for _t, _v in (("GLD", "2006-12"), ("QQQ", "2006-12"), ("HYG", "2007-04")):
    assert fb.PX[_t][_v] > 10, f"{_t} {_v}: preco plausivel, nao um numero de mes"
    ok += 1

# Os instrumentos do mapa Resilient tinham seis meses de precos (Jul-Dez 2021) e
# o ramo era estruturalmente impossivel de avaliar. Agora tem serie completa.
for _t in ("QQQ", "SHY", "HYG", "IWO"):
    eq(len([m for m in fb.PX[_t] if m in fb.MONTHS]) >= len(fb.MONTHS), True,
       f"{_t} cobre os {len(fb.MONTHS)} meses do periodo")

# O HYG so cotou em 2007-04 e os dois meses anteriores sao encadeados pelos
# retornos do LQD. Se fosse o PRECO do LQD, Abril de 2007 apareceria como uma
# queda de 36% que nunca existiu.
_abril = fb.PX["HYG"]["2007-04"] / fb.PX["HYG"]["2007-03"] - 1
assert abs(_abril) < 0.05, f"o emendo do HYG nao pode criar um degrau ({_abril:+.1%})"
ok += 1

print(f"TODOS OS {ok} TESTES PASSARAM")
