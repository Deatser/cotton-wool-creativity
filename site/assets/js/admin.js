/* Админка каталога: плашка «вошли как администратор», добавление, правка
   и удаление игрушек. Firebase отвечает только за вход. Каталог лежит
   в data/toys.json, файлы в site/img/upload/, и то и другое ведёт serve.py.
   Покупателю ничего из этого не видно.

   Firebase подгружается лениво и только после полной загрузки страницы:
   раньше здесь был await на верхнем уровне модуля, и браузер откладывал
   DOMContentLoaded до ответа gstatic. Если тот отвечал медленно, страница
   висела «загружается» по десять-пятнадцать секунд. */

const SDK = 'https://www.gstatic.com/firebasejs/12.18.0/';
const API_VERSION = 3;   // должно совпадать с API_VERSION в serve.py
const ROOT = document.body.getAttribute('data-root') || '';
const SECTIONS = ['in_stock', 'repeat', 'custom'];
const SECTION_NAMES = {
  in_stock: 'Игрушки в наличии',
  repeat: 'Реализованные игрушки',
  custom: 'Игрушки под заказ',
};

let fb = null;          // подключённый Firebase, появляется по требованию
let isAdmin = false;
let toys = [];
let editorState = null;

const esc = (v) => String(v ?? '').replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/** Ссылка на файл: пути хранятся относительно корня сайта. */
const url = (u) => (!u ? '' : /^https?:/.test(u) ? u : ROOT + u);

const priceText = (v) => (v || v === 0 ? v + ' руб.' : '');

/** Подключаем Firebase один раз и только когда он действительно нужен. */
async function firebase() {
  if (fb) return fb;
  const [appMod, authMod, cfg] = await Promise.all([
    import(SDK + 'firebase-app.js'),
    import(SDK + 'firebase-auth.js'),
    import('./firebase-config.js'),
  ]);
  const app = appMod.initializeApp(cfg.firebaseConfig);
  fb = {
    auth: authMod.getAuth(app),
    onAuthStateChanged: authMod.onAuthStateChanged,
    signOut: authMod.signOut,
  };
  return fb;
}

/* ============================================================ вход и плашка */

function renderHud(user) {
  document.querySelector('.admin-hud')?.remove();
  if (!user) return;

  const hud = document.createElement('div');
  hud.className = 'admin-hud';
  hud.innerHTML =
    '<span class="admin-hud__text">Вы вошли как администратор</span>' +
    '<button type="button" class="admin-hud__out">Выйти</button>';
  hud.querySelector('.admin-hud__out').addEventListener('click', async () => {
    await fb.signOut(fb.auth);
    location.reload();
  });
  document.body.appendChild(hud);
  checkServer(hud);
}

/** Сразу говорим, если сайт открыт не через serve.py: иначе про это
    выясняется только в момент сохранения, когда форма уже заполнена. */
async function checkServer(hud) {
  let warn = '';
  try {
    const pong = await api('/api/ping');
    if (pong.version !== API_VERSION) {
      warn = 'Сервер запущен старой версии. Остановите его в окне терминала ' +
             '(Ctrl+C) и запустите заново: <b>py serve.py</b>';
    }
  } catch (e) {
    warn = 'Сохранять некуда: сайт открыт не через <b>py serve.py</b>. ' +
           'Правки не запишутся.';
  }
  if (!warn) return;
  hud.classList.add('admin-hud--warn');
  hud.insertAdjacentHTML('afterbegin', '<span class="admin-hud__warn">' + warn + '</span>');
}

/* ================================================== загрузка и вывод каталога */

/** Разговор со своим сервером. Отдельная обёртка, чтобы у падений
    была понятная причина, а не голое «Failed to fetch». */
async function api(path, options) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (e) {
    throw new Error('локальный сервер не отвечает. Запустите его командой ' +
      '«py serve.py» в папке проекта и откройте сайт по адресу, который он покажет');
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* тело может быть пустым */ }
  if (!res.ok) throw new Error((data && data.error) || 'сервер ответил ' + res.status);
  return data;
}

/** Запасной каталог из статики, в том же виде, что отдаёт сервер.
    Нужен, чтобы кнопки правки появились даже когда serve.py не запущен. */
function staticToys() {
  return (window.CATALOG || []).map((t) => ({
    id: t.slug,
    name: t.name,
    size: t.size,
    note: t.note || '',
    price: t.priceValue || null,
    section: t.sectionId,
    order: t.order,
    cover: { url: t.cover },
    media: (t.media || []).map((m) => ({ type: m.type, url: m.url })),
    staticUrl: t.url,
  }));
}

