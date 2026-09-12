# Runbook operacional — USMRM

O que fazer quando o pipeline de sexta falha. Escrito para ser lido às 23:00 de
uma sexta-feira, não para ser bonito.

O pipeline (`.github/workflows/friday-pipeline.yml`) corre às 22:00 UTC e tem
quatro jobs em cadeia: `refresh-data` → `update-portfolio` → `send-newsletter`,
mais o `alert-on-failure`, que abre um issue quando algum deles falha.

---

## Regra que está acima de todas as outras

**Antes de voltar a correr o job da newsletter, descobrir se a edição já saiu.**

```
cat sent_issues.json
```

Se houver uma entrada com o `issue` desta semana, **a edição já foi enviada a
alguém**. O que fazer a seguir depende do campo `complete`.

| `complete` | Significa | O que fazer |
|---|---|---|
| ausente ou `true` | Toda a gente recebeu | Nada. Re-correr o job não faz mal (ele reconhece a marca e sai), mas também não faz nada. |
| `false` | Entrega **incompleta** | Re-correr o job da newsletter. Ele serve **apenas** quem falta, reutilizando a edição já publicada. Ninguém recebe duas vezes. |

Se o ficheiro não existir ou estiver vazio (`{"sent": []}`), a edição desta
semana ainda não saiu e o caminho normal aplica-se.

---

## Caso 1 — o log diz `A edicao foi enviada, mas:`

O envio correu. O que falhou foi o estado que tinha de ficar no repositório.
A mensagem diz qual, e há três variantes:

### 1a. `a marca de envio NAO foi publicada no repositorio`

**Era o mais perigoso. Desde Set 2026, o sistema recupera sozinho.**

A edição saiu, mas o `sent_issues.json` ficou só no runner, que já foi destruído.
Antigamente, a corrida seguinte não via marca nenhuma, gerava uma edição nova e
**reenviava a toda a gente**.

Agora há uma segunda testemunha de quem recebeu, e não depende de um `git push`:
cada mensagem vai etiquetada na Brevo com `mrm-issue-<N>`. Quando a edição da
semana **já está publicada no repositório** mas não tem marca, a corrida pergunta
à Brevo quem já a recebeu, reconstrói a marca, grava-a, e serve **apenas quem
falta**. Ninguém recebe duas vezes.

A pergunta só se faz quando há motivo para desconfiar — a página publicada. Numa
semana normal (primeira passagem, sem ficheiro publicado) não se pergunta nada, e
uma avaria da API de estatísticas da Brevo não impede a newsletter de sair.

**O que ainda precisa de uma pessoa:** se a Brevo não responder *e* a edição
estiver publicada sem marca, a corrida **pára** com um erro que aponta para aqui.
É deliberado: nesse estado ninguém sabe se a edição saiu, e reenviar custa mais do
que esperar. O que fazer:

1. Abrir o painel da Brevo → *Transactional* → *Logs*, e filtrar pela etiqueta
   `mrm-issue-N`. Isso diz quem recebeu.
2. Se ninguém recebeu, re-correr o workflow normalmente.
3. Se alguém recebeu e a Brevo continuar indisponível, criar a entrada à mão no
   `sent_issues.json` de `main`, como descrito abaixo.

   ```json
   {"sent": [{"issue": N, "recipients": X, "sentAt": "2026-09-11T22:41:00Z",
              "served": ["<marcas>"], "complete": true}]}
   ```

   **Nunca escrever endereços de e-mail neste ficheiro.** Ele é commitado para
   `main` e a raiz do ramo é servida em usmrm.net: um endereço aqui fica público
   e fica no histórico do git para sempre. O campo `served` leva *marcas* —
   digests de 16 caracteres — e é isso que está no log da marca escrita antes de
   o push falhar (procurar `sent_issues.json` no log e copiar os digests tal e
   qual). Se não estiverem lá, pôr só `complete: true` e a contagem: impede o
reenvio, que é o que interessa.

   Se for mesmo preciso calcular uma marca a partir de um endereço:
   `python3 -c "import send_newsletter as s; print(s.marca_destinatario('a@b.pt'))"`.
