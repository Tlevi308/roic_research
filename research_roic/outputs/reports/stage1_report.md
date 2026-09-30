# שלב 1 — לימוד הנתונים ובניית מדגם המחקר

ריצה: `20260913_104535` · נתונים נכונים ל־2026-09-05 · הגדרות: `outputs/logs/config_used_20260913_104535.yaml` · כל קובצי המקור לא השתנו במהלך הריצה (SHA-256 לפני/אחרי): **True**

## 0. מילון הנתונים

- המילון מצהיר על **174 עמודות**; חולצו 174 משתנים (כולל 63 עמודות דמה). הטקסט המלא, כולל משוואות OMML שהומרו לנוסחה לינארית, נשמר ב־`outputs/reports/dictionary_fulltext.txt`.
- הקטלוג (משמעות, נוסחה, יחידה, ערכים אפשריים, דגל איכות, תנאי אי־תקפות) נשמר ב־`outputs/tables/dictionary_variables.csv`.
- ב־CSV יש 119 עמודות: 111 תואמות למילון; 63 עמודות מהמילון חסרות ב־CSV (63 עמודות דמה, שנמצאות רק בגיליון Decomposition של ה־xlsx).
- עמודות לא מתועדות: `KEY`, `Future_Quarterly_Return`, `Momentum_3Q`. עמודות כפולות זהות (הוסרו אחרי אימות): `calc_debt_value_quarterly.1`, `cash_and_cash_equivalents.1`, `short_term_investments.1`, `market_cap.1`, `calc_debt_value_quarterly.2`.
- נוסחאות מרכזיות מהמילון (אומתו מול הנתונים, ראו סעיף 5):
  - `ROIC_post = NOPAT / avg IC`, `NOPAT = EBIT·(1−T)`, `avg IC = (IC[t−1]+IC[t])/2` רק כשהרבעון הקודם רצוף; `IC = TCA − TCL + net PPE + goodwill`.
  - `C_EBIT = (E1−E0)·[⅓Q0/I0 + ⅙Q1/I0 + ⅙Q0/I1 + ⅓Q1/I1]`, `C_TAX = (Q1−Q0)·[⅓E0/I0 + ⅙E1/I0 + ⅙E0/I1 + ⅓E1/I1]`, `C_IC = (1/I1 − 1/I0)·[⅓E0Q0 + ⅙E1Q0 + ⅙E0Q1 + ⅓E1Q1]`, כאשר Q = 1−T. הסכום שווה בדיוק ל־ΔROIC.
  - תרומת IC **אינה** כיוון השינוי בהון: כש־NOPAT שלילי, ירידה בהון מורידה את ROIC. כיוון ההון עצמו: `calc_ic_movement` / `avg_ic_change_4q`.

## 1. שדה התשואה העתידית — מועמדים ובחירה


| column | in_dictionary | non_null_company_rows | exact_zero | mean | median | p01 | p99 |
|---|---|---|---|---|---|---|---|
| valuations__per_share_data__month_end_stock_price | True | 22875 | 226 | 154.9 | 78.94 | 0.5796 | 809.2 |
| Future_Quarterly_Return | False | 22566 | 563 | 0.03889 | 0.02365 | -0.358 | 0.5775 |
| Momentum_3Q | False | 22566 | 151 | 0.1307 | 0.08366 | -0.5297 | 1.408 |

`valuations__per_share_data__month_end_stock_price` הוא רמת מחיר בסוף הרבעון הפיסקלי, לא תשואה. `Momentum_3Q` הוא תשואת עבר. המועמד היחיד לתשואה עתידית הוא:

**בחירה: `Future_Quarterly_Return`** (לא מתועד במילון). ראיות:

| evidence | n | value |
|---|---|---|
| SPY/QQQ rows: fiscal_period_end_date equals formation_date (quarter end + 2 months) | 245 | 1 |
| fiscal end = formation date (lag 0 days): corr(FQR[t], price[t+1]/price[t]-1) | 874 | 0.9997 |
| fiscal end = formation date (lag 0 days): median \|FQR[t] - (price[t+1]/price[t]-1)\| | 874 | 0.003177 |
| fiscal end = formation date (lag 0 days): corr(FQR[t], price[t]/price[t-1]-1) (wrong alignment) | 874 | 0.1048 |
| fiscal end = formation - ~1 month: corr(FQR[t], price[t+1]/price[t]-1) | 1920 | 0.6358 |
| fiscal end = formation - ~1 month: median \|FQR[t] - (price[t+1]/price[t]-1)\| | 1920 | 0.097 |
| fiscal end = formation - ~1 month: corr(FQR[t], price[t]/price[t-1]-1) (wrong alignment) | 1903 | 0.1262 |
| fiscal end = formation - ~2 months (calendar quarters): corr(FQR[t], price[t+1]/price[t]-1) | 19032 | 0.3307 |
| fiscal end = formation - ~2 months (calendar quarters): median \|FQR[t] - (price[t+1]/price[t]-1)\| | 19032 | 0.1163 |
| fiscal end = formation - ~2 months (calendar quarters): corr(FQR[t], price[t]/price[t-1]-1) (wrong alignment) | 18500 | 0.02029 |

