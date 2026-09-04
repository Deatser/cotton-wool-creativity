# -*- coding: utf-8 -*-
"""Локальный сервер: отдаёт сайт, принимает файлы и правки каталога из админки.

Ни Firebase Storage, ни Firestore здесь не участвуют. Каталог лежит
в data/toys.json, файлы - в site/img/upload/. После каждой правки сайт
пересобирается, поэтому у новых игрушек сразу появляются свои страницы.

Запуск:  py serve.py        (по умолчанию http://127.0.0.1:8000)
"""
import io
import json
import os
import re
import sys
import shutil
import threading
import time
import unicodedata
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from PIL import Image, ImageOps

BASE = os.path.dirname(os.path.abspath(__file__))    # код приложения
# На хостинге данные обязаны лежать на постоянном диске: папка с кодом
# пересобирается при каждом деплое, и всё, что в ней создано, теряется.
STORAGE = os.environ.get('STORAGE_DIR') or BASE
ROOT = os.path.join(STORAGE, 'site')
DATA = os.path.join(STORAGE, 'data', 'toys.json')
UPLOAD_DIR = os.path.join(ROOT, 'img', 'upload')
UPLOAD_PREFIX = 'img/upload/'
MAX_IMAGE_BYTES = 60 * 1024 * 1024     # картинку читаем в память, поэтому скромнее
MAX_VIDEO_BYTES = 1024 * 1024 * 1024   # ролик пишем на диск потоком
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
VIDEO_EXT = {'.mp4', '.webm', '.mov', '.m4v'}
ALLOWED = IMAGE_EXT | VIDEO_EXT

# те же размеры и качество, что у остальных игрушек в build.py:
# иначе новые карточки выбивались бы из общего вида
COVER_SIZES = (600, 300)        # обложка каталога, квадрат
PHOTO_SIZES = (900, 1600)       # фото на странице и оно же при увеличении
JPEG_Q = 82
SECTIONS = ('in_stock', 'repeat', 'custom')