/** Каталог лежит в data/toys.json, его отдаёт serve.py. */
async function loadToys() {
  try {
    const data = await api('/api/catalog');
    return data.toys || [];
  } catch (e) {
    console.warn('каталог не прочитался:', e.message);
    return null;
  }
}

/** Заменяем статическую сетку данными из базы. Пока база пуста, остаётся статика. */
function renderGrid() {
  if (!toys.length) return;
  for (const id of SECTIONS) {
    const grid = document.querySelector('#' + id + ' .grid');
    if (!grid) continue;
    grid.innerHTML = toys.filter((t) => t.section === id).map(cardHtml).join('');
  }
}

function cardHtml(toy) {
  const href = url(toy.staticUrl || ('tovar/?id=' + encodeURIComponent(toy.id)));
  const size = toy.size ? ', <span class="size">' + esc(toy.size) + '</span>' : '';
  const note = toy.note ? '<span class="note">' + esc(toy.note) + '</span>' : '';
  const price = toy.price
    ? '<p class="card__price">' + esc(priceText(toy.price)) + '</p>' +
      '<a class="card__buy" href="' + href + '">Купить</a>'
    : '';
  return '<article class="card" data-id="' + esc(toy.id) + '">' +
      '<a class="card__media" href="' + href + '">' +
        '<img src="' + esc(url(toy.cover && toy.cover.url)) + '" width="600" height="600" ' +
        'loading="lazy" decoding="async" alt="Ватная ёлочная игрушка «' + esc(toy.name) + '»">' +
      '</a>' +
      '<p class="card__title"><a href="' + href + '"><b>' + esc(toy.name) + '</b>' + size + '</a>' + note + '</p>' +
      price +
    '</article>';
}

/* ============================================ кнопки, видные только админу */

function renderAdminBits() {
  document.querySelectorAll('.card--add, .card__tools').forEach((el) => el.remove());
  if (!isAdmin) return;

  for (const id of SECTIONS) {
    const grid = document.querySelector('#' + id + ' .grid');
    if (!grid) continue;

    // кнопка добавления одна на весь каталог: раздел всё равно выбирается в окне
    if (id === SECTIONS[0]) {
      const add = document.createElement('article');
      add.className = 'card card--add';
      add.innerHTML = '<button type="button" class="card__add">' +
        '<span class="card__add-plus">' +
          '<svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true">' +
          '<path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" ' +
          'stroke-width="2" stroke-linecap="round"/></svg>' +
        '</span><span>Добавить игрушку</span></button>';
      add.querySelector('button').addEventListener('click', () => openEditor(null, id));
      grid.prepend(add);
    }

    grid.querySelectorAll('.card[data-id]').forEach((card) => {
      const toy = toys.find((t) => t.id === card.dataset.id);
      if (!toy) return;
      const tools = document.createElement('div');
      tools.className = 'card__tools';
      tools.innerHTML =
        '<button type="button" class="card__tool" title="Изменить">' +
          '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
          '<path d="M4 20h4L20 8l-4-4L4 16v4z"/></svg></button>' +
        '<button type="button" class="card__tool card__tool--del" title="Удалить">' +
          '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
          '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg></button>';
      const [edit, del] = tools.querySelectorAll('button');
      edit.addEventListener('click', (e) => { e.preventDefault(); openEditor(toy, toy.section); });
      del.addEventListener('click', (e) => { e.preventDefault(); confirmDelete(toy); });
      card.appendChild(tools);
    });
  }
}

/* ============================================================ окно правки */

