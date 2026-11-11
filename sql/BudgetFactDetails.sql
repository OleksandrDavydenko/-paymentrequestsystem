/* PayDesk · BudgetFactDetails — розшифровка факту бюджету: рядки розподілу накладних з документом-джерелом, USD.
   DepartmentGUID / ExpenseItemGUID / Month — ті самі, що в BudgetPlanFact (маршрут по Owner статті).
   EmployeeGUID — поки лише для «Списання» (СписаниеДенСредств.Сотрудник = _Fld10416RRef, підтверджено);
   для інших типів — нулі, доки не звірено поле (як у кубі). */
SELECT
    dt.D                                                                AS [Date],
    DATEFROMPARTS(YEAR(dt.D), MONTH(dt.D), 1)                           AS [Month],
    CONVERT(varchar(32), r.DepRef, 2)                                   AS DepartmentGUID,
    CONVERT(varchar(32), COALESCE(vt._Fld15234RRef, 0x00000000000000000000000000000000), 2) AS LineDepartmentGUID,
    r.AllocationStatus,
    CONVERT(varchar(32), COALESCE(vt._Fld15231RRef, 0x00000000000000000000000000000000), 2) AS ExpenseItemGUID,
    COALESCE(RTRIM(vr._Code), N'')                                      AS ExpenseItemCode,
    CAST(vt._Fld15232 AS numeric(28,2))                                 AS AmountUSD,
    src.DocumentType,
    CONVERT(varchar(32), vt._Fld15230_RRRef, 2)                         AS SourceDocumentGUID,
    src.DocumentNumber,
    CAST(DATEADD(year, -2000, src.DocDateTime) AS date)                 AS DocumentDate,
    src.Comment,
    CONVERT(varchar(32), COALESCE(src.EmpRef, 0x00000000000000000000000000000000), 2) AS EmployeeGUID,
    COALESCE(p._Description, N'')                                       AS EmployeeName,
    doc._Number                                                         AS DistributionNumber,
    CAST(vt._LineNo15229 AS int)                                        AS [LineNo]
FROM dbo._Document15134_VT15228 vt
INNER JOIN dbo._Document15134 doc ON doc._IDRRef = vt._Document15134_IDRRef
LEFT JOIN dbo._Reference4924  vr       ON vr._IDRRef       = vt._Fld15231RRef
LEFT JOIN dbo._Document7811   spis     ON spis._IDRRef     = vt._Fld15230_RRRef AND vt._Fld15230_RTRef = 0x00001E83
LEFT JOIN dbo._Document7810   nakl     ON nakl._IDRRef     = vt._Fld15230_RRRef AND vt._Fld15230_RTRef = 0x00001E82
LEFT JOIN dbo._Document4935   prih     ON prih._IDRRef     = vt._Fld15230_RRRef AND vt._Fld15230_RTRef = 0x00001347
LEFT JOIN dbo._Document6618   vnutr    ON vnutr._IDRRef    = vt._Fld15230_RRRef AND vt._Fld15230_RTRef = 0x000019DA
LEFT JOIN dbo._Document13888  obmen    ON obmen._IDRRef    = vt._Fld15230_RRRef AND vt._Fld15230_RTRef = 0x00003640
LEFT JOIN dbo._Document15307  zp_okl   ON zp_okl._IDRRef   = vt._Fld15230_RRRef AND vt._Fld15230_RTRef = 0x00003BCB
LEFT JOIN dbo._Document15308  zp_proc  ON zp_proc._IDRRef  = vt._Fld15230_RRRef AND vt._Fld15230_RTRef = 0x00003BCC
LEFT JOIN dbo._Document18759  kom_bank ON kom_bank._IDRRef = vt._Fld15230_RRRef AND vt._Fld15230_RTRef = 0x00004947
CROSS APPLY (SELECT CAST(DATEADD(year, -2000, doc._Date_Time) AS date) AS D) dt
CROSS APPLY (SELECT
    CASE
        WHEN vr._IDRRef IS NULL THEN 0x00000000000000000000000000000000
        WHEN vr._Fld19036RRef IS NOT NULL AND vr._Fld19036RRef <> 0x00000000000000000000000000000000
            THEN vr._Fld19036RRef
        ELSE COALESCE(vt._Fld15234RRef, 0x00000000000000000000000000000000)
    END AS DepRef,
    CASE
        WHEN vr._IDRRef IS NULL THEN N'Не розподілено: стаття не знайдена'
        WHEN vr._Fld19036RRef IS NOT NULL AND vr._Fld19036RRef <> 0x00000000000000000000000000000000
            THEN N'На Owner статті'
        ELSE N'Прямий (відділ рядка)'
    END AS AllocationStatus) r
CROSS APPLY (SELECT
    CASE vt._Fld15230_RTRef
        WHEN 0x00001E83 THEN N'Списання'           WHEN 0x00001E82 THEN N'Накладні витрати'
        WHEN 0x00001347 THEN N'Прихід'             WHEN 0x000019DA THEN N'Внутрішнє переміщення'
        WHEN 0x00003640 THEN N'Обмін'              WHEN 0x00003BCB THEN N'Нарахування ЗП по окладах'
        WHEN 0x00003BCC THEN N'Інше нарахування/утримання ЗП' WHEN 0x00004947 THEN N'Розподіл комісій банку'
        ELSE N'Інше'
    END AS DocumentType,
    CASE vt._Fld15230_RTRef
        WHEN 0x00001E83 THEN spis._Number    WHEN 0x00001E82 THEN nakl._Number
        WHEN 0x00001347 THEN prih._Number    WHEN 0x000019DA THEN vnutr._Number
        WHEN 0x00003640 THEN obmen._Number   WHEN 0x00003BCB THEN zp_okl._Number
        WHEN 0x00003BCC THEN zp_proc._Number WHEN 0x00004947 THEN kom_bank._Number
    END AS DocumentNumber,
    CASE vt._Fld15230_RTRef
        WHEN 0x00001E83 THEN spis._Date_Time    WHEN 0x00001E82 THEN nakl._Date_Time
        WHEN 0x00001347 THEN prih._Date_Time    WHEN 0x000019DA THEN vnutr._Date_Time
        WHEN 0x00003640 THEN obmen._Date_Time   WHEN 0x00003BCB THEN zp_okl._Date_Time
        WHEN 0x00003BCC THEN zp_proc._Date_Time WHEN 0x00004947 THEN kom_bank._Date_Time
    END AS DocDateTime,
    CASE vt._Fld15230_RTRef
        WHEN 0x00001E83 THEN spis._Fld7982    WHEN 0x00001E82 THEN nakl._Fld7938
        WHEN 0x00001347 THEN prih._Fld5110    WHEN 0x000019DA THEN vnutr._Fld6630
        WHEN 0x00003640 THEN obmen._Fld13903  WHEN 0x00003BCB THEN zp_okl._Fld15323
        WHEN 0x00003BCC THEN zp_proc._Fld15350 WHEN 0x00004947 THEN kom_bank._Fld18761
    END AS Comment,
    CASE vt._Fld15230_RTRef
        WHEN 0x00001E83 THEN spis._Fld10416RRef     -- СписаниеДенСредств.Сотрудник (підтверджено)
        -- інші типи: дописати поле, коли буде звірене (як у кубі)
    END AS EmpRef) src
LEFT JOIN dbo._Reference59 p ON p._IDRRef = src.EmpRef
WHERE doc._Marked = 0x00 AND doc._Posted = 0x01
  AND doc._Date_Time >= DATEADD(year, 2000, '2024-01-01')
