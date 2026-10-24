#!/usr/bin/env python3
"""
smoke_precos.py — as cotacoes chegam ao runner? Sem tocar em nada.

Porque existe. A avaria de Setembro de 2026 so era observavel na sexta-feira a
noite, depois de a carteira ja ter sido publicada com precos velhos. Descobrir
que o runner nao fala com a Yahoo NAO tem de custar uma semana de espera nem uma
edicao errada: isto corre a proci, a pedido, e responde a unica pergunta que
interessa — que fonte serve cada instrumento a partir DESTE runner.

Nao escreve ficheiros, nao faz commit, nao envia nada. Le o portfolio.json so
para saber que instrumentos estao detidos.
"""
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import update_portfolio as up


# O ensaio de recurso. A cascata, quando trabalha bem, NUNCA exercita a segunda
# fonte: a principal responde e a de recurso e saltada, que e o comportamento
# certo. A consequencia e que "temos segunda fonte" fica por verificar ate a
# noite em que a primeira cair — e essa noite e a pior possivel para descobrir
# que o runner tambem nao fala com a segunda. Aqui desliga-se a principal de
# proposito.
#
# Desde a inversao de Outubro de 2026 a principal e a Alpha Vantage e a rede de
# recurso e a Yahoo, portanto o `so_recurso` passa a exercitar a YAHOO. Continua
# a ser o unico caminho que poe a segunda fonte a prova de proposito.
FONTE_PRINCIPAL = "alphavantage"


def _fontes_para(modo):
    """(fontes a usar, nota a imprimir) para o modo pedido."""
    if modo != "so_recurso":
        return up.FONTES_DE_PRECO, None
    restantes = tuple((n, f) for n, f in up.FONTES_DE_PRECO if n != FONTE_PRINCIPAL)
    if not restantes:
        return up.FONTES_DE_PRECO, ("nao ha nenhuma fonte alem da principal para "
                                    "ensaiar — a cascata tem uma fonte so")
    return restantes, (f"ENSAIO: a `{FONTE_PRINCIPAL}` foi desligada de proposito. "
                       f"Isto nao e uma avaria — e a verificacao de que a rede de "
                       f"seguranca arranca a partir deste runner.")


# ── O VEREDICTO ───────────────────────────────────────────────────────────
#
# Uma funcao PURA: sem rede, sem impressao, sem estado. Existe por duas razoes,
# as duas descobertas a 24 de Outubro de 2026.
#
# A primeira: ate essa data o veredicto vivia dentro do `main()`, entrelacado
# com os `print`, e por isso nao tinha teste NENHUM — o proprio
# `test_smoke_precos.py` dizia, na docstring, "so se testa a escolha das fontes
# e os textos". Foi essa cegueira que deixou o literal "yahoo" da classificacao
# por fonte sobreviver a tres inventarios da politica. Um semaforo por testar e
# um semaforo que ninguem verificou.
#
# A segunda: o AMARELO devolvia 0, igual ao VERDE. Uma quinta-feira em que a
# fonte principal ja estava em baixo saia `success`. Aconteceu nas quintas de
# 15 e de 22 de Outubro de 2026 — as duas verdes, as duas seguidas de uma sexta
# servida pelo recurso. Agora devolve 1.
def veredicto(modo, n_instrumentos, sem_preco, de_recurso, principal=None):
    """(codigo de saida, rotulo, texto de uma linha). Nao imprime nada."""
    p = principal if principal is not None else FONTE_PRINCIPAL
    if sem_preco:
        if modo == "so_recurso":
            return 1, "VERMELHO (ensaio)", (
                f"{', '.join(sem_preco)} sem cotação com a `{p}` desligada. A "
                f"carteira NÃO está em risco hoje — a principal continua a "
                f"servir. Mas a rede de segurança não responde a partir deste "
                f"runner, e é exactamente isso que este ensaio existe para "
                f"descobrir ANTES de ser precisa. Ver se a chave é válida e se "
                f"o runner alcança a fonte de recurso.")
        return 1, "VERMELHO", (
            f"{', '.join(sem_preco)} sem cotação nenhuma. Se isto acontecesse "
            f"na sexta, a carteira seria publicada ao último preço conhecido e "
            f"o alerta abriria um issue.")
    if modo == "so_recurso":
        return 0, "VERDE (ensaio)", (
            f"a fonte de recurso valorizou os {n_instrumentos} instrumentos a "
            f"partir deste runner, com a `{p}` desligada. A rede de segurança "
            f"não é só uma declaração no código — arranca e responde.")
    if de_recurso:
        return 1, "AMARELO", (
            f"tudo valorizado, mas {', '.join(de_recurso)} veio da fonte de "
            f"recurso — a `{p}` não serviu neste runner. A carteira de sexta "
            f"sairia CORRECTA, pela cascata; o que fica em risco é a semana "
            f"SEGUINTE, porque o sistema passa a correr sobre uma fonte só. "
            f"Devolve 1 de propósito: este aviso tem de se ver da lista de "
            f"corridas, não só de dentro do log.")
    return 0, "VERDE", (
        f"os {n_instrumentos} vieram da `{p}`. O acesso do runner esta bom.")


