/* PayDesk · BudgetFactDetails — розшифровка факту бюджету: проводки 1С по рахунках 8100 / 8200 з документом-джерелом, USD.
   Та сама логіка, що й ФАКТ у BudgetPlanFact (і fact_actual куба FTP_Budgeting):
     ФАКТ — РегистрБухгалтерии.Хозрасчетный (_AccRg6732), рахунки 8100 / 8200 і їхні субрахунки: рядок факту з'являється
            одразу після проведення документа-джерела (документ «Розподіл накладних витрат» більше не використовується).
            +  дебет 8100/8200 (будь-який кор. рахунок)                           -> аналітика Дт;
            -  кредит 8100/8200, ЛИШЕ якщо дебет теж 8100/8200 (перекласифікація) -> аналітика Кт;
            кредит 8100/8200 на інші рахунки (закриття періоду тощо) не береться.
            СТАТТЯ — субконто проводки своєї сторони (_AccRgED6757, тип 0x0000133C = Справочник.ВидыРасходов).
            ВІДДІЛ — Owner статті (_Fld19036RRef), якщо заповнений; інакше відділ проводки (ОтделДт/Кт _Fld17936),
            а якщо він порожній — «Отдел» шапки документа-джерела; статті немає — нульовий відділ («Інше»).
            Без премій керівникам: НачислениеЗПпоОкладам з Вид (_Fld15975RRef, _Enum15970) = порядок 1.
   DepartmentGUID / ExpenseItemGUID / Month — ті самі, що в BudgetPlanFact. LineDepartmentGUID — відділ документа.
   SourceDocumentGUID — документ-реєстратор проводки: за ним PayDesk бачить, що платіж оплаченої заявки вже у факті.
   EmployeeGUID — лише для «Списання» (СписаниеДенСредств.Сотрудник = _Fld10416RRef), як у кубі.
   Швидкодія: з 2025 року; проводки відсікаються за рахунком дебету до розгортання на сторони; WITH (NOLOCK) — не
   блокує 1С і не стає жертвою deadlock; OPTION (MAXDOP 1) — без deadlock між потоками паралельного плану (якщо
   Power BI не прийме OPTION, рядок можна прибрати). Без CTE (WITH ... AS), щоб запит працював у Power BI як є. */
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
    RTRIM(sd.AccCode)                                                   AS Account,
    RTRIM(cor._Code)                                                    AS CorrAccount,
    CASE sd.Side WHEN 0 THEN N'Дт' ELSE N'Кт' END                       AS Side,
    r._Fld6738                                                          AS EntryText
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
LEFT JOIN dbo._Acc6699 cor WITH (NOLOCK) ON cor._IDRRef = sd.CorAccRef
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
    SELECT MAX(ed._Value_RRRef) AS ItemRef                                   -- субконто «Вид расходов» своєї сторони
    FROM dbo._AccRgED6757 ed WITH (NOLOCK)
    WHERE ed._RecorderTRef = r._RecorderTRef AND ed._RecorderRRef = r._RecorderRRef
      AND ed._LineNo = r._LineNo AND ed._Correspond = sd.Side AND ed._Value_RTRef = 0x0000133C
) sk
LEFT JOIN dbo._Reference4924  vr       WITH (NOLOCK) ON vr._IDRRef       = sk.ItemRef      -- без фільтра _Marked, щоб не губити суми
LEFT JOIN dbo._Document7811   spis     WITH (NOLOCK) ON spis._IDRRef     = r._RecorderRRef AND r._RecorderTRef = 0x00001E83
LEFT JOIN dbo._Document7810   nakl     WITH (NOLOCK) ON nakl._IDRRef     = r._RecorderRRef AND r._RecorderTRef = 0x00001E82
LEFT JOIN dbo._Document4935   prih     WITH (NOLOCK) ON prih._IDRRef     = r._RecorderRRef AND r._RecorderTRef = 0x00001347
LEFT JOIN dbo._Document6618   vnutr    WITH (NOLOCK) ON vnutr._IDRRef    = r._RecorderRRef AND r._RecorderTRef = 0x000019DA
LEFT JOIN dbo._Document13888  obmen    WITH (NOLOCK) ON obmen._IDRRef    = r._RecorderRRef AND r._RecorderTRef = 0x00003640
LEFT JOIN dbo._Document15307  zp_okl   WITH (NOLOCK) ON zp_okl._IDRRef   = r._RecorderRRef AND r._RecorderTRef = 0x00003BCB
LEFT JOIN dbo._Document15308  zp_proc  WITH (NOLOCK) ON zp_proc._IDRRef  = r._RecorderRRef AND r._RecorderTRef = 0x00003BCC
LEFT JOIN dbo._Document18759  kom_bank WITH (NOLOCK) ON kom_bank._IDRRef = r._RecorderRRef AND r._RecorderTRef = 0x00004947
LEFT JOIN dbo._Enum15970      zp_vid   WITH (NOLOCK) ON zp_vid._IDRRef   = zp_okl._Fld15975RRef  -- ВидЗП документа нарахування ЗП
LEFT JOIN dbo._Reference13165 hdr_dep  WITH (NOLOCK) ON hdr_dep._IDRRef  = CASE r._RecorderTRef  -- «Отдел» шапки документа-джерела
        WHEN 0x00001E83 THEN spis._Fld14095RRef     -- СписаниеДенСредств.Отдел
        WHEN 0x00001E82 THEN nakl._Fld13855RRef     -- НакладныеРасходы.Отдел
        WHEN 0x00003BCB THEN zp_okl._Fld15638RRef   -- НачислениеЗПпоОкладам.Отдел
        WHEN 0x00003BCC THEN zp_proc._Fld15639RRef  -- ПрочееНачислениеУдержаниеПоЗП.Отдел
    END
LEFT JOIN dbo._Reference59    p        WITH (NOLOCK) ON p._IDRRef        = CASE WHEN r._RecorderTRef = 0x00001E83 THEN spis._Fld10416RRef END
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
OPTION (MAXDOP 1)
