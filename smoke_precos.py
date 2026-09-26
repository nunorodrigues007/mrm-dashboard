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
# fonte: a Yahoo responde e a de recurso e saltada, que e o comportamento certo.
# A consequencia e que "temos segunda fonte" fica por verificar ate a noite em
# que a primeira cair — e essa noite e a pior possivel para descobrir que o
# runner tambem nao fala com a segunda. Aqui desliga-se a principal de proposito.
FONTE_PRINCIPAL = "yahoo"


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
        print("  -> sem ela a cascata corre com a Yahoo sozinha, que e a fonte "
              "que falhou. Criar o segredo em Settings > Secrets and variables "
              "> Actions.")
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
        if fonte != "yahoo":
            de_recurso.append(t)
        print(f"  {t:<{largura}}  {p:>10.4f}  de {price_dates[t]}  via {fonte}")

    print("=" * 66)
    if sem_preco:
        if modo == "so_recurso":
            print(f"VERMELHO (ensaio): {', '.join(sem_preco)} sem cotação com a "
                  f"`{FONTE_PRINCIPAL}` desligada. A carteira NÃO está em risco "
                  f"hoje — a principal continua a servir. Mas a rede de "
                  f"segurança não responde a partir deste runner, e é exactamente "
                  f"isso que este ensaio existe para descobrir ANTES de ser "
                  f"precisa. Ver se a chave é válida e se o runner alcança a "
                  f"fonte de recurso.")
        else:
            print(f"VERMELHO: {', '.join(sem_preco)} sem cotação nenhuma. Se isto "
                  f"acontecesse na sexta, a carteira seria publicada ao último "
                  f"preço conhecido e o alerta abriria um issue.")
        return 1
    if modo == "so_recurso":
        print(f"VERDE (ensaio): a fonte de recurso valorizou os {len(tickers)} "
              f"instrumentos a partir deste runner, com a `{FONTE_PRINCIPAL}` "
              f"desligada. A rede de segurança não é só uma declaração no "
              f"código — arranca e responde.")
        return 0
    if de_recurso:
        print(f"AMARELO: tudo valorizado, mas {', '.join(de_recurso)} veio da "
              f"fonte de recurso — a Yahoo continua em baixo neste runner. O "
              f"sistema publicaria CORRECTO na mesma. É a cascata a trabalhar.")
        return 0
    print(f"VERDE: os {len(tickers)} vieram da Yahoo. O acesso do runner está "
          f"restabelecido.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
