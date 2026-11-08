# פאנל המדד `qqq100` — דוחות כספיים ומחירים, עם זמינות להשקעה לכל רבעון

קובץ ההרכב: `qqq100hist.csv` (524 רשומות, 1995-01-01–2026-10-09) · ריצה 2026-11-07 20:14:56 (9.0 שניות) · קובצי הקלט לא השתנו במהלך הריצה (SHA-256 לפני/אחרי): **True**

## 0. תקציר

- **46,152 שורות** — כל שורות הדוחות של **436 החברות** שהיו אי פעם במדד, לאורך כל ההיסטוריה שלהן. **85 עמודות**: 74 של הדוחות, 7 של המחירים, ו-4 של המדד.
- ל-40,485 שורות (87.7%) יש שורת מחיר.
- **`in_index = 1` ב-12,489 שורות** — בממוצע 99.1 חברות לרבעון (88–102), 1995Q1–2026Q2.
- הפלט: `output/index_panels/qqq100/qqq100_panel.parquet` (כל השורות) ו-`output/index_panels/qqq100/qqq100_panel_from_2025.xlsx` (1,340 שורות מ-2025).
- בדיקה צולבת: הרבעונים שנמנו מהטווחים בלבד, בלי הפאנל, זהים לשורות שסומנו 1 (עד 2026Q2): **True**.

## 1. הכלל

רבעון זמין להשקעה כאשר **שני** התאריכים נמצאים **באותו טווח** `from`–`thru` של אותה חברה (`thru` ריק = עדיין במדד):

```
quarter_end    = סוף הרבעון מתוך ה-KEY          2025Q1_AAPL → 2025-03-31
formation_date = סוף החודש, חודשיים אחריו        → 2025-05-31  (= Date של המחירים)
in_index = 1   אם יש טווח של אותו gvkey שבו  from ≤ quarter_end  וגם  formation_date ≤ thru
in_index = 0   אחרת
```

- הרבעון נקרא מה-KEY ונבדק מול `fiscal_period_end_date` לפי כלל הפאנל הבסיסי; אי-התאמה הייתה עוצרת את הריצה.
- `thru` עצמו — היום שבו החברה **תצא** מהמדד — לא נכנס לפאנל.
- ⚠️ הקובץ מתחיל ב-1995-01-01, ולכן כל שורות 1994Q4 (272) מקבלות 0: סוף הרבעון 1994-12-31 לא נמצא באף טווח.
- ⚠️ שורה אחת מרבעון אחרי 2026Q2 עם 1: יום ההשקעה אחרי התאריך האחרון בקובץ (2026-10-09), ו-`thru` ריק נקרא "עדיין במדד".

## 2. יציאה וחזרה — רשומות חוזרות

- רשומות לחברה: רשומה אחת: 361 חברות · 2 רשומות: 63 חברות · 3 רשומות: 11 חברות · 4 רשומות: חברה אחת.
- יציאה וחזרה אמיתיות נשארות טווחים נפרדים. רשומות של אותה חברה שחופפות, או שהבאה מתחילה עד 1 יום אחרי סוף הקודמת, הן **שהייה רציפה אחת** ומאוחדות: 524 רשומות → 514 טווחים.
- `OVERLAPS_PREVIOUS` — שתי סדרות מניות של אותה חברה (GOOG/GOOGL) או החלפת טיקר באותו יום. `STARTS_RIGHT_AFTER_PREVIOUS` — הרשומה הבאה מתחילה יום אחרי. בלי האיחוד, `2002Q3_CMCSA` (סוף הרבעון ברשומה אחת, יום ההשקעה בבאה) היה מקבל 0 למרות רצף.

