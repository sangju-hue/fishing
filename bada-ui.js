(() => {
  window.badaTheme.refresh();
  document.querySelectorAll('button[data-theme]').forEach(button => button.addEventListener('click', () => window.badaTheme.set(button.dataset.theme)));
  document.querySelectorAll('[data-alert-link]').forEach(link => link.addEventListener('click', () => {
    document.getElementById('ntfySettings').open = true;
  }));
  document.querySelectorAll('[data-reservation-link]').forEach(link => link.addEventListener('click', () => selectSpecies(document.getElementById('tab-jjukkumi'))));
  const title = document.getElementById('summaryTitle'), state = document.getElementById('summaryState'), detail = document.getElementById('summaryDetail');
  const summary = document.querySelector('.collection-summary');
  let collectorConnected = false;
  function showSnapshot() {
    if (collectorConnected) return;
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
    state.textContent = s.state;
    detail.textContent = [s.step, s.elapsed].filter(Boolean).join(' · ');
    summary.classList.toggle('is-running', s.running);
    // The collector reports a step, not a measurable percentage. Show activity only.
    document.querySelector('.summary-track').hidden = !s.running;
  });
  const settings = document.getElementById('summarySettings');
  settings.hidden = !['localhost', '127.0.0.1'].includes(location.hostname);
  settings.addEventListener('click', () => {
    document.getElementById('scrapeSettings').scrollIntoView({ block: 'start' });
    if (document.getElementById('collectionSettingsGrid').hidden) document.getElementById('collectionToggle').click();
    document.getElementById('collectionToggle').focus({ preventScroll: true });
  });
  const dialog = document.getElementById('guideDialog');
  document.getElementById('operatorGuide').addEventListener('click', () => {
    const list = document.getElementById('guideList');
    list.replaceChildren();
    BOATS.slice().sort((a,b) => comparePorts(a,b) || a.name.localeCompare(b.name,'ko')).forEach(boat => {
      const row = document.createElement('article'); row.className = 'directory-item';
      const copy = document.createElement('div');
      const name = document.createElement('strong'); name.textContent = boat.name;
      const meta = document.createElement('p'); meta.className = 'directory-meta'; meta.textContent = [boat.region, normalizePort(boat.port), operatorName(boat)].filter(Boolean).join(' · ');
      copy.append(name, meta);
      const actions = document.createElement('div'); actions.className = 'directory-actions'; actions.innerHTML = channelButtons(boat);
      row.append(copy, actions); list.append(row);
    });
    if (!BOATS.length) list.textContent = '선사 정보를 불러오는 중입니다. 잠시 후 다시 확인해 주세요.';
    dialog.showModal();
  });
  document.getElementById('guideClose').addEventListener('click', () => dialog.close());
})();
