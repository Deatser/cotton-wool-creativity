/* Страница /proverit-zakaz/.

   Две части в одной странице. Покупатель вводит номер заказа и телефон
   и видит свой заказ. Мастер, вошедший в админку, видит над этим полем
   панель со всеми заказами: фильтры по датам, поиск и переключение
   состояний. Разметка панели лежит в странице у всех, но она пустая:
   заказы в неё приходят с /api/orders, а тот отвечает только на запрос
   с пропуском. Постороннему панель и не показывается, и наполнить её нечем.

   Правила переходов проверяет сервер, здесь они только отражены кнопками:
   отправить можно лишь оплаченный заказ, у отправленного оплату не снять. */

import { firebase, whoAmI, call } from './fb.js?v=7b927257';

const page = document.querySelector('[data-check-page]');
const ROOT = document.body.getAttribute('data-root') || '';

const seller = {
  pay: page.dataset.pay,
  max: page.dataset.max,
  owner: page.dataset.owner,
  phone: page.dataset.phone,
};

const esc = (v) => String(v ?? '').replace(/[&<>"]/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const money = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' ₽';

function when(iso, withTime = true) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const two = (n) => (n < 10 ? '0' : '') + n;
  const day = two(d.getDate()) + '.' + two(d.getMonth() + 1) + '.' + d.getFullYear();
  return withTime ? day + ' в ' + two(d.getHours()) + ':' + two(d.getMinutes()) : day;
}

const PAY = {
  waiting: { text: 'Ждёт оплаты', cls: 'badge--wait' },
  paid: { text: 'Оплачен', cls: 'badge--ok' },
  cancelled: { text: 'Отменён', cls: 'badge--off' },
};

const SHIP = {
  waiting: { text: 'Ждёт отправки', cls: 'badge--wait' },
  sent: { text: 'Отправлен', cls: 'badge--ok' },
};

const badge = (map, key) => {
  const s = map[key] || map.waiting;
  return '<span class="badge ' + s.cls + '">' + esc(s.text) + '</span>';
};

/** Строки с данными заказа: одинаковые и у покупателя, и у мастера. */
/**
 * Что ответил Resend по одному письму. Пустая строка - заказ старый,
 * до писем, и строку в карточке рисовать не надо.
 */
function mailText(one) {
  if (!one) return '';
  if (one.ok) return 'отправлено';
  return 'НЕ отправлено' + (one.error ? ' - ' + one.error : '');
}

function rowsHtml(o) {
  const b = o.buyer || {};
  const toy = o.toy || {};
  const post = o.mail || {};
  const address = [b.zip, b.region, b.city, b.street].filter(Boolean).join(', ');
  const rows = [
    ['Игрушка', toy.name],
    ['Стоимость', toy.price ? money(toy.price) : 'по договорённости'],
    ['Оформлен', when(o.created)],
    ['Получатель', b.name],
    ['Телефон', b.phone],
    ['Почта', b.email],
    ['Доставка', b.deliveryName || b.delivery],
    ['Адрес', address],
    ['Комментарий', b.comment],
    ['Трек-номер', o.track],
    ['Письмо покупателю', mailText(post.buyer)],
    ['Письмо мне', mailText(post.seller)],
  ];
  return rows.filter((r) => r[1])
    .map((r) => '<div><dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd></div>')
    .join('');
}

/* ======================================================= заказ для покупателя */

const foundBox = page.querySelector('[data-found]');
const findError = page.querySelector('[data-find-error]');

function renderFound(o) {
  const toy = o.toy || {};
  const waiting = o.payment === 'waiting';
  let next = '';

  if (waiting) {
    next =
      '<div class="found__next">' +
        '<p>Заказ записан, оплата ещё не отмечена. Переведите ' +
        (toy.price ? '<b>' + esc(money(toy.price)) + '</b>' : 'сумму, которую подтвердит мастер') +
        ' по номеру <b>' + esc(seller.phone) + '</b>, получатель <b>' + esc(seller.owner) +
        '</b>, и укажите в сообщении номер заказа <b>' + esc(o.number) + '</b>.</p>' +
        (o.holdUntil ? '<p class="found__hold">Оплату ждём до ' + esc(when(o.holdUntil)) +
          '. Если не придёт, заказ закроется сам, но оформить его заново можно всегда.</p>' : '') +
        '<div class="found__buttons">' +
          '<a class="btn" href="' + esc(seller.pay) + '" target="_blank" rel="noopener">Перейти к переводу</a>' +
          '<a class="btn btn--ghost" href="' + esc(seller.max) + '" target="_blank" rel="noopener">Написать в MAX</a>' +
        '</div>' +
      '</div>';
  } else if (o.payment === 'paid' && o.shipping === 'waiting') {
    next = '<div class="found__next"><p>Оплата получена, собираем посылку. ' +
           'Как отправим, здесь появится трек-номер.</p></div>';
  } else if (o.shipping === 'sent') {
    next = '<div class="found__next"><p>Посылка отправлена' +
           (o.track ? ', трек-номер <b>' + esc(o.track) + '</b>' : '') +
           '. Отследить можно на сайте перевозчика.</p></div>';
  } else if (o.payment === 'cancelled') {
    next = '<div class="found__next"><p>Заказ отменён' +
           (o.cancelReason ? ': ' + esc(o.cancelReason) : '') +
           '. Если это ошибка, напишите нам в MAX, мы всё поправим.</p></div>';
  }

  foundBox.innerHTML =
    '<div class="found__head">' +
      '<h2>Заказ ' + esc(o.number) + '</h2>' +
      // пока оплата не отмечена, про отправку покупателю говорить рано:
      // то же правило, что на странице самого заказа
      '<p class="found__badges">' + badge(PAY, o.payment) +
        (o.payment === 'paid' ? badge(SHIP, o.shipping) : '') + '</p>' +
    '</div>' +
    next +
    '<dl class="found__list">' + rowsHtml(o) + '</dl>' +
    (o.receipt
      ? '<p class="found__receipt">Чек: <a href="' + esc(o.receipt) +
        '" target="_blank" rel="noopener">открыть</a></p>' : '') +
    '<p class="found__link">Постоянная ссылка на этот заказ:<br>' +
      '<a href="' + esc(ROOT + 'order/' + o.id + '/') + '">' +
      esc(location.origin + '/order/' + o.id + '/') + '</a></p>';
  foundBox.hidden = false;
}

page.querySelector('.check__form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const button = form.querySelector('.check__send');
  const number = form.querySelector('[name="number"]').value.trim();
  const phone = form.querySelector('[name="phone"]').value.trim();

  findError.hidden = true;
  foundBox.hidden = true;

  if (!number || phone.replace(/\D/g, '').length < 4) {
    findError.textContent = 'Нужны номер заказа и телефон, который вы указывали в форме.';
    findError.hidden = false;
    return;
  }

  button.disabled = true;
  button.textContent = 'Ищем...';
  try {
    const data = await call('/api/order-find', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ number, phone }),
    });
    // заказ не нашёлся - сервер отвечает обычным ответом с пояснением,
    // а не ошибкой: не нашлось это не поломка
    if (!data.order) throw new Error(data.error || 'заказ не найден');
    renderFound(data.order);
  } catch (err) {
    findError.textContent = err.message;
    findError.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = 'Найти заказ';
  }
});

