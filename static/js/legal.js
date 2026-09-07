/* Всплывающие окна с условиями, политикой и контактами.

   Тексты не дублируются по страницам: они лежат отдельным куском разметки,
   который окно подгружает при первом открытии. Так текст правится в одном
   месте, страницы остаются лёгкими, и отдельная страница документов не нужна. */
(function () {
  'use strict';

  var ROOT = document.body.getAttribute('data-root') || '';
  var PAGE = ROOT + 'assets/legal.html';
  var cache = null;          // разобранный кусок с документами
  var box = null;

  /** Забираем страницу документов один раз за посещение. */
  function load() {
    if (cache) return Promise.resolve(cache);
    return fetch(PAGE)
      .then(function (r) {
        if (!r.ok) throw new Error('документы не загрузились');
        return r.text();
      })
      .then(function (html) {
        cache = new DOMParser().parseFromString(html, 'text/html');
        return cache;
      });
  }

  function build() {
    if (box) return box;
    box = document.createElement('div');
    box.className = 'legal-modal';
    box.hidden = true;
    box.innerHTML =
      '<div class="legal-modal__box" role="dialog" aria-modal="true">' +
        '<button type="button" class="legal-modal__close" aria-label="Закрыть">&times;</button>' +
        '<div class="legal-modal__head">' +
          '<div class="legal-modal__icon"></div><h2></h2>' +
        '</div>' +
        '<div class="legal-modal__body"></div>' +
      '</div>';
    document.body.appendChild(box);

    box.querySelector('.legal-modal__close').addEventListener('click', close);
    box.addEventListener('click', function (e) { if (e.target === box) close(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !box.hidden) close();
    });
    return box;
  }

  function open(name) {
    var el = build();
    el.hidden = false;
    document.body.style.overflow = 'hidden';
    el.querySelector('.legal-modal__body').innerHTML = '<p>Загружаем...</p>';

    load().then(function (doc) {
      var part = doc.querySelector('[data-legal-doc="' + name + '"]');
      if (!part) throw new Error('раздел не найден');
      el.querySelector('.legal-modal__icon').textContent = part.dataset.legalIcon || '';
      el.querySelector('.legal-modal__head h2').textContent = part.dataset.legalTitle || '';
      el.querySelector('.legal-modal__body').innerHTML = part.innerHTML;
      el.querySelector('.legal-modal__body').scrollTop = 0;
    }).catch(function () {
      // не смогли подгрузить - даём прямую ссылку на файл, а не бросаем ни с чем
      el.querySelector('.legal-modal__body').innerHTML =
        '<p>Не получилось открыть окно. Текст документа доступен ' +
        '<a href="' + PAGE + '" target="_blank" rel="noopener">отдельным файлом</a>.</p>';
    });
  }

  function close() {
    if (!box) return;
    box.hidden = true;
    document.body.style.overflow = '';
  }

  // любая ссылка с data-legal открывает своё окно
  document.addEventListener('click', function (e) {
    var link = e.target.closest('[data-legal]');
    if (!link) return;
    e.preventDefault();
    open(link.getAttribute('data-legal'));
  });

  /* ---------------- согласия на странице игрушки ---------------- */
  var agree = document.querySelector('.agree');
  if (agree) {
    var boxes = agree.querySelectorAll('input[type="checkbox"]');
    var buy = document.querySelector('[data-buy]');

    var refresh = function () {
      var all = true;
      boxes.forEach(function (c) { if (!c.checked) all = false; });
      buy.classList.toggle('is-locked', !all);
      buy.setAttribute('aria-disabled', all ? 'false' : 'true');
    };

    boxes.forEach(function (c) { c.addEventListener('change', refresh); });
    refresh();
  }
})();
