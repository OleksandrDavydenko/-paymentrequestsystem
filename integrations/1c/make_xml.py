"""Генерує XML-опис обробки «Платіжки із заявок PayDesk» у форматі вивантаження конфігуратора 1С
(src/PayDeskPayments.xml + src/PayDeskPayments/Forms/…). Модулі (*.bsl) пишуться вручну й тут не змінюються.
Запуск: python integrations/1c/make_xml.py — далі build.bat збирає з src/ файл PayDeskPayments.epf."""
from pathlib import Path
from xml.sax.saxutils import escape

NAME = "PayDeskPayments"
TITLE = "Платіжки із заявок PayDesk"
SRC = Path(__file__).resolve().parent / "src"
# Сталі UUID обробки (не змінювати: за ними 1С впізнає ту саму обробку)
UUID = {"object": "6f1d2b8e-3c4a-4e57-9b21-7a5d0c9e4f10", "contained": "0b7c9a3e-1f2d-4c6b-8e5a-2d4f6a8c0e12",
        "type": "4a8e2c6d-9b1f-4e3a-a7c5-1d3f5b7e9a24", "value": "8c2e4a6b-7d9f-4b1e-93a5-6f8d0b2e4c36",
        "form": "2e6a8c0d-4b7f-4d2e-b1a3-9c5e7f1a3d48"}
VERSION = "2.10"  # формат вивантаження 8.3.16 — завантажується й новішими платформами (у вас 8.3.19)
NS = ('xmlns="{main}" xmlns:app="http://v8.1c.ru/8.2/managed-application/core" '
      'xmlns:cfg="http://v8.1c.ru/8.1/data/enterprise/current-config" xmlns:cmi="http://v8.1c.ru/8.2/managed-application/cmi" '
      'xmlns:ent="http://v8.1c.ru/8.1/data/enterprise" xmlns:lf="http://v8.1c.ru/8.2/managed-application/logform" '
      'xmlns:style="http://v8.1c.ru/8.1/data/ui/style" xmlns:sys="http://v8.1c.ru/8.1/data/ui/fonts/system" '
      'xmlns:v8="http://v8.1c.ru/8.1/data/core" xmlns:v8ui="http://v8.1c.ru/8.1/data/ui" '
      'xmlns:web="http://v8.1c.ru/8.1/data/ui/colors/web" xmlns:win="http://v8.1c.ru/8.1/data/ui/colors/windows" '
      'xmlns:xen="http://v8.1c.ru/8.3/xcf/enums" xmlns:xpr="http://v8.1c.ru/8.3/xcf/predef" '
      'xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" xmlns:xs="http://www.w3.org/2001/XMLSchema" '
      'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="' + VERSION + '"')
MD = "http://v8.1c.ru/8.3/MDClasses"
LF = "http://v8.1c.ru/8.3/xcf/logform"


def text(value, tag="v8:item"):
    """Багатомовний рядок: однаковий текст для ru й uk (мова конфігурації може бути будь-яка з двох)."""
    return "".join(f"<{tag}><v8:lang>{lang}</v8:lang><v8:content>{escape(value)}</v8:content></{tag}>" for lang in ("ru", "uk"))


# ---------------------------------------------------------------- типи реквізитів форми

def t_str(length=0):
    return (f"<v8:Type>xs:string</v8:Type><v8:StringQualifiers><v8:Length>{length}</v8:Length>"
            "<v8:AllowedLength>Variable</v8:AllowedLength></v8:StringQualifiers>")


T_BOOL = "<v8:Type>xs:boolean</v8:Type>"
T_DATE = "<v8:Type>xs:dateTime</v8:Type><v8:DateQualifiers><v8:DateFractions>Date</v8:DateFractions></v8:DateQualifiers>"


def t_num(digits, fraction):
    return (f"<v8:Type>xs:decimal</v8:Type><v8:NumberQualifiers><v8:Digits>{digits}</v8:Digits>"
            f"<v8:FractionDigits>{fraction}</v8:FractionDigits><v8:AllowedSign>Any</v8:AllowedSign></v8:NumberQualifiers>")


