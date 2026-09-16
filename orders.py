# -*- coding: utf-8 -*-
"""Заказы: приём формы с сайта, хранение и смена состояния мастером.

Заказы лежат рядом с каталогом, в data/orders.json, то есть на постоянном
диске хостинга.

Писем сайт не шлёт. Автоматическое письмо уходит в спам, и покупатель
всё равно остаётся без подтверждения, поэтому подтверждение здесь одно:
номер заказа на экране. По нему заказ открывается на странице проверки,
а мастер видит все заказы в своей панели на той же странице.

У заказа два независимых состояния, и путать их нельзя:
  payment  - ждёт оплаты, оплачен, отменён;
  shipping - ждёт отправки, отправлен.
Отправить можно только оплаченный заказ; у отправленного заказа оплату
снять уже нельзя. Оба правила проверяются здесь, а не в браузере.
"""
import json
import os
import re
import threading
from datetime import datetime, timedelta

import clock

BASE = os.path.dirname(os.path.abspath(__file__))
STORAGE = os.environ.get('STORAGE_DIR') or BASE
ORDERS_FILE = os.path.join(STORAGE, 'data', 'orders.json')
SELLER_FILE = os.path.join(BASE, 'data', 'seller.json')

PAYMENT = ('waiting', 'paid', 'cancelled')
SHIPPING = ('waiting', 'sent')
SECTION_NAMES = {'in_stock': 'Игрушки в наличии',
                 'repeat': 'Реализованные игрушки',
                 'custom': 'Игрушки под заказ'}

# Больше этого числа заказов с одного адреса за час не принимаем. Живой человек
# столько не оформит, а рассыльщик упрётся сразу.
PER_IP_HOUR = 5
MIN_SECONDS = 3          # быстрее человек форму не заполнит
# Номера заказов идут подряд, поэтому их можно перебирать. Телефон перебор
# не пускает, а этот предел не даёт подбирать сам телефон.
FIND_PER_HOUR = 20

_lock = threading.Lock()
_recent = {}             # адрес -> времена последних заказов
_finds = {}              # адрес -> времена последних попыток найти заказ


class Refused(Exception):
    """Заказ не принят или правка не разрешена: в ответ уходит этот текст."""


# ------------------------------------------------------------------ хранение

def seller():
    with open(SELLER_FILE, encoding='utf-8') as f:
        return json.load(f)


def digits(value):
    return re.sub(r'\D', '', str(value or ''))


def migrate(order):
    """Старая запись с одним полем status превращается в пару состояний.

    Первые заказы писались до разделения оплаты и отправки. Чтобы они
    не выпали из панели, переводим их на лету при первом же чтении."""
    if 'payment' in order and 'shipping' in order:
        return order
    was = order.pop('status', 'new')
    order['payment'] = {'new': 'waiting', 'paid': 'paid',
                        'sent': 'paid', 'cancelled': 'cancelled'}.get(was, 'waiting')
    order['shipping'] = 'sent' if was == 'sent' else 'waiting'
    order.pop('mailed', None)
    return order


def read_all():
    if not os.path.exists(ORDERS_FILE):
        return {'last': {'year': 0, 'n': 0}, 'orders': []}
    with open(ORDERS_FILE, encoding='utf-8') as f:
        data = json.load(f)
    for order in data.get('orders', []):
        migrate(order)
    return data


def write_all(data):
    os.makedirs(os.path.dirname(ORDERS_FILE), exist_ok=True)
    tmp = ORDERS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ORDERS_FILE)      # чтобы файл не остался обрезанным


def next_number(data):
    """Номер заказа вида 2026-0148. Нумерация своя на каждый год.

    Счётчику одному верить нельзя: файл могли править руками или
    восстановить из копии, и тогда номер повторился бы. А номер это ключ,
    по которому покупатель ищет свой заказ, поэтому занятые пропускаем."""
    year = clock.now().year
    taken = {o.get('number') for o in data.get('orders', [])}
    last = data.get('last') or {}
    n = last.get('n', 0) if last.get('year') == year else 0
    while True:
        n += 1
        number = '%d-%04d' % (year, n)
        if number not in taken:
            data['last'] = {'year': year, 'n': n}
            return number


