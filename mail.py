# -*- coding: utf-8 -*-
"""Письма о заказе: покупателю и мастеру.

Отправляем через Resend его обычным HTTP-адресом. Отдельная библиотека ради
одного запроса не нужна, а на тарифе с 0.1 ядра лишние зависимости тем более
ни к чему.

Письма простым текстом, без разметки: так они одинаково выглядят везде,
не прячут ничего за отключёнными картинками и не разъезжаются в почтовых
клиентах, которые вырезают половину стилей.

Ключ лежит в переменной RESEND_API_KEY. Без неё письма молча не уходят,
но заказ всё равно записывается: потерять заказ из-за недоступной почты
нельзя, это деньги покупателя.
"""
import json
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime

import clock
from build import BASE_URL, load_env

API = 'https://api.resend.com/emails'
TIMEOUT = 20


def setting(key):
    """На своём компьютере значение берётся из .env, на хостинге -
    из переменной окружения в панели. Так же, как у Firebase в build.py."""
    return os.environ.get(key) or load_env().get(key, '')


# Отправитель обязан быть на домене, подтверждённом в Resend, иначе письмо
# не уйдёт вовсе. Имя перед адресом человек видит в списке писем, адрес -
# мелким шрифтом, поэтому имя важнее.
DOMAIN = BASE_URL.split('//', 1)[-1]
FROM = setting('MAIL_FROM') or ('cotton wool creativity <zakaz@' + DOMAIN + '>')

MONTHS = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
          'августа', 'сентября', 'октября', 'ноября', 'декабря')


def money(value):
    """Цена так, как она написана на сайте: 9 000 ₽."""
    try:
        return '{:,}'.format(int(value)).replace(',', ' ') + ' ₽'
    except (TypeError, ValueError):
        return ''


def when(iso):
    """Дата из заказа по-человечески: «16 сентября в 20:15»."""
    try:
        d = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ''
    return '%d %s в %02d:%02d' % (d.day, MONTHS[d.month - 1], d.hour, d.minute)


def address(b):
    """Одна строка адреса из четырёх полей. Пустые не показываем."""
    parts = [b.get('zip'), b.get('region'), b.get('city'), b.get('street')]
    return ', '.join(p for p in parts if p)


def first_name(full):
    """Из «Иванов Иван Иванович» берём «Иван»: в приветствии фамилия лишняя."""
    parts = (full or '').split()
    return parts[1] if len(parts) > 1 else (parts[0] if parts else '')


def text_of(lines):
    """Строки в готовое письмо.

    Пустые строки между кусками ставим руками, но если кусок не понадобился
    (нет комментария, нет срока брони), на его месте осталась бы дыра
    в три-четыре переноса. Схлопываем до одной пустой строки.
    """
    body = '\n'.join(line for line in lines if line is not None)
    return re.sub(r'\n{3,}', '\n\n', body).strip() + '\n'


# ------------------------------------------------------- письмо покупателю

def buyer_letter(order, s):
    toy = order['toy']
    b = order['buyer']
    price = money(toy.get('price')) or 'сумму подтвердит мастер'
    link = BASE_URL + '/order/' + order['id'] + '/'
    name = first_name(b.get('name'))

    subject = 'Заказ ' + order['number'] + ' принят - ' + toy['name']

    text = text_of([
        ('Здравствуйте, ' + name + '!') if name else 'Здравствуйте!',
        '',
        'Вы оформили заказ, информация доступна по - ' + link,
        '',
        'Номер заказа: ' + order['number'],
        'Игрушка: ' + toy['name'] + (', ' + toy['size'] if toy.get('size') else ''),
        'Сумма: ' + price,
        '',
        'КАК ОПЛАТИТЬ',
        '1. Переведите ' + price + ' по номеру ' + (s.get('phone') or '') +
        ', получатель ' + (s.get('name') or '') + '.',
        '2. В сообщении к переводу укажите номер заказа ' + order['number'] + '.',
        '3. Пришлите чек об оплате в MAX, и мы соберём посылку.',
        '',
        'Ссылка для перевода: ' + (s.get('pay') or ''),
        'Написать в MAX: ' + (s.get('max') or ''),
        '',
        'ЧТО И КУДА ЕДЕТ',
        'Получатель: ' + (b.get('name') or ''),
        'Телефон: ' + (b.get('phone') or ''),
        'Адрес: ' + address(b),
        'Доставка: ' + (b.get('deliveryName') or ''),
        '',
        'Спасибо за заказ!',
        '',
        '--',
        'Это письмо отправил сайт ' + BASE_URL + ' после оформления заказа.',
    ])
    return subject, text