ATTRIBUTES = [  # (ім'я, заголовок, тип)
    ("АдресPayDesk", "Адреса PayDesk", t_str(300)),
    ("КлючAPI", "Ключ API", t_str(200)),
    ("ВидДокумента", "Вид документа платіжки", t_str(150)),
    ("СостояниеЗаявок", "Заявки", t_str(20)),
    ("ФормаОплаты", "Форма оплати", t_str(10)),
    ("Справка", "Документи конфігурації", t_str(0)),
]
TABLES = {
    "Заявки": ("Заявки з PayDesk", [
        ("Выбрать", "✓", T_BOOL), ("Ид", "Ід", t_num(10, 0)), ("Номер", "№ заявки", t_str(20)),
        ("ДатаОплаты", "Дата оплати", T_DATE), ("Организация", "Організація", t_str(200)),
        ("Контрагент", "Контрагент", t_str(250)), ("Сумма", "Сума", t_num(15, 2)), ("Валюта", "Валюта", t_str(10)),
        ("Назначение", "Призначення платежу", t_str(500)), ("Состояние", "Стан", t_str(50)),
        ("Документ", "Платіжка 1С", t_str(100)), ("Примечание", "Примітка", t_str(0)), ("Данные", "Дані", t_str(0))]),
    "Сопоставление": ("Як заповнити платіжку", [
        ("Реквизит", "Реквізит документа", t_str(100)), ("Источник", "Звідки (поле заявки)", t_str(50)),
        ("Справочник", "Довідник 1С", t_str(100)), ("РеквизитПоиска", "Шукати за реквізитом", t_str(100)),
        ("ПолеПоиска", "Поле PayDesk для пошуку", t_str(20))]),
}
COMMANDS = [  # (ім'я = процедура, заголовок, підказка)
    ("ОбновитьЗаявки", "Оновити", "Забрати заявки з PayDesk"),
    ("СоздатьПлатежки", "Створити платіжки", "Створити чернетки платіжних доручень для позначених заявок"),
    ("ПроверитьСвязь", "Перевірити зв'язок", "Перевірити адресу й ключ PayDesk"),
    ("ВыбратьВсе", "Позначити всі", ""),
    ("СнятьВсе", "Зняти позначки", ""),
    ("ЗаписатьНастройки", "Зберегти налаштування", ""),
    ("ПоказатьДокументы", "Документи конфігурації", "Показати документи, схожі на платіжки, і реквізити вибраного"),
    ("ПравилаПоУмолчанию", "Правила за замовчуванням", "Повернути правила заповнення за замовчуванням"),
]


# ---------------------------------------------------------------- елементи форми

class Ids:
    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return self.n


ID = Ids()


def extras(name):
    return (f'<ContextMenu name="{name}КонтекстноеМеню" id="{ID()}"/>'
            f'<ExtendedTooltip name="{name}РасширеннаяПодсказка" id="{ID()}"/>')


def field(name, path, *props, kind="InputField"):
    return f'<{kind} name="{name}" id="{ID()}"><DataPath>{path}</DataPath>{"".join(props)}{extras(name)}</{kind}>'


def button(name, command, usual=False, default=False):
    return (f'<Button name="{name}" id="{ID()}"><Type>{"UsualButton" if usual else "CommandBarButton"}</Type>'
            + ("<DefaultButton>true</DefaultButton>" if default else "")
            + f'<CommandName>Form.Command.{command}</CommandName>'
            f'<ExtendedTooltip name="{name}РасширеннаяПодсказка" id="{ID()}"/></Button>')


def group(name, title, items, horizontal=False):
    return (f'<UsualGroup name="{name}" id="{ID()}"><Title>{text(title)}</Title>'
            f'<Group>{"Horizontal" if horizontal else "Vertical"}</Group><Representation>None</Representation>'
            f'<ShowTitle>false</ShowTitle><ExtendedTooltip name="{name}РасширеннаяПодсказка" id="{ID()}"/>'
            f'<ChildItems>{"".join(items)}</ChildItems></UsualGroup>')


