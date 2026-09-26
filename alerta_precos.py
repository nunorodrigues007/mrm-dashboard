#!/usr/bin/env python3
"""
alerta_precos.py — torna a avaria de cotacoes RUIDOSA.

Porque existe. O `update-portfolio` TEVE EXITO nas tres semanas de Setembro de
2026 em que publicou o mesmo preco ao centimo: o motor esta desenhado para
publicar com aviso em vez de parar, e essa decisao esta certa — parar a
publicacao para sempre porque um ETF foi retirado de bolsa e pior do que
publicar com o aviso. Mas a consequencia era que NADA ficava vermelho: o
`alert-on-failure` corre `if: failure()` e nunca disparou, e a unica forma de dar
pela avaria era alguem ir ver o portfolio.json a mao. Foi o que aconteceu, tres
semanas seguidas.

Um sistema que tem exito enquanto esta errado nao tem um bug de precos — tem um
bug de silencio. Este script e o barulho: le o que a corrida acabou de publicar
e, se a valorizacao nao e desta semana, abre (ou actualiza) um issue com mencao
ao dono, que e o que faz a notificacao chegar por e-mail e por telefone.

Nao decide nada sobre a carteira e nao para o pipeline: sai sempre com 0 excepto
se o proprio portfolio.json estiver ilegivel. Uma falha a FALAR sobre a avaria
nao deve transformar-se numa segunda avaria.
"""
import json, os, sys, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORTFOLIO = ROOT / "portfolio.json"

# A fonte que nao consome quota e nao precisa de segredo. Um preco que venha de
# qualquer outra significa que esta a correr sobre um unico motor.
FONTE_PRINCIPAL = "yahoo"

ETIQUETA_CONGELADO = "precos-congelados"
ETIQUETA_DEGRADADO = "precos-degradados"


def diagnostica(cur):
    """(gravidade, congelados, degradados) a partir do bloco `current`.

    gravidade: None (nada a dizer), "degradado" (a principal caiu mas houve
    recurso) ou "congelado" (nenhuma fonte deu preco — a valorizacao nao e
    desta semana).
    """
    congelados = sorted(cur.get("valuation_frozen") or [])
    fontes = cur.get("price_sources") or {}
    degradados = sorted(t for t, f in fontes.items() if f != FONTE_PRINCIPAL)
    if congelados:
        return "congelado", congelados, degradados
    if degradados:
        return "degradado", congelados, degradados
    return None, congelados, degradados


def corpo_congelado(cur, congelados, dono):
    idades = cur.get("valuation_frozen_days") or {}
    datas = cur.get("valuation_frozen_dates") or {}
    limite = cur.get("price_frozen_after_days")
    pior = max((idades.get(t, 0) for t in congelados), default=0)
    linhas = [
        f"@{dono}",
        "",
        f"**A valorizacao publicada a {cur.get('date')} NAO usa precos desta semana.**",
        "",
        f"Nenhuma das fontes de preco deu um fecho utilizavel para "
        f"{len(congelados)} instrumento(s). O valor da carteira e o P&L que o site "
        f"e a newsletter mostram sao o do ultimo preco conhecido.",
        "",
        "| Instrumento | Ultimo preco de | Idade |",
        "|---|---|---|",
    ]
    for t in congelados:
        linhas.append(f"| `{t}` | {datas.get(t, '?')} | {idades.get(t, '?')} dias |")
    linhas += [
        "",
        f"- Valor publicado: **{cur.get('portfolio_value')}** "
        f"(P&L {cur.get('portfolio_pnl_pct')}%)",
        f"- Alpha publicado: **{cur.get('alpha_vs_benchmark_pct')} pp** — e o que mais "
        f"engana, porque o benchmark congela no mesmo instante e o defice aparece "
        f"menor do que e.",
    ]
    if limite is not None:
        # O motor dispara em `idade > limite`, nao em `idade == limite`: aos 21
        # dias exactos a valorizacao ainda e publicada como aproximacao. Um dia a
        # mais aqui e um aviso que chega uma semana adiantado e treina quem o le
        # a nao acreditar nele.
        restam = limite - pior + 1
        linhas += [
            "",
            f"O limite declarado e de **{limite} dias** (`price_frozen_after_days`). "
            + (f"Faltam **{restam} dias** para a valorizacao passar a ser publicada "
               f"como *not credible* e qualquer rebalanceamento ser cancelado."
               if restam > 0 else
               "**Esse limite JA foi passado**: a edicao publica a valorizacao como "
               "*not credible* e cancela rebalanceamentos."),
        ]
    linhas += [
        "",
        "**O que verificar, por esta ordem:**",
        "",
        "1. O segredo `ALPHAVANTAGE_API_KEY` esta configurado no repositorio? Sem "
        "ele a segunda fonte nao arranca e o sistema volta a depender so da Yahoo.",
        "2. Correr `python -c` com o yfinance FORA do runner. Se der os fechos, o "
        "problema e o acesso do runner, nao os dados — foi o caso a 11 de Setembro "
        "de 2026.",
        "3. O log do job `update-portfolio` diz, por ticker e por fonte, o que "
        "falhou (`via yahoo attempt N failed`, `via alphavantage attempt N failed`).",
    ]
    return "\n".join(linhas)


