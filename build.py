# -*- coding: utf-8 -*-
"""Сборка витрины cotton wool creativity.

Читает каталог из data/toys.json и оригиналы из photos/, готовит картинки под веб
и раскладывает готовый статический сайт в site/.

Запуск:  py build.py
"""
import html
import json
import os
import re
import hashlib
import shutil
import sys
from datetime import date

import clock
import pages

from PIL import Image, ImageDraw, ImageFilter, ImageOps
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

ROOT = os.path.dirname(os.path.abspath(__file__))   # где лежит код
# На хостинге готовый сайт и каталог живут на постоянном диске, иначе
# их затрёт при первой же пересборке приложения. Локально это та же папка.
STORAGE = os.environ.get('STORAGE_DIR') or ROOT
PHOTOS = os.path.join(ROOT, 'photos')               # оригиналы, только на моём компьютере
OUT = os.path.join(STORAGE, 'site')
IMG = os.path.join(OUT, 'img')
CATALOG_FILE = os.path.join(STORAGE, 'data', 'toys.json')
# реквизиты, телефон, почта и ссылки продавца: подставляются во все страницы
SELLER_FILE = os.path.join(ROOT, 'data', 'seller.json')
# Текст простых страниц («О себе», «Покупателю») правится на живом сайте,
# поэтому лежит на постоянном диске, рядом с каталогом, а не в коде.
# Какие это страницы и что показывать, пока файла нет, знает pages.py.

# Обложка главной страницы тоже правится на самой странице, но своего файла
# у неё нет: её текст и так лежит в каталоге, разделом site. Здесь только то,
# чем заполняются недостающие поля. На хостинге каталог появился раньше этой
# правки, и без значений по умолчанию обложка осталась бы пустой.
HERO_DEFAULT = {
    'title': 'cotton wool creativity',
    'tagline_big': 'творчество из ваты',
    'tagline_small': 'по забытой технологии XIX века',
    'subtitle': 'Ватные авторские игрушки',
    'text': 'Наши ватные ёлочные украшения — **это возрождение древнего ремесла**, '
            'где каждая деталь лепится из натуральной хлопковой и льняной ваты. '
            'Никакой химии — только экологически чистые материалы, бережные '
            'к вашему здоровью и природе.\n\n'
            '**Каждая фигурка — единственная в своём роде.** Лёгкие, прочные '
            'и невероятно душевные — они превратят вашу ёлку в центр '
            'праздничного волшебства.',
    'call': 'Подарите себе роскошь ручной работы и тепло ушедшей эпохи.\n'
            'Добавьте в свой дом неповторимый шарм, который запомнится надолго!',
    'photo': 'img/logo.png',
    'alt': 'Ватная игрушка «Пёс с тюльпанами» — работа мастерской',
    # 0 - размер из оформления, иначе сторона круга в пикселях
    'photoSize': 0,
}

# адрес будущего сайта: нужен для canonical и sitemap
# Домен кириллический, поэтому адресов два. В canonical, sitemap и robots идёт
# punycode: его одинаково понимают все машины. Людям в документах показывается
# обычная кириллическая запись.
BASE_URL = 'https://xn-----8kchczebbr9d6ae4euc.xn--p1ai'
SITE_URL = 'https://игрушки-из-ваты.рф'

COVER_BIG, COVER_SM = 600, 300     # обложка в каталоге, квадрат
PHOTO_MID, PHOTO_BIG = 900, 1600   # фото на странице игрушки
JPEG_Q = 82

sys.stdout.reconfigure(encoding='utf-8')


# ----------------------------------------------------------------- картинки
def load(path):
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)          # учитываем поворот из EXIF
    if im.mode in ('RGBA', 'LA', 'P'):
        bg = Image.new('RGB', im.size, (255, 255, 255))
        im = im.convert('RGBA')
        bg.paste(im, mask=im.split()[-1])
        im = bg
    return im.convert('RGB')