- הגדרה מאומתת: התשואה בשורה t נמדדת **מסוף החודש של (סוף רבעון period_key + חודשיים) ועד 3 חודשים אחר כך**. למשל, 2025Q4 → מ־28/02/2026 עד 31/05/2026. זה תואם את המוסכמה שתיארת. ההפרש החציוני הקטן מיחס המחירים (~0.3%) מרמז שמדובר במחיר מתואם, ככל הנראה כולל דיבידנדים (Adjusted Close בגיליון Price).
- ⚠️ ברבעון שחלון התשואה שלו טרם הסתיים (סוף החלון אחרי 2026-09-05), הקובץ שומר **0.0 במקום ערך חסר**: 555 ערכי 0.0 ו־3 ערכי NaN, מתוך 558 שורות (2026Q2: 558, כולל SPY/QQQ). בעמודה `fwd_return` כולם NaN. 10 תשואות 0.0 במחזורים שהתממשו נשמרו ומסומנות (`fwd_return_exact_zero_realized`), וגם בגיליון Price הן 0 — ייתכן מחיר קפוא.
- `Momentum_3Q` = מכפלת שלוש התשואות שקדמו למועד הבנייה (אין שימוש במידע עתידי). 157 ערכים שהם 0.0 בדיוק הם היסטוריה חסרה, והוגדרו NaN ב־`momentum_3q`.
- אפסים שמקודדים ערך חסר גם בשדות שוק: `market_cap` = 0 ב־282 שורות חברה → `market_cap_clean` = NaN; `valuations__per_share_data__month_end_stock_price` = 0 ב־226 שורות חברה → `price_clean` = NaN (למשל רבעונים לפני הנפקה, כמו ABNB 2020Q1–Q3). העמודות המקוריות לא שונו.

## 2. קובץ הרלוונטיות

- מבנה: `ticker, start_quarter, end_quarter`; 872 טווחים, 852 טיקרים, 503 טווחים פתוחים. 19 טיקרים עם כמה טווחים, ללא חפיפות. פורמטים לא תקינים: 0 / 0.
- **כלל (אושר על ידך):** חברה שייכת לאוכלוסיית רבעון q ⇔ `start_quarter ≤ period_key < end_quarter`; end ריק ⇒ חברה עד היום. לפי הכלל, 3 טווחים שבהם start = end הם ריקים: BMS (2019Q2), MBC (2022Q4), SOLS (2025Q4) — החברה נכנסה ויצאה באותו רבעון.
- כיסוי: חברות המדד לפי הקובץ לעומת חברות עם שורה בפאנל (טבלה מלאה: `relevance_coverage_by_quarter.csv`):

| period_key | index_members | members_with_panel_row | members_ticker_absent_from_panel | members_ticker_in_panel_but_no_row | coverage_share |
|---|---|---|---|---|---|
| 2016Q4 | 503 | 55 | 81 | 367 | 0.1093 |
| 2017Q1 | 504 | 420 | 78 | 6 | 0.8333 |
| 2018Q1 | 505 | 428 | 68 | 9 | 0.8475 |
| 2019Q1 | 508 | 443 | 59 | 6 | 0.872 |
| 2020Q1 | 508 | 456 | 50 | 2 | 0.8976 |
| 2021Q1 | 507 | 462 | 42 | 3 | 0.9112 |
| 2022Q1 | 506 | 470 | 33 | 3 | 0.9289 |
| 2023Q1 | 504 | 479 | 22 | 3 | 0.9504 |
| 2024Q1 | 502 | 483 | 16 | 3 | 0.9622 |
| 2025Q1 | 503 | 489 | 12 | 2 | 0.9722 |
| 2026Q1 | 503 | 497 | 4 | 2 | 0.9881 |
| 2026Q2 | 503 | 444 | 3 | 56 | 0.8827 |

  הרבעון הראשון והאחרון חלקיים מבחינת דיווח: 2016Q4 כולל רק שנות כספים שמסתיימות בינואר/פברואר, וב־2026Q2 חלק מהדוחות עוד לא פורסמו.
