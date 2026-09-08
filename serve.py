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
import urllib.request
from urllib.parse import urlparse, parse_qs, unquote

import jwt
from cryptography.x509 import load_pem_x509_certificate
from PIL import Image, ImageOps

import logs
import orders

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
API_VERSION = 12

sys.stdout.reconfigure(encoding='utf-8')

TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh',
    'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
    'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c',
    'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e',
    'ю': 'yu', 'я': 'ya',
}


# /order/<номер заказа>/ - страница одна, номеров много
ORDER_PATH = re.compile(r'^/order/[0-9a-zA-Z-]{8,64}/?$')


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


# ------------------------------------------------------------------- доступ
#
# Пароль в браузере защищает только кнопки: сервер видит обычный запрос и
# ничего про вход не знает. Поэтому каждый запрос, который что-то меняет,
# несёт подписанный пропуск от Firebase, а сервер проверяет подпись
# публичными ключами Google. Своих секретов на сервере не появляется.

CERTS_URL = ('https://www.googleapis.com/robot/v1/metadata/x509/'
             'securetoken@system.gserviceaccount.com')
CERTS_TTL = 3600
_certs = {'at': 0.0, 'keys': {}}


class Denied(Exception):
    """Пропуск не подошёл: запрос выполнять нельзя."""


def setting(key, default=''):
    """Значение из окружения, а на своём компьютере - из .env."""
    if os.environ.get(key):
        return os.environ[key]
    path = os.path.join(BASE, '.env')
    if os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                name, value = line.split('=', 1)
                if name.strip() == key:
                    return value.strip().strip('"').strip("'")
    return default


def google_certs(force=False):
    """Публичные ключи Google. Они меняются, поэтому обновляем раз в час."""
    now = time.time()
    if _certs['keys'] and not force and now - _certs['at'] < CERTS_TTL:
        return _certs['keys']
    with urllib.request.urlopen(CERTS_URL, timeout=20) as r:
        _certs['keys'] = json.load(r)
    _certs['at'] = now
    return _certs['keys']


