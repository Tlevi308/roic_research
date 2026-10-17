# פאנל מחירים מ-Compustat Security Monthly — דוח בנייה ואימות

נתונים: `prices.gz` · 8,572,102 שורות חודשיות · 1962-01-31–2026-10-31 · ריצה 2026-10-17 11:09:48 (38.2 שניות) · הגדרות: `output/prices_build/logs/config_used.yaml` · קובצי המקור לא השתנו במהלך הריצה (SHA-256 לפני/אחרי): **True**

## 0. תקציר

- **8 עמודות** לפי החוזה, באותו סדר, **1,925,749 שורות** — שורה אחת לכל (חברה, רבעון), 38,452 חברות.
- כיסוי: `Close` 97.5% · `Quaterly_return` 94.9% · `MomentomQ3` 90.8% · `ipodate` 32.8% · `dldte` 61.1%.
- הפלט: `output/prices.parquet` (כל השורות) ו-`output/prices_from_2025.xlsx` (74,097 שורות מ-2025).
- ⚠️ `Quaterly_return` הוא **ערך עתידי**: התשואה ברבעון שאחרי `Date`. זה המשתנה התלוי, ולעולם אינו נכנס לסיגנל, לסינון או לטרסילים.

## 1. ההגדרות

סימון: `q` הוא רבעון השורה, `q+1` הרבעון הבא (3 חודשים קדימה), `q−3` שלושה רבעונים אחורה (9 חודשים).

| עמודה | הגדרה |
|---|---|
| `KEY` | `period_key + "_" + tic`, דרך `keys.build_key` — אותה פונקציה של הפאנל הבסיסי |
| `Date` | `datadate` מהקובץ: היום האחרון בחודש הקלנדרי |
| `Ticker` | `tic`, זהה ל-`symbol` בפאנל הבסיסי |
| `Close` | `prccm / ajexm` — מחיר מתואם לפיצולים |
| `Quaterly_return` | `Close(q+1)/Close(q) − 1` — הרבעון הבא, **בלי דיבידנדים** |
| `MomentomQ3` | `Close(q)/Close(q−3) − 1` — שלושת הרבעונים האחרונים |
| `ipodate`, `dldte` | כפי שהם בקובץ; לא מסננים ולא משנים שום ערך |

- **חודש לרבעון** — אותו כלל של הפאנל הבסיסי, הרבעון הקלנדרי של (סוף החודש − 2 חודשים): פברואר → Q4 של השנה הקודמת, מאי → Q1, אוגוסט → Q2, נובמבר → Q3.
- **חיפוש לפי רבעון קלנדרי** (gvkey, quarter_index ± k), לא לפי מיקום שורה: רבעון חסר נותן NaN, ולעולם לא תשואה של שני רבעונים שמתויגת כרבעונית.
- **רק נקודות הקצה**: לתשואה Close תקף ב-q וב-q+1, למומנטום ב-q−3 וב-q. רבעוני ביניים לא משפיעים.
- **"אין מחיר"**: `prccm` אפס או חסר, או `ajexm` חסר, נותן `Close = NaN`. אף שורה לא נמחקה ושום ערך לא נקטם.

## 2. המקור והשדות

- כל עמודה בקובץ מדווחת: ממופה, חסרה, או קיימת-ולא-בשימוש. תאריכים שאינם סוף חודש: **0**.

| logical_field | source_column | status | n_non_null |
|---|---|---|---|
| gvkey | gvkey | MAPPED | 8572102 |
| symbol | tic | MAPPED | 8571696 |
| company | conm | MAPPED | 8572102 |
| exchange | exchg | MAPPED | 8567755 |
| month_end | datadate | MAPPED | 8572102 |
| price_close | prccm | MAPPED | 8343843 |
| adjustment_factor | ajexm | MAPPED | 8516393 |
| total_return_pct | trt1m | MAPPED | 8302620 |
| ipo_date | ipodate | MAPPED | 2420234 |
| deletion_date | dldte | MAPPED | 4820968 |

