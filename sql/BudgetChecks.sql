/* PayDesk · перевірки перед підключенням таблиць бюджету в Power BI (виконати в SSMS на базі expeditor). */

-- 1) Ключі відділів: та сама строка має бути в Departments[DepartmentGUID] датасету PayDesk.
SELECT _Code AS DepartmentCode, _Description AS DepartmentName, CONVERT(varchar(32), _IDRRef, 2) AS DepartmentGUID
FROM dbo._Reference13165
WHERE _Marked = 0x00
ORDER BY _Code;

-- 2) Звірка з кубом FTP_Budgeting: суми плану й факту по відділах і місяцях 2026 року (запит BudgetPlanFact як є).
--    Мають збігтися з [Сума план] / [Сума факт] куба за тим самим відділом і місяцем.
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

            /* ---------- ФАКТ: проводки 8100 / 8200 ---------- */
            SELECT
                DATEFROMPARTS(YEAR(dt.D), MONTH(dt.D), 1)                        AS [Month],
                CASE rt.Route WHEN 2 THEN k.OwnerDep
                              WHEN 1 THEN COALESCE(k.DocDep, 0x00000000000000000000000000000000)
                              ELSE 0x00000000000000000000000000000000 END        AS DepRef,
                COALESCE(sk.ItemRef, 0x00000000000000000000000000000000)         AS ItemRef,
                CAST(0 AS numeric(28,2))                                         AS PlanUSD,
                CAST(sd.Sign * r._Fld6735 AS numeric(28,2))                      AS FactUSD
            FROM dbo._AccRg6732 r
            CROSS APPLY (VALUES
                (0,  1, r._AccountDtRRef, r._Fld17936DtRRef),    -- дебет: витрата
                (1, -1, r._AccountCtRRef, r._Fld17936CtRRef)     -- кредит: сторно при перекласифікації
            ) sd(Side, Sign, AccRef, DepLineRef)
            INNER JOIN (
                SELECT ac._IDRRef
                FROM dbo._Acc6699 ac
                LEFT JOIN dbo._Acc6699 p1 ON p1._IDRRef = ac._ParentIDRRef
                LEFT JOIN dbo._Acc6699 p2 ON p2._IDRRef = p1._ParentIDRRef
                WHERE RTRIM(ac._Code) IN ('8100', '8200') OR RTRIM(p1._Code) IN ('8100', '8200') OR RTRIM(p2._Code) IN ('8100', '8200')
            ) acc ON acc._IDRRef = sd.AccRef
            OUTER APPLY (
                SELECT MAX(CASE WHEN ed._Value_RTRef = 0x0000133C THEN ed._Value_RRRef END) AS ItemRef
                FROM dbo._AccRgED6757 ed
                WHERE ed._RecorderTRef = r._RecorderTRef AND ed._RecorderRRef = r._RecorderRRef
                  AND ed._LineNo = r._LineNo AND ed._Correspond = sd.Side
            ) sk
            LEFT JOIN dbo._Reference4924  vr      ON vr._IDRRef      = sk.ItemRef            -- без фільтра _Marked, щоб не губити суми
            LEFT JOIN dbo._Document7811   spis    ON spis._IDRRef    = r._RecorderRRef AND r._RecorderTRef = 0x00001E83
            LEFT JOIN dbo._Document7810   nakl    ON nakl._IDRRef    = r._RecorderRRef AND r._RecorderTRef = 0x00001E82
            LEFT JOIN dbo._Document15307  zp_okl  ON zp_okl._IDRRef  = r._RecorderRRef AND r._RecorderTRef = 0x00003BCB
            LEFT JOIN dbo._Document15308  zp_proc ON zp_proc._IDRRef = r._RecorderRRef AND r._RecorderTRef = 0x00003BCC
            LEFT JOIN dbo._Enum15970      zp_vid  ON zp_vid._IDRRef  = zp_okl._Fld15975RRef  -- ВидЗП документа нарахування ЗП
            LEFT JOIN dbo._Reference13165 hdr_dep ON hdr_dep._IDRRef = CASE r._RecorderTRef  -- «Отдел» шапки документа-джерела
                    WHEN 0x00001E83 THEN spis._Fld14095RRef     -- СписаниеДенСредств.Отдел
                    WHEN 0x00001E82 THEN nakl._Fld13855RRef     -- НакладныеРасходы.Отдел
                    WHEN 0x00003BCB THEN zp_okl._Fld15638RRef   -- НачислениеЗПпоОкладам.Отдел
                    WHEN 0x00003BCC THEN zp_proc._Fld15639RRef  -- ПрочееНачислениеУдержаниеПоЗП.Отдел
                END
            CROSS APPLY (SELECT
                COALESCE(NULLIF(sd.DepLineRef, 0x00000000000000000000000000000000), hdr_dep._IDRRef) AS DocDep,
                NULLIF(vr._Fld19036RRef, 0x00000000000000000000000000000000)                        AS OwnerDep) k
            CROSS APPLY (SELECT CASE
                WHEN vr._IDRRef IS NULL        THEN 0   -- статті немає
                WHEN k.OwnerDep IS NOT NULL    THEN 2   -- непряма: Owner статті
                ELSE                                1   -- пряма: відділ документа
                END AS Route) rt
            CROSS APPLY (SELECT CAST(DATEADD(year, -2000, r._Period) AS date) AS D) dt
            WHERE r._Active = 0x01
              AND r._Period >= '4024-01-01'
              AND (sd.Side = 0 OR r._AccountDtRRef IN (
                    SELECT ac._IDRRef
                    FROM dbo._Acc6699 ac
                    LEFT JOIN dbo._Acc6699 p1 ON p1._IDRRef = ac._ParentIDRRef
                    LEFT JOIN dbo._Acc6699 p2 ON p2._IDRRef = p1._ParentIDRRef
                    WHERE RTRIM(ac._Code) IN ('8100', '8200') OR RTRIM(p1._Code) IN ('8100', '8200') OR RTRIM(p2._Code) IN ('8100', '8200')))
              AND NOT (r._RecorderTRef = 0x00003BCB AND COALESCE(zp_vid._EnumOrder, -1) = 1)   -- без премій керівникам
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

