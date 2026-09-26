"""
O selector de fontes do teste de fumo — sem rede.

Porque existe. O `so_recurso` e o unico caminho do sistema que exercita a
segunda fonte de proposito: na cascata normal a Yahoo responde e a de recurso e
saltada, que e o comportamento certo e e tambem a razao pela qual "temos segunda
fonte" ficava por verificar ate a noite em que a primeira caisse.

Um ensaio partido e pior do que ensaio nenhum: diz que a rede de seguranca
funciona quando ninguem a experimentou. Estes testes afirmam que o modo faz o
que diz — que DESLIGA a principal, e nao que finge desliga-la.

Sem rede: so se testa a escolha das fontes e os textos, nunca o `fetch_prices`.
"""
import importlib.util, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("sp", ROOT / "smoke_precos.py")
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

ok = 0
def eq(got, want, what):
    global ok
    assert got == want, f"{what}: esperado {want!r}, obtido {got!r}"
    ok += 1
def true(c, what): eq(bool(c), True, what)

# ── 1. O modo normal usa a cascata inteira, pela ordem declarada ──────────
fontes, nota = sp._fontes_para("cascata")
eq([n for n, _ in fontes], [n for n, _ in sp.up.FONTES_DE_PRECO],
   "o modo normal usa a cascata declarada, sem tirar nem por")
eq(nota, None, "e nao imprime nota de ensaio")

# Qualquer valor desconhecido — ou vazio, que e o que o GitHub manda quando o
# parametro nao e preenchido — cai no modo normal. Um ensaio que arrancasse por
# engano desligava a fonte principal numa corrida que se queria normal.
for _m in ("", "cascata", "qualquer_outra_coisa", "SO_RECURSO"):
    _f, _n = sp._fontes_para(_m)
    eq([n for n, _ in _f], [n for n, _ in sp.up.FONTES_DE_PRECO],
       f"o modo {_m!r} nao e ensaio — usa a cascata inteira")
    eq(_n, None, f"o modo {_m!r} nao imprime nota de ensaio")

# ── 2. O ensaio DESLIGA mesmo a principal ────────────────────────────────
# A afirmacao que interessa: a fonte principal nao pode estar na lista. Se
# estivesse, o ensaio passava por bom com a Yahoo a responder e nunca teria
# exercitado nada — exactamente a cegueira que ele existe para corrigir.
fontes, nota = sp._fontes_para("so_recurso")
true(sp.FONTE_PRINCIPAL not in [n for n, _ in fontes],
     f"o ensaio tira a `{sp.FONTE_PRINCIPAL}` da lista de fontes")
true(len(fontes) >= 1, "e sobra pelo menos uma fonte para exercitar")
eq([n for n, _ in fontes],
   [n for n, _ in sp.up.FONTES_DE_PRECO if n != sp.FONTE_PRINCIPAL],
   "e as que sobram sao as outras todas, pela mesma ordem")
true(nota and "desligada de proposito" in nota,
     "e a nota diz que a fonte foi desligada de proposito")
true(nota and "nao e uma avaria" in nota,
     "e deixa claro que isto nao e uma avaria — quem le o log nao pode "
     "confundir um ensaio com uma falha")

# ── 3. A principal do smoke e a mesma do alerta ──────────────────────────
# Duas copias da mesma ideia divergem sempre. Se o alerta considerar `yahoo` a
# principal e o ensaio desligar outra coisa, o ensaio passa a exercitar a fonte
# errada e ninguem da por isso.
spec_ap = importlib.util.spec_from_file_location("ap", ROOT / "alerta_precos.py")
ap = importlib.util.module_from_spec(spec_ap)
spec_ap.loader.exec_module(ap)
eq(sp.FONTE_PRINCIPAL, ap.FONTE_PRINCIPAL,
   "o smoke e o alerta concordam em qual e a fonte principal")
eq(sp.FONTE_PRINCIPAL, sp.up.FONTES_DE_PRECO[0][0],
   "e a principal e mesmo a PRIMEIRA da cascata, nao um nome escrito a mao "
   "ao lado dela")

# ── 4. Uma cascata de fonte unica nao se deixa ensaiar ───────────────────
# Sem esta guarda, tirar a unica fonte deixava a lista vazia, o ensaio dava
# VERMELHO em qualquer circunstancia e ensinava-se a ignora-lo.
_guarda = sp.up.FONTES_DE_PRECO
try:
    sp.up.FONTES_DE_PRECO = ((sp.FONTE_PRINCIPAL, lambda *a: {}),)
    _f, _n = sp._fontes_para("so_recurso")
    eq(len(_f), 1, "com uma fonte so, o ensaio nao fica sem fontes nenhumas")
    true(_n and "fonte so" in _n,
         "e diz porque e que nao ha nada para ensaiar")
finally:
    sp.up.FONTES_DE_PRECO = _guarda

print(f"TODOS OS {ok} TESTES PASSARAM")