def table(name, columns, buttons=(), height=None):
    bar = f'<AutoCommandBar name="{name}КоманднаяПанель" id="{ID()}"><ChildItems>{"".join(buttons)}</ChildItems></AutoCommandBar>'
    head = (f'<Table name="{name}" id="{ID()}"><Representation>List</Representation>'
            + (f"<HeightInTableRows>{height}</HeightInTableRows>" if height else "")
            + f'<DataPath>{name}</DataPath><ContextMenu name="{name}КонтекстноеМеню" id="{ID()}"/>{bar}'
            f'<ExtendedTooltip name="{name}РасширеннаяПодсказка" id="{ID()}"/>')
    return head + f'<ChildItems>{"".join(columns)}</ChildItems></Table>'


def page(name, title, items):
    return (f'<Page name="{name}" id="{ID()}"><Title>{text(title)}</Title>'
            f'<ExtendedTooltip name="{name}РасширеннаяПодсказка" id="{ID()}"/><ChildItems>{"".join(items)}</ChildItems></Page>')


def form_xml():
    ro = "<ReadOnly>true</ReadOnly>"
    requests_columns = [field("ЗаявкиВыбрать", "Заявки.Выбрать", kind="CheckBoxField")] + [
        field("Заявки" + c, "Заявки." + c, ro) for c in
        ("Номер", "ДатаОплаты", "Организация", "Контрагент", "Сумма", "Валюта", "Назначение", "Состояние", "Документ", "Примечание")]
    rules_columns = [field("Сопоставление" + c, "Сопоставление." + c, *(("<ListChoiceMode>true</ListChoiceMode>",)
                                                                         if c in ("Источник", "ПолеПоиска") else ()))
                     for c in ("Реквизит", "Источник", "Справочник", "РеквизитПоиска", "ПолеПоиска")]
    requests_page = page("СтраницаЗаявки", "Заявки до оплати", [
        group("ГруппаОтбор", "Відбір", [
            field("СостояниеЗаявок", "СостояниеЗаявок", "<ListChoiceMode>true</ListChoiceMode>"),
            field("ФормаОплаты", "ФормаОплаты", "<ListChoiceMode>true</ListChoiceMode>"),
            button("КнопкаОбновить", "ОбновитьЗаявки", usual=True)], horizontal=True),
        table("Заявки", requests_columns, [button("ЗаявкиВыбратьВсе", "ВыбратьВсе"), button("ЗаявкиСнятьВсе", "СнятьВсе")]),
    ])
    settings_page = page("СтраницаНастройки", "Налаштування", [
        field("АдресPayDesk", "АдресPayDesk", "<HorizontalStretch>true</HorizontalStretch>"),
        field("КлючAPI", "КлючAPI", "<PasswordMode>true</PasswordMode>", "<HorizontalStretch>true</HorizontalStretch>"),
        field("ВидДокумента", "ВидДокумента"),
        group("ГруппаКнопкиНастроек", "Дії", [
            button("КнопкаПроверитьСвязь", "ПроверитьСвязь", usual=True),
            button("КнопкаЗаписатьНастройки", "ЗаписатьНастройки", usual=True),
            button("КнопкаПоказатьДокументы", "ПоказатьДокументы", usual=True),
            button("КнопкаПравилаПоУмолчанию", "ПравилаПоУмолчанию", usual=True)], horizontal=True),
        table("Сопоставление", rules_columns, height=8),
        field("Справка", "Справка", ro, "<MultiLine>true</MultiLine>", "<Height>10</Height>",
              "<HorizontalStretch>true</HorizontalStretch>"),
    ])
    items = (f'<Pages name="Страницы" id="{ID()}"><ExtendedTooltip name="СтраницыРасширеннаяПодсказка" id="{ID()}"/>'
             f'<ChildItems>{requests_page}{settings_page}</ChildItems></Pages>')
    bar = (f'<AutoCommandBar name="ФормаКоманднаяПанель" id="-1"><ChildItems>'
           + button("ФормаСоздатьПлатежки", "СоздатьПлатежки", default=True) + button("ФормаОбновить", "ОбновитьЗаявки")
           + button("ФормаПроверитьСвязь", "ПроверитьСвязь") + "</ChildItems></AutoCommandBar>")

    attrs = [f'<Attribute name="Объект" id="1"><Type><v8:Type>cfg:ExternalDataProcessorObject.{NAME}</v8:Type></Type>'
             f'<MainAttribute>true</MainAttribute></Attribute>']
    n = 1
    for name, title, typ in ATTRIBUTES:
        n += 1
        attrs.append(f'<Attribute name="{name}" id="{n}"><Title>{text(title)}</Title><Type>{typ}</Type></Attribute>')
    for name, (title, columns) in TABLES.items():
        n += 1
        cols = "".join(f'<Column name="{c}" id="{i}"><Title>{text(t)}</Title><Type>{typ}</Type></Column>'
                       for i, (c, t, typ) in enumerate(columns, 1))
        attrs.append(f'<Attribute name="{name}" id="{n}"><Title>{text(title)}</Title>'
                     f'<Type><v8:Type>v8:ValueTable</v8:Type></Type><Columns>{cols}</Columns></Attribute>')
    commands = "".join(f'<Command name="{name}" id="{i}"><Title>{text(title)}</Title>'
                       + (f"<ToolTip>{text(tip)}</ToolTip>" if tip else "") + f"<Action>{name}</Action></Command>"
                       for i, (name, title, tip) in enumerate(COMMANDS, 1))
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<Form {NS.format(main=LF)}><Title>{text(TITLE)}</Title>'
            f'<AutoTitle>false</AutoTitle>{bar}<Events><Event name="OnCreateAtServer">ПриСозданииНаСервере</Event></Events>'
            f'<ChildItems>{items}</ChildItems><Attributes>{"".join(attrs)}</Attributes>'
            f'<Commands>{commands}</Commands></Form>\n')


