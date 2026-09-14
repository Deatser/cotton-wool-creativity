/* Страница «О себе». Покупатель видит только текст. Вошедшему администратору
   под текстом появляется кнопка правки, и текст меняется здесь же: отдельной
   страницы в админке у него нет, потому что менять его будут редко.

   Здесь же правится название вкладки в меню: заказчица просила возможность
   переименовать её, например в «Корпоративные заказы». Название стоит
   в шапке всех страниц, поэтому после сохранения сайт пересобирается. */

import { firebase, whoAmI, call } from './fb.js?v=cd3ce31a';

const textBox = document.querySelector('[data-about-text]');
const headBox = document.querySelector('[data-about-heading]');
const navLink = document.querySelector('[data-about-link]');

let me = null;

/** Текст в том же виде, в каком он лежит в файле: абзацы через пустую строку. */
function currentText() {
  return Array.from(textBox.querySelectorAll('p'))
    .map((p) => p.textContent.trim())
    .filter(Boolean)
    .join('\n\n');
}

function openEditor(button) {
  button.hidden = true;
  textBox.hidden = true;

  const form = document.createElement('form');
  form.className = 'about__form';
  form.innerHTML =
    '<p class="about__error"></p>' +
    '<label>Название вкладки в меню<input name="menu" maxlength="40"></label>' +
    '<label>Заголовок страницы<input name="heading" maxlength="80"></label>' +
    '<label>Текст. Между абзацами оставляйте пустую строку' +
    '<textarea name="text" rows="14"></textarea></label>' +
    '<div class="about__form-foot">' +
    '<button type="button" class="btn-flat" data-act="cancel">Отменить</button>' +
    '<button type="submit" class="btn-main">Сохранить</button>' +
    '</div>';

  const field = (name) => form.querySelector('[name="' + name + '"]');
  // значения ставим свойством, а не в разметку: иначе кавычка в тексте
  // разорвала бы атрибут
  field('menu').value = navLink ? navLink.textContent.trim() : 'О себе';
  field('heading').value = headBox ? headBox.textContent.trim() : '';
  field('text').value = currentText();

  const close = () => {
    form.remove();
    textBox.hidden = false;
    button.hidden = false;
  };
  form.querySelector('[data-act="cancel"]').addEventListener('click', close);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const save = form.querySelector('button[type="submit"]');
    const error = form.querySelector('.about__error');
    save.disabled = true;
    save.textContent = 'Сохраняем...';
    error.textContent = '';
    try {
      await call('/api/about', {
        method: 'POST',
        auth: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
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
  button.className = 'btn-main about__edit';
  button.textContent = 'Изменить текст';
  button.addEventListener('click', () => openEditor(button));
  textBox.after(button);
});
