/* PayDesk · BudgetPlanFact — план і факт бюджету по місяцях, USD. Рядок = місяць × відділ × стаття.
   Логіка — як у кубі FTP_Budgeting:
     ПЛАН — Документ.Бюджет: шапка _Document11459 (відділ _Fld19037RRef, період _Fld11488 «2026 год»),
            рядки _Document11459_VT11489 (стаття _Fld19038RRef, суми місяців _Fld11505.._Fld11516);
     ФАКТ — РегистрБухгалтерии.Хозрасчетный (_AccRg6732), рахунки 8100 / 8200 і їхні субрахунки: рядок факту з'являється
            одразу після проведення документа-джерела (документ «Розподіл накладних витрат» більше не використовується).
            +  дебет 8100/8200 (будь-який кор. рахунок)                           -> аналітика Дт;
            -  кредит 8100/8200, ЛИШЕ якщо дебет теж 8100/8200 (перекласифікація) -> аналітика Кт;
            кредит 8100/8200 на інші рахунки (закриття періоду тощо) не береться.
            СТАТТЯ — субконто проводки своєї сторони (_AccRgED6757, тип 0x0000133C = Справочник.ВидыРасходов).
            ВІДДІЛ — Owner статті (_Fld19036RRef), якщо заповнений; інакше відділ проводки (ОтделДт/Кт _Fld17936),
            а якщо він порожній — «Отдел» шапки документа-джерела; статті немає — нульовий відділ («Інше»).
            Без премій керівникам: НачислениеЗПпоОкладам з Вид (_Fld15975RRef, _Enum15970) = порядок 1.
   GUID — 32 hex без 0x (як Departments[DepartmentGUID]); нулі — «не вказано».
   Швидкодія: з 2025 року; проводки відсікаються за рахунком дебету до розгортання на сторони; WITH (NOLOCK) — не
   блокує 1С і не стає жертвою deadlock; OPTION (MAXDOP 1) — без deadlock між потоками паралельного плану (якщо
   Power BI не прийме OPTION, рядок можна прибрати). Без CTE (WITH ... AS), щоб запит працював у Power BI як є. */
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
        FROM dbo._Document11459 d WITH (NOLOCK)
        INNER JOIN dbo._Document11459_VT11489 s WITH (NOLOCK) ON s._Document11459_IDRRef = d._IDRRef
        CROSS APPLY (SELECT CASE WHEN PATINDEX('%[0-9][0-9][0-9][0-9]%', d._Fld11488) > 0
                         THEN CAST(SUBSTRING(d._Fld11488, PATINDEX('%[0-9][0-9][0-9][0-9]%', d._Fld11488), 4) AS int)
                     END AS Yr) y
        CROSS APPLY (VALUES
            (1,  s._Fld11505), (2,  s._Fld11506), (3,  s._Fld11507), (4,  s._Fld11508),
            (5,  s._Fld11509), (6,  s._Fld11510), (7,  s._Fld11511), (8,  s._Fld11512),
            (9,  s._Fld11513), (10, s._Fld11514), (11, s._Fld11515), (12, s._Fld11516)
        ) m(MonthNo, Amount)
        WHERE d._Marked = 0x00 AND m.Amount <> 0 AND y.Yr >= 2025

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
        FROM dbo._AccRg6732 r WITH (NOLOCK)
        -- Обидві сторони вимагають дебет 8100/8200 (Дт — витрата; Кт — лише перекласифікація між 8100/8200), тож проводки
        -- відсікаються одразу за рахунком дебету — індексом, без подвоєння всіх рядків регістру
        INNER JOIN (
        SELECT ac._IDRRef, ac._Code AS Code
            FROM dbo._Acc6699 ac WITH (NOLOCK)
            LEFT JOIN dbo._Acc6699 p1 WITH (NOLOCK) ON p1._IDRRef = ac._ParentIDRRef
            LEFT JOIN dbo._Acc6699 p2 WITH (NOLOCK) ON p2._IDRRef = p1._ParentIDRRef
            WHERE RTRIM(ac._Code) IN ('8100', '8200') OR RTRIM(p1._Code) IN ('8100', '8200') OR RTRIM(p2._Code) IN ('8100', '8200')
        ) adt ON adt._IDRRef = r._AccountDtRRef
        LEFT JOIN (
        SELECT ac._IDRRef, ac._Code AS Code
            FROM dbo._Acc6699 ac WITH (NOLOCK)
            LEFT JOIN dbo._Acc6699 p1 WITH (NOLOCK) ON p1._IDRRef = ac._ParentIDRRef
            LEFT JOIN dbo._Acc6699 p2 WITH (NOLOCK) ON p2._IDRRef = p1._ParentIDRRef
            WHERE RTRIM(ac._Code) IN ('8100', '8200') OR RTRIM(p1._Code) IN ('8100', '8200') OR RTRIM(p2._Code) IN ('8100', '8200')
        ) act ON act._IDRRef = r._AccountCtRRef
        CROSS APPLY (
            SELECT 0 AS Side,  1 AS Sign, r._AccountDtRRef AS AccRef, r._AccountCtRRef AS CorAccRef, r._Fld17936DtRRef AS DepLineRef, adt.Code AS AccCode
            UNION ALL                                                              -- кредит: сторно при перекласифікації
            SELECT 1, -1, r._AccountCtRRef, r._AccountDtRRef, r._Fld17936CtRRef, act.Code WHERE act._IDRRef IS NOT NULL
        ) sd
        OUTER APPLY (
            SELECT MAX(ed._Value_RRRef) AS ItemRef                                   -- субконто «Вид расходов» своєї сторони
            FROM dbo._AccRgED6757 ed WITH (NOLOCK)
            WHERE ed._RecorderTRef = r._RecorderTRef AND ed._RecorderRRef = r._RecorderRRef
              AND ed._LineNo = r._LineNo AND ed._Correspond = sd.Side AND ed._Value_RTRef = 0x0000133C
        ) sk
        LEFT JOIN dbo._Reference4924  vr       WITH (NOLOCK) ON vr._IDRRef       = sk.ItemRef      -- без фільтра _Marked, щоб не губити суми
        LEFT JOIN dbo._Document7811   spis     WITH (NOLOCK) ON spis._IDRRef     = r._RecorderRRef AND r._RecorderTRef = 0x00001E83
        LEFT JOIN dbo._Document7810   nakl     WITH (NOLOCK) ON nakl._IDRRef     = r._RecorderRRef AND r._RecorderTRef = 0x00001E82
        LEFT JOIN dbo._Document15307  zp_okl   WITH (NOLOCK) ON zp_okl._IDRRef   = r._RecorderRRef AND r._RecorderTRef = 0x00003BCB
        LEFT JOIN dbo._Document15308  zp_proc  WITH (NOLOCK) ON zp_proc._IDRRef  = r._RecorderRRef AND r._RecorderTRef = 0x00003BCC
        LEFT JOIN dbo._Enum15970      zp_vid   WITH (NOLOCK) ON zp_vid._IDRRef   = zp_okl._Fld15975RRef  -- ВидЗП документа нарахування ЗП
        LEFT JOIN dbo._Reference13165 hdr_dep  WITH (NOLOCK) ON hdr_dep._IDRRef  = CASE r._RecorderTRef  -- «Отдел» шапки документа-джерела
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
          AND r._Period >= '4025-01-01'                                                   -- з 2025 року
          AND NOT (r._RecorderTRef = 0x00003BCB AND COALESCE(zp_vid._EnumOrder, -1) = 1)   -- без премій керівникам
    ) u
    GROUP BY u.[Month], u.DepRef, u.ItemRef
) a
LEFT JOIN dbo._Reference13165 dep WITH (NOLOCK) ON dep._IDRRef = a.DepRef
LEFT JOIN dbo._Reference4924  e   WITH (NOLOCK) ON e._IDRRef   = a.ItemRef
LEFT JOIN dbo._Reference4924  grp WITH (NOLOCK) ON grp._IDRRef = e._ParentIDRRef
OPTION (MAXDOP 1)