-- 4) Перехід: старий факт (документ «Розподіл накладних витрат») проти нового (проводки 8100/8200) за 2026 рік.
--    Різниця очікувана: премії керівникам (у новому факті їх немає), місяці, які ще не розподілено (у новому факті вже є),
--    і перекласифікації між 8100/8200.
SELECT COALESCE(dep._Description, N'Інше') AS DepartmentName, v.[Month],
       SUM(v.OldFactUSD) AS OldFactUSD, SUM(v.NewFactUSD) AS NewFactUSD, SUM(v.NewFactUSD) - SUM(v.OldFactUSD) AS DiffUSD
FROM (
    SELECT o.[Month], o.DepRef, o.FactUSD AS OldFactUSD, CAST(0 AS numeric(28,2)) AS NewFactUSD
    FROM (
    SELECT
        DATEFROMPARTS(YEAR(DATEADD(year, -2000, doc._Date_Time)), MONTH(DATEADD(year, -2000, doc._Date_Time)), 1) AS [Month],
        CASE
            WHEN vr._IDRRef IS NULL THEN 0x00000000000000000000000000000000
            WHEN vr._Fld19036RRef IS NOT NULL AND vr._Fld19036RRef <> 0x00000000000000000000000000000000
                THEN vr._Fld19036RRef
            ELSE COALESCE(vt._Fld15234RRef, 0x00000000000000000000000000000000)
        END AS DepRef,
        CAST(vt._Fld15232 AS numeric(28,2)) AS FactUSD
    FROM dbo._Document15134_VT15228 vt
    INNER JOIN dbo._Document15134 doc ON doc._IDRRef = vt._Document15134_IDRRef
    LEFT JOIN dbo._Reference4924 vr ON vr._IDRRef = vt._Fld15231RRef
    WHERE doc._Marked = 0x00 AND doc._Posted = 0x01
      AND doc._Date_Time >= DATEADD(year, 2000, '2026-01-01') AND doc._Date_Time < DATEADD(year, 2000, '2027-01-01')
    ) o
    UNION ALL
    SELECT n.[Month], CONVERT(binary(16), CONVERT(varbinary(16), n.DepartmentGUID, 2)), CAST(0 AS numeric(28,2)), n.FactUSD
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

                /* ---------- ФАКТ: проводки 8100 / 8200 ---------- */
                SELECT
                    DATEFROMPARTS(YEAR(dt.D), MONTH(dt.D), 1)                        AS [Month],
                    CASE rt.Route WHEN 2 THEN k.OwnerDep
                                  WHEN 1 THEN COALESCE(k.DocDep, 0x00000000000000000000000000000000)
                                  ELSE 0x00000000000000000000000000000000 END        AS DepRef,
                    COALESCE(sk.ItemRef, 0x00000000000000000000000000000000)         AS ItemRef,
                    CAST(0 AS numeric(28,2))                                         AS PlanUSD,
                    CAST(sd.Sign * r._Fld6735 AS numeric(28,2))                      AS FactUSD
                FROM dbo._AccRg6732 r
                CROSS APPLY (VALUES
                    (0,  1, r._AccountDtRRef, r._Fld17936DtRRef),    -- дебет: витрата
                    (1, -1, r._AccountCtRRef, r._Fld17936CtRRef)     -- кредит: сторно при перекласифікації
                ) sd(Side, Sign, AccRef, DepLineRef)
                INNER JOIN (
                    SELECT ac._IDRRef
                    FROM dbo._Acc6699 ac
                    LEFT JOIN dbo._Acc6699 p1 ON p1._IDRRef = ac._ParentIDRRef
                    LEFT JOIN dbo._Acc6699 p2 ON p2._IDRRef = p1._ParentIDRRef
                    WHERE RTRIM(ac._Code) IN ('8100', '8200') OR RTRIM(p1._Code) IN ('8100', '8200') OR RTRIM(p2._Code) IN ('8100', '8200')
                ) acc ON acc._IDRRef = sd.AccRef
                OUTER APPLY (
                    SELECT MAX(CASE WHEN ed._Value_RTRef = 0x0000133C THEN ed._Value_RRRef END) AS ItemRef
                    FROM dbo._AccRgED6757 ed
                    WHERE ed._RecorderTRef = r._RecorderTRef AND ed._RecorderRRef = r._RecorderRRef
                      AND ed._LineNo = r._LineNo AND ed._Correspond = sd.Side
                ) sk
                LEFT JOIN dbo._Reference4924  vr      ON vr._IDRRef      = sk.ItemRef            -- без фільтра _Marked, щоб не губити суми
                LEFT JOIN dbo._Document7811   spis    ON spis._IDRRef    = r._RecorderRRef AND r._RecorderTRef = 0x00001E83
                LEFT JOIN dbo._Document7810   nakl    ON nakl._IDRRef    = r._RecorderRRef AND r._RecorderTRef = 0x00001E82
                LEFT JOIN dbo._Document15307  zp_okl  ON zp_okl._IDRRef  = r._RecorderRRef AND r._RecorderTRef = 0x00003BCB
                LEFT JOIN dbo._Document15308  zp_proc ON zp_proc._IDRRef = r._RecorderRRef AND r._RecorderTRef = 0x00003BCC
                LEFT JOIN dbo._Enum15970      zp_vid  ON zp_vid._IDRRef  = zp_okl._Fld15975RRef  -- ВидЗП документа нарахування ЗП
                LEFT JOIN dbo._Reference13165 hdr_dep ON hdr_dep._IDRRef = CASE r._RecorderTRef  -- «Отдел» шапки документа-джерела
                        WHEN 0x00001E83 THEN spis._Fld14095RRef     -- СписаниеДенСредств.Отдел
                        WHEN 0x00001E82 THEN nakl._Fld13855RRef     -- НакладныеРасходы.Отдел
                        WHEN 0x00003BCB THEN zp_okl._Fld15638RRef   -- НачислениеЗПпоОкладам.Отдел
                        WHEN 0x00003BCC THEN zp_proc._Fld15639RRef  -- ПрочееНачислениеУдержаниеПоЗП.Отдел
                    END
                CROSS APPLY (SELECT
                    COALESCE(NULLIF(sd.DepLineRef, 0x00000000000000000000000000000000), hdr_dep._IDRRef) AS DocDep,
                    NULLIF(vr._Fld19036RRef, 0x00000000000000000000000000000000)                        AS OwnerDep) k
                CROSS APPLY (SELECT CASE
                    WHEN vr._IDRRef IS NULL        THEN 0   -- статті немає
                    WHEN k.OwnerDep IS NOT NULL    THEN 2   -- непряма: Owner статті
                    ELSE                                1   -- пряма: відділ документа
                    END AS Route) rt
                CROSS APPLY (SELECT CAST(DATEADD(year, -2000, r._Period) AS date) AS D) dt
                WHERE r._Active = 0x01
                  AND r._Period >= '4024-01-01'
                  AND (sd.Side = 0 OR r._AccountDtRRef IN (
                        SELECT ac._IDRRef
                        FROM dbo._Acc6699 ac
                        LEFT JOIN dbo._Acc6699 p1 ON p1._IDRRef = ac._ParentIDRRef
                        LEFT JOIN dbo._Acc6699 p2 ON p2._IDRRef = p1._ParentIDRRef
                        WHERE RTRIM(ac._Code) IN ('8100', '8200') OR RTRIM(p1._Code) IN ('8100', '8200') OR RTRIM(p2._Code) IN ('8100', '8200')))
                  AND NOT (r._RecorderTRef = 0x00003BCB AND COALESCE(zp_vid._EnumOrder, -1) = 1)   -- без премій керівникам
            ) u
            GROUP BY u.[Month], u.DepRef, u.ItemRef
        ) a
        LEFT JOIN dbo._Reference13165 dep ON dep._IDRRef = a.DepRef
        LEFT JOIN dbo._Reference4924  e   ON e._IDRRef   = a.ItemRef
        LEFT JOIN dbo._Reference4924  grp ON grp._IDRRef = e._ParentIDRRef
    ) n
    WHERE YEAR(n.[Month]) = 2026 AND n.FactUSD <> 0
) v
LEFT JOIN dbo._Reference13165 dep ON dep._IDRRef = v.DepRef
GROUP BY COALESCE(dep._Description, N'Інше'), v.[Month]
HAVING SUM(v.NewFactUSD) <> SUM(v.OldFactUSD)
ORDER BY DepartmentName, v.[Month];