def expire(order):
    """Заказ, который так и не оплатили в срок, считается отменённым.

    Игрушку это ни за кем не закрепляет: за одной и той же игрушкой может
    стоять сколько угодно заказов. Срок нужен только чтобы неоплаченные
    заявки не копились в панели без конца. Отдельного будильника у сайта
    нет и не нужно: срок проверяется в тот момент, когда заказ читают."""
    if order.get('payment') != 'waiting' or not order.get('holdUntil'):
        return order
    try:
        until = datetime.fromisoformat(order['holdUntil'])
    except ValueError:
        return order
    if clock.now() > until:
        order['payment'] = 'cancelled'
        order['cancelReason'] = 'оплата не пришла в срок'
    return order


def refresh_all(data):
    """Прогоняем срок оплаты по всем заказам. True, если что-то изменилось
    и файл надо перезаписать."""
    changed = False
    for order in data['orders']:
        before = order.get('payment')
        expire(order)
        changed = changed or order.get('payment') != before
    return changed


def find(order_id):
    """Заказ по его длинному адресу. Просроченный срок оплаты закрываем на месте."""
    if not order_id:
        return None
    with _lock:
        data = read_all()
        for order in data['orders']:
            if order['id'] == order_id:
                before = order.get('payment')
                expire(order)
                if order.get('payment') != before:
                    write_all(data)
                return order
    return None


def find_limit(ip):
    now = clock.now()
    with _lock:
        times = [t for t in _finds.get(ip, []) if now - t < timedelta(hours=1)]
        if len(times) >= FIND_PER_HOUR:
            raise Refused('слишком много попыток подряд, попробуйте через час '
                          'или напишите нам в MAX')
        times.append(now)
        _finds[ip] = times


def find_by_number(number, phone, ip=''):
    """Заказ по номеру и телефону.

    Одного номера мало: номера идут подряд, и по ним любой желающий читал бы
    чужие имена и адреса. Телефон знают только сам покупатель и мастер."""
    if ip:
        find_limit(ip)
    number = re.sub(r'\s+', '', str(number or '')).upper()
    tail = digits(phone)
    if not number or len(tail) < 4:
        raise Refused('нужны номер заказа и телефон, который вы указывали')

    with _lock:
        data = read_all()
        changed = refresh_all(data)
        found = None
        for order in data['orders']:
            if order['number'] != number:
                continue
            # перебираем все заказы с этим номером, а не первый попавшийся:
            # если в файле окажется пара одинаковых номеров, покупатель всё
            # равно должен увидеть свой, а не чужой
            mine = digits(order['buyer'].get('phone'))
            if mine and (mine.endswith(tail) or tail.endswith(mine)):
                found = order
                break
        if changed:
            write_all(data)
    return found


def public(order):
    """То, что можно отдать в браузер: без адреса отправителя и служебного.

    Отметка о письмах тоже служебная: покупателю знать, дошло ли письмо
    мастеру, незачем, а в панели она добавляется обратно в listing()."""
    out = dict(order)
    out.pop('ip', None)
    out.pop('mail', None)
    return out


def listing(frm='', to='', query='', payment='', shipping=''):
    """Список заказов для панели мастера, от новых к старым."""
    with _lock:
        data = read_all()
        if refresh_all(data):
            write_all(data)
        # мастеру, в отличие от покупателя, отметку о письмах показываем
        rows = [dict(public(o), mail=o.get('mail')) for o in data['orders']]

    q = (query or '').strip().lower()
    q_digits = digits(q)
    out = []
    for o in rows:
        day = (o.get('created') or '')[:10]
        if frm and day < frm:
            continue
        if to and day > to:
            continue
        if payment and o.get('payment') != payment:
            continue
        if shipping and o.get('shipping') != shipping:
            continue
        if q:
            b = o.get('buyer') or {}
            haystack = ' '.join(str(x or '') for x in (
                o.get('number'), b.get('name'), b.get('phone'), b.get('email'),
                b.get('city'), b.get('street'), (o.get('toy') or {}).get('name'),
                o.get('track'))).lower()
            # телефон ищем и по одним цифрам: в базе он с пробелами и скобками
            if q not in haystack and not (q_digits and q_digits in digits(haystack)):
                continue
        out.append(o)

    out.sort(key=lambda o: o.get('created') or '', reverse=True)
    return out


def summary():
    """Короткая сводка для панели: сколько чего ждёт мастера."""
    rows = listing()
    return {
        'total': len(rows),
        'waiting': sum(1 for o in rows if o['payment'] == 'waiting'),
        'toSend': sum(1 for o in rows
                      if o['payment'] == 'paid' and o['shipping'] == 'waiting'),
    }