def corpo_degradado(cur, degradados, dono):
    fontes = cur.get("price_sources") or {}
    linhas = [
        f"@{dono}",
        "",
        "**Os precos desta semana vieram, mas nao da fonte principal.**",
        "",
        f"A corrida de {cur.get('date')} publicou uma valorizacao correcta — isto "
        f"nao e uma avaria na carteira. E o aviso de que o sistema esta a correr "
        f"sobre uma unica fonte: se a de recurso tambem cair, a semana seguinte "
        f"publica precos congelados.",
        "",
        "| Instrumento | Fonte que serviu |",
        "|---|---|",
    ]
    for t in degradados:
        linhas.append(f"| `{t}` | `{fontes.get(t)}` |")
    linhas += [
        "",
        f"A fonte principal (`{FONTE_PRINCIPAL}`) falhou para estes. Vale a pena "
        f"perceber porque antes de a de recurso esgotar a quota diaria.",
    ]
    return "\n".join(linhas)


# ── A camada que fala com o GitHub ─────────────────────────────────────────
def _api(metodo, caminho, token, corpo=None):
    req = urllib.request.Request(
        f"https://api.github.com{caminho}", method=metodo,
        data=json.dumps(corpo).encode() if corpo is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json",
                 "User-Agent": "mrm-alerta-precos"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def publica(repo, token, etiqueta, titulo, corpo):
    """Abre o issue, ou comenta no que ja esta aberto com a mesma etiqueta.

    Sem esta deduplicacao, tres semanas de avaria davam tres issues iguais e a
    terceira ja nao se lia. Um fio por avaria, que cresce enquanto ela durar.
    """
    # Na PRIMEIRA vez que este alerta dispara, a etiqueta ainda nao existe no
    # repositorio. Um filtro por etiqueta inexistente nao pode impedir o alerta
    # de sair — seria falhar exactamente na estreia, que e a corrida que mais
    # importa. Nao se encontrar fio aberto, seja porque nao ha seja porque a
    # etiqueta nao existe, da no mesmo: abre-se um. (A etiqueta e criada pelo
    # proprio `issues.create`, como ja acontece com `pipeline-failure`.)
    try:
        abertos = _api("GET",
                       f"/repos/{repo}/issues?state=open&labels={etiqueta}&per_page=1",
                       token) or []
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        abertos = []
    if abertos:
        n = abertos[0]["number"]
        _api("POST", f"/repos/{repo}/issues/{n}/comments", token, {"body": corpo})
        return f"comentado no issue #{n} ({etiqueta})"
    novo = _api("POST", f"/repos/{repo}/issues", token,
                {"title": titulo, "body": corpo, "labels": [etiqueta]})
    return f"aberto o issue #{novo['number']} ({etiqueta})"


def main():
    try:
        cur = (json.loads(PORTFOLIO.read_text(encoding="utf-8")) or {}).get("current") or {}
    except Exception as e:
        print(f"::error::alerta_precos: portfolio.json ilegivel ({e})")
        return 1

    gravidade, congelados, degradados = diagnostica(cur)
    if gravidade is None:
        print("alerta_precos: os precos desta semana vieram todos da fonte "
              "principal — nada a comunicar.")
        return 0

    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    dono = repo.split("/")[0] if "/" in repo else "o dono do repositorio"
    data = cur.get("date")

    if gravidade == "congelado":
        etiqueta = ETIQUETA_CONGELADO
        titulo = f"🧊 Precos congelados — valorizacao de {data} nao e desta semana"
        corpo = corpo_congelado(cur, congelados, dono)
        print(f"::error::precos congelados em {', '.join(congelados)} — "
              f"a valorizacao de {data} nao usa precos desta semana")
    else:
        etiqueta = ETIQUETA_DEGRADADO
        titulo = f"⚠️ Fonte de precos degradada — {data}"
        corpo = corpo_degradado(cur, degradados, dono)
        print(f"::warning::a fonte principal falhou para "
              f"{', '.join(degradados)} — servidos por fonte de recurso")

    if not token or not repo:
        print("alerta_precos: sem GITHUB_TOKEN/GITHUB_REPOSITORY — o alerta fica "
              "no log desta corrida. Corpo que seria publicado:")
        print(corpo)
        return 0

    try:
        print(f"alerta_precos: {publica(repo, token, etiqueta, titulo, corpo)}")
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TypeError) as e:
        # Falhar a ABRIR o alerta nao pode pintar o pipeline de vermelho: isso
        # dispararia o `alert-on-failure` com um diagnostico errado ("a sexta
        # falhou") por causa de um soluco da API do GitHub.
        print(f"::error::alerta_precos: nao foi possivel publicar o alerta ({e}). "
              f"O diagnostico fica aqui:")
        print(corpo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
