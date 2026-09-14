# -*- coding: utf-8 -*-
"""Журнал действий администратора: кто, когда и что изменил в каталоге,
и возврат карточки к прежнему виду.

Записи лежат в data/logs.json, то есть на постоянном диске хостинга, рядом
с каталогом и заказами. Читает журнал только вошедший администратор,
страница /logs/ закрыта от поиска и от посторонних.

Отдельная забота - файлы. Заменённую фотографию админка удаляет сразу после
сохранения, а вместе с товаром уходит вся его папка. Поэтому в момент правки
с уходящих файлов снимается два вида копий:

  thumb - маленькая картинка (600 px), она нужна только чтобы показать
          в журнале, как выглядело раньше;
  files - полные копии всех размеров, по ним запись можно откатить.

Копии занимают место, поэтому живут только у последних ARCHIVE_KEEP записей.
У тех, что старше, остаются названия файлов, но ни картинок, ни отката.
"""
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime

import clock

from PIL import Image, ImageOps

BASE = os.path.dirname(os.path.abspath(__file__))
STORAGE = os.environ.get('STORAGE_DIR') or BASE
LOGS_FILE = os.path.join(STORAGE, 'data', 'logs.json')
ROOT = os.path.join(STORAGE, 'site')

ARCHIVE_DIR = os.path.join(ROOT, 'img', 'logs')
ARCHIVE_PREFIX = 'img/logs/'

# Сколько последних записей хранят копии файлов. Дальше журнал остаётся
# текстом: копии занимают место, а диска на тарифе мало.
ARCHIVE_KEEP = 10
# Записей в журнале всего. Одна запись весит меньше килобайта, но расти
# без границы файлу тоже незачем.
MAX_ENTRIES = 500
THUMB = 600          # длинная сторона картинки для показа
THUMB_Q = 78
# Ролик может весить под гигабайт: такой в журнал не копируем, иначе одна
# правка забьёт весь диск. Запись останется, но откатить её будет нельзя.
MAX_ARCHIVE_FILE = 80 * 1024 * 1024

SECTION_NAMES = {'in_stock': 'Игрушки в наличии',
                 'repeat': 'Реализованные игрушки',
                 'custom': 'Игрушки под заказ'}

ACTIONS = {'add': 'добавил товар',
           'edit': 'изменил товар',
           'delete': 'удалил товар',
           'reorder': 'изменил порядок карточек',
           'restore': 'вернул прежнюю версию'}

# Какие файлы прячутся за одной записью в карточке: у обложки это квадраты
# 600 и 300, у фотографии - 900 и 1600. Вернуть надо все, иначе на странице
# товара останется битая ссылка.
FILE_KEYS = {'cover': ('url', 'small'), 'photo': ('url', 'full')}

# Поля карточки, изменения которых показываем строкой «было - стало».
FIELDS = (('name', 'Название'),
          ('size', 'Высота'),
          ('price', 'Цена'),
          ('section', 'Раздел'),
          ('note', 'Примечание'))

# Загруженный файл называется «1757325600000-снеговик-900.jpg»: время загрузки
# спереди, размер сзади. Для журнала это шум, показываем середину.
STAMP = re.compile(r'^\d{10,16}-')
SIZED = re.compile(r'-(\d{2,4})$')

_lock = threading.RLock()


# ------------------------------------------------------------------ хранение

def read_all():
    if not os.path.exists(LOGS_FILE):
        return {'entries': []}
    try:
        with open(LOGS_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except (ValueError, OSError):
        # битый файл журнала не должен ронять сохранение товара
        return {'entries': []}
    data.setdefault('entries', [])
    return data


def write_all(data):
    os.makedirs(os.path.dirname(LOGS_FILE), exist_ok=True)
    tmp = LOGS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LOGS_FILE)


def find(entry_id):
    with _lock:
        return next((e for e in read_all()['entries'] if e['id'] == entry_id), None)


# ------------------------------------------------------------ копии файлов

def nice_name(url):
    """Понятное имя файла: без метки времени и без размера в конце."""
    if not url:
        return ''
    name = url.rstrip('/').split('/')[-1]
    stem, ext = os.path.splitext(name)
    stem = STAMP.sub('', stem)
    stem = SIZED.sub('', stem)
    return (stem or name) + ext