## 3. בחירת הנייר

- בקובץ אין `iid`, ולחלק מהחברות יש כמה ניירות. נבחר הנייר שה-(gvkey, tic) שלו **זהה** לזה של הפאנל הבסיסי — ולכן ה-KEY של המחירים זהה מבנית ל-KEY הבסיסי.
- חברה בלי התאמה מדויקת **לא מנוחשת**: אין לה שורות, והיא מפורטת, עם הטיקרים שכן נמצאים בקובץ, ב-`issue_selection_unmatched.csv`.

| category | firms | meaning |
|---|---|---|
| MATCHED | 38589 | the security whose (gvkey, tic) equals the base panel's |
| NO_TIC_MATCH | 169 | gvkey is in the file only under other tickers - no rows, not guessed |
| NO_PRICE_ROWS | 2150 | gvkey is not in the price file at all |
| NO_SYMBOL_IN_BASE | 17 | the base panel has no ticker for this gvkey, so no KEY exists |
| FILE_ONLY_NOT_IN_BASE | 15004 | gvkeys in the price file with no fundamentals - not used |
| MATCHED_WITHOUT_QUARTER_MONTH | 137 | of MATCHED: no price row in Feb/May/Aug/Nov at all, so no panel rows |

## 4. למה ערך חסר

- כל NaN מקבל סיבה. `DELETED_BY_NEXT_DATE`: ‏`dldte` נופל עד תאריך הרבעון הבא. `NOT_YET_AVAILABLE`: תאריך הרבעון הבא אחרי סוף הקובץ. `BEFORE_FIRST_PRICE`: שלושת הרבעונים הראשונים של הסדרה.

| column | reason | rows | share |
|---|---|---|---|
| Close | OK | 1877958 | 0.9752 |
| Close | MISSING_PRICE | 42154 | 0.02189 |
| Close | ZERO_PRICE | 5637 | 0.002927 |
| Quaterly_return | OK | 1827699 | 0.9491 |
| Quaterly_return | CLOSE_MISSING_PRICE | 42154 | 0.02189 |
| Quaterly_return | DELETED_BY_NEXT_DATE | 19460 | 0.01011 |
| Quaterly_return | NOT_YET_AVAILABLE | 12693 | 0.006591 |
| Quaterly_return | NEXT_MISSING_PRICE | 8760 | 0.004549 |
| Quaterly_return | NEXT_QUARTER_MISSING | 7894 | 0.004099 |
| Quaterly_return | CLOSE_ZERO_PRICE | 5637 | 0.002927 |
| Quaterly_return | NEXT_ZERO_PRICE | 1452 | 0.000754 |
| MomentomQ3 | OK | 1747876 | 0.9076 |
| MomentomQ3 | BEFORE_FIRST_PRICE | 106812 | 0.05547 |
| MomentomQ3 | CLOSE_MISSING_PRICE | 42154 | 0.02189 |
| MomentomQ3 | BACK_QUARTER_MISSING | 14048 | 0.007295 |
| MomentomQ3 | BACK_MISSING_PRICE | 8110 | 0.004211 |
| MomentomQ3 | CLOSE_ZERO_PRICE | 5637 | 0.002927 |
| MomentomQ3 | BACK_ZERO_PRICE | 1112 | 0.0005774 |
| MomentomQ3 | INFO: computed although q-1 or q-2 has no valid Close | 7695 | 0.003996 |

## 5. פער הדיבידנד

- לפי בחירתך התשואה היא יחס מחירים, **בלי דיבידנדים**. הטבלה משווה, לכל רבעון, את תשואת המחיר שמומשה (q−1 → q) לתשואה הכוללת ממכפלת שלושת ה-`trt1m` החודשיים. הפער `(1+כוללת)/(1+מחיר) − 1` הוא תשואת הדיבידנד של הרבעון.
- החישוב **מסתכל אחורה בלבד**. פער שלילי ברור אינו דיבידנד ונספר כחריג.