# Поднимать при каждом изменении набора адресов. Админка сверяет это число
# со своим и говорит, если сервер остался запущенным со старой версией.
API_VERSION = 6

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
    small = toy.get('coverUrlSmall') or ('img/igrushki/' + slug + '/cover-300.jpg')
    media = toy.get('media')
    if not media:
        media = [{'type': 'image',
                  'url': 'img/igrushki/' + slug + '/' + str(i) + '-900.jpg',
                  'full': 'img/igrushki/' + slug + '/' + str(i) + '-1600.jpg'}
                 for i in range(1, len(toy.get('photos') or []) + 1)]
    return {
        'id': slug,
        'name': toy['name'],
        'size': toy.get('size', ''),
        'note': toy.get('note', ''),
        'price': toy.get('price'),
        'section': toy['section'],
        'order': order,
        'cover': {'url': cover, 'small': small, 'type': toy.get('coverType') or 'image'},
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
    cover = item.get('cover') or {}
    toy['coverUrl'] = cover.get('url')
    toy['coverUrlSmall'] = cover.get('small')
    toy['coverType'] = cover.get('type') or 'image'
    toy['media'] = [{'type': m.get('type', 'image'), 'url': m['url'], 'full': m.get('full')}
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
            if parsed.path == '/api/reorder':
                return self.reorder()
        except Exception as e:
            return self.reply(500, {'error': str(e)})
        self.reply(404, {'error': 'сервер не знает адрес ' + parsed.path +
                         '. Похоже, serve.py запущен старой версии: '
                         'остановите его (Ctrl+C) и запустите заново'})

    @staticmethod
    def prepare_image(raw, folder, stem, kind):
        """Готовим картинку под веб: разворот по метке телефона, сжатие
        до нужных размеров и удаление служебных данных.

        Заодно из снимка пропадают координаты съёмки: телефон записывает
        в файл место, где он сделан, и без пересохранения они уехали бы
        на сайт вместе с фотографией.
        """
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im)          # снимок с телефона часто лежит боком
        if im.mode in ('RGBA', 'LA', 'P'):
            bg = Image.new('RGB', im.size, (255, 255, 255))
            im = im.convert('RGBA')
            bg.paste(im, mask=im.split()[-1])
            im = bg
        im = im.convert('RGB')

        out = {}
        if kind == 'cover':
            for side, key in zip(COVER_SIZES, ('url', 'small')):
                cut = ImageOps.fit(im, (side, side), Image.LANCZOS, centering=(0.5, 0.42))
                name = stem + '-' + str(side) + '.jpg'
                cut.save(os.path.join(folder, name), 'JPEG',
                         quality=JPEG_Q, optimize=True, progressive=True)
                out[key] = name
        else:
            for width, key in zip(PHOTO_SIZES, ('url', 'full')):
                cut = im
                if cut.width > width:
                    cut = cut.resize((width, round(cut.height * width / cut.width)), Image.LANCZOS)
                name = stem + '-' + str(width) + '.jpg'
                cut.save(os.path.join(folder, name), 'JPEG',
                         quality=JPEG_Q, optimize=True, progressive=True)
                out[key] = name
        return out

    def upload(self, query):
        length = int(self.headers.get('Content-Length') or 0)
        if not length:
            return self.reply(400, {'error': 'пустой файл'})

        name = safe_name((query.get('name') or [''])[0])
        ext = os.path.splitext(name)[1]
        if ext not in ALLOWED:
            return self.reply(415, {'error': 'такой тип файла не принимаем'})

        limit = MAX_VIDEO_BYTES if ext in VIDEO_EXT else MAX_IMAGE_BYTES
        if length > limit:
            return self.reply(413, {'error': 'файл больше ' + str(limit // 1024 // 1024) + ' МБ'})

        toy_id = safe_id((query.get('id') or [''])[0])
        folder = os.path.join(UPLOAD_DIR, toy_id)
        os.makedirs(folder, exist_ok=True)

        stem = str(int(time.time() * 1000)) + '-' + os.path.splitext(name)[0]
        here = UPLOAD_PREFIX + toy_id + '/'

        if ext in VIDEO_EXT:
            # Ролики не пережимаем, для этого нужен ffmpeg. Пишем потоком:
            # целиком в память большой файл класть нельзя, памяти на тарифе мало.
            fname = stem + ext
            left = length
            with open(os.path.join(folder, fname), 'wb') as f:
                while left > 0:
                    chunk = self.rfile.read(min(1024 * 1024, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            print('  принято видео: ' + here + fname + ' (' + str(length // 1024) + ' КБ)')
            return self.reply(200, {'url': here + fname, 'path': here + fname, 'type': 'video'})

        raw = self.rfile.read(length)
        kind = (query.get('kind') or ['photo'])[0]
        try:
            made = self.prepare_image(raw, folder, stem, kind)
        except Exception as e:
            # Предохранитель: не смогли обработать - кладём как есть.
            # Лучше тяжёлая, но живая фотография, чем отказ на пустом месте.
            fname = stem + ext
            with open(os.path.join(folder, fname), 'wb') as f:
                f.write(raw)
            print('  сжать не вышло (' + str(e) + '), сохранил как есть: ' + here + fname)
            return self.reply(200, {'url': here + fname, 'path': here + fname,
                                    'type': 'image', 'raw': True})

        result = {key: here + fname for key, fname in made.items()}
        result['path'] = result['url']
        result['type'] = 'image'
        was = length // 1024
        now = os.path.getsize(os.path.join(folder, made['url'])) // 1024
        print('  принято фото: ' + result['url'] + ' (' + str(was) + ' КБ -> ' + str(now) + ' КБ)')
        self.reply(200, result)

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

    def reorder(self):
        """Новый порядок карточек внутри одного раздела.

        Меняем местами только записи этого раздела, оставляя их на прежних
        позициях в общем списке: так порядок других разделов не съезжает.
        """
        item = self.body_json()
        section = item.get('section')
        ids = item.get('ids') or []
        if section not in SECTIONS or not ids:
            return self.reply(400, {'error': 'не указан раздел или порядок'})

        data = read_data()
        toys = data['toys']
        spots = [i for i, t in enumerate(toys) if t['section'] == section]
        by_id = {t['slug']: t for t in toys if t['section'] == section}

        ordered = [by_id[s] for s in ids if s in by_id]
        # если что-то не пришло с клиента, дописываем в конец, чтобы не потерять
        known = {t['slug'] for t in ordered}
        ordered += [toys[i] for i in spots if toys[i]['slug'] not in known]

        if len(ordered) != len(spots):
            return self.reply(400, {'error': 'список карточек не совпал с каталогом'})

        for pos, toy in zip(spots, ordered):
            toys[pos] = toy

        write_data(data)
        rebuild()
        print('  новый порядок в разделе ' + section)
        self.reply(200, {'ok': True})

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
            print('первый запуск: переношу ' + name + ' на постоянный диск', flush=True)
            shutil.copytree(src, dst)


def warm_up():
    """Готовим хранилище и пересобираем сайт. Выполняется уже после того,
    как порт открыт: перенос 70 МБ и сборка занимают время, а хостинг
    считает приложение упавшим, если оно долго не отвечает на порту."""
    try:
        prepare_storage()
    except Exception as e:
        print('не удалось подготовить хранилище: ' + repr(e), flush=True)
    try:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        os.makedirs(os.path.dirname(DATA), exist_ok=True)
    except Exception as e:
        print('не удалось создать папки: ' + repr(e), flush=True)
    try:
        rebuild()
    except Exception as e:
        print('сборка при старте не удалась: ' + repr(e), flush=True)
    print('готово к работе', flush=True)


def main():
    # Порт и адрес не угадываем: на хостинге приложение обязано слушать
    # containerPort из amvera.yaml на всех адресах, иначе снаружи будет 503.
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get('PORT', 0) or 0)
    if not port:
        port = 80 if os.environ.get('STORAGE_DIR') else 8000
    host = os.environ.get('HOST') or '0.0.0.0'

    print('serve.py запускается', flush=True)
    print('  код:       ' + BASE, flush=True)
    print('  хранилище: ' + STORAGE, flush=True)
    print('  адрес:     ' + host + ':' + str(port), flush=True)

    try:
        server = Server((host, port), Handler)
    except OSError as e:
        print('')
        print('Порт ' + str(port) + ' уже занят другой программой (' + str(e) + ').')
        print('Скорее всего, в соседнем окне остался запущен «py -m http.server».')
        print('Закройте его (Ctrl+C в том окне) и запустите заново,')
        print('либо возьмите другой порт:  py serve.py 8080')
        print('')
        sys.exit(1)

    # порт открываем сразу, тяжёлую подготовку делаем следом в отдельном потоке
    threading.Thread(target=warm_up, daemon=True).start()

    print('Сайт:      http://127.0.0.1:' + str(port) + '/', flush=True)
    print('Каталог:   ' + DATA, flush=True)
    print('Загрузки:  ' + UPLOAD_DIR, flush=True)
    print('Остановить: Ctrl+C', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
