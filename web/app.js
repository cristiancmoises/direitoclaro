/* Direito Claro — interface sem bibliotecas de terceiros nem rastreamento. */
(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  let laws = [];
  let selectedCategory = 'Todas';
  let expandCatalog = false;
  let topics = [];
  let normTypes = [];
  let officialPortals = [];
  let activeLaw = null;
  let activeQuery = '';
  let nextOffset = null;
  let requestId = 0;
  let readerFont = 15;
  let speaking = false;
  let readingLevels = null;
  let toastTimer;
  const nf = new Intl.NumberFormat('pt-BR');

  function toast(message) {
    const box = $('#toast'); box.textContent = message; box.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => box.hidden = true, 3600);
  }

  async function getJSON(path) {
    try {
      const response = await fetch(path, {headers: {Accept:'application/json'}});
      if (response.ok) return await response.json();
    } catch (_) { /* Network and response failures use the same public message. */ }
    throw new Error('Não foi possível consultar os dados agora.');
  }

  function themeInit() {
    let value = 'dark';
    try { value = localStorage.getItem('direito-tema') === 'light' ? 'light' : 'dark'; } catch (_) { /* storage blocked */ }
    applyTheme(value);
    $('#theme-toggle').addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      applyTheme(next); try { localStorage.setItem('direito-tema', next); } catch (_) { /* storage blocked */ }
    });
  }
  function applyTheme(mode) {
    document.documentElement.dataset.theme = mode;
    $('#theme-icon').textContent = mode === 'dark' ? '☼' : '☾';
    $('#theme-toggle').setAttribute('aria-label', mode === 'dark' ? 'Ativar tema claro' : 'Ativar tema escuro');
    $('meta[name="theme-color"]').content = mode === 'dark' ? '#000000' : '#ffffff';
  }

  function initCategories() {
    const cats = ['Todas',...new Set(laws.map(l=>l.category))];
    const row = $('#category-pills'); row.replaceChildren();
    const side = $('#side-categories'); side.replaceChildren();
    cats.forEach(cat => {
      const btn = el('button','pill' + (cat === selectedCategory ? ' selected':''),cat);
      btn.type = 'button'; btn.dataset.category = cat; btn.setAttribute('aria-pressed',String(cat===selectedCategory));
      btn.addEventListener('click',()=>chooseCategory(cat)); row.append(btn);
      if(cat!=='Todas') {
        const link=el('button','nav-link',cat);link.type='button';
                link.addEventListener('click',()=>{chooseCategory(cat);$('#biblioteca').scrollIntoView({behavior:'smooth'});});
        side.append(link);
      }
    });
  }
  function chooseCategory(cat) {
    selectedCategory = cat;
    document.querySelectorAll('.pill').forEach(b=>{
      const selected=b.dataset.category===cat;
      b.classList.toggle('selected',selected);b.setAttribute('aria-pressed',String(selected));
    });
    renderLaws();
  }

  function renderLaws() {
    const grid = $('#law-grid'); grid.replaceChildren();
    const all = laws.filter(l=>selectedCategory==='Todas'||l.category===selectedCategory);
    const list = (selectedCategory==='Todas' && !expandCatalog) ? all.slice(0,12) : all;
    const seeAll = $('#show-all');
    seeAll.textContent = selectedCategory==='Todas' ? (expandCatalog ? 'Mostrar destaques' : 'Ver as '+nf.format(laws.length)+' normas') : 'Mostrar todas as categorias';
    for(const law of list) {
      const card = el('button','law-card');card.type='button';
      const top = el('div','law-card-top');
      const summaryTag = law.imported_count ? 'Texto local' : (law.pending_captures ? 'Em revisão' : 'Referência');
      top.append(el('span','law-symbol',law.symbol),el('span','law-badge',summaryTag));
      const title = el('h3','law-title',law.title);
      const desc = el('p','law-description',law.description);
      const bottom=el('div','law-card-bottom');
      bottom.append(el('span','law-number',law.number),el('span','status' + (law.imported_count ? ' synced':''), law.imported_count ? nf.format(law.imported_count)+' artigos importados' : (law.pending_captures ? 'Captura aguardando revisão' : (law.reference_kind==='existing_direct_link_unverified' ? 'Página cadastrada' : 'Pesquisa oficial'))));
      card.append(top,title,desc,bottom);
      card.addEventListener('click',()=>showLaw(law.slug));
      grid.append(card);
    }
  }

  function renderTopics() {
    const grid=$('#topic-grid'); grid.replaceChildren();
    for (const topic of topics) {
      const card=el('button','topic-card');card.type='button';
      card.append(el('span','topic-index',String(topics.indexOf(topic)+1).padStart(2,'0')),
                  el('h3','',topic.title),el('p','',topic.question),el('span','topic-call','Ver normas relacionadas'));
      card.addEventListener('click',()=>showTopic(topic));
      grid.append(card);
    }
  }

  function renderNormTypes() {
    const list=$('#norm-types-list');list.replaceChildren();
    for (const type of normTypes) {
      const details=el('details','norm-type');
      const summary=el('summary','');
      summary.append(el('span','',type.name),el('small','',type.scope));
      const answer=el('div','norm-explained');
      answer.append(el('p','',type.simple),el('p','norm-warning',type.note));
      details.append(summary,answer);list.append(details);
    }
  }

  function renderPortals() {
    const area=$('#sources-list');area.replaceChildren();
    for (const source of officialPortals) {
      const link=el('a','source-entry');link.href=source.url;
      link.target='_blank';link.rel='noopener noreferrer';
      link.append(el('span','source-category',source.scope),el('strong','',source.title),
                  el('span','source-desc',source.description),el('span','source-arrow','Abrir'));
      area.append(link);
    }
  }

  function sourceLink(law, label='Abrir referência oficial') {
    const a=el('a','catalog-source',label); a.href=law.source_url;
    a.target='_blank';a.rel='noopener noreferrer';
    return a;
  }

  function lawReference(law) {
    const node=el('div','catalog-hit');
    const content=el('div','catalog-hit-info');
    content.append(el('span','result-tag',law.number),el('h3','',law.title),
                   el('p','',law.description));
    const status=law.official_articles ? `${law.official_articles} artigos com texto importado (vigência não verificada)` :
      'Referência catalogada. Texto integral ainda não importado.';
    content.append(el('small','catalog-hint',status));
    node.append(content);
    const actions=el('div','catalog-actions');
    const local=el('button','text-link','Ver nesta biblioteca');local.type='button';
    local.addEventListener('click',()=>showLaw(law.slug));
    actions.append(local,sourceLink(law,law.reference_kind==='existing_direct_link_unverified' ? 'Abrir página cadastrada' : 'Pesquisar em fonte oficial')); node.append(actions);
    return node;
  }

  function showTopic(topic) {
    const id=++requestId;
    activeLaw=null;activeQuery='';nextOffset=null;
    $('#load-more').hidden=true;
    setResultHeader(topic.title,topic.question);
    const surface=$('#result-surface');surface.replaceChildren();
    const lead=el('div','topic-detail');
    lead.append(el('strong','','POR ONDE COMEÇAR'),el('p','',topic.orientation),
                el('p','topic-caveat','As regras podem ter condições, exceções e alterações. Confira a versão oficial de cada norma.'));
    surface.append(lead);
    let found=0;
    for(const slug of topic.laws){
      const law=laws.find(l=>l.slug===slug);
      if(law){surface.append(lawReference(law));found++;}
    }
    if(!found)surface.append(el('p','','Não há normas correspondentes neste catálogo.'));
    showResultsSection();
  }

  async function updateCaptureStatus() {
    try {
      const info = await getJSON('/api/status');
      $('#capture-message').textContent = `${info.laws_with_captures} de ${info.catalog_laws} normas já tiveram texto importado; ${info.imported} artigos com texto extraído, ${info.reviewed} explicações revisadas, ${info.stale} aguardando nova revisão; ${info.pending_captures} captura(s) aguardam validação e não aparecem na busca. Nenhuma captura certifica vigência jurídica.`;
    } catch (_) {
      $('#capture-message').textContent = 'Estado das importações indisponível. Consulte sempre a fonte oficial.';
    }
  }

  async function updateCoverageProgress() {
    try {
      const data = await getJSON('/api/coverage-progress');
      $('#coverage-catalog').textContent = nf.format(data.summary.cataloged);
      $('#coverage-private').textContent = nf.format(data.summary.private_captures_pending);
      $('#coverage-published').textContent = nf.format(data.summary.articles_published);
      $('#coverage-legal').textContent = nf.format(data.summary.independently_verified_legal_opinions);
    } catch (_) {
      $('#coverage-caution').textContent = 'Cobertura detalhada indisponível. Nenhuma certificação de vigência foi estabelecida neste portal.';
    }
  }

  function updateStats() {
    $('#metric-laws').textContent = nf.format(laws.length);
    $('#metric-articles').textContent = nf.format(laws.reduce((a,l)=>a+l.visible_articles,0));
    $('#metric-guides').textContent = nf.format(laws.reduce((a,l)=>a+l.easy_articles,0));
  }

  function setResultHeader(title,description) {
    $('#results-heading').textContent = title;
    $('#results-description').textContent = description;
    $('#clear-results').hidden = false;
  }

  function showResultsSection() {
    $('#results-heading').focus({preventScroll:true});
    $('#resultados').scrollIntoView({block:'start'});
  }

  function addResult(item) {
    const node = el('article','article-result');node.tabIndex = 0; node.setAttribute('role','button');
    const info=el('div','result-info');
    info.append(el('span','result-tag',item.law_short.toUpperCase() + '  /  '+item.label));
    const statusText=item.state==='reviewed' ? 'Explicação revisada' : item.state==='illustrative' ? 'Explicação preliminar' : 'Texto legal extraído, não verificado quanto à vigência';
    const captured=item.last_sync ? ' · Capturado em '+new Date(item.last_sync).toLocaleDateString('pt-BR') : '';
    info.append(el('span','result-evidence',statusText+captured));
    info.append(el('h3','result-title',item.heading || item.label));
    const preview = item.state==='stale' ? '' : (item.simple || item.original || 'Abra para consultar o artigo.');
    info.append(el('p','result-text',preview.length>225 ? preview.slice(0,223)+'…' : preview));
    node.append(info,el('span','result-more','Ler'));
    node.addEventListener('click',()=>openArticle(item.law_slug,item.article_key));
    node.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openArticle(item.law_slug,item.article_key);}});
    return node;
  }

  function displayResults(items,subtitle,append=false) {
    const surface = $('#result-surface');
    if(!append) surface.replaceChildren();
    if(!items.length && !append) {
      surface.append(el('p','results-empty-small','Nenhum artigo encontrado na base disponível. Experimente palavras diferentes ou abra a fonte oficial da lei.'));
      return;
    }
    if(!append) surface.append(el('div','results-status',subtitle));
    const fragment=document.createDocumentFragment();
    items.forEach(item=>fragment.append(addResult(item)));
    surface.append(fragment);
  }

  async function search(query) {
    const q=query.trim();activeQuery=q;activeLaw=null;nextOffset=null;
    $('#load-more').hidden=true;
    $('#search-input').value=q;
    if(!q){clearResults();return;}
    const id=++requestId;
    setResultHeader('Resultados da busca',`Pesquisando “${q}” no acervo e no catálogo.`);
    $('#result-surface').replaceChildren(el('p','loading-state','Pesquisando…'));
    showResultsSection();
    try {
      const params=new URLSearchParams({q,limit:'40',explanation:$('#search-explanations').value});
      const law=$('#search-law').value;
      if(law)params.set('law',law);
      const [data,discovery]=await Promise.all([
        getJSON('/api/search?'+params), getJSON('/api/discover?'+new URLSearchParams({q,limit:'12'}))]);
      if(id!==requestId)return;
      setResultHeader('Resultados da busca',`${nf.format(data.count)} artigo(s) locais e ${nf.format(discovery.catalog_matches.length)} norma(s) relacionadas no catálogo. A pesquisa não cobre todas as leis do Brasil.`);
      const surface=$('#result-surface');surface.replaceChildren();
      const warning=el('div','search-scope-notice');
      warning.append(el('strong','','Atenção à abrangência'),el('p','',
         'Esta busca cobre uma seleção de normas e artigos disponíveis nesta instalação. Nenhum resultado não significa inexistência de direito ou dever.'));surface.append(warning);
      if(data.results.length){
        const heading=el('h3','result-subhead','Artigos disponíveis neste portal');surface.append(heading);
        const how=data.match_mode==='article_exact' ? 'Número de artigo encontrado.' :
          data.match_mode==='spelling_suggestion' ? 'Termos próximos: '+data.suggested_query :
          data.match_mode==='lexical_or' ? 'Busca ampliada por palavras.' : 'Busca textual.';
        surface.append(el('p','results-status',`${data.count} resultado(s). ${how} A extração não garante vigência.`));
        for(const hit of data.results)surface.append(addResult(hit));
      }
      if(discovery.catalog_matches.length){
        surface.append(el('h3','result-subhead','Normas relacionadas à sua pesquisa'));
        for(const item of discovery.catalog_matches)surface.append(lawReference(item));
      }
      if(discovery.topics.length){
        surface.append(el('h3','result-subhead','Assuntos que podem ajudar'));
        const suggestions=el('div','related-topics');
        for(const topic of discovery.topics){
          const button=el('button','related-topic',topic.title);button.type='button';
          button.addEventListener('click',()=>showTopic(topic));suggestions.append(button);
        }
        surface.append(suggestions);
      }
      if(!data.results.length&&!discovery.catalog_matches.length){
        surface.append(el('p','results-empty-small','Não há correspondência neste acervo. Pesquise também nas bases oficiais abaixo.'));
      }
      const lookup=el('div','search-portals');
      lookup.append(el('h3','','Pesquise além deste portal'),el('p','',
        'Busque por assunto, número e ano da norma nos sites públicos. Estes links abrem a pesquisa institucional, não resultados previamente verificados.'));
      for(const source of discovery.official_portals.slice(0,3)){
        const a=el('a','official-discover',source.title);a.href=source.url;
        a.target='_blank';a.rel='noopener noreferrer';lookup.append(a);
      }
      surface.append(lookup);
    }catch(e){
      if(id!==requestId)return;
      $('#results-description').textContent=e.message;
      $('#result-surface').replaceChildren(el('p','results-empty-small',e.message));
    }
  }

  async function showLaw(slug) {
    const law=laws.find(x=>x.slug===slug);if(!law)return;
    activeLaw=slug;activeQuery='';$('#search-input').value='';
    const id=++requestId;
    const reference=law.title + ' · '+law.number;
    setResultHeader(law.short,`${reference}. Carregando artigos…`);
    $('#result-surface').replaceChildren(el('p','loading-state','Carregando artigos…'));
    showResultsSection();
    try {
      const data=await getJSON('/api/laws/'+encodeURIComponent(slug)+'/articles?limit=30');
      if(id!==requestId)return;
      nextOffset=data.next_offset;
      $('#results-description').textContent=`${reference}. ${nf.format(data.total)} artigo(s) nesta base.`;
      displayResults(data.articles,`${nf.format(data.total)} artigo(s) nesta base. ${law.imported_count ? 'Texto importado; confira sempre a fonte oficial.' : 'Texto completo ainda não foi importado nesta instalação.'}`);
      const brief=el('section','law-brief');
      const heading=el('h3','','Entenda esta norma');
      const simple=el('p','law-brief-summary',law.description);
      const how=el('p','law-brief-usage',law.plain_use || 'Consulte os assuntos tratados por esta norma e verifique o texto na fonte oficial.');
      const sourceKind=law.reference_kind==='existing_direct_link_unverified' ?
        'O link aponta para uma página legislativa cadastrada, cuja disponibilidade e vigência ainda precisam ser conferidas.' :
        'O link abre uma página de pesquisa institucional. Informe o número e o ano da norma.';
      const evidence=el('p','law-brief-caution',`Esta é uma orientação preliminar, não uma análise jurídica revisada. ${sourceKind}`);
      const counts=el('div','law-brief-stats');
      counts.append(el('span','','Texto integral validado nesta instalação: não'),
                    el('span','',`${nf.format(law.reviewed_articles || 0)} explicação(ões) com revisão editorial vinculada ao texto`));
      brief.append(heading,simple,how,evidence,counts);
      $('#result-surface').prepend(brief);
      const guidance=el('div','source-while-pending');
      guidance.append(el('p','',law.imported_count ? 'Para confirmar a redação e a vigência, confira o texto na fonte oficial.' : 'Esta norma está catalogada, mas não possui artigos importados aqui. O link oficial permite continuar a pesquisa.'));
      guidance.append(sourceLink(law,'Consultar texto ou referência oficial'));
      $('#result-surface').append(guidance);
      $('#load-more').hidden = nextOffset===null;
    } catch(e) {
      if(id!==requestId)return;
      $('#results-description').textContent=e.message;
      $('#result-surface').replaceChildren(el('p','results-empty-small',e.message));
    }
  }

  async function moreLawArticles() {
    if(activeLaw===null||nextOffset===null)return;
    const slug=activeLaw,offset=nextOffset;
    const button=$('#load-more');button.disabled=true;
    try {
      const data=await getJSON(`/api/laws/${encodeURIComponent(slug)}/articles?offset=${offset}&limit=30`);
      if(activeLaw!==slug)return;
      displayResults(data.articles,'',true);nextOffset=data.next_offset;
      button.hidden = nextOffset===null;
    }catch(e){toast(e.message);}finally{button.disabled=false;}
  }

  function clearResults() {
    ++requestId;activeLaw=null;activeQuery='';nextOffset=null;
    $('#search-input').value='';
    $('#search-input').focus();
    $('#load-more').hidden=true;$('#clear-results').hidden=true;
    $('#results-heading').textContent='Aprenda na prática.';
    $('#results-description').textContent='Comece com um assunto ou escolha uma lei para ver seus artigos.';
    const surface=$('#result-surface');surface.replaceChildren();
    const empty=el('div','empty-results');
    empty.append(el('div','empty-art','?'),el('h3','', 'Uma pergunta pode mudar muita coisa.'),
      el('p','','Pesquise pelo que aconteceu com você, por um tema ou pelo número de um artigo.'));
    const links=el('div','empty-examples');
    [['garantia','Garantia de produto'],['dados','Meus dados pessoais'],['prioridade','Atendimento prioritário']].forEach(([q,name])=>{
      const btn=el('button','example-link',name);btn.type='button';btn.addEventListener('click',()=>search(q));links.append(btn);
    });empty.append(links);surface.append(empty);
  }

  async function openArticle(slug,key) {
    try {
      const article=await getJSON(`/api/articles/${encodeURIComponent(slug)}/${encodeURIComponent(key)}`);
      $('#reader-category').textContent=article.category.toUpperCase();
      $('#reader-law-number').textContent=article.number;
      $('#dialog-title').textContent=article.heading ? article.label+' — '+article.heading : article.label;
      $('#reader-law-name').textContent=article.law_title;
      let banner='';let summary=article.simple;
      if(article.state==='reviewed') banner='Explicação vinculada ao texto importado e marcada como revisada por '+article.reviewed_by+'. Confira sempre a norma oficial.';
      else if(article.state==='stale') {banner='O texto original mudou desde a última revisão. A explicação anterior foi ocultada até nova verificação.';summary='Esta explicação precisa ser atualizada. Leia o texto legal e consulte a fonte oficial.';}
      else if(article.state==='illustrative') banner='Explicação educativa preliminar, ainda sem revisão jurídica. A transcrição integral pode depender de sincronização.';
      else banner='Ainda não há uma versão simplificada revisada deste artigo.';
      if(article.article_capture_at) banner += ' Capturado em '+new Date(article.article_capture_at).toLocaleDateString('pt-BR')+'.';
      $('#reader-state').textContent=banner;
      const acquisitionText={
        network:'HTML obtido pela conexão HTTPS com o domínio do Planalto',
        local_file:'HTML fornecido manualmente; origem dos bytes não verificada automaticamente',
        unverified_legacy:'origem da captura anterior não verificada',
        not_imported:'nenhum texto importado'}[article.acquisition] || 'origem não verificada';
      const partialWarning=article.release_scope==='single_article' ? ' Publicação restrita a este artigo; outros dispositivos da norma não foram liberados com ele.' : '';
      $('#reader-provenance').textContent = article.article_capture_at ?
        `Registro: ${new Date(article.article_capture_at).toLocaleString('pt-BR')}. Proveniência: ${acquisitionText}.${partialWarning} A publicação não certifica vigência, completude ou revisão jurídica.` :
        'O texto completo ainda não foi importado e aprovado nesta instalação. Utilize a fonte oficial para conferir a norma.';
      setReadingView('compare');
      const details=$('#reader-details');
      details.hidden=!article.editorial;
      const fields=$('#reader-detail-fields');fields.replaceChildren();
      if(article.editorial){
        const ed=article.editorial;
        [['Para quem vale',ed.applies_to],['Condições',ed.conditions],['Exceções e limites',ed.exceptions],['Prazos',ed.deadlines],['Outras normas relacionadas',ed.related_norms],['Próximos passos',ed.next_steps],['Referências',ed.references.join(' · ')]].forEach(([name,value])=>{
          if(!value)return;
          const box=el('div','detail-entry');box.append(el('dt','',name),el('dd','',value));fields.append(box);
        });
        if(Array.isArray(ed.evidence) && ed.evidence.length){
          const excerpts=ed.evidence.slice(0,6).map(x=>x.quote).filter(Boolean).join(' · ');
          if(excerpts){
            const box=el('div','detail-entry');
            box.append(el('dt','','Trechos do artigo usados para conferência'),el('dd','',excerpts));
            fields.append(box);
          }
        }
        $('#reader-detail-review').textContent=`Público: ${ed.audience} · Revisão editorial registrada em ${new Date(ed.reviewed_at).toLocaleDateString('pt-BR')} · ${ed.reviewer}. Não certifica vigência jurídica.`;
      }
      $('#history-content').hidden = true;
      $('#history-trigger').setAttribute('aria-expanded','false');
      $('#history-items').replaceChildren();
      $('#history-diff').hidden=true;
      $('#history-trigger').dataset.law=slug;
      $('#history-trigger').dataset.article=key;
      readingLevels=article.editorial?.reading_levels || null;
      $('#reader-level-switch').hidden=!readingLevels;
      document.querySelectorAll('[data-reader-level]').forEach(btn=>{
        btn.hidden=!readingLevels || !readingLevels[btn.dataset.readerLevel];
        const selected=btn.dataset.readerLevel==='adults_basic';
        btn.setAttribute('aria-pressed',String(selected));
        btn.classList.toggle('is-active',selected);
      });
      $('#reader-simple').textContent=readingLevels?.adults_basic || summary || 'Uma explicação em linguagem simples ainda não está disponível. Para conhecer a regra, consulte o texto original e a fonte oficial.';
      const example=$('#reader-example');
      example.hidden=!article.example || article.state==='stale';
      example.querySelector('p').textContent=example.hidden?'':article.example;
      $('#reader-original').textContent=article.original || 'O texto integral ainda não foi sincronizado neste portal. Abra a fonte oficial para ler o artigo.';
      $('#reader-source').href=article.source_url;
      $('#listen-button').disabled=!article.original;
      const url=new URL(window.location.href);url.searchParams.set('lei',slug);url.searchParams.set('art',key);
      history.replaceState({},'',url.pathname+url.search);
      $('#article-dialog').showModal();
      $('#share-article').onclick=()=>share(url.href);
    }catch(e){toast(e.message);}
  }
  async function showHistory() {
    const btn = $('#history-trigger');
    const panel = $('#history-content');
    if (!panel.hidden) {
      panel.hidden=true;
      btn.setAttribute('aria-expanded','false');
      btn.textContent='Ver histórico deste artigo ↓';
      return;
    }
    btn.setAttribute('aria-expanded','true');
    btn.textContent='Ocultar histórico ↑';
    panel.hidden=false;
    const container=$('#history-items');
    container.replaceChildren(el('p','loading-state','Buscando as capturas anteriores…'));
    const slug=btn.dataset.law, key=btn.dataset.article;
    try {
      const result=await getJSON(`/api/articles/${encodeURIComponent(slug)}/${encodeURIComponent(key)}/history`);
      if (btn.dataset.law!==slug || btn.dataset.article!==key || panel.hidden)return;
      const versions=result.versions;
      container.replaceChildren();
      if (!versions.length) {
        container.append(el('p','history-note','Não há histórico ainda. As versões começam a ser registradas quando uma lei é sincronizada na v0.2.0.'));
        return;
      }
      const titles={added:'Presente na captura',modified:'Texto extraído diferente',removed:'Ausente nesta captura'};
      for (const v of versions) {
        const row=el('div','history-entry');
        row.append(el('strong','',titles[v.change_kind]||v.change_kind));
        row.append(el('span','',`${new Date(v.recorded_at).toLocaleString('pt-BR')} · registro #${v.id}`));
        container.append(row);
      }
      if (versions.length>1) {
        const compare=el('button','history-trigger','Comparar as duas últimas versões ↑↓');
        compare.type='button';
        compare.addEventListener('click',()=>showDiff(slug,key,versions[1].id,versions[0].id));
        container.append(compare);
      }
    }catch(e){container.replaceChildren(el('p','history-note',e.message));}
  }

  async function showDiff(slug,key,older,newer) {
    const pre=$('#history-diff');
    pre.hidden=false;pre.textContent='Preparando comparação…';
    try {
      const diff=await getJSON(`/api/articles/${encodeURIComponent(slug)}/${encodeURIComponent(key)}/diff?${new URLSearchParams({older,newer})}`);
      if($('#history-trigger').dataset.law!==slug || $('#history-trigger').dataset.article!==key)return;
      pre.textContent=diff.patch || 'Nenhuma diferença de texto encontrada.';
    }catch(e){pre.textContent=e.message;}
  }

  function setReadingView(view) {
    if(!['compare','simple','original'].includes(view))return;
    $('#reading-pair').dataset.view=view;
    document.querySelectorAll('[data-reading-view]').forEach(btn=>{
      const selected=btn.dataset.readingView===view;
      btn.classList.toggle('is-active',selected);
      btn.setAttribute('aria-pressed',String(selected));
    });
  }

  function closeArticle() {
    if(window.speechSynthesis)window.speechSynthesis.cancel();speaking=false;$('#listen-button').textContent='Ouvir';
    $('#article-dialog').close();
  }
  async function share(url) {
    try {
      await navigator.clipboard.writeText(url);
      toast('Link do artigo copiado.');
    } catch {toast('Copie o endereço exibido no navegador para compartilhar.');}
  }
  function playText(){
    const btn=$('#listen-button');
    if(!('speechSynthesis' in window)){toast('Seu navegador não oferece leitura por voz.');return;}
    if(speaking){speechSynthesis.cancel();speaking=false;btn.textContent='Ouvir';return;}
    const utterance = new SpeechSynthesisUtterance($('#reader-original').textContent.slice(0,10000));
    utterance.lang='pt-BR';utterance.rate=0.94;
    utterance.onend=utterance.onerror=()=>{speaking=false;btn.textContent='Ouvir';};
    speaking=true;btn.textContent='Parar áudio';speechSynthesis.speak(utterance);
  }

  async function init(){
    themeInit();
    $('#search-form').addEventListener('submit',event=>{event.preventDefault();search($('#search-input').value);});
    document.querySelectorAll('[data-query]').forEach(btn=>btn.addEventListener('click',()=>search(btn.dataset.query)));
    $('#clear-results').addEventListener('click',clearResults);
    $('#show-all').addEventListener('click',()=>{if(selectedCategory!=='Todas'){expandCatalog=true;chooseCategory('Todas');}else{expandCatalog=!expandCatalog;renderLaws();}});
    $('#load-more').addEventListener('click',moreLawArticles);
    $('#dialog-close').addEventListener('click',closeArticle);
    $('#article-dialog').addEventListener('close',()=>{
      if(window.speechSynthesis)window.speechSynthesis.cancel();
      const url=new URL(location.href);url.searchParams.delete('lei');url.searchParams.delete('art');
      history.replaceState({},'',url.pathname+url.search);
    });
    $('#font-down').addEventListener('click',()=>{readerFont=Math.max(12,readerFont-1);$('.reader-scroll').style.setProperty('--reader-size',readerFont+'px');});
    $('#font-up').addEventListener('click',()=>{readerFont=Math.min(22,readerFont+1);$('.reader-scroll').style.setProperty('--reader-size',readerFont+'px');});
    $('#listen-button').addEventListener('click',playText);
    $('#history-trigger').addEventListener('click',showHistory);
    document.querySelectorAll('[data-reading-view]').forEach(btn=>btn.addEventListener('click',()=>setReadingView(btn.dataset.readingView)));
    document.querySelectorAll('[data-reader-level]').forEach(btn=>btn.addEventListener('click',()=>{
      const level=btn.dataset.readerLevel;
      if(!readingLevels || !Object.hasOwn(readingLevels,level))return;
      $('#reader-simple').textContent=readingLevels[level];
      document.querySelectorAll('[data-reader-level]').forEach(other=>{
        const active=other.dataset.readerLevel===level;
        other.setAttribute('aria-pressed',String(active));
        other.classList.toggle('is-active',active);
      });
    }));
    $('#print-article').addEventListener('click',()=>window.print());
    document.addEventListener('keydown',event=>{
      const editing=document.activeElement.matches('input, textarea, select, [contenteditable]:not([contenteditable="false"])');
      if(event.key==='/' && event.altKey && !event.ctrlKey && !event.metaKey && !event.isComposing && !editing && !$('#article-dialog').open){
        event.preventDefault();$('#search-input').focus();
      }
    });
    try {
      const data=await getJSON('/api/laws');laws=data.laws;
      const searchLaw=$('#search-law');
      laws.forEach(law=>{const option=el('option','',law.short);option.value=law.slug;searchLaw.append(option);});
      const [t,p,n]=await Promise.all([getJSON('/api/topics'),getJSON('/api/portals'),getJSON('/api/norm-types')]);
      topics=t.topics;officialPortals=p.portals;normTypes=n.types;
      initCategories();renderLaws();renderTopics();renderPortals();renderNormTypes();updateStats();updateCaptureStatus();updateCoverageProgress();
      const url=new URL(location.href);
      if(url.searchParams.get('lei')&&url.searchParams.get('art')) {
        await openArticle(url.searchParams.get('lei'),url.searchParams.get('art'));
      }
    } catch(e) {
      $('#law-grid').replaceChildren(el('p','loading-state','Não foi possível carregar os dados. Verifique se o servidor está em execução.'));
      $('#side-categories').textContent='Catálogo temporariamente indisponível.';
      toast('Falha ao carregar o catálogo.');
    }
  }
  init();
})();