/* ============================================================ панель мастера */

const panel = page.querySelector('[data-admin]');
const list = panel.querySelector('[data-list]');
const panelError = panel.querySelector('[data-panel-error]');
const summaryBox = panel.querySelector('[data-summary]');

let me = null;
let orders = [];

const filters = {
  from: panel.querySelector('[data-from]'),
  to: panel.querySelector('[data-to]'),
  q: panel.querySelector('[data-q]'),
  payment: panel.querySelector('[data-payment]'),
  shipping: panel.querySelector('[data-shipping]'),
};

/** Кнопки и поля под состояние конкретного заказа. */
function actionsHtml(o) {
  const parts = [];

  if (o.payment !== 'paid') {
    parts.push('<button type="button" class="act act--go" data-do="paid">Оплачен</button>');
  }
  if (o.payment !== 'cancelled' && o.shipping !== 'sent') {
    parts.push('<button type="button" class="act act--off" data-do="cancelled">Отменить</button>');
  }
  if (o.payment !== 'waiting' && o.shipping !== 'sent') {
    parts.push('<button type="button" class="act" data-do="waiting">Вернуть в «ждёт оплаты»</button>');
  }

  // удаление стоит последним и отделено от остальных: оно необратимо
  parts.push('<button type="button" class="act act--del" data-do="delete">Удалить заказ</button>');

  let ship = '';
  if (o.shipping === 'sent') {
    ship = '<div class="act-row">' +
      '<span class="act-row__note">Отправлен, трек ' + esc(o.track || 'не указан') + '</span>' +
      '<button type="button" class="act" data-do="unsent">Вернуть в «ждёт отправки»</button>' +
      '</div>';
  } else if (o.payment === 'paid') {
    ship = '<div class="act-row">' +
      '<input class="act-row__input" type="text" data-track value="' + esc(o.track || '') +
      '" placeholder="трек-номер посылки">' +
      '<button type="button" class="act act--go" data-do="sent">Отметить отправленным</button>' +
      '</div>';
  } else {
    // пока заказ не оплачен, про отправку в карточке ничего не показываем
    ship = '';
  }

  return '<div class="acts">' + parts.join('') + '</div>' + ship +
    '<p class="card__error" data-card-error hidden></p>';
}