function openEditor(toy, section) {
  const digits = toy ? String(toy.size || '').replace(/[^0-9]/g, '') : '';
  const box = modal(toy ? 'Изменить игрушку' : 'Новая игрушка', [
    '<p class="modal__error"></p>',
    '<div class="field"><label>Название игрушки</label>',
    '<input type="text" name="name" value="' + esc(toy ? toy.name : '') + '" maxlength="80"></div>',
    '<div class="field-row">',
      '<div class="field"><label>Высота, см</label>',
      '<input type="number" name="size" min="1" max="200" value="' + esc(digits) + '"></div>',
      '<div class="field"><label>Цена, руб</label>',
      '<input type="number" name="price" min="0" step="50" value="' +
        (toy && toy.price ? toy.price : '') + '"></div>',
    '</div>',
    '<div class="field"><label>Раздел</label><select name="section">',
      SECTIONS.map((s) => '<option value="' + s + '"' + (s === section ? ' selected' : '') +
        '>' + SECTION_NAMES[s] + '</option>').join(''),
    '</select></div>',
    '<div class="field"><label>Примечание, необязательно</label>',
    '<textarea name="note" maxlength="200">' + esc(toy ? toy.note || '' : '') + '</textarea></div>',
    '<div class="field"><label>Обложка для каталога</label>',
      '<label class="upload">Выбрать фото<input type="file" accept="image/*" data-cover></label>',
      '<div class="media" data-cover-box></div></div>',
    '<div class="field"><label>Фото и видео на странице игрушки</label>',
      '<label class="upload">Добавить файлы',
      '<input type="file" accept="image/*,video/*" multiple data-media></label>',
      '<div class="media" data-media-box></div>',
      '<p class="hint">Перетащите файлы мышкой, чтобы поменять порядок. ',
      'Первый идёт первым на странице.</p></div>',
  ].join(''), [
    { text: 'Отменить', cls: 'btn-flat', act: closeModal },
    { text: 'Сохранить', cls: 'btn-main', act: saveToy, name: 'save' },
  ]);

  // строго после modal(): он закрывает предыдущее окно и обнуляет состояние,
  // так что заполнять его раньше нельзя
  editorState = {
    toy,
    section,
    media: toy ? (toy.media || []).map((m) => ({ ...m })) : [],
    cover: toy && toy.cover ? { ...toy.cover } : null,
    removed: [],    // файлы, которые надо стереть с диска при сохранении
  };

  box.querySelector('[data-cover]').addEventListener('change', (e) => {
    const f = e.target.files[0];
    if (f) editorState.cover = { file: f, url: URL.createObjectURL(f), local: true };
    e.target.value = '';
    drawMedia();
  });

  box.querySelector('[data-media]').addEventListener('change', (e) => {
    for (const f of e.target.files) {
      editorState.media.push({
        file: f,
        url: URL.createObjectURL(f),
        type: f.type.startsWith('video') ? 'video' : 'image',
        local: true,
      });
    }
    e.target.value = '';
    drawMedia();
  });

  box.querySelectorAll('input, select, textarea').forEach((el) =>
    el.addEventListener('input', validate));

  drawMedia();
}

function drawMedia() {
  const box = document.querySelector('.modal');
  const coverBox = box.querySelector('[data-cover-box]');
  const mediaBox = box.querySelector('[data-media-box]');

  coverBox.innerHTML = editorState.cover ? tile(editorState.cover, -1, 'обложка') : '';
  mediaBox.innerHTML = editorState.media.map((m, i) => tile(m, i)).join('');

  coverBox.querySelector('.media__del')?.addEventListener('click', () => {
    if (editorState.cover && editorState.cover.path) editorState.removed.push(editorState.cover.path);
    editorState.cover = null;
    drawMedia();
  });

  mediaBox.querySelectorAll('.media__item').forEach((el, i) => {
    el.querySelector('.media__del').addEventListener('click', () => {
      const gone = editorState.media.splice(i, 1)[0];
      if (gone && gone.path) editorState.removed.push(gone.path);
      drawMedia();
    });
    el.addEventListener('dragstart', (e) => {
      el.classList.add('is-dragging');
      e.dataTransfer.setData('text/plain', String(i));
    });
    el.addEventListener('dragend', () => el.classList.remove('is-dragging'));
    el.addEventListener('dragover', (e) => { e.preventDefault(); el.classList.add('is-over'); });
    el.addEventListener('dragleave', () => el.classList.remove('is-over'));
    el.addEventListener('drop', (e) => {
      e.preventDefault();
      el.classList.remove('is-over');
      const from = Number(e.dataTransfer.getData('text/plain'));
      if (Number.isNaN(from) || from === i) return;
      const moved = editorState.media.splice(from, 1)[0];
      editorState.media.splice(i, 0, moved);
      drawMedia();
    });
  });

  validate();
}

function tile(m, i, tag) {
  const src = m.local ? m.url : url(m.url);
  const inner = m.type === 'video'
    ? '<video src="' + esc(src) + '" muted playsinline></video>'
    : '<img src="' + esc(src) + '" alt="">';
  return '<div class="media__item"' + (i >= 0 ? ' draggable="true"' : '') + '>' +
    inner +
    '<span class="media__tag">' + esc(tag || (m.type === 'video' ? 'видео' : 'фото')) + '</span>' +
    (i >= 0 ? '<span class="media__grip">···</span>' : '') +
    '<button type="button" class="media__del" title="Убрать">&times;</button>' +
  '</div>';
}

