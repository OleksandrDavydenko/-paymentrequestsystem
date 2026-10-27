"""Збирає обробку «PayDesk: дані з API» (PayDeskAPI.epf) без конфігуратора 1С: бере за зразок обробку з одним
полем і однією кнопкою (ТаблиціВДжейсонЕксп.epf — звичайна форма для товстого клієнта й керована), міняє назви,
підписи й модулі обох форм (Module.bsl поруч; модуль звичайної форми виводиться з нього) і складає контейнер 1С
так само, як зразок (блоки підряд, сторінка даних ≥ 512 байт, raw deflate).

Запуск: python build_epf.py <зразок.epf> <результат.epf>
Перед складанням перевіряє, що зразок без змін збирається назад байт у байт (інакше — виняток).
"""
import re
import struct
import sys
import uuid
import zlib
from pathlib import Path

END = 0x7FFFFFFF
HERE = Path(__file__).resolve().parent

# Елементи зразка (ідентифікатори всередині контейнера)
FORM_BODY = "47b6cedf-a4d8-4696-accc-58832f40aab2.0"  # керована форма з модулем усередині
ORDINARY_FORM = "4cff6f62-66bd-45ea-8ec5-53289e4881e0"
META = "60e96917-fd43-4231-88e3-8ccec036474a"     # опис обробки


# ---------------------------------------------------------------- контейнер

def _read_block(buf, addr):
    out, total = b"", None
    while addr != END:
        size, page, nxt = (int(x, 16) for x in buf[addr + 2:addr + 29].split(b" ")[:3])
        total = size if total is None else total
        out += buf[addr + 31:addr + 31 + page]
        addr = nxt
    return out[:total]


def read(buf):
    """[(ім'я, заголовок файлу, збережені дані як є)] і заголовок контейнера."""
    head = struct.unpack_from("<iiii", buf, 0)
    toc = _read_block(buf, 16)
    files = []
    for i in range(0, len(toc) - 11, 12):
        h, d, _ = struct.unpack_from("<iii", toc, i)
        header = _read_block(buf, h)
        name = header[20:].decode("utf-16-le").split("\x00")[0]
        files.append((name, header, _read_block(buf, d)))
    return head, files


def _block(data, page=None):
    page = max(len(data), page or 0)
    return b"\r\n%08x %08x %08x \r\n" % (len(data), page, END) + data + b"\x00" * (page - len(data))


def write(head, files):
    """Контейнер: заголовок, зміст (сторінка 512), далі для кожного файлу — заголовок і дані (сторінка ≥ 512)."""
    toc_page = max(512, 12 * len(files))
    pos = 16 + 31 + toc_page
    toc, body = b"", b""
    for name, header, data in files:
        h_addr = pos
        hb = _block(header)
        d_addr = h_addr + len(hb)
        db = _block(data, 512)
        toc += struct.pack("<iii", h_addr, d_addr, END)
        body += hb + db
        pos = d_addr + len(db)
    return struct.pack("<iiii", head[0], head[1], len(files), head[3]) + _block(toc, toc_page) + body


def write_nested(head, files):
    """Вкладений контейнер (форма + модуль звичайної форми), як його пише 1С: спершу заголовки всіх файлів,
    далі дані у зворотному порядку; сторінка даних — рівно за розміром."""
    toc_page = max(512, 12 * len(files))
    pos = 16 + 31 + toc_page
    heads, addrs = b"", []
    for name, header, data in files:
        addrs.append(pos)
        hb = _block(header)
        heads += hb
        pos += len(hb)
    datas, d_addrs = b"", {}
    for i in reversed(range(len(files))):
        d_addrs[i] = pos
        db = _block(files[i][2])
        datas += db
        pos += len(db)
    toc = b"".join(struct.pack("<iii", addrs[i], d_addrs[i], END) for i in range(len(files)))
    return struct.pack("<iiii", head[0], head[1], len(files), head[3]) + _block(toc, toc_page) + heads + datas


def ordinary_module(managed):
    """Модуль звичайної форми з модуля керованої: без директив, інші обробники, реквізит без «Объект.»."""
    text = "\r\n".join(line for line in managed.split("\r\n") if line.strip() != "&НаКлиенте")
    text = _replace(text, "Процедура ПутьНачалоВыбора(Элемент, ДанныеВыбора, СтандартнаяОбработка)",
                    "Процедура ПутьНачалоВыбора(Элемент, СтандартнаяОбработка)")
    text = _replace(text, "Процедура ВивантажитиТаблиці(Команда)", "Процедура КнопкаВыполнитьНажатие(Кнопка)")
    text = _replace(text, "Объект.Путь", "Путь")
    # Попередження — повідомленням: у звичайному застосунку модальні вікна можуть бути заборонені
    text = re.sub(r"ПоказатьПредупреждение\(, (.+)\);", r"Сообщить(\1);", text)
    assert "ПоказатьПредупреждение" not in text and "&На" not in text
    return text


# ---------------------------------------------------------------- зміни

def _text(data):
    return zlib.decompress(data, -15).decode("utf-8-sig")