- ⚠️ **Survivorship:** 102 טווחי חברות מדד שחופפים לתקופת המחקר חסרים לגמרי בפאנל (סטטוס הורדה ב־Manifest: {'FAILED': 102}). 99 מהם של חברות שכבר יצאו מהמדד (למשל CTRA, HOLX, DAY, IPG, K, ANSS, HES, JNPR, WBA, DFS, CTLT, MRO, CMA, PXD, CDAY), ו־3 של חברות שעדיין במדד (AVB, CBOE, FDXF). בגלל זה הכיסוי עולה מ־83.3% ב־2017Q1 ל־98.8% ב־2026Q1: ככל שחוזרים אחורה, חסרות יותר חברות שנרכשו, נמחקו או קרסו. כיוון ההטיה על התשואות אינו ידוע מראש, כי חברות נרכשות נוטות לתשואה חיובית וחברות קורסות לשלילית. אי אפשר לתקן זאת בלי מקור נתונים נוסף (`relevance_members_missing_from_panel.csv`).
- טיקרים עם כמה טווחים: 19. בפאנל יש לכל טיקר שם חברה אחד בלבד. שורות פאנל ששויכו ליותר מטווח אחד: DD, EQT, PCG. מהקבצים אי אפשר לאמת שהטווח המוקדם מתייחס לאותה ישות משפטית (למשל שושלת DuPont ב־DD). (`relevance_reused_tickers.csv`)

## 3. מבנה וכיסוי הפאנל

- 23,214 שורות: 22,969 שורות חברה (630 חברות) ו־245 שורות SPY/QQQ (מדדי ייחוס, נשמרו בנפרד ב־`benchmarks.csv`).
- רבעונים: 2016Q4–2026Q2. הרבעון הראשון חלקי (74 חברות, רק שנות כספים לא קלנדריות), וגם האחרון חלקי (556 חברות).
- כפילויות:


| key | duplicated_rows |
|---|---|
| symbol + period_key | 0 |
| symbol + fiscal_period_end_date | 0 |
| KEY | 0 |

- רבעונים לכל חברה: חציון 38, מינימום 1, מקסימום 39 (`coverage_quarters_per_firm.csv`, `coverage_firms_per_quarter.csv`).
- שבירות רצף: 58 ב־19 חברות (הכי הרבה: DPZ (18), KR (9), AAP (8)). הגדרת הרצף שלנו: צעד period_key של 1, ו־60–130 יום בין סופי תקופה. ההשוואה להגדרת הצינור (`UNCLASSIFIED_NONCONSECUTIVE`):

| ours_not_consecutive | False | True |
|---|---|---|
| False | 22281 | 0 |
| True | 1 | 687 |

  שורות שבהן רק פער הימים שובר את הרצף: 2018Q4_JEF (151 יום; לפי הצינור רצוף=True). בשורות אלה חלונות המגמה שלנו מקבלים NaN, אף שהתרומות בקובץ קיימות.

## 4. תזמון ומידע עתידי

- `filing_date` אינו מועד ההגשה המקורי: פער חציוני של 399 יום מסוף התקופה, ו־89.9% מהתאריכים מאוחרים ממועד בניית התיק (בשנים 2016–2024 בין 99.3% ל־100.0%; ב־2025 55.4%; ב־2026 5.1%). ככל הנראה זה תאריך ההגשה האחרון שבו הופיעו נתוני הרבעון (יתכנו נתונים מתוקנים, restated). **המשמעות אינה ברורה, ולכן השדה אינו משמש לתזמון.** המחקר נשען על מוסכמת החודשיים שלך, ולא ניתן לאמת point-in-time מתוך הקובץ.
- ⚠️ חברות שהרבעון הפיסקלי שלהן מסתיים בפברואר/מאי/אוגוסט/נובמבר ממופות לרבעון שבו **מועד בניית התיק הוא יום סוף התקופה עצמו** (0 חודשים). דוח לא יכול להיות זמין ביום סוף התקופה, ולכן אלה שורות עם מידע עתידי מובנה. חברות שמסיימות בינואר/אפריל/יולי/אוקטובר מקבלות חודש אחד (28–31 ימים), וחברות קלנדריות חודשיים (59–62 ימים). באוכלוסייה: 745 שורות עם 0 חודשים, ו־2,140 שורות בסך הכול עם פחות מ־2 חודשים. הן **סומנו ולא הוסרו** (`signal_lag_below_min`):

