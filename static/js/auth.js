/* Вход в админку. Firebase Authentication, только вход, регистрации нет:
   единственный пользователь заводится вручную в консоли Firebase. */
import { initializeApp } from 'https://www.gstatic.com/firebasejs/12.18.0/firebase-app.js';
import {
  getAuth,
  setPersistence,
  browserLocalPersistence,
  browserSessionPersistence,
  signInWithEmailAndPassword,
  onAuthStateChanged,
  signOut,
} from 'https://www.gstatic.com/firebasejs/12.18.0/firebase-auth.js';
import { firebaseConfig } from './firebase-config.js';

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
auth.languageCode = 'ru';

const form = document.getElementById('signin-form');
if (form) {
  const message = document.querySelector('.message');
  const submit = form.querySelector('input[type="submit"]');
  const root = document.body.getAttribute('data-root') || '';

  const say = (text, kind) => {
    message.textContent = text;
    message.classList.add('is-shown');
    message.classList.toggle('is-error', kind === 'error');
  };

  // уже вошли - незачем показывать форму заново
  onAuthStateChanged(auth, (user) => {
    if (!user) return;
    form.hidden = true;
    document.querySelector('.signed-in').hidden = false;
    document.querySelector('.signed-in__email').textContent = user.email || '';
  });

  document.querySelector('.signed-in__out').addEventListener('click', async () => {
    await signOut(auth);
    location.reload();
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = form.email.value.trim();
    const password = form.password.value;
    const remember = form.remember.checked;

    submit.disabled = true;
    submit.value = 'Проверяем...';
    say('', null);

    try {
      // «Запомнить меня» - остаёмся в системе после закрытия браузера
      await setPersistence(auth, remember ? browserLocalPersistence : browserSessionPersistence);
      await signInWithEmailAndPassword(auth, email, password);
      say('Готово, вы вошли.');
      // админка появится следующим шагом, пока возвращаем на сайт
      setTimeout(() => { location.href = root || '/'; }, 900);
    } catch (err) {
      say(describe(err), 'error');
      submit.disabled = false;
      submit.value = 'Войти';
    }
  });
}

/** Понятный текст вместо кода ошибки Firebase. */
function describe(err) {
  switch (err && err.code) {
    case 'auth/invalid-email':
      return 'Почта введена с ошибкой.';
    case 'auth/missing-password':
      return 'Введите пароль.';
    case 'auth/invalid-credential':
    case 'auth/wrong-password':
    case 'auth/user-not-found':
      return 'Неверная почта или пароль.';
    case 'auth/user-disabled':
      return 'Этот доступ отключён.';
    case 'auth/too-many-requests':
      return 'Слишком много попыток. Подождите пару минут и попробуйте снова.';
    case 'auth/network-request-failed':
      return 'Нет связи с сервером. Проверьте интернет.';
    case 'auth/unauthorized-domain':
      return 'Этот адрес не разрешён в настройках Firebase.';
    case 'auth/configuration-not-found':
      return 'В проекте Firebase не включён вход по почте и паролю.';
    case 'auth/operation-not-allowed':
      return 'Способ входа по почте и паролю отключён в консоли Firebase.';
    default:
      return 'Войти не получилось: ' + ((err && err.code) || 'неизвестная ошибка');
  }
}