| scope | quarters_compared | share_no_dividend | share_positive_gap | share_anomalous_negative | gap_p01 | gap_median | gap_p99 | gap_median_when_positive |
|---|---|---|---|---|---|---|---|---|
| all | 1825245 | 0.6151 | 0.3824 | 0.002463 | -1.296e-06 | 4.393e-07 | 0.04068 | 0.008669 |
| 1960s | 47999 | 0.2576 | 0.7424 | 0 | -1.162e-06 | 0.00669 | 0.0233 | 0.00851 |
| 1970s | 136415 | 0.3916 | 0.6084 | 0 | -1.069e-06 | 0.005882 | 0.03498 | 0.01154 |
| 1980s | 224152 | 0.6018 | 0.3981 | 6.692e-05 | -1.052e-06 | 4.575e-07 | 0.03823 | 0.009492 |
| 1990s | 333296 | 0.6744 | 0.3249 | 0.0006331 | -1.387e-06 | 3.2e-07 | 0.03696 | 0.007894 |
| 2000s | 411553 | 0.7211 | 0.2783 | 0.0005564 | -1.3e-06 | 2.114e-07 | 0.04058 | 0.008417 |
| 2010s | 393717 | 0.6208 | 0.3744 | 0.004811 | -1.311e-06 | 4.07e-07 | 0.0414 | 0.00771 |
| 2020s | 278113 | 0.5613 | 0.431 | 0.007716 | -1e-05 | 6.306e-07 | 0.06075 | 0.007925 |

## 6. ערכים קיצוניים — נספרים, לא נחתכים

- לפי כלליך שום ערך לא נקטם ולא הוסר. 20 הקיצוניים מכל עמודה, עם KEY, ב-`extreme_return_top.csv`.

| column | measure | value |
|---|---|---|
| Quaterly_return | values | 1827699 |
| Quaterly_return | min | -1 |
| Quaterly_return | p0.1 | -0.9803 |
| Quaterly_return | median | 0 |
| Quaterly_return | p99.9 | 32.3 |
| Quaterly_return | max | 493,330 |
| Quaterly_return | at or below -0.9 | 7977 |
| Quaterly_return | above 1 | 41467 |
| Quaterly_return | above 10 | 3892 |
| Quaterly_return | above 100 | 717 |
| MomentomQ3 | values | 1747876 |
| MomentomQ3 | min | -1 |
| MomentomQ3 | p0.1 | -0.9976 |
| MomentomQ3 | median | 0 |
| MomentomQ3 | p99.9 | 72.11 |
| MomentomQ3 | max | 459,997 |
| MomentomQ3 | at or below -0.9 | 28843 |
| MomentomQ3 | above 1 | 95555 |
| MomentomQ3 | above 10 | 7167 |
| MomentomQ3 | above 100 | 1205 |
| trt1m (percent, matched monthly rows) | max | 520,999,900 |
| trt1m (percent, matched monthly rows) | above 1000 | 9622 |

## 7. כיסוי ה-JOIN לפאנל הבסיסי

- איזה אחוז מה-KEY של הפאנל הבסיסי מוצא שורת מחיר ואת כל אחד מהמספרים. הפירוק לפי שנה ב-`join_coverage.csv`.

| period_year | base_keys | with_price_row | with_Close | with_Quaterly_return | with_MomentomQ3 | share_with_price_row | share_with_Close | share_with_Quaterly_return | share_with_MomentomQ3 |
|---|---|---|---|---|---|---|---|---|---|
| all | 1903226 | 1607732 | 1597470 | 1567635 | 1506415 | 0.8447 | 0.8393 | 0.8237 | 0.7915 |
| >= 2000 | 1045134 | 918345 | 916941 | 895745 | 873524 | 0.8787 | 0.8773 | 0.8571 | 0.8358 |