def _pack(text):
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    return c.compress(("﻿" + text).encode("utf-8")) + c.flush()


def _replace(text, old, new, count=1):
    assert text.count(old) == count, (old, text.count(old))
    return text.replace(old, new)


def braces_ok(text):
    """Дужки збалансовані поза рядками ("…", подвоєні лапки всередині)."""
    depth, i, n = 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n:
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':
                        i += 2
                        continue
                    break
                i += 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def build(template, out):
    src = Path(template).read_bytes()
    head, files = read(src)
    assert write(head, files) == src, "складальник не відтворює зразок байт у байт — збирати не можна"
    files = {name: [header, data] for name, header, data in files}
    order = list(files)

    # Модуль форми: рядок у лапках між «"\r\n&НаКлиенте» і «",\r\n{4,1,» у описі керованої форми
    form = _text(files[FORM_BODY][1])
    start = form.index('"\r\n&НаКлиенте')
    end = form.index('",\r\n{4,1,\r\n{9,\r\n{1},0,"Объект"', start)
    module = (HERE / "Module.bsl").read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    form = form[:start + 1] + "\r\n" + module.replace('"', '""') + form[end:]
    form = _replace(form, '{"uk","Вивантажити таблиці"}', '{"uk","Отримати дані з PayDesk"}', count=2)
    assert braces_ok(form)
    files[FORM_BODY][1] = _pack(form)

    # Звичайна форма (основна — для товстого клієнта): вкладений контейнер «form» + «module»
    nested_raw = zlib.decompress(files[ORDINARY_FORM + ".0"][1], -15)
    n_head, n_files = read(nested_raw)
    assert write_nested(n_head, n_files) == nested_raw, "вкладений контейнер не відтворюється байт у байт"
    n_files = {name: [header, data] for name, header, data in n_files}
    o_form = n_files["form"][1].decode("utf-8-sig")
    o_form = _replace(o_form, '{"ru","Путь:"},\r\n{"eng","Путь:"},\r\n{"uk","Путь:"}',
                      '{"ru","Ключ API:"},\r\n{"eng","Ключ API:"},\r\n{"uk","Ключ API:"}')
    o_form = _replace(o_form, '{"ru","Виконати"},\r\n{"eng","Виконати"},\r\n{"uk","Виконати"}',
                      '{"ru","Отримати дані з PayDesk"},\r\n{"eng","Отримати дані з PayDesk"},\r\n{"uk","Отримати дані з PayDesk"}')
    o_form = _replace(o_form, '{"ru","Обробка  ТаблиціВДжейсон"}', '{"ru","PayDesk: дані з API"}')
    assert braces_ok(o_form)
    n_files["form"][1] = ("\ufeff" + o_form).encode("utf-8")
    n_files["module"][1] = ("\ufeff" + ordinary_module(module)).encode("utf-8")
    nested = write_nested(n_head, [(name, *n_files[name]) for name in ("form", "module")])
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    files[ORDINARY_FORM + ".0"][1] = c.compress(nested) + c.flush()

    # Опис обробки: назва, синонім, підпис поля (основна форма — звичайна, як у зразку: Expeditor — товстий клієнт)
    meta = _text(files[META][1])
    meta = _replace(meta, '"ТаблиціВДжейсон",\r\n{1,"uk","Таблиці в джейсон"}', '"PayDeskAPI",\r\n{1,"uk","PayDesk: дані з API"}')
    meta = _replace(meta, '"Путь",\r\n{1,"uk","Путь"}', '"Путь",\r\n{1,"uk","Ключ API"}')
    assert braces_ok(meta)
    files[META][1] = _pack(meta)

    copyinfo = _replace(_text(files["copyinfo"][1]), '"ТаблиціВДжейсон"', '"PayDeskAPI"')
    assert braces_ok(copyinfo)
    files["copyinfo"][1] = _pack(copyinfo)

    # Версії змінених файлів — нові (так робить і конфігуратор)
    versions = _text(files["versions"][1])
    for name in ("", FORM_BODY, ORDINARY_FORM + ".0", META, "copyinfo"):
        versions = re.sub(r'("%s",)([0-9a-f-]{36})' % re.escape(name), lambda m: m.group(1) + str(uuid.uuid4()), versions, count=1)
    assert braces_ok(versions)
    files["versions"][1] = _pack(versions)

    result = write(head, [(name, *files[name]) for name in order])
    Path(out).write_bytes(result)
    # Перечитати результат: усі файли розпаковуються, тексти — з збалансованими дужками
    _, check = read(result)
    for name, _, data in check:
        raw = zlib.decompress(data, -15)
        if raw.startswith(b"\xff\xff\xff\x7f"):
            _, inner = read(raw)
            assert [n for n, _, _ in inner] == ["form", "module"] and braces_ok(inner[0][2].decode("utf-8-sig")), name
        else:
            assert braces_ok(raw.decode("utf-8-sig")), name
    return len(result)


if __name__ == "__main__":
    print("ok", build(sys.argv[1], sys.argv[2]), "bytes")