def keep_files(media, role, entry_id, seq):
    """Полные копии всех размеров. По ним запись возвращается обратно."""
    saved = []
    for key in FILE_KEYS.get(role, ('url',)):
        rel = media.get(key)
        if not rel:
            continue
        src = os.path.join(ROOT, *rel.split('/'))
        if not os.path.exists(src):
            continue
        try:
            if os.path.getsize(src) > MAX_ARCHIVE_FILE:
                print('  для отката файл слишком тяжёлый, пропускаю: ' + rel)
                continue
            folder = os.path.join(ARCHIVE_DIR, entry_id, 'files')
            os.makedirs(folder, exist_ok=True)
            name = str(seq) + '-' + key + '-' + os.path.basename(rel)
            shutil.copy2(src, os.path.join(folder, name))
            saved.append({'to': rel, 'at': ARCHIVE_PREFIX + entry_id + '/files/' + name})
        except Exception as e:
            print('  файл не сохранился в журнал (' + str(e) + '): ' + rel)
    return saved


def make_thumb(url, entry_id, seq):
    """Маленькая картинка для показа в журнале."""
    src = os.path.join(ROOT, *url.split('/'))
    if not os.path.exists(src):
        return None
    try:
        folder = os.path.join(ARCHIVE_DIR, entry_id)
        os.makedirs(folder, exist_ok=True)
        fname = str(seq) + '.jpg'
        with Image.open(src) as raw:
            im = ImageOps.exif_transpose(raw)
            if im.mode != 'RGB':
                im = im.convert('RGB')
            im.thumbnail((THUMB, THUMB), Image.LANCZOS)
            im.save(os.path.join(folder, fname), 'JPEG',
                    quality=THUMB_Q, optimize=True)
        return ARCHIVE_PREFIX + entry_id + '/' + fname
    except Exception as e:
        print('  копия для журнала не снялась (' + str(e) + '): ' + url)
        return None


def snapshot(media, role, entry_id, seq, archive=False):
    """Описание файла для журнала.

    archive=True для того, что с диска уходит: тогда рядом с картинкой
    для показа кладутся и полные копии, иначе откатывать будет нечем.
    """
    media = media or {}
    url = media.get('url') or ''
    item = {'name': nice_name(url), 'url': url,
            'type': media.get('type') or 'image', 'role': role}
    if not url:
        return item
    if archive:
        saved = keep_files(media, role, entry_id, seq)
        if saved:
            item['files'] = saved
    if item['type'] != 'video':
        thumb = make_thumb(url, entry_id, seq)
        if thumb:
            item['thumb'] = thumb
    return item


def media_items(entry):
    """Все файлы, упомянутые в записи: и старые, и новые."""
    for change in entry.get('changes') or []:
        for key in ('from', 'to'):
            value = change.get(key)
            if isinstance(value, dict):
                yield value
        for key in ('added', 'removed'):
            for value in change.get(key) or []:
                yield value
    if isinstance(entry.get('cover'), dict):
        yield entry['cover']
    for value in entry.get('saved') or []:
        yield value


def forget_archive(entry):
    """Убираем копии у старой записи. Текст и названия файлов остаются,
    но вернуть её уже нельзя: файлов на диске больше нет."""
    shutil.rmtree(os.path.join(ARCHIVE_DIR, entry['id']), ignore_errors=True)
    for item in media_items(entry):
        item.pop('thumb', None)
        item.pop('files', None)
    entry['archived'] = False


def prune(data):
    """Копии держим только у последних ARCHIVE_KEEP записей."""
    kept = 0
    for entry in data['entries']:            # список идёт от новых к старым
        if not entry.get('archived'):
            continue
        kept += 1
        if kept > ARCHIVE_KEEP:
            forget_archive(entry)

    if len(data['entries']) > MAX_ENTRIES:
        for entry in data['entries'][MAX_ENTRIES:]:
            shutil.rmtree(os.path.join(ARCHIVE_DIR, entry['id']), ignore_errors=True)
        data['entries'] = data['entries'][:MAX_ENTRIES]

    # папки без записи: остаются, если журнал переписали или потеряли
    known = {e['id'] for e in data['entries'] if e.get('archived')}
    if os.path.isdir(ARCHIVE_DIR):
        for name in os.listdir(ARCHIVE_DIR):
            if name not in known:
                shutil.rmtree(os.path.join(ARCHIVE_DIR, name), ignore_errors=True)


