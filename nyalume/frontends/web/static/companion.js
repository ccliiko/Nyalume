/* Companion shell. Reuses the existing chat actions and API boundary. */
window.NyalumeUI = (() => {
  const $ = id => document.getElementById(id);
  let draftSession = null;
  let focusData = null;
  let focusOffset = 0;
  let focusPending = false;
  let focusLoading = false;
  let focusVersion = 0;
  let focusSignature = '';
  let noticeTimer;
  let storageWarning = false;
  let previousSidebarFocus;

  function notice(message) {
    $('ui-notice').textContent = message;
    $('ui-notice').hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { $('ui-notice').hidden = true; }, 5500);
  }

  function saveDraft() {
    if (!draftSession) return;
    try {
      const key = 'nyalume:draft:' + draftSession;
      if ($('input').value) localStorage.setItem(key, $('input').value);
      else localStorage.removeItem(key);
      $('draft-status').textContent = $('input').value ? '草稿已保存在此浏览器' : '草稿仅存于当前浏览器';
    } catch (_) {
      $('draft-status').textContent = '草稿未保存 · 浏览器存储不可用';
      if (!storageWarning) { notice('浏览器存储不可用，请在离开前复制输入内容。'); storageWarning = true; }
    }
  }

  function sessionChanged(id) {
    if (id !== draftSession) {
      saveDraft();
      draftSession = id;
      try { $('input').value = localStorage.getItem('nyalume:draft:' + id) || ''; }
      catch (_) { $('input').value = ''; }
      resizeInput();
      $('draft-status').textContent = $('input').value ? '已恢复这段对话的草稿' : '草稿仅存于当前浏览器';
    }
    showChat(false);
    closeSidebar();
  }

  function forgetDraft(id) {
    if (draftSession === id) { draftSession = null; $('input').value = ''; resizeInput(); }
    try { localStorage.removeItem('nyalume:draft:' + id); }
    catch (_) { notice('对话已删除，但浏览器草稿未能清除。可在浏览器设置中清除此站点数据。'); }
  }

  function showHome() {
    if (document.body.classList.contains('quick-chat')) return;
    saveDraft();
    document.body.classList.add('home-open');
    $('home-panel').hidden = false;
    $('home-nav').setAttribute('aria-current', 'page');
    $('chat-nav').removeAttribute('aria-current');
    closeSidebar();
    refreshFocus();
  }

  function showChat(focusInput = true) {
    document.body.classList.remove('home-open');
    $('home-panel').hidden = true;
    $('chat-nav').setAttribute('aria-current', 'page');
    $('home-nav').removeAttribute('aria-current');
    closeSidebar();
    if (focusInput) { resizeInput(); $('input').focus(); }
  }

  function closeSidebar() {
    const wasOpen = document.body.classList.contains('sidebar-open');
    document.body.classList.remove('sidebar-open');
    $('sidebar-toggle').setAttribute('aria-expanded', 'false');
    if (wasOpen && previousSidebarFocus?.isConnected) previousSidebarFocus.focus();
  }

  async function refreshFocus() {
    if (focusPending || focusLoading) return;
    focusLoading = true;
    const version = focusVersion;
    try {
      const data = await api('/api/focus');
      if (version !== focusVersion) return;
      acceptFocus(data);
      $('focus-feedback').textContent = '';
      $('focus-retry').hidden = true;
    } catch (error) {
      if (version !== focusVersion) return;
      $('focus-feedback').textContent = '专注记录暂时无法读取，请重试。' + error.message;
      $('focus-retry').hidden = false;
      if (!focusData) $('focus-history').textContent = '连接恢复后，你的足迹会显示在这里。';
    } finally { focusLoading = false; }
  }

  function acceptFocus(data) {
    if (!data || !Array.isArray(data.recent) || !data.today || !Number.isFinite(data.server_time)) {
      throw new Error('返回的记录格式不正确');
    }
    focusData = data;
    focusOffset = data.server_time * 1000 - Date.now();
    renderFocus();
  }

  async function changeFocus(action, body = {}) {
    if (focusPending) return;
    focusVersion++;
    focusPending = true;
    $('focus-card').setAttribute('aria-busy', 'true');
    $('focus-card').querySelectorAll('button').forEach(b => { b.disabled = true; });
    try {
      const path = action === 'start' ? '/api/focus' : '/api/focus/' + focusData.current.id;
      const data = await api(path, {
        method: action === 'start' ? 'POST' : 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(action === 'start' ? body : { action, ...body }),
      });
      acceptFocus(data);
      $('focus-feedback').textContent = '';
      $('focus-retry').hidden = true;
      if (action === 'complete') {
        $('focus-note').value = '';
        $('focus-goal').value = '';
        notice(body.outcome === 'done' ? '这件小事完成了。已经为你留下一页记录。' : '前进了一点，也值得记下来。下次接着来。');
      }
    } catch (error) {
      $('focus-feedback').textContent = error.message + '。可重新加载最新状态后重试。';
      $('focus-retry').hidden = false;
    } finally {
      focusPending = false;
      $('focus-card').removeAttribute('aria-busy');
      $('focus-card').querySelectorAll('button').forEach(b => { b.disabled = false; });
    }
  }

  function renderFocus() {
    const current = focusData.current;
    const signature = current ? current.id + ':' + current.status : 'none';
    const changed = signature !== focusSignature;
    const previous = focusSignature;
    focusSignature = signature;
    if (previous.split(':')[0] !== signature.split(':')[0]) $('focus-note').value = '';
    $('focus-start-form').hidden = !!current;
    $('focus-running').hidden = !current || current.status === 'ready';
    $('focus-review').hidden = !current || current.status !== 'ready';
    $('focus-chip').hidden = !current;
    $('focus-intro').textContent = !current ? '这一小段时间，就交给我们吧。我会安静陪着你。'
      : current.status === 'ready' ? '辛苦啦。任务有没有完成，由你来告诉我。'
      : current.status === 'paused' ? '休息一下喵。等你准备好，我们再继续。' : '现在只管眼前这一件，我替你守着这段安静。';
    $('focus-heading').textContent = current?.status === 'ready' ? '今天的努力，想替你记下来。' : '这次的小任务，陪你一起。';
    if (current) {
      $('focus-goal-display').textContent = current.goal;
      $('focus-review-goal').textContent = '这次的小目标：' + current.goal;
      $('focus-pause').textContent = current.status === 'paused' ? '继续专注' : '暂停一下';
      if (changed && current.status === 'ready' && previous.endsWith(':active')) notice('这段专注到时间了。回到小窝，记下你的收获吧。');
    }
    tickFocus();
    $('focus-today').textContent = focusData.today.count
      ? `今天 ${focusData.today.count} 段陪伴 · ${Math.floor(focusData.today.seconds / 60)} 分钟` : '专注足迹 · 本地保存';
    const history = $('focus-history');
    history.replaceChildren();
    if (!focusData.recent.length) {
      const empty = document.createElement('div');
      empty.className = 'history-empty';
      empty.textContent = '这一页还空着，等我们一起写下第一件小事。';
      history.append(empty);
    }
    for (const row of focusData.recent) {
      const item = document.createElement('div');
      item.className = 'focus-history-row';
      const symbol = document.createElement('span');
      symbol.className = 'history-symbol';
      symbol.textContent = row.outcome === 'done' ? '✓' : '↗';
      const content = document.createElement('div');
      const title = document.createElement('strong');
      title.textContent = row.goal;
      const note = document.createElement('p');
      note.textContent = (row.outcome === 'done' ? '完成了' : '有进展，下次继续')
        + ' · 专注 ' + Math.floor((row.duration - row.remaining) / 60) + ' 分钟' + (row.note ? '\n' + row.note : '');
      content.append(title, note);
      if (!current && row.outcome === 'progress') {
        const next = document.createElement('button');
        next.type = 'button'; next.className = 'history-continue'; next.textContent = '接着走一小步 ↗';
        next.onclick = () => {
          $('focus-goal').value = row.goal;
          $('focus-card').scrollIntoView({ block: 'center' });
          $('focus-goal').focus();
          notice('已带回上次的小目标，调整后再开始。');
        };
        content.append(next);
      }
      const date = document.createElement('time');
      date.dateTime = new Date(row.finished_at * 1000).toISOString();
      date.textContent = new Date(row.finished_at * 1000).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
      const meta = document.createElement('div');
      meta.className = 'history-meta';
      const remove = document.createElement('button');
      remove.type = 'button'; remove.className = 'history-remove'; remove.textContent = '删除';
      remove.setAttribute('aria-label', '删除专注记录：' + row.goal);
      remove.onclick = async () => {
        if (!confirm('删除这段专注记录和回顾？已有备份中的记录仍会保留。')) return;
        if (focusPending) return;
        focusPending = true; focusVersion++; remove.disabled = true;
        try { acceptFocus(await api('/api/focus/' + row.id, { method: 'DELETE' })); notice('这段专注记录已删除。'); }
        catch (error) { notice('删除失败：' + error.message); }
        finally { focusPending = false; remove.disabled = false; }
      };
      meta.append(date, remove);
      item.append(symbol, content, meta);
      history.append(item);
    }
  }

  function tickFocus() {
    const current = focusData?.current;
    if (!current) return;
    const seconds = current.status === 'active'
      ? Math.max(0, Math.ceil(current.deadline - (Date.now() + focusOffset) / 1000)) : Math.ceil(current.remaining);
    const clock = String(Math.floor(seconds / 60)).padStart(2, '0') + ':' + String(seconds % 60).padStart(2, '0');
    $('focus-clock').textContent = clock;
    $('focus-stage').textContent = current.status === 'paused' ? '已暂停 · 不计入专注时间' : seconds === 0 ? '正在保存计时状态…' : '专注中 · 我在旁边陪你';
    $('focus-chip').textContent = current.status === 'ready' ? '专注 · 待回顾' : (current.status === 'paused' ? '已暂停 ' : '陪伴中 ') + clock;
    $('focus-progress').style.width = Math.min(100, Math.max(0, (1 - seconds / current.duration) * 100)) + '%';
    // Polling owns network retries; the one-second clock never sends requests.
  }

  function startupError(error) {
    showChat(false);
    const box = document.createElement('div');
    box.className = 'chat-load-error';
    const text = document.createElement('p');
    text.textContent = '暂时没能打开对话：' + error.message;
    const retry = document.createElement('button');
    retry.className = 'home-primary';
    retry.textContent = '重新加载';
    retry.onclick = () => location.reload();
    box.append(text, retry);
    $('log').append(box);
  }

  document.addEventListener('DOMContentLoaded', () => {
    const portrait = $('home-portrait');
    const revealPortrait = () => {
      const ready = portrait.naturalWidth > 1;
      portrait.hidden = !ready;
      portrait.closest('.home-hero').classList.toggle('portrait-ready', ready);
    };
    portrait.addEventListener('load', revealPortrait);
    portrait.addEventListener('error', revealPortrait);
    if (portrait.complete) revealPortrait();
    const now = new Date();
    $('home-date').textContent = now.toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' });
    $('home-greeting').textContent = now.getHours() < 6 ? '夜深了，' : now.getHours() < 11 ? '早上好，' : now.getHours() < 14 ? '中午好，' : now.getHours() < 18 ? '下午好，' : '晚上好，';
    $('home-nav').onclick = showHome;
    $('chat-nav').onclick = () => showChat();
    $('input').addEventListener('input', saveDraft);
    window.addEventListener('pagehide', saveDraft);
    $('sidebar-toggle').onclick = () => {
      if (document.body.classList.contains('sidebar-open')) return closeSidebar();
      previousSidebarFocus = document.activeElement;
      document.body.classList.add('sidebar-open');
      $('sidebar-toggle').setAttribute('aria-expanded', 'true');
      $('home-nav').focus();
    };
    $('sidebar-scrim').onclick = closeSidebar;
    $('home-daily').onclick = () => $('daily-nyalume-btn').click();
    $('home-plan').onclick = () => $('pet-plan-toggle').click();
    document.querySelectorAll('[data-prompt]').forEach(button => {
      button.onclick = () => {
        showChat();
        const field = $('input');
        field.value = field.value ? field.value + '\n\n' + button.dataset.prompt : button.dataset.prompt;
        resizeInput(); saveDraft(); field.focus();
        notice('已放进输入框，改成你的话再发送就好。');
      };
    });
    $('focus-start-form').onsubmit = event => {
      event.preventDefault();
      changeFocus('start', { goal: $('focus-goal').value.trim(), minutes: Number(document.querySelector('[name="focus-minutes"]:checked').value) });
    };
    $('focus-pause').onclick = () => changeFocus(focusData.current.status === 'paused' ? 'resume' : 'pause');
    $('focus-finish').onclick = () => changeFocus('finish');
    const cancel = () => { if (confirm('放下这次专注？这次不会留在专注足迹中。')) changeFocus('cancel'); };
    $('focus-cancel').onclick = cancel;
    $('focus-discard').onclick = cancel;
    $('focus-review').onsubmit = event => {
      event.preventDefault();
      changeFocus('complete', { outcome: event.submitter?.value || 'done', note: $('focus-note').value });
    };
    $('focus-retry').onclick = refreshFocus;
    const goFocus = () => {
      showHome();
      if (!document.body.classList.contains('quick-chat')) {
        $('focus-card').scrollIntoView({ block: 'center' });
        if (!$('focus-start-form').hidden) $('focus-goal').focus();
      } else window.open('/?home=1', '_blank', 'noopener');
    };
    $('focus-chip').onclick = goFocus;
    const dialog = $('command-dialog');
    const commands = [
      ['今日小窝', '首页 · home', showHome],
      ['继续对话', '聊天 · chat', () => showChat()],
      ['新建对话', '新的开始', () => newChat()],
      ['陪伴专注', '计时 · focus', goFocus],
      ['今日 Nyalume', '每日卡片 · 祝福', () => $('daily-nyalume-btn').click()],
      ['她的小计划', '花园 · 陪伴', () => $('pet-plan-toggle').click()],
      ['奇幻冒险', '另一段故事', () => window.open('/fantasy', '_blank', 'noopener')],
      ['聊天壁纸', '外观 · 图片', () => { showChat(false); document.body.classList.add('toolbar-open'); $('wall-menu').classList.remove('hidden'); }],
      ['人设与角色', '切换人设', () => { showChat(); document.body.classList.add('toolbar-open'); $('persona-select').focus(); notice('在顶栏的人设选择框中切换角色。'); }],
      ['API 与模型设置', '设置 · 配置', () => openSettings()],
      ['Skill 管理', '技能 · 工具', () => openSettings('skills')],
    ];
    function renderCommands() {
      const query = $('command-search').value.trim().toLowerCase();
      $('command-list').replaceChildren();
      commands.filter(([label, hint]) => (label + hint).toLowerCase().includes(query)).forEach(([label, hint, run]) => {
        const button = document.createElement('button');
        button.type = 'button'; button.textContent = label;
        const small = document.createElement('small'); small.textContent = hint; button.append(small);
        button.onclick = async () => { dialog.close(); try { await run(); } catch (e) { notice(e.message); } };
        $('command-list').append(button);
      });
      if (!$('command-list').children.length) {
        const empty = document.createElement('p'); empty.className = 'history-empty'; empty.textContent = '没有匹配的入口，试试「专注」或「设置」。'; $('command-list').append(empty);
      }
    }
    function openCommands() { closeSidebar(); $('command-search').value = ''; renderCommands(); if (!dialog.open) dialog.showModal(); $('command-search').focus(); }
    $('command-open').onclick = openCommands;
    $('command-close').onclick = () => dialog.close();
    $('command-search').oninput = renderCommands;
    dialog.addEventListener('click', event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); } });
    dialog.addEventListener('keydown', event => {
      if (event.isComposing) return;
      const buttons = [...$('command-list').querySelectorAll('button')];
      if ((event.key === 'ArrowDown' || event.key === 'ArrowUp') && buttons.length) {
        event.preventDefault();
        const current = buttons.indexOf(document.activeElement);
        buttons[event.key === 'ArrowDown' ? (current + 1) % buttons.length : (current <= 0 ? buttons.length - 1 : current - 1)].focus();
      }
      if (event.key === 'Enter' && event.target === $('command-search') && buttons.length) { event.preventDefault(); buttons[0].click(); }
    });
    document.addEventListener('keydown', event => {
      if (event.isComposing) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); openCommands(); }
      if (event.key === 'Escape') closeSidebar();
      if (event.key === 'Tab' && document.body.classList.contains('sidebar-open')) {
        const items = [...$('sidebar').querySelectorAll('button, input, a, select, [tabindex="0"]')].filter(el => !el.disabled && el.getClientRects().length);
        const first = items[0], last = items.at(-1);
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    });
    refreshFocus();
    setInterval(tickFocus, 1000);
    setInterval(() => { if (!document.hidden) refreshFocus(); }, 10000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshFocus(); });
    if (new URLSearchParams(location.search).get('home') === '1') showHome();
  });
  return { sessionChanged, saveDraft, forgetDraft, showHome, showChat, startupError };
})();