def root_xml():
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject {NS.format(main=MD)}>'
            f'<ExternalDataProcessor uuid="{UUID["object"]}"><InternalInfo><xr:ContainedObject>'
            f'<xr:ClassId>c3831ec8-d8d5-4f93-8a22-f9bfae07327f</xr:ClassId><xr:ObjectId>{UUID["contained"]}</xr:ObjectId>'
            f'</xr:ContainedObject><xr:GeneratedType name="ExternalDataProcessorObject.{NAME}" category="Object">'
            f'<xr:TypeId>{UUID["type"]}</xr:TypeId><xr:ValueId>{UUID["value"]}</xr:ValueId></xr:GeneratedType></InternalInfo>'
            f'<Properties><Name>{NAME}</Name><Synonym>{text(TITLE)}</Synonym><Comment/>'
            f'<DefaultForm>ExternalDataProcessor.{NAME}.Form.Форма</DefaultForm><AuxiliaryForm/></Properties>'
            f'<ChildObjects><Form>Форма</Form></ChildObjects></ExternalDataProcessor></MetaDataObject>\n')


def form_md_xml():
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject {NS.format(main=MD)}><Form uuid="{UUID["form"]}">'
            f'<Properties><Name>Форма</Name><Synonym>{text("Форма")}</Synonym><Comment/><FormType>Managed</FormType>'
            f'<IncludeHelpInContents>false</IncludeHelpInContents><UsePurposes>'
            f'<v8:Value xsi:type="app:ApplicationUsePurpose">PlatformApplication</v8:Value></UsePurposes></Properties>'
            f'</Form></MetaDataObject>\n')


def main():
    out = {SRC / f"{NAME}.xml": root_xml(), SRC / NAME / "Forms" / "Форма.xml": form_md_xml(),
           SRC / NAME / "Forms" / "Форма" / "Ext" / "Form.xml": form_xml()}
    for path, body in out.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8-sig")  # 1С пише вивантаження з BOM
        print("written", path.relative_to(SRC.parent))


if __name__ == "__main__":
    main()
