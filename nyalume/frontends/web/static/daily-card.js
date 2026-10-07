/* One layered renderer for today's draw and every card opened from the album. */
window.NyalumeCards = {
  front(profile) { return `/static/daily_nyalume/${profile}.png?v=trio-v1`; },
  create(data) {
    const profile = data.profile;
    const config = window.NyalumeCardCatalog[profile];
    const art = document.createElement('div');
    art.className = 'daily-nyalume-art';
    art.dataset.profile = profile;
    art.style.setProperty('--pearl', config.foil);
    const rotator = document.createElement('div');
    rotator.className = 'daily-card-rotator';
    const front = document.createElement('div');
    front.className = 'daily-card-face daily-card-front';
    front.setAttribute('role', 'img');
    front.setAttribute('aria-label', `${config.title} · ${config.technique}`);
    for (const layer of ['background', 'subject', 'lineart', 'text']) {
      const image = document.createElement('img');
      image.className = `daily-card-layer daily-card-${layer}`;
      if (layer === 'background') image.classList.add('daily-nyalume-portrait');
      image.src = `/static/daily_nyalume/${profile}/${layer}.png?v=trio-v1`;
      image.alt = '';
      image.draggable = false;
      front.append(image);
    }
    const back = document.createElement('div');
    back.className = 'daily-card-face daily-card-back';
    const mark = document.createElement('span');
    mark.textContent = "TODAY'S NYALUME · 小秘密";
    const title = document.createElement('strong');
    title.textContent = config.technique;
    const egg = document.createElement('p');
    egg.className = 'daily-card-egg';
    egg.textContent = config.egg;
    const edition = document.createElement('small');
    edition.textContent = config.edition;
    back.append(mark, title, egg, edition);
    back.setAttribute('aria-hidden', 'true');
    rotator.append(front, back);
    art.append(rotator);
    const flip = document.createElement('button');
    flip.className = 'daily-nyalume-action daily-card-flip';
    flip.type = 'button';
    flip.textContent = '翻面 · 看小秘密';
    flip.setAttribute('aria-pressed', 'false');
    let flipped = false;
    flip.onclick = () => {
      flipped = !flipped;
      art.classList.toggle('flipped', flipped);
      front.setAttribute('aria-hidden', String(flipped));
      back.setAttribute('aria-hidden', String(!flipped));
      flip.setAttribute('aria-pressed', String(flipped));
      flip.textContent = flipped ? '翻回正面' : '翻面 · 看小秘密';
    };
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    let drag = null, rx = 0, ry = 0;
    const pose = (x, y) => {
      rx = Math.max(-18, Math.min(18, x));
      ry = Math.max(-24, Math.min(24, y));
      art.style.setProperty('--card-rx', `${rx}deg`);
      art.style.setProperty('--card-ry', `${ry}deg`);
      art.style.setProperty('--parallax-x', `${ry * .18}px`);
      art.style.setProperty('--parallax-y', `${-rx * .18}px`);
      art.style.setProperty('--shine-x', `${50 + ry * 1.8}%`);
      art.style.setProperty('--shine-y', `${50 - rx * 2}%`);
    };
    art.onpointerdown = event => {
      if (!event.isPrimary || event.button !== 0 || reduced.matches) return;
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY, rx, ry };
      art.setPointerCapture(event.pointerId);
      art.classList.add('dragging');
    };
    art.onpointermove = event => {
      if (reduced.matches) return;
      if (drag) {
        if (event.pointerId === drag.id) pose(drag.rx - (event.clientY - drag.y) * .18, drag.ry + (event.clientX - drag.x) * .18);
      } else if (event.pointerType === 'mouse') {
        const rect = art.getBoundingClientRect();
        pose((.5 - (event.clientY - rect.top) / rect.height) * 24, ((event.clientX - rect.left) / rect.width - .5) * 32);
      }
    };
    const release = event => {
      if (!drag || event.pointerId !== drag.id) return;
      drag = null;
      art.classList.remove('dragging');
      if (art.hasPointerCapture(event.pointerId)) art.releasePointerCapture(event.pointerId);
    };
    art.onpointerup = release;
    art.onpointercancel = event => { release(event); pose(0, 0); };
    art.onlostpointercapture = release;
    art.onpointerleave = () => { if (!drag) pose(0, 0); };
    return { art, flip };
  },
};