-- 5) Факт без статті (проводки 8100/8200 без субконто «Вид расходов» або статті немає в довіднику) — «Інше / не
--    розподілено» у PayDesk. Документи, з якими варто розібратися в 1С.
SELECT d.DocumentType, d.DocumentNumber, d.DocumentDate, d.Account, d.Side, d.AmountUSD, d.EntryText
FROM (
    SELECT
        dt.D                                                                AS [Date],
        DATEFROMPARTS(YEAR(dt.D), MONTH(dt.D), 1)                           AS [Month],
        CONVERT(varchar(32), CASE rt.Route WHEN 2 THEN k.OwnerDep
                                           WHEN 1 THEN COALESCE(k.DocDep, 0x00000000000000000000000000000000)
                                           ELSE 0x00000000000000000000000000000000 END, 2) AS DepartmentGUID,
        CONVERT(varchar(32), COALESCE(k.DocDep, 0x00000000000000000000000000000000), 2)    AS LineDepartmentGUID,
        CASE rt.Route WHEN 2 THEN N'На Owner статті'
                      WHEN 1 THEN N'Прямий (відділ рядка)'
                      ELSE        N'Не розподілено: стаття не знайдена' END             AS AllocationStatus,
        CONVERT(varchar(32), COALESCE(sk.ItemRef, 0x00000000000000000000000000000000), 2) AS ExpenseItemGUID,
        COALESCE(RTRIM(vr._Code), N'')                                      AS ExpenseItemCode,
        CAST(sd.Sign * r._Fld6735 AS numeric(28,2))                         AS AmountUSD,
        COALESCE(tp.Name, N'Інше')                                          AS DocumentType,
        CONVERT(varchar(32), r._RecorderRRef, 2)                            AS SourceDocumentGUID,
        CASE r._RecorderTRef
            WHEN 0x00001E83 THEN spis._Number    WHEN 0x00001E82 THEN nakl._Number
            WHEN 0x00001347 THEN prih._Number    WHEN 0x000019DA THEN vnutr._Number
            WHEN 0x00003640 THEN obmen._Number   WHEN 0x00003BCB THEN zp_okl._Number
            WHEN 0x00003BCC THEN zp_proc._Number WHEN 0x00004947 THEN kom_bank._Number
        END                                                                 AS DocumentNumber,
        CAST(DATEADD(year, -2000, COALESCE(
            spis._Date_Time, nakl._Date_Time, prih._Date_Time, vnutr._Date_Time,
            obmen._Date_Time, zp_okl._Date_Time, zp_proc._Date_Time, kom_bank._Date_Time, r._Period)) AS date) AS DocumentDate,
        CASE r._RecorderTRef
            WHEN 0x00001E83 THEN spis._Fld7982     WHEN 0x00001E82 THEN nakl._Fld7938
            WHEN 0x00001347 THEN prih._Fld5110     WHEN 0x000019DA THEN vnutr._Fld6630
            WHEN 0x00003640 THEN obmen._Fld13903   WHEN 0x00003BCB THEN zp_okl._Fld15323
            WHEN 0x00003BCC THEN zp_proc._Fld15350 WHEN 0x00004947 THEN kom_bank._Fld18761
        END                                                                 AS Comment,
        CONVERT(varchar(32), COALESCE(CASE WHEN r._RecorderTRef = 0x00001E83 THEN spis._Fld10416RRef END,
                                      0x00000000000000000000000000000000), 2) AS EmployeeGUID,
        COALESCE(p._Description, N'')                                       AS EmployeeName,
        RTRIM(a.Code)                                                       AS Account,
        RTRIM(cor._Code)                                                    AS CorrAccount,
        CASE sd.Side WHEN 0 THEN N'Дт' ELSE N'Кт' END                       AS Side,
        r._Fld6738                                                          AS EntryText
    FROM dbo._AccRg6732 r
    CROSS APPLY (VALUES
        (0,  1, r._AccountDtRRef, r._AccountCtRRef, r._Fld17936DtRRef),    -- дебет: витрата
        (1, -1, r._AccountCtRRef, r._AccountDtRRef, r._Fld17936CtRRef)     -- кредит: сторно при перекласифікації
    ) sd(Side, Sign, AccRef, CorAccRef, DepLineRef)
    INNER JOIN (
        SELECT ac._IDRRef, ac._Code AS Code
        FROM dbo._Acc6699 ac
        LEFT JOIN dbo._Acc6699 p1 ON p1._IDRRef = ac._ParentIDRRef
        LEFT JOIN dbo._Acc6699 p2 ON p2._IDRRef = p1._ParentIDRRef
        WHERE RTRIM(ac._Code) IN ('8100', '8200') OR RTRIM(p1._Code) IN ('8100', '8200') OR RTRIM(p2._Code) IN ('8100', '8200')
    ) a ON a._IDRRef = sd.AccRef
    LEFT JOIN dbo._Acc6699 cor ON cor._IDRRef = sd.CorAccRef
    LEFT JOIN (VALUES
            (0x000012EB, N'Сделка'),
            (0x00001318, N'Счет'),
            (0x00001344, N'Коносамент'),
            (0x00001345, N'Наряд'),
            (0x00001347, N'Прихід'),
            (0x00001428, N'Расход'),
            (0x000014C2, N'Поручение'),
            (0x00001517, N'ДвижениеКонтейнеров'),
            (0x00001599, N'CMR'),
            (0x00001601, N'Котировка'),
            (0x000016BE, N'НалоговыеНакладные'),
            (0x00001708, N'ПроформаСчета'),
            (0x00001873, N'Аванс2'),
            (0x000018F3, N'ДебитНота'),
            (0x00001903, N'КредитНота'),
            (0x000019DA, N'Внутрішнє переміщення'),
            (0x00001A9B, N'ЗаявкаНаОплату'),
            (0x00001B41, N'ОсновныеСредства'),
            (0x00001B76, N'ОперацияБух'),
            (0x00001BCD, N'КурсовыеРазницы'),
            (0x00001C19, N'СохранениеОстатков'),
            (0x00001CA2, N'Акты'),
            (0x00001CE1, N'ЗакрытиеПериода'),
            (0x00001D6B, N'СтатусКонтейнера'),
            (0x00001D6C, N'ЗаявкаНаАвтоперевозку'),
            (0x00001E81, N'РасходнаяНакладная'),
            (0x00001E82, N'Накладні витрати'),
            (0x00001E83, N'Списання'),
            (0x00001E84, N'ВыгрузкаРегламентированныхОтчетов'),
            (0x00002068, N'ИзвещениеНаДемередж'),
            (0x00002240, N'ПоступлениеАктивов'),
            (0x00002241, N'НачислениеЗП'),
            (0x00002242, N'ВводВЭксплуатацию'),
            (0x00002243, N'ВыплатаЗП'),
            (0x00002244, N'Взаимозачет'),
            (0x00002544, N'НачислениеИзноса'),
            (0x00002545, N'Агенты'),
            (0x00002546, N'ЗаправкаАвтомобилей'),
            (0x00002547, N'ЭлектронноеПисьмо'),
            (0x00002548, N'ФинансовыйРезультат'),
            (0x00002549, N'НалоговаяНакладнаяВходящая'),
            (0x0000254A, N'НачислениеВДРпоАвансам'),
            (0x0000296F, N'ПланКасса'),
            (0x00002BA0, N'БалансСЛинией'),
            (0x00002BB8, N'СливГСМ'),
            (0x00002BB9, N'РемонтныйЛист'),
            (0x00002CC3, N'Бюджет'),
            (0x00002D1C, N'ЗаявкаНаЖДконтейнер'),
            (0x00002D81, N'Аванс'),
            (0x00002D82, N'ПереоценкаВалютныхОстатков'),
            (0x00002E06, N'РегламентированныйОтчет'),
            (0x000034EC, N'ПоступлениеДС'),
            (0x0000350A, N'СписаниеДС'),
            (0x00003560, N'ОбязательствоДебет'),
            (0x00003561, N'ОбязательствоКредит'),
            (0x00003598, N'Кредит'),
            (0x00003640, N'Обмін'),
            (0x000036CB, N'ОбменСделка'),
            (0x00003925, N'ПоступлениеТоваров'),
            (0x00003926, N'СписаниеТоваров'),
            (0x00003927, N'ПеремещениеТоваров'),
            (0x00003928, N'ПоступлениеДопРасходов'),
            (0x00003AAA, N'ОбъединениеБалансов'),
            (0x00003AAB, N'ОбменБалансами'),
            (0x00003B1E, N'РаспределениеНакладныхРасходов'),
            (0x00003BAD, N'ПланированиеАванса'),
            (0x00003BCB, N'Нарахування ЗП по окладах'),
            (0x00003BCC, N'Інше нарахування/утримання ЗП'),
            (0x00003BCD, N'ВыплатаЗПпоВалютам'),
            (0x00003C2A, N'ЗаявкаНаАвтоперевозкуКлиент'),
            (0x00003CC6, N'НачислениеБонусов'),
            (0x00003D45, N'OLDНачислениеБонусовОперативный'),
            (0x00003DA1, N'НачислениеПремий'),
            (0x00003E0C, N'НачислениеБонусовОперативнымМенеджерам'),
            (0x00003E59, N'НачислениеШтрафа'),
            (0x00003ED0, N'НачислениеБонусовУчредителям'),
            (0x00003FA1, N'Консоль'),
            (0x000040F7, N'СправкаОТранспортныхРасходах'),
            (0x00004168, N'КонсольLCL'),
            (0x00004231, N'ЗаявкаМореАвиаЖДУкр'),
            (0x00004239, N'ЗаявкаМореАвиаЖДАнгл'),
            (0x0000424E, N'ИнвойсыКонтрактодержатель'),
            (0x00004266, N'РаспределениеКурсовыхРазниц'),
            (0x00004267, N'КупляПродажаВалюты'),
            (0x0000446A, N'ЗаявкаАвто'),
            (0x00004616, N'ЗаявкаНаОплатуСчета'),
            (0x00004617, N'ЗаявкаНаОплатуРасходов'),
            (0x00004743, N'КомандаОтделаПродаж'),
            (0x0000476C, N'НачислениеПремийНачальникамОтделов'),
            (0x00004832, N'ВнутреннееПеремещениеФин'),
            (0x0000492D, N'КомплексФинСделок'),
            (0x00004947, N'Розподіл комісій банку'),
            (0x00004A00, N'БюджетНовый'),
            (0x00004A01, N'ПланированиеПродаж')
    ) tp(TRef, Name) ON tp.TRef = r._RecorderTRef
    OUTER APPLY (
        SELECT MAX(CASE WHEN ed._Value_RTRef = 0x0000133C THEN ed._Value_RRRef END) AS ItemRef
        FROM dbo._AccRgED6757 ed
        WHERE ed._RecorderTRef = r._RecorderTRef AND ed._RecorderRRef = r._RecorderRRef
          AND ed._LineNo = r._LineNo AND ed._Correspond = sd.Side
    ) sk
    LEFT JOIN dbo._Reference4924  vr       ON vr._IDRRef       = sk.ItemRef           -- без фільтра _Marked, щоб не губити суми
    LEFT JOIN dbo._Document7811   spis     ON spis._IDRRef     = r._RecorderRRef AND r._RecorderTRef = 0x00001E83
    LEFT JOIN dbo._Document7810   nakl     ON nakl._IDRRef     = r._RecorderRRef AND r._RecorderTRef = 0x00001E82
    LEFT JOIN dbo._Document4935   prih     ON prih._IDRRef     = r._RecorderRRef AND r._RecorderTRef = 0x00001347
    LEFT JOIN dbo._Document6618   vnutr    ON vnutr._IDRRef    = r._RecorderRRef AND r._RecorderTRef = 0x000019DA
    LEFT JOIN dbo._Document13888  obmen    ON obmen._IDRRef    = r._RecorderRRef AND r._RecorderTRef = 0x00003640
    LEFT JOIN dbo._Document15307  zp_okl   ON zp_okl._IDRRef   = r._RecorderRRef AND r._RecorderTRef = 0x00003BCB
    LEFT JOIN dbo._Document15308  zp_proc  ON zp_proc._IDRRef  = r._RecorderRRef AND r._RecorderTRef = 0x00003BCC
    LEFT JOIN dbo._Document18759  kom_bank ON kom_bank._IDRRef = r._RecorderRRef AND r._RecorderTRef = 0x00004947
    LEFT JOIN dbo._Enum15970      zp_vid   ON zp_vid._IDRRef   = zp_okl._Fld15975RRef  -- ВидЗП документа нарахування ЗП
    LEFT JOIN dbo._Reference13165 hdr_dep  ON hdr_dep._IDRRef  = CASE r._RecorderTRef  -- «Отдел» шапки документа-джерела
            WHEN 0x00001E83 THEN spis._Fld14095RRef     -- СписаниеДенСредств.Отдел
            WHEN 0x00001E82 THEN nakl._Fld13855RRef     -- НакладныеРасходы.Отдел
            WHEN 0x00003BCB THEN zp_okl._Fld15638RRef   -- НачислениеЗПпоОкладам.Отдел
            WHEN 0x00003BCC THEN zp_proc._Fld15639RRef  -- ПрочееНачислениеУдержаниеПоЗП.Отдел
        END
    LEFT JOIN dbo._Reference59    p        ON p._IDRRef        = CASE WHEN r._RecorderTRef = 0x00001E83 THEN spis._Fld10416RRef END
    CROSS APPLY (SELECT
        COALESCE(NULLIF(sd.DepLineRef, 0x00000000000000000000000000000000), hdr_dep._IDRRef) AS DocDep,
        NULLIF(vr._Fld19036RRef, 0x00000000000000000000000000000000)                        AS OwnerDep) k
    CROSS APPLY (SELECT CASE
        WHEN vr._IDRRef IS NULL        THEN 0   -- статті немає
        WHEN k.OwnerDep IS NOT NULL    THEN 2   -- непряма: Owner статті
        ELSE                                1   -- пряма: відділ документа
        END AS Route) rt
    CROSS APPLY (SELECT CAST(DATEADD(year, -2000, r._Period) AS date) AS D) dt
    WHERE r._Active = 0x01
      AND r._Period >= '4024-01-01'
      AND (sd.Side = 0 OR r._AccountDtRRef IN (
            SELECT ac._IDRRef
                FROM dbo._Acc6699 ac
                LEFT JOIN dbo._Acc6699 p1 ON p1._IDRRef = ac._ParentIDRRef
                LEFT JOIN dbo._Acc6699 p2 ON p2._IDRRef = p1._ParentIDRRef
                WHERE RTRIM(ac._Code) IN ('8100', '8200') OR RTRIM(p1._Code) IN ('8100', '8200') OR RTRIM(p2._Code) IN ('8100', '8200')))
      AND NOT (r._RecorderTRef = 0x00003BCB AND COALESCE(zp_vid._EnumOrder, -1) = 1)   -- без премій керівникам
) d
WHERE d.AllocationStatus LIKE N'Не розподілено:%' AND YEAR(d.[Date]) = 2026
ORDER BY d.[Date];
