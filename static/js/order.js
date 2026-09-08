/* Оформление заказа.

   Адрес у каждого заказа свой: /order/<длинный номер>/. Номер выдаёт браузер
   при нажатии «Купить», сервер узнаёт о заказе только когда форма отправлена.
   Поэтому одна и та же страница показывает либо форму, либо принятый заказ:
   решает ответ сервера по этому номеру. */
(function () {
  'use strict';

  var page = document.querySelector('[data-order-page]');
  if (!page) return;

  var API = '/api/order';
  var ROOT = document.body.getAttribute('data-root') || '/';
  var seller = {
    pay: page.dataset.pay,
    max: page.dataset.max,
    owner: page.dataset.owner,
    phone: page.dataset.phone,
    email: page.dataset.email
  };
  var holdHours = parseInt(page.dataset.holdHours, 10) || 48;

  var panes = {};
  page.querySelectorAll('[data-pane]').forEach(function (el) {
    panes[el.dataset.pane] = el;
  });

  function show(name) {
    Object.keys(panes).forEach(function (k) { panes[k].hidden = (k !== name); });
  }

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  var money = function (n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' ₽';
  };

  /* ------------------------------------------------- номер заказа в адресе */

  function newId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    // запасной путь для старых браузеров: тот же вид, лишь бы не повторялся
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0;
      return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }

  var params = new URLSearchParams(location.search);
  var found = location.pathname.match(/\/order\/([0-9a-zA-Z-]{8,})\/?$/);
  var id = found ? found[1] : '';
  var slug = params.get('t') || '';

  if (!id) {
    // пришли по короткой ссылке /order/?t=<игрушка>: выдаём номер и правим адрес,
    // чтобы страницу можно было открыть заново или переслать
    id = newId();
    history.replaceState(null, '', '/order/' + id + '/' + (slug ? '?t=' + encodeURIComponent(slug) : ''));
  }

  /* ------------------------------------------------------------- запуск */

  fetch(API + '?id=' + encodeURIComponent(id), { headers: { Accept: 'application/json' } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) {
      if (data && data.order) { renderDone(data.order); return; }
      startForm();
    })
    .catch(function () {
      // сервер не ответил: заказ мог быть уже принят, поэтому форму не подсовываем
      fail('Сайт сейчас не отвечает. Обновите страницу через минуту или напишите нам в MAX.');
    });

  function fail(text) {
    panes.error.querySelector('[data-error-text]').textContent = text;
    show('error');
  }

  /* --------------------------------------------------------------- форма */

  var toy = null;
  var openedAt = 0;

  function startForm() {
    var catalog = window.CATALOG || [];
    toy = catalog.filter(function (t) { return t.slug === slug; })[0];
    if (!toy) {
      fail('Мы не поняли, какая игрушка заказывается. Откройте её страницу в каталоге и нажмите «Купить».');
      return;
    }

    var form = panes.form.querySelector('.order__form');
    var q = function (sel) { return panes.form.querySelector(sel); };

    document.title = 'Заказ: ' + toy.name + ' — cotton wool creativity';
    q('[data-toy-link]').href = '/' + toy.url;
    q('[data-toy-link]').textContent = toy.name;
    q('[data-toy-img]').src = '/' + toy.img;
    q('[data-toy-img]').alt = toy.name;
    q('[data-toy-name]').textContent = toy.name;
    q('[data-toy-meta]').textContent =
      [toy.size ? 'высота ' + toy.size : '', toy.section].filter(Boolean).join(' · ');
    q('[data-hold-text]').textContent = holdText();

    if (toy.note) {
      q('[data-toy-note]').textContent = toy.note;
      q('[data-toy-note]').hidden = false;
    }

    if (toy.priceValue) {
      q('[data-toy-price]').textContent = money(toy.priceValue);
      q('[data-toy-total]').textContent = money(toy.priceValue);
      q('[data-toy-status]').textContent =
        'Игрушка в наличии в единственном экземпляре. Отправляем в течение 1-2 дней после оплаты.';
    } else {
      // повтор или работа по фотографии: цену и срок называет мастер
      q('[data-toy-price]').textContent = 'по договорённости';
      q('[data-toy-total]').textContent = 'уточним';
      q('[data-toy-status]').textContent =
        'Это заказ на повтор: срок изготовления от 14 до 30 дней. Точную цену и срок мы подтвердим письмом до оплаты.';
    }

    // способов может быть один или несколько: во втором случае это
    // переключатели, в первом - скрытое поле, читаем одинаково
    var ways = form.querySelectorAll('input[name="delivery"]');
    var addressBlock = q('[data-address-block]');
    var deliveryLine = q('[data-sum-delivery]');
    var refreshWay = function () {
      var chosen = chosenWay(form);
      addressBlock.hidden = !!chosen && chosen.dataset.address === 'no';
      deliveryLine.textContent = chosen ? (chosen.dataset.note || '') : '';
    };
    ways.forEach(function (w) { w.addEventListener('change', refreshWay); });
    refreshWay();

    form.addEventListener('submit', submit);
    show('form');
    openedAt = Date.now();
  }

  /** Выбранная доставка: отмеченный переключатель либо единственное
      скрытое поле, если способ на сайте один. */
  function chosenWay(form) {
    return form.querySelector('input[name="delivery"]:checked') ||
           form.querySelector('input[name="delivery"][type="hidden"]');
  }

  function holdText() {
    if (holdHours % 24 === 0) {
      var days = holdHours / 24;
      return days === 1 ? 'сутки' : (days === 2 ? 'двое суток' : days + ' суток');
    }
    return holdHours + ' часов';
  }

  /* ---------------------------------------------------------- проверки */

  function setError(form, name, text) {
    var box = form.querySelector('[data-error-for="' + name + '"]');
    var input = form.querySelector('[name="' + name + '"]');
    if (box) {
      box.textContent = text || '';
      box.classList.toggle('is-on', !!text);
    }
    if (input) input.classList.toggle('is-bad', !!text);
  }

  function collect(form) {
    var get = function (name) {
      var el = form.querySelector('[name="' + name + '"]');
      return el ? el.value.trim() : '';
    };
    var chosen = chosenWay(form);
    return {
      name: get('name'),
      phone: get('phone'),
      email: get('email'),
      delivery: chosen ? chosen.value : '',
      needAddress: !chosen || chosen.dataset.address !== 'no',
      zip: get('zip'),
      region: get('region'),
      city: get('city'),
      street: get('street'),
      comment: get('comment'),
      podpis: get('podpis')
    };
  }

  function check(form, v) {
    ['name', 'phone', 'email', 'zip', 'city', 'street', 'agree'].forEach(function (n) {
      setError(form, n, '');
    });
    var bad = [];
    var add = function (name, text) { setError(form, name, text); bad.push(name); };

    if (v.name.length < 5 || v.name.split(/\s+/).length < 2) {
      add('name', 'Напишите фамилию и имя полностью.');
    }
    // считаем только цифры: скобки, пробелы и дефисы люди ставят как угодно
    var digits = v.phone.replace(/\D/g, '');
    if (digits.length < 10 || digits.length > 15) {
      add('phone', 'Телефон с кодом города или оператора, 10-11 цифр.');
    }
    if (!/^[^\s@]+@[^\s@.]+\.[^\s@]{2,}$/.test(v.email)) {
      add('email', 'Проверьте адрес почты: на него уйдёт письмо с заказом.');
    }
    if (v.needAddress) {
      if (!/^\d{6}$/.test(v.zip)) add('zip', 'Индекс из шести цифр.');
      if (v.city.length < 2) add('city', 'Укажите город или посёлок.');
      if (v.street.length < 5) add('street', 'Улица, дом и квартира.');
    }
    var terms = form.querySelector('[name="agree-terms"]').checked;
    var privacy = form.querySelector('[name="agree-privacy"]').checked;
    if (!terms || !privacy) {
      add('agree', 'Чтобы отправить заказ, отметьте оба согласия.');
    }
    return bad;
  }

  /* -------------------------------------------------------- отправка */

  function submit(e) {
    e.preventDefault();
    var form = e.target;
    var button = form.querySelector('.order__send');
    var failBox = form.querySelector('[data-form-error]');
    failBox.hidden = true;

    var v = collect(form);
    var bad = check(form, v);
    if (bad.length) {
      var first = form.querySelector('[name="' + bad[0] + '"]') ||
                  form.querySelector('[data-error-for="' + bad[0] + '"]');
      if (first) first.scrollIntoView({ block: 'center', behavior: 'smooth' });
      if (first && first.focus) first.focus({ preventScroll: true });
      return;
    }

    button.disabled = true;
    button.textContent = 'Отправляем...';

    fetch(API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: id,
        toy: toy.slug,
        buyer: {
          name: v.name, phone: v.phone, email: v.email,
          delivery: v.delivery,
          zip: v.zip, region: v.region, city: v.city, street: v.street,
          comment: v.comment
        },
        agree: { terms: true, privacy: true },
        podpis: v.podpis,                 // поле-приманка, у человека оно пустое
        seconds: Math.round((Date.now() - openedAt) / 1000),
        page: location.href
      })
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok || !res.body.order) throw new Error(res.body.error || 'заказ не сохранился');
        renderDone(res.body.order);
        window.scrollTo(0, 0);
      })
      .catch(function (err) {
        button.disabled = false;
        button.textContent = 'Отправить заказ';
        // объяснение приходит от сервера и уже законченное, своё сюда
        // не дописываем: иначе выходило два приглашения в MAX подряд
        failBox.textContent = 'Заказ не отправился. ' +
          err.message.charAt(0).toUpperCase() + err.message.slice(1);
        failBox.hidden = false;
      });
  }

  /* ------------------------------------------------------- заказ принят */

  var PAY = {
    waiting: { text: 'Ждёт оплаты', cls: 'badge--wait' },
    paid: { text: 'Оплачен', cls: 'badge--ok' },
    cancelled: { text: 'Отменён', cls: 'badge--off' }
  };

  var SHIP = {
    waiting: { text: 'Ждёт отправки', cls: 'badge--wait' },
    sent: { text: 'Отправлен', cls: 'badge--ok' }
  };

  /** Что сказать покупателю в первой строке: зависит от обоих состояний. */
  function leadText(order, priced) {
    if (order.payment === 'cancelled') {
      return 'Заказ отменён' + (order.cancelReason ? ': ' + order.cancelReason : '') +
        '. Игрушка снова свободна. Если это ошибка, напишите нам в MAX.';
    }
    if (order.shipping === 'sent') {
      return 'Посылка отправлена' + (order.track ? ', трек-номер ' + order.track : '') + '.';
    }
    if (order.payment === 'paid') {
      return 'Оплата получена, собираем посылку. Трек-номер появится здесь, как отправим.';
    }
    return priced
      ? 'Заказ записан. Осталось перевести оплату.'
      : 'Заказ записан. Это повтор игрушки, поэтому цену и срок мы подтвердим, ' +
        'а оплату попросим уже после этого.';
  }

  function renderDone(order) {
    var q = function (sel) { return panes.done.querySelector(sel); };
    var priced = !!(order.toy && order.toy.price);
    var payState = PAY[order.payment] || PAY.waiting;
    var shipState = SHIP[order.shipping] || SHIP.waiting;

    document.title = 'Заказ ' + order.number + ' — cotton wool creativity';

    var payBadge = q('[data-badge-pay]');
    payBadge.className = 'badge ' + payState.cls;
    payBadge.textContent = payState.text;

    var shipBadge = q('[data-badge-ship]');
    shipBadge.className = 'badge ' + shipState.cls;
    shipBadge.textContent = shipState.text;
    // пока не оплачено, про отправку говорить рано
    shipBadge.hidden = (order.payment !== 'paid');

    q('[data-done-number]').textContent = order.number;
    q('[data-done-number-2]').textContent = order.number;
    q('[data-done-number-3]').textContent = order.number;
    q('[data-done-check]').href = ROOT + 'proverit-zakaz/';
    q('[data-done-lead]').textContent = leadText(order, priced);

    q('[data-done-total]').textContent = priced ? money(order.toy.price) : 'сумму подтвердит мастер';
    q('[data-done-phone]').textContent = seller.phone;
    q('[data-done-owner]').textContent = seller.owner;
    q('[data-done-pay]').href = seller.pay;
    q('[data-done-max]').href = seller.max;

    // платёжные кнопки нужны только пока оплата не отмечена
    panes.done.querySelector('.done__pay').hidden = (order.payment !== 'waiting');

    var hold = q('[data-done-hold]');
    hold.textContent = (order.payment === 'waiting' && order.holdUntil)
      ? 'Оплату ждём до ' + when(order.holdUntil) +
        '. Если не придёт, заказ закроется сам, но оформить его заново можно всегда.'
      : '';

    var b = order.buyer || {};
    var rows = [
      ['Игрушка', order.toy ? order.toy.name : ''],
      ['Стоимость', priced ? money(order.toy.price) : 'по договорённости'],
      ['Получатель', b.name],
      ['Телефон', b.phone],
      ['Почта', b.email],
      ['Доставка', b.deliveryName || b.delivery],
      ['Адрес', [b.zip, b.region, b.city, b.street].filter(Boolean).join(', ')],
      ['Комментарий', b.comment],
      ['Оформлен', when(order.created)],
      ['Трек-номер', order.track]
    ];
    q('[data-done-list]').innerHTML = rows
      .filter(function (r) { return r[1]; })
      .map(function (r) { return '<div><dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd></div>'; })
      .join('');

    var receipt = q('[data-done-receipt]');
    if (order.receipt) {
      receipt.innerHTML = 'Чек: <a href="' + esc(order.receipt) +
        '" target="_blank" rel="noopener">открыть</a>';
      receipt.hidden = false;
    } else {
      receipt.hidden = true;
    }

    q('[data-done-url]').textContent = location.origin + '/order/' + order.id + '/';
    show('done');
  }

  function when(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    var two = function (n) { return (n < 10 ? '0' : '') + n; };
    return two(d.getDate()) + '.' + two(d.getMonth() + 1) + '.' + d.getFullYear() +
           ' в ' + two(d.getHours()) + ':' + two(d.getMinutes());
  }
})();