# -------------------------------------------------------------- разбор правок

def show(field, value):
    """Значение поля так, как его читает человек. Только для показа:
    откат берёт исходные значения из entry['restore'], а не отсюда."""
    if value is None or value == '':
        return ''
    if field == 'price':
        return '{:,}'.format(int(value)).replace(',', ' ') + ' руб.'
    if field == 'section':
        return SECTION_NAMES.get(value, value)
    return str(value)


def same(a, b):
    """Один и тот же файл: сравниваем по адресу."""
    return (a or {}).get('url') == (b or {}).get('url')


def compare(before, after, entry_id):
    """Список изменений между двумя карточками в том виде, как их шлёт админка.

    Полные копии снимаем только с того, что уходит с диска: то, что осталось
    в карточке, никуда не делось и лежит на своём месте.
    """
    changes = []
    seq = [0]

    def keep(media, role, archive):
        seq[0] += 1
        return snapshot(media, role, entry_id, seq[0], archive)

    for field, label in FIELDS:
        was, now = before.get(field), after.get(field)
        if (was if was is not None else '') == (now if now is not None else ''):
            continue
        changes.append({'kind': 'text', 'label': label,
                        'from': show(field, was), 'to': show(field, now)})

    was_cover = before.get('cover') or {}
    now_cover = after.get('cover') or {}
    if was_cover.get('url') != now_cover.get('url'):
        changes.append({'kind': 'media', 'label': 'Обложка',
                        'from': keep(was_cover, 'cover', True),
                        'to': keep(now_cover, 'cover', False)})

    was_media = before.get('media') or []
    now_media = after.get('media') or []
    gone = [m for m in was_media if not any(same(m, n) for n in now_media)]
    fresh = [m for m in now_media if not any(same(m, n) for n in was_media)]

    if gone and fresh and len(gone) == len(fresh):
        # столько же убрали, сколько добавили: читается как замена
        for old, new in zip(gone, fresh):
            changes.append({'kind': 'media', 'label': 'Фотография',
                            'from': keep(old, 'photo', True),
                            'to': keep(new, 'photo', False)})
    else:
        if fresh:
            changes.append({'kind': 'list', 'label': 'Добавлены файлы',
                            'added': [keep(m, 'photo', False) for m in fresh]})
        if gone:
            changes.append({'kind': 'list', 'label': 'Убраны файлы',
                            'removed': [keep(m, 'photo', True) for m in gone]})

    if not gone and not fresh:
        was_order = [m.get('url') for m in was_media]
        now_order = [m.get('url') for m in now_media]
        if was_order and was_order != now_order:
            changes.append({'kind': 'text', 'label': 'Порядок фотографий',
                            'from': 'прежний', 'to': 'изменён'})

    return changes


# ---------------------------------------------------------------------- откат

def needed_files(toy):
    """Все файлы, без которых карточка не соберётся."""
    need = []
    cover = toy.get('cover') or {}
    for key in FILE_KEYS['cover']:
        if cover.get(key):
            need.append(cover[key])
    for m in toy.get('media') or []:
        for key in FILE_KEYS['photo']:
            if m.get(key):
                need.append(m[key])
    return need


def can_restore(entry):
    """Можно ли вернуть эту запись. Возвращает (да/нет, причина отказа)."""
    plan = entry.get('restore') or {}
    kind = plan.get('kind')
    if kind in ('remove', 'order'):
        return True, ''          # файлы для этого не нужны
    if kind != 'toy':
        return False, 'эту запись вернуть нельзя'

    saved = {f['to'] for item in media_items(entry) for f in (item.get('files') or [])}
    missing = [rel for rel in needed_files(plan.get('toy') or {})
               if rel not in saved
               and not os.path.exists(os.path.join(ROOT, *rel.split('/')))]
    if missing:
        return False, ('копии файлов уже не хранятся, вернуть нечем '
                       '(не хватает ' + str(len(missing)) + ')')
    return True, ''


