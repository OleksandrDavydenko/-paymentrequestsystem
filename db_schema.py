"""Оновлення схеми бази без ручних SQL-скриптів.

Таблиці описані в db_sql.py. metadata.create_all створює відсутні таблиці, але не чіпає наявні, тож тут:
  • відсутні колонки додаються ALTER TABLE … ADD (лише нові колонки з nullable або значенням за замовчуванням);
  • відсутні індекси створюються;
  • службові рядки: request_rev (лічильник змін) і зсув нумерації заявок (schema_meta).
Кожен крок можна повторювати: якщо два процеси оновлюють схему одночасно, другий побачить, що все вже є.
Колонки ніколи не видаляються й не змінюються автоматично.
"""
import logging

import sqlalchemy as sa

import db_sql

logger = logging.getLogger(__name__)


def _add_missing_columns(eng):
    insp = sa.inspect(eng)
    for table in db_sql.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"].lower() for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name.lower() in existing:
                continue
            ddl = sa.schema.CreateColumn(col).compile(dialect=eng.dialect)
            try:
                with eng.begin() as c:
                    c.execute(sa.text(f"ALTER TABLE {table.name} ADD {ddl}"))
                logger.info("Додано колонку %s.%s", table.name, col.name)
            except Exception:
                # інший процес міг додати її щойно — перевіряємо
                names = {c["name"].lower() for c in sa.inspect(eng).get_columns(table.name)}
                if col.name.lower() not in names:
                    raise


def _add_missing_indexes(eng):
    insp = sa.inspect(eng)
    for table in db_sql.metadata.sorted_tables:
        existing = {i["name"] for i in insp.get_indexes(table.name) if i.get("name")}
        for idx in table.indexes:
            if idx.name in existing:
                continue
            try:
                idx.create(eng)
                logger.info("Створено індекс %s", idx.name)
            except Exception:
                names = {i["name"] for i in sa.inspect(eng).get_indexes(table.name) if i.get("name")}
                if idx.name not in names:
                    raise


def meta_get(conn, key):
    return conn.execute(sa.select(db_sql.meta_t.c.value).where(db_sql.meta_t.c.key == key)).scalar()


def meta_set(conn, key, value):
    t = db_sql.meta_t
    if conn.execute(t.update().where(t.c.key == key).values(value=str(value))).rowcount == 0:
        conn.execute(t.insert().values(key=key, value=str(value)))


def _init_rows(eng):
    with eng.begin() as c:
        if c.execute(sa.select(db_sql.rev_t.c.rev).where(db_sql.rev_t.c.id == 1)).scalar() is None:
            try:
                c.execute(db_sql.rev_t.insert().values(id=1, rev=0))
            except Exception:
                pass  # вставив інший процес
    with eng.begin() as c:
        # Номери нових заявок = зсув + номер із request_seq; зсув — найбільший номер заявки на момент
        # першого запуску лічильника, щоб нумерація продовжувалась, а не починалась з 1
        if meta_get(c, "request_id_offset") is None:
            top = c.execute(sa.select(sa.func.max(db_sql.requests_t.c.id))).scalar() or 0
            seq = c.execute(sa.select(sa.func.max(db_sql.seq_t.c.id))).scalar() or 0
            meta_set(c, "request_id_offset", max(top - seq, 0))


def upgrade(eng):
    """Довести схему до опису в db_sql.py. Викликається один раз на процес (storage/sql.py)."""
    db_sql.metadata.create_all(eng)
    _add_missing_columns(eng)
    _add_missing_indexes(eng)
    _init_rows(eng)
