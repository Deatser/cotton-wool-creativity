/* Общая точка входа в Firebase. Подключается лениво и ровно один раз:
   и админка каталога, и страница журнала спрашивают вход отсюда, а не
   поднимают SDK каждая по-своему. */

const SDK = 'https://www.gstatic.com/firebasejs/12.18.0/';

let ready = null;      // обещание подключения, создаётся при первом обращении

/** Подключаем Firebase один раз и только когда он действительно нужен. */
export function firebase() {
  if (!ready) {
    ready = Promise.all([
      import(SDK + 'firebase-app.js'),
      import(SDK + 'firebase-auth.js'),
      import('./firebase-config.js'),
    ]).then(([appMod, authMod, cfg]) => {
      const app = appMod.initializeApp(cfg.firebaseConfig);
      return {
        auth: authMod.getAuth(app),
        onAuthStateChanged: authMod.onAuthStateChanged,
        signOut: authMod.signOut,
      };
    }).catch((e) => {
      // неудачную попытку не запоминаем: связь могла пропасть на минуту
      ready = null;
      throw e;
    });
  }
  return ready;
}

/** Ждём первый ответ Firebase о том, вошёл пользователь или нет. */
export function whoAmI(fb) {
  return new Promise((resolve) => {
    const stop = fb.onAuthStateChanged(fb.auth, (user) => { stop(); resolve(user); });
  });
}

/** Разговор со своим сервером. Отдельная обёртка, чтобы у падений
    была понятная причина, а не голое «Failed to fetch».
    С options.auth запрос уходит с пропуском вошедшего. */
export async function call(path, options, user) {
  const opts = Object.assign({}, options);

  // Всё, что меняет или показывает служебные данные, сервер принимает
  // только с пропуском от Firebase. Пропуск живёт около часа,
  // getIdToken сам обновляет его при надобности.
  if (opts.auth) {
    delete opts.auth;
    if (!user) throw new Error('вы не вошли в админку');
    let token;
    try {
      token = await user.getIdToken();
    } catch (e) {
      throw new Error('не удалось подтвердить вход, обновите страницу');
    }
    opts.headers = Object.assign({}, opts.headers, { Authorization: 'Bearer ' + token });
  }

  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error('локальный сервер не отвечает. Запустите его командой ' +
      '«py serve.py» в папке проекта и откройте сайт по адресу, который он покажет');
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* тело может быть пустым */ }
  if (!res.ok) throw new Error((data && data.error) || 'сервер ответил ' + res.status);
  return data;
}