## 8. תזמון — מסוף הרבעון הפיסקלי ועד `Date`

- ⚠️ אצל חברות שהרבעון הפיסקלי שלהן מסתיים בינואר/אפריל/יולי/אוקטובר המחיר נלקח **חודש** אחרי סוף הרבעון, ואצל פברואר/מאי/אוגוסט/נובמבר — **באותו חודש**. בשתי הקבוצות הדוח כנראה עוד לא פורסם ב-`Date`. המיפוי לא שונה; ההחלטה שייכת לשלב ה-JOIN.

| scope | lag_months | rows | share |
|---|---|---|---|
| all | 0 | 98835 | 0.06147 |
| all | 1 | 144391 | 0.08981 |
| all | 2 | 1364506 | 0.8487 |
| period_year >= 2000 | 0 | 40087 | 0.04365 |
| period_year >= 2000 | 1 | 63394 | 0.06903 |
| period_year >= 2000 | 2 | 814864 | 0.8873 |


## 9. תאריכי הנפקה ומחיקה

- ⚠️ **`dldte` אינו היום האחרון למסחר**: לחלק מהחברות יש מחירים שנים אחרי `dldte`, ולאחרות המחירים נגמרים שנים לפניו (האחוזונים בטבלה).
- ⚠️ **אזהרת look-ahead לשלב הבא**: מותר להשתמש בתאריכים רק בצורה שידועה ב-`Date` — "ההנפקה כבר הייתה" (`ipodate ≤ Date`) ו"החברה עוד לא נמחקה" (`dldte` ריק או `dldte > Date`). סינון של חברה כי **תימחק** ברבעון הבא משתמש במידע עתידי, מוציא בדיוק את התשואות הגרועות ומטה את התוצאות.

| measure | value | denominator | share |
|---|---|---|---|
| matched firms | 38589 | 38589 | 1 |
| firms with ipodate | 13917 | 38589 | 0.3606 |
| firms with dldte | 24881 | 38589 | 0.6448 |
| active (costat A) firms | 13708 | 38589 | 0.3552 |
| active (costat A) firms with dldte | 0 | 13708 | 0 |
| inactive (costat I) firms | 24881 | 38589 | 0.6448 |
| inactive (costat I) firms with dldte | 24881 | 24881 | 1 |
| monthly rows dated before ipodate | 37530 | 5783673 | 0.006489 |
| ... of which with a valid price | 34752 | 5783673 | 0.006009 |
| monthly rows in a month after the dldte month | 247309 | 5783673 | 0.04276 |
| ... of which with a valid price | 219845 | 5783673 | 0.03801 |
| panel rows with Date before ipodate | 12484 | 1925749 | 0.006483 |
| panel rows in a month after the dldte month | 82020 | 1925749 | 0.04259 |
| days from dldte to the last valid price, p01 (firms) | -2643 | 24803 |  |
| days from dldte to the last valid price, p10 (firms) | -290 | 24803 |  |
| days from dldte to the last valid price, p50 (firms) | 5 | 24803 |  |
| days from dldte to the last valid price, p90 (firms) | 1566 | 24803 |  |
| days from dldte to the last valid price, p99 (firms) | 7611 | 24803 |  |

## 10. הדוגמה שלך — AAPL

- בדוגמה שלך המחירים הם Adj Close של יאהו, שמתואם גם לדיבידנדים, ונלקחים ביום המסחר האחרון. כאן התשואה בלי דיבידנדים ו-`Date` הוא סוף החודש הקלנדרי — ולכן התשואות נמוכות מעט משלך. להמחשה בלבד.