| fiscal_end_month | rows | population_rows | lag_months | min_lag_days | max_lag_days | rows_flagged |
|---|---|---|---|---|---|---|
| 1 | 528 | 370 | 1 | 28 | 29 | 528 |
| 2 | 238 | 198 | 0 | 0 | 0 | 238 |
| 3 | 5273 | 4072 | 2 | 61 | 61 | 0 |
| 4 | 518 | 361 | 1 | 31 | 31 | 518 |
| 5 | 235 | 194 | 0 | 0 | 0 | 235 |
| 6 | 5279 | 4088 | 2 | 62 | 62 | 0 |
| 7 | 485 | 338 | 1 | 31 | 31 | 485 |
| 8 | 214 | 177 | 0 | 0 | 0 | 214 |
| 9 | 4741 | 3672 | 2 | 61 | 61 | 0 |
| 10 | 471 | 326 | 1 | 30 | 30 | 471 |
| 11 | 214 | 176 | 0 | 0 | 0 | 214 |
| 12 | 4773 | 3705 | 2 | 59 | 60 | 0 |

## 5. אימות נוסחאות המילון ודיוק הנתונים

- ⚠️ **ה־CSV מעגל חלק מהעמודות לתצוגה**: ROIC רבעוני, שיעור מס ו־D/E מעוגלים ל־0.01. ב־ROIC רבעוני (חציון ~0.03) זה יוצר קשרים מסיביים ומשבש את חלוקת השלישים:

| column | quarters | median_obs_per_quarter | median_distinct_per_quarter | median_share_tied |
|---|---|---|---|---|
| calc_roic_pretax_quarterly_ic_raw | 38 | 343 | 36 | 0.8911 |
| roic_pretax_q | 38 | 343 | 339 | 0.009709 |
| calc_roic_posttax_quarterly_ic_raw | 38 | 336 | 34 | 0.8964 |
| roic_posttax_q | 38 | 336 | 332.5 | 0.009709 |
| roic_trend_4q | 35 | 329 | 326 | 0.009119 |

  לכן רמות ה־ROIC חושבו מחדש לפי נוסחאות המילון מאותו קובץ (`roic_pretax_q = ebit/avg IC`, `roic_posttax_q = NOPAT/avg IC`); דפוס ה־NaN זהה לעמודות המקור. פרמטר: `features.roic_level_source = recompute`.
- השוואה לקריאה בלבד מול `GuruFocus_quarterly_data.xlsx`, שבו הערכים בדיוק מלא:

| check | value | n |
|---|---|---|
| CSV keys found in xlsx Price sheet | 0.9826 | 23214 |
| Price.As_Of_Date == formation_date | 1 | 22811 |
| CSV FQR == 0 where Price sheet FQR is NaN (zero-coded missing) | 555 | 22811 |
| cleaned fwd_return NaN where Price sheet NaN (company rows with price data) | 1 | 555 |
| max \|fwd_return - Price sheet FQR\| | 5e-10 | 22256 |
| CSV Momentum_3Q == 0 where Price sheet is NaN (zero-coded missing) | 157 | 22811 |
| CSV Momentum_3Q == 0 where Price sheet also has a value (genuine zero, set NaN by rule) | 0 | 22811 |
| pre-tax ROIC recomputed vs xlsx full precision: median \|diff\| | 3.469e-18 | 15627 |
| pre-tax ROIC recomputed vs xlsx full precision: 99th pct \|diff\| | 4.798e-06 | 15627 |
| pre-tax ROIC recomputed vs xlsx full precision: max \|diff\| | 0.02093 | 15627 |
| pre-tax ROIC NaN pattern recomputed == xlsx | 1 | 23214 |
| post-tax ROIC recomputed vs xlsx full precision: median \|diff\| | 2.204e-07 | 15284 |
| post-tax ROIC recomputed vs xlsx full precision: 99th pct \|diff\| | 5.41e-06 | 15284 |
| post-tax ROIC recomputed vs xlsx full precision: max \|diff\| | 0.02093 | 15284 |
| post-tax ROIC CSV rounded vs xlsx full precision: median \|diff\| | 0.002485 | 15284 |
| post-tax ROIC NaN pattern recomputed == xlsx | 1 | 23214 |
| D/E recomputed vs xlsx: 99th pct \|diff\| | 0.0002049 | 21046 |

- בדיקות נוסחה (טבלה מלאה: `dictionary_formula_checks.csv`):

