# פאנל S&P 500 — דוחות כספיים ומחירים, עם זמינות להשקעה לכל רבעון

קובץ ההרכב: `sp500hist.csv` (872 רשומות, 852 טיקרים, 2000Q1–2026Q2) · ריצה 2026-11-17 20:47:46 (16.8 שניות) · קובצי הקלט לא השתנו במהלך הריצה (SHA-256 לפני/אחרי): **True**

## 0. תקציר

- **121,263 שורות** — כל שורות הדוחות של **785 החברות** שרשומות הקובץ שויכו אליהן, לאורך כל ההיסטוריה שלהן. **85 עמודות**: 74 של הדוחות, 7 של המחירים, ו-4 של המדד — אותן עמודות כמו בפאנל QQQ100.
- **799 מתוך 872 רשומות שויכו לחברה**, כל רשומה לחברה אחת לכל היותר. 73 לא שויכו, כל אחת עם סיבה אחת (סעיף 2).
- ל-113,688 שורות (93.8%) יש שורת מחיר.
- **`in_index = 1` ב-48,014 שורות** — בממוצע 452.9 חברות לרבעון (359–498), 2000Q1–2026Q2.
- הפלט: `output/sp500_panel/sp500_panel.parquet` (כל השורות) ו-`output/sp500_panel/sp500_panel_from_2025.xlsx` (3,646 שורות מ-2025).
- בדיקה צולבת: הרבעונים שנמנו מהרשומות המשויכות בלבד, בלי הפאנל, זהים לשורות שסומנו 1 (עד 2026Q2): **True**.
- הפרדה מ-QQQ100: קוד (`src/sp500_panel.py`), סעיף בקונפיג (`sp500_panel`) ותיקיית פלט (`output/sp500_panel`) משלו. הריצה כותבת רק לתיקייה הזאת.

## 1. הכלל

```
quarter        = הרבעון מתוך ה-KEY                     2024Q2_AAL → 2024Q2
in_index = 1   אם לאותו gvkey יש רשומה משויכת שבה    start_quarter ≤ quarter < end_quarter
               (end_quarter ריק = עדיין במדד)
in_index = 0   אחרת
formation_date = סוף החודש, חודשיים אחרי סוף הרבעון    2024Q2 → 2024-08-31  (= Date של המחירים)
```

- דוגמה: `AAL 2015Q1–2024Q3` → השורה `2024Q2_AAL` (סוף רבעון 2024-06-30, השקעה ב-2024-08-31) מקבלת 1, והשורה `2024Q3_AAL` מקבלת 0. הכלל נקבע ב-13/09 לקובץ הזה.
- הרבעון נקרא מה-KEY ונבדק מול `fiscal_period_end_date` לפי כלל הפאנל הבסיסי; אי-התאמה הייתה עוצרת את הריצה.
- ⚠️ הקובץ ברבעונים ולא בימים: הרבעון האחרון לפני היציאה מקבל 1 גם כשהחברה יצאה לפני יום ההשקעה. חברה שיצאה ביולי 2024 (end_quarter 2024Q3) מקבלת 1 ב-2024Q2, אף שב-2024-08-31 כבר לא הייתה במדד.
- ⚠️ הקובץ מתחיל ב-2000Q1, ולכן כל 51,140 השורות שלפניו מקבלות 0.
- ⚠️ 413 רשומות מתחילות ב-2000Q1, תחילת הקובץ. כשהדוחות של חברה מתחילים מאוחר יותר, הרבעונים האלה מקבלים 1 כמו שהקובץ אומר. 663 שורות עם 1 הן בלי שורת מחיר — העמודה `in_index_with_price_row` בסעיף 4.
- ⚠️ 2 שורות מרבעון אחרי 2026Q2 עם 1: `end_quarter` ריק נקרא "עדיין במדד".

## 2. השיוך: מרשומת טיקר לחברה

