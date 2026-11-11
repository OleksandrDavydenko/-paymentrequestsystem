/* PayDesk · перевірки перед підключенням таблиць бюджету в Power BI (виконати в SSMS на базі expeditor). */

-- 1) Ключі відділів: та сама строка має бути в Departments[DepartmentGUID] датасету PayDesk.
SELECT _Code AS DepartmentCode, _Description AS DepartmentName, CONVERT(varchar(32), _IDRRef, 2) AS DepartmentGUID
FROM dbo._Reference13165
WHERE _Marked = 0x00
ORDER BY _Code;

-- 2) Звірка з кубом FTP_Budgeting / Excel: суми по відділах і місяцях 2026 року.
--    Наприклад, FTP PL Spółka z o.o.: січень — план 6 433,83, факт 6 819,46; лютий — 6 494,14 / 8 521,90.
SELECT x.DepartmentName, x.[Month], SUM(x.PlanUSD) AS PlanUSD, SUM(x.FactUSD) AS FactUSD
FROM (
    SELECT
        a.[Month],
        CONVERT(varchar(32), a.DepRef, 2)                                   AS DepartmentGUID,
        COALESCE(dep._Description, N'Інше')                                 AS DepartmentName,
        CONVERT(varchar(32), a.ItemRef, 2)                                  AS ExpenseItemGUID,
        COALESCE(RTRIM(e._Code), N'')                                       AS ExpenseItemCode,
        COALESCE(e._Description, N'(без статті)')                           AS ExpenseItemName,
        COALESCE(grp._Description, N'(без групи)')                          AS ExpenseGroup,
        CONVERT(varchar(32), COALESCE(e._Fld19036RRef, 0x00000000000000000000000000000000), 2) AS OwnerDepartmentGUID,
        a.PlanUSD,
        a.FactUSD
    FROM (
        SELECT u.[Month], u.DepRef, u.ItemRef, SUM(u.PlanUSD) AS PlanUSD, SUM(u.FactUSD) AS FactUSD
        FROM (
            /* ---------- ПЛАН ---------- */
            SELECT
                DATEFROMPARTS(y.Yr, m.MonthNo, 1)                                AS [Month],
                COALESCE(d._Fld19037RRef, 0x00000000000000000000000000000000)    AS DepRef,
                COALESCE(s._Fld19038RRef, 0x00000000000000000000000000000000)    AS ItemRef,
                CAST(m.Amount AS numeric(28,2))                                  AS PlanUSD,
                CAST(0 AS numeric(28,2))                                         AS FactUSD
            FROM dbo._Document11459 d
            INNER JOIN dbo._Document11459_VT11489 s ON s._Document11459_IDRRef = d._IDRRef
            CROSS APPLY (SELECT CASE WHEN PATINDEX('%[0-9][0-9][0-9][0-9]%', d._Fld11488) > 0
                             THEN CAST(SUBSTRING(d._Fld11488, PATINDEX('%[0-9][0-9][0-9][0-9]%', d._Fld11488), 4) AS int)
                         END AS Yr) y
            CROSS APPLY (VALUES
                (1,  s._Fld11505), (2,  s._Fld11506), (3,  s._Fld11507), (4,  s._Fld11508),
                (5,  s._Fld11509), (6,  s._Fld11510), (7,  s._Fld11511), (8,  s._Fld11512),
                (9,  s._Fld11513), (10, s._Fld11514), (11, s._Fld11515), (12, s._Fld11516)
            ) m(MonthNo, Amount)
            WHERE d._Marked = 0x00 AND m.Amount <> 0 AND y.Yr IS NOT NULL

            UNION ALL

            /* ---------- ФАКТ ---------- */
            SELECT
                DATEFROMPARTS(YEAR(dt.D), MONTH(dt.D), 1)                        AS [Month],
                CASE
                    WHEN vr._IDRRef IS NULL THEN 0x00000000000000000000000000000000
                    WHEN vr._Fld19036RRef IS NOT NULL
                         AND vr._Fld19036RRef <> 0x00000000000000000000000000000000
                        THEN vr._Fld19036RRef                                            -- Owner статті
                    ELSE COALESCE(vt._Fld15234RRef, 0x00000000000000000000000000000000)  -- відділ рядка
                END                                                              AS DepRef,
                COALESCE(vt._Fld15231RRef, 0x00000000000000000000000000000000)   AS ItemRef,
                CAST(0 AS numeric(28,2))                                         AS PlanUSD,
                CAST(vt._Fld15232 AS numeric(28,2))                              AS FactUSD
            FROM dbo._Document15134_VT15228 vt
            INNER JOIN dbo._Document15134 doc ON doc._IDRRef = vt._Document15134_IDRRef
            LEFT JOIN dbo._Reference4924 vr  ON vr._IDRRef  = vt._Fld15231RRef   -- без фільтра _Marked, щоб не губити суми
            CROSS APPLY (SELECT CAST(DATEADD(year, -2000, doc._Date_Time) AS date) AS D) dt
            WHERE doc._Marked = 0x00 AND doc._Posted = 0x01
              AND doc._Date_Time >= DATEADD(year, 2000, '2024-01-01')
        ) u
        GROUP BY u.[Month], u.DepRef, u.ItemRef
    ) a
    LEFT JOIN dbo._Reference13165 dep ON dep._IDRRef = a.DepRef
    LEFT JOIN dbo._Reference4924  e   ON e._IDRRef   = a.ItemRef
    LEFT JOIN dbo._Reference4924  grp ON grp._IDRRef = e._ParentIDRRef
) x
WHERE YEAR(x.[Month]) = 2026
GROUP BY x.DepartmentName, x.[Month]
ORDER BY x.DepartmentName, x.[Month];

-- 3) Чи є кілька документів «Бюджет» на один відділ і рік (версії) — тоді план подвоюється і в кубі, і тут.
SELECT d._Fld11488 AS Period, CONVERT(varchar(32), d._Fld19037RRef, 2) AS DepartmentGUID,
       COUNT(*) AS Documents, SUM(CASE WHEN d._Posted = 0x01 THEN 1 ELSE 0 END) AS Posted
FROM dbo._Document11459 d
WHERE d._Marked = 0x00
GROUP BY d._Fld11488, d._Fld19037RRef
HAVING COUNT(*) > 1;