| check | n_tested | share_pass | max_abs_dev | median_abs_dev |
|---|---|---|---|---|
| tax expense = -tax_provision | 22172 | 1 | 0 | 0 |
| tax rate = tax expense / pretax income | 21838 | 1 | 44.45 | 0.002446 |
| NOPAT = EBIT x (1 - T) | 20777 | 1 | 201.4 | 0.002713 |
| IC raw = TCA - TCL + net PPE + goodwill | 16216 | 1 | 0.02 | 0 |
| IC raw NaN pattern equals NaN of any input | 22969 | 1 |  |  |
| average IC = (IC[t-1] + IC[t]) / 2 (previous quarter in sequence) | 15627 | 1 | 0.005 | 0 |
| average IC is NaN after a sequence break | 687 | 1 |  |  |
| pre-tax ROIC (file, rounded) = recomputed ROIC rounded to 0.01 | 15627 | 1 | 0.0213 | 0.002527 |
| post-tax ROIC (file, rounded) = recomputed ROIC rounded to 0.01 | 15284 | 0.9999 | 0.0213 | 0.002486 |
| ROIC change = ROIC[t] - ROIC[t-1] | 14610 | 0.9995 | 0.0215 | 4.048e-07 |
| Shapley: C_EBIT + C_TAX + C_IC = change in ROIC | 14610 | 1 | 1e-06 | 8.674e-19 |
| decomposition residual = 0 | 14610 | 1 | 0 | 0 |
| Shapley C_EBIT rebuilt from E, Q, I (weights 1/3, 1/6) | 14608 | 0.9861 | 0.2849 | 3.052e-07 |
| Shapley C_TAX rebuilt from E, Q, I (weights 1/3, 1/6) | 14608 | 0.9839 | 0.2488 | 3.071e-07 |
| Shapley C_IC rebuilt from E, Q, I (weights 1/3, 1/6) | 14608 | 0.9969 | 0.0217 | 2.58e-07 |
| debt = short-term + long-term debt incl. leases | 22969 | 1 | 0.01 | 0 |
| D/E (file, rounded) = debt / stockholders equity | 21046 | 1 | 76.4 | 0.002474 |
| economic_interpretation_valid == (quality_flag == VALID) | 14610 | 1 |  |  |
| annualised post-tax ROIC = (1 + q)^4 - 1 | 15260 | 0.9997 | 179,006 | 1.029e-06 |
| Momentum_3Q = prod(1 + FQR[t-3..t-1]) - 1 | 20627 | 1 | 1.126e-08 | 4.224e-10 |
| period_key = calendar quarter of (fiscal end - 2 months) | 22969 | 1 |  |  |
| KEY = period_key + '_' + symbol | 23214 | 1 |  |  |
| period_year + period_quarter = period_key | 22969 | 1 |  |  |

  בדיקות על ה־CSV עם פחות מ־99.9% הצלחה: Shapley C_EBIT rebuilt from E, Q, I (weights 1/3, 1/6), Shapley C_TAX rebuilt from E, Q, I (weights 1/3, 1/6), Shapley C_IC rebuilt from E, Q, I (weights 1/3, 1/6).
- אותן נוסחאות על ערכי ה־xlsx בדיוק מלא (סובלנות יחסית 1e-9):

| check | n_tested | share_pass | max_abs_dev | median_abs_dev |
|---|---|---|---|---|
| [full precision] calc_roic_ebit_contribution = Shapley formula (1/3, 1/6) | 14610 | 1 | 4.547e-13 | 1.857e-18 |
| [full precision] calc_roic_tax_contribution = Shapley formula (1/3, 1/6) | 14610 | 1 | 5.684e-14 | 2.602e-18 |
| [full precision] calc_roic_ic_contribution = Shapley formula (1/3, 1/6) | 14610 | 1 | 1.643e-14 | 2.087e-18 |
| [full precision] post-tax ROIC = NOPAT / avg IC | 15284 | 1 | 5.329e-15 | 3.469e-18 |
| [full precision] pre-tax ROIC = EBIT / avg IC | 15627 | 1 | 3.553e-15 | 0 |
| [full precision] ROIC change = ROIC[t] - ROIC[t-1] | 14610 | 1 | 5.684e-14 | 3.469e-18 |
| [full precision] C_EBIT + C_TAX + C_IC = change | 14610 | 1 | 2.274e-13 | 8.674e-19 |
| [full precision] annualised = (1 + q)^4 - 1 | 15260 | 1 | 3.052e-05 | 0 |

  מכיוון שבדיוק מלא הנוסחאות מתקיימות כמעט בכל השורות, **הכשלים ב־CSV נובעים מעיגול הקלטים** (בעיקר מכנה קטן: pretax income של כמה מיליונים, שמנפח את שיעור המס, או avg IC קטן). אין אי־התאמה בין המילון לנתונים.