1. **מועמדות** — חברות בפאנל שהטיקר שלהן הוא בדיוק הטיקר בקובץ, או אותו טיקר עם הסיומת `.<מספר>` של Compustat (חברה ותיקה שהטיקר שלה עבר לחברה חדשה: `AA.3` = Alcoa Inc), ושיש להן לפחות שורת דוח אחת בטווח הרשומה. `AAX` או `AA.B` אינם `AA`.
2. **בדיקת בורסה** — נשארות רק מועמדות עם `exchg` ב-[11, 14] (11 = NYSE, 14 = NASDAQ). ⚠️ `exchg` של Compustat הוא הבורסה של היום (או האחרונה), לא של אז.
3. **הכרעה** — הרשומה הולכת למועמדת עם הכי הרבה רבעוני דוח בטווח. בתיקו היא לא משויכת ונרשמת. רשומה לא הולכת לשתי חברות.

- ההכרעה קוראת את שורות הדוח של כל טווח הרשומה, כדי לדעת לאיזו חברה הרשומה מתכוונת. היא לא קוראת מחיר או תשואה.

| decided_by | records | meaning |
|---|---|---|
| ONLY_CANDIDATE | 761 | מועמדת אחת, והיא עברה את בדיקת הבורסה |
| EXCHANGE_CHECK | 4 | כמה מועמדות, ורק אחת עברה את בדיקת הבורסה |
| MOST_QUARTERS | 34 | כמה עברו את בדיקת הבורסה; הכריע מספר רבעוני הדוח בטווח |

