/**
 * Testes do JavaScript do index.html, sem browser.
 *
 * Este ficheiro existe por uma razão concreta: metade da lógica de apresentação
 * vive no index.html e não tinha teste NENHUM. As "verificações" que existiam
 * eram `grep` ao código-fonte a partir do Python — que passavam com o defeito
 * presente, e que numa auditoria foram demonstradas inúteis: trocar a lista de
 * instrumentos do dashboard de volta para a versão escrita à mão não fazia
 * falhar um único teste.
 *
 * Dois defeitos reais que só um teste destes apanha:
 *   - o dashboard mostrava ~30% da carteira em Critical, porque a lista de
 *     instrumentos era o mapa de Turbulence escrito à mão;
 *   - a legenda do donut ficou com as cores desalinhadas das fatias, porque
 *     eram duas fontes diferentes para a mesma cor.
 *
 * Corre em Node puro (sem Playwright, sem rede): extrai o bloco de script do
 * index.html e avalia-o contra um DOM mínimo. As chamadas de topo (loadData,
 * setInterval, …) são neutralizadas pelos stubs.
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const HTML = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');

// Se este ficheiro terminar sem chegar ao fim — uma promessa pendurada faz o
// Node esvaziar o event loop e sair em silêncio com código 0 — o CI leria isso
// como sucesso. O guarda abaixo transforma "não chegou ao fim" numa falha.
let terminou = false;
process.on('exit', (code) => {
  if (!terminou && code === 0) {
    console.error('FALHOU: os testes terminaram sem chegar ao fim '
                  + '(promessa pendurada ou saída prematura)');
    process.exitCode = 1;
  }
});

let ok = 0;
function eq(got, want, what) {
  const a = JSON.stringify(got), b = JSON.stringify(want);
  if (a !== b) throw new Error(`${what}: esperado ${b}, obtido ${a}`);
  ok++;
}
function truthy(cond, what) { eq(Boolean(cond), true, what); }

// ── DOM mínimo ──────────────────────────────────────────────────────────────
function elemento(id) {
  const el = {
    id, textContent: '', style: {}, dataset: {}, cells: [], rows: [],
    classList: { _s: new Set(), add(...c){c.forEach(x=>this._s.add(x));},
                 remove(...c){c.forEach(x=>this._s.delete(x));},
                 contains(c){return this._s.has(c);} },
    // `appendChild` era um no-op: tudo o que a página monta com
    // `createElement` + `appendChild` — o painel do Medidor B, entre outros —
    // ficava invisível aos testes, e podia publicar o que quisesse com a suite
    // verde. Guarda-se o filho, como o browser faz, para se poder ler o que a
    // página realmente mostra.
    _kids: [],
    appendChild(c){ if (c) this._kids.push(c); return c; },
    insertAdjacentHTML(){}, addEventListener(){},
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    getContext(){ return { canvas: {}, clearRect(){}, beginPath(){}, arc(){}, fill(){},
                           stroke(){}, moveTo(){}, lineTo(){}, fillText(){}, measureText(){return{width:0};},
                           setTransform(){}, scale(){}, save(){}, restore(){}, closePath(){}, fillRect(){} }; },
    getBoundingClientRect(){ return { width: 600, height: 300, top: 0, left: 0 }; },
    offsetWidth: 600, offsetHeight: 300,
  };
  // `innerHTML` RECRIA a subárvore, como no browser: os ids que o novo HTML
  // declara deixam de ser os elementos que estavam antes. Sem isto, uma escrita
  // feita ANTES de um `innerHTML` sobrevivia no dicionário para sempre e o teste
  // via um valor que a página real nunca mostra — foi assim que o "Pillars
  // Active" derivado passou nos testes enquanto o site publicava "— / —".
  let _html = '';
  Object.defineProperty(el, 'innerHTML', {
    get() { return _html; },
    set(v) {
      _html = String(v == null ? '' : v);
      el._kids = [];           // o browser também deita fora a subárvore antiga
      for (const m of _html.matchAll(/\bid="([^"]+)"/g)) {
        if (m[1] !== el.id) delete elems[m[1]];
      }
    },
    enumerable: true, configurable: true,
  });
  return el;
}

// O HTML que um elemento mostra: o que lhe foi ATRIBUÍDO mais o dos filhos que
// lhe foram anexados. Sem a segunda metade, tudo o que a página monta por
// `createElement` + `appendChild` era invisível ao teste.
function htmlVisivel(el) {
  if (!el) return '';
  const proprio = el.innerHTML || '';
  const filhos = (el._kids || []).map(k => htmlVisivel(k)).join('');
  return proprio + filhos;
}

// O texto que o HTML atribuído dá a um id — é o que o browser mostraria. Os
// elementos criados por `innerHTML` não existem no dicionário plano; esta é a
// única forma honesta de ler o que a página publica.
function textoDeIdNoHTML(html, id) {
  const re = new RegExp(`<[^>]*\\bid="${id}"[^>]*>([\\s\\S]*?)</`, 'i');
  const m = String(html || '').match(re);
  return m ? m[1].replace(/<[^>]*>/g, '').trim() : null;
}
// Os ids que existem MESMO no index.html. Sem isto, `getElementById` devolvia
// um elemento para qualquer id, e renomear um id no corpo do HTML — deixando o
// JS a apontar para o antigo — passava nos testes com a página real em branco.
const IDS_REAIS = new Set([...HTML.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));
truthy(IDS_REAIS.size > 50, `o index.html declara ids (${IDS_REAIS.size} encontrados)`);

const elems = {};
const doc = {
  getElementById: (id) => {
    if (!IDS_REAIS.has(id)) return null;      // como no browser
    return elems[id] || (elems[id] = elemento(id));
  },
  querySelector: () => elemento('q'),
  querySelectorAll: () => [],
  createElement: (t) => elemento(t),
  addEventListener(){}, body: elemento('body'),
  documentElement: elemento('html'),
};

const sandbox = {
  document: doc, console,
  window: { addEventListener(){}, matchMedia: () => ({ matches: false, addEventListener(){} }),
            devicePixelRatio: 1, innerWidth: 1200, scrollY: 0 },
  // A página chama loadData() ao carregar. Devolve-se uma promessa que nunca
  // resolve, em vez de rejeitar: uma rejeição não apanhada mata o processo de
  // Node e não é isso que está a ser testado aqui.
  fetch: () => new Promise(() => {}),
  setInterval: () => 0, setTimeout: () => 0, clearInterval(){}, clearTimeout(){},
  requestAnimationFrame: () => 0,
  Chart: function () { return { destroy(){}, data: {}, update(){} }; },
  getComputedStyle: () => ({ getPropertyValue: () => '#000000' }),
  Date, Math, JSON, Object, Array, String, Number, Boolean, parseFloat, parseInt,
  isNaN, isFinite, Set, Map, Intl, RegExp, Error, encodeURIComponent, decodeURIComponent,
};

const blocos = [...HTML.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
eq(blocos.length, 2, 'o index.html tem dois blocos de script');

const vm = require('vm');
const ctx = vm.createContext(sandbox);
vm.runInContext(blocos[0], ctx, { filename: 'index.html <script>' });

const F = (nome) => {
  const f = vm.runInContext(`typeof ${nome} === 'function' ? ${nome} : null`, ctx);
  truthy(f, `a função ${nome} existe`);
  return f;
};

// ── heldTickers: o dashboard desenha o que a carteira DETÉM ─────────────────
const heldTickers = F('heldTickers');

const critical = {
  regime: 'Critical', critical_subregime: 'Critical_Stress',
  shares: { USMV: 16.5, SHY: 24.1, SGOV: 19.7, GLD: 5.9, BIL: 27.0, VNQ: 5.6 },
  allocation_pct: { USMV: 15, SHY: 20, SGOV: 20, GLD: 15, BIL: 25, VNQ: 5 },
  active_etf_map: { US_EQUITIES:'USMV', US_TREASURIES:'SHY', IG_CREDIT:'SGOV',
                    COMMODITIES:'GLD', CASH:'BIL', ALTERNATIVES:'VNQ' },
  last_prices: { USMV: 90, SHY: 82, SGOV: 100.5, GLD: 250, BIL: 91.5, VNQ: 88 },
};
const emCritical = heldTickers(critical);
eq([...emCritical].sort(), ['BIL','GLD','SGOV','SHY','USMV','VNQ'],
   'em Critical o dashboard lista os instrumentos de Critical, não os de Turbulence');
truthy(!emCritical.includes('SPY'),
   'e NÃO lista o SPY, que a carteira não detém');
eq(emCritical[0], 'BIL', 'a ordem é pela alocação, do maior para o menor');

const resilient = {
  shares: { QQQ: 10, SHY: 5, HYG: 15, PDBC: 5, BIL: 5, IWO: 15 },
  allocation_pct: { QQQ: 55, SHY: 5, HYG: 15, PDBC: 5, BIL: 5, IWO: 15 },
  active_etf_map: { US_EQUITIES:'QQQ', US_TREASURIES:'SHY', IG_CREDIT:'HYG',
                    COMMODITIES:'PDBC', CASH:'BIL', ALTERNATIVES:'IWO' },
};
eq(heldTickers(resilient)[0], 'QQQ', 'em Resilient o maior peso é o QQQ');
eq([...heldTickers(resilient)].sort(), ['BIL','HYG','IWO','PDBC','QQQ','SHY'],
   'e o conjunto é o do mapa Resilient');

// robustez: campos em falta não podem rebentar a página
eq(heldTickers({}), [], 'um current vazio não rebenta e não inventa instrumentos');
eq([...heldTickers({ shares: { SPY: 1 } })], ['SPY'], 'sem active_etf_map, usa o que detém');
const semAlloc = heldTickers({ active_etf_map: critical.active_etf_map });
eq([...semAlloc].sort(), ['BIL','GLD','SGOV','SHY','USMV','VNQ'],
   'sem allocation_pct, cai no mapa declarado');
// Determinismo a sério: a mesma carteira com as chaves por outra ordem tem de
// dar a mesma lista. Chamar duas vezes a mesma função pura não podia falhar.
const baralhado = { ...critical,
  shares: Object.fromEntries(Object.entries(critical.shares).reverse()),
  allocation_pct: Object.fromEntries(Object.entries(critical.allocation_pct).reverse()) };
eq(heldTickers(baralhado), heldTickers(critical),
   'a ordem não depende da ordem das chaves no JSON');

// ── renderPortfolioHoldings: o HTML desenhado, não só a função auxiliar ────
// Testar `heldTickers` isolada não chega: o defeito original estava na CHAMADA,
// e uma mutação que reponha a lista escrita à mão dentro do render passa por
// cima de um teste que só exercita a auxiliar.
const renderHoldings = F('renderPortfolioHoldings');
const renderDonut    = F('renderPortfolioDonut');

renderHoldings({ current: critical });
const htmlCritical = elems['port-holdings-body'].innerHTML;
for (const t of ['USMV','SHY','SGOV','GLD','BIL','VNQ']) {
  truthy(htmlCritical.includes(`>${t}<`),
    `a tabela desenhada em Critical mostra o ${t}`);
}
for (const t of ['SPY','IEF','LQD','PDBC']) {
  truthy(!htmlCritical.includes(`>${t}<`),
    `e NÃO mostra o ${t}, que a carteira não detém em Critical`);
}
// a soma dos valores desenhados tem de bater com a carteira
// A coluna do VALOR é a última de cada linha (a anterior é o preço unitário).
const somaDesenhada = htmlCritical.split('<tr>').slice(1).reduce((total, linha) => {
  const cifras = [...linha.matchAll(/\$([\d,]+\.\d{2})/g)].map(m => parseFloat(m[1].replace(/,/g, '')));
  return total + (cifras.length ? cifras[cifras.length - 1] : 0);
}, 0);
const somaReal = Object.entries(critical.shares)
  .reduce((a, [t, n]) => a + n * critical.last_prices[t], 0);
truthy(Math.abs(somaDesenhada - somaReal) < 1,
  `a soma dos valores desenhados (${somaDesenhada.toFixed(2)}) corresponde à carteira ` +
  `(${somaReal.toFixed(2)}) — antes desenhava ~30% dela`);

renderHoldings({ current: resilient });
const htmlResilient = elems['port-holdings-body'].innerHTML;
for (const t of ['QQQ','HYG','IWO']) {
  truthy(htmlResilient.includes(`>${t}<`), `em Resilient a tabela mostra o ${t}`);
}
truthy(!htmlResilient.includes('>SPY<'), 'e não mostra o SPY em Resilient');

// o donut usa o mesmo conjunto que a tabela
// ── ETF_META cobre todos os instrumentos de todos os mapas ─────────────────
const meta = vm.runInContext('ETF_META', ctx);
for (const t of ['SPY','IEF','LQD','PDBC','BIL','VNQ','USMV','SHY','TLT','SGOV','GLD','QQQ','HYG','IWO']) {
  truthy(meta[t] && meta[t].name && meta[t].color,
         `ETF_META conhece o ${t} (nome e cor), que aparece em pelo menos um mapa`);
}
const cores = Object.values(meta).map(m => m.color);
eq(new Set(cores).size, cores.length, 'nenhuma cor está repetida entre instrumentos');

// ── a legenda do donut usa a MESMA fonte de cor que as fatias ──────────────
// A coerência legenda↔fatias é o motivo declarado deste ficheiro, por isso é
// testada como COMPORTAMENTO: desenha-se o donut, lê-se a cor de cada fatia e a
// cor de cada chip, e comparam-se. A versão anterior fazia grep ao código-fonte
// — e pintar a legenda inteira de preto passava nos testes.
let cfgDonut = null;
vm.runInContext(
  'Chart = function (canvas, cfg) { globalThis.__cfg = cfg; return { destroy(){} }; };' +
  'window.Chart = Chart;', ctx);
renderDonut({ current: critical });
cfgDonut = vm.runInContext('globalThis.__cfg', ctx);
truthy(cfgDonut && cfgDonut.data, 'o donut foi construído');

const coresFatia = {};
cfgDonut.data.labels.forEach((t, i) => { coresFatia[t] = cfgDonut.data.datasets[0].backgroundColor[i]; });
const htmlLegenda = elems['port-donut-legend'].innerHTML;
truthy(htmlLegenda.length > 0, 'a legenda foi desenhada');
for (const [t, cor] of Object.entries(coresFatia)) {
  const linha = htmlLegenda.split('</div></div>').find(s => s.includes(`>${t}<`));
  truthy(linha, `a legenda tem um chip para o ${t}`);
  truthy(linha.includes(`background:${cor}`),
    `o chip do ${t} tem a MESMA cor da sua fatia (${cor}) — estavam desalinhados`);
}
truthy(!htmlLegenda.includes('background:#000000'), 'nenhum chip está a preto');

// ── fmtTrigger: os motivos que o motor emite hoje ──────────────────────────
vm.runInContext('RULES = { resilientMax: 4.0, criticalMin: 8.0 };', ctx);
const fmtTrigger = F('fmtTrigger');
const esperados = {
  'stress_on': 'Critical map', 'stress_off': 'Turbulence',
  'stress_off_to_resilient': 'Resilient', 'resilient_off': 'Resilient',
  'semestral_rebalance': 'semi-annual', 'emergency_resilient_3.8': 'Resilient',
  'critical_subregime_switch:a->b': 'Sub-regime', 'missing_prices_held': 'price',
  'no_allocation_available': 'allocation', 'aborted_invalid_shares': 'value check',
};
for (const [motivo, fragmento] of Object.entries(esperados)) {
  const txt = fmtTrigger(motivo);
  truthy(txt.includes(fragmento),
    `fmtTrigger(${motivo}) fala do que aconteceu (esperava "${fragmento}", deu "${txt}")`);
  truthy(!/^⟳ [a-z_ ]+$/.test(txt) || motivo.includes(':'),
    `fmtTrigger(${motivo}) não cai no genérico`);
}
eq(fmtTrigger('hold'), 'Hold — no rebalance', 'sem gatilho, diz hold');
eq(fmtTrigger(null), 'Hold — no rebalance', 'e sem motivo também');
// O limiar tem de vir das RULES. Testar com 4.0 não provava nada, porque 4.0 é
// também o valor escrito à mão no fallback: move-se o limiar.
vm.runInContext('RULES.resilientMax = 3.5;', ctx);
truthy(fmtTrigger('emergency_resilient_3.4').includes('3.5'),
   'o limiar publicado vem das RULES, não do fallback escrito à mão');
vm.runInContext('RULES.resilientMax = 4.0;', ctx);
// Vocabulário morto: o fmtTrigger não pode ter ramos próprios para motivos que
// o motor não emite. Verificar contra o literal do próprio teste não provava
// nada — verifica-se contra o comportamento da função.
// O genérico leva ⚠ e não ⟳: um motivo que este vocabulário não conhece é mais
// provavelmente um modo de falha novo do que um rebalanceamento bem sucedido, e
// desenhá-lo como executado é o erro mais caro dos dois.
for (const morto of ['quarterly_rebalance', 'tactical_wow_delta', 'wow_delta_spy']) {
  eq(fmtTrigger(morto), '⚠ ' + morto.replace(/_/g, ' '),
     `${morto} cai no genérico — não tem ramo próprio, porque o motor não o emite`);
}

// ── logReason delega no fmtTrigger, não duplica o vocabulário ──────────────
const logReason = F('logReason');
eq(logReason({ rebalance_triggered: false, rebalance_reason: 'hold' }), '— hold',
   'uma semana sem transacções di-lo');
truthy(logReason({ rebalance_triggered: false, rebalance_reason: 'missing_prices_held' })
         .includes('price'),
   'e uma retenção por falta de preço explica-se, em vez de dizer só "hold"');
truthy(logReason({ rebalance_triggered: true, rebalance_reason: 'stress_on' })
         === fmtTrigger('stress_on'),
   'os motivos normais delegam no fmtTrigger — uma fonte só');
truthy(logReason({ rebalance_triggered: true,
                   rebalance_reason: 'critical_subregime_switch:Critical_Stress->Critical_FTQ' })
         .includes('Flight to Quality'),
   'a troca de sub-regime traduz o destino');

// ── bandTone lê os limiares publicados, não escritos à mão ─────────────────
const bandTone = F('bandTone');
vm.runInContext("RULES.pillarStatusBands = [[3.0,'stable'],[5.0,'caution'],[6.0,'warning']];", ctx);
eq(bandTone(2.5), 'green', 'bandTone segue as bandas publicadas (limiar movido para 3.0)');
eq(bandTone(4.0), 'orange', 'e não os 4.0/7.5 que estavam escritos à mão');
eq(bandTone(9.0), 'red', 'acima da última banda é vermelho');

// ── Os formatadores: um valor ausente é "n/d", nunca "NaN" ────────────────
// Esta secção existe porque o motor suprime o P&L quando não consegue valorizar
// a carteira toda — e o site publicava `parseFloat(null).toFixed(2)` = "NaN%",
// a vermelho, ao lado do valor. Nenhum teste tocava nestas funções.
const fmtPct = F('fmtPct'), fmtUSD = F('fmtUSD'), portColor = F('portColor');
for (const ausente of [null, undefined, '', 'n/d', NaN]) {
  eq(fmtPct(ausente), 'n/d', `fmtPct(${JSON.stringify(ausente)}) é "n/d", não "NaN%"`);
  eq(fmtUSD(ausente), 'n/d', `fmtUSD(${JSON.stringify(ausente)}) é "n/d", não "$NaN"`);
  truthy(!portColor(ausente).includes('red'),
    `um valor ausente não é pintado de vermelho como se fosse uma perda`);
}
eq(fmtPct(6.18), '+6.18%', 'um valor positivo leva sinal');
eq(fmtPct(-1.04), '-1.04%', 'e um negativo também');
eq(fmtPct(0), '+0.00%', 'zero conta como não-negativo');
truthy(fmtUSD(10617.97).startsWith('$10,617.97'), 'o valor leva separador de milhares');
truthy(portColor(1).includes('green') && portColor(-1).includes('red'),
  'as cores de ganho e perda mantêm-se');

// ── renderPortfolioKPIs: o P&L suprimido chega ao ecrã como n/d ───────────
const renderKPIs = F('renderPortfolioKPIs');
const semPnl = {
  current: { ...critical, portfolio_value: 10617.97,
             portfolio_pnl_pct: null, alpha_vs_benchmark_pct: null, date: '2026-09-11' },
  history: [{ issue: 27, regime: 'Critical', mrm_score: 6.97,
              rebalance_triggered: false, rebalance_reason: 'valuation_incomplete_held' }],
};
renderKPIs(semPnl);
for (const id of ['port-kpi-pnl', 'port-kpi-alpha']) {
  const el = elems[id];
  truthy(el, `o KPI ${id} existe no HTML`);
  truthy(!String(el.textContent).includes('NaN'),
    `o KPI ${id} não publica "NaN" quando o motor suprimiu o valor (deu "${el.textContent}")`);
  truthy(String(el.textContent).includes('n/d'),
    `o KPI ${id} diz "n/d" (deu "${el.textContent}")`);
  truthy(!String(el.style.color || '').includes('red'),
    `e não o pinta de vermelho como se fosse uma perda`);
}
// Antes desta versão passava por acidente: `valuation_incomplete_held` não
// tinha ramo no fmtTrigger e caía no genérico, que mostrava o motivo cru — com
// a palavra "held" lá dentro por ser o nome da chave, não por ser uma
// explicação. Agora tem texto próprio, e o teste pede a explicação.
eq(String(elems['port-kpi-rebalance'].textContent),
   '⚠ Held — portfolio value incomplete',
   'o KPI do rebalanceamento explica porque não houve transacções');

// com valores normais, os KPI mostram-nos
renderKPIs({ current: { ...critical, portfolio_value: 10617.97,
                        portfolio_pnl_pct: 6.18, alpha_vs_benchmark_pct: -10.16 },
             history: [{ issue: 27, regime: 'Critical', mrm_score: 6.97,
                         rebalance_triggered: true, rebalance_reason: 'stress_on' }] });
truthy(String(elems['port-kpi-pnl'].textContent).includes('+6.18'), 'com dados, o P&L aparece');
truthy(String(elems['port-kpi-alpha'].textContent).includes('-10.16'), 'e o alpha também');
truthy(String(elems['port-kpi-rebalance'].textContent).includes('Critical map'),
  'e o motivo do rebalanceamento é o que aconteceu');

// ── A qualidade da valorização chega ao ECRÃ, não só ao ficheiro ─────────
//
// O motor declara — e a newsletter da mesma semana publica por escrito — que a
// valorização de um instrumento com o preço congelado além do limite já não é
// credível. Esta página desenhava o mesmo valor, o mesmo P&L e o mesmo alpha
// como números sãos, e por baixo "Updated: <data da corrida>". Um estado novo
// que o consumidor a jusante não conhece é uma falsidade publicada.
{
  const base = { ...critical, portfolio_value: 10617.97, portfolio_pnl_pct: 6.18,
                 alpha_vs_benchmark_pct: -10.16, date: '2026-09-11' };
  const hist = [{ issue: 27, regime: 'Critical', mrm_score: 6.97,
                  rebalance_triggered: false, rebalance_reason: 'hold' }];
  // 1) Sem avaria nenhuma: nada de nota, e os KPIs sem véu.
  renderKPIs({ current: { ...base }, history: hist });
  truthy(elems['port-valuation-note'].hidden,
    'sem avaria de preços, não há nota de valorização');
  truthy(!String(elems['port-kpi-value'].style.opacity || '').startsWith('0.'),
    'e o KPI do valor não está esbatido');
  // 2) Preço congelado dentro do limite: aproximação declarada.
  renderKPIs({ current: { ...base, valuation_frozen: ['TLT'],
                          valuation_frozen_days: { TLT: 14 },
                          valuation_not_credible: [],
                          price_frozen_after_days: 21 }, history: hist });
  truthy(!elems['port-valuation-note'].hidden &&
         String(elems['port-valuation-note'].textContent).includes('TLT'),
    `um preço congelado é declarado no ecrã ("${elems['port-valuation-note'].textContent}")`);
  truthy(!String(elems['port-valuation-note'].textContent).includes('not credible'),
    'mas dentro do limite não se chama não-credível');
  // 3) Congelado ALÉM do limite: o veredicto e o número de dias.
  renderKPIs({ current: { ...base, valuation_frozen: ['TLT'],
                          valuation_frozen_days: { TLT: 300 },
                          valuation_not_credible: ['TLT'],
                          price_frozen_after_days: 21 }, history: hist });
  const nota = String(elems['port-valuation-note'].textContent);
  truthy(nota.includes('not credible'),
    `além do limite, o ecrã diz que a valorização não é credível ("${nota}")`);
  truthy(nota.includes('300'), `e há quantos dias ("${nota}")`);
  truthy(nota.includes('21'), `e contra que limite ("${nota}")`);
  for (const id of ['port-kpi-value', 'port-kpi-pnl', 'port-kpi-alpha']) {
    truthy(String(elems[id].style.opacity || '').startsWith('0.'),
      `e o KPI ${id} deixa de se apresentar como um número são`);
  }
  // 4) E a LINHA do instrumento leva a marca: a tabela desenhava o preço morto
  //    exactamente como um preço de hoje.
  const renderHold = F('renderPortfolioHoldings');
  renderHold({ current: { ...base, shares: { TLT: 10 }, allocation_pct: { TLT: 100 },
                          last_prices: { TLT: 95.0 },
                          valuation_frozen: ['TLT'],
                          valuation_frozen_days: { TLT: 300 },
                          valuation_not_credible: ['TLT'],
                          price_frozen_after_days: 21 } });
  const linhas = elems['port-holdings-body'].innerHTML;
  truthy(linhas.includes('300d'),
    `a linha do TLT diz há quantos dias o preço está parado (${linhas.slice(0, 400)})`);
  truthy(linhas.includes('⚠'),
    'e leva a marca de aviso, não o preço desenhado como um preço de hoje');
  // 5) E um instrumento SÃO não leva marca nenhuma: uma marca que aparece
  //    sempre não distingue nada.
  renderHold({ current: { ...base, shares: { TLT: 10 }, allocation_pct: { TLT: 100 },
                          last_prices: { TLT: 95.0 }, valuation_frozen: [],
                          valuation_frozen_days: {}, valuation_not_credible: [] } });
  truthy(!elems['port-holdings-body'].innerHTML.includes('⚠'),
    'e um preço fresco não leva marca nenhuma');
}

// ── renderRebalanceLog: a tabela de histórico não publica NaN ─────────────
const renderLog = F('renderRebalanceLog');
renderLog({ history: [
  { issue: 26, date: '2026-09-04', regime: 'Turbulence', mrm_score: 6.9,
    rebalance_triggered: false, rebalance_reason: 'hold',
    portfolio_value: 10500.0, portfolio_pnl_pct: 5.0 },
  { issue: 27, date: '2026-09-11', regime: 'Critical', mrm_score: 6.97,
    rebalance_triggered: false, rebalance_reason: 'valuation_incomplete_held',
    portfolio_value: 10617.97, portfolio_pnl_pct: null },
]});
const htmlLog = elems['port-log-body'].innerHTML;
truthy(!htmlLog.includes('NaN'), 'a tabela de histórico não publica NaN em lado nenhum');
truthy(htmlLog.includes('n/d'), 'publica "n/d" onde o P&L foi suprimido');
truthy(htmlLog.includes('#26') && htmlLog.includes('#27'), 'e mostra as duas edições');

// ── portfolioMapLine: a posição vem do portfolio.json, não do medidor ──────
// O site afirmava "Portfolio on the Critical map" a partir do data.stressGauge.
// As duas coisas divergem legitimamente — medidor em n/d, rebalanceamento
// cancelado, janela de confirmação do Resilient — e nessas semanas a página
// dizia ao leitor que a carteira estava onde não estava.
const mapLine = F('portfolioMapLine');
eq(mapLine({ regime: 'Turbulence' }), 'Portfolio on the Turbulence map.',
   'em Turbulence diz Turbulence');
eq(mapLine({ regime: 'Critical', critical_subregime: 'Critical_FTQ' }),
   'Portfolio on the Critical_FTQ map.', 'em Critical diz o sub-regime detido');
eq(mapLine({ regime: 'Critical' }), 'Portfolio on the Critical map.',
   'em Critical sem sub-regime declarado, não inventa um');
eq(mapLine({ regime: 'Resilient' }), 'Portfolio on the Resilient map.',
   'em Resilient diz Resilient');
truthy(mapLine(null).includes('not available'),
   'sem portfolio.json diz que não sabe, em vez de assumir');
truthy(mapLine({}).includes('not available'), 'e um current vazio também');
// A discordância é o ponto: com o medidor ligado e a carteira ainda fora de
// Critical, a frase da página tem de seguir a CARTEIRA.
eq(mapLine({ regime: 'Turbulence', critical_subregime: 'Critical_FTQ' }),
   'Portfolio on the Turbulence map.',
   'um sub-regime residual não muda o mapa quando o regime não é Critical');
// E o texto do medidor deixou de afirmar a posição da carteira.
const blocoJS = blocos[0];
truthy(!/No concurrent stress\. Portfolio on the/.test(blocoJS),
   'o texto do medidor já não afirma o mapa da carteira');
truthy(blocoJS.includes('js-portfolio-map-line'),
   'a frase da carteira tem um sítio próprio, preenchido a partir do portfolio.json');

// ── fmtTrigger cobre TODOS os motivos canónicos ────────────────────────────
// O `valuation_incomplete_held` foi criado no motor e nunca chegou ao site:
// aparecia como "⟳ Valuation incomplete held", com o ícone de rebalanceamento
// executado, numa semana em que o motor recusou rebalancar.
const REGRAS = JSON.parse(require('child_process').execSync(
  `python3 -c "import json,sys; sys.path.insert(0,'${ROOT}'); import mrm_rules; print(json.dumps(mrm_rules.as_dict()))"`
).toString());
truthy(Array.isArray(REGRAS.rebalanceReasons) && REGRAS.rebalanceReasons.length >= 12,
   `as regras publicam a lista canónica de motivos (${REGRAS.rebalanceReasons.length})`);
for (const r of REGRAS.rebalanceReasons) {
  const txt = fmtTrigger(r);
  truthy(txt && !txt.includes('_'),
    `fmtTrigger("${r}") tem texto próprio, não o motivo cru (obtido "${txt}")`);
}
// Os motivos de "não rebalanceou porque algo falhou" levam o ícone de aviso.
for (const r of ['valuation_incomplete_held', 'missing_prices_held',
                 'aborted_invalid_shares', 'no_allocation_available']) {
  truthy(fmtTrigger(r).startsWith('⚠'),
    `fmtTrigger("${r}") é um aviso, não um rebalanceamento executado`);
}
// E um motivo desconhecido é desenhado como aviso, não como sucesso.
truthy(fmtTrigger('motivo_que_ainda_nao_existe').startsWith('⚠'),
   'um motivo desconhecido é desenhado como aviso');

// ── renderMain: score n/d não rebenta a página ────────────────────────────
// `null.toFixed(1)` lançava TypeError, o render inteiro morria e o utilizador
// via a faixa de "não foi possível carregar o data.json" com o ficheiro bom.
const renderMain = F('renderMain');
// O data.json commitado e o ponto de partida, mas o score dele NAO e o que se
// afirma: `globalResilienceScore` fica em `null` sempre que menos de metade do
// peso dos pilares esta vivo — uma avaria da FRED chega — e nessa semana o
// `Number(null).toFixed(1)` dava "0.0" contra um render que, correctamente,
// desenha "n/d". Este ficheiro e portao dos dois jobs de sexta: seria a semana
// em que os dados apagam a ser tambem a semana sem newsletter para o dizer.
// Por isso o caso "com score" e construido com um score DECLARADO aqui.
const dataBase = JSON.parse(fs.readFileSync(path.join(ROOT, 'data.json'), 'utf8'));
const comScore = JSON.parse(JSON.stringify(dataBase));
comScore.globalResilienceScore = 6.4;
const semScore = JSON.parse(JSON.stringify(dataBase));
semScore.globalResilienceScore = null;
semScore.ndPillars = ['cycle','liquidity','premium','solvency','debt'];
semScore.pillars = semScore.pillars.map(p => ({ ...p, score: null }));
renderMain(semScore);
const htmlMain = elems['terminal-main'].innerHTML;
// A asserção tem de olhar para o NÚMERO do score, não para a página inteira: os
// cartões dos pilares já mostram "n/d" e faziam passar um teste que só
// procurasse a palavra em qualquer sítio.
const numeroDoScore = (html) => {
  const m = html.match(/class="score-number"[^>]*>([^<]*)</);
  return m ? m[1].trim() : null;
};
eq(numeroDoScore(htmlMain), 'n/d', 'com o score em n/d o anel mostra "n/d"');
truthy(!htmlMain.includes('NaN'), 'e não desenha NaN');
truthy(!/class="score-number"[^>]*>\s*0\.0\s*</.test(htmlMain),
   'e não mostra 0.0, que seria uma leitura inventada');
// O anel também não pode ficar pintado com a cor de uma banda: sem score não há
// banda, e a cor verde de "Resilient" ao lado de um "n/d" é uma contradição.
truthy(htmlMain.includes('color:var(--text-muted)'),
   'sem score o anel usa a cor neutra, não a cor de uma banda');
renderMain(comScore);
eq(numeroDoScore(elems['terminal-main'].innerHTML),
   comScore.globalResilienceScore.toFixed(1),
   'e com score volta a desenhar o número real');

// ── renderPortfolioHoldings: preço em falta é n/d, não $0.00 ──────────────
const semPreco = { ...critical,
  last_prices: { ...critical.last_prices, GLD: null } };
renderHoldings({ current: semPreco });
const htmlSemPreco = elems['port-holdings-body'].innerHTML;
const linhaGLD = htmlSemPreco.split('<tr>').find(l => l.includes('>GLD<'));
truthy(linhaGLD && !linhaGLD.includes('$0.00'),
   'um instrumento sem preço não é desenhado a valer $0.00');
truthy(linhaGLD && (linhaGLD.match(/n\/d/g) || []).length >= 2,
   'o preço e o valor saem ambos como n/d');
truthy(linhaGLD.includes('15%'), 'e a alocação continua a ser mostrada — a posição existe');
// preço zero conta como ausente: um preço de 0 não é um preço.
renderHoldings({ current: { ...critical, last_prices: { ...critical.last_prices, GLD: 0 } } });
truthy(!elems['port-holdings-body'].innerHTML.split('<tr>')
        .find(l => l.includes('>GLD<')).includes('$0.00'),
   'um preço de zero também é n/d, não $0.00');

// ── renderRebalanceLog: n/d não é pintado de vermelho ──────────────────────
renderLog({ history: [
  { issue: 27, date: '2026-09-11', regime: 'Critical', mrm_score: 6.97,
    rebalance_triggered: false, rebalance_reason: 'valuation_incomplete_held',
    portfolio_value: 10617.97, portfolio_pnl_pct: null },
]});
const linhaND = elems['port-log-body'].innerHTML;
truthy(!/color:var\(--red\);[^<]*>n\/d/.test(linhaND),
   'o "n/d" do P&L suprimido não é pintado a vermelho');
truthy(linhaND.includes('var(--text-muted)'),
   'é desenhado como ausência de leitura, com a cor neutra');
truthy(linhaND.includes('Held'), 'e a razão diz que a semana foi de retenção');

// ── Os KPI vêm TODOS do portfolio.json, nenhum é literal ──────────────────
// Uma auditoria mostrou que substituir cada um destes por um literal não fazia
// falhar teste nenhum: os KPI eram verificados por "não contém NaN", que um
// número escrito à mão também satisfaz. Aqui cada KPI é comparado com o valor
// que entrou, e um segundo conjunto de números confirma que o ecrã os SEGUE.
function kpisPara(valores) {
  renderKPIs({
    current: { ...critical, date: valores.date, regime: valores.regime,
               portfolio_value: valores.valor, portfolio_pnl_pct: valores.pnl,
               benchmark_spy_value: valores.bench, benchmark_spy_pnl_pct: valores.benchPnl,
               alpha_vs_benchmark_pct: valores.alpha },
    history: [{ issue: 27, regime: valores.regime, regime_signalled: valores.sinalizado,
                mrm_score: valores.score, rebalance_triggered: true,
                rebalance_reason: valores.motivo }],
  });
  return {
    valor:  elems['port-kpi-value'].textContent,
    pnl:    elems['port-kpi-pnl'].textContent,
    bench:  elems['port-kpi-bench'].textContent,
    benchPnl: elems['port-kpi-bench-pnl'].textContent,
    alpha:  elems['port-kpi-alpha'].textContent,
    regime: elems['port-kpi-regime'].textContent,
    score:  elems['port-kpi-score'].textContent,
    motivo: elems['port-kpi-rebalance'].textContent,
    data:   elems['port-updated'].textContent,
  };
}

const A = kpisPara({ valor: 10617.97, pnl: 6.18, bench: 11234.5, benchPnl: 12.34,
                     alpha: -6.16, regime: 'Critical', sinalizado: 'Critical',
                     score: 6.97, motivo: 'stress_on', date: '2026-09-11' });
eq(A.valor, '$10,617.97', 'o KPI do valor é o valor da carteira');
truthy(A.pnl.startsWith('+6.18%'), `o KPI do P&L é o P&L (deu "${A.pnl}")`);
eq(A.bench, '$11,234.50', 'o KPI do benchmark é o valor do benchmark');
truthy(A.benchPnl.startsWith('+12.34%'), `o P&L do benchmark também (deu "${A.benchPnl}")`);
eq(A.alpha, '-6.16%', 'e o alpha');
eq(A.regime, 'Critical regime', 'o regime é o da carteira');
eq(A.score, '7.0/10', 'o score é o da semana');
eq(A.data, 'Updated: 2026-09-11', 'e a data é a da carteira');

const B = kpisPara({ valor: 98765.43, pnl: -3.21, bench: 87654.32, benchPnl: -1.11,
                     alpha: -2.10, regime: 'Turbulence', sinalizado: 'Turbulence',
                     score: 4.05, motivo: 'semestral_rebalance', date: '2026-10-02' });
eq(B.valor, '$98,765.43', 'com outros números, o valor acompanha');
truthy(B.pnl.startsWith('-3.21%'), 'o P&L acompanha');
eq(B.bench, '$87,654.32', 'o benchmark acompanha');
truthy(B.benchPnl.startsWith('-1.11%'), 'o P&L do benchmark acompanha');
eq(B.alpha, '-2.10%', 'o alpha acompanha');
eq(B.regime, 'Turbulence regime', 'o regime acompanha');
eq(B.score, '4.0/10', 'o score acompanha (4.05 arredonda a 4.0, banqueiro)');
eq(B.data, 'Updated: 2026-10-02', 'a data acompanha');
truthy(B.motivo.includes('semi-annual'), 'e o motivo acompanha');

// O KPI do regime segue a CARTEIRA, não o sinal dos medidores. O motor grava os
// dois; mostrar o sinalizado anunciava uma rotação que ainda não aconteceu.
const C = kpisPara({ valor: 100, pnl: 0, bench: 100, benchPnl: 0, alpha: 0,
                     regime: 'Turbulence', sinalizado: 'Resilient',
                     score: 3.5, motivo: 'hold', date: '2026-10-09' });
eq(C.regime, 'Turbulence regime',
   'com o sinal em Resilient e a carteira em Turbulence, o KPI diz Turbulence');
// E um score de 0.0 é uma leitura, não uma ausência.
const D = kpisPara({ valor: 100, pnl: 0, bench: 100, benchPnl: 0, alpha: 0,
                     regime: 'Resilient', sinalizado: 'Resilient',
                     score: 0.0, motivo: 'hold', date: '2026-10-16' });
eq(D.score, '0.0/10', 'um score de 0.0 é publicado, não tratado como ausente');

// ── renderRebalanceLog: o histórico também mostra o regime detido ─────────
renderLog({ history: [
  { issue: 28, date: '2026-10-09', regime: 'Turbulence', regime_signalled: 'Critical',
    mrm_score: 6.5, rebalance_triggered: false, rebalance_reason: 'hold',
    portfolio_value: 10000, portfolio_pnl_pct: 0 },
]});
const htmlSinal = elems['port-log-body'].innerHTML;
truthy(htmlSinal.includes('>Turbulence<'), 'o histórico mostra o regime em que a carteira esteve');
truthy(!htmlSinal.includes('>Critical<'), 'e não o que os medidores sinalizaram');
truthy(htmlSinal.includes('$10,000.00'), 'e a coluna do valor mostra o valor da semana');
// A COR da badge tem de seguir o mesmo regime que o texto. Enquanto foram duas
// expressões separadas, mudar só uma delas passava despercebido.
// Olha-se para a badge, não para a linha toda: a coluna do P&L também tem
// cor, e um "não contém verde" na linha inteira não distinguia as duas.
const badge = (htmlSinal.match(/<span style="[^"]*">Turbulence<\/span>/) || [''])[0];
truthy(badge.includes('var(--orange)'),
   `a badge de uma semana em Turbulence é laranja, seguindo o regime detido (deu "${badge}")`);
truthy(!badge.includes('var(--green)'),
   'e não verde, que era a cor que saía ao ler o regime sinalizado');
const tone = F('regimeTone');
eq(tone('Turbulence').color, 'var(--orange)', 'Turbulence é laranja');
eq(tone('Critical').color, 'var(--red)', 'Critical é vermelho');
eq(tone('Resilient').color, 'var(--green)', 'Resilient é verde');
eq(tone(null).color, 'var(--text-muted)', 'um regime desconhecido é neutro, não verde');
eq(tone('QualquerCoisa').color, 'var(--text-muted)', 'e um regime que não existe também');

// ── renderSubregimeStatus: a caixa do capítulo do sub-regime ──────────────
// Era o único render da página sem teste nenhum. Descreve qual dos dois
// sub-portfolios de Critical está activo — a decisão sobre a manga de duração,
// que é a diferença entre TLT e SHY numa recessão.
const renderSub = F('renderSubregimeStatus');
const caixaSub = () => elems['subregime-live-status'].innerHTML;

renderSub({ current: { regime: 'Critical', critical_subregime: 'Critical_FTQ',
                       critical_subregime_note: 'The 10Y fell 24bp over three months.' } });
truthy(caixaSub().includes('Flight to Quality'), 'em FTQ diz Flight to Quality');
truthy(caixaSub().includes('callout green'), 'e desenha-a como a leitura favorável à duração');
truthy(caixaSub().includes('The 10Y fell 24bp'), 'e publica a nota do motor, não uma inventada');

renderSub({ current: { regime: 'Critical', critical_subregime: 'Critical_Stress' } });
truthy(caixaSub().includes('Stress without Relief'), 'em STRESS diz Stress without Relief');
truthy(caixaSub().includes('callout red'), 'e desenha-a como a leitura adversa');
truthy(!caixaSub().includes('Flight to Quality'), 'sem misturar as duas');

// Fora de Critical a regra não se aplica — e a caixa tem de dizer o regime REAL.
renderSub({ current: { regime: 'Turbulence' } });
truthy(caixaSub().includes('Not currently applicable'), 'fora de Critical diz que não se aplica');
truthy(caixaSub().includes('Turbulence'), 'e nomeia o regime em que a carteira está');

// Sem `current`, cai no histórico em vez de mentir.
renderSub({ history: [{ issue: 27, regime: 'Critical' }], current: {} });
truthy(caixaSub().includes('Stress without Relief') || caixaSub().includes('Critical'),
   'sem current, usa a última entrada do histórico');
// E sem nada, não inventa um regime.
renderSub({ current: {}, history: [] });
truthy(caixaSub().includes('unknown'), 'sem dados nenhuns, diz que não sabe');

// ── Um score de 0.0 no histórico é uma leitura, não uma ausência ──────────
// O mesmo defeito foi corrigido no renderPortfolioKPIs com _ausente() e ficou
// aqui: `s.mrm_score ? ... : '—'` trata 0.0 como se não houvesse leitura.
renderLog({ history: [
  { issue: 29, date: '2026-10-16', regime: 'Resilient', mrm_score: 0.0,
    rebalance_triggered: false, rebalance_reason: 'hold',
    portfolio_value: 10000, portfolio_pnl_pct: 0 },
  { issue: 30, date: '2026-10-23', regime: 'Resilient', mrm_score: null,
    rebalance_triggered: false, rebalance_reason: 'hold',
    portfolio_value: 10000, portfolio_pnl_pct: 0 },
]});
const linhas = elems['port-log-body'].innerHTML.split('<tr>').filter(l => l.includes('#'));
const linha29 = linhas.find(l => l.includes('#29'));
const linha30 = linhas.find(l => l.includes('#30'));
truthy(linha29.includes('>0.0<'), `um score de 0.0 e publicado (deu ${linha29.slice(0, 200)})`);
truthy(linha30.includes('>—<'), 'e um score ausente continua a sair como travessao');

// ── O custo de transacção da semana aparece no histórico ──────────────────
// O backtest publicado no mesmo site cobra $10 a abrir e $10 a fechar por
// posição. Enquanto a carteira real não os mostrava, os dois números conviviam
// como se fossem comparáveis.
renderLog({ history: [
  { issue: 31, date: '2026-10-30', regime: 'Critical', mrm_score: 6.9,
    rebalance_triggered: true, rebalance_reason: 'stress_on',
    portfolio_value: 10000, portfolio_pnl_pct: 0, transaction_cost_usd: 80.0 },
  { issue: 32, date: '2026-11-06', regime: 'Critical', mrm_score: 6.9,
    rebalance_triggered: false, rebalance_reason: 'hold',
    portfolio_value: 10000, portfolio_pnl_pct: 0, transaction_cost_usd: 0.0 },
]});
const logCusto = elems['port-log-body'].innerHTML.split('<tr>');
const l31 = logCusto.find(l => l.includes('#31'));
const l32 = logCusto.find(l => l.includes('#32'));
truthy(l31.includes('$80.00 custos'), `a semana que negociou mostra o custo (${l31.slice(0, 260)})`);
truthy(!l32.includes('custos'), 'e a semana de hold nao mostra custo nenhum');

// ── loadPortfolio: uma falha transitória não degrada a página inteira ─────
// Guardar também a promessa REJEITADA fazia com que um fetch falhado degradasse
// as três secções da carteira para todo o carregamento da página, onde antes
// cada uma tentava por si.
const loadPortfolio = F('loadPortfolio');
// A página já pediu o portfolio.json ao carregar (o stub devolve uma promessa
// que nunca resolve). Limpa-se a cache antes de medir o comportamento — sem
// isto, o `await` abaixo ficava pendurado e o Node saía em silêncio com
// código 0, um teste que nunca corre e nunca falha.
vm.runInContext('_portfolioPromise = null;', ctx);
let chamadas = 0;
sandbox.fetch = () => {
  chamadas++;
  return chamadas === 1
    ? Promise.reject(new Error('rede em baixo'))
    : Promise.resolve({ json: () => Promise.resolve({ current: { regime: 'Critical' } }) });
};

(async () => {
  let falhou1 = false;
  try { await loadPortfolio(); } catch (e) { falhou1 = true; }
  truthy(falhou1, 'a primeira chamada propaga a falha de rede');

  const pf = await loadPortfolio();
  eq(pf.current.regime, 'Critical', 'a chamada seguinte volta a tentar e resolve');
  eq(chamadas, 2, 'houve exactamente dois pedidos: a falha não ficou em cache');

  await loadPortfolio();
  eq(chamadas, 2, 'e a promessa boa fica em cache — um pedido por carregamento');

  // ── O contrato: os renderers do site sobre o que o MOTOR escreveu ───────
  //
  // Este ficheiro alimentava os renderers com objectos escritos à mão. Um teste
  // que inventa o output do produtor não está a testar o produtor: o motor podia
  // deixar de escrever um campo, ou escrevê-lo com outro nome, e a suite ficava
  // verde enquanto o site mostrava barras a 0% ao lado de posições reais, ou um
  // KPI em branco.
  //
  // E não se procura a leitura no texto do código — um `const {x} = c`, um
  // `c['x']` ou um alias escapam a qualquer expressão regular. Envolve-se o que
  // o motor escreveu num Proxy que anota o que lhe pedem, correm-se os renderers
  // a sério, e exige-se que tudo o que foi pedido lá esteja. A pergunta passa a
  // ser "o consumidor leu isto?" em vez de "o código parece ler isto?".
  const pedidos = new Set();
  let pedidosDoMundo = new Set();
  const espiaCom = (obj, prefixo, destino) => new Proxy(obj, {
    get(alvo, chave) {
      if (typeof chave === 'string' && !chave.startsWith('__') &&
          chave !== 'then' && !(chave in Array.prototype) && !(chave in Object.prototype)) {
        if (destino) { destino.add(prefixo + chave); } else {
          pedidos.add(prefixo + chave); pedidosDoMundo.add(prefixo + chave);
        }
      }
      return Reflect.get(alvo, chave);
    },
    // Uma cópia em bloco — `{...c}`, `Object.assign({}, c)`, `Object.entries(c)`,
    // um `for (const k in c)` — lê tudo. Sem isto, bastava o site ler
    // `{...c}.allocation_pct` para o campo sair do contrato em silêncio.
    ownKeys(alvo) {
      Reflect.ownKeys(alvo).forEach(k => {
        if (typeof k !== 'string') return;
        if (destino) { destino.add(prefixo + k); } else {
          pedidos.add(prefixo + k); pedidosDoMundo.add(prefixo + k);
        }
      });
      return Reflect.ownKeys(alvo);
    },
  });
  const espia = (obj, prefixo) => espiaCom(obj, prefixo, null);

  // O estado que o motor acabou de escrever, deixado pelo test_end_to_end. O
  // portfolio.json commitado nao serve: e mais antigo do que o esquema actual —
  // nao tem o custo de transaccao, que o motor escreve hoje em todas as linhas —
  // e comparar com ele daria falsos vermelhos e falsos verdes.
  const caminhoProduzido = path.join(ROOT, 'tests', '.produzido', 'portfolio.json');
  truthy(fs.existsSync(caminhoProduzido),
    'o test_end_to_end deixou o portfolio.json que o motor produziu em ' +
    'tests/.produzido/ (correr `python3 tests/test_end_to_end.py` primeiro; ' +
    'o portao corre-o antes deste ficheiro)');
  // A espia tem de estar presa também. O trap `ownKeys` — que apanha `{...c}`,
  // `Object.assign`, `Object.entries` — não muda nada hoje, porque o site não
  // faz cópias em bloco: podia ser revertido por engano e só se daria por isso
  // no dia em que passasse a fazê-las.
  const provaLidas = new Set();
  {
    const antes = pedidos.size;
    const provaEspia = new Proxy({ a: 1, b: 2, c: 3 }, {
      get(alvo, chave) { if (typeof chave === 'string') provaLidas.add(chave); return Reflect.get(alvo, chave); },
      ownKeys(alvo) { Reflect.ownKeys(alvo).forEach(k => { if (typeof k === 'string') provaLidas.add(k); }); return Reflect.ownKeys(alvo); },
    });
    void { ...provaEspia };
    eq([...provaLidas].sort().join(','), 'a,b,c',
      `um Proxy com ownKeys anota uma cópia em bloco (${[...provaLidas].join(',')})`);
    eq(pedidos.size, antes, 'e a prova não contamina o contrato real');
  }
  // E o trap está mesmo na espia que o contrato usa: sem ele, `{...c}` não
  // anotava nada.
  // `{...c}` chama o `get` de cada chave, portanto já era anotado. O que só o
  // `ownKeys` apanha é a enumeração pura — `Object.keys(c)`, `for (const k in c)`
  // — que lê os nomes sem tocar nos valores. É essa a porta que o trap fecha.
  const cópiaLidas = new Set();
  const cópiaEspia = espiaCom({ x: 1, y: 2 }, 'prova.', cópiaLidas);
  Object.keys(cópiaEspia);
  eq([...cópiaLidas].sort().join(','), 'prova.x,prova.y',
    `a espia do contrato anota uma enumeração de chaves (${[...cópiaLidas].join(',')})`);
  const espalhaLidas = new Set();
  void { ...espiaCom({ x: 1, y: 2 }, 'prova.', espalhaLidas) };
  eq([...espalhaLidas].sort().join(','), 'prova.x,prova.y',
    `e uma cópia em bloco (${[...espalhaLidas].join(',')})`);

  const pfReal = JSON.parse(fs.readFileSync(caminhoProduzido, 'utf8'));
  const caminhoCritical = path.join(ROOT, 'tests', '.produzido', 'portfolio_critical.json');
  // Obrigatório, como o mundo calmo. Sem esta exigência, o contrato da
  // superfície de crise — a caixa que explica qual dos dois sub-portfolios está
  // activo — desligava-se sozinho no dia em que o produtor deixasse de escrever
  // o ficheiro: um `if (existe)` é uma porta de saída silenciosa.
  truthy(fs.existsSync(caminhoCritical),
    'o test_end_to_end deixou também o portfolio.json do mundo Critical em ' +
    'tests/.produzido/ — há caminhos do site que só correm nesse regime');
  const pfCritical = JSON.parse(fs.readFileSync(caminhoCritical, 'utf8'));
  // ── Tudo o que o motor escreve em `history` tem a forma que os leitores
  // esperam. Uma entrada que nao e um registo — uma linha escrita a mao numa
  // recuperacao — era PRESERVADA dentro desta lista, e o `history[-1]` e o que
  // o cartao de topo da carteira e a primeira linha do log lêem: na semana em
  // que a carteira rodou 100% para o mapa de Critical, o site publicava "Hold
  // — no rebalance" e o score "—". Preservar era certo; preservar dentro da
  // serie que a pagina desenha era criar um estado que nenhum leitor conhece.
  truthy(Array.isArray(pfReal.history) && pfReal.history.length > 0,
    'o motor escreve um historico com entradas');
  for (const h_ of pfReal.history) {
    truthy(h_ && typeof h_ === 'object' && !Array.isArray(h_),
      `cada entrada do history e um registo (obtido ${JSON.stringify(h_)})`);
    truthy(Number.isFinite(Number(h_.issue)),
      `com um numero de edicao legivel (${JSON.stringify(h_.issue)})`);
    truthy(typeof h_.date === 'string' && /^\d{4}-\d{2}-\d{2}/.test(h_.date),
      `e uma data legivel (${JSON.stringify(h_.date)})`);
  }
  const dadosEspiados = {
    ...pfReal,
    current: espia(pfReal.current, 'current.'),
    history: pfReal.history.map(h => espia(h, 'history.')),
  };
  // Os renderers correm A SÉRIO. Uma excepção engolida aqui era um buraco: um
  // renderer que rebentasse SÓ sobre o estado que o motor escreve — e não sobre
  // os objectos escritos à mão — deixava a página em branco no site com a suite
  // verde, e ainda por cima o contrato encolhia, porque os campos que ele ainda
  // ia ler deixavam de ser anotados. Só se toleram as falhas que a ausência de
  // canvas no jsdom provoca, e essas são nomeadas.
  const rebentaram = [];
  // A lista sai do FICHEIRO, não é escrita à mão. Enumerar já falhou uma vez —
  // é o argumento que fez as chaves do contrato passarem a sair do código do
  // consumidor — e aqui continuava escrita: um renderer NOVO que lesse um campo
  // novo do portfolio.json ficava fora do contrato sem uma asserção vermelha, e
  // o campo podia desaparecer do produtor com a suite verde.
  const nomesRenderers = [...new Set(
    (HTML.match(/function\s+(render[A-Za-z0-9_]*)\s*\(/g) || [])
      .map(m => m.replace(/function\s+/, '').replace(/\s*\($/, '')))];
  truthy(nomesRenderers.length >= 6,
    `o index.html define os renderers que o contrato corre (${nomesRenderers.length})`);
  // Os seis que este contrato sempre correu têm de continuar lá: se a
  // enumeração deixar de os apanhar — outra forma de declarar a função, por
  // exemplo — o contrato encolhe em silêncio.
  for (const obrigatorio of ['renderPortfolioKPIs', 'renderPortfolioHoldings',
                             'renderRebalanceLog', 'renderEquityCurve',
                             'renderPortfolioDonut', 'renderSubregimeStatus']) {
    truthy(nomesRenderers.includes(obrigatorio),
      `a enumeração apanha o ${obrigatorio}`);
  }
  // O contrato corre-os a TODOS — o que se quer é anotar tudo o que alguém lê
  // do `current` e do `history`. Mas só os consumidores do portfolio.json são
  // julgados por rebentarem: um renderer do data.json a receber uma carteira
  // rebenta por razão legítima, e contá-lo era um vermelho falso.
  const PORTFOLIO_RENDERERS = new Set([
    'renderPortfolioKPIs', 'renderPortfolioHoldings', 'renderRebalanceLog',
    'renderEquityCurve', 'renderPortfolioDonut', 'renderSubregimeStatus',
  ]);
  const renderers = nomesRenderers.map(nome => [
    nome,
    vm.runInContext(`typeof ${nome} === 'function' ? ${nome} : null`, ctx),
  ]);
  pedidosDoMundo = new Set();
  for (const [nome, render] of renderers) {
    if (typeof render !== 'function') continue;
    try {
      render(dadosEspiados);
    } catch (e) {
      const semCanvas = /getContext|Chart|canvas/i.test(String(e && e.message));
      if (!semCanvas && PORTFOLIO_RENDERERS.has(nome)) {
        rebentaram.push(`${nome}: ${e && e.message}`);
      }
    }
  }
  const pedidosCalmo = pedidosDoMundo;
  // E o mesmo passe sobre a carteira em CRITICAL: ha caminhos do site que so
  // correm nesse regime, e os campos que eles leem — a nota que explica qual dos
  // dois sub-portfolios esta activo — nao entravam em contrato nenhum.
  pedidosDoMundo = new Set();
  {
    const dadosCritical = {
      ...pfCritical,
      current: espia(pfCritical.current, 'current.'),
      history: pfCritical.history.map(h => espia(h, 'history.')),
    };
    for (const [nome, render] of renderers) {
      if (typeof render !== 'function') continue;
      try {
        render(dadosCritical);
      } catch (e) {
        const semCanvas = /getContext|Chart|canvas/i.test(String(e && e.message));
        if (!semCanvas && PORTFOLIO_RENDERERS.has(nome)) {
          rebentaram.push(`${nome} (Critical): ${e && e.message}`);
        }
      }
    }
  }
  const pedidosCritical = pedidosDoMundo;

  // ── A Academia corre A SÉRIO, sobre o data.json que o motor produziu ─────
  //
  // O `test_academy.py` faz asserções sobre o TEXTO do HTML e sobre as regras
  // exportadas; nunca executa o `renderPillarScoring`. E o que ele afirma — os
  // `<tbody id="bands-*">` vazios e o `<p id="text-*">` a dizer "Loading" — é,
  // à letra, o estado de avaria: se o renderer rebentar, a página fica assim e
  // o teste chama-lhe verde. Aqui ele corre sobre o que o `fetch_data`
  // escreveu, com as regras que o `mrm_rules` exporta.
  const caminhoDataProd = path.join(ROOT, 'tests', '.produzido', 'data.json');
  truthy(fs.existsSync(caminhoDataProd),
    'o test_end_to_end deixou o data.json que o motor produziu em tests/.produzido/');
  const dadosProd = JSON.parse(fs.readFileSync(caminhoDataProd, 'utf8'));

  // ── O bloco `meta` do data.json também tem contrato ──────────────────────
  //
  // O contrato fechou o `current` e o `history` do portfolio.json e os pilares
  // e sentinelas do data.json — e o `data.meta` ficou de fora. Renomear
  // `meta.source` no fetch_data deixava a suite VERDE e usmrm.net passava a
  // mostrar "Source: FRED API / undefined" no cabeçalho do score, que é a
  // primeira coisa que um leitor vê. É a mesma família do defeito histórico
  // ('icsa' vs 'jobless'), no único bloco que faltava.
  {
    const lidasMeta = new Set();
    const metaEspiada = espiaCom(dadosProd.meta || {}, 'meta.', lidasMeta);
    const dadosMeta = espiaCom({ ...dadosProd, meta: metaEspiada }, '', new Set());
    // TODOS os renderers de topo, não um pelo nome. Prender o contrato a
    // `renderMain` fazia dele um portão frágil: mover o desenho do cabeçalho do
    // score para uma função à parte — refactorização que não muda uma linha do
    // que o leitor vê — deixava a espia vazia e fechava os dois jobs de sexta.
    const nomesRender = [...new Set(
      (HTML.match(/function\s+(render[A-Za-z0-9_]*)\s*\(/g) || [])
        .map(m => m.replace(/function\s+/, '').replace(/\s*\($/, '')))];
    truthy(nomesRender.length > 0, 'o site define renderers');
    for (const nome of nomesRender) {
      const fn = vm.runInContext(
        `typeof ${nome} === 'function' ? ${nome} : null`, ctx);
      if (typeof fn !== 'function') continue;
      try {
        fn(dadosMeta);
      } catch (e) {
        // Aqui não se julga se o renderer rebenta — isso é o passe anterior,
        // com o estado completo. Este passe só quer saber que campos de `meta`
        // foram PEDIDOS, e um pedido conta mesmo que a chamada morra a seguir.
      }
    }
    const chavesMeta = [...lidasMeta].map(k => k.slice('meta.'.length));
    // O piso sai do FICHEIRO, não de um número: enquanto o site ler `data.meta`,
    // a espia tem de ver pelo menos um campo. No dia em que deixar de o ler, não
    // há contrato a verificar e isto não pode ficar vermelho por isso.
    const siteLeMeta = /\bdata\.meta\b/.test(HTML);
    if (siteLeMeta) {
      truthy(chavesMeta.length > 0,
        `o site lê data.meta e a espia vê-o (${chavesMeta.join(', ')})`);
    }
    const faltamMeta = chavesMeta.filter(k => !(k in (dadosProd.meta || {})));
    eq(faltamMeta.join(', '), '',
      `e todos os campos de meta que o site lê existem no data.json que o ` +
      `motor escreveu (faltam: ${faltamMeta.join(', ')})`);
  }
  const renderAcademia = F('renderPillarScoring');
  vm.runInContext('RULES = ' + JSON.stringify(dadosProd.rules || {}) + ';', ctx);
  let erroAcademia = null;
  try {
    renderAcademia(dadosProd);
  } catch (e) {
    erroAcademia = e && e.message;
  }
  eq(erroAcademia, null,
    `o renderPillarScoring corre sobre o data.json que o motor produziu: ${erroAcademia}`);

  // ── "Pillars Active" sai dos dados, não está escrito no template ─────────
  //
  // Com UM pilar em n/d, o cabeçalho dizia "5 / 5" e o cartão logo a seguir
  // dizia "n/d", na mesma renderização — e a newsletter da mesma semana dizia
  // "4/5 Pillars Active". O `send_newsletter` já derivava o número do
  // `ndPillars`; esta era a cópia que faltava. O caso de UM pilar é o que
  // distingue a cópia constante da derivada (com cinco, "5/5" também é falso,
  // mas com zero é verdadeiro por acidente).
  {
    const totalPilares = ((dadosProd.rules || {}).pillarOrder || []).length
      || (dadosProd.pillars || []).length;
    truthy(totalPilares >= 3, `o data.json exporta os pilares (${totalPilares})`);
    // Até 3: é a partir de três pilares em n/d que o `global_score` ABANDONA o
    // composto (peso sobrevivente abaixo do mínimo) e devolve null. O laço
    // parava nos dois, e era exactamente o estado que faltava.
    for (const quantosNd of [0, 1, 2, 3]) {
      const idsNd = (dadosProd.pillars || []).slice(0, quantosNd).map(p_ => p_.id);
      const dadosNd = {
        ...dadosProd,
        ndPillars: idsNd,
        pillars: (dadosProd.pillars || []).map(
          p_ => (idsNd.includes(p_.id) ? { ...p_, score: null } : p_)),
      };
      try { F('renderMain')(dadosNd); } catch (e) { /* o canvas não existe no jsdom */ }
      // Lê-se do HTML que o `renderMain` ATRIBUIU, que é o que o browser
      // mostra. Ler de um elemento do dicionário dava o valor de uma escrita
      // anterior ao `innerHTML` — que a página real apaga.
      const publicado = textoDeIdNoHTML(
        (elems['terminal-main'] || {}).innerHTML, 'pillars-active');
      eq(publicado, `${totalPilares - quantosNd} / ${totalPilares}`,
        `com ${quantosNd} pilar(es) em n/d o site PUBLICA ` +
        `${totalPilares - quantosNd}/${totalPilares} (publicou ${publicado})`);
    }
  }

  // ── Uma sentinela SEM LEITURA não leva um veredicto ─────────────────────
  //
  // Havia três estados (alert / caution / o resto) e uma sentinela em n/d caía
  // no "resto": a página publicava, a verde, "✓ Within Normal Range" sobre uma
  // sentinela que não tem número — e a barra a 0,0% sugeria distância máxima ao
  // gatilho quando não há distância nenhuma. A mesma página diz, à letra, que
  // não se publica como medição um número que ninguém verificou.
  {
    for (const alvo of (dadosProd.sentinels || [])) {
      const dadosSent = {
        ...dadosProd,
        sentinels: (dadosProd.sentinels || []).map(s_ => (s_.id === alvo.id
          ? { ...s_, value: null, displayValue: 'n/d', status: 'nd', alert: false }
          : s_)),
      };
      let _erroSent = null;
      try { F('renderSidebar')(dadosSent); } catch (e) { _erroSent = e && e.message; }
      truthy(_erroSent === null || /getContext|Chart|canvas/i.test(String(_erroSent)),
        `o renderSidebar corre com a sentinela ${alvo.id} em n/d: ${_erroSent}`);
      // As sentinelas ficam na barra lateral do terminal.
      const html = (elems['terminal-sidebar'] || {}).innerHTML || '';
      const cartoes = html.split('sentinel-card');
      const cartao = cartoes.find(c => c.includes(alvo.name)) || '';
      truthy(cartao.includes('No reading'),
        `a sentinela ${alvo.id} em n/d é publicada como sem leitura ` +
        `(${cartao.slice(0, 200)})`);
      truthy(!cartao.includes('Within Normal Range'),
        `e NÃO como "dentro do normal" (${alvo.id})`);
      truthy(!/threshold-fill/.test(cartao),
        `e sem barra de distância ao gatilho, que não existe (${alvo.id})`);
    }
    // E o controlo negativo: sobre o data.json COMO ESTA, cada sentinela diz
    // "no reading" se e so se nao tiver leitura. A versao anterior exigia que
    // NENHUMA dissesse — uma expectativa tirada de um ficheiro que o proprio
    // sistema reescreve: numa semana simulada la a frente a ancora dos earnings
    // passa do prazo, o ERP entra em n/d com toda a razao, e o teste ficava
    // vermelho por o sistema estar a fazer exactamente o que deve.
    try { F('renderSidebar')(dadosProd); } catch (e) { /* sem canvas no jsdom */ }
    const htmlOk = (elems['terminal-sidebar'] || {}).innerHTML || '';
    const cartoesOk = htmlOk.split('sentinel-card');
    for (const s_ of (dadosProd.sentinels || [])) {
      const cartaoOk = cartoesOk.find(c => c.includes(s_.name)) || '';
      const semLeitura = s_.value === null || s_.value === undefined || s_.status === 'nd';
      eq(/No reading/.test(cartaoOk), semLeitura,
        `${s_.id}: diz "no reading" se e so se nao tiver leitura ` +
        `(valor ${JSON.stringify(s_.value)}, estado ${s_.status})`);
    }
  }

  // ── O painel do Medidor B conhece o terceiro estado do sub-regime ────────
  //
  // A janela de 3 meses do 10Y separa 35% da carteira em TLT de 20% em SHY, e
  // é o único dos três inputs do medidor que nunca foi publicado. Com ela por
  // medir, o painel escrevia "Stress confirmed with the 10Y flat or rising" —
  // o RESULTADO de um filtro que não correu, e o oposto do que faz a carteira
  // (que retém o sub-regime em vigor em vez de trocar).
  {
    const _sgBase = {
      active: true, subregime: null, label: 'Stress ON — 10Y window n/d',
      basis: 'Sahm',
      triggers: {
        sahmRealtime: { series: 'SAHMREALTIME', value: 0.62, threshold: 0.5,
                        fired: true, asOf: '2026-08-01', decides: 'regime' },
        delinquencyAccel: { series: 'DRALACBN', value: -0.06, threshold: 0.81,
                            fired: false, asOf: '2026-04-01', decides: 'regime' },
        tenY3m: { series: 'DGS10', value: null, threshold: -10, fired: null,
                  stale: true, asOf: null, decides: 'subregime' },
      },
    };
    // O `renderStressGauge` ANEXA uma secção; na página corre uma vez. Aqui
    // corre várias, portanto limpa-se a subárvore entre corridas para não se
    // ler o painel da corrida anterior.
    const _limpaMain = () => { if (elems['terminal-main']) elems['terminal-main']._kids = []; };
    _limpaMain();
    try { F('renderStressGauge')({ ...dadosProd, stressGauge: _sgBase }); }
    catch (e) { truthy(false, `o renderStressGauge corre sem a janela do 10Y: ${e && e.message}`); }
    // (asserções sobre o painel logo abaixo)
    const htmlSg = htmlVisivel(elems['terminal-main']);
    truthy(!/flat or rising/.test(htmlSg),
      `sem a janela medida, o painel NÃO afirma que o 10Y está plano ou a subir`);
    truthy(!/duration is paying as a hedge/.test(htmlSg),
      'nem que a duração está a pagar');
    truthy(/could not be measured/.test(htmlSg),
      `di-lo (${(htmlSg.match(/.{0,50}could not be measured.{0,60}/) || [''])[0]})`);
    // E NÃO afirma o que a carteira fez. Este painel lê o data.json; o que a
    // carteira detém vem do portfolio.json, na linha ao lado. A asserção
    // anterior exigia a palavra "retained" — e numa entrada fresca em Critical
    // não há sub-regime nenhum para reter: o motor roda 100% da carteira para o
    // vector defensivo. O teste fixava a falsidade.
    truthy(!/retained/.test(htmlSg),
      `e não afirma o que a carteira fez, que vem de outro ficheiro ` +
      `(${(htmlSg.match(/.{0,60}retained.{0,40}/) || [''])[0]})`);
    truthy(/js-portfolio-map-line/.test(htmlSg),
      'a posição da carteira tem o seu próprio lugar, lido do portfolio.json');
    truthy(/10Y, 3-month change/.test(htmlSg),
      'e o terceiro gatilho aparece na lista, como os outros dois');

    // ── E o LIMIAR publicado e o do ficheiro, com o sinal e a unidade ────
    //
    // O painel passou a derivar os tres limiares do data.json, mas o que
    // prendia essa derivacao era uma contagem de ocorrencias no codigo-fonte:
    // qualquer outra maneira de cravar o numero passava, e perder o ramo do
    // sinal fazia a pagina anunciar que o gatilho da fuga para a qualidade
    // dispara com o 10Y a SUBIR — o sentido oposto ao do filtro que separa 35%
    // em TLT de 20% em SHY, e o contrario do que a Academia diz tres ecrans
    // acima. Aqui le-se o texto que o painel PRODUZ.
    {
      const _sgLim = {
        active: true, subregime: 'FTQ', label: 'Stress ON', basis: 'Sahm',
        triggers: {
          sahmRealtime: { series: 'SAHMREALTIME', value: 0.62, threshold: 0.5,
                          fired: true, stale: false, decides: 'regime' },
          delinquencyAccel: { series: 'DRALACBN', value: -0.06, threshold: 0.81,
                              fired: false, stale: false, decides: 'regime' },
          tenY3m: { series: 'DGS10', value: -32, threshold: -10, fired: true,
                    stale: false, decides: 'subregime' },
        },
      };
      _limpaMain();
      try { F('renderStressGauge')({ ...dadosProd, stressGauge: _sgLim }); }
      catch (e) { truthy(false, `renderStressGauge: ${e && e.message}`); }
      const htmlLim = htmlVisivel(elems['terminal-main']);
      const limiares = [...htmlLim.matchAll(
        /mrm-sg__tlabel">([\s\S]*?)<\/span>[\s\S]*?mrm-sg__tthr">fires at ([^<]*)</g)]
        .map(m => [m[1].replace(/<[^>]*>/g, '').trim(), m[2].trim()]);
      eq(limiares.length, 3, `o painel desenha os tres gatilhos (${JSON.stringify(limiares)})`);
      const porRotulo = Object.fromEntries(limiares);
      const sahm = porRotulo['Sahm Rule real-time'] || '';
      const npl = porRotulo['Delinquency, 4-qtr change'] || '';
      const teny = porRotulo['10Y, 3-month change'] || '';
      // O NUMERO e o do ficheiro.
      truthy(sahm.includes('0.50'), `o limiar de Sahm publicado e 0.50 (${sahm})`);
      truthy(npl.includes('0.81'), `o da delinquencia e 0.81 (${npl})`);
      truthy(teny.includes('10'), `o do 10Y e 10 (${teny})`);
      // E o SENTIDO tambem: um limiar negativo dispara para BAIXO.
      truthy(/&ge;/.test(sahm) && !/&le;/.test(sahm),
        `Sahm dispara para cima (${sahm})`);
      truthy(/&ge;/.test(npl) && !/&le;/.test(npl),
        `a delinquencia dispara para cima (${npl})`);
      truthy(/&le;/.test(teny) && !/&ge;/.test(teny),
        `e o 10Y dispara para BAIXO — o contrario e o oposto do filtro que ` +
        `separa 35% em TLT de 20% em SHY (${teny})`);
      truthy(/&minus;|-/.test(teny), `com o sinal negativo visivel (${teny})`);
      // E a UNIDADE de cada um.
      truthy(npl.includes('pp'), `a delinquencia e em pontos percentuais (${npl})`);
      truthy(teny.includes('bp'), `e o 10Y em pontos base (${teny})`);
      truthy(!sahm.includes('pp') && !sahm.includes('bp'),
        `e o Sahm e um nivel, sem unidade (${sahm})`);
      // ── E o VALOR desenhado explica o disparo desenhado ────────────────
      //
      // O cartao tem tres coisas: o valor, o limiar e o `fired`. Se o valor for
      // desenhado a uma resolucao MAIS GROSSA do que aquela com que a decisao
      // foi tomada, o cartao contradiz-se: com -9.9 bp e `toFixed(0)`, a pagina
      // escrevia "-10 bp ... fires at <= -10 bp" ao lado de um gatilho que nao
      // disparou. E a mesma contradicao que o motor deixou de ter quando passou
      // a decidir sobre o numero que publica.
      for (const [_v10, _fired10] of [[-32, true], [-10, true], [-9.9, false],
                                      [18, false], [0, false]]) {
        const _sgV = JSON.parse(JSON.stringify(_sgLim));
        _sgV.triggers.tenY3m.value = _v10;
        _sgV.triggers.tenY3m.fired = _fired10;
        _sgV.subregime = _fired10 ? 'FTQ' : 'STRESS';
        _limpaMain();
        try { F('renderStressGauge')({ ...dadosProd, stressGauge: _sgV }); }
        catch (e) { /* nada */ }
        const htmlV = htmlVisivel(elems['terminal-main']);
        const bloco = (htmlV.split('10Y, 3-month change')[1] || '').slice(0, 400);
        const mVal = bloco.match(/mrm-sg__tval">([^<]*)</);
        const mThr = bloco.match(/mrm-sg__tthr">fires at ([^<]*)</);
        truthy(mVal && mThr, `o cartao do 10Y desenha valor e limiar (${_v10})`);
        const num = s => Number(String(s).replace(/&minus;/g, '-')
          .replace(/&le;|&ge;|&rarr;|FTQ|bp|\s|\+/g, ''));
        const vDes = num(mVal[1]);
        const tDes = num(mThr[1]);
        eq(vDes <= tDes, _fired10,
          `o valor DESENHADO (${mVal[1].trim()}) e o limiar DESENHADO ` +
          `(${mThr[1].trim()}) explicam o disparo (${_fired10})`);
        eq(vDes, _v10,
          `e o valor desenhado e o do ficheiro, sem perder resolucao (${_v10})`);
        // E os dois sao desenhados a MESMA resolucao: comparar um numero com
        // uma casa contra outro com zero e comparar duas coisas diferentes, e o
        // leitor nao tem como saber qual delas o `fired` usou.
        const casas = s => (String(s).replace(/&minus;/g, '-')
          .match(/\.(\d+)/) || ['', ''])[1].length;
        eq(casas(mThr[1]), casas(mVal[1]),
          `o valor (${mVal[1].trim()}) e o limiar (${mThr[1].trim()}) sao ` +
          `desenhados a mesma resolucao`);
      }

      // E se o produtor mudar o limiar, o painel muda com ele.
      const _sgOutro = JSON.parse(JSON.stringify(_sgLim));
      _sgOutro.triggers.sahmRealtime.threshold = 0.77;
      _sgOutro.triggers.tenY3m.threshold = -25;
      _limpaMain();
      try { F('renderStressGauge')({ ...dadosProd, stressGauge: _sgOutro }); }
      catch (e) { /* nada */ }
      const htmlOutro = htmlVisivel(elems['terminal-main']);
      truthy(htmlOutro.includes('0.77'),
        'o limiar publicado ACOMPANHA o ficheiro, nao e uma segunda copia');
      truthy(htmlOutro.includes('25'), 'e o do 10Y tambem');
      truthy(!htmlOutro.includes('0.50'),
        'e o valor antigo desaparece — nao ha literal cravado por baixo');
    }

    // E com a janela medida, as duas frases de sempre — uma para cada lado.
    for (const [_sub, _frase] of [['FTQ', 'duration is paying as a hedge'],
                                  ['STRESS', 'flat or rising']]) {
      _limpaMain();
      try {
        F('renderStressGauge')({ ...dadosProd, stressGauge: {
          ..._sgBase, subregime: _sub,
          triggers: { ..._sgBase.triggers,
                      tenY3m: { ..._sgBase.triggers.tenY3m, value: _sub === 'FTQ' ? -32 : 18,
                                fired: _sub === 'FTQ', stale: false,
                                asOf: '2026-09-04' } } } });
      } catch (e) { /* nada */ }
      const htmlOkSg = htmlVisivel(elems['terminal-main']);
      truthy(htmlOkSg.includes(_frase),
        `com a janela medida em ${_sub}, o painel diz o que mediu`);
      truthy(!/could not be measured/.test(htmlOkSg),
        `e não diz que não mediu (${_sub})`);
    }
  }

  // ── "Os dois gatilhos indisponíveis" só quando são mesmo os dois ─────────
  //
  // O protocolo n/d alargou-se: UM gatilho em falta ao lado de UM gatilho
  // quieto também dá `active: null`, porque a ausência ao lado do silêncio não
  // é calma. Nessa semana um dos dois FOI lido — e o parágrafo dizia "Both
  // triggers unavailable" três linhas acima do gatilho que traz valor,
  // `fired: false` e `stale: false`. O `basis` que o medidor publica já
  // distingue os dois casos.
  {
    const _basisUm = 'n/d — Sahm stale (last observation is 253 days old) and '
      + 'the other trigger is quiet; absence of a signal is not a signal. '
      + 'Retain previous state.';
    const _sgUm = {
      active: null, subregime: null, label: 'Stress n/d', basis: _basisUm,
      triggers: {
        sahmRealtime: { series: 'SAHMREALTIME', value: 0.20, threshold: 0.5,
                        fired: null, stale: true, ageDays: 253,
                        decides: 'regime' },
        delinquencyAccel: { series: 'DRALACBN', value: 0.05, threshold: 0.81,
                            fired: false, stale: false, ageDays: 73,
                            asOf: '2026-04-01', decides: 'regime' },
      },
    };
    if (elems['terminal-main']) elems['terminal-main']._kids = [];
    try { F('renderStressGauge')({ ...dadosProd, stressGauge: _sgUm }); }
    catch (e) { truthy(false, `o renderStressGauge corre com um gatilho lido: ${e && e.message}`); }
    const htmlUm = htmlVisivel(elems['terminal-main']);
    truthy(!/Both triggers unavailable/i.test(htmlUm),
      'com um gatilho lido, o painel NÃO afirma que os dois estão indisponíveis');
    truthy(htmlUm.includes(_basisUm),
      `e publica a razão que o produtor declarou ` +
      `(${(htmlUm.match(/.{0,40}no usable reading.{0,80}/) || [''])[0]})`);
    // E o gatilho que FOI lido aparece com o seu valor, não como n/d.
    truthy(/0\.05/.test(htmlUm),
      'e o gatilho avaliado aparece com o valor que trouxe');
  }

  // ── O SEGUNDO painel do medidor B lê os mesmos números do ficheiro ──────
  //
  // A página tem dois leitores dos mesmos três gatilhos. O do terminal ficou
  // preso na ronda passada; este — o "Live Status" do capítulo que EXPLICA a
  // regra — escrevia o sentido e a unidade à mão (`fires at >=` para todos, e o
  // limiar do 10Y cravado), e não aparecia uma única vez nesta suite porque
  // estava enterrado dentro de um `fetch`. Com o sinal errado, o capítulo
  // anunciava que o gatilho da fuga para a qualidade dispara com o 10Y a SUBIR
  // — o oposto do filtro que separa 35% em TLT de 20% em SHY.
  {
    const _sgG = {
      active: true, subregime: 'FTQ', label: 'Stress ON', basis: 'Sahm',
      triggers: {
        sahmRealtime: { series: 'SAHMREALTIME', value: 0.62, threshold: 0.5,
                        fired: true, stale: false, decides: 'regime' },
        delinquencyAccel: { series: 'DRALACBN', value: -0.06, threshold: 0.81,
                            fired: false, stale: false, decides: 'regime' },
        tenY3m: { series: 'DGS10', value: -32, threshold: -10, fired: true,
                  stale: false, decides: 'subregime' },
      },
    };
    const htmlG = F('renderGaugeBStatusHTML')(_sgG);
    truthy(htmlG, 'o painel do capítulo desenha alguma coisa');
    // O 10Y dispara para BAIXO. Era o sentido que estava escrito à mão.
    const linha10 = (htmlG.split('10Y, 3-month change')[1] || '').split('<')[0];
    truthy(/\u2264|&le;/.test(linha10),
      `o 10Y dispara para BAIXO no capítulo (${linha10})`);
    truthy(!/\u2265|&ge;/.test(linha10),
      `e NÃO para cima — o oposto do filtro que decide 35% em TLT (${linha10})`);
    truthy(/10\.0|10/.test(linha10), `com o limiar do ficheiro (${linha10})`);
    // E os outros dois disparam para cima, com o limiar do ficheiro.
    const linhaS = (htmlG.split('Sahm rule (real-time):')[1] || '').split('<')[0];
    const linhaD = (htmlG.split('Delinquency, 4-qtr change:')[1] || '').split('<')[0];
    truthy(/\u2265|&ge;/.test(linhaS) && linhaS.includes('0.50'),
      `o Sahm dispara para cima, a 0.50 (${linhaS})`);
    truthy(/\u2265|&ge;/.test(linhaD) && linhaD.includes('0.81'),
      `e a delinquência a +0.81 pp (${linhaD})`);
    // E se o produtor mudar o limiar, o capítulo muda com ele.
    const _sgG2 = JSON.parse(JSON.stringify(_sgG));
    _sgG2.triggers.sahmRealtime.threshold = 0.77;
    _sgG2.triggers.tenY3m.threshold = -25;
    const htmlG2 = F('renderGaugeBStatusHTML')(_sgG2);
    truthy(htmlG2.includes('0.77') && htmlG2.includes('25'),
      'os limiares do capítulo acompanham o ficheiro');
    truthy(!htmlG2.includes('0.50'),
      'e o valor antigo desaparece — não há literal cravado por baixo');
    // E sem medidor nenhum, não rebenta.
    eq(F('renderGaugeBStatusHTML')(null), '',
      'sem stressGauge, o painel do capítulo fica vazio');
  }

  // ── O painel do sinal não decide sobre um score que não existe ───────────
  //
  // Em JavaScript `null <= 4.0` é TRUE. Numa semana sem composto — alcançável
  // com pilares em n/d que cheguem para o peso restante ficar abaixo do mínimo
  // — este painel pintava-se de VERDE e publicava "Maximum Exposure", a
  // recomendação mais agressiva da página, sobre um score que não existe.
  {
    for (const scoreNd of [null, undefined]) {
      const dadosSemScore = { ...dadosProd, globalResilienceScore: scoreNd,
                              status: 'nd' };
      try { F('renderBDCSignal')(dadosSemScore); } catch (e) {
        truthy(false, `o renderBDCSignal corre sem score: ${e && e.message}`);
      }
      const alloc = elems['signal-allocation'] || {};
      truthy(!/Maximum Exposure/.test(alloc.textContent || ''),
        `sem score, o painel NÃO publica "Maximum Exposure" ` +
        `(${alloc.textContent})`);
      truthy(!/Minimum|Tactical/.test(alloc.textContent || ''),
        `nem nenhuma das outras recomendações (${alloc.textContent})`);
      truthy(/No Reading/i.test(alloc.textContent || ''),
        `diz que não há leitura (${alloc.textContent})`);
      truthy(!/var\(--green\)/.test((alloc.style || {}).color || ''),
        `e não se pinta de verde (${(alloc.style || {}).color})`);
      eq((elems['signal-score'] || {}).textContent, '—',
        'e o número publicado é um travessão, não um 0.0');
    }
    // E com score, o painel de sempre — senão isto passava com um painel morto.
    try { F('renderBDCSignal')(dadosProd); } catch (e) { /* nada */ }
    const allocOk = (elems['signal-allocation'] || {}).textContent || '';
    truthy(/(Maximum Exposure|Minimum|Tactical)/.test(allocOk),
      `com score, o painel volta a recomendar (${allocOk})`);
    truthy(!/No Reading/i.test(allocOk),
      'e não diz "no reading" numa semana com score');
  }

  // ── Um filtro por AVALIAR não é um filtro alinhado ───────────────────────
  //
  // O veredicto da Golden Rule contava só o que era MAU e assumia que o resto
  // estava bem: com os três filtros sem leitura, `nonOk` era zero e a página
  // publicava, a verde, "All 3 entry filters are aligned".
  {
    const dadosCegos = {
      ...dadosProd,
      pillars: (dadosProd.pillars || []).map(p_ => (
        p_.id === 'cycle' ? { ...p_, value: 'n/d', score: null, status: 'nd' }
        : p_.id === 'liquidity' ? { ...p_, m2YoyGrowthPct: null } : p_)),
      sentinels: (dadosProd.sentinels || []).map(s_ => (s_.id === 'jobless'
        ? { ...s_, value: null, displayValue: 'n/d', status: 'nd', alert: false }
        : s_)),
    };
    try { F('renderBDCSignal')(dadosCegos); } catch (e) {
      truthy(false, `o renderBDCSignal corre com os filtros em n/d: ${e && e.message}`);
    }
    const vTexto = (elems['signal-verdict-text'] || {}).innerHTML || '';
    const vCaixa = (elems['bdc-signal-verdict'] || {}).className || '';
    truthy(!/are aligned/.test(vTexto),
      `com filtros por avaliar, a página NÃO declara os filtros alinhados ` +
      `(${vTexto.slice(0, 160)})`);
    truthy(/could not be evaluated/.test(vTexto),
      `di-lo (${vTexto.slice(0, 160)})`);
    truthy(!/green/.test(vCaixa),
      `e a caixa não é verde (${vCaixa})`);
    // E o badge do filtro sem leitura não pode dizer OK.
    const badge = (elems['filter-jobless-badge'] || {}).textContent || '';
    truthy(badge !== 'OK',
      `o badge de um filtro sem leitura não diz OK (${badge})`);
  }

  // ── A caixa de alertas conhece o quarto estado ───────────────────────────
  //
  // Ela olhava só para o que é MAU e, não encontrando nada, publicava "All
  // sentinels and pillars are within normal range this week" — uma afirmação
  // sobre TODAS as sentinelas e todos os pilares, numa semana em que nenhum
  // deles tem leitura, vinte linhas abaixo do cartão que já sabia calar-se.
  {
    const dadosTudoNd = {
      ...dadosProd,
      sentinels: (dadosProd.sentinels || []).map(s_ => (
        { ...s_, value: null, displayValue: 'n/d', status: 'nd', alert: false })),
      pillars: (dadosProd.pillars || []).map(p_ => (
        { ...p_, score: null, status: 'nd' })),
      ndPillars: (dadosProd.pillars || []).map(p_ => p_.id),
    };
    try { F('renderSidebar')(dadosTudoNd); } catch (e) { /* sem canvas */ }
    const htmlNd = (elems['terminal-sidebar'] || {}).innerHTML || '';
    const caixaNd = (htmlNd.split('alert-box').slice(1) || []).join(' ');
    truthy(!/within normal range/i.test(caixaNd),
      `sem uma única leitura, a caixa de alertas NÃO declara tudo dentro do ` +
      `normal (${caixaNd.slice(0, 160)})`);
    truthy(!/No Active Alerts/i.test(htmlNd),
      'nem "sem alertas activos"');

    // E cada um dos dois lados por si. Com os dois em n/d ao mesmo tempo,
    // bastava um deles saber calar-se para o teste passar — e foi assim que a
    // caixa de alertas ficou anos a ignorar o estado que o cartão vinte linhas
    // acima já conhecia. Cada item sem leitura tem de aparecer NOMEADO.
    for (const lado of ['sentinels', 'pillars']) {
      const dadosLado = {
        ...dadosProd,
        sentinels: lado === 'sentinels'
          ? (dadosProd.sentinels || []).map(s_ => ({ ...s_, value: null,
              displayValue: 'n/d', status: 'nd', alert: false }))
          : dadosProd.sentinels,
        pillars: lado === 'pillars'
          ? (dadosProd.pillars || []).map(p_ => ({ ...p_, score: null, status: 'nd' }))
          : dadosProd.pillars,
        ndPillars: lado === 'pillars'
          ? (dadosProd.pillars || []).map(p_ => p_.id) : [],
      };
      try { F('renderSidebar')(dadosLado); } catch (e) { /* sem canvas */ }
      const htmlLado = (elems['terminal-sidebar'] || {}).innerHTML || '';
      const caixa = (htmlLado.split('alert-box').slice(1) || []).join(' ');
      // Scoped à CAIXA DE ALERTAS: "✓ Within Normal Range" é também o texto
      // de estado de um cartão de sentinela com leitura, e no lado dos pilares
      // as sentinelas continuam a tê-lo com toda a razão.
      truthy(!/within normal range/i.test(caixa),
        `com ${lado} em n/d, a caixa de alertas não declara tudo dentro do ` +
        `normal (${caixa.slice(0, 160)})`);
      for (const item of (dadosProd[lado] || [])) {
        truthy(caixa.includes(item.name) && /No Reading/i.test(caixa),
          `a caixa de alertas nomeia ${item.name} como sem leitura ` +
          `(${caixa.slice(0, 200)})`);
      }
    }
    // E sobre o data.json COMO ESTÁ, a caixa fala de leituras em falta se e só
    // se houver alguma. Exigir que NÃO falasse era tirar a expectativa de um
    // ficheiro que o próprio sistema reescreve: numa sexta simulada lá à frente
    // a âncora dos earnings passa do prazo, o ERP entra em n/d com toda a
    // razão, e a caixa passa a dizê-lo — correctamente.
    try { F('renderSidebar')(dadosProd); } catch (e) { /* sem canvas */ }
    const htmlNormal = (elems['terminal-sidebar'] || {}).innerHTML || '';
    const caixaNormal = (htmlNormal.split('alert-box').slice(1) || []).join(' ');
    const semLeituraAlgum =
      (dadosProd.sentinels || []).some(s_ => s_.value === null
        || s_.value === undefined || s_.status === 'nd')
      || (dadosProd.pillars || []).some(p_ => p_.score === null
        || p_.score === undefined || p_.status === 'nd');
    eq(/No Reading/i.test(caixaNormal), semLeituraAlgum,
      `a caixa fala de leituras em falta se e só se houver alguma ` +
      `(há: ${semLeituraAlgum})`);
  }

  // ── E o capítulo da Academia sobre a UNRATE também não ────────────────────
  //
  // O cartão da barra lateral já sabia calar-se, mas o parágrafo do capítulo
  // BDC lia a MESMA sentinela e escrevia, a verde, "Below the 5.2% Stagflation
  // Scenario trigger" — a afirmação mais forte da página sobre o gatilho, feita
  // sobre um número que não existe, e acompanhada de um delta "+0.0%" e de uma
  // tendência "stable" que ninguém mediu. Duas leituras da mesma ausência não
  // podem dar veredictos opostos.
  {
    const _unr = (dadosProd.sentinels || []).find(s_ => s_.id === 'unemployment');
    truthy(!!_unr, 'o data.json exporta a sentinela da UNRATE');
    if (_unr) {
      const dadosU = {
        ...dadosProd,
        sentinels: (dadosProd.sentinels || []).map(s_ => (s_.id === 'unemployment'
          ? { ...s_, value: null, displayValue: 'n/d', status: 'nd',
              trend: 'nd', delta: 'n/d', alert: false }
          : s_)),
      };
      try { F('renderBDCUnrateStatus')(dadosU); } catch (e) {
        truthy(false, `o renderBDCUnrateStatus corre com a UNRATE em n/d: ${e && e.message}`);
      }
      const capNd = (elems['bdc-unrate-status'] || {}).innerHTML || '';
      truthy(/No Reading/i.test(capNd),
        `o capítulo da UNRATE em n/d publica-se como sem leitura (${capNd.slice(0, 200)})`);
      truthy(!/Below the/i.test(capNd),
        'e NÃO afirma que a taxa está abaixo do gatilho');
      truthy(!/ACTIVE/i.test(capNd),
        'e também não afirma que o gatilho disparou');
      truthy(!/callout (red|orange|green)/.test(capNd),
        'e não pinta um veredicto de cor sobre a ausência');
      // Com leitura, o parágrafo de sempre — e a condição sai da sentinela
      // publicada, não da suposição de que ela traz sempre um número.
      try { F('renderBDCUnrateStatus')(dadosProd); } catch (e) { /* nada */ }
      const capOk = (elems['bdc-unrate-status'] || {}).innerHTML || '';
      const unrSemLeitura = _unr.value === null || _unr.value === undefined
        || _unr.status === 'nd';
      eq(/No Reading/i.test(capOk), unrSemLeitura,
        `o capítulo diz "no reading" se e só se a UNRATE não tiver leitura ` +
        `(valor ${JSON.stringify(_unr.value)}, estado ${_unr.status})`);
      eq(/(Below the|ACTIVE)/.test(capOk), !unrSemLeitura,
        'e só compara com o gatilho quando há um número para comparar');
    }
  }

  // ── Sem composto não há redistribuição de pesos nenhuma ─────────────────
  //
  // O `global_score` abandona o composto quando o peso sobrevivente cai abaixo
  // do mínimo — três pilares em n/d chegam, e três dos cinco vivem de séries
  // trimestrais. A caixa do capítulo afirmava, mesmo assim, que o peso do pilar
  // tinha sido "redistributed across the remaining pillars". A newsletter da
  // mesma semana já dizia a verdade; esta era a cópia que faltava.
  {
    const idsTodos = (dadosProd.pillars || []).map(p_ => p_.id);
    const idsTres = idsTodos.slice(0, 3);
    const semComposto = {
      ...dadosProd,
      globalResilienceScore: null,
      ndPillars: idsTres,
      ndReasons: Object.fromEntries(idsTres.map(i_ => [i_, 'series'])),
      pillars: (dadosProd.pillars || []).map(
        p_ => (idsTres.includes(p_.id) ? { ...p_, score: null, status: 'nd' } : p_)),
    };
    try { F('renderPillarScoring')(semComposto); } catch (e) {
      truthy(false, `o renderPillarScoring corre sem composto: ${e && e.message}`);
    }
    for (const id_ of idsTres) {
      const txt = (elems[`text-${id_}`] || {}).textContent || '';
      truthy(txt, `o capítulo do pilar ${id_} tem texto`);
      truthy(!/redistributed/.test(txt),
        `sem composto, o capítulo de ${id_} NÃO afirma redistribuição de pesos ` +
        `(${txt.slice(0, 200)})`);
      truthy(/no Resilience Score this week/.test(txt),
        `di-lo (${txt.slice(0, 200)})`);
    }
    // E COM composto, a frase de sempre.
    const comComposto = {
      ...dadosProd,
      globalResilienceScore: 6.4,
      ndPillars: [idsTodos[0]],
      ndReasons: { [idsTodos[0]]: 'series' },
      pillars: (dadosProd.pillars || []).map(
        p_ => (p_.id === idsTodos[0] ? { ...p_, score: null, status: 'nd' } : p_)),
    };
    try { F('renderPillarScoring')(comComposto); } catch (e) { /* nada */ }
    const txtOk = (elems[`text-${idsTodos[0]}`] || {}).textContent || '';
    truthy(/redistributed/.test(txtOk),
      `com composto, o peso É redistribuído e o capítulo di-lo (${txtOk.slice(0, 160)})`);
    truthy(!/no Resilience Score this week/.test(txtOk),
      'e não se diz que não há score quando há');
  }

  // ── E a CAUSA do n/d sai do data.json, não é adivinhada ──────────────────
  //
  // A frase imputava sempre o n/d à FRED. Basta existir um segundo motivo — a
  // referência dos earnings passada do prazo, posta à mão — para o site acusar
  // um fornecedor de dados de uma falha que é interna, numa semana em que ele
  // publicou tudo a horas.
  {
    const alvo = (dadosProd.pillars || []).find(p_ => p_.id === 'premium')
      || (dadosProd.pillars || [])[0];
    const comRazao = (razao) => ({
      ...dadosProd,
      ndPillars: [alvo.id],
      ndReasons: razao ? { [alvo.id]: razao } : {},
      pillars: (dadosProd.pillars || []).map(
        p_ => (p_.id === alvo.id ? { ...p_, score: null } : p_)),
    });
    renderAcademia(comRazao('stale-anchor'));
    const txtAncora = (elems['text-' + alvo.id] || {}).textContent || '';
    truthy(/hand-set earnings reference/.test(txtAncora),
      `com ndReason 'stale-anchor' o site diz a causa certa: ${txtAncora.slice(0, 160)}`);
    truthy(!/did not publish in time/.test(txtAncora),
      `e NÃO culpa a série da FRED: ${txtAncora.slice(0, 160)}`);
    renderAcademia(comRazao('series'));
    const txtSerie = (elems['text-' + alvo.id] || {}).textContent || '';
    truthy(/did not publish in time/.test(txtSerie),
      `e com ndReason 'series' diz que a série não publicou: ${txtSerie.slice(0, 160)}`);
  }
  // E desenha mesmo: as bandas deixam de estar vazias e o texto deixa de dizer
  // "Loading". Sem isto, um renderer que devolvesse cedo passava na mesma.
  const idsAcademia = Object.keys((dadosProd.rules || {}).pillarScoring || {});
  // O piso sai do PRODUTOR — a ordem dos pilares que o próprio motor exporta —,
  // não de um número escrito à mão: `>= 3` sobre cinco deixava dois pilares sem
  // bandas exportadas com a suite verde, e ficaria vermelho por engano no dia em
  // que o sistema tivesse legitimamente menos.
  const ordemAcademia = (dadosProd.rules || {}).pillarOrder || [];
  truthy(ordemAcademia.length > 0,
    `o data.json exporta a ordem dos pilares (${ordemAcademia.join(', ')})`);
  eq(idsAcademia.length, ordemAcademia.length,
    `e as regras de scoring de TODOS eles (${idsAcademia.join(', ')} vs ` +
    `${ordemAcademia.join(', ')})`);
  let desenhadas = 0;
  for (const id of idsAcademia) {
    const corpo = elems['bands-' + id];
    if (corpo && corpo.innerHTML && corpo.innerHTML.trim()) desenhadas += 1;
  }
  // TODAS, nao "pelo menos tres": o data.json exporta cinco pilares e o piso
  // fixo deixava dois por desenhar sem uma unica falha. A propriedade e "o site
  // desenha o que o motor exporta", e essa nao tem numero magico.
  eq(desenhadas, idsAcademia.length,
    `e TODAS as tabelas de bandas ficam desenhadas (${desenhadas} de ${idsAcademia.length})`);
  vm.runInContext('RULES = { resilientMax: 4.0, criticalMin: 8.0 };', ctx);
  eq(rebentaram.join(' | '), '',
    `nenhum renderer do site rebenta sobre o estado que o motor escreveu: ${rebentaram.join(' | ')}`);

  // Um campo conta como escrito se o motor o escreveu em QUALQUER dos dois
  // mundos: `critical_subregime_note` so existe em Critical, e o `hold` de uma
  // semana calma nao tem custo de transaccao a declarar.
  const presente = (onde, campo) => [pfReal, pfCritical].filter(Boolean).some(pf_ => {
    const alvo = onde === 'current' ? pf_.current : pf_.history[pf_.history.length - 1];
    return alvo && campo in alvo;
  });
  const emFalta = [...pedidos].filter(k => {
    const [onde, campo] = k.split('.');
    return !presente(onde, campo);
  });
  // Pisos por superfície E POR MUNDO. Com um piso global, desarmar metade do
  // contrato — tirar a espia ao histórico de um dos mundos — passava
  // despercebido, porque o outro mundo sozinho preenchia o conjunto. O conjunto
  // é partilhado de propósito (um campo escrito só em Critical conta); os pisos
  // é que não podem ser.
  for (const [mundo, conj] of [['calmo', pedidosCalmo], ['Critical', pedidosCritical]]) {
    const por = p => [...conj].filter(k => k.startsWith(p)).length;
    truthy(por('current.') >= 8,
      `no mundo ${mundo} os renderers pedem campos ao bloco current (${[...conj].filter(k => k.startsWith('current.')).sort().join(', ')})`);
    truthy(por('history.') >= 6,
      `e à linha do histórico (${[...conj].filter(k => k.startsWith('history.')).sort().join(', ')})`);
  }
  eq(emFalta.join(', '), '',
    `e o motor escreve todos os que eles pedem — em falta: ${emFalta.join(', ')}`);

  terminou = true;
  console.log(`TODOS OS ${ok} TESTES PASSARAM`);
})().catch(e => { console.error('FALHOU: ' + e.message); process.exit(1); });
