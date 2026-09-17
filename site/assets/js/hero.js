/* Обложка главной страницы: та самая полоса с фотографией и названием.
   Покупатель видит её как обычно, вошедшему администратору в углу появляется
   кнопка «Изменить обложку»: текст правится полями, фотография заменяется
   файлом, а её размер тянется за ручку в углу самой фотографии.

   Firebase поднимаем только после полной загрузки страницы: главная должна
   открываться и тогда, когда он молчит. */

import { firebase, whoAmI, call } from './fb.js?v=57b82608';

/* Прокси хостинга не пропускает запрос с телом больше 10 МиБ, поэтому крупный
   файл уходит частями. Здесь это почти не нужно: снимок ужимается до 2000 px
   ещё в браузере. Но если ужать не вышло, файл поедет как есть. */
const PART_BYTES = 8 * 1024 * 1024;
// сторона круга в пикселях, те же границы проверяет сервер
const MIN = 120;
const MAX = 420;

const hero = document.querySelector('[data-hero]');
const photo = hero && hero.querySelector('[data-hero-photo]');

/* Значения в том виде, в каком их писал человек. Из готовой разметки их
   не собрать: переносы строк там уже стали тегами. */
let saved = {};
try {
  saved = JSON.parse(hero.querySelector('[data-hero-raw]').textContent);
} catch (e) { /* разметка старой сборки: поля будут пустыми */ }

const FIELDS = [
  ['tagline_big', 'Строка над названием', 'line'],
  ['tagline_small', 'Приписка мелким шрифтом рядом с ней', 'line'],
  ['title', 'Название', 'line'],
  ['subtitle', 'Подзаголовок оранжевым', 'line'],
  ['text', 'Текст. Между абзацами оставляйте пустую строку', 'text'],
  ['call', 'Выделенная надпись в рамке', 'call'],
  ['alt', 'Подпись к фотографии: на странице её не видно, она для поисковиков', 'line'],
];

let me = null;
let picked = null;      // выбранный, но ещё не отправленный файл
let size = 0;           // 0 - размер из оформления
let box = null;         // обёртка фотографии на время правки

/** Запрос к своему серверу от имени вошедшего администратора. */
const api = (path, options) => call(path, options, me);

/* ============================================================ размер фото */

function setSize(value, badge) {
  size = value ? Math.round(Math.max(MIN, Math.min(MAX, value))) : 0;
  if (size) hero.style.setProperty('--hero-photo', size + 'px');
  else hero.style.removeProperty('--hero-photo');
  showSize(badge);
}

function showSize(badge) {
  if (!badge) return;
  const wide = Math.round(photo.getBoundingClientRect().width);
  badge.textContent = size ? size + ' px' : wide + ' px, обычный';
}

/** Тянем ручку в углу фотографии. События указателя одинаковы для мыши
    и для пальца, поэтому отдельной ветки под телефон не нужно. */
function dragSize(grip, badge) {
  grip.addEventListener('pointerdown', (event) => {
    event.preventDefault();
    const fromX = event.clientX;
    const fromY = event.clientY;
    // Отсчитываем от заданного размера, а не от того, что видно: на узком
    // экране круг ограничен шириной телефона, и от него размер поехал бы вниз.
    const start = size || photo.getBoundingClientRect().width;

    const onMove = (e) => {
      // ручка в правом нижнем углу: вправо и вниз - больше, обратно - меньше
      setSize(start + ((e.clientX - fromX) + (e.clientY - fromY)) / 2, badge);
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove, true);
      window.removeEventListener('pointerup', onUp, true);
      window.removeEventListener('pointercancel', onUp, true);
    };
    window.addEventListener('pointermove', onMove, true);
    window.addEventListener('pointerup', onUp, true);
    window.addEventListener('pointercancel', onUp, true);
  });
}

/** На время правки фотография переезжает в обёртку с ручкой размера. */
function gripPhoto() {
  box = document.createElement('span');
  box.className = 'hero__photo-box';
  photo.replaceWith(box);
  box.appendChild(photo);

  const grip = document.createElement('button');
  grip.type = 'button';
  grip.className = 'hero__grip';
  grip.title = 'Потяните, чтобы изменить размер фотографии';
  grip.setAttribute('aria-label', 'Изменить размер фотографии');
  grip.innerHTML = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
    '<path d="M4 14v6h6M20 10V4h-6M20 4l-7 7M4 20l7-7"/></svg>';

  const badge = document.createElement('span');
  badge.className = 'hero__size';
  box.append(grip, badge);
  showSize(badge);
  dragSize(grip, badge);
  return badge;
}

function ungripPhoto() {
  if (!box) return;
  box.replaceWith(photo);
  box = null;
}

/* ============================================================ отправка фото */

/** Уменьшаем снимок прямо в браузере, до отправки: кадр с телефона весит
    5-8 МБ, а на обложке нужен максимум 600 px. Заодно из файла пропадают
    служебные данные, включая координаты съёмки. */
async function shrink(file) {
  if (!file.type.startsWith('image/')) return file;
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
  } catch (e) {
    return file;                       // не смогли - отправим как есть
  }
  const max = 2000;
  const k = Math.min(1, max / Math.max(bitmap.width, bitmap.height));
  const w = Math.round(bitmap.width * k);
  const h = Math.round(bitmap.height * k);

  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  canvas.getContext('2d').drawImage(bitmap, 0, 0, w, h);
  bitmap.close();

  const blob = await new Promise((done) => canvas.toBlob(done, 'image/jpeg', 0.92));
  if (!blob) return file;
  // именно File, а не Blob: имя нужно, чтобы сервер понял расширение
  return new File([blob], file.name.replace(/\.[^.]+$/, '') + '.jpg', { type: 'image/jpeg' });
}

