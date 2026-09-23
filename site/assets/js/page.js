/* Простые страницы с текстом: «О себе», «Покупателю». Покупатель видит только
   текст. Вошедшему администратору под текстом появляется кнопка правки,
   и текст меняется здесь же: отдельной страницы в админке у него нет,
   потому что менять его будут редко.

   Здесь же правится название вкладки в меню: заказчица просила возможность
   переименовать её, например в «Корпоративные заказы». Название стоит
   в шапке всех страниц, поэтому после сохранения сайт пересобирается. */

import { firebase, whoAmI, call } from './fb.js?v=b0672225';

const main = document.querySelector('[data-page]');
const textBox = document.querySelector('[data-page-text]');
const KEY = main ? main.dataset.page : '';

/* Текст в том виде, в каком его писал человек. Из готовой разметки его
   не собрать: переносы строк там уже стали тегами, и правка съедала бы их. */
let now = { menu: '', heading: '', text: '' };
try {
  now = JSON.parse(document.querySelector('[data-page-raw]').textContent);
} catch (e) { /* разметка старой сборки: поля просто будут пустыми */ }

let me = null;

function openEditor(button) {
  button.hidden = true;
  textBox.hidden = true;

  const form = document.createElement('form');
  form.className = 'page__form';
  form.innerHTML =
    '<p class="page__error"></p>' +
    '<label>Название вкладки в меню<input name="menu" maxlength="40"></label>' +
    '<label>Заголовок страницы<input name="heading" maxlength="80"></label>' +
    '<label>Текст. Между абзацами оставляйте пустую строку' +
    '<textarea name="text" rows="14"></textarea></label>' +
    '<p class="page__hint">Чтобы выделить слова жирным, поставьте по две ' +
    'звёздочки с двух сторон: **важное**.</p>' +
    '<div class="page__form-foot">' +
    '<button type="button" class="btn-flat" data-act="cancel">Отменить</button>' +
    '<button type="submit" class="btn-main">Сохранить</button>' +
    '</div>';

  const field = (name) => form.querySelector('[name="' + name + '"]');
  // значения ставим свойством, а не в разметку: иначе кавычка в тексте
  // разорвала бы атрибут
  field('menu').value = now.menu || '';
  field('heading').value = now.heading || '';
  field('text').value = now.text || '';

  const close = () => {
    form.remove();
    textBox.hidden = false;
    button.hidden = false;
  };
  form.querySelector('[data-act="cancel"]').addEventListener('click', close);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const save = form.querySelector('button[type="submit"]');
    const error = form.querySelector('.page__error');
    save.disabled = true;
    save.textContent = 'Сохраняем...';
    error.textContent = '';
    try {
      await call('/api/page', {
        method: 'POST',
        auth: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          key: KEY,
          menu: field('menu').value,
          heading: field('heading').value,
          text: field('text').value,
        }),
      }, me);
      location.reload();
    } catch (e) {
      error.textContent = 'Не сохранилось: ' + e.message;
      save.disabled = false;
      save.textContent = 'Сохранить';
    }
  });

  textBox.after(form);
  field('text').focus();
}

/* Firebase поднимаем только после полной загрузки страницы: страница должна
   читаться и тогда, когда он не отвечает. */
window.addEventListener('load', async () => {
  if (!textBox) return;
  try {
    me = await whoAmI(await firebase());
  } catch (e) {
    return;
  }
  if (!me) return;

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn-main page__edit';
  button.textContent = 'Изменить текст';
  button.addEventListener('click', () => openEditor(button));
  textBox.after(button);
});