- ערכי קיצון (לא הוסרו): ROIC רבעוני לאחר מס באוכלוסייה נע בין -14.7 ל־491 (אחוזונים 1%/99%: -0.128/0.316), בגלל הון מושקע קטן או שלילי. ב־44 שורות באוכלוסייה avg IC ≤ 0. שיפוע OLS רגיש לערכים כאלה; מיון לשלישים פחות רגיש.

## 6. משתני המגמה

- שיפוע OLS על x = 0,1,2,3 (משקלים −0.3, −0.1, 0.1, 0.3) מחושב רק כשארבעת הערכים קיימים ו־4 השורות הן רבעונים רצופים; אחרת NaN. חושבו: `roic_trend_4q` (ROIC לאחר מס בדיוק מלא), `ebit/tax/ic_contribution_trend_4q`, סכום וממוצע 4 התרומות, `roic_change_4q` = ROIC[t] − ROIC[t−4], שינוי גולמי בהון (`avg_ic_change_4q`, `avg_ic_pct_change_4q`, `avg_ic_trend_4q_rel`) ודגלי איכות לחלון (`n_econ_valid_4q` ועוד).
- המגמה של חברה ברבעון t משתמשת בהיסטוריה שלה גם מרבעונים שבהם עוד לא הייתה במדד. זה מידע שהיה זמין במועד הבנייה.
- תקציר באוכלוסייה (טבלה מלאה: `feature_summary_population.csv`):

| feature | n | share_available | mean | std | p01 | median | p99 |
|---|---|---|---|---|---|---|---|
| roic_pretax_q | 12487 | 0.7064 | 0.05444 | 0.3262 | -0.1291 | 0.04409 | 0.3424 |
| roic_posttax_q | 12229 | 0.6918 | 0.08528 | 4.453 | -0.1281 | 0.03574 | 0.316 |
| debt_to_equity_q | 16255 | 0.9196 | -1.145 | 131.9 | -21.46 | 0.7579 | 16.87 |
| roic_trend_4q | 10826 | 0.6124 | -0.01794 | 1.494 | -0.07155 | 0.0001758 | 0.07751 |
| ebit_contribution_trend_4q | 10419 | 0.5894 | -0.02951 | 3.751 | -0.09071 | 7.67e-05 | 0.09188 |
| tax_contribution_trend_4q | 10419 | 0.5894 | 0.03416 | 2.841 | -0.08748 | -5.91e-05 | 0.07467 |
| ic_contribution_trend_4q | 10419 | 0.5894 | 0.000129 | 0.04606 | -0.01643 | 1.11e-05 | 0.01407 |
| ebit_contribution_sum_4q | 10419 | 0.5894 | 0.09402 | 11.04 | -0.2303 | 0.003073 | 0.2825 |
| ebit_contribution_mean_4q | 10419 | 0.5894 | 0.0235 | 2.761 | -0.05758 | 0.0007683 | 0.07062 |
| tax_contribution_sum_4q | 10419 | 0.5894 | -0.1366 | 11.13 | -0.2107 | 4.5e-05 | 0.2142 |
| tax_contribution_mean_4q | 10419 | 0.5894 | -0.03415 | 2.783 | -0.05268 | 1.125e-05 | 0.05355 |
| ic_contribution_sum_4q | 10419 | 0.5894 | -0.003747 | 0.1232 | -0.09067 | -0.001593 | 0.06088 |
| ic_contribution_mean_4q | 10419 | 0.5894 | -0.0009368 | 0.03081 | -0.02267 | -0.0003983 | 0.01522 |
| roic_change_4q | 10782 | 0.6099 | -0.04629 | 4.757 | -0.2304 | 0.0008029 | 0.2495 |
| avg_ic_change_4q | 11092 | 0.6275 | 1379 | 8485 | -1.149e+04 | 400.6 | 3.119e+04 |
| avg_ic_pct_change_4q | 11056 | 0.6254 | 0.08441 | 0.4057 | -0.4487 | 0.04747 | 1.234 |
| avg_ic_trend_4q_rel | 11382 | 0.6439 | 0.01505 | 0.1338 | -0.1626 | 0.01136 | 0.2234 |
| n_decomp_valid_4q | 16339 | 0.9243 | 2.741 | 1.777 | 0 | 4 | 4 |
| n_econ_valid_4q | 16339 | 0.9243 | 2.197 | 1.733 | 0 | 2 | 4 |
| n_positive_avg_ic_4q | 16339 | 0.9243 | 2.871 | 1.771 | 0 | 4 | 4 |

