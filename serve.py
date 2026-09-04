# -*- coding: utf-8 -*-
"""Локальный сервер: отдаёт сайт, принимает файлы и правки каталога из админки.

Ни Firebase Storage, ни Firestore здесь не участвуют. Каталог лежит
в data/toys.json, файлы - в site/img/upload/. После каждой правки сайт
пересобирается, поэтому у новых игрушек сразу появляются свои страницы.

Запуск:  py serve.py        (по умолчанию http://127.0.0.1:8000)
"""
import json
import os
import re
import sys
import shutil
import time
import unicodedata
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

BASE = os.path.dirname(os.path.abspath(__file__))    # код приложения
# На хостинге данные обязаны лежать на постоянном диске: папка с кодом
# пересобирается при каждом деплое, и всё, что в ней создано, теряется.
STORAGE = os.environ.get('STORAGE_DIR') or BASE
ROOT = os.path.join(STORAGE, 'site')
DATA = os.path.join(STORAGE, 'data', 'toys.json')
UPLOAD_DIR = os.path.join(ROOT, 'img', 'upload')
UPLOAD_PREFIX = 'img/upload/'
MAX_BYTES = 200 * 1024 * 1024          # с запасом под видео
ALLOWED = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.mp4', '.webm', '.mov', '.m4v'}
SECTIONS = ('in_stock', 'repeat', 'custom')

# Поднимать при каждом изменении набора адресов. Админка сверяет это число
# со своим и говорит, если сервер остался запущенным со старой версией.
API_VERSION = 3

sys.stdout.reconfigure(encoding='utf-8')

TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e',
    'ю': 'yu', 'я': 'ya',
}


def translit(text):
    out = []
    for ch in text:
        low = ch.lower()
        if low in TRANSLIT:
            rep = TRANSLIT[low]
            out.append(rep.capitalize() if ch.isupper() else rep)
        else:
            out.append(ch)
    return ''.join(out)


def safe_name(name):
    """Имя файла латиницей. Расширение отделяем до чистки, иначе у файла
    с кириллическим именем оно терялось целиком."""
    stem, ext = os.path.splitext(unquote(name or ''))
    stem = unicodedata.normalize('NFKD', translit(stem)).encode('ascii', 'ignore').decode()
    stem = re.sub(r'[^A-Za-z0-9_-]+', '-', stem).strip('-_') or 'file'
    return stem[:60] + ext.lower()


def slugify(name):
    """Адрес игрушки из её названия: «Дед мороз» -> «ded-moroz»."""
    out = translit(name or '').lower()
    out = re.sub(r'[^a-z0-9]+', '-', out).strip('-')
    return out[:60] or 'igrushka'


def unique_slug(base, taken):
    """Если такое имя уже занято, дописываем номер."""
    slug = base
    n = 2
    while slug in taken:
        slug = base + '-' + str(n)
        n += 1
    return slug


def safe_id(value):
    return re.sub(r'[^A-Za-z0-9_-]+', '', value or '')[:64] or 'misc'


# ------------------------------------------------------------------ каталог

def read_data():
    with open(DATA, encoding='utf-8') as f:
        return json.load(f)


