"""Uma edicao plausivel da newsletter, para os testes que precisam de uma.

Existe porque a validacao passou a ser real: desde que o send_newsletter recusa
publicar o que nao passa no parser do motor, um `"<html>teste</html>"` deixou de
servir como resposta do modelo nos testes. Esta funcao devolve o que uma edicao
boa tem de ter — e os testes de rejeicao partem-na de proposito.
"""

import re

LINHAS_ALLOC = [
    ("US Equities", 35),
    ("US Treasuries", 30),
    ("Investment-Grade Credit", 10),
    ("Commodities", 10),
    ("Cash", 10),
    ("Alternatives", 5),
]


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
    return edicao(issue_number, **kw)