## 7. שלוש שכבות המדגם


| step | rows | firms | quarters | first_quarter | last_quarter |
|---|---|---|---|---|---|
| 1 raw rows (all symbols) | 23214 | 632 | 135 | 1992Q4 | 2026Q2 |
| 1a benchmark ETF rows (SPY/QQQ) | 245 | 2 | 135 | 1992Q4 | 2026Q2 |
| 1b company rows | 22969 | 630 | 39 | 2016Q4 | 2026Q2 |
| 2 population: company rows that are index members in period_key | 17677 | 579 | 39 | 2016Q4 | 2026Q2 |
| 2a population with pre-tax ROIC level | 12487 | 430 | 38 | 2017Q1 | 2026Q2 |
| 2b population with post-tax ROIC level | 12229 | 428 | 38 | 2017Q1 | 2026Q2 |
| 2c population with ROIC 4q trend | 10826 | 415 | 35 | 2017Q4 | 2026Q2 |
| 2d population with all three signals (common sort sample) | 10826 | 415 | 35 | 2017Q4 | 2026Q2 |
| 2e population with all three contribution trends | 10419 | 413 | 34 | 2018Q1 | 2026Q2 |
| 2f population rows flagged fiscal end < 2 months before formation (kept) | 2140 | 71 | 39 | 2016Q4 | 2026Q2 |
| 3 evaluation: population with realised forward return | 17119 | 574 | 38 | 2016Q4 | 2026Q1 |
| 3d evaluation within common sort sample | 10465 | 411 | 34 | 2017Q4 | 2026Q1 |
| 3e evaluation with all contribution trends | 10061 | 409 | 33 | 2018Q1 | 2026Q1 |

- **המדגם הגולמי** = כל שורות ה־CSV. **מדגם בניית האות (אוכלוסייה)** = שורות חברה שהן חברות מדד ב־period_key, בלי שום תלות בתשואות. **מדגם ההערכה** = אוכלוסייה עם תשואה שהתממשה. זמינות התשואה לא משנה את האוכלוסייה, את האותות או את גבולות השלישים (נבדק בבדיקות).
- המדגם המשותף לשלושת המיונים של שלב 2 (ROIC לפני מס, ROIC לאחר מס, מגמה) מכסה 35 רבעונים; במדגם ההערכה שלו 34 רבעונים (2017Q4–2026Q1), כ־308 חברות ברבעון בממוצע (בלי 2017Q4, שבו יש רק 45 חברות: כ־316). **זו סדרת זמן קצרה**, והיא תגביל את עוצמת ההסקה.
- פירוט לפי רבעון: `sample_flow_by_quarter.csv`.

### 7א. למה רק ~60% מחברות המדד נכנסות למיון

ממוצע לרבעון, 2018Q1–2026Q1 (טבלה מלאה: `signal_funnel_by_quarter.csv`):

| שלב | חברות ברבעון (ממוצע) | ירידה מהשלב הקודם |
|---|---|---|
| חברות מדד לפי קובץ הרלוונטיות | 505.2 |  |
| עם שורה בפאנל | 469 | 36.2 |
| עם IC (calc_ic_raw) | 342.9 | 126.1 |
| עם ROIC לאחר מס | 333.5 | 9.4 |
| עם מגמת ROIC ל־4 רבעונים | 317.7 | 15.8 |
| מדגם משותף עם תשואה שהתממשה | 315.8 | 2 |

הסיבה הראשונה לחוסר ROIC לאחר מס בשורות האוכלוסייה (לפי סדר רכיבי הנוסחה; `roic_missing_reasons.csv`):

| reason | rows | rows_per_quarter |
|---|---|---|
| 1 current assets or current liabilities missing (IC undefined) | 3044 | 78.05 |
| 2 goodwill missing, other IC inputs present | 1746 | 44.77 |
| 3 net PPE missing | 13 | 0.3333 |
| 4 IC present but previous quarter missing / not consecutive | 387 | 9.923 |
| 5 NOPAT missing (EBIT, tax or pretax income) | 258 | 6.615 |

לפי סקטור (`roic_missing_reasons_by_sector.csv`):