def save_jpeg(im, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    im.save(dst, 'JPEG', quality=JPEG_Q, optimize=True, progressive=True)


def make_square(src, dst, side):
    """Квадратная обложка: обрезаем по центру, как в каталоге на текущем сайте."""
    if os.path.exists(dst):
        return
    im = load(src)
    im = ImageOps.fit(im, (side, side), Image.LANCZOS, centering=(0.5, 0.42))
    save_jpeg(im, dst)


def make_wide(src, dst, width):
    """Пропорциональное уменьшение по ширине. Возвращает итоговый размер."""
    if os.path.exists(dst):
        with Image.open(dst) as done:
            return done.size
    im = load(src)
    if im.width > width:
        h = round(im.height * width / im.width)
        im = im.resize((width, h), Image.LANCZOS)
    save_jpeg(im, dst)
    return im.size


def make_logo(src, dst, side):
    """Логотип: круглая вырезка с прозрачными углами, настоящий PNG.

    Через общий load() гнать нельзя - он кладёт картинку на белый фон,
    и у фавиконки появляются белые уголки.
    """
    if os.path.exists(dst):
        return
    im = ImageOps.exif_transpose(Image.open(src)).convert('RGBA')
    im = ImageOps.fit(im, (side, side), Image.LANCZOS, centering=(0.5, 0.42))

    # маску рисуем крупнее и уменьшаем: край круга выходит гладким
    big = side * 4
    mask = Image.new('L', (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    mask = mask.resize((side, side), Image.LANCZOS)

    out = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    out.save(dst, 'PNG', optimize=True)


def make_favicon(src, dst):
    """Иконка сайта: /favicon.ico из готового круглого логотипа.

    Берём не оригинал из photos/ (его на хостинге нет), а собранный logo.png:
    он лежит на постоянном диске рядом с каталогом.

    Почему .ico в корне, а не прежняя ссылка на logo.png. Роботы поиска
    забирают иконку отдельно от страниц и первым делом стучатся в /favicon.ico.
    Размеров в файле несколько: 16 и 32 - вкладка браузера, 48 - требование
    Google (сторона должна быть кратна 48), 120 - рекомендация Яндекса.
    """
    if os.path.exists(dst):
        return
    im = ImageOps.exif_transpose(Image.open(src)).convert('RGBA')
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    im.save(dst, 'ICO', sizes=[(16, 16), (32, 32), (48, 48), (120, 120)])


def make_hero(src, dst, width):
    """Фон шапки: тёплое оранжево-коричневое размытие, как на её сайте.

    Берём нижнюю центральную часть обложки - там рыжая куртка, она и даёт
    нужный тон. Размываем так, чтобы очертаний не осталось, и приглушаем,
    чтобы белый текст поверх читался.
    """
    if os.path.exists(dst):
        return
    im = load(src)
    w, h = im.size
    im = im.crop((int(w * 0.22), int(h * 0.48), int(w * 0.82), h))
    im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(80))
    im = Image.blend(im, Image.new('RGB', im.size, (138, 92, 40)), 0.38)   # тёплый рыжий
    im = Image.blend(im, Image.new('RGB', im.size, (48, 33, 16)), 0.22)    # приглушаем
    save_jpeg(im, dst)


def make_login_bg(src, dst, width):
    """Фон страницы входа: почти без размытия.

    Стекло видно только тогда, когда за ним есть детали, поэтому сильно
    размытая обложка шапки тут не годится.
    """
    if os.path.exists(dst):
        return
    im = load(src)
    h = round(im.height * width / im.width)
    im = im.resize((width, h), Image.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(2))
    im = Image.blend(im, Image.new('RGB', im.size, (30, 20, 10)), 0.18)
    save_jpeg(im, dst)


def load_env(path=os.path.join(ROOT, '.env')):
    """Простой разбор .env: KEY=VALUE, комментарии со # и пустые строки пропускаем.

    Отдельная библиотека ради шести строк не нужна.
    """
    env = {}
    if not os.path.exists(path):
        return env
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def assets_version():
    """Короткий отпечаток стилей и скриптов.

    Меняется только когда меняется сам файл, поэтому лишних сбросов кэша нет.
    """
    h = hashlib.md5()
    for sub in ('css', 'js'):
        folder = os.path.join(ROOT, 'static', sub)
        for name in sorted(os.listdir(folder)):
            with open(os.path.join(folder, name), 'rb') as f:
                h.update(name.encode('utf-8'))
                h.update(f.read())
    return h.hexdigest()[:8]


def slug_path(*parts):
    return '/'.join(parts)


# ------------------------------------------------------------------- данные
def price_text(value):
    return f'{value} руб.'


def build_toy(toy):
    """Готовит картинки одной игрушки и дополняет её данные путями."""
    if not toy['folder'] and not toy.get('media'):
        raise SystemExit(f"у игрушки «{toy['name']}» нет ни папки с фото, ни файлов")

    src_dir = os.path.join(PHOTOS, *toy['folder']) if toy['folder'] else PHOTOS
    dst_dir = os.path.join(IMG, 'igrushki', toy['slug'])

    # Игрушки, добавленные через админку, ссылаются на готовые файлы внутри site/:
    # их не нужно ни искать в photos/, ни пережимать.
    ready_cover = toy.get('coverUrl')
    if ready_cover:
        cover_web = ready_cover
        cover_web_sm = toy.get('coverUrlSmall') or ready_cover
    else:
        cover_dir = os.path.join(PHOTOS, *toy.get('cover_folder', toy['folder']))
        cover_src = os.path.join(cover_dir, toy['cover'])
        make_square(cover_src, os.path.join(dst_dir, 'cover-600.jpg'), COVER_BIG)
        make_square(cover_src, os.path.join(dst_dir, 'cover-300.jpg'), COVER_SM)
        cover_web = slug_path('img', 'igrushki', toy['slug'], 'cover-600.jpg')
        cover_web_sm = slug_path('img', 'igrushki', toy['slug'], 'cover-300.jpg')

    # у некоторых игрушек фото лежат в двух папках сразу
    search_dirs = [src_dir] + [os.path.join(PHOTOS, *fd)
                               for fd in toy.get('extra_folders', [])]

    gallery = []
    for m in toy.get('media') or []:
        item = {'mid': m['url'], 'big': m.get('full') or m['url'],
                'type': m.get('type', 'image'), 'w': 900, 'h': 900}
        real = os.path.join(OUT, *m['url'].split('/'))
        if m.get('type') != 'video' and os.path.exists(real):
            try:
                with Image.open(real) as im:
                    item['w'], item['h'] = im.size
            except Exception:
                pass
        gallery.append(item)

    for i, name in enumerate(toy['photos'] if not toy.get('media') else [], 1):
        mid = os.path.join(dst_dir, f'{i}-{PHOTO_MID}.jpg')
        big = os.path.join(dst_dir, f'{i}-{PHOTO_BIG}.jpg')

        # готовые картинки уже лежат - оригинал не нужен. Так сборка работает
        # на хостинге, куда папку photos/ мы не возим.
        if os.path.exists(mid) and os.path.exists(big):
            with Image.open(mid) as done:
                w, h = done.size
            gallery.append({
                'mid': slug_path('img', 'igrushki', toy['slug'], os.path.basename(mid)),
                'big': slug_path('img', 'igrushki', toy['slug'], os.path.basename(big)),
                'w': w, 'h': h,
            })
            continue

        src = next((os.path.join(d, name) for d in search_dirs
                    if os.path.exists(os.path.join(d, name))), None)
        if not src:
            print('  нет файла:', name, 'в', toy['folder'])
            continue
        w, h = make_wide(src, mid, PHOTO_MID)
        make_wide(src, big, PHOTO_BIG)
        gallery.append({
            'mid': slug_path('img', 'igrushki', toy['slug'], os.path.basename(mid)),
            'big': slug_path('img', 'igrushki', toy['slug'], os.path.basename(big)),
            'w': w, 'h': h,
        })

    alt = f"Ватная ёлочная игрушка «{toy['name']}»"
    if toy['size']:
        alt += f", высота {toy['size']}"

    toy = dict(toy)
    toy['cover_img'] = cover_web
    toy['cover_img_sm'] = cover_web_sm
    toy['cover_type'] = toy.get('coverType') or 'image'
    toy['gallery'] = gallery
    toy['alt'] = alt
    toy['price_text'] = price_text(toy['price']) if toy['price'] else ''
    toy['status_text'] = (
        'Игрушка продана. Можно заказать повтор: срок изготовления от 14 до 30 дней, '
        'в зависимости от сложности.'
        if toy['section'] == 'repeat' else
        'Игрушка сделана под заказ. Можно заказать похожую по картинке или фотографии: '
        'срок изготовления от 14 до 30 дней, в зависимости от сложности.'
    )

    ld = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        'name': toy['name'],
        'image': [f"{BASE_URL}/{toy['cover_img']}"],
        'description': (toy['note'] or alt),
        'offers': {
            '@type': 'Offer',
            'priceCurrency': 'RUB',
            'url': f"{BASE_URL}/igrushki/{toy['slug']}/",
            'availability': ('https://schema.org/InStock' if toy['price']
                             else 'https://schema.org/PreOrder'),
        },
    }
    if toy['price']:
        ld['offers']['price'] = str(toy['price'])
    toy['jsonld'] = json.dumps(ld, ensure_ascii=False)
    return toy


def page_file(key):
    return os.path.join(STORAGE, 'data', pages.PAGES[key]['file'])


def read_page(key):
    """Текст простой страницы. Файла может не быть вовсе: на хостинге
    постоянный диск завели раньше, чем появились эти страницы, а стартовое
    содержимое переносится туда только при первом запуске. Поэтому
    недостающее берём из значений по умолчанию, а не падаем."""
    try:
        got = json.load(open(page_file(key), encoding='utf-8'))
    except (OSError, ValueError):
        got = {}
    item = dict(pages.PAGES[key]['default'])
    item.update({k: v for k, v in got.items() if isinstance(v, str) and v.strip()})
    return item


def counted(value):
    """Число из данных, которые правит человек: там бывает и пусто, и строка."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def read_hero(site):
    """Обложка главной страницы. Лежит разделом site в самом каталоге,
    поэтому отдельного файла у неё нет."""
    # Пустое значение тоже значение: если заказчица стёрла приписку и сохранила,
    # подставлять вместо неё стартовый текст нельзя. А вот недостающего поля
    # в каталоге на хостинге просто нет - его и берём из значений по умолчанию.
    hero = dict(HERO_DEFAULT)
    hero.update({k: v for k, v in (site or {}).items()
                 if k in HERO_DEFAULT and v is not None})
    hero['photoSize'] = counted(hero.get('photoSize'))
    return hero


# **Две звёздочки** делают текст жирным. Больше никакой разметки в текстах,
# которые правит заказчица, нет: жирное начертание в обложке главной было
# с самого начала, и терять его при переводе текста в правимый нельзя.
BOLD = re.compile(r'\*\*(.+?)\*\*', re.S)


def rich(text):
    """Текст, который писал человек, в абзацы для страницы.

    Пустая строка разделяет абзацы, одиночный перенос строки остаётся
    переносом: в списках вроде способов доставки каждая строка своя.
    Сначала экранируем, потом расставляем разметку - иначе угловая скобка
    из текста уехала бы на страницу тегом.
    """
    out = []
    for para in (text or '').split('\n\n'):
        para = para.strip()
        if not para:
            continue
        safe = html.escape(para).replace('\n', '<br>')
        out.append(Markup(BOLD.sub(r'<strong>\1</strong>', safe)))
    return out


def plain(text, limit=200):
    """Тот же текст без разметки и в одну строку: для описания страницы."""
    flat = ' '.join((text or '').replace('**', '').split())
    return flat[:limit]


def main():
    data = json.load(open(CATALOG_FILE, encoding='utf-8'))
    seller = json.load(open(SELLER_FILE, encoding='utf-8'))
    # простые страницы: и текст, и название вкладки в шапке
    texts = {key: dict(read_page(key), url=meta['url'])
             for key, meta in pages.PAGES.items()}
    hero = read_hero(data.get('site'))
    # в документах адрес сайта пишется целиком, ссылкой
    seller['site_url'] = SITE_URL

    if os.path.isdir(os.path.join(OUT, 'assets')):
        shutil.rmtree(os.path.join(OUT, 'assets'))
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(IMG, exist_ok=True)

    # --- статика
    shutil.copytree(os.path.join(ROOT, 'static', 'css'), os.path.join(OUT, 'assets', 'css'))
    shutil.copytree(os.path.join(ROOT, 'static', 'js'), os.path.join(OUT, 'assets', 'js'))
    shutil.copytree(os.path.join(ROOT, 'static', 'fonts'), os.path.join(OUT, 'assets', 'fonts'),
                    ignore=shutil.ignore_patterns('google.css'))
    # Модули просят друг друга по имени без версии, и браузер отдаёт старую
    # копию из кэша: свежий admin.js падал на импорте функции, которой в старом
    # fb.js ещё не было, и админка исчезала целиком. Поэтому в копиях
    # подставляем ту же версию, что стоит в адресах из разметки.
    version = assets_version()
    # Номер набора адресов сервера админка обязана знать точно: по нему она
    # понимает, что сервер запущен старой версии. Берём его прямо из serve.py,
    # иначе число живёт в двух файлах и однажды разъезжается.
    api = ''
    try:
        found = re.search(r'^API_VERSION = (\d+)',
                          open(os.path.join(ROOT, 'serve.py'), encoding='utf-8').read(),
                          re.M)
        api = found.group(1) if found else ''
    except OSError:
        pass
    js_dir = os.path.join(OUT, 'assets', 'js')
    for name in sorted(os.listdir(js_dir)):
        if not name.endswith('.js'):
            continue
        path = os.path.join(js_dir, name)
        text = open(path, encoding='utf-8').read()
        fixed = text
        for module in ('./fb.js', './firebase-config.js'):
            fixed = fixed.replace("'" + module + "'", "'" + module + '?v=' + version + "'")
        if api:
            fixed = re.sub(r'const API_VERSION = \d+',
                           'const API_VERSION = ' + api, fixed)
        if fixed != text:
            open(path, 'w', encoding='utf-8', newline='\n').write(fixed)

    # документы, которые скачивает покупатель: отказное письмо и всё, что добавится позже
    docs = os.path.join(ROOT, 'static', 'docs')
    if os.path.isdir(docs):
        shutil.copytree(docs, os.path.join(OUT, 'assets', 'docs'))

    # --- логотип, обложка, экраны оплаты
    make_logo(os.path.join(PHOTOS, 'круг.png'), os.path.join(IMG, 'logo.png'), 400)
    make_favicon(os.path.join(IMG, 'logo.png'), os.path.join(OUT, 'favicon.ico'))
    make_hero(os.path.join(PHOTOS, 'обложка.jpg'), os.path.join(IMG, 'hero.jpg'), 1920)

    payments = []
    for src, name, alt in [
        ('платеж.jpg', 'pay-1.jpg', 'Оплата переводом: QR-код мессенджера MAX и памятка, что указать в сообщении'),
        ('платеж 1.jpg', 'pay-2.jpg', 'QR-код для перевода через Т-Банк или Сбербанк'),
    ]:
        w, h = make_wide(os.path.join(PHOTOS, src), os.path.join(IMG, name), 1200)
        payments.append({'img': f'img/{name}', 'w': w, 'h': h, 'alt': alt})

    # --- игрушки
    toys = []
    for t in data['toys']:
        toys.append(build_toy(t))
        print('готово:', t['name'])

    sections = []
    # у каждой строки жирной идёт только первая часть, остальное обычным начертанием
    srok = 'Срок изготовления игрушки от 14 до 30 дней, в зависимости от сложности.'
    titles = {
        'in_stock': {
            'strong': 'Игрушки в наличии', 'rest': '',
            'sub_strong': 'Все игрушки в единичном экземпляре.',
            'sub_rest': ' Повторы возможны только на заказ',
            'red': False, 'subtitle_red': True,
        },
        'repeat': {
            'strong': 'Реализованные игрушки',
            'rest': ' (нет в наличии). МОЖНО ЗАКАЗАТЬ ПОВТОР!',
            'sub_strong': '', 'sub_rest': srok,
            'red': True, 'subtitle_red': False,
        },
        'custom': {
            'strong': 'Игрушки сделанные под заказ',
            'rest': ' (нет в наличии). МОЖНО ЗАКАЗАТЬ ПО КАРТИНКЕ ИЛИ ФОТОГРАФИИ',
            'sub_strong': '', 'sub_rest': srok,
            'red': True, 'subtitle_red': False,
        },
    }
    for s in data['sections']:
        sections.append(dict(titles[s['id']], id=s['id']))

    env = Environment(loader=FileSystemLoader(os.path.join(ROOT, 'templates')),
                      autoescape=select_autoescape(['html']), trim_blocks=True, lstrip_blocks=True)
    env.filters['price'] = price_text
    # Версия статики: короткий отпечаток содержимого css и js. Подставляется
    # в адреса файлов, иначе после пересборки браузер отдаёт старый кэш.
    env.globals['v'] = version
    # подвал стоит на каждой странице, поэтому данные продавца нужны везде
    env.globals['seller'] = seller
    # названия вкладок простых страниц стоят в шапке, то есть на всех страницах
    env.globals['pages'] = texts

    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, 'w', encoding='utf-8', newline='\n').write(text)

    # --- главная
    write(os.path.join(OUT, 'index.html'),
          env.get_template('index.html').render(
              site=data['site'], sections=sections, toys=toys,
              hero=hero, hero_text=rich(hero['text']), hero_call=rich(hero['call']),
              root='', page='home', canonical=BASE_URL + '/'))

    # --- страницы игрушек
    for toy in toys:
        write(os.path.join(OUT, 'igrushki', toy['slug'], 'index.html'),
              env.get_template('toy.html').render(
                  site=data['site'], toy=toy, root='../../', page='toy',
                  canonical=f"{BASE_URL}/igrushki/{toy['slug']}/"))

    # --- оплата
    write(os.path.join(OUT, 'oplata', 'index.html'),
          env.get_template('payment.html').render(
              site=data['site'], payments=payments, root='../', page='payment',
              canonical=BASE_URL + '/oplata/'))

    # --- простые страницы: текст правится прямо на них. Шаблон у всех один,
    #     отличаются только содержимым.
    for key, item in texts.items():
        write(os.path.join(OUT, *item['url'].strip('/').split('/'), 'index.html'),
              env.get_template('page.html').render(
                  site=data['site'], item=item, key=key,
                  paragraphs=rich(item['text']), summary=plain(item['text']),
                  root='../', page=key, canonical=BASE_URL + '/' + item['url']))

    # --- оформление заказа. Своего адреса у страницы нет: она открывается
    #     как /order/<номер заказа>/, поэтому все ссылки внутри абсолютные.
    write(os.path.join(OUT, 'order', 'index.html'),
          env.get_template('order.html').render(
              site=data['site'], root='/', page='order', canonical='',
              delivery=seller.get('delivery') or [],
              hold_hours=seller.get('hold_hours') or 48))

    # --- проверка заказа: одна страница и покупателю, и мастеру
    write(os.path.join(OUT, 'proverit-zakaz', 'index.html'),
          env.get_template('check.html').render(
              site=data['site'], root='../', page='check', canonical=''))

    # --- настройки Firebase из .env в отдельный модуль
    #     Внимание: web-конфиг Firebase не секрет, он в любом случае уходит в браузер.
    #     .env нужен, чтобы значения не лежали в репозитории. Реально доступ
    #     ограничивают правила безопасности и список разрешённых доменов в консоли.
    dotenv = load_env()          # не env: так уже назван объект шаблонизатора

    def setting(key):
        """На своём компьютере значения берутся из .env, на хостинге -
        из переменных окружения, заданных в панели."""
        return os.environ.get(key) or dotenv.get(key, '')

    fb = {
        'apiKey': setting('FIREBASE_API_KEY'),
        'authDomain': setting('FIREBASE_AUTH_DOMAIN'),
        'projectId': setting('FIREBASE_PROJECT_ID'),
        'storageBucket': setting('FIREBASE_STORAGE_BUCKET'),
        'messagingSenderId': setting('FIREBASE_MESSAGING_SENDER_ID'),
        'appId': setting('FIREBASE_APP_ID'),
    }
    if not fb['apiKey']:
        print('ВНИМАНИЕ: .env не найден или пуст, вход работать не будет')
    write(os.path.join(OUT, 'assets', 'js', 'firebase-config.js'),
          '// собирается из .env командой build.py, руками не править' + chr(10) +
          'export const firebaseConfig = ' + json.dumps(fb, ensure_ascii=False, indent=2) + ';' + chr(10))

    # --- страница входа по секретному адресу
    #     сам адрес лежит в secret/login-path.txt, в репозиторий не попадает
    key_file = os.path.join(ROOT, 'secret', 'login-path.txt')
    login_path = os.environ.get('LOGIN_PATH', '').strip().strip('/')
    if not login_path and os.path.exists(key_file):
        login_path = open(key_file, encoding='utf-8').read().strip().strip('/')
    if login_path:
        depth = '../' * len(login_path.split('/'))
        write(os.path.join(OUT, *login_path.split('/'), 'index.html'),
              env.get_template('login.html').render(root=depth))
        print('вход:', '/' + login_path + '/')

    # --- данные каталога для поиска и выпадающего списка в шапке
    section_names = {'in_stock': 'Игрушки в наличии',
                     'repeat': 'Реализованные игрушки',
                     'custom': 'Игрушки под заказ'}
    catalog = [{
        'slug': t['slug'],
        'name': t['name'],
        'size': t['size'],
        'note': t['note'],
        'price': t['price_text'],
        'priceValue': t['price'],
        'section': section_names[t['section']],
        'sectionId': t['section'],
        'order': i,
        'url': f"igrushki/{t['slug']}/",
        'img': t['cover_img_sm'],
        'cover': t['cover_img'],
        # нужен админке: без него сохранение из запасного каталога
        # превратило бы обложку-ролик в обычную картинку
        'coverType': t['cover_type'],
        'media': [{'type': 'image', 'url': ph['mid'], 'full': ph['big']}
                  for ph in t['gallery']],
    } for i, t in enumerate(toys)]
    write(os.path.join(OUT, 'assets', 'js', 'catalog.js'),
          'window.CATALOG = ' + json.dumps(catalog, ensure_ascii=False) + ';' + chr(10))

    # --- карточка товара, добавленного через админку: рисуется из базы по ?id=
    write(os.path.join(OUT, 'tovar', 'index.html'),
          env.get_template('item.html').render(
              site=data['site'], root='../', page='toy',
              canonical=BASE_URL + '/tovar/'))

    # --- журнал действий администратора. Разметка пустая: записи подгружает
    #     logs.js по пропуску, а того, кто не вошёл, уводит на главную.
    #     В sitemap страницы нет, в robots.txt она закрыта.
    write(os.path.join(OUT, 'logs', 'index.html'),
          env.get_template('logs.html').render(
              site=data['site'], root='../', page='logs', canonical=''))

    # --- страница 404. Отдаётся по любому неизвестному адресу, поэтому
    #     все ссылки внутри неё абсолютные: root это '/'.
    write(os.path.join(OUT, '404.html'),
          env.get_template('404.html').render(
              site=data['site'], root='/', page='404', canonical=''))

    # --- тексты документов. Это не страница, а кусок разметки: его
    #     подгружает legal.js и показывает окном на любой странице.
    write(os.path.join(OUT, 'assets', 'legal.html'),
          env.get_template('docs.html').render(root='../'))

    # --- sitemap и robots
    today = clock.today().isoformat()
    urls = [BASE_URL + '/', BASE_URL + '/oplata/'] + \
           [BASE_URL + '/' + meta['url'] for meta in pages.PAGES.values()] + \
           [f"{BASE_URL}/igrushki/{t['slug']}/" for t in toys]
    body = '\n'.join(
        f'  <url><loc>{html.escape(u)}</loc><lastmod>{today}</lastmod></url>' for u in urls)
    write(os.path.join(OUT, 'sitemap.xml'),
          '<?xml version="1.0" encoding="UTF-8"?>\n'
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          f'{body}\n</urlset>\n')
    write(os.path.join(OUT, 'robots.txt'),
          'User-agent: *\nAllow: /\n'
          'Disallow: /assets/legal.html\nDisallow: /order/\n'
          'Disallow: /logs/\nDisallow: /img/logs/\n'
          'Disallow: /proverit-zakaz/\n\n'
          f'Sitemap: {BASE_URL}/sitemap.xml\n')

    print(f'\nстраниц: {len(toys) + 1 + len(pages.PAGES)}, '
          f'адресов в sitemap: {len(urls)}')


if __name__ == '__main__':
    main()