/** Кнопка «Сохранить» включается, только когда заполнено всё нужное. */
function validate() {
  const box = document.querySelector('.modal');
  if (!box) return;
  const f = (n) => box.querySelector('[name="' + n + '"]').value.trim();
  const section = f('section');
  const ok = f('name') && f('size') &&
    (section !== 'in_stock' || f('price')) &&
    editorState.cover && editorState.media.length > 0;
  box.querySelector('[data-act="save"]').disabled = !ok;
}

async function saveToy() {
  const box = document.querySelector('.modal');
  const save = box.querySelector('[data-act="save"]');
  const err = box.querySelector('.modal__error');
  const val = (n) => box.querySelector('[name="' + n + '"]').value.trim();

  save.disabled = true;
  save.textContent = 'Сохраняем...';
  err.textContent = '';

  try {
    // адрес новой игрушки собирает сервер из её названия: «Дед мороз» -> «ded-moroz».
    // У существующей адрес не меняем, иначе сломались бы ссылки и выдача в поиске.
    let id = editorState.toy && editorState.toy.id;
    if (!id) {
      const got = await api('/api/slug?name=' + encodeURIComponent(val('name')));
      id = got.slug;
    }

    // новые файлы уезжают на сервер, уже загруженные остаются как есть
    const cover = editorState.cover.local
      ? await upload(id, editorState.cover.file)
      : { url: editorState.cover.url };

    const media = [];
    for (const m of editorState.media) {
      if (m.local) {
        const up = await upload(id, m.file);
        media.push({ url: up.url, type: m.type });
      } else {
        media.push({ url: m.url, type: m.type });
      }
    }

    await api('/api/toy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id,
        name: val('name'),
        size: val('size') + ' см',
        note: val('note'),
        price: val('price') ? Number(val('price')) : null,
        section: val('section'),
        cover,
        media,
      }),
    });

    for (const path of editorState.removed) await dropFile(path);
    location.reload();
  } catch (e) {
    err.textContent = 'Не сохранилось: ' + (e.code || e.message);
    save.disabled = false;
    save.textContent = 'Сохранить';
  }
}

/* Файлы не уходят в облако: их принимает локальный serve.py и кладёт
   в site/img/upload/. В базе хранится только путь относительно сайта. */
async function upload(id, file) {
  const q = '?id=' + encodeURIComponent(id) + '&name=' + encodeURIComponent(file.name);
  return api('/api/upload' + q, { method: 'POST', body: file });   // { url, path }
}

/** Стираем только то, что лежит в папке загрузок: файлы статики трогать нельзя. */
async function dropFile(path) {
  if (!path || path.indexOf('img/upload/') !== 0) return;
  try {
    await api('/api/delete?path=' + encodeURIComponent(path), { method: 'POST' });
  } catch (e) { /* файла уже нет, это не беда */ }
}

/* ============================================================== удаление */

function confirmDelete(toy) {
  const box = modal('Удалить игрушку', [
    '<p class="modal__error"></p>',
    '<p>Удалить «<b>' + esc(toy.name) + '</b>» из каталога? ',
    'Вместе с карточкой удалятся её фотографии.</p>',
    '<p>Это действие нельзя отменить.</p>',
  ].join(''), [
    { text: 'Отменить', cls: 'btn-flat', act: closeModal },
    { text: 'Удалить', cls: 'btn-main btn-danger', name: 'del', act: async () => {
      const btn = box.querySelector('[data-act="del"]');
      btn.disabled = true;
      btn.textContent = 'Удаляем...';
      try {
        for (const m of toy.media || []) await dropFile(m.url);
        await dropFile(toy.cover && toy.cover.url);
        await api('/api/toy-delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: toy.id }),
        });
        location.reload();
      } catch (e) {
        box.querySelector('.modal__error').textContent = 'Не удалось: ' + (e.code || e.message);
        btn.disabled = false;
        btn.textContent = 'Удалить';
      }
    } },
  ], true);
}

/* ================================================================== окно */

