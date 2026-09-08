# Backtest de validação — 2007-2026

Este directório contém o backtest que sustenta os números publicados no site e na
Academia, com os dados necessários para o reproduzir.

```
python backtest/final_backtest.py     # v1 vs v2, ano a ano
python backtest/sensitivity.py        # sensibilidade dos limiares
python tests/test_backtest.py         # fixa os números publicados (corre no CI)
```

## O que está a ser testado

O ponto essencial: **a lógica não está reimplementada aqui**. O
`final_backtest.py` importa o `mrm_rules.py` do repositório e chama as mesmas
funções que correm à sexta-feira — `classify_regime`, `subregime_from_gauge`,
`decide_rebalance`, `effective_bucket_alloc`. O que é testado é o código que está
em produção. Se alguém mexer numa regra, os números mudam e o
`tests/test_backtest.py` falha.

**v1** — o sistema anterior a Setembro de 2026: o regime decidido pelo Global
Resilience Score, com confirmação de duas leituras consecutivas, e rebalanceamento
semestral.

**v2** — o sistema actual: o regime Critical decidido pelo medidor B, com os pesos
de Critical a sobreporem-se à alocação macro, saída imediata, e a porta assimétrica
FTQ/Stress.

## Dados

| Ficheiro | Conteúdo |
|---|---|
| `data/scores.json` | 260 meses (2005-01 a 2026-08) do score reconstruído pilar a pilar, com percentis em janela expansiva e desfasamentos de publicação reais |
| `data/monthly.csv` | 10Y mensal (FRED DGS10) |
| `data/fred_quarterly.csv` | Delinquência bancária trimestral (FRED DRALACBN) |
| `data/sahm_realtime.txt` | Regra de Sahm em vintage real-time (FRED SAHMREALTIME), 1994-2026 |
| `data/px/*.txt` | Preços mensais dos ETF |

Não há hindsight na reconstrução: cada mês vê apenas o que estava publicado nesse
mês, com o atraso de publicação de cada série (Sahm: 1 mês; delinquência: 5 meses)
e percentis calculados só com a história até esse ponto.

Uma excepção assinalada: a observação de Outubro de 2025 da série de Sahm não
existe na série publicada e é interpolada entre Setembro e Novembro. Não cai perto
de nenhum limiar.

## Substituições de ETF

Nem todos os instrumentos de produção existiam em 2007. As substituições estão
declaradas no código e nenhuma favorece o sistema novo:

| Produção | Backtest | Porquê |
|---|---|---|
| PDBC | DBC | PDBC cotado desde 2014-02; mesmo cabaz de matérias-primas |
| BIL | SHV | BIL desde 2007-05; bilhetes do Tesouro 0-1 ano |
| SGOV | SHV | SGOV desde 2020-05 |
| USMV | SPY | USMV (baixa volatilidade) desde 2011-10. **Penaliza o v2**: em Critical fica com beta total em vez de um fator defensivo |
| SHY | SHV | Sem série completa; SHV é ainda mais curto, logo mais conservador |

Fora de Critical, as percentagens vêm em produção da newsletter semanal, que não
existe antes de Março de 2026. O backtest usa um vector fixo, **igual nos dois
sistemas**, para que a comparação isole o efeito do medidor B e não o do
julgamento semanal.

## Resultados

Período: 2007-02 a 2026-08, 235 meses.

| | CAGR | Vol | Sharpe | Sortino | Quebra máx. | (mês) |
|---|---|---|---|---|---|---|
| v1 — sistema anterior | 6,93% | 8,17% | 0,671 | 0,870 | −23,9% | 2009-02 |
| **v2 — sistema actual** | **6,60%** | **7,54%** | **0,681** | **0,911** | **−16,4%** | **2022-09** |
| SPY buy & hold | 11,10% | 15,45% | 0,661 | 0,899 | −50,8% | 2009-02 |
| 60/40 SPY-IEF anual | 8,39% | 9,38% | 0,742 | 0,993 | −27,3% | 2009-02 |

Anos que decidem a diferença:

| Ano | v1 | v2 | SPY | Medidor B |
|---|---|---|---|---|
| 2008 | −13,8% | **+1,5%** | −36,8% | ON 10/12 meses |
| 2009 | +13,5% | +2,2% | +26,4% | ON 12/12 meses |
| 2020 | +11,2% | +5,9% | +18,4% | ON 8/12 meses |
| 2022 | −12,7% | −12,8% | −18,2% | — |

