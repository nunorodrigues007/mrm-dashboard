# Backtest de validação — 2007-2026

Este directório contém o backtest que sustenta os números publicados no site e na
Academia, com os dados necessários para o reproduzir.

```
python backtest/final_backtest.py     # v1 vs v2, com custos, ano a ano
python backtest/sensitivity.py        # sensibilidade dos limiares
python tests/test_backtest.py         # fixa os números publicados (corre no CI)
python tests/test_costs.py            # a aritmética dos custos, verificada à mão
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

Uma diferença que não é detalhe: o v1 usa o score com o **E/P congelado** e o v2 o
score com o **E/P marcado a mercado**, porque foi isso que mudou na produção em
Setembro de 2026 — os earnings ficam ancorados numa referência trimestral e o preço
é marcado ao fecho diário. A comparação é antes contra depois, incluindo esta
diferença e não apesar dela. O efeito isolado é pequeno: 6,62% contra 6,59% de CAGR,
quebra máxima igual, 2008 igual. Muda uma coisa só — com o E/P a responder ao preço
o score nunca desce a 4,0 nesta amostra, e o episódio Resilient de 2021 desaparece.

## Capital e custos de transacção

O backtest corre sobre **$100.000 compostos** desde Fevereiro de 2007: os ganhos e
as perdas ficam no portfolio e são reinvestidos no rebalanceamento seguinte, e os
custos são deduzidos do valor antes de se comprar, pelo que também compõem.

O capital tem de ser declarado porque um custo fixo não é escalável: dez dólares
são 0,01% de cem mil e 0,003% de trezentos e cinquenta mil. Sem capital declarado,
"quanto custam as transacções" não tem resposta.

**Corretagem: $10 por abertura e $10 por fecho de posição.** Uma posição é um
ticker — dois buckets que partilhem instrumento (o SHV serve CASH e US_TREASURIES
em vários mapas) são uma linha só no corretor, e contam como uma.

Há duas leituras honestas de "custo por posição", e ambas são publicadas:

| Modelo | O que cobra | v2 paga | v1 paga |
|---|---|---|---|
| `open_close` | Literal: abrir e fechar. Um rebalanceamento semestral que só mexe nos pesos dos mesmos seis instrumentos não paga nada. | **$540** | $220 |
| `every_trade` | Realista: qualquer linha tocada é uma ordem executada, incluindo o acerto de peso de um instrumento já detido. | $3.300 | $2.600 |

As dezasseis transacções do v2 que abrem ou fecham posições, em dezanove anos e
meio:

| Quando | Custo | Porquê |
|---|---|---|
| 2007-02 | $60 | abertura inicial, seis posições |
| 2008-03 · 2020-05 · 2024-08 | $50 cada | entrada em stress: fecham IEF, LQD e PDBC, abrem GLD e a manga curta |
| 2010-12 · 2021-05 · 2024-11 | $50 cada | saída de stress, pelo caminho inverso |
| nove trocas de sub-regime | $20 cada | **só a manga de duração muda de instrumento: fecha o TLT, abre o SHY** |

Aquele último número responde à questão que este README deixava em aberto: as
seis trocas de sub-regime entre 2008 e 2010, apontadas como a omissão que mais
pesava, custam **cento e vinte dólares no total** — e as nove de todo o período,
cento e oitenta. A porta assimétrica FTQ/Stress troca um instrumento, não a
carteira.

**Nota sobre o Sortino.** Até Setembro de 2026 este backtest calculava o
Sortino com o desvio-padrão da sub-amostra de meses negativos, e não com o
*downside deviation* da definição — raiz da média dos quadrados dos retornos
abaixo do alvo sobre todos os períodos. Subestimava em cerca de 0,12, de forma
igual em todas as estratégias, pelo que a comparação se mantinha; mas o número
não era o que o nome dizia. Está corrigido, e todos os Sortino publicados são
os da definição padrão.

### O que isto não resolve

**A comissão fixa é o custo mais visível e o menos importante.** O que pesa é o
custo proporcional — spread entre compra e venda, e deslize entre a decisão e a
execução. Dez dólares são dez dólares quer se negoceiem mil ou cem mil; cinco
pontos base sobre sessenta mil transaccionados são trinta dólares, e o
rebalanceamento seguinte volta a pagá-los.

Sensibilidade, sobre o modelo `every_trade`:

| Custo proporcional | v2 CAGR | v2 custo total | v1 CAGR | Custo do seguro |
|---|---|---|---|---|
| 0 bp (só comissão) | 6,48% | $3.300 | 6,84% | 0,36 pp |
| 5 bp | 6,44% | $4.834 | 6,83% | 0,39 pp |
| 10 bp | 6,40% | $6.352 | 6,81% | 0,41 pp |
| 20 bp | 6,31% | $9.343 | 6,78% | 0,47 pp |

Duas leituras. A comissão está resolvida e é irrelevante a esta escala. O spread
não está medido — está modelado como sensibilidade — e como o sistema novo
negoceia mais do que o antigo, é ele que paga a diferença: o preço do seguro
alarga de 0,36 para 0,47 pp entre 0 e 20 pontos base.

**Impostos não estão modelados de todo**, em nenhuma jurisdição. Num regime que
tribute mais-valias realizadas, cada saída de stress é um evento fiscal, e a
conta pode ser maior do que tudo o que está nesta secção.

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

O SHY deixou de ser substituído: tem série própria desde 2006-12 e é agora
carregada. O HYG só começou a cotar em 2007-04, e os dois primeiros meses do
período (2007-02 e 2007-03) tomam o LQD — crédito de empresas, o de qualidade em
vez do de alto rendimento, pela mesma regra das linhas acima. O HYG só entra na
carteira em Resilient, um regime que nunca dispara no período, pelo que estes
dois meses não tocam em nenhum resultado publicado.

Os pesos de cada regime vêm do `mrm_rules.REGIME_WEIGHTS`, o mesmo dicionário
que a produção usa para montar a carteira. O backtest não tem constantes
próprias: se os pesos mudarem em produção, mudam aqui, e a comparação entre v1 e
v2 continua a isolar o efeito do medidor B porque ambos usam o mesmo vector fora
de Critical.

## Resultados

Período: 2007-02 a 2026-08, 235 meses.

Capital inicial $100.000, composto, com a corretagem de $10/$10 aplicada.

| | CAGR | Vol | Sharpe | Sortino | Quebra máx. | (mês) | Valor final |
|---|---|---|---|---|---|---|---|
| v1 — sistema anterior | 6,92% | 8,17% | 0,671 | 0,991 | −23,9% | 2009-02 | $368.554 |
| **v2 — sistema actual** | **6,58%** | **7,46%** | **0,683** | **1,031** | **−16,4%** | **2022-09** | **$346.055** |
| SPY buy & hold | 11,10% | 15,45% | 0,661 | 0,986 | −50,8% | 2009-02 | $778.729 |
| 60/40 SPY-IEF anual | 8,39% | 9,38% | 0,742 | 1,118 | −27,3% | 2009-02 | $480.890 |

Sem corretagem os mesmos números são 6,92% e 6,59% — a diferença é de um
centésimo de ponto percentual, e está aqui só para se ver que é isso mesmo.

Anos que decidem a diferença:

| Ano | v1 | v2 | SPY | Medidor B |
|---|---|---|---|---|
| 2008 | −13,8% | **+1,4%** | −36,8% | ON 10/12 meses |
| 2009 | +13,5% | +2,2% | +26,4% | ON 12/12 meses |
| 2020 | +11,2% | +5,9% | +18,4% | ON 8/12 meses |
| 2022 | −12,7% | −12,8% | −18,2% | — |

O medidor B disparou em 48 dos 235 meses: 2008-03 a 2010-12, 2020-05 a 2021-05,
e 2024-08 a 2024-11. Ficou calado em 2011, 2018 e 2022.

## O que estes números dizem, e o que não dizem

**O seguro funciona e tem preço.** 2008 passa de −13,8% para +1,4% e a quebra
máxima cai de −23,9% para −16,4%. Custa 0,34 pontos percentuais de CAGR ao longo
de dezanove anos, quase todos pagos nas recuperações: 2009 rende +2,2% contra
+13,5%, porque a regra de Sahm continua acima do limiar muito depois de o mercado
ter feito o fundo.

**A pior quebra do v2 já não é uma recessão — é 2022.** −16,4% em Setembro de
2022, um ano em que o medidor B ficou deliberadamente calado. Isto não é uma
falha do medidor: 2022 foi um bear market sem recessão, e o medidor existe para
separar as duas coisas. Mas é a consequência honesta da escolha: o sistema não
protege contra quedas de mercado sem deterioração do emprego e do crédito.

**O v2 continua a perder muito para o SPY.** 6,59% contra 11,10%, com um terço
da volatilidade e menos de um terço da quebra máxima. Quem compara só o CAGR
está a comparar coisas diferentes.

**Seis trocas de sub-regime entre 2008 e 2010** (nove em todo o período). Cada
uma é uma transacção real, e agora está paga: vinte dólares cada — fecha o TLT,
abre o SHY —, cento e vinte no período de crise e cento e oitenta no total,
porque a porta assimétrica troca um instrumento e não a carteira. O que continua
por medir é o spread — e os impostos, que não estão modelados de todo.

## Sensibilidade dos limiares

Os limiares não foram escolhidos por optimização — a regra de Sahm é publicada e
o limiar da delinquência é o decil 90 da própria série desde 1987. Ainda assim,
importa saber quão frágil é o resultado:

| Sahm | ΔNPL | FTQ | CAGR | Sortino | Quebra máx. | 2008 | Meses ON |
|---|---|---|---|---|---|---|---|
| **0,50** | **0,81** | **−0,10** | **6,59%** | **1,034** | **−16,4%** | **+1,5%** | **48** |
| 0,40 | 0,81 | −0,10 | 6,76% | 1,070 | −16,4% | +1,9% | 55 |
| 0,60 | 0,81 | −0,10 | 6,68% | 1,051 | −16,4% | +1,5% | 45 |
| 0,50 | 0,60 | −0,10 | 6,59% | 1,034 | −16,4% | +1,5% | 48 |
| 0,50 | 1,00 | −0,10 | 6,89% | 1,091 | −16,4% | +3,7% | 43 |
| 0,50 | 0,81 | −0,05 | 6,55% | 1,027 | −16,4% | +0,7% | 48 |
| 0,50 | 0,81 | −0,20 | 6,28% | 0,980 | −16,4% | −3,8% | 48 |
| só Sahm | — | −0,10 | 6,84% | 1,081 | −16,4% | +3,7% | 41 |
| — | só ΔNPL | −0,10 | 7,11% | 1,130 | −16,4% | +1,5% | 33 |

Duas leituras honestas desta tabela.

A primeira: o resultado é robusto. Em nenhuma variante a quebra máxima muda, o
CAGR fica entre 6,28% e 7,11%, e 2008 fica entre −3,8% e +3,7% — muito acima dos
−13,8% do sistema anterior em qualquer configuração.

A segunda: **a configuração escolhida não é a melhor desta amostra.** Usar só a
aceleração da delinquência daria 7,11% e Sortino 1,130. Mantemos os dois gatilhos
na mesma. A regra de Sahm é publicada, validada por terceiros e desenhada para
sobreviver a revisões; o limiar da delinquência é nosso, tirado dos mesmos dados.
Escolher o que ganha nesta amostra é exactamente o exercício de sobreajuste que o
resto do framework tenta evitar — e dois gatilhos independentes protegem contra
uma série falhar, ser revista ou ser descontinuada.

## O regime Resilient nunca dispara

O `score_real` — o mesmo que a produção publica, com o E/P marcado ao preço —
tem **mínimo de 4,38 em 21 anos** e **zero meses em 4,0 ou abaixo**. O limiar do
Resilient nunca é atingido, e as 15 mudanças de estado do período são todas de
entrada ou saída de Critical. Na prática o sistema tem dois regimes, não três.

A pergunta natural é se o limiar devia subir. O `resilient_grid.py` corre a
grelha (limiar × confirmação de N leituras consecutivas), com as séries reais de
QQQ, SHY, HYG e IWO:

| limiar | confirmação | CAGR | Vol | Sharpe | Sortino | Quebra máx. | meses em Resilient | entradas |
|---|---|---|---|---|---|---|---|---|
| **4,0 (actual)** | — | **6,58%** | 7,5% | **0,683** | **1,031** | **−16,4%** | 0 | 0 |
| 4,4 | — | 6,34% | 7,7% | 0,639 | 0,947 | −19,8% | 5 | 1 |
| 4,4 | 2 | 6,28% | 7,7% | 0,630 | 0,933 | −20,6% | 6 | 1 |
| 4,4 | 3 | 6,38% | 7,7% | 0,642 | 0,952 | −19,2% | 7 | 1 |
| 5,0 | — | 6,33% | 7,7% | 0,635 | 0,941 | −20,7% | 8 | 1 |
| 5,0 | 2 | 6,43% | 7,7% | 0,646 | 0,960 | −19,2% | 9 | 1 |
| 5,5 | — | 6,86% | 9,2% | 0,602 | 0,904 | −22,6% | 54 | 8 |
| 5,5 | 2 | 7,24% | 9,2% | 0,636 | 0,960 | −23,0% | 64 | 7 |
| 5,5 | 3 | 7,48% | 9,4% | 0,650 | 0,981 | −24,4% | 72 | 5 |

**Nenhuma variante melhora o Sharpe ou o Sortino, e todas pioram a quebra
máxima** — entre 2,8 e 8,0 pontos percentuais. A 5,5 há mais retorno, comprado
com mais volatilidade e mais oito pontos de quebra: o oposto do que o sistema
existe para fazer.

A razão é estrutural, não uma peculiaridade da amostra. O mapa Resilient carrega
**75% em activos de risco** contra 50% em Turbulence, e o momento em que dispara
é sempre o mesmo — 2021-08 nos limiares baixos, 2021-05/06 a 5,0, meses antes do
mercado de 2022. A quebra máxima acontece em 2022-09 em todas as variantes. O
score mede o risco *corrente*, não o futuro: o seu valor mais baixo é o mercado
mais complacente, que é historicamente o pior momento para adicionar risco. Um
ramo risk-on accionado por um mínimo de risco percebido compra no topo da calma.

Confirmar a mudança com duas ou três leituras consecutivas não salva nada: adia
a entrada, e a entrada é que é o problema.

**O limiar fica em 4,0.** Um ramo que nunca dispara é inofensivo; qualquer
versão que dispare piora exactamente o que interessa. Fica registado que os
limiares de 4,4 a 5,0 produzem **um único episódio** — é uma anedota, não uma
amostra; só a linha dos 5,5, com oito entradas, tem algum conteúdo estatístico, e
é a que mostra pior Sharpe de forma consistente.

## Limitações

- Frequência mensal. O sistema real corre à sexta-feira; o backtest usa fecho de mês.
- A corretagem está modelada ($10 por abertura, $10 por fecho, sobre $100.000
  compostos). O spread e o deslize de execução estão modelados como
  sensibilidade, não medidos. **Impostos não estão modelados de todo.**
- Um único caminho histórico: dezanove anos e três episódios de stress. Três
  observações não são uma amostra estatística, e nenhum intervalo de confiança
  daqui seria honesto.
- O E/P da reconstrução usa os earnings reais de cada trimestre; a produção usa uma
  referência manual actualizada trimestralmente e marcada ao preço diário. São
  próximos mas não idênticos — a produção conhece os earnings com o atraso da
  actualização manual, a reconstrução não.