function historyHtml(o) {
  if (!o.history || !o.history.length) return '';
  return '<ul class="story">' + o.history.map((h) =>
    '<li><span>' + esc(when(h.at)) + '</span> ' + esc(h.what) + '</li>').join('') + '</ul>';
}

function cardHtml(o) {
  const toy = o.toy || {};
  const b = o.buyer || {};
  return '<article class="card" data-order="' + esc(o.id) + '">' +
    '<button type="button" class="card__line" data-open>' +
      '<span class="card__num">' + esc(o.number) + '</span>' +
      '<span class="card__date">' + esc(when(o.created, false)) + '</span>' +
      '<span class="card__toy">' + esc(toy.name || '') + '</span>' +
      '<span class="card__who">' + esc(b.name || '') + '</span>' +
      '<span class="card__price">' + esc(toy.price ? money(toy.price) : 'повтор') + '</span>' +
      '<span class="card__badges">' + badge(PAY, o.payment) + badge(SHIP, o.shipping) + '</span>' +
    '</button>' +
    '<div class="card__body" hidden>' +
      '<dl class="card__list">' + rowsHtml(o) + '</dl>' +
      historyHtml(o) +
      actionsHtml(o) +
    '</div>' +
  '</article>';
}

function renderList() {
  if (!orders.length) {
    list.innerHTML = '<p class="panel__empty">Заказов по этим условиям нет.</p>';
    return;
  }
  list.innerHTML = orders.map(cardHtml).join('');
}

/** Перерисовываем одну карточку, не трогая остальные и не теряя прокрутку. */
function replaceCard(o) {
  const at = orders.findIndex((x) => x.id === o.id);
  if (at >= 0) orders[at] = o;
  const old = list.querySelector('[data-order="' + o.id + '"]');
  if (!old) return renderList();
  const wrap = document.createElement('div');
  wrap.innerHTML = cardHtml(o);
  const fresh = wrap.firstElementChild;
  fresh.querySelector('.card__body').hidden = false;   // карточка была открыта
  old.replaceWith(fresh);
}