def write_data(data):
    with open(DATA, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def to_admin(toy, order):
    """Запись каталога в том виде, в каком её ждёт админка в браузере."""
    slug = toy['slug']
    cover = toy.get('coverUrl') or ('img/igrushki/' + slug + '/cover-600.jpg')
    media = toy.get('media')
    if not media:
        media = [{'type': 'image', 'url': 'img/igrushki/' + slug + '/' + str(i) + '-900.jpg'}
                 for i in range(1, len(toy.get('photos') or []) + 1)]
    return {
        'id': slug,
        'name': toy['name'],
        'size': toy.get('size', ''),
        'note': toy.get('note', ''),
        'price': toy.get('price'),
        'section': toy['section'],
        'order': order,
        'cover': {'url': cover},
        'media': media,
        'staticUrl': 'igrushki/' + slug + '/',
    }


def from_admin(item, existing):
    """Правка из админки поверх записи каталога. Старые поля с оригиналами
    не выбрасываем: они пригодятся, если картинки придётся пересобрать."""
    toy = dict(existing or {})
    toy['slug'] = item['id']
    toy['name'] = (item.get('name') or '').strip()
    toy['size'] = (item.get('size') or '').strip()
    toy['note'] = (item.get('note') or '').strip()
    toy['price'] = item.get('price')
    toy['section'] = item.get('section') if item.get('section') in SECTIONS else 'in_stock'
    toy['coverUrl'] = (item.get('cover') or {}).get('url')
    toy['media'] = [{'type': m.get('type', 'image'), 'url': m['url']}
                    for m in (item.get('media') or []) if m.get('url')]
    toy.setdefault('folder', None)
    toy.setdefault('cover', None)
    toy.setdefault('photos', [])
    return toy


def rebuild():
    """Пересобираем сайт, чтобы правки попали и в статические страницы."""
    if BASE not in sys.path:
        sys.path.insert(0, BASE)
    import importlib
    import build
    importlib.reload(build)
    build.main()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, fmt, *args):
        if '/api/' in (self.path or ''):
            super().log_message(fmt, *args)

    def reply(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        length = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(length).decode('utf-8')) if length else {}

    # ------------------------------------------------------------- GET
    def do_GET(self):
        if urlparse(self.path).path == '/api/ping':
            return self.reply(200, {'ok': True, 'version': API_VERSION})
        if urlparse(self.path).path == '/api/slug':
            query = parse_qs(urlparse(self.path).query)
            name = (query.get('name') or [''])[0]
            current = (query.get('id') or [''])[0]
            taken = {t['slug'] for t in read_data()['toys'] if t['slug'] != current}
            return self.reply(200, {'slug': unique_slug(slugify(name), taken)})
        if urlparse(self.path).path == '/api/catalog':
            try:
                data = read_data()
                return self.reply(200, {'toys': [to_admin(t, i)
                                                 for i, t in enumerate(data['toys'])]})
            except Exception as e:
                return self.reply(500, {'error': str(e)})
        if urlparse(self.path).path.startswith('/api/'):
            return self.reply(404, {'error': 'сервер не знает адрес ' +
                                    urlparse(self.path).path +
                                    '. Похоже, serve.py запущен старой версии: '
                                    'остановите его (Ctrl+C) и запустите заново'})
        return super().do_GET()

    # ------------------------------------------------------------ POST
    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == '/api/upload':
                return self.upload(query)
            if parsed.path == '/api/delete':
                return self.remove(query)
            if parsed.path == '/api/toy':
                return self.save_toy()
            if parsed.path == '/api/toy-delete':
                return self.delete_toy()
        except Exception as e:
            return self.reply(500, {'error': str(e)})
        self.reply(404, {'error': 'сервер не знает адрес ' + parsed.path +
                         '. Похоже, serve.py запущен старой версии: '
                         'остановите его (Ctrl+C) и запустите заново'})

    def upload(self, query):
        length = int(self.headers.get('Content-Length') or 0)
        if not length:
            return self.reply(400, {'error': 'пустой файл'})
        if length > MAX_BYTES:
            return self.reply(413, {'error': 'файл больше 200 МБ'})

        name = safe_name((query.get('name') or [''])[0])
        if os.path.splitext(name)[1] not in ALLOWED:
            return self.reply(415, {'error': 'такой тип файла не принимаем'})

        toy_id = safe_id((query.get('id') or [''])[0])
        folder = os.path.join(UPLOAD_DIR, toy_id)
        os.makedirs(folder, exist_ok=True)

        fname = str(int(time.time() * 1000)) + '-' + name
        with open(os.path.join(folder, fname), 'wb') as f:
            f.write(self.rfile.read(length))

        rel = UPLOAD_PREFIX + toy_id + '/' + fname
        print('  принят файл: ' + rel + ' (' + str(length // 1024) + ' КБ)')
        self.reply(200, {'url': rel, 'path': rel})

    def remove(self, query):
        rel = (query.get('path') or [''])[0].lstrip('/')
        # наружу из папки загрузок не выпускаем
        if not rel.startswith(UPLOAD_PREFIX) or '..' in rel:
            return self.reply(400, {'error': 'недопустимый путь'})
        target = os.path.join(ROOT, *rel.split('/'))
        try:
            os.remove(target)
            print('  удалён файл: ' + rel)
        except FileNotFoundError:
            pass
        except OSError as e:
            return self.reply(500, {'error': str(e)})
        self.reply(200, {'ok': True})

    def save_toy(self):
        item = self.body_json()
        if not item.get('id') or not item.get('name'):
            return self.reply(400, {'error': 'нет названия игрушки'})

        data = read_data()
        toys = data['toys']
        at = next((i for i, t in enumerate(toys) if t['slug'] == item['id']), None)

        if at is None:
            # новая игрушка встаёт первой в своём разделе
            section = item.get('section')
            at = next((i for i, t in enumerate(toys) if t['section'] == section), len(toys))
            toys.insert(at, from_admin(item, None))
            print('  добавлена игрушка: ' + item['name'])
        else:
            toys[at] = from_admin(item, toys[at])
            print('  изменена игрушка: ' + item['name'])

        write_data(data)
        rebuild()
        self.reply(200, {'ok': True, 'slug': item['id']})

    def delete_toy(self):
        item = self.body_json()
        slug = item.get('id')
        data = read_data()
        before = len(data['toys'])
        data['toys'] = [t for t in data['toys'] if t['slug'] != slug]
        if len(data['toys']) == before:
            return self.reply(404, {'error': 'такой игрушки в каталоге нет'})

        write_data(data)

        # убираем за собой: страницу, картинки и загруженные файлы,
        # иначе адрес удалённой игрушки продолжает отвечать
        safe = safe_id(slug)
        for folder in (os.path.join(ROOT, 'igrushki', safe),
                       os.path.join(ROOT, 'img', 'igrushki', safe),
                       os.path.join(UPLOAD_DIR, safe)):
            shutil.rmtree(folder, ignore_errors=True)

        rebuild()
        print('  удалена игрушка: ' + str(slug))
        self.reply(200, {'ok': True})


class Server(ThreadingHTTPServer):
    # На Windows http.server по умолчанию ставит SO_REUSEADDR, и тогда можно
    # молча привязаться к уже занятому порту: сервер якобы работает, а запросы
    # достаются чужому процессу. Выключаем, чтобы занятый порт был виден сразу.
    allow_reuse_address = False


def prepare_storage():
    """Первый запуск на хостинге: переносим стартовый сайт и каталог из кода
    на постоянный диск. Уже существующее не трогаем, иначе деплой затирал бы
    то, что добавила заказчица."""
    if STORAGE == BASE:
        return
    for name in ('site', 'data'):
        src = os.path.join(BASE, name)
        dst = os.path.join(STORAGE, name)
        if os.path.isdir(src) and not os.path.exists(dst):
            print('первый запуск: переношу ' + name + ' на постоянный диск')
            shutil.copytree(src, dst)


def main():
    # на хостинге приложение обязано слушать порт из amvera.yaml (по умолчанию 80),
    # на своём компьютере удобнее 8000
    default_port = 80 if os.environ.get('STORAGE_DIR') else 8000
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get('PORT', default_port))
    prepare_storage()
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(DATA), exist_ok=True)

    # Пересобираем при старте. Без этого на хостинге не было бы страницы входа:
    # её секретный адрес не хранится в репозитории, а берётся из LOGIN_PATH,
    # и собирается она только сборщиком.
    try:
        rebuild()
    except Exception as e:
        print('сборка при старте не удалась: ' + str(e))
    try:
        host = '0.0.0.0' if os.environ.get('STORAGE_DIR') else '127.0.0.1'
        server = Server((host, port), Handler)
    except OSError as e:
        print('')
        print('Порт ' + str(port) + ' уже занят другой программой (' + str(e) + ').')
        print('Скорее всего, в соседнем окне остался запущен «py -m http.server».')
        print('Закройте его (Ctrl+C в том окне) и запустите заново,')
        print('либо возьмите другой порт:  py serve.py 8080')
        print('')
        sys.exit(1)

    print('Хранилище: ' + STORAGE)
    print('Сайт:      http://127.0.0.1:' + str(port) + '/')
    print('Каталог:   ' + DATA)
    print('Загрузки:  ' + UPLOAD_DIR)
    print('Остановить: Ctrl+C')
    server.serve_forever()


if __name__ == '__main__':
    main()