| KEY | your_Date | Date | your_Close | Close | your_Quaterly_return | Quaterly_return | Quaterly_return_minus_yours | your_MomentomQ3 | MomentomQ3 | MomentomQ3_minus_yours |
|---|---|---|---|---|---|---|---|---|---|---|
| 2013Q4_AAPL | 2014-02-28 | 2014-02-28 | 16.3758 | 18.7943 | 0.209591 | 0.202873 | -0.00671827 |  | 0.170111 |  |
| 2014Q1_AAPL | 2014-05-30 | 2014-05-31 | 19.808 | 22.6071 | 0.139129 | 0.133491 | -0.005638 |  | 0.299218 |  |
| 2014Q2_AAPL | 2014-08-29 | 2014-08-31 | 22.5639 | 25.625 | 0.165324 | 0.160293 | -0.00503148 |  | 0.290305 |  |
| 2014Q3_AAPL | 2014-11-28 | 2014-11-30 | 26.2942 | 29.7325 | 0.0843939 | 0.0801312 | -0.00426275 | 0.605678 | 0.581997 | -0.0236813 |
| 2014Q4_AAPL | 2015-02-27 | 2015-02-28 | 28.5133 | 32.115 | 0.0184041 | 0.0141678 | -0.00423626 | 0.439484 | 0.420569 | -0.0189153 |
| 2015Q1_AAPL | 2015-05-29 | 2015-05-31 | 29.0381 | 32.57 | -0.130562 | -0.13448 | -0.00391761 | 0.286927 | 0.271024 | -0.0159027 |
| 2015Q2_AAPL | 2015-08-31 | 2015-08-31 | 25.2468 | 28.19 | 0.053622 | 0.0491309 | -0.00449108 | -0.0398351 | -0.0518793 | -0.0120442 |
| 2015Q3_AAPL | 2015-11-30 | 2015-11-30 | 26.6006 | 29.575 | -0.178236 | -0.182671 | -0.00443492 | -0.0670818 | -0.0790908 | -0.012009 |
| 2015Q4_AAPL | 2016-02-29 | 2016-02-29 | 21.8594 | 24.1725 | 0.0390736 | 0.0327852 | -0.00628837 | -0.247216 | -0.257829 | -0.0106134 |
| 2016Q1_AAPL | 2016-05-31 | 2016-05-31 | 22.7135 | 24.965 | 0.0682429 | 0.0624875 | -0.00575541 | -0.100341 | -0.114402 | -0.0140616 |
| 2016Q2_AAPL | 2016-08-31 | 2016-08-31 | 24.2635 | 26.525 | 0.0470069 | 0.0416588 | -0.00534811 | -0.0878563 | -0.103128 | -0.0152714 |
| 2016Q3_AAPL | 2016-11-30 | 2016-11-30 | 25.4041 | 27.63 | 0.244878 | 0.239504 | -0.00537423 | 0.16216 | 0.143034 | -0.0191254 |
| 2016Q4_AAPL | 2017-02-28 | 2017-02-28 | 31.625 | 34.2475 | 0.119721 | 0.115118 | -0.0046027 | 0.392344 | 0.371821 | -0.0205233 |
| 2017Q1_AAPL | 2017-05-31 | 2017-05-31 | 35.4112 | 38.19 | 0.0777954 | 0.0735795 | -0.00421589 | 0.45944 | 0.439774 | -0.0196659 |

## 11. שחזור


| file | path | exists | bytes | modified | sha256 | unchanged_during_run |
|---|---|---|---|---|---|---|
| input_parquet | C:\Users\User\roic_research\data\data.parquet | True | 77905989 | 2026-10-10T17:16:54.939969540 | 5efd0835bf73a057d8431d1e9ff33408ff747ebacd07ace4f3f9662ce15c0974 | True |
| prices_input | C:\Users\User\roic_research\data\prices.gz | True | 87178427 | 2026-10-17T06:51:42.300364971 | 58ea5780ec721f06b2da5bf92ccffbf8156dcc66ceda41d4f69c7de8040d3069 | True |

- `output/prices_build/logs/run_manifest.json` — גרסאות, ספירות וה-hashes. `output/prices_build/logs/config_used.yaml` — עותק בייט של ההגדרות.
