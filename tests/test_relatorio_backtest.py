"""
A actualizacao mensal do backtest: o acrescento de precos e a pagina gerada.

  1. O acrescento aos ficheiros px nunca toca na historia, respeita o fim de
     linha de cada ficheiro (quatro sao CRLF) e le de volta pelo load() do
     proprio backtest — o defeito de 18 Nov foi um leitor proprio que leu o mes
     como preco.
  2. O backtest.html commitado corresponde aos dados commitados: a pagina diz
     "dados ate" o ultimo mes do scores.json, e os numeros principais sao os que
     o final_backtest.py da hoje. Acrescentar um mes sem regenerar a pagina
     fecha o portao — que e o que o workflow mensal faz, por esta ordem.
  3. O gerador recusa deixar marcadores por preencher.

Sem rede.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.modules.setdefault("yfinance", types.ModuleType("yfinance"))

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1
def true(c, what): eq(bool(c), True, what)


def _mod(nome, caminho):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    m = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(m)
    return m


ed = _mod("ed_t", ROOT / "backtest" / "estender_dados.py")

# ── 1. acrescento de precos ─────────────────────────────────────────────────
casos = [
    ("2006 12 1.0000\n2007 2.0000 3.0000\n", {"2007-03": 4.5}, "\n", "a meio do ano"),
    ("2026 1.0000 2.0000 3.0000 4.0000 5.0000 6.0000 7.0000 8.0000 9.0000 10.0000 11.0000 12.0000\n",
     {"2027-01": 13.0, "2027-02": 14.0}, "\n", "virar o ano"),
    ("2006 12 1.0000\r\n2007 2.0000\r\n", {"2007-02": 2.5}, "\r\n", "ficheiro CRLF"),
]
with tempfile.TemporaryDirectory() as td:
    for texto, novos, nl, o_que in casos:
        p = Path(td) / "x.txt"
        p.write_bytes(texto.encode("ascii"))
        antes = ed._load_px(p)
        depois_txt = ed.texto_px_com(texto, novos)
        p.write_bytes(depois_txt.encode("ascii"))
        depois = ed._load_px(p)
        eq({k: v for k, v in depois.items() if k in antes}, antes, f"{o_que}: a historia fica igual")
        eq({k: depois[k] for k in novos}, novos, f"{o_que}: os meses novos leem-se de volta")
        true(depois_txt.startswith(texto.rstrip("\r\n")), f"{o_que}: o texto antigo e prefixo do novo")
        eq(("\r\n" in depois_txt), nl == "\r\n", f"{o_que}: o fim de linha do ficheiro mantem-se")

eq(ed.madd("2026-12", 1), "2027-01", "madd vira o ano")
eq(ed.ultimo_mes_fechado(__import__("datetime").date(2027, 1, 3)), "2026-12",
   "a 3 de Janeiro o ultimo mes fechado e Dezembro")

# ── 2. a pagina commitada corresponde aos dados commitados ─────────────────
os.environ.pop("BACKTEST_FIM", None)
gr = _mod("gr_t", ROOT / "backtest" / "gerar_relatorio.py")
fb = gr.carrega_backtest()
pagina = (ROOT / "backtest.html").read_text(encoding="utf-8")
ultimo = json.load(open(ROOT / "backtest" / "data" / "scores.json"))[-1]["month"]
eq(fb.MONTHS[-1], ultimo, "o backtest corre ate ao ultimo mes dos dados")
m = re.search(r"dados até (\d{4}-\d{2})", pagina)
true(m, "a pagina declara ate onde vao os dados")
eq(m.group(1) if m else None, ultimo,
   "o backtest.html foi regenerado depois do ultimo mes acrescentado "
   "(correr python backtest/gerar_relatorio.py)")
s2 = fb.stats(fb.run_v2("open_close")[0])
true(gr.pct(s2["cagr"]) in pagina, f"o CAGR do v2 na pagina e o de hoje ({gr.pct(s2['cagr'])})")
true(gr.inteiro(s2["final"]) in pagina, "e o valor final tambem")
true("{{" not in pagina, "a pagina nao tem marcadores por preencher")

# ── 3. o gerador recusa marcadores por preencher ────────────────────────────
_rebentou = False
try:
    with contextlib.redirect_stdout(io.StringIO()):
        gr.preenche("<p>{{nao_existe}}</p>", {})
except SystemExit as e:
    _rebentou = e.code == 1
true(_rebentou, "um marcador sem valor faz o gerador sair com erro")
eq(gr.preenche("<p>{{a}}</p>", {"a": "1"}), "<p>1</p>", "e preenche os que tem")
eq(gr.num(-1234.5, 1), "−1.234,5", "numeros em formato portugues, com sinal tipografico")

print(f"TODOS OS {ok} TESTES PASSARAM")