3. Commitar e empurrar para `main`.
4. Só então voltar a correr o workflow, se for preciso.

### 1b. `o data_prev.json NAO rodou`

Menos grave e não urgente. A edição saiu correcta; a da **próxima** semana é que
sairia com todos os "vs. semana anterior" a zero.

```
cp data.json data_prev.json
git add data_prev.json && git commit -m "chore: rotate data_prev after issue #N"
git push
```

Fazer isto antes da sexta seguinte. Não é preciso re-correr nada.

### 1c. `a edicao #N ficou INCOMPLETA`

Ver o Caso 2.

---

## Caso 2 — entrega incompleta

O log diz `Issue #N: X enviados, Y falhados, Z por servir`, e a marca tem
`complete: false`.

Isto acontece quando a Brevo recusa mensagens (rate-limit, endereço inválido, uma
avaria do lado deles) ou quando o orçamento de tempo do envio
(`SEND_BUDGET_SECONDS`, 8 minutos) se esgota.

**O que fazer:** voltar a correr o workflow `🗓️ Friday Pipeline` com
`workflow_dispatch`. Não é preciso mais nada, e não há risco de duplicados:

- quem já recebeu está em `served` e é saltado;
- a edição já publicada é **reutilizada** (procurada pelo número, não pela data),
  para que quem falta receba exactamente o mesmo texto;
- se o ficheiro da edição não estiver no checkout, o job **pára com erro** em vez
  de gerar uma edição nova.

Se falhar outra vez com os mesmos endereços, o problema é a lista: verificar
esses contactos na Brevo. Uma edição pode ficar incompleta indefinidamente sem
prejuízo para as outras semanas.

---

## Caso 3 — falhou antes de enviar

Qualquer falha nos jobs `refresh-data` ou `update-portfolio`, ou no
`send-newsletter` antes da linha `Sending to N subscribers`.

Ninguém recebeu nada. Re-correr o workflow normalmente. O motor é idempotente:
uma re-corrida sobre os mesmos dados toma a **mesma** decisão, não duplica a
entrada do histórico, e não reabre a porta FTQ.

`force_rebalance` **não** é preciso para isto e não deve ser usado por rotina:
serve só para ignorar a guarda de data, e a guarda de data existe para impedir
que uma corrida fora de horas decida a semana outra vez.

---

## Caso 4 — `MarcaIlegivel`

O job para logo no início com `sent_issues.json existe mas nao se consegue ler`.

Um push interrompido deixou o ficheiro truncado, ou um merge mal resolvido deixou
marcadores de conflito. **Nada foi enviado** — o job recusa-se a decidir sobre um
registo que não sabe ler.

Corrigir o ficheiro à mão (é um JSON com uma chave `sent` que é uma lista),
commitar, e voltar a correr. Se o conteúdo for irrecuperável, reconstruir com as
edições que se sabe terem saído — em dúvida, marcar como enviadas: uma edição
que não sai é um incómodo, uma edição enviada duas vezes é um problema com os
subscritores.

---

## Quando a DGS10 falha — o sub-regime dentro de Critical

A `DGS10` alimenta **duas** coisas, e só uma delas é óbvia. A primeira é o E/P
do pilar Premium. A segunda é a **janela de 3 meses do 10Y**, que escolhe, já
dentro de Critical, entre `Critical_FTQ` (35% em TLT) e `Critical_Stress` (20%
em SHY). A diferença entre os dois vectores é mais de um terço da carteira.

Essa janela é agora publicada como o terceiro gatilho do medidor B,
`stressGauge.triggers.tenY3m`, ao lado do Sahm e da delinquência — mas com
`decides: "subregime"`, porque **não decide Critical**: decide qual dos dois
vectores de Critical se executa.

Com a janela por medir (a série não responde, ou traz menos de 55 pontos):

- o medidor devolve `subregime: null` — declara a ausência, não a substitui por
  um valor defensivo;