/** Отправляем фотографию тем же путём, что и снимки игрушек: сервер сам
    режет её на квадраты 600 и 300 и кладёт в папку загрузок. */
async function sendPhoto(file, tell) {
  const small = await shrink(file);
  const q = '?id=hero&kind=cover&name=' + encodeURIComponent(small.name || file.name);
  if (small.size <= PART_BYTES) {
    return api('/api/upload' + q, { method: 'POST', body: small, auth: true });
  }
  const parts = Math.ceil(small.size / PART_BYTES);
  const session = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
  let answer = null;
  for (let i = 0; i < parts; i++) {
    const from = i * PART_BYTES;
    answer = await api('/api/upload' + q + '&session=' + session +
                       '&part=' + i + '&parts=' + parts + '&offset=' + from,
      { method: 'POST', body: small.slice(from, from + PART_BYTES), auth: true });
    if (tell) tell(Math.round(((i + 1) / parts) * 100));
  }
  return answer;
}

/* ================================================================== правка */

function openEditor(button) {
  button.hidden = true;
  size = Number(saved.photoSize) || 0;
  picked = null;
  const wasSrc = photo.getAttribute('src');
  const badge = gripPhoto();

  const form = document.createElement('form');
  form.className = 'hero__form';
  form.innerHTML =
    '<h2>Обложка главной страницы</h2>' +
    '<p class="hero__error"></p>' +
    '<div class="hero__fields">' +
      FIELDS.map(([name, label, kind]) => '<label>' + label +
        (kind === 'line'
          ? '<input name="' + name + '" maxlength="200">'
          : '<textarea name="' + name + '" rows="' + (kind === 'text' ? 7 : 3) +
            '"></textarea>') + '</label>').join('') +
      '<div class="hero__photo-field">' +
        '<label class="upload">Заменить фотографию' +
        '<input type="file" accept="image/*" data-hero-file></label>' +
        '<button type="button" class="btn-flat" data-act="reset">Обычный размер</button>' +
      '</div>' +
      '<p class="hero__hint">Размер фотографии меняется прямо на странице: ' +
      'потяните за круглую ручку в её правом нижнем углу. Чтобы выделить слова ' +
      'жирным, поставьте по две звёздочки с двух сторон: **важное**.</p>' +
    '</div>' +
    '<div class="hero__form-foot">' +
      '<button type="button" class="btn-flat" data-act="cancel">Отменить</button>' +
      '<button type="submit" class="btn-main">Сохранить</button>' +
    '</div>';

  const field = (name) => form.querySelector('[name="' + name + '"]');
  // значения ставим свойством, а не в разметку: иначе кавычка в тексте
  // разорвала бы атрибут
  for (const [name] of FIELDS) field(name).value = saved[name] || '';

  form.querySelector('[data-act="reset"]').addEventListener('click',
    () => setSize(0, badge));

  form.querySelector('[data-hero-file]').addEventListener('change', (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    picked = file;
    // показываем выбранный снимок сразу: так видно, как он сядет в круг
    photo.src = URL.createObjectURL(file);
  });

  const close = () => {
    form.remove();
    ungripPhoto();
    photo.setAttribute('src', wasSrc);
    setSize(Number(saved.photoSize) || 0, null);
    button.hidden = false;
  };
  form.querySelector('[data-act="cancel"]').addEventListener('click', close);
  form.addEventListener('submit', (e) => save(e, form));

  hero.append(form);
  // Прокручиваем к верху обложки, а не к самому окну правки: окно длинное,
  // и браузер уводил фотографию с ручкой размера за верхний край экрана.
  hero.scrollIntoView({ block: 'start', behavior: 'smooth' });
}

async function save(event, form) {
  event.preventDefault();
  const button = form.querySelector('button[type="submit"]');
  const error = form.querySelector('.hero__error');
  const field = (name) => form.querySelector('[name="' + name + '"]');
  button.disabled = true;
  button.textContent = 'Сохраняем...';
  error.textContent = '';

  try {
    let src = saved.photo;
    if (picked) {
      button.textContent = 'Отправляем фото...';
      const up = await sendPhoto(picked, (p) => {
        button.textContent = 'Отправляем фото... ' + p + '%';
      });
      src = up.url;
      button.textContent = 'Сохраняем...';
    }
    const body = { photo: src, photoSize: size };
    for (const [name] of FIELDS) body[name] = field(name).value;
    await api('/api/hero', {
      method: 'POST',
      auth: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    location.reload();
  } catch (e) {
    error.textContent = 'Не сохранилось: ' + e.message;
    error.scrollIntoView({ block: 'nearest' });
    button.disabled = false;
    button.textContent = 'Сохранить';
  }
}

window.addEventListener('load', async () => {
  if (!hero || !photo) return;
  try {
    me = await whoAmI(await firebase());
  } catch (e) {
    return;
  }
  if (!me) return;

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'hero__edit';
  button.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2"><path d="M4 20h4L20 8l-4-4L4 16v4z"/></svg>' +
    'Изменить обложку';
  button.addEventListener('click', () => openEditor(button));
  hero.append(button);
});
