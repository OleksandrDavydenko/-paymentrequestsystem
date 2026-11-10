"""З'єднання з Azure SQL: тимчасова недоступність бази (40613 тощо) — кілька нових спроб замість помилки сторінки;
інші помилки — одразу, без очікування; пул видає найсвіжіше з'єднання (LIFO), а не старші по черзі."""
import os, sys
sys.path.insert(0, os.getcwd())
import pyodbc
import db_sql

ok = True
def check(label, cond):
    global ok
    ok &= bool(cond); print(("PASS " if cond else "FAIL ") + label)

os.environ.update(SQL_SERVER="example.database.windows.net", SQL_DATABASE="payrequests")
db_sql._driver = lambda: "ODBC Driver 18 for SQL Server"
db_sql._token = lambda: b"token"
slept = []
db_sql.time.sleep = slept.append

UNAVAILABLE = pyodbc.ProgrammingError("42000", "[42000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]Database "
                                      "'payrequests' on server 'ftp-payreq-sql' is not currently available.  Please "
                                      "retry the connection later. (40613) (SQLDriverConnect)")
LOGIN_FAILED = pyodbc.InterfaceError("28000", "[28000] Login failed for user '<token-identified principal>'. (18456)")
calls = []
def fake_connect(outcomes):
    def connect(conn_str, attrs_before=None):
        calls.append(conn_str)
        result = outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result
    return connect

check("40613 and a dropped link are transient, login failure is not",
      db_sql._transient(UNAVAILABLE) and db_sql._transient(pyodbc.OperationalError("08S01", "Communication link failure"))
      and not db_sql._transient(LOGIN_FAILED))

pyodbc.connect = fake_connect([UNAVAILABLE, UNAVAILABLE, "conn"])
check("database briefly unavailable: connected on the third try", db_sql._azure_connect() == "conn"
      and len(calls) == 3 and slept == [1, 3])

calls.clear(); slept.clear()
pyodbc.connect = fake_connect([LOGIN_FAILED])
try:
    db_sql._azure_connect(); raised = False
except pyodbc.InterfaceError:
    raised = True
check("other errors: raised at once, no waiting", raised and len(calls) == 1 and not slept)

calls.clear(); slept.clear()
pyodbc.connect = fake_connect([UNAVAILABLE] * 4)
try:
    db_sql._azure_connect(); raised = False
except pyodbc.ProgrammingError:
    raised = True
check("still unavailable after the retries: the original error", raised and len(calls) == 4 and slept == [1, 3, 6])

# Пул Azure SQL: найсвіжіше з'єднання (LIFO) — і ці налаштування справді приймає SQLAlchemy
import sqlalchemy as sa
eng = sa.create_engine("mssql+pyodbc://", creator=lambda: None, future=True, **db_sql.AZURE_POOL)
check("pool hands out the freshest connection (LIFO)", db_sql.AZURE_POOL["pool_use_lifo"] is True
      and getattr(eng.pool._pool, "use_lifo", False) is True)
print("ALL OK" if ok else "SOME FAILED")
