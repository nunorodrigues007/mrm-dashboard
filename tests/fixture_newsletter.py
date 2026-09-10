"""Uma edicao plausivel da newsletter, para os testes que precisam de uma.

Existe porque a validacao passou a ser real: desde que o send_newsletter recusa
publicar o que nao passa no parser do motor, um `"<html>teste</html>"` deixou de
servir como resposta do modelo nos testes. Esta funcao devolve o que uma edicao
boa tem de ter — e os testes de rejeicao partem-na de proposito.
"""

import re

# A tabela de uma edicao boa e, desde Set 2026, a alocacao que o motor executou —
# nao um vector plausivel qualquer. O `validate_newsletter` compara-a com o
# `REGIME_WEIGHTS` e recusa a edicao que dela se afaste mais de um ponto
# percentual, portanto uma fixture com numeros inventados deixaria de passar por
# uma edicao boa. Deriva-se das regras, arredondada como o prompt a manda
# escrever: e assim que uma pessoa a le, e e o que o parser volta a ler.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import mrm_rules as _rules

_NOMES_ALLOC = [
    ("US Equities", "US_EQUITIES"),
    ("US Treasuries", "US_TREASURIES"),
    ("Investment-Grade Credit", "IG_CREDIT"),
    ("Commodities", "COMMODITIES"),
    ("Cash", "CASH"),
    ("Alternatives", "ALTERNATIVES"),
]


def linhas_de(regime="Turbulence"):
    """As seis linhas da tabela, com as percentagens do vector desse regime."""
    v = _rules.REGIME_WEIGHTS[regime]
    return [(nome, round(v[bucket])) for nome, bucket in _NOMES_ALLOC]


LINHAS_ALLOC = linhas_de("Turbulence")


def edicao(issue_number, avisos=(), alloc=LINHAS_ALLOC, enchimento=12000,
           fechar=True, com_tabela=True, score=6.97):
    linhas = "\n".join(
        f'<tr><td>{nome}</td><td>{pct}%</td><td>Rationale</td><td>+0.0</td></tr>'
        for nome, pct in alloc)
    tabela = (
        '<table><thead><tr><th>Asset Class</th><th>Allocation</th>'
        '<th>Rationale</th><th>WoW</th></tr></thead><tbody>'
        f'{linhas}</tbody></table>') if com_tabela else ""
    caixa = ("".join(f'<div class="data-quality"><p>{a}</p></div>' for a in avisos))
    _s = f"{score:.1f}" if isinstance(score, (int, float)) else str(score)
    corpo = (
        f'<div class="wrapper"><div class="header"><h1>MRM Weekly Audit '
        f'- Issue #{issue_number}</h1></div>'
        f'<div class="content"><div class="score" data-mrm-score="{_s}">{_s}</div>'
        f'{caixa}'
        f'<div class="section"><h2>Gauge B</h2><p>{"Concurrent stress reading. " * 40}</p></div>'
        f'<div class="section"><h2>CIO Verdict</h2><p>{"Positioning commentary. " * 40}</p></div>'
        f'<div class="section"><h2>Allocation</h2>{tabela}</div>'
        f'<div class="section"><p>{"Analysis paragraph. " * (enchimento // 20)}</p></div>'
        f'</div></div>')
    html = (f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">'
            f'<title>MRM Weekly Audit - Issue #{issue_number}</title></head>'
            f'<body>{corpo}</body>')
    return html + "</html>" if fechar else html[:len(html) // 2]


def score_do_prompt(prompt):
    """O score que o motor pos no esqueleto do prompt."""
    m = re.search(r'data-mrm-score="([^"]*)"', prompt)
    if not m:
        return 6.97
    try:
        return float(m.group(1))
    except ValueError:
        return m.group(1)          # semanas n/d: o motor nao publica numero


def avisos_do_prompt(prompt):
    """Os avisos de qualidade de dados que o prompt exige, palavra por palavra."""
    return [l.strip()[2:].strip() for l in prompt.splitlines()
            if l.strip().startswith("! DATA QUALITY")]


def alloc_do_prompt(prompt, prefixo="- Effective allocation now:"):
    """As seis linhas da tabela, com as percentagens que o prompt mandou.

    O gerador escreve `- Effective allocation now: US_EQUITIES: 41% | ...`, e a
    regra 8 manda reproduzi-las. Se a linha nao existir — um prompt de um teste
    que a nao inclui — devolve-se o vector de Turbulence, que e o que uma semana
    normal executa."""
    for linha in prompt.splitlines():
        if linha.strip().startswith(prefixo):
            corpo = linha.split(":", 1)[1]
            lidos = {}
            for parte in corpo.split("|"):
                if ":" not in parte:
                    continue
                bucket, pct = parte.split(":", 1)
                try:
                    lidos[bucket.strip()] = int(round(float(pct.strip().rstrip("%"))))
                except ValueError:
                    continue
            if all(b in lidos for _, b in _NOMES_ALLOC):
                return [(nome, lidos[b]) for nome, b in _NOMES_ALLOC]
    return linhas_de("Turbulence")


def edicao_do_prompt(prompt, issue_number, **kw):
    """A edicao que um modelo OBEDIENTE escreveria para ESTE prompt.

    O score e os avisos saem do proprio prompt, que e onde o gerador os poe —
    nao de constantes. Um fixture com o score fixo em 6,97 e sem aviso nenhum so
    passa na semana em que o data.json commitado disser 6,97 e nao houver nada a
    avisar: basta o score sair da janela de tolerancia — aconteceu na maioria
    das edicoes ja publicadas — ou uma serie da FRED parar de publicar para a
    validacao recusar a edicao tres vezes. E como os testes que usam este
    fixture sao portao dos dois jobs de sexta, isso nao daria um teste vermelho
    na segunda-feira: daria uma semana sem carteira e sem newsletter, na semana
    em que os avisos existem precisamente para serem lidos.
    """
    kw.setdefault("score", score_do_prompt(prompt))
    kw.setdefault("avisos", avisos_do_prompt(prompt))
    # E a tabela de alocacao tambem sai do prompt, pela mesma razao que o score:
    # desde Set 2026 a regra 8 da ao modelo o vector EXACTO que o motor executou
    # e a validacao recusa a edicao que dele se afaste. Um modelo obediente
    # copia-o. Um fixture com percentagens proprias nao e um modelo obediente —
    # e um modelo que inventa, que e o caso que os testes de rejeicao cobrem de
    # propósito noutro sitio.
    kw.setdefault("alloc", alloc_do_prompt(prompt))
    return edicao(issue_number, **kw)