- 48 רשומות שויכו לחברה ותיקה עם סיומת, כי לטיקר המדויק לא היה דוח בטווח (למשל ABS → `ABS.1` Albertson's).
- 10 רשומות שויכו לחברה ותיקה **במקום** המחזיקה הנוכחית של הטיקר, שגם לה היו דוחות בטווח:

| ticker | start_quarter | end_quarter | chosen | company | decided_by | all_candidates |
|---|---|---|---|---|---|---|
| AA | 2000Q1 | 2016Q4 | AA.3 | ALCOA INC | MOST_QUARTERS | AA.3 ALCOA INC (exchg 11, 67q); AA ALCOA CORP (exchg 11, 11q) |
| ADT | 2012Q4 | 2016Q2 | ADT.4 | ADT CORP | MOST_QUARTERS | ADT.4 ADT CORP (exchg 11, 14q); ADT ADT INC (exchg 11, 5q) |
| ALTR | 2000Q1 | 2015Q4 | ALTR.1 | ALTERA CORP | MOST_QUARTERS | ALTR.1 ALTERA CORP (exchg 14, 63q); ALTR ALTAIR ENGINEERING INC (exchg 14, 3q) |
| EP | 2000Q1 | 2012Q2 | EP.1 | EL PASO CORP | EXCHANGE_CHECK | EP.1 EL PASO CORP (exchg 11, 49q); EP EMPIRE PETROLEUM CORP (exchg 12, 9q) |
| GENZ | 2000Q1 | 2011Q2 | GENZ.1 | GENZYME CORP | MOST_QUARTERS | GENZ.1 GENZYME CORP (exchg 14, 44q); GENZ VANECK DIGITAL NTV ECNMY ETF (exchg 14, 13q) |
| GLK | 2000Q1 | 2005Q3 | GLK.1 | GREAT LAKES CHEMICAL CORP | MOST_QUARTERS | GLK.1 GREAT LAKES CHEMICAL CORP (exchg 11, 21q); GLK GREAT LAKES BANCORP INC (exchg 11, 6q) |
| KG | 2000Q1 | 2010Q4 | KG.1 | KING PHARMACEUTICALS INC | MOST_QUARTERS | KG.1 KING PHARMACEUTICALS INC (exchg 11, 43q); KG KESTREL GROUP LTD (exchg 14, 15q) |
| NSM | 2000Q1 | 2011Q3 | NSM.2 | NATIONAL SEMICONDUCTOR CORP | MOST_QUARTERS | NSM.2 NATIONAL SEMICONDUCTOR CORP (exchg 11, 45q); NSM NATIONSTAR MORTGAGE HOLDINGS (exchg 11, 10q) |
| SE | 2000Q1 | 2017Q1 | SE.7 | SPECTRA ENERGY CORP | MOST_QUARTERS | SE.7 SPECTRA ENERGY CORP (exchg 11, 44q); SE.3 7-ELEVEN INC (exchg 11, 23q); SE SEA LIMITED (exchg 11, 8q) |
| SUN | 2000Q1 | 2012Q4 | SUN.1 | SUNOCO INC | EXCHANGE_CHECK | SUN.1 SUNOCO INC (exchg 11, 50q); SUN SUNOCO LP (exchg 24, 11q) |

### רשומות שלא שויכו


| reason | records | meaning |
|---|---|---|
| TICKER_NOT_IN_PANEL | 44 | אין בפאנל חברה עם הטיקר, גם לא עם סיומת. בדרך כלל הטיקר הוחלף (ACE→CB, DISCA→WBD), או שזו סדרת מניות שנייה (GOOG, FOX, NWS, UA) |
| NO_REPORT_ROW_IN_RANGE | 20 | יש בפאנל חברה עם הטיקר, אבל בלי אף שורת דוח בטווח הרשומה |
| NOT_NYSE_NASDAQ | 3 | כל המועמדות נפסלו בבדיקת הבורסה (הבורסה של היום) |
| TIE | 3 | כמה מועמדות עברו את בדיקת הבורסה עם אותו מספר רבעונים מקסימלי |
| ZERO_LENGTH | 3 | start_quarter = end_quarter: הרשומה לא מכסה אף רבעון |


| ticker | start_quarter | end_quarter | reason | candidates | ticker_holders_in_panel |
|---|---|---|---|---|---|
| ACE | 2000Q1 | 2016Q1 | TICKER_NOT_IN_PANEL |  |  |
| BBBY | 2000Q1 | 2017Q3 | TICKER_NOT_IN_PANEL |  |  |
| BIG | 2000Q1 | 2013Q1 | TICKER_NOT_IN_PANEL |  |  |
| BS | 2000Q1 | 2000Q4 | TICKER_NOT_IN_PANEL |  |  |
| CCE | 2000Q1 | 2016Q2 | TICKER_NOT_IN_PANEL |  |  |
| CDAY | 2021Q3 | 2024Q1 | TICKER_NOT_IN_PANEL |  |  |
| CMCSK | 2015Q3 | 2015Q4 | TICKER_NOT_IN_PANEL |  |  |
| CSC | 2000Q1 | 2015Q4 | TICKER_NOT_IN_PANEL |  |  |
| CVC | 2010Q4 | 2016Q2 | TICKER_NOT_IN_PANEL |  |  |
| DISCA | 2000Q1 | 2022Q2 | TICKER_NOT_IN_PANEL |  |  |
| DISCK | 2014Q3 | 2022Q2 | TICKER_NOT_IN_PANEL |  |  |
| DNR | 2000Q1 | 2015Q1 | TICKER_NOT_IN_PANEL |  |  |
| DWDP | 2017Q3 | 2019Q2 | TICKER_NOT_IN_PANEL |  |  |
| EK | 2000Q1 | 2010Q4 | TICKER_NOT_IN_PANEL |  |  |
| ENDP | 2015Q1 | 2017Q1 | TICKER_NOT_IN_PANEL |  |  |
| ESV | 2012Q3 | 2016Q1 | TICKER_NOT_IN_PANEL |  |  |
| FBHS | 2016Q2 | 2022Q4 | TICKER_NOT_IN_PANEL |  |  |
| FNM | 2000Q1 | 2008Q3 | TICKER_NOT_IN_PANEL |  |  |
| FRE | 2000Q1 | 2008Q3 | TICKER_NOT_IN_PANEL |  |  |
| FTR | 2000Q1 | 2017Q1 | TICKER_NOT_IN_PANEL |  |  |
| GGP | 2013Q4 | 2018Q3 | TICKER_NOT_IN_PANEL |  |  |
| GMCR | 2014Q1 | 2016Q1 | TICKER_NOT_IN_PANEL |  |  |
| GOOG | 2014Q2 |  | TICKER_NOT_IN_PANEL |  |  |
| GPS | 2000Q1 | 2022Q1 | TICKER_NOT_IN_PANEL |  |  |
| HFC | 2018Q2 | 2021Q2 | TICKER_NOT_IN_PANEL |  |  |
| HNZ | 2000Q1 | 2013Q2 | TICKER_NOT_IN_PANEL |  |  |
| JCP | 2000Q1 | 2013Q4 | TICKER_NOT_IN_PANEL |  |  |
| JDSU | 2000Q3 | 2013Q4 | TICKER_NOT_IN_PANEL |  |  |
| KFT | 2007Q1 | 2012Q4 | TICKER_NOT_IN_PANEL |  |  |
| LEH | 2000Q1 | 2008Q3 | TICKER_NOT_IN_PANEL |  |  |
| NCR | 2000Q1 | 2007Q4 | TICKER_NOT_IN_PANEL |  |  |
| NWS | 2015Q3 |  | TICKER_NOT_IN_PANEL |  |  |
| PSKY | 2000Q1 |  | TICKER_NOT_IN_PANEL |  |  |
| QTRN | 2000Q1 | 2003Q3 | TICKER_NOT_IN_PANEL |  |  |
| RAD | 2000Q1 | 2000Q3 | TICKER_NOT_IN_PANEL |  |  |
| RE | 2017Q2 | 2023Q3 | TICKER_NOT_IN_PANEL |  |  |
| RSH | 2000Q1 | 2011Q2 | TICKER_NOT_IN_PANEL |  |  |
| SIVB | 2018Q1 | 2023Q1 | TICKER_NOT_IN_PANEL |  |  |
| TRB | 2000Q1 | 2007Q4 | TICKER_NOT_IN_PANEL |  |  |
| UA | 2014Q2 | 2022Q2 | TICKER_NOT_IN_PANEL |  |  |
| WFR | 2000Q1 | 2011Q4 | TICKER_NOT_IN_PANEL |  |  |
| WIN | 2000Q1 | 2015Q2 | TICKER_NOT_IN_PANEL |  |  |
| WLTW | 2016Q1 | 2022Q1 | TICKER_NOT_IN_PANEL |  |  |
| YHOO | 2000Q1 | 2017Q2 | TICKER_NOT_IN_PANEL |  |  |
| ABK | 2000Q4 | 2008Q2 | NO_REPORT_ROW_IN_RANGE |  | ABK.1 ALLIANCE BANCORPORATION (1987Q1-1989Q1) |
| ADS | 2013Q4 | 2020Q2 | NO_REPORT_ROW_IN_RANGE |  | ADS.2 ALLIED MILLS INC (1965Q4-1973Q3); ADS.3 APPLIED DIGITAL DATA SYSTEMS (1971Q4-1980Q2) |
| ANR | 2011Q2 | 2012Q4 | NO_REPORT_ROW_IN_RANGE |  | ANR.2 AMERICAN NATURAL RESOURCES (1965Q1-1984Q4) |
| AV | 2000Q1 | 2007Q4 | NO_REPORT_ROW_IN_RANGE |  | AV CORGI AEROSPACE & COM AV ETF (2026Q2-2026Q3) |
| CHK | 2000Q1 | 2018Q1 | NO_REPORT_ROW_IN_RANGE |  | CHK.2 C & K PETROLEUM INC (1971Q1-1980Q2) |
| DV | 2000Q1 | 2012Q4 | NO_REPORT_ROW_IN_RANGE |  | DV DOUBLEVERIFY HOLD INC (2019Q1-2026Q2) |
| FDXF | 2026Q2 |  | NO_REPORT_ROW_IN_RANGE |  | FDXF FEDEX FRGHT HLDNG CMPNY INC (2023Q2-2026Q1) |
| FII | 2000Q1 | 2013Q1 | NO_REPORT_ROW_IN_RANGE |  | FII.1 FRIER INDUSTRIES INC (1967Q3-1976Q1) |
| FLT | 2018Q2 | 2024Q1 | NO_REPORT_ROW_IN_RANGE |  | FLT.1 FILTROL CORP (1966Q2-1978Q1) |
| FOX | 2019Q1 |  | NO_REPORT_ROW_IN_RANGE |  | FOX.1 FOXBORO CO (1962Q1-1990Q2); FOX.3 FOX ENTERTAINMENT GROUP INC (1996Q3-2004Q4) |
| FRC | 2019Q1 | 2023Q2 | NO_REPORT_ROW_IN_RANGE |  | FRC.3 NETS CAC40 INDEX FUND - FRNC (2008Q2-2008Q4) |
| MNK | 2014Q3 | 2017Q3 | NO_REPORT_ROW_IN_RANGE |  | MNK.1 MUNIHOLDINGS N Y INSD FD III (1999Q1-2000Q1) |
| PGN | 2000Q1 | 2012Q3 | NO_REPORT_ROW_IN_RANGE |  | PGN.3 PORTLAND GENERAL CORP (1965Q1-1997Q1) |
| Q | 2017Q3 | 2017Q4 | NO_REPORT_ROW_IN_RANGE |  | Q.2 QWEST COMMUNICATION INTL INC (1984Q1-2010Q4); Q.1 QWEST COMMUNICATION INTL-OLD (1996Q1-2000Q1); Q QNITY ELECTRONICS INC (2023Q1-2026Q2) |
| RX | 2000Q1 | 2010Q1 | NO_REPORT_ROW_IN_RANGE |  | RX.2 RESISTOFLEX CORP (1965Q4-1978Q1) |
| SAI | 2009Q4 | 2013Q3 | NO_REPORT_ROW_IN_RANGE |  | SAI.1 SUNAMERICA INC (1966Q1-1998Q3); SAI.2 SEASON-ALL INDUSTRIES INC (1966Q2-1978Q4) |
| SHLD | 2000Q1 | 2012Q3 | NO_REPORT_ROW_IN_RANGE |  | SHLD.1 VICTORYSHARES PR AMERICA ETF (2020Q4-2021Q3); SHLD GLOBAL X DEFENSE TECH ETF (2023Q3-2026Q3) |
| SLE | 2000Q1 | 2012Q2 | NO_REPORT_ROW_IN_RANGE |  | SLE SUPER LEAGUE ENTERPRISE INC (2016Q1-2026Q2) |
| TEG | 2000Q1 | 2015Q3 | NO_REPORT_ROW_IN_RANGE |  | TEG.1 ENERGY GROUP PLC (1995Q4-1997Q4) |
| WYN | 2000Q1 | 2018Q2 | NO_REPORT_ROW_IN_RANGE |  | WYN.1 WYNDHAM HOTEL CORP (1995Q1-1997Q3) |
| CBOE | 2017Q1 |  | NOT_NYSE_NASDAQ | CBOE CBOE GLOBAL MARKETS INC (exchg 21, 38q) | CBOE CBOE GLOBAL MARKETS INC (2009Q1-2026Q2) |
| SBNY | 2021Q4 | 2023Q1 | NOT_NYSE_NASDAQ | SBNY SIGNATURE BANK/NY (exchg 19, 5q) | SBNY SIGNATURE BANK/NY (2004Q1-2022Q4) |
| XEC | 2014Q2 | 2020Q1 | NOT_NYSE_NASDAQ | XEC CIMAREX ENERGY CO (exchg 17, 23q) | XEC CIMAREX ENERGY CO (1999Q4-2021Q2) |
| DD | 2000Q1 | 2017Q3 | TIE | DD DUPONT DE NEMOURS INC (exchg 11, 70q); DD.2 DU PONT (E I) DE NEMOURS (exchg 11, 70q) | DD DUPONT DE NEMOURS INC (1962Q1-2026Q2); DD.2 DU PONT (E I) DE NEMOURS (1962Q1-2017Q2) |
| LSI | 2000Q1 | 2014Q2 | TIE | LSI LIFE STORAGE INC (exchg 11, 57q); LSI.1 LSI CORP (exchg 14, 57q) | LSI.1 LSI CORP (1982Q1-2014Q1); LSI LIFE STORAGE INC (1994Q1-2023Q1) |
| MMI | 2011Q1 | 2012Q2 | TIE | MMI MARCUS & MILLICHAP INC (exchg 11, 5q); MMI.3 MOTOROLA MOBILITY HLDGS INC (exchg 11, 5q) | MMI.1 MAMMOTH MART INC (1971Q1-1977Q1); MMI.2 MMI COMPANIES INC (1992Q1-1999Q4); MMI.3 MOTOROLA MOBILITY HLDGS INC (2008Q1-2012Q1); MMI MARCUS & MILLICHAP INC (2011Q1-2026Q2) |
| BMS | 2019Q2 | 2019Q2 | ZERO_LENGTH |  | BMS BEMIS CO INC (1966Q2-2019Q1) |
| MBC | 2022Q4 | 2022Q4 | ZERO_LENGTH |  | MBC.2 MICKELBERRY COMMUNICATIONS (1971Q1-1995Q2); MBC MASTERBRAND INC (2020Q1-2026Q2) |
| SOLS | 2025Q4 | 2025Q4 | ZERO_LENGTH |  | SOLS.1 SOLLENSYS CORP -OLD (2012Q2-2020Q3); SOLS SOLSTICE ADVANCED MTRILS INC (2023Q1-2026Q2) |

## 3. החיבור למחירים

- `left join` מהדוחות למחירים לפי `(gvkey, KEY)`, אחד-לאחד. שורה בלי מחיר שומרת את הדוחות שלה ומקבלת עמודות מחיר ריקות. הערכים מועתקים כפי שהם — המומנטום, ההפרשים והממוצעים חושבו על ההיסטוריה המלאה לפני הבחירה.
- שורות בלי KEY: 0.
- חברות בלי אף מחיר (פאנל המחירים לא בחר להן נייר):

| gvkey | symbol | company | rows | rows_in_index |
|---|---|---|---|---|
| 077301 | CTVA | CORTEVA INC  -SPN | 4 | 4 |

## 4. זמינות לפי רבעון

- כל הרבעונים ב-`tables/in_index_by_quarter.csv`. אלה נקודת הפתיחה של שלב 2: מתוך `in_index = 1`, כמה עם מחיר, עם תשואה ועם ROIC.

| period_key | rows | in_index | in_index_with_price_row | in_index_with_close | in_index_with_return | in_index_with_roic | investable_without_report_row |
|---|---|---|---|---|---|---|---|
| 2000Q1 | 623 | 359 | 345 | 345 | 343 | 289 | 17 |
| 2000Q2 | 621 | 360 | 347 | 347 | 347 | 294 | 17 |
| 2000Q3 | 619 | 359 | 348 | 348 | 348 | 297 | 19 |
| 2000Q4 | 617 | 361 | 350 | 350 | 350 | 299 | 21 |
| 2024Q3 | 622 | 498 | 498 | 498 | 496 | 417 | 1 |
| 2024Q4 | 619 | 497 | 497 | 497 | 497 | 415 | 1 |
| 2025Q1 | 618 | 498 | 497 | 497 | 493 | 416 | 0 |
| 2025Q2 | 612 | 494 | 493 | 493 | 493 | 413 | 4 |
| 2025Q3 | 610 | 498 | 497 | 497 | 495 | 414 | 0 |
| 2025Q4 | 605 | 497 | 496 | 496 | 495 | 415 | 1 |
| 2026Q1 | 600 | 496 | 496 | 496 | 495 | 415 | 2 |
| 2026Q2 | 599 | 496 | 496 | 496 | 0 | 414 | 1 |

## 5. מה לא נכלל

- חברות שלא שויכו להן רשומות לא נמצאות בפאנל כלל (סעיף 2). כאן — מה שחסר אצל החברות המשויכות:

| reason | lines |
|---|---|
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 582 |
| FIRM_WITHOUT_ANY_PRICE | 1 |


| reason | gvkey | company | period_key | symbol | rows_in_panel | rows_in_index |
|---|---|---|---|---|---|---|
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 001722 | ARCHER-DANIELS-MIDLAND CO | 2012Q3 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 001722 | ARCHER-DANIELS-MIDLAND CO | 2012Q4 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 010466 | ANDEAVOR | 2018Q3 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 063080 | ANSYS INC | 2025Q2 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 035978 | ARCONIC CORP | 2016Q4 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 180405 | ACTIVISION BLIZZARD INC | 2023Q3 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 002044 | BARD (C.R.) INC | 2017Q4 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 001408 | BEAM INC | 2014Q1 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 022794 | BJ SERVICES CO | 2010Q1 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 066708 | BROADCOM CORP | 2015Q4 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 060894 | CAMERON INTERNATIONAL CORP | 2016Q1 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 023945 | CEPHALON INC | 2011Q3 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 003555 | COUNTRYWIDE FINANCIAL CORP | 2008Q2 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2000Q1 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2000Q2 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2000Q3 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2000Q4 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2001Q1 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2001Q2 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2001Q3 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2001Q4 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2002Q1 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2002Q2 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2002Q3 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2002Q4 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2003Q1 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2003Q2 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2003Q3 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2003Q4 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 183257 | CAREFUSION CORP | 2004Q1 |  |  |  |

_... 553 שורות נוספות בקובץ המלא_

## 6. מסוף הרבעון הפיסקלי ועד יום ההשקעה (רק `in_index = 1`)

- כמו ב-QQQ100: מסמנים עכשיו ומחליטים בשלב 2. 2 = רבעון קלנדרי רגיל; 1 ו-0 = רבעון פיסקלי שנגמר חודש לפני יום ההשקעה או באותו חודש — הדוח כנראה עוד לא פורסם.

| period_year | months | rows | share_of_year | examples_latest_year |
|---|---|---|---|---|
| all | 0 | 2582 | 0.05378 | ACN, ADBE, AZO, CAG, CCL, COST, CTAS, DRI, FDS, FDX, GIS, JBL |
| all | 1 | 4002 | 0.08335 | A, ADI, ADSK, AMAT, AVGO, BBY, BF.B, CASY, CIEN, COO, CPB, CPRT |
| all | 2 | 41430 | 0.8629 | AAPL, ABBV, ABNB, ABT, ACGL, ADM, ADP, AEE, AEP, AES, AFL, AIG |

## 7. שחזור


| file | path | exists | bytes | modified | sha256 | unchanged_during_run |
|---|---|---|---|---|---|---|
| input_parquet | C:\Users\User\roic_research\data\data.parquet | True | 77905989 | 2026-10-10T17:16:54.939969540 | 5efd0835bf73a057d8431d1e9ff33408ff747ebacd07ace4f3f9662ce15c0974 | True |
| panel_parquet | C:\Users\User\roic_research\output\panel.parquet | True | 342853093 | 2026-10-27T15:43:05.354883194 | 73f1dda0e3ee4985f90cd924d7142e63e4e464759287b37768fce669f2c5f3a8 | True |
| prices_parquet | C:\Users\User\roic_research\output\prices.parquet | True | 34353349 | 2026-10-27T15:45:09.557599783 | 9ebc7f2aa27be0c0d8332dd1ff0fb3ca0e62dec1814475b21a6303e2a0092ee0 | True |
| sp500_records | C:\Users\User\roic_research\data\sp500hist.csv | True | 13737 | 2026-07-27T17:02:56.100515604 | 6c179c5a653e38a43be2fce40a7017be908e386258f40022b84b7bbdf0c361dd | True |

- `output/sp500_panel/logs/run_manifest.json` — גרסאות, ספירות וה-hashes.