function modal(title, body, buttons, narrow) {
  closeModal();
  const el = document.createElement('div');
  el.className = 'modal';
  el.innerHTML =
    '<div class="modal__box' + (narrow ? ' modal__box--narrow' : '') + '">' +
      '<div class="modal__head"><h2>' + esc(title) + '</h2>' +
        '<button type="button" class="modal__close" aria-label="Закрыть">&times;</button></div>' +
      '<div class="modal__body">' + body + '</div>' +
      '<div class="modal__foot">' +
        buttons.map((b) => '<button type="button" class="' + b.cls + '"' +
          (b.name ? ' data-act="' + b.name + '"' : '') + '>' + b.text + '</button>').join('') +
      '</div>' +
    '</div>';
  document.body.appendChild(el);

  el.querySelectorAll('.modal__foot button').forEach((btn, i) =>
    btn.addEventListener('click', buttons[i].act));
  el.querySelector('.modal__close').addEventListener('click', closeModal);
  el.addEventListener('click', (e) => { if (e.target === el) closeModal(); });
  document.addEventListener('keydown', onEsc);
  document.body.style.overflow = 'hidden';
  return el;
}

function onEsc(e) { if (e.key === 'Escape') closeModal(); }

function closeModal() {
  document.querySelector('.modal')?.remove();
  document.removeEventListener('keydown', onEsc);
  document.body.style.overflow = '';
  editorState = null;
}

/* ============================== страница игрушки, добавленной через админку */

function renderItemPage() {
  const page = document.querySelector('[data-item-page]');
  if (!page) return;
  const id = new URLSearchParams(location.search).get('id');
  const toy = toys.find((t) => t.id === id);

  if (!toy) {
    page.querySelector('[data-item-title]').textContent = 'Игрушка не найдена';
    page.querySelector('[data-item-status]').textContent =
      'Возможно, её убрали из каталога. Вернитесь на главную страницу.';
    return;
  }

  document.title = toy.name + ' — ватная ёлочная игрушка ручной работы';
  page.querySelector('[data-item-name]').textContent = toy.name;
  page.querySelector('[data-item-title]').textContent = toy.name;

  const set = (sel, text) => {
    const el = page.querySelector(sel);
    if (!text) return;
    el.textContent = text;
    el.hidden = false;
  };
  set('[data-item-size]', toy.size ? 'Высота ' + toy.size : '');
  set('[data-item-note]', toy.note);
  set('[data-item-price]', priceText(toy.price));

  page.querySelector('[data-item-status]').textContent = toy.price
    ? 'В наличии, в единственном экземпляре. Отправляем в течение 1-2 дней после оплаты.'
    : 'Можно заказать повтор: срок изготовления от 14 до 30 дней, в зависимости от сложности.';
  if (toy.price) page.querySelector('[data-item-buy]').hidden = false;

  page.querySelector('[data-item-gallery]').innerHTML = (toy.media || []).map((m) =>
    m.type === 'video'
      ? '<video src="' + esc(url(m.url)) + '" controls preload="metadata"></video>'
      : '<button type="button" data-full="' + esc(url(m.url)) + '" data-alt="' + esc(toy.name) + '">' +
        '<img src="' + esc(url(m.url)) + '" alt="' + esc(toy.name) + '"></button>'
  ).join('');
}

/* ==================================================================== старт */

/** Ждём первый ответ Firebase о том, вошёл пользователь или нет. */
function whoAmI() {
  return new Promise((resolve) => {
    const stop = fb.onAuthStateChanged(fb.auth, (user) => { stop(); resolve(user); });
  });
}

async function boot() {
  try {
    await firebase();
  } catch (e) {
    // gstatic недоступен - сайт продолжает работать на статическом каталоге
    console.warn('Firebase не подключился:', e.message);
    return;
  }

  const user = await whoAmI();
  isAdmin = !!user;
  renderHud(user);

  // кнопки показываем сразу по статическому каталогу: база может отвечать
  // долго или не отвечать вовсе, а ждать её ради отрисовки незачем
  if (isAdmin) {
    toys = staticToys();
    renderAdminBits();
  }

  let loaded = await loadToys();

  // первый вход админа на пустую базу: переносим каталог молча
  if (isAdmin && loaded && !loaded.length && window.CATALOG) {
    try {
      await seedFromStatic();
      loaded = await loadToys();
    } catch (e) {
      console.warn('каталог не перенёсся в базу:', e.code || e.message);
    }
  }

  if (loaded && loaded.length) {
    toys = loaded;
    renderGrid();
    renderAdminBits();
  }

  renderItemPage();

  // вход или выход в другой вкладке - обновляем страницу
  fb.onAuthStateChanged(fb.auth, (u) => {
    if (!!u !== isAdmin) location.reload();
  });
}

// на странице товара из базы содержимое нужно сразу, на остальных - можно позже
if (document.querySelector('[data-item-page]')) {
  boot();
} else if (document.readyState === 'complete') {
  setTimeout(boot, 0);
} else {
  window.addEventListener('load', () => setTimeout(boot, 0));
}