# ---------------------------------------------------------- письмо мастеру

def seller_letter(order, s):
    toy = order['toy']
    b = order['buyer']
    price = money(toy.get('price')) or 'цена не указана'
    link = BASE_URL + '/order/' + order['id'] + '/'
    panel = BASE_URL + '/proverit-zakaz/'

    subject = 'Новый заказ ' + order['number'] + ': ' + toy['name'] + ', ' + price

    text = text_of([
        'Новый заказ на сайте, ' + when(order.get('created')) + '.',
        '',
        'Проверить любой заказ можно тут - ' + panel,
        'Проверить этот заказ можно тут - ' + link,
        '',
        'Номер: ' + order['number'],
        'Игрушка: ' + toy['name'] + (', ' + toy['size'] if toy.get('size') else ''),
        'Раздел: ' + (toy.get('section') or ''),
        'Цена: ' + price,
        '',
        'ПОКУПАТЕЛЬ',
        'Имя: ' + (b.get('name') or ''),
        'Телефон: ' + (b.get('phone') or ''),
        'Почта: ' + (b.get('email') or ''),
        'Доставка: ' + (b.get('deliveryName') or ''),
        'Адрес: ' + address(b),
        ('Комментарий: ' + b['comment']) if b.get('comment') else '',
        '',
        'Когда деньги придут, откройте заказ и нажмите «оплачен».',
    ])
    return subject, text


# ------------------------------------------------------------- отправка

def post(to, subject, text):
    """Один запрос к Resend.

    Возвращает то, что ответил Resend: принял он письмо или нет, его номер
    отправления и причину отказа. Это и есть отметка в заказе - мастеру
    должно быть видно, дошло письмо до покупателя или нет.
    """
    key = setting('RESEND_API_KEY').strip()
    if not key:
        print('  почта: ключ RESEND_API_KEY не задан, письмо не отправлено',
              flush=True)
        return {'ok': False, 'id': '', 'error': 'ключ Resend не задан'}
    if not to:
        return {'ok': False, 'id': '', 'error': 'адрес не указан'}

    # Письма односторонние: адреса для ответа нет намеренно. Покупатель
    # пишет в MAX, мастер - покупателю напрямую по адресу из заказа
    letter = {'from': FROM, 'to': [to], 'subject': subject, 'text': text}

    # User-Agent обязателен: перед Resend стоит Cloudflare, и запрос
    # со стандартной подписью Python он отбивает 403 с кодом 1010,
    # не доходя до самого Resend. Проверено запросом к их API
    request = urllib.request.Request(
        API, data=json.dumps(letter, ensure_ascii=False).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + key,
                 'Content-Type': 'application/json',
                 'User-Agent': 'cotton-wool-site/1.0'})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            body = json.loads(answer.read().decode('utf-8', 'replace') or '{}')
            sent = str(body.get('id') or '')
            print('  почта: отправлено на ' + to + ' (' + sent + ')', flush=True)
            return {'ok': True, 'id': sent, 'error': ''}
    except urllib.error.HTTPError as e:
        # тело ответа Resend объясняет причину куда точнее кода
        why = e.read().decode('utf-8', 'replace')
        try:
            why = json.loads(why).get('message') or why
        except ValueError:
            pass
        why = why.strip()[:200]
        print('  почта: ' + to + ' - отказ ' + str(e.code) + ': ' + why, flush=True)
        return {'ok': False, 'id': '', 'error': why or ('отказ ' + str(e.code))}
    except Exception as e:
        print('  почта: ' + to + ' - не отправлено, ' + repr(e), flush=True)
        return {'ok': False, 'id': '', 'error': 'почта недоступна'}


def order_created(order, s, note=None):
    """Два письма о новом заказе, в отдельном потоке.

    В отдельном - чтобы покупатель не ждал у пустой страницы, пока сайт
    ходит в Resend. Заказ к этому моменту уже записан, и если письмо
    не уйдёт, пропадёт только письмо.

    note - куда записать ответ Resend. Без этой отметки мастер не отличит
    «покупатель получил письмо» от «письмо не ушло, а он и не знает».
    """
    def work():
        buyer = order['buyer'].get('email')
        subject, text = buyer_letter(order, s)
        to_buyer = post(buyer, subject, text)

        subject, text = seller_letter(order, s)
        to_seller = post(s.get('email') or '', subject, text)

        if note:
            note(order['id'], {'buyer': to_buyer, 'seller': to_seller,
                               'at': clock.now().isoformat(timespec='seconds')})

    threading.Thread(target=work, daemon=True).start()