| gvkey | company | from | thru | relation | days_after_previous_thru |
|---|---|---|---|---|---|
| 003226 | COMCAST CORP | 1995-01-01 | 2002-11-18 | FIRST_RECORD_OF_STAY |  |
| 003226 | COMCAST CORP | 2002-11-19 | ריק (עדיין במדד) | STARTS_RIGHT_AFTER_PREVIOUS | 1 |
| 003226 | COMCAST CORP | 2014-12-22 | 2015-12-11 | OVERLAPS_PREVIOUS |  |
| 012886 | TWENTY-FIRST CENTURY FOX INC | 2009-01-21 | 2019-03-19 | FIRST_RECORD_OF_STAY |  |
| 012886 | TWENTY-FIRST CENTURY FOX INC | 2014-12-22 | 2019-03-19 | OVERLAPS_PREVIOUS |  |
| 017010 | LIBERTY MEDIA SIRIUSXM GROUP | 2013-06-05 | 2016-04-17 | FIRST_RECORD_OF_STAY |  |
| 017010 | LIBERTY MEDIA SIRIUSXM GROUP | 2014-07-30 | 2016-04-17 | OVERLAPS_PREVIOUS |  |
| 024724 | LIBERTY LATIN AMERICA LTD | 2015-07-02 | 2015-12-20 | FIRST_RECORD_OF_STAY |  |
| 024724 | LIBERTY LATIN AMERICA LTD | 2015-07-02 | 2015-12-20 | OVERLAPS_PREVIOUS |  |
| 024724 | LIBERTY LATIN AMERICA LTD | 2016-12-19 | 2018-01-02 | FIRST_RECORD_OF_STAY |  |
| 024724 | LIBERTY LATIN AMERICA LTD | 2016-12-19 | 2018-01-02 | OVERLAPS_PREVIOUS |  |
| 027186 | ATLANTA BRAVES HOLDINGS INC | 2016-04-18 | 2016-06-21 | FIRST_RECORD_OF_STAY |  |
| 027186 | ATLANTA BRAVES HOLDINGS INC | 2016-04-18 | 2016-06-21 | OVERLAPS_PREVIOUS |  |
| 027187 | LIBERTY MEDIA FORMULA ONE | 2016-04-18 | 2016-06-21 | FIRST_RECORD_OF_STAY |  |
| 027187 | LIBERTY MEDIA FORMULA ONE | 2016-04-18 | 2016-06-21 | OVERLAPS_PREVIOUS |  |
| 034636 | FOX CORP | 2020-03-19 | 2021-12-20 | FIRST_RECORD_OF_STAY |  |
| 034636 | FOX CORP | 2020-03-19 | 2021-12-20 | OVERLAPS_PREVIOUS |  |
| 160329 | ALPHABET INC | 2005-12-21 | ריק (עדיין במדד) | FIRST_RECORD_OF_STAY |  |
| 160329 | ALPHABET INC | 2014-04-03 | ריק (עדיין במדד) | OVERLAPS_PREVIOUS |  |

## 3. החיבור למחירים

- `left join` מהדוחות למחירים לפי `(gvkey, KEY)`, אחד-לאחד. שורה בלי מחיר שומרת את הדוחות שלה ומקבלת עמודות מחיר ריקות. הערכים מועתקים כפי שהם — המומנטום, ההפרשים והממוצעים חושבו על ההיסטוריה המלאה לפני הבחירה.
- שורות בלי KEY: 0.
- חברות בלי אף מחיר — הטיקר שלהן בפונדמנטלס הוא קוד-ממלא של Compustat ולכן פאנל המחירים לא בחר להן נייר:

| gvkey | symbol | company | rows | rows_in_index |
|---|---|---|---|---|
| 002230 | 5938B | BIOMET INC | 135 | 49 |
| 004828 | DEG1 | DELHAIZE AMERICA INC | 104 | 18 |
| 012144 | 0139A | SUNGARD DATA SYSTEMS INC | 123 | 2 |
| 013506 | 0189A | QVC INC | 162 | 0 |
| 030095 | 0573B | QUINTILES TRANSNATIONAL CORP | 51 | 16 |

## 4. זמינות לפי רבעון

- כל הרבעונים ב-`tables/in_index_by_quarter.csv`. אלה נקודת הפתיחה של שלב 2: מתוך `in_index = 1`, כמה עם מחיר, עם תשואה ועם ROIC.

| period_key | rows | in_index | in_index_with_price_row | in_index_with_close | in_index_with_return | in_index_with_roic | investable_without_report_row |
|---|---|---|---|---|---|---|---|
| 1995Q1 | 297 | 99 | 94 | 94 | 93 | 95 | 1 |
| 1995Q2 | 299 | 96 | 91 | 91 | 89 | 94 | 1 |
| 1995Q3 | 294 | 94 | 88 | 88 | 88 | 91 | 2 |
| 1995Q4 | 296 | 98 | 92 | 92 | 92 | 96 | 1 |
| 2024Q3 | 230 | 99 | 98 | 98 | 98 | 97 | 0 |
| 2024Q4 | 228 | 100 | 99 | 99 | 99 | 98 | 0 |
| 2025Q1 | 226 | 99 | 98 | 98 | 97 | 97 | 0 |
| 2025Q2 | 223 | 99 | 98 | 98 | 98 | 97 | 0 |
| 2025Q3 | 223 | 100 | 99 | 99 | 99 | 97 | 0 |
| 2025Q4 | 223 | 99 | 99 | 99 | 99 | 96 | 0 |
| 2026Q1 | 222 | 98 | 98 | 98 | 98 | 95 | 0 |
| 2026Q2 | 222 | 100 | 100 | 100 | 0 | 97 | 0 |