def check_pass(header):
    """Проверяем пропуск. Возвращает почту вошедшего или бросает Denied."""
    if not header or not header.lower().startswith('bearer '):
        raise Denied('нужно войти в админку')
    token = header.split(' ', 1)[1].strip()

    project = setting('FIREBASE_PROJECT_ID')
    if not project:
        raise Denied('на сервере не задан FIREBASE_PROJECT_ID')

    try:
        kid = jwt.get_unverified_header(token).get('kid')
    except Exception:
        raise Denied('пропуск не читается')

    keys = google_certs()
    if kid not in keys:
        keys = google_certs(force=True)      # ключи могли смениться только что
    if kid not in keys:
        raise Denied('пропуск подписан незнакомым ключом')

    try:
        public = load_pem_x509_certificate(keys[kid].encode()).public_key()
        claims = jwt.decode(token, public, algorithms=['RS256'], audience=project,
                            issuer='https://securetoken.google.com/' + project)
    except jwt.ExpiredSignatureError:
        raise Denied('срок пропуска истёк, обновите страницу')
    except Exception as e:
        raise Denied('пропуск не подошёл: ' + e.__class__.__name__)

    email = claims.get('email') or ''
    allowed = [x.strip().lower() for x in setting('ADMIN_EMAILS').split(',') if x.strip()]
    if allowed and email.lower() not in allowed:
        raise Denied('этой учётной записи вход в админку не разрешён')
    return email


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
    who = ''          # почта вошедшего, заполняется в allowed()

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def list_directory(self, path):
        """Содержимое папок наружу не отдаём.

        Страница входа лежит по секретному адресу вида /login/<длинный ключ>/.
        SimpleHTTPRequestHandler по умолчанию рисует список файлов для любой
        папки без index.html, поэтому по адресу /login/ посторонний читал бы
        этот ключ обычной ссылкой.
        """
        self.send_error(404)
        return None

    def send_error(self, code, message=None, explain=None):
        """На 404 показываем свою страницу вместо служебной заглушки."""
        page = os.path.join(ROOT, '404.html')
        if code == 404 and self.command in ('GET', 'HEAD') and os.path.exists(page):
            with open(page, 'rb') as f:
                body = f.read()
            self.send_response(404)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if self.command == 'GET':
                self.wfile.write(body)
            return
        return super().send_error(code, message, explain)

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
            if not self.allowed():
                return
            query = parse_qs(urlparse(self.path).query)
            name = (query.get('name') or [''])[0]
            current = (query.get('id') or [''])[0]
            taken = {t['slug'] for t in read_data()['toys'] if t['slug'] != current}
            return self.reply(200, {'slug': unique_slug(slugify(name), taken)})
        if urlparse(self.path).path == '/api/order':
            # заказ читает сам покупатель по своей ссылке: пароля у него нет,
            # а угадать адрес из 36 знаков нельзя
            query = parse_qs(urlparse(self.path).query)
            order = orders.find((query.get('id') or [''])[0])
            # заказа ещё нет - это обычное дело: покупатель только открыл форму.
            # Отвечаем пустотой, а не ошибкой, иначе в консоли браузера
            # у каждого покупателя горел бы красный 404
            return self.reply(200, {'order': orders.public(order) if order else None})
        if urlparse(self.path).path == '/api/logs':
            # журнал действий видит только вошедший администратор
            if not self.allowed():
                return
            try:
                return self.reply(200, {'entries': logs.recent(),
                                        'keep': logs.ARCHIVE_KEEP})
            except Exception as e:
                return self.reply(500, {'error': str(e)})
        if urlparse(self.path).path == '/api/orders':
            # весь список заказов виден только вошедшему мастеру
            if not self.allowed():
                return
            query = parse_qs(urlparse(self.path).query)
            one = lambda key: (query.get(key) or [''])[0].strip()
            try:
                rows = orders.listing(frm=one('from'), to=one('to'), query=one('q'),
                                      payment=one('payment'), shipping=one('shipping'))
                return self.reply(200, {'orders': rows, 'summary': orders.summary()})
            except Exception as e:
                return self.reply(500, {'error': str(e)})
        if urlparse(self.path).path == '/api/catalog':
            try:
                data = read_data()
                return self.reply(200, {'toys': [to_admin(t, i)
                                                 for i, t in enumerate(data['toys'])]})
            except Exception as e:
                return self.reply(500, {'error': str(e)})
        # у каждого заказа свой адрес /order/<номер>/, а страница одна:
        # папки с таким именем на диске нет и быть не должно
        if ORDER_PATH.match(urlparse(self.path).path):
            self.path = '/order/index.html'
            return super().do_GET()
        if urlparse(self.path).path.startswith('/api/'):
            return self.reply(404, {'error': 'сервер не знает адрес ' +
                                    urlparse(self.path).path +
                                    '. Похоже, serve.py запущен старой версии: '
                                    'остановите его (Ctrl+C) и запустите заново'})
        return super().do_GET()

    # ------------------------------------------------------------ POST
    def allowed(self):
        """Пускаем дальше только с действующим пропуском."""
        try:
            who = check_pass(self.headers.get('Authorization'))
        except Denied as e:
            print('  отказано: ' + str(e) + '  ' + self.path)
            self.reply(401, {'error': str(e)})
            return False
        except Exception as e:
            # ключи Google не скачались, сети нет и тому подобное
            print('  проверить пропуск не удалось: ' + repr(e))
            self.reply(503, {'error': 'не удалось проверить вход, попробуйте ещё раз'})
            return False
        self.who = who
        return True

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        # заказ оформляет покупатель, пропуска у него нет: эта точка открыта,
        # а от роботов её закрывают проверки внутри orders.py
        if parsed.path == '/api/order':
            return self.new_order()
        # покупатель ищет свой заказ по номеру и телефону: пропуска у него нет
        if parsed.path == '/api/order-find':
            return self.find_order()
        # все остальные точки меняют данные сайта и без пропуска не работают
        if parsed.path.startswith('/api/') and not self.allowed():
            return
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
            if parsed.path == '/api/restore':
                return self.restore_entry()
            if parsed.path == '/api/order-update':
                return self.update_order()
            if parsed.path == '/api/order-delete':
                return self.delete_order()
        except Exception as e:
            return self.reply(500, {'error': str(e)})
        self.reply(404, {'error': 'сервер не знает адрес ' + parsed.path +
                         '. Похоже, serve.py запущен старой версии: '
                         'остановите его (Ctrl+C) и запустите заново'})

    def visitor(self):
        """Адрес покупателя. На хостинге запрос идёт через прокси, поэтому
        реальный адрес приходит заголовком, а не в соединении."""
        head = self.headers.get('X-Forwarded-For') or ''
        return head.split(',')[0].strip() or self.client_address[0]

    def new_order(self):
        """Форма заказа с сайта: записываем и отправляем письма."""
        try:
            payload = self.body_json()
        except ValueError:
            return self.reply(400, {'error': 'заказ пришёл в непонятном виде'})
        try:
            order = orders.create(payload, self.visitor(), read_data()['toys'])
        except orders.Refused as e:
            print('  заказ отклонён: ' + str(e), flush=True)
            return self.reply(400, {'error': str(e)})
        except Exception as e:
            print('  заказ не сохранился: ' + repr(e), flush=True)
            return self.reply(500, {'error': 'сайт не смог записать заказ'})
        print('  заказ ' + order['number'] + ': ' + order['toy']['name'] +
              ', ' + order['buyer']['email'], flush=True)
        return self.reply(200, {'order': orders.public(order)})

    def find_order(self):
        """Покупатель смотрит свой заказ по номеру и телефону."""
        try:
            payload = self.body_json()
        except ValueError:
            return self.reply(400, {'error': 'запрос пришёл в непонятном виде'})
        try:
            order = orders.find_by_number(payload.get('number'), payload.get('phone'),
                                          ip=self.visitor())
        except orders.Refused as e:
            return self.reply(400, {'error': str(e)})
        except Exception as e:
            print('  поиск заказа не удался: ' + repr(e), flush=True)
            return self.reply(500, {'error': 'сайт не смог найти заказ'})
        if not order:
            # Ничего не нашлось - это обычный исход поиска, а не сбой, поэтому
            # отвечаем 200: иначе у покупателя, опечатавшегося в номере,
            # в консоли браузера горел бы красный 404.
            # Что именно не совпало, не уточняем: иначе по номерам можно было бы
            # выяснять, какие заказы вообще существуют.
            return self.reply(200, {'order': None,
                                    'error': 'заказ с таким номером и телефоном не найден. '
                                    'Проверьте номер заказа и тот телефон, '
                                    'который вы указывали в форме'})
        return self.reply(200, {'order': orders.public(order)})

    def update_order(self):
        """Мастер отмечает оплату, отмену или отправку."""
        payload = self.body_json() or {}
        changes = {k: payload[k] for k in ('payment', 'shipping', 'track', 'receipt')
                   if k in payload}
        if not changes:
            return self.reply(400, {'error': 'менять нечего'})
        try:
            order = orders.update(payload.get('id'), changes, who=self.who)
        except orders.Refused as e:
            return self.reply(400, {'error': str(e)})
        print('  заказ ' + str(payload.get('id'))[:8] + ': ' +
              ', '.join(sorted(changes)), flush=True)
        return self.reply(200, {'order': order})

    def delete_order(self):
        """Мастер убирает лишний заказ насовсем."""
        payload = self.body_json() or {}
        try:
            orders.remove(payload.get('id'))
        except orders.Refused as e:
            return self.reply(400, {'error': str(e)})
        print('  заказ удалён: ' + str(payload.get('id'))[:8], flush=True)
        return self.reply(200, {'ok': True})

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
            was, action = None, 'add'
            print('  добавлена игрушка: ' + item['name'])
        else:
            was, action = to_admin(toys[at], at), 'edit'
            toys[at] = from_admin(item, toys[at])
            print('  изменена игрушка: ' + item['name'])

        write_data(data)
        # Журнал пишем до пересборки: старые файлы ещё на месте, и с них
        # успевает сняться копия для страницы /logs/.
        logs.add(self.who, action, was, to_admin(toys[at], at))
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
        # прежний порядок запоминаем до перестановки: только по нему журнал
        # сможет вернуть раздел к тому, как было
        was_order = [toys[i]['slug'] for i in spots]

        ordered = [by_id[s] for s in ids if s in by_id]
        # если что-то не пришло с клиента, дописываем в конец, чтобы не потерять
        known = {t['slug'] for t in ordered}
        ordered += [toys[i] for i in spots if toys[i]['slug'] not in known]

        if len(ordered) != len(spots):
            return self.reply(400, {'error': 'список карточек не совпал с каталогом'})

        for pos, toy in zip(spots, ordered):
            toys[pos] = toy

        write_data(data)
        logs.add(self.who, 'reorder', extra=logs.SECTION_NAMES.get(section, section),
                 restore={'kind': 'order', 'section': section, 'order': was_order})
        rebuild()
        print('  новый порядок в разделе ' + section)
        self.reply(200, {'ok': True})

    def delete_toy(self):
        item = self.body_json()
        slug = item.get('id')
        data = read_data()
        was = next(((i, t) for i, t in enumerate(data['toys']) if t['slug'] == slug), None)
        if was is None:
            return self.reply(404, {'error': 'такой игрушки в каталоге нет'})
        data['toys'] = [t for t in data['toys'] if t['slug'] != slug]

        write_data(data)
        # запись в журнал делаем раньше уборки: обложку удаляемой игрушки
        # нужно успеть скопировать, иначе в журнале останется битая картинка
        logs.add(self.who, 'delete', before=to_admin(was[1], was[0]))

        self.sweep_toy(slug)
        rebuild()
        print('  удалена игрушка: ' + str(slug))
        self.reply(200, {'ok': True})

    @staticmethod
    def sweep_toy(slug):
        """Убираем за собой: страницу, картинки и загруженные файлы,
        иначе адрес удалённой игрушки продолжает отвечать."""
        safe = safe_id(slug)
        for folder in (os.path.join(ROOT, 'igrushki', safe),
                       os.path.join(ROOT, 'img', 'igrushki', safe),
                       os.path.join(UPLOAD_DIR, safe)):
            shutil.rmtree(folder, ignore_errors=True)

    def restore_entry(self):
        """Возврат к прежней версии по записи журнала.

        Сам возврат тоже попадает в журнал: история остаётся правдивой,
        и откат можно откатить.
        """
        entry = logs.find((self.body_json() or {}).get('id'))
        if not entry:
            return self.reply(404, {'error': 'такой записи в журнале нет'})
        can, why = logs.can_restore(entry)
        if not can:
            return self.reply(400, {'error': why})

        plan = entry['restore']
        data = read_data()
        toys = data['toys']

        if plan['kind'] == 'order':
            return self.restore_order(data, plan)

        if plan['kind'] == 'remove':
            # откат добавления: товар надо убрать
            slug = plan.get('id')
            at = next((i for i, t in enumerate(toys) if t['slug'] == slug), None)
            if at is None:
                return self.reply(400, {'error': 'этот товар уже удалён'})
            was = to_admin(toys[at], at)
            data['toys'] = [t for t in toys if t['slug'] != slug]
            write_data(data)
            logs.add(self.who, 'restore', before=was,
                     extra='отменил добавление')
            self.sweep_toy(slug)
            rebuild()
            print('  откат: убрана игрушка ' + str(slug))
            return self.reply(200, {'ok': True, 'kind': 'remove'})

        # откат правки или удаления: возвращаем карточку целиком
        logs.restore_files(entry)
        toy = plan['toy']
        at = next((i for i, t in enumerate(toys) if t['slug'] == toy['id']), None)
        if at is None:
            at = max(0, min(int(toy.get('order') or 0), len(toys)))
            toys.insert(at, from_admin(toy, None))
            was = None
            print('  откат: игрушка ' + toy['name'] + ' возвращена в каталог')
        else:
            was = to_admin(toys[at], at)
            toys[at] = from_admin(toy, toys[at])
            print('  откат: игрушка ' + toy['name'] + ' возвращена к прежнему виду')

        write_data(data)
        logs.add(self.who, 'restore', was, to_admin(toys[at], at))
        rebuild()
        self.reply(200, {'ok': True, 'slug': toy['id']})

    def restore_order(self, data, plan):
        """Возврат прежнего порядка карточек в разделе."""
        toys = data['toys']
        section = plan['section']
        spots = [i for i, t in enumerate(toys) if t['section'] == section]
        by_id = {t['slug']: t for t in toys if t['section'] == section}
        was_order = [toys[i]['slug'] for i in spots]

        ordered = [by_id[s] for s in plan['order'] if s in by_id]
        known = {t['slug'] for t in ordered}
        ordered += [toys[i] for i in spots if toys[i]['slug'] not in known]
        if len(ordered) != len(spots):
            return self.reply(400, {'error': 'состав раздела с тех пор изменился, '
                                             'прежний порядок не восстановить'})
        if was_order == [t['slug'] for t in ordered]:
            return self.reply(400, {'error': 'порядок и так этот'})

        for pos, toy in zip(spots, ordered):
            toys[pos] = toy
        write_data(data)
        logs.add(self.who, 'restore',
                 extra='вернул порядок в разделе «' +
                       logs.SECTION_NAMES.get(section, section) + '»',
                 restore={'kind': 'order', 'section': section, 'order': was_order})
        rebuild()
        print('  откат: прежний порядок в разделе ' + section)
        self.reply(200, {'ok': True, 'kind': 'order'})


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