async function load() {
  panelError.hidden = true;
  const query = new URLSearchParams();
  Object.keys(filters).forEach((k) => {
    const v = filters[k].value.trim();
    if (v) query.set(k, v);
  });
  try {
    const data = await call('/api/orders?' + query.toString(), { auth: true }, me);
    orders = data.orders || [];
    const s = data.summary || {};
    summaryBox.textContent =
      'Всего заказов: ' + (s.total || 0) +
      '. Ждут оплаты: ' + (s.waiting || 0) +
      '. Оплачены и ждут отправки: ' + (s.toSend || 0) + '.';
    renderList();
  } catch (e) {
    panelError.hidden = false;
    panelError.textContent = 'Заказы не загрузились: ' + e.message;
  }
}

/** Что именно отправляем серверу по нажатой кнопке. */
function changesFor(card, what) {
  if (what === 'paid') return { payment: 'paid' };
  if (what === 'cancelled') return { payment: 'cancelled' };
  if (what === 'waiting') return { payment: 'waiting' };
  if (what === 'unsent') return { shipping: 'waiting' };
  if (what === 'sent') {
    return { shipping: 'sent', track: card.querySelector('[data-track]').value.trim() };
  }
  return null;
}

const ASK = {
  delete: 'Удалить заказ насовсем? Он исчезнет вместе с данными покупателя, вернуть не получится.',
  cancelled: 'Отменить этот заказ?',
  waiting: 'Вернуть заказ в состояние «ждёт оплаты»?',
  unsent: 'Вернуть заказ в состояние «ждёт отправки»?',
};

list.addEventListener('click', async (e) => {
  const opener = e.target.closest('[data-open]');
  if (opener) {
    const body = opener.parentElement.querySelector('.card__body');
    body.hidden = !body.hidden;
    return;
  }

  const btn = e.target.closest('[data-do]');
  if (!btn) return;
  const card = btn.closest('.card');
  const what = btn.dataset.do;
  const errorBox = card.querySelector('[data-card-error]');
  errorBox.hidden = true;

  // отмена и откаты необратимы наполовину, поэтому переспрашиваем
  if (ASK[what] && !confirm(ASK[what])) return;

  const id = card.dataset.order;
  const changes = what === 'delete' ? null : changesFor(card, what);
  if (what !== 'delete' && !changes) return;

  const was = btn.textContent;
  btn.disabled = true;
  btn.textContent = what === 'delete' ? 'Удаляем...' : 'Сохраняем...';
  try {
    if (what === 'delete') {
      await call('/api/order-delete', {
        method: 'POST',
        auth: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      }, me);
      orders = orders.filter((x) => x.id !== id);
      card.remove();
      load();
      return;
    }
    const data = await call('/api/order-update', {
      method: 'POST',
      auth: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ id }, changes)),
    }, me);
    replaceCard(data.order);
    load();          // сводка сверху тоже должна обновиться
  } catch (err) {
    btn.disabled = false;
    btn.textContent = was;
    errorBox.hidden = false;
    errorBox.textContent = err.message;
  }
});

panel.querySelector('[data-refresh]').addEventListener('click', load);
panel.querySelector('[data-reset]').addEventListener('click', () => {
  Object.keys(filters).forEach((k) => { filters[k].value = ''; });
  load();
});
Object.keys(filters).forEach((k) => {
  const el = filters[k];
  el.addEventListener(el.tagName === 'SELECT' || el.type === 'date' ? 'change' : 'input', debounce(load));
});

function debounce(fn) {
  let timer = null;
  return () => {
    clearTimeout(timer);
    timer = setTimeout(fn, 300);
  };
}

/* ==================================================================== старт */

async function boot() {
  let fb;
  try {
    fb = await firebase();
  } catch (e) {
    // без Firebase вход не подтвердить: для покупателя страница и так работает
    console.warn('Firebase не подключился:', e.message);
    return;
  }

  me = await whoAmI(fb);
  if (me) {
    panel.hidden = false;
    await load();
  }

  // вошли или вышли в соседней вкладке
  fb.onAuthStateChanged(fb.auth, (u) => {
    if (!!u === !!me) return;
    location.reload();
  });
}

if (document.readyState === 'complete') setTimeout(boot, 0);
else window.addEventListener('load', () => setTimeout(boot, 0));