## 5. מה לא נכלל


| reason | gvkey | company | period_key | symbol | rows_in_panel | rows_in_index |
|---|---|---|---|---|---|---|
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 001055 | AST RESEARCH INC | 1995Q3 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 010393 | TELE-COMM TCI GROUP  -SER A | 1998Q4 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 012126 | PACIFICARE HEALTH SYSTEMS | 1996Q4 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 012233 | GENZYME CORP | 1995Q1 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 012233 | GENZYME CORP | 1995Q2 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 012233 | GENZYME CORP | 1995Q3 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 012233 | GENZYME CORP | 1995Q4 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 012484 | ADELPHIA COMMUN  -CL A | 2002Q1 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 029322 | ATI TECHNOLOGIES INC | 2006Q2 |  |  |  |
| INVESTABLE_QUARTER_WITHOUT_REPORT_ROW | 061143 | NETSCAPE COMMUNICATIONS CORP | 1998Q4 |  |  |  |
| FIRM_WITHOUT_ANY_PRICE | 002230 | BIOMET INC |  | 5938B | 135 | 49 |
| FIRM_WITHOUT_ANY_PRICE | 004828 | DELHAIZE AMERICA INC |  | DEG1 | 104 | 18 |
| FIRM_WITHOUT_ANY_PRICE | 012144 | SUNGARD DATA SYSTEMS INC |  | 0139A | 123 | 2 |
| FIRM_WITHOUT_ANY_PRICE | 013506 | QVC INC |  | 0189A | 162 | 0 |
| FIRM_WITHOUT_ANY_PRICE | 030095 | QUINTILES TRANSNATIONAL CORP |  | 0573B | 51 | 16 |

## 6. מסוף הרבעון הפיסקלי ועד יום ההשקעה (רק `in_index = 1`)

- לפי החלטתך (19/10): מסמנים עכשיו ומחליטים בשלב 2. 2 = רבעון קלנדרי רגיל; 1 ו-0 = רבעון פיסקלי שנגמר חודש לפני יום ההשקעה או באותו חודש — הדוח כנראה עוד לא פורסם.

| period_year | months | rows | share_of_year | examples_latest_year |
|---|---|---|---|---|
| all | 0 | 1040 | 0.08327 | ADBE, COST, CTAS, MU, PAYX |
| all | 1 | 1826 | 0.1462 | ADI, ADSK, AMAT, AVGO, CPRT, CRWD, CSCO, INTU, MRVL, NVDA, PANW, ROST |
| all | 2 | 9623 | 0.7705 | AAPL, ABNB, ADP, AEP, ALAB, ALNY, AMD, AMGN, AMZN, APP, ARM, ASML |

## 7. שחזור


| file | path | exists | bytes | modified | sha256 | unchanged_during_run |
|---|---|---|---|---|---|---|
| panel_parquet | C:\Users\User\roic_research\output\panel.parquet | True | 342853093 | 2026-10-27T15:43:05.354883194 | 73f1dda0e3ee4985f90cd924d7142e63e4e464759287b37768fce669f2c5f3a8 | True |
| prices_parquet | C:\Users\User\roic_research\output\prices.parquet | True | 34353349 | 2026-10-27T15:45:09.557599783 | 9ebc7f2aa27be0c0d8332dd1ff0fb3ca0e62dec1814475b21a6303e2a0092ee0 | True |
| qqq100_constituents | C:\Users\User\roic_research\data\qqq100hist.csv | True | 39724 | 2026-10-17T17:47:46.629358768 | 95730d321fc97b2f714f7d46e6219b3bd7174fbf658b1f994b1d748e509ca6fe | True |

- `output/index_panels/qqq100/logs/run_manifest.json` — גרסאות, ספירות וה-hashes.