- **o sub-regime em vigor mantém-se**. Quem está em FTQ fica em FTQ; quem está
  em Stress fica em Stress. Não há transacção nenhuma por falta de leitura, pela
  mesma razão por que não há quando o medidor inteiro fica sem dados;
- a **entrada fresca** em Critical continua a ir para `Critical_Stress`: aí não
  há nada para manter, e o TLT teria de ser conquistado por uma medição que não
  houve;
- o site e a edição dizem-no, em vez de escreverem o resultado de um filtro que
  não correu.

Nada a fazer à mão. Se a `DGS10` ficar em falta várias semanas seguidas, o
sub-regime congela — e isso vê-se no aviso de qualidade de dados da edição.

---

## Manutenção trimestral — a âncora dos earnings

`SP500_EARNINGS_YIELD` e `SP500_EARNINGS_YIELD_ASOF`, no `fetch_data.py`, são o
único input posto à mão. O E/P publicado são *esses earnings divididos pelo
fecho de hoje*: o preço move-se todos os dias, os earnings ficam onde estão até
alguém os actualizar.

Há dois prazos, e ambos aparecem no site e na newsletter:

- **passados 100 dias** (`EP_STALE_AFTER_DAYS`) a referência declara-se velha e a
  edição leva um aviso de qualidade de dados;
- **passados 180 dias** (`EP_ND_AFTER_DAYS`) a leitura deixa de contar como
  leitura: o pilar Premium entra em `n/d`, sai do composto, e os pesos dos
  restantes renormalizam. O ERP, a banda e a sentinela do ERP passam também a
  `n/d` — não se publica como medição um número que ninguém verificou.

**Actualizar a âncora mexe no composto, e o composto decide a carteira.** Com o
Premium fora, o composto é a média dos outros quatro; ao repor a referência, o
Premium volta e o composto muda de novo. Uma entrada ou saída de regime pode
seguir-se — é a decisão certa sobre a informação certa, mas não é uma alteração
cosmética. Por isso:

- actualizar de preferência **logo a seguir a uma sexta**, para o pipeline correr
  com o valor novo já assente e com uma semana inteira antes da decisão seguinte;
- **enquanto houver um pilar em `n/d` o score não decide o regime, em nenhuma
  direcção**: não se entra em Resilient nem se sai dele, e não há transacção
  nenhuma por causa da renormalização. O medidor B continua a decidir Critical
  sozinho, porque não passa pelo score. Isto quer dizer que, com a referência
  vencida, o regime Resilient fica **inalcançável até ela ser actualizada** — é
  o preço de não negociar sobre um composto incompleto, e é mais uma razão para
  não deixar a referência apodrecer;
- o valor a pôr é o E/P agregado do S&P 500 na data que se escrever no `ASOF` —
  não o de hoje com a data de hoje se os earnings forem de outro trimestre.

---

## O que NÃO fazer, nunca

- **Apagar a entrada do `sent_issues.json` para "forçar o reenvio".** Reenvia a
  quem já recebeu. Se for mesmo preciso reenviar (uma edição com um erro grave),
  fazê-lo pela Brevo, à mão, para a lista certa.
- **Correr o `send_newsletter.py` localmente contra as chaves de produção.** Envia
  a sério, e a marca fica no computador em vez de no repositório.
- **Usar `git add -u`.** Só leva ficheiros já seguidos; um módulo novo fica de
  fora e os três jobs morrem no import. Há um teste que verifica isto
  (`tests/test_workflows.py`), mas ele só corre depois de o commit existir.

---

## Verificações antes da primeira sexta-feira

- [ ] Todos os ficheiros commitados em `main` (`git status --short` limpo).
- [ ] Segredos no repositório: `FRED_API_KEY`, `ANTHROPIC_API_KEY`,
      `BREVO_API_KEY`. O `ALERT_WEBHOOK` é opcional.
- [ ] Correr o workflow `✅ Tests` à mão (`workflow_dispatch`): é o único que
      corre o `actionlint`, e uma expressão inválida num `if:` faz o GitHub
      rejeitar o ficheiro inteiro sem sequer arrancar.
- [ ] `sent_issues.json` existe e tem `{"sent": []}`.