# ------------------------------------------------------------------ проверки

EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@.]+\.[^\s@]{2,}$')
ID_RE = re.compile(r'^[0-9a-zA-Z-]{8,64}$')
LIMITS = {'name': 120, 'phone': 30, 'email': 120, 'region': 80,
          'city': 80, 'street': 200, 'comment': 1000, 'zip': 6}


def clean(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def rate_limit(ip):
    now = clock.now()
    with _lock:
        times = [t for t in _recent.get(ip, []) if now - t < timedelta(hours=1)]
        if len(times) >= PER_IP_HOUR:
            raise Refused('с этого адреса уже несколько заказов подряд, '
                          'напишите нам в MAX и мы оформим вручную')
        times.append(now)
        _recent[ip] = times


def check(payload, toys):
    """Разбираем присланное и возвращаем готовые данные заказа.

    Всё, что пришло из браузера, проверяется здесь заново: проверка в форме
    нужна человеку, а защищает только эта."""
    order_id = str(payload.get('id') or '')
    if not ID_RE.match(order_id):
        raise Refused('заказ открылся по неправильной ссылке')

    # поле-приманка и слишком быстрая отправка: и то и другое бывает у роботов
    if clean(payload.get('podpis'), 50):
        raise Refused('заказ не прошёл проверку')
    try:
        seconds = int(payload.get('seconds') or 0)
    except (TypeError, ValueError):
        seconds = 0
    if seconds < MIN_SECONDS:
        raise Refused('форма отправилась слишком быстро, попробуйте ещё раз')

    slug = str(payload.get('toy') or '')
    toy = next((t for t in toys if t.get('slug') == slug), None)
    if not toy:
        raise Refused('такой игрушки нет в каталоге')

    src = payload.get('buyer') or {}
    b = {key: clean(src.get(key), limit) for key, limit in LIMITS.items()}

    ways = {w['id']: w for w in (seller().get('delivery') or [])}
    way = ways.get(str(src.get('delivery') or ''))
    if not way:
        raise Refused('выберите способ доставки')
    b['delivery'] = way['id']
    b['deliveryName'] = way['name']
    needs_address = way.get('address') != 'no'

    if len(b['name']) < 5 or len(b['name'].split()) < 2:
        raise Refused('в поле получателя нужны фамилия и имя полностью')
    if not 10 <= len(digits(b['phone'])) <= 15:
        raise Refused('телефон указан не полностью')
    if not EMAIL_RE.match(b['email']):
        raise Refused('адрес почты выглядит неверным')
    if needs_address:
        if not re.fullmatch(r'\d{6}', b['zip']):
            raise Refused('индекс состоит из шести цифр')
        if len(b['city']) < 2:
            raise Refused('укажите город или посёлок')
        if len(b['street']) < 5:
            raise Refused('укажите улицу, дом и квартиру')
    else:
        b['zip'] = b['region'] = b['city'] = b['street'] = ''

    agree = payload.get('agree') or {}
    if not (agree.get('terms') and agree.get('privacy')):
        raise Refused('без обоих согласий заказ оформить нельзя')

    return order_id, toy, b


# -------------------------------------------------------------------- заказ

def create(payload, ip, toys):
    """Записываем заказ. Возвращает запись и признак «он новый».

    Признак нужен письмам: при повторной отправке формы заказ возвращается
    старый, и слать о нём письмо второй раз нельзя."""
    order_id, toy, buyer = check(payload, toys)

    existing = find(order_id)
    if existing:
        # повторное нажатие кнопки или обновление страницы: второй заказ
        # с тем же адресом не заводим
        return existing, False

    rate_limit(ip)
    hold_hours = int(seller().get('hold_hours') or 48)
    now = clock.now()

    with _lock:
        data = read_all()
        if any(o['id'] == order_id for o in data['orders']):
            return next(o for o in data['orders'] if o['id'] == order_id), False
        order = {
            'id': order_id,
            'number': next_number(data),
            'created': now.isoformat(timespec='seconds'),
            'payment': 'waiting',
            'shipping': 'waiting',
            'holdUntil': (now + timedelta(hours=hold_hours)).isoformat(timespec='seconds'),
            'toy': {
                'slug': toy['slug'],
                'name': toy['name'],
                'size': toy.get('size', ''),
                'price': toy.get('price'),
                'section': SECTION_NAMES.get(toy.get('section'), ''),
                'sectionId': toy.get('section'),
                'url': 'igrushki/' + toy['slug'] + '/',
            },
            'buyer': buyer,
            'agreedAt': now.isoformat(timespec='seconds'),
            'ip': ip,
            'track': '',
            'receipt': '',
            'history': [],
        }
        data['orders'].append(order)
        write_all(data)
    return order, True


def note_mail(order_id, info):
    """Отмечаем в заказе, что ответил Resend по каждому письму.

    Зовётся из потока отправки, уже после ответа покупателю. Заказа может
    не оказаться на месте, если мастер успел его удалить, - это не ошибка."""
    with _lock:
        data = read_all()
        order = next((o for o in data['orders'] if o['id'] == order_id), None)
        if not order:
            return
        order['mail'] = info
        write_all(data)


def remove(order_id):
    """Убираем заказ насовсем.

    Это не то же самое, что отмена: отменённый заказ остаётся в панели,
    а удалённый исчезает вместе с данными покупателя. Счётчик номеров
    не трогаем, чтобы освободившийся номер не достался новому заказу."""
    with _lock:
        data = read_all()
        before = len(data['orders'])
        data['orders'] = [o for o in data['orders'] if o['id'] != order_id]
        if len(data['orders']) == before:
            raise Refused('заказ не найден')
        write_all(data)


# ------------------------------------------------------- состояние заказа

TRACK_RE = re.compile(r'^[A-Za-z0-9 -]{6,40}$')
PAY_WORDS = {'waiting': 'ждёт оплаты', 'paid': 'оплачен', 'cancelled': 'отменён'}
SHIP_WORDS = {'waiting': 'ждёт отправки', 'sent': 'отправлен'}


def update(order_id, changes, who=''):
    """Мастер меняет состояние заказа. Правила проверяются здесь.

    changes: payment, shipping, track, receipt - любое подмножество."""
    with _lock:
        data = read_all()
        order = next((o for o in data['orders'] if o['id'] == order_id), None)
        if not order:
            raise Refused('заказ не найден')
        expire(order)

        payment = order['payment']
        shipping = order['shipping']
        track = order.get('track') or ''
        receipt = order.get('receipt') or ''
        note = []

        if 'receipt' in changes:
            value = clean(changes['receipt'], 300)
            if value and not re.match(r'^https?://', value):
                raise Refused('ссылка на чек должна начинаться с http')
            if value != receipt:
                receipt = value
                note.append('ссылка на чек')

        if 'track' in changes:
            value = clean(changes['track'], 40)
            if value and not TRACK_RE.match(value):
                raise Refused('трек-номер это буквы, цифры и дефисы, от 6 знаков')
            if value != track:
                track = value
                note.append('трек-номер')

        if 'payment' in changes:
            want = str(changes['payment'])
            if want not in PAYMENT:
                raise Refused('неизвестное состояние оплаты')
            # снять оплату у отправленной посылки нельзя: она уже уехала
            if shipping == 'sent' and want != 'paid':
                raise Refused('заказ уже отправлен, оплату у него менять нельзя')
            if want != payment:
                payment = want
                note.append('оплата: ' + PAY_WORDS[want])

        if 'shipping' in changes:
            want = str(changes['shipping'])
            if want not in SHIPPING:
                raise Refused('неизвестное состояние отправки')
            if want == 'sent':
                if payment != 'paid':
                    raise Refused('отправить можно только оплаченный заказ')
                if not track:
                    raise Refused('чтобы отметить отправку, нужен трек-номер')
            if want != shipping:
                shipping = want
                note.append('отправка: ' + SHIP_WORDS[want])

        if not note:
            return public(order)

        if payment != 'cancelled':
            order.pop('cancelReason', None)
        if payment == 'paid':
            # оплаченный заказ сроком оплаты больше не ограничен
            order.pop('holdUntil', None)

        order['payment'] = payment
        order['shipping'] = shipping
        order['track'] = track
        order['receipt'] = receipt
        order['updated'] = clock.now().isoformat(timespec='seconds')
        order.setdefault('history', []).append({
            'at': order['updated'],
            'who': who or 'администратор',
            'what': ', '.join(note),
        })
        write_all(data)
        return public(order)