def restore_files(entry):
    """Возвращаем сохранённые файлы на прежние места.

    Существующие не трогаем: файл мог никуда не деваться, и перезаписывать
    его копией из журнала незачем.
    """
    done = 0
    for item in media_items(entry):
        for f in item.get('files') or []:
            src = os.path.join(ROOT, *f['at'].split('/'))
            dst = os.path.join(ROOT, *f['to'].split('/'))
            if not os.path.exists(src) or os.path.exists(dst):
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                done += 1
            except Exception as e:
                print('  файл не вернулся (' + str(e) + '): ' + f['to'])
    return done


# ------------------------------------------------------------------- запись

def toy_card(toy):
    """Короткая карточка игрушки для строки журнала."""
    return {'id': toy.get('id') or '',
            'name': toy.get('name') or '',
            'url': toy.get('staticUrl') or ('igrushki/' + str(toy.get('id')) + '/')}


def add(who, action, before=None, after=None, extra='', restore=None):
    """Пишем действие в журнал. Сбой журнала не должен мешать работе админки,
    поэтому наружу ошибки не летят - только в вывод сервера."""
    try:
        return _add(who, action, before, after, extra, restore)
    except Exception as e:
        print('  журнал не записался: ' + repr(e), flush=True)
        return None


def _add(who, action, before, after, extra, restore):
    now = clock.now()
    entry_id = now.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]

    entry = {
        'id': entry_id,
        'at': now.isoformat(timespec='seconds'),
        'date': now.strftime('%d.%m.%Y'),
        'time': now.strftime('%H:%M'),
        'who': who or 'администратор',
        'action': action,
        'actionText': ACTIONS.get(action, action),
        'toy': toy_card(after or before or {}),
        'extra': extra,
        'changes': [],
        'archived': False,
    }

    if before and after:
        entry['changes'] = compare(before, after, entry_id)
        if not entry['changes']:
            # нажали «Сохранить», ничего не поменяв: записывать нечего
            return None
    elif after:
        # добавление: файлы никуда не уходят, копии для отката не нужны
        entry['cover'] = snapshot(after.get('cover'), 'cover', entry_id, 1)
    elif before:
        # удаление: с диска уходит вся папка товара, сохраняем всё,
        # иначе вернуть его будет нечем
        entry['cover'] = snapshot(before.get('cover'), 'cover', entry_id, 1, archive=True)
        entry['saved'] = [snapshot(m, 'photo', entry_id, 100 + i, archive=True)
                          for i, m in enumerate(before.get('media') or [])]

    # чем откатывать: у правки и удаления это прежняя карточка целиком,
    # у добавления - просто убрать товар
    if restore is not None:
        entry['restore'] = restore
    elif before:
        entry['restore'] = {'kind': 'toy', 'toy': before}
    elif after:
        entry['restore'] = {'kind': 'remove', 'id': after.get('id'),
                            'name': after.get('name')}

    entry['archived'] = any(m.get('thumb') or m.get('files') for m in media_items(entry))

    with _lock:
        data = read_all()
        # десяток перетаскиваний подряд не должен залить журнал: идущие следом
        # перестановки одного раздела складываем в одну запись. Прежний порядок
        # при этом остаётся от самой первой - откат вернёт к состоянию до всей пачки
        last = data['entries'][0] if data['entries'] else None
        if (action == 'reorder' and last and last.get('action') == 'reorder'
                and last.get('extra') == extra and last.get('who') == entry['who']
                and (now - datetime.fromisoformat(last['at'])).total_seconds() < 300):
            last.update({'at': entry['at'], 'date': entry['date'], 'time': entry['time']})
        else:
            data['entries'].insert(0, entry)
        prune(data)
        write_all(data)
    return entry


def public(entry):
    """Запись для страницы журнала. Саму прежнюю карточку наружу не отдаём:
    браузеру она не нужна, а весит заметно."""
    out = {k: v for k, v in entry.items() if k != 'restore'}
    ok, why = can_restore(entry)
    out['restoreKind'] = (entry.get('restore') or {}).get('kind') or ''
    out['canRestore'] = ok
    out['restoreNote'] = why
    return out


def recent(limit=MAX_ENTRIES):
    """Записи для страницы журнала: от новых к старым."""
    with _lock:
        return [public(e) for e in read_all()['entries'][:limit]]