def main():
    chave = (os.environ.get("ALPHAVANTAGE_API_KEY") or "").strip()
    modo = (os.environ.get("SMOKE_FONTE") or "cascata").strip() or "cascata"
    print("=" * 66)
    print("TESTE DE FUMO ÀS COTAÇÕES — não escreve nada, não envia nada")
    print("=" * 66)
    # Nunca o VALOR, so se existe e o tamanho: um segredo impresso num log de um
    # repositorio publico deixa de ser um segredo.
    print(f"ALPHAVANTAGE_API_KEY configurada: "
          f"{'SIM (' + str(len(chave)) + ' caracteres)' if chave else 'NAO'}")
    if not chave:
        print("  -> sem ela a fonte PRINCIPAL esta desligada e a cascata corre "
              "com a Yahoo sozinha, que e a intermitente. Criar o segredo em "
              "Settings > Secrets and variables > Actions.")
    print(f"Fontes declaradas, por ordem: "
          f"{', '.join(n for n, _ in up.FONTES_DE_PRECO)}")
    fontes, nota = _fontes_para(modo)
    print(f"Modo: {modo}")
    if nota:
        print(f"  -> {nota}")
        print(f"  -> Fontes em uso nesta corrida: "
              f"{', '.join(n for n, _ in fontes)}")

    alvo = up.adjust_for_market_holiday(up.get_last_friday())
    print(f"Sexta-feira alvo: {alvo}")

    try:
        cur = json.loads((ROOT / "portfolio.json").read_text(encoding="utf-8"))["current"]
        tickers = sorted(set(cur.get("shares") or {}) | {"SPY"})
    except Exception as e:
        print(f"  (portfolio.json ilegivel: {e} — a usar o universo de Turbulence)")
        tickers = ["BIL", "IEF", "LQD", "PDBC", "SPY", "VNQ"]
    print(f"Instrumentos: {', '.join(tickers)}")
    print("-" * 66)

    guarda = up.FONTES_DE_PRECO
    try:
        up.FONTES_DE_PRECO = fontes
        prices, price_dates, stale = up.fetch_prices(tickers, alvo)
    finally:
        up.FONTES_DE_PRECO = guarda

    print("-" * 66)
    largura = max(len(t) for t in tickers)
    sem_preco, de_recurso = [], []
    for t in tickers:
        p = prices.get(t)
        if p is None:
            sem_preco.append(t)
            print(f"  {t:<{largura}}  SEM COTAÇÃO — nenhuma fonte respondeu")
            continue
        fonte = up.ULTIMAS_FONTES.get(t, "?")
        # Contra a CONSTANTE, nunca contra um literal. Com "yahoo" escrito aqui
        # a mao, a inversao de Outubro de 2026 teria trocado o significado do
        # AMARELO e do VERDE sem fechar o portao: nenhum teste exercita esta
        # linha, e o `test_smoke_precos.py` so prende as constantes.
        if fonte != FONTE_PRINCIPAL:
            de_recurso.append(t)
        print(f"  {t:<{largura}}  {p:>10.4f}  de {price_dates[t]}  via {fonte}")

    print("=" * 66)
    codigo, rotulo, texto = veredicto(modo, len(tickers), sem_preco, de_recurso)
    print(f"{rotulo}: {texto}")
    if codigo != 0:
        # A ANOTAÇÃO, e não só o `print`. Um `print` vive dentro do passo e só
        # se vê abrindo a corrida; um `::error::` aparece no resumo da corrida e
        # na lista. A sentinela existe para avisar com 24 horas de antecedência,
        # e um aviso que obriga a abrir o log é um aviso que ninguém lê — foi
        # exactamente o que aconteceu a 15 e a 22 de Outubro de 2026.
        print(f"::error title=Sentinela das cotações — {rotulo}::{texto}")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
