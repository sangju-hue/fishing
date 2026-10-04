(() => {
  window.badaTheme.refresh();
  document.querySelectorAll('button[data-theme]').forEach(button => button.addEventListener('click', () => window.badaTheme.set(button.dataset.theme)));
  const themeDialog=document.getElementById('themeSettingsDialog');
  document.getElementById('themeSettingsOpen').addEventListener('click',()=>themeDialog.showModal());
  document.getElementById('themeSettingsClose').addEventListener('click',()=>themeDialog.close());
  document.querySelectorAll('[data-alert-link]').forEach(link => link.addEventListener('click', () => {
    document.getElementById('ntfySettings').open = true;
  }));
  document.querySelectorAll('[data-reservation-link],a[href="#reservation"]').forEach(link => link.addEventListener('click', () => selectSpecies(document.getElementById('tab-jjukkumi'))));
  const title = document.getElementById('summaryTitle'), state = document.getElementById('summaryState'), detail = document.getElementById('summaryDetail');
  const summary = document.querySelector('.collection-summary');
  let collectorConnected = false;
  let activityDetail = '';
  function updateDetail() {
    detail.textContent = [activityDetail, document.querySelector('.snapshot strong').textContent].filter(Boolean).join(' · ');
  }
  function showSnapshot() {
    if (collectorConnected) { updateDetail(); return; }
    const text = document.querySelector('.snapshot strong').textContent;
    const failed = text.includes('실패');
    title.textContent = failed ? '연결을 다시 확인하고 있어요' : '최근 예약 정보를 확인하세요';
    state.textContent = failed ? '재시도 중' : text.includes('불러오는') ? '확인 중' : '최근 현황';
    detail.textContent = text;
  }
  new MutationObserver(showSnapshot).observe(document.querySelector('.snapshot strong'), { childList: true, characterData: true, subtree: true });
  showSnapshot();
  document.addEventListener('bada:collector', event => {
    collectorConnected = true;
    const s = event.detail;
    title.textContent = s.running ? '예약 정보를 업데이트하고 있어요' : '예약 정보 수집 상태';
    state.textContent = s.running ? '수집 중' : /대기/.test(s.state) ? '대기' : /중지/.test(s.state) ? '중지' : s.state;
    activityDetail = [s.step, s.elapsed].filter(Boolean).join(' · ');
    updateDetail();
    summary.classList.toggle('is-running', s.running);
  });
  if (!['localhost', '127.0.0.1'].includes(location.hostname)) {
    let stream = null, expiryTimer = null, latest = 0;
    function unknown() {
      document.dispatchEvent(new CustomEvent('bada:collector', {detail:{running:false,state:'상태 확인 불가',step:'맥 수집기 연결을 확인하고 있습니다'}}));
    }
    function receive(raw) {
      try {
        const envelope=JSON.parse(raw);if(envelope.event!=='message')return;
        const s=JSON.parse(envelope.message),at=Date.parse(s.updated_at),age=Date.now()-at;
        if(!['running','waiting','stopped'].includes(s.state)||!Number.isFinite(at)||at<latest||age< -30000)return;
        if(age>150000){unknown();return;}
        latest=at;clearTimeout(expiryTimer);
        document.dispatchEvent(new CustomEvent('bada:collector',{detail:{running:s.state==='running',state:{running:'수집 중',waiting:'대기',stopped:'중지'}[s.state],step:s.detail}}));
        expiryTimer=setTimeout(unknown,150000-Math.max(0,age));
      } catch(e) {}
    }
    function connect(config) {
      if(stream||!config?.server||!config?.inbox)return;
      stream=new EventSource(`${config.server}/${config.inbox}-collector/sse?since=latest`);
      stream.onmessage=e=>receive(e.data);
      stream.onerror=()=>{clearTimeout(expiryTimer);unknown();};
    }
    unknown();
    document.addEventListener('bada:ntfy-config',e=>connect(e.detail));
    if(window.badaNtfyConfig)connect(window.badaNtfyConfig);
  }
  const settings = document.getElementById('summarySettings');
  settings.hidden = !['localhost', '127.0.0.1'].includes(location.hostname);
  settings.addEventListener('click', () => {
    document.getElementById('scrapeSettings').scrollIntoView({ block: 'start' });
    if (document.getElementById('collectionSettingsGrid').hidden) document.getElementById('collectionToggle').click();
    document.getElementById('collectionToggle').focus({ preventScroll: true });
  });
  const dialog = document.getElementById('guideDialog');
  const guideSearch = document.getElementById('guideSearch');
  const guideCount = document.getElementById('guideCount');
  let guideRows = [];
  const searchText = value => String(value || '').normalize('NFKC').toLocaleLowerCase('ko').replace(/\s+/g,'');
  function filterGuide() {
    const query=searchText(guideSearch.value);let count=0;
    guideRows.forEach(({row,text})=>{row.hidden=!text.includes(query);if(!row.hidden)count++;});
    guideCount.textContent=BOATS.length ? count ? `${count}척 표시 · 전체 ${guideRows.length}척` : '검색 결과가 없습니다.' : '선사 정보를 불러오는 중입니다.';
  }
  guideSearch.addEventListener('input',filterGuide);
  document.querySelectorAll('[data-guide-open]').forEach(button=>button.addEventListener('click', () => {
    const list = document.getElementById('guideList');
    list.replaceChildren();
    guideRows=[];guideSearch.value='';
    BOATS.slice().sort((a,b) => comparePorts(a,b) || a.name.localeCompare(b.name,'ko')).forEach(boat => {
      const row = document.createElement('article'); row.className = 'directory-item';
      const copy = document.createElement('div');
      const name = document.createElement('strong'); name.textContent = boat.name;
      const meta = document.createElement('p'); meta.className = 'directory-meta'; meta.textContent = [boat.region, normalizePort(boat.port), operatorName(boat)].filter(Boolean).join(' · ');
      copy.append(name, meta);
      const actions = document.createElement('div'); actions.className = 'directory-actions'; actions.innerHTML = channelButtons(boat);
      row.append(copy, actions); list.append(row);
      guideRows.push({row,text:searchText([boat.name,operatorName(boat)].join(' '))});
    });
    if (!BOATS.length) list.textContent = '선사 정보를 불러오는 중입니다. 잠시 후 다시 확인해 주세요.';
    filterGuide();
    dialog.showModal();
    guideSearch.focus({preventScroll:true});
  }));
  document.getElementById('guideClose').addEventListener('click', () => dialog.close());
})();
