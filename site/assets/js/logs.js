/* Страница /logs/ - журнал действий администратора.

   Содержимое не лежит в разметке: его отдаёт serve.py по адресу /api/logs,
   и только с пропуском от Firebase. Тому, кто не вошёл, показывать нечего,
   поэтому его сразу уводит на главную. */

import { firebase, whoAmI, call } from './fb.js?v=b0672225';

const page = document.querySelector('[data-logs-page]');
const ROOT = document.body.getAttribute('data-root') || '';
const HOME = ROOT || '/';

const gate = page.querySelector('[data-logs-gate]');
const body = page.querySelector('[data-logs-body]');
const list = page.querySelector('[data-logs-list]');
const hint = page.querySelector('[data-logs-hint]');
const errorBox = page.querySelector('[data-logs-error]');

let me = null;

const esc = (v) => String(v ?? '').replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/** Ссылка на файл: пути в журнале хранятся относительно корня сайта. */
const url = (u) => (!u ? '' : /^https?:/.test(u) ? u : ROOT + u);

/** Уводим с страницы. replace, а не href: чтобы «назад» не возвращало сюда. */
function home() {
  location.replace(HOME);
}

/* ================================================================ отрисовка */

/** Картинка из журнала. У свежих записей есть сохранённая копия, у старых
    остаётся только имя файла: копии хранятся не вечно. */
function shot(item, label) {
  if (!item || (!item.url && !item.name)) {
    return '<span class="shot shot--none"><span class="shot__note">' +
           esc(label || 'пусто') + '</span></span>';
  }
  if (item.type === 'video') {
    return '<span class="shot shot--video"><span class="shot__note">видео</span>' +
           '<span class="shot__name">' + esc(item.name) + '</span></span>';
  }
  // Показываем только сохранённую копию, а не живой файл: заменённую
  // фотографию админка удаляет сразу после сохранения, и ссылка на неё
  // привела бы к битой картинке. Нет копии - так и пишем.
  if (!item.thumb) {
    return '<span class="shot shot--gone"><span class="shot__note">копия не сохранилась</span>' +
           '<span class="shot__name">' + esc(item.name) + '</span></span>';
  }
  return '<span class="shot">' +
         '<img src="' + esc(url(item.thumb)) + '" alt="' + esc(item.name) + '" loading="lazy">' +
         '<span class="shot__name">' + esc(item.name) + '</span></span>';
}

/** Одно изменение внутри правки товара. */
function changeHtml(c) {
  const label = '<span class="change__label">' + esc(c.label) + '</span>';

  if (c.kind === 'text') {
    return '<li class="change">' + label +
      '<span class="change__pair">' +
        '<span class="was">' + (c.from ? esc(c.from) : '<i>пусто</i>') + '</span>' +
        '<span class="arrow">&rarr;</span>' +
        '<span class="now">' + (c.to ? esc(c.to) : '<i>пусто</i>') + '</span>' +
      '</span></li>';
  }

  if (c.kind === 'media') {
    return '<li class="change change--media">' + label +
      '<span class="change__pair">' +
        shot(c.from, 'не было') +
        '<span class="arrow">&rarr;</span>' +
        shot(c.to, 'убрано') +
      '</span></li>';
  }

  const items = c.added || c.removed || [];
  return '<li class="change change--media">' + label +
    '<span class="change__pair">' + items.map((m) => shot(m)).join('') + '</span></li>';
}

/** Строка журнала. */
function entryHtml(e) {
  const who = '<b class="entry__who">' + esc(e.who) + '</b>';
  let what = esc(e.actionText);

  if (e.action === 'reorder') {
    what += e.extra ? ' в разделе «' + esc(e.extra) + '»' : '';
  } else if (e.toy && e.toy.name) {
    const name = '«' + esc(e.toy.name) + '»';
    // удалённой игрушки на сайте больше нет, ссылка вела бы в 404
    what += ' ' + (e.action === 'delete'
      ? '<span class="entry__toy entry__toy--gone">' + name + '</span>'
      : '<a class="entry__toy" href="' + esc(url(e.toy.url)) + '">' + name + '</a>');
  }

  let extra = '';
  if (e.action !== 'edit' && e.cover) {
    extra = '<div class="entry__cover">' + shot(e.cover) + '</div>';
  }
  if (e.changes && e.changes.length) {
    extra = '<ul class="changes">' + e.changes.map(changeHtml).join('') + '</ul>';
  }

  return '<div class="entry entry--' + esc(e.action) + '" data-entry="' + esc(e.id) + '">' +
    '<div class="entry__line">' +
      '<span class="entry__time">' + esc(e.time) + '</span>' +
      '<span class="entry__text">' + who + ' ' + what + '</span>' +
    '</div>' + extra + undoHtml(e) + '</div>';
}

/* ===================================================================== откат */

const UNDO_LABEL = {
  toy: 'Вернуть как было',
  remove: 'Убрать этот товар',
  order: 'Вернуть прежний порядок',
  about: 'Вернуть прежний текст',
  hero: 'Вернуть прежнюю обложку',
};

