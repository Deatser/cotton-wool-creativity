/* cotton wool creativity — общие скрипты витрины */
(function () {
  'use strict';

  /* ---- шапка темнеет после прокрутки, как на текущем сайте ---- */
  var header = document.querySelector('.site-header');
  if (header) {
    var onScroll = function () {
      header.classList.toggle('is-stuck', window.scrollY > 40);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  /* ---- меню на телефоне ---- */
  var burger = document.querySelector('.burger');
  var nav = document.querySelector('.nav');
  if (burger && nav) {
    burger.addEventListener('click', function () {
      var open = nav.classList.toggle('is-open');
      burger.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    nav.addEventListener('click', function (e) {
      if (e.target.closest('a')) {
        nav.classList.remove('is-open');
        burger.setAttribute('aria-expanded', 'false');
      }
    });
  }

  var root = document.body.getAttribute('data-root') || '';
  var catalog = window.CATALOG || [];

  /* ---- список всех игрушек под пунктом «Главная страница» ---- */
  var list = document.querySelector('[data-toy-list]');
  if (list && catalog.length) {
    list.innerHTML = catalog.map(function (t) {
      return '<li><a href="' + root + t.url + '">' + esc(t.name) + '</a></li>';
    }).join('');
  }

  /* ---- поиск по каталогу ---- */
  var toggle = document.querySelector('.search-toggle');
  var panel = document.getElementById('search');
  if (toggle && panel) {
    var input = panel.querySelector('.search__input');
    var results = panel.querySelector('.search__results');
    var clear = panel.querySelector('.search__clear');
    var active = -1;

    function openSearch() {
      panel.hidden = false;
      toggle.setAttribute('aria-expanded', 'true');
      input.focus();
    }
    function closeSearch() {
      panel.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
    }

    toggle.addEventListener('click', function () {
      if (panel.hidden) openSearch(); else closeSearch();
    });

    clear.addEventListener('click', function () {
      // поле уже пустое - значит крестик закрывает саму панель поиска
      if (!input.value) { closeSearch(); return; }
      input.value = '';
      render('');
      input.focus();
    });

    input.addEventListener('input', function () { render(input.value); });

    input.addEventListener('keydown', function (e) {
      var rows = results.querySelectorAll('.search__row');
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!rows.length) return;
        e.preventDefault();
        active += (e.key === 'ArrowDown' ? 1 : -1);
        if (active < 0) active = rows.length - 1;
        if (active >= rows.length) active = 0;
        rows.forEach(function (r, i) { r.classList.toggle('is-active', i === active); });
        rows[active].scrollIntoView({ block: 'nearest' });
      } else if (e.key === 'Enter') {
        if (active >= 0 && rows[active]) { e.preventDefault(); rows[active].click(); }
      } else if (e.key === 'Escape') {
        closeSearch();
      }
    });

    document.addEventListener('click', function (e) {
      if (panel.hidden) return;
      if (!panel.contains(e.target) && !toggle.contains(e.target)) closeSearch();
    });

    function render(q) {
      active = -1;
      q = q.trim().toLowerCase();
      if (!q) { results.hidden = true; results.innerHTML = ''; return; }

      var found = catalog.filter(function (t) {
        return (t.name + ' ' + t.note + ' ' + t.section + ' ' + t.size).toLowerCase().indexOf(q) !== -1;
      });

      results.hidden = false;
      if (!found.length) {
        results.innerHTML = '<p class="search__hint">Ничего не нашлось. Попробуйте другое слово.</p>';
        return;
      }
      results.innerHTML =
        '<p class="search__hint">Нашлось на сайте: ' + found.length + '</p>' +
        found.slice(0, 30).map(function (t) {
          var meta = [t.size, t.note || t.section].filter(Boolean).join(' · ');
          return '<a class="search__row" href="' + root + t.url + '">' +
                   '<img class="search__thumb" src="' + root + t.img + '" alt="" loading="lazy">' +
                   '<span class="search__text">' +
                     '<span class="search__name">' + mark(t.name, q) + '</span>' +
                     '<span class="search__meta">' + esc(meta) + '</span>' +
                   '</span>' +
                   (t.price ? '<span class="search__price">' + esc(t.price) + '</span>' : '') +
                 '</a>';
        }).join('');
    }
  }

  function esc(v) {
    return String(v).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function mark(text, q) {
    var i = text.toLowerCase().indexOf(q);
    if (i === -1) return esc(text);
    return esc(text.slice(0, i)) + '<mark>' + esc(text.slice(i, i + q.length)) + '</mark>' +
           esc(text.slice(i + q.length));
  }

  /* ---- просмотр фотографий во весь экран ---- */
  var gallery = document.querySelector('.gallery');
  if (!gallery) return;

  var shots = [].slice.call(gallery.querySelectorAll('[data-full]'));
  if (!shots.length) return;

  var box = document.createElement('div');
  box.className = 'lightbox';
  box.hidden = true;
  box.innerHTML =
    '<button class="lightbox__close" type="button" aria-label="Закрыть">&times;</button>' +
    '<button class="lightbox__nav prev" type="button" aria-label="Предыдущее фото">&#8249;</button>' +
    '<img alt="">' +
    '<button class="lightbox__nav next" type="button" aria-label="Следующее фото">&#8250;</button>';
  document.body.appendChild(box);

  var pic = box.querySelector('img');
  var current = 0;

  function show(i) {
    current = (i + shots.length) % shots.length;
    var el = shots[current];
    pic.src = el.getAttribute('data-full');
    pic.alt = el.getAttribute('data-alt') || '';
    box.hidden = false;
    document.body.style.overflow = 'hidden';
  }

  function close() {
    box.hidden = true;
    pic.removeAttribute('src');
    document.body.style.overflow = '';
  }

  shots.forEach(function (el, i) {
    el.addEventListener('click', function () { show(i); });
  });

  box.querySelector('.lightbox__close').addEventListener('click', close);
  box.querySelector('.prev').addEventListener('click', function () { show(current - 1); });
  box.querySelector('.next').addEventListener('click', function () { show(current + 1); });
  box.addEventListener('click', function (e) { if (e.target === box) close(); });

  document.addEventListener('keydown', function (e) {
    if (box.hidden) return;
    if (e.key === 'Escape') close();
    if (e.key === 'ArrowLeft') show(current - 1);
    if (e.key === 'ArrowRight') show(current + 1);
  });
})();