| sector | population_rows | share_without_roic | 1 current assets or current liabilities missing (IC undefined) | 2 goodwill missing, other IC inputs present | 3 net PPE missing | 4 IC present but previous quarter missing / not consecutive | 5 NOPAT missing (EBIT, tax or pretax income) |
|---|---|---|---|---|---|---|---|
| Real Estate | 1104 | 0.8143 | 824 | 67 | 0 | 6 | 2 |
| Financial Services | 2430 | 0.7922 | 1895 | 0 | 5 | 13 | 12 |
| Energy | 743 | 0.5262 | 1 | 362 | 0 | 20 | 8 |
| Utilities | 1101 | 0.3851 | 31 | 326 | 0 | 25 | 42 |
| Consumer Cyclical | 2180 | 0.2606 | 142 | 292 | 0 | 77 | 57 |
| Industrials | 2556 | 0.1588 | 55 | 242 | 0 | 67 | 42 |
| Consumer Defensive | 1301 | 0.1576 | 3 | 107 | 3 | 47 | 45 |
| Healthcare | 2181 | 0.1499 | 86 | 179 | 0 | 45 | 17 |
| Basic Materials | 769 | 0.09883 | 0 | 47 | 0 | 18 | 11 |
| Communication Services | 777 | 0.08623 | 0 | 44 | 0 | 15 | 8 |
| Technology | 2535 | 0.06312 | 7 | 80 | 5 | 54 | 14 |

- **בנקים, ביטוח ו־REITs** לא מדווחים נכסים והתחייבויות שוטפים, ולכן ה־IC לפי נוסחת המילון (TCA − TCL + PPE + goodwill) אינו מוגדר עבורם. זו תכונה של ההגדרה ולא תקלה, ומחקרי ROIC נוהגים להוציא חברות פיננסיות מראש.
- **goodwill חסר**: ב־113 חברות (למשל AA, AAPL, ABNB, ADCT, ADM, AEP, ALGN, ANET) רק goodwill חסר, וכל שאר רכיבי ה־IC קיימים. goodwill = 0 מופיע רק ב־35 שורות בכל הפאנל, ו־94 חברות אף פעם לא מדווחות goodwill. חלק מהמקרים הם כנראה "אין goodwill", אבל לא כולם: ל־AAPL יש goodwill של 5,889 ב־2017, ומ־2018 הערך חסר (אפל הפסיקה להציג אותו בשורה נפרדת). ב־51 חברות יש חורים באמצע סדרת ה־goodwill. **לא הנחתי ש־NaN = 0.**

## 8. פערים ונקודות פתוחות לפני שלב 2

1. **2,140 שורות באוכלוסייה שבהן סוף התקופה קרוב מדי למועד הבנייה** (0 או 1 חודשים; חברות עם שנת כספים לא קלנדרית): להשאיר במדגם הראשי ולבדוק רגישות, או להחריג מראש (או לדחות את האות שלהן ברבעון)? כרגע הן מסומנות בלבד.
2. **סינוני איכות לשלב 2** צריכים להיקבע מראש ולחול על שלושת המיונים: למשל `calc_roic_economic_interpretation_valid`, avg IC > 0, או NOPAT חיובי. לא החלתי אף סינון.
3. **מינימום חברות ברבעון** לחלוקה לשלישים (פרמטר שעוד לא נקבע).
3א. **goodwill חסר**: להשאיר את חישוב ה־IC כבמילון (החברות האלה ללא ROIC), או להוסיף בדיקת רגישות שבה goodwill חסר = 0? בגרסה השנייה נוספות כ־45 חברות ברבעון, אבל ה־IC מוטה כלפי מטה אצל חברות שה־goodwill שלהן פשוט לא מוצג בשורה נפרדת (כמו AAPL).
3ב. **חברות פיננסיות ונדל"ן**: להוציא אותן מראש מהמדגם הראשי, כמקובל במחקרי ROIC, או להשאיר את המיעוט שיש לו ROIC (כ־19 ברבעון)?
4. Survivorship ו־filing_date: מגבלות שיתועדו בפרק המגבלות, כי אי אפשר לתקן אותן מהקבצים.
5. `valuations__ratios__debt_to_equity` ללא NaN גם כשההון העצמי חסר — ייתכן מילוי אפסים במקור. בשלב 4 יש להשתמש ב־D/E המחושב.
6. בקרות שלא קיימות בקבצים: תנודתיות ובטא (דורשות מקור נוסף). קיימים: גודל (market_cap), סקטור, מינוף, מומנטום (`momentum_3q`), תמחור (EV/FCF; יחס ספר/שוק ניתן לחישוב), רווחיות וצמיחת הון.