O medidor B disparou em 48 dos 235 meses: 2008-03 a 2010-12, 2020-05 a 2021-05,
e 2024-08 a 2024-11. Ficou calado em 2011, 2018 e 2022.

## O que estes números dizem, e o que não dizem

**O seguro funciona e tem preço.** 2008 passa de −13,8% para +1,5% e a quebra
máxima cai de −23,9% para −16,4%. Custa 0,33 pontos percentuais de CAGR ao longo
de dezanove anos, quase todos pagos nas recuperações: 2009 rende +2,2% contra
+13,5%, porque a regra de Sahm continua acima do limiar muito depois de o mercado
ter feito o fundo.

**A pior quebra do v2 já não é uma recessão — é 2022.** −16,4% em Setembro de
2022, um ano em que o medidor B ficou deliberadamente calado. Isto não é uma
falha do medidor: 2022 foi um bear market sem recessão, e o medidor existe para
separar as duas coisas. Mas é a consequência honesta da escolha: o sistema não
protege contra quedas de mercado sem deterioração do emprego e do crédito.

**O v2 continua a perder muito para o SPY.** 6,60% contra 11,10%, com um terço
da volatilidade e menos de um terço da quebra máxima. Quem compara só o CAGR
está a comparar coisas diferentes.

**Sete trocas de sub-regime entre 2008 e 2010.** Cada uma é uma transacção real.
O backtest não modela custos de transacção nem impostos, e este é o período onde
essa omissão mais pesa.

## Sensibilidade dos limiares

Os limiares não foram escolhidos por optimização — a regra de Sahm é publicada e
o limiar da delinquência é o decil 90 da própria série desde 1987. Ainda assim,
importa saber quão frágil é o resultado:

| Sahm | ΔNPL | FTQ | CAGR | Sortino | Quebra máx. | 2008 | Meses ON |
|---|---|---|---|---|---|---|---|
| **0,50** | **0,81** | **−0,10** | **6,60%** | **0,911** | **−16,4%** | **+1,5%** | **48** |
| 0,40 | 0,81 | −0,10 | 6,74% | 0,932 | −16,4% | +1,6% | 55 |
| 0,60 | 0,81 | −0,10 | 6,69% | 0,926 | −16,4% | +1,5% | 45 |
| 0,50 | 0,60 | −0,10 | 6,60% | 0,911 | −16,4% | +1,5% | 48 |
| 0,50 | 1,00 | −0,10 | 6,88% | 0,959 | −16,4% | +3,5% | 43 |
| 0,50 | 0,81 | −0,05 | 6,56% | 0,905 | −16,4% | +0,7% | 48 |
| 0,50 | 0,81 | −0,20 | 6,29% | 0,857 | −16,4% | −3,9% | 48 |
| só Sahm | — | −0,10 | 6,83% | 0,955 | −16,4% | +3,5% | 41 |
| — | só ΔNPL | −0,10 | 7,12% | 1,004 | −16,4% | +1,5% | 33 |

Duas leituras honestas desta tabela.

A primeira: o resultado é robusto. Em nenhuma variante a quebra máxima muda, o
CAGR fica entre 6,29% e 7,12%, e 2008 fica entre −3,9% e +3,5% — muito acima dos
−13,8% do sistema anterior em qualquer configuração.

A segunda: **a configuração escolhida não é a melhor desta amostra.** Usar só a
aceleração da delinquência daria 7,12% e Sortino 1,004. Mantemos os dois gatilhos
na mesma. A regra de Sahm é publicada, validada por terceiros e desenhada para
sobreviver a revisões; o limiar da delinquência é nosso, tirado dos mesmos dados.
Escolher o que ganha nesta amostra é exactamente o exercício de sobreajuste que o
resto do framework tenta evitar — e dois gatilhos independentes protegem contra
uma série falhar, ser revista ou ser descontinuada.

## Limitações

- Frequência mensal. O sistema real corre à sexta-feira; o backtest usa fecho de mês.
- Sem custos de transacção, spreads nem impostos.
- Um único caminho histórico: dezanove anos e três episódios de stress. Três
  observações não são uma amostra estatística, e nenhum intervalo de confiança
  daqui seria honesto.
- O E/P do S&P 500 na reconstrução usa a série real, enquanto a produção usa uma
  constante mantida à mão. É a diferença conhecida entre o backtest e o sistema vivo.