const UNDO_ASK = {
  toy: 'Вернуть карточку к этому состоянию? Всё, что меняли после, пропадёт.',
  remove: 'Убрать этот товар из каталога вместе с его файлами?',
  order: 'Вернуть карточки раздела в прежний порядок?',
  about: 'Вернуть страницу к этому виду? Всё, что писали после, пропадёт.',
  hero: 'Вернуть обложку главной страницы к этому виду? Всё, что меняли после, пропадёт.',
};

/** Кнопка возврата. Показываем, только пока копии файлов ещё хранятся. */
function undoHtml(e) {
  if (!e.restoreKind) return '';
  if (!e.canRestore) {
    return '<div class="entry__act"><span class="entry__cold">' +
           esc(e.restoreNote || 'вернуть эту запись уже нельзя') + '</span></div>';
  }
  return '<div class="entry__act">' +
    '<button type="button" class="entry__undo" data-undo="' + esc(e.id) + '" ' +
    'data-kind="' + esc(e.restoreKind) + '">' +
    esc(UNDO_LABEL[e.restoreKind] || 'Вернуть') + '</button></div>';
}

/** Спрашиваем прямо в строке: отдельное окно ради одной кнопки избыточно. */
function askUndo(btn) {
  const box = btn.closest('.entry__act');
  const id = btn.dataset.undo;
  const kind = btn.dataset.kind;
  box.innerHTML =
    '<span class="entry__ask">' + esc(UNDO_ASK[kind] || 'Вернуть эту версию?') + '</span>' +
    '<button type="button" class="entry__undo entry__undo--yes">Да, вернуть</button>' +
    '<button type="button" class="entry__cancel">Отмена</button>';

  box.querySelector('.entry__cancel').addEventListener('click', () => {
    box.outerHTML = undoHtml({ id, restoreKind: kind, canRestore: true });
    bindUndo();
  });

  box.querySelector('.entry__undo--yes').addEventListener('click', async (ev) => {
    const yes = ev.currentTarget;
    yes.disabled = true;
    yes.textContent = 'Возвращаем...';
    try {
      await call('/api/restore', {
        method: 'POST',
        auth: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      }, me);
      // сборка страниц идёт на сервере, поэтому проще перечитать всё заново
      location.reload();
    } catch (err) {
      box.innerHTML = '<span class="entry__cold">Не вернулось: ' + esc(err.message) + '</span>';
    }
  });
}

function bindUndo() {
  list.querySelectorAll('[data-undo]').forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', () => askUndo(btn));
  });
}

function render(data) {
  const entries = data.entries || [];
  hint.textContent = entries.length
    ? 'Записей: ' + entries.length + '. Копии заменённых фотографий хранятся у последних ' +
      (data.keep || 10) + ' правок: пока копия есть, правку можно вернуть кнопкой, ' +
      'дальше остаются только названия файлов.'
    : '';

  if (!entries.length) {
    list.innerHTML = '<p class="logs__empty">Пока пусто. Здесь появятся записи, ' +
      'как только в каталоге что-нибудь изменится.</p>';
    return;
  }

  // записи приходят от новых к старым, дни идут в том же порядке
  const days = [];
  for (const e of entries) {
    if (!days.length || days[days.length - 1].date !== e.date) {
      days.push({ date: e.date, items: [] });
    }
    days[days.length - 1].items.push(e);
  }

  list.innerHTML = days.map((d) =>
    '<section class="day"><h2 class="day__date">' + esc(d.date) + '</h2>' +
    d.items.map(entryHtml).join('') + '</section>').join('');

  bindUndo();

  // файла может не быть на диске: у старых записей копию уже убрали,
  // а живой оригинал мог уехать вместе с игрушкой
  list.querySelectorAll('.shot img').forEach((img) => {
    img.addEventListener('error', () => {
      const box = img.closest('.shot');
      box.classList.add('shot--gone');
      img.remove();
      box.insertAdjacentHTML('afterbegin',
        '<span class="shot__note">копия не сохранилась</span>');
    });
  });
}

/* ==================================================================== старт */

async function load() {
  errorBox.hidden = true;
  try {
    render(await call('/api/logs', { auth: true }, me));
  } catch (e) {
    errorBox.hidden = false;
    errorBox.textContent = 'Журнал не открылся: ' + e.message;
  }
}

async function boot() {
  let fb;
  try {
    fb = await firebase();
  } catch (e) {
    // без Firebase вход не подтвердить, значит показывать журнал нельзя
    console.warn('Firebase не подключился:', e.message);
    return home();
  }

  me = await whoAmI(fb);
  if (!me) return home();

  gate.hidden = true;
  body.hidden = false;
  page.querySelector('[data-logs-refresh]').addEventListener('click', load);
  await load();

  // вышли из админки в соседней вкладке - здесь смотреть больше нечего
  fb.onAuthStateChanged(fb.auth, (u) => {
    me = u;
    if (!u) home();
  });
}

boot();
