# פאנל ROIC מנתוני Compustat — דוח בנייה ואימות

נתונים: `data.parquet` · 1,905,390 שורות מקור · נכונים ל־2026-09-30 · ריצה 2026-10-16 18:14:42 (61.8 שניות) · הגדרות: `output/logs/config_used.yaml` · קובצי המקור לא השתנו במהלך הריצה (SHA-256 לפני/אחרי): **True**

## 0. תקציר

- **73 עמודות** בדיוק לפי הרשימה שהוגדרה, באותו סדר, **1,903,609 שורות** — שורה אחת לכל (חברה, רבעון).
- **כל העמודות חושבו** — אין עמודה ריקה בהחלטה (`schema_contract.csv`).
- רציפות: 97.7% מהשורות מקושרות לרבעון שקדם להן.
- `ibq` נוסף לקובץ ב-10/10, ולכן ה-FCFE נבנה כולו משדות רבעוניים (§10א). `fyearq`, `fqtr`, `rdq`, `fyr`, `ivstq` עדיין אינם במשיכה — ואף חישוב בפאנל הזה אינו תלוי בהם.
## 1. המקור, הטיפוסים והשדות

- `decimal128(18,4)` מומר ל-float64 **בתוך Arrow** לפני pandas. אחרת מתקבלים מיליוני אובייקטי `Decimal`, וחלוקה באפס זורקת חריגה במקום להחזיר NaN.
- כל עמודה בקובץ מדווחת: ממופה, חסרה, או קיימת-ולא-בשימוש (`input_fields.csv`).

| logical_field | source_candidates | source_found | status | n_non_null |
|---|---|---|---|---|
| gvkey | gvkey | gvkey | MAPPED | 1905390 |
| symbol | tic | tic | MAPPED | 1905007 |
| company | conm | conm | MAPPED | 1905390 |
| sector | gsector | gsector | MAPPED | 1597993 |
| fiscal_period_end_date | datadate | datadate | MAPPED | 1905390 |
| total_current_assets | actq | actq | MAPPED | 1164507 |
| total_current_liabilities | lctq | lctq | MAPPED | 1173256 |
| cash_and_short_term_investments | cheq | cheq | MAPPED | 1395069 |
| income_before_extraordinary_items | ibq | ibq | MAPPED | 1565125 |
| net_ppe | ppentq | ppentq | MAPPED | 1374075 |
| goodwill | gdwlq | gdwlq | MAPPED | 671819 |
| total_assets | atq | atq | MAPPED | 1410305 |
| total_liabilities | ltq | ltq | MAPPED | 1402734 |
| ebit | oiadpq | oiadpq | MAPPED | 1461258 |
| pretax_income | piq | piq | MAPPED | 1538282 |
| tax_provision_raw | txtq | txtq | MAPPED | 1542466 |
| short_term_debt | dlcq | dlcq | MAPPED | 1346981 |
| long_term_debt | dlttq | dlttq | MAPPED | 1424201 |
| market_cap | mkvaltq | mkvaltq | MAPPED | 470196 |
|  |  | capxy | UNMAPPED_SOURCE_COLUMN | 1155622 |
|  |  | consol | UNMAPPED_SOURCE_COLUMN | 1905390 |
|  |  | costat | UNMAPPED_SOURCE_COLUMN | 1905390 |
|  |  | curcdq | UNMAPPED_SOURCE_COLUMN | 1905390 |
|  |  | datafmt | UNMAPPED_SOURCE_COLUMN | 1905390 |
|  |  | exchg | UNMAPPED_SOURCE_COLUMN | 1904995 |
|  |  | indfmt | UNMAPPED_SOURCE_COLUMN | 1905390 |
|  |  | oancfy | UNMAPPED_SOURCE_COLUMN | 1055139 |
|  |  | seqq | UNMAPPED_SOURCE_COLUMN | 1441428 |
|  |  | teqq | UNMAPPED_SOURCE_COLUMN | 524118 |
|  |  | xintq | UNMAPPED_SOURCE_COLUMN | 1205810 |

- דיוק ההמרה: הערך המוחלט הגדול ביותר בשדות המקור הוא 4,921,519, מול גבול ההמרה המדויקת 900,719,925,474 — פי 183,017 מרווח. ההמרה מ-decimal ל-float64 אינה מאבדת ספרה (`precision_audit.csv`).
- ⚠️ ערכי ROIC ו-D/E נגזרים יכולים לצאת עצומים כשבסיס ההון קרוב לאפס (אך אינו אפס בדיוק, שאז הכלל מחזיר NaN). לפי כלליך לא נעשתה שום קטימה ולא הוסרה שום שורה — הסדרי הגודל מדווחים במקום:

| measure | rows | share_of_defined | median_abs_avg_ic_on_those_rows |
|---|---|---|---|
| \|quarterly ROIC\| > 1 | 32336 | 0.03026 | 0.4283 |
| \|quarterly ROIC\| > 10 | 4127 | 0.003862 | 0.0495 |
| \|quarterly ROIC\| > 100 | 519 | 0.0004857 | 0.007 |
| \|quarterly ROIC\| > 1e+06 | 18 | 1.685e-05 | 2.776e-17 |
| \|D/E\| > 100 | 3387 | 0.002438 |  |

## 2. המפתח והרבעון

- `period_key` = הרבעון הקלנדרי של (סוף התקופה הפיסקלית פחות שני חודשים). `KEY = period_key + "_" + symbol`, למשל `2016Q4_A`.
- ⚠️ הכפילות מוכרעת על `(gvkey, period_key)` ולא על ה-KEY עצמו: 383 שורות (נאמנויות, ETNs) אינן נושאות טיקר, ושרשור טיקר חסר היה מאחד ישויות שונות למפתח אחד. השורות האלה נשמרות עם KEY ריק.

## 3. כפילויות — מיזוג בשני שלבים

- שלב 1 (`gvkey` + תאריך זהים): השורה הראשונה נשארת, וערך חסר בה מושלם מהשורות העוקבות באותה קבוצה.
- שלב 2 (אותו רבעון, תאריכים שונים — שינוי סוף שנה פיסקלית): השורה הראשונה קובעת והערכים שלה קובעים; שום ערך לא מעורבב בין תקופות פיסקליות שונות.
- כל שורה שהוסרה נרשמת במלואה, עם כל ערכיה ועם השורה שגברה עליה (`duplicates_audit.csv`) — ההכרעה ניתנת לשחזור ולביטול.

| measure | value |
|---|---|
| rows in source | 1905390 |
| removed in step 1 (same gvkey and date, values merged) | 1108 |
| rows after step 1 | 1904282 |
| removed in step 2 (same period, different date, first row wins) | 673 |
| rows in panel | 1903609 |

## 4. רציפות

- שורה מקושרת לרבעון שקדם לה רק כאשר צעד הרבעון הוא 1 **וגם** פער הימים בטווח שבקונפיג. חישוב חשבוני על חודשים, לא על ימים, היה מסמן 31/12→28/02 בטעות.

| measure | value |
|---|---|
| rows | 1,903,609 |
| rows linked to the previous quarter | 1,859,613 |
| share linked | 0.9769 |
| within-firm pairs | 1,862,684 |
| pairs with quarter step == 1 | 1,860,143 |
| pairs with day gap in [60, 130] | 1,859,748 |
| day gap p01 | 89 |
| day gap median | 92 |
| day gap p99 | 92 |
| rows with run_len >= 2 | 1,859,613 |
| rows with run_len >= 4 | 1,773,899 |

## 5. שרשרת הרמות

- כיסוי מדורג: `ebit` 76.7% · שיעור מס 80.5% · NOPAT 76.3% · `calc_ic_raw` 61.0% · `calc_average_ic_raw_quarterly` 57.1% · ROIC posttax 56.1%.
- **הון חוזר חובה**: בלי נכסים או התחייבויות שוטפים ההון המושקע נשאר NaN ולא מתכווץ ל-PPE+Goodwill. בנקים ומבטחים מדווחים מאזן לא מסווג — זה הפער העיקרי בין כיסוי NOPAT לכיסוי ROIC.
- PPE ומוניטין חסרים נספרים כאפס: שורה אחת חסרה לא צריכה למחוק רבעון שלם.
- אנואליזציה היא `(1+r)⁴−1` ומוגדרת רק כש-`1+r > 0`.

## 6. מס — כולל מס שלילי

שיעור המס **לעולם לא נחתך** לטווח [0,1]: הוא מסומן ומשמש כפי שדווח. זו ההכרעה בין המקרא (שהציע קטימה ל-0–40%) לבין המפרט, ועצם קיומם של דגלי האיכות מניח שיעורים מחוץ לטווח.

⚠️ שיעור מס שלילי נובע משתי תופעות שונות לגמרי — הטבת מס על רווח, ומס חיובי על הפסד לפני מס — ולכן הן נספרות בנפרד:

| measure | rows | denominator | share_of_denominator |
|---|---|---|---|
| tax expense present | 1541200 | 1903609 | 0.8096 |
| tax expense < 0 (benefit) | 208890 | 1541200 | 0.1355 |
| tax expense == 0 | 369596 | 1541200 | 0.2398 |
| tax expense > 0 | 962714 | 1541200 | 0.6247 |
| tax rate defined | 1532314 | 1903609 | 0.805 |
| tax rate < 0 | 149299 | 1532314 | 0.09743 |
| tax rate in [0, 1] | 1361116 | 1532314 | 0.8883 |
| tax rate > 1 (above 100%) | 21899 | 1532314 | 0.01429 |
| negative rate: benefit on a profit (tax<0, pretax>0) | 45869 | 149299 | 0.3072 |
| negative rate: tax on a pretax loss (tax>0, pretax<0) | 103430 | 149299 | 0.6928 |

## 7. הפירוקים

- `NOPAT = E·Q` (שני גורמים) ו-`ROIC = E·Q/I` (שלושה). שני הגשרים משתמשים באותה מטריצת מקדמי Shapley: ⅓ ו-⅙, על 2ⁿ ערכי קואליציה שמחושבים פעם אחת.
- `C_EBIT = ΔE·[⅓Q₀/I₀ + ⅙Q₁/I₀ + ⅙Q₀/I₁ + ⅓Q₁/I₁]` ושתי אחיותיה.
- ה-residual הוא **בדיקת חיווט ולא שארית**: הזהות מדויקת מהבנייה.

| check | n_tested | n_passed | n_failed | share_passed | note |
|---|---|---|---|---|---|
| calc_raw_tax_rate_quarterly = tax expense / pretax income | 1532314 | 1532314 | 0 | 1 |  |
| calc_nopat_quarterly = ebit * (1 - tax rate) | 1453398 | 1453398 | 0 | 1 |  |
| calc_ic_raw = TCA - TCL + PPE + goodwill (PPE/GW missing = 0) | 1160864 | 1160864 | 0 | 1 |  |
| calc_average_ic_raw_quarterly = (IC(t-1) + IC(t)) / 2 | 1087389 | 1087389 | 0 | 1 | defined only across a consecutive pair |
| calc_roic_pretax_quarterly_ic_raw = numerator / avg IC | 1071359 | 1071359 | 0 | 1 |  |
| calc_roic_pretax_annualized_ic_raw = (1+r)^4 - 1 | 1054190 | 1054190 | 0 | 1 | NaN when 1+r <= 0 |
| calc_roic_pretax_annualized is NaN exactly when 1+r <= 0 | 1903609 | 1903609 | 0 | 1 |  |
| calc_roic_posttax_quarterly_ic_raw = numerator / avg IC | 1068559 | 1068559 | 0 | 1 |  |
| calc_roic_posttax_annualized_ic_raw = (1+r)^4 - 1 | 1051337 | 1051337 | 0 | 1 | NaN when 1+r <= 0 |
| calc_roic_posttax_annualized is NaN exactly when 1+r <= 0 | 1903609 | 1903609 | 0 | 1 |  |
| NOPAT bridge: C_EBIT + C_TAX = change (Shapley efficiency) | 1361755 | 1361755 | 0 | 1 |  |
| ROIC bridge: C_EBIT + C_TAX + C_IC = change (Shapley efficiency) | 1026478 | 1026478 | 0 | 1 |  |
| calc_roic_total_absolute_contribution = sum of \|C_i\| | 1026478 | 1026478 | 0 | 1 |  |
| absolute shares sum to 1 where they are defined | 1025633 | 1025633 | 0 | 1 |  |
| positive + negative + neutral driver counts = 3 | 1026478 | 1026478 | 0 | 1 |  |
| calc_roic_offset_ratio is inside [0, 1] | 1025633 | 1025633 | 0 | 1 | triangle inequality \|dROIC\| <= sum \|C_i\| |
| calc_debt_value_quarterly = short + long (missing leg = 0) | 1428359 | 1428359 | 0 | 1 |  |
| calc_debt_value_quarterly is NaN exactly when both legs are | 1903609 | 1903609 | 0 | 1 |  |
| calc_debt_to_equity_quarterly = debt / equity | 1389220 | 1389220 | 0 | 1 | zero or missing equity -> NaN |
| period_key = calendar quarter of (fiscal end - 2 months) | 1903609 | 1903609 | 0 | 1 |  |
| KEY = period_key + '_' + symbol | 1903226 | 1903226 | 0 | 1 | rows with no ticker carry no KEY |
| KEY is unique among rows that have a ticker | 1903226 | 1903226 | 0 | 1 |  |

## 8. התוויות

- כל תווית כיוונית מגיעה ממסווג אחד מול רצועת מהותיות. ערך בדיוק על הרצועה הוא ניטרלי.
- ⚠️ **הבדל השערים**: תנועות EBIT והמס מותנות בסטטוס ה-NOPAT (בסיס הון אפס לא אומר כלום על אם EBIT עלה), ורק תנועת ההון המושקע והקומבינציה מותנות בסטטוס ה-ROIC. משטרי הסימן תלויים רק בדוח רווח והפסד ולכן שורדים הון אפס, שלילי או חסר.
- ⚠️ `*_effect` נקרא מסימן התרומה ולא מכיוון התנועה: כששיעור ההשארה שלילי, EBIT עולה מוריד את NOPAT.

| column | level | rows | share |
|---|---|---|---|
| calc_nopat_decomposition_status | VALID | 1361755 | 0.7154 |
| calc_nopat_decomposition_status | UNCLASSIFIED_MISSING_DATA | 497858 | 0.2615 |
| calc_nopat_decomposition_status | UNCLASSIFIED_NONCONSECUTIVE | 43996 | 0.02311 |
| calc_roic_decomposition_status | VALID | 1026478 | 0.5392 |
| calc_roic_decomposition_status | UNCLASSIFIED_MISSING_DATA | 832917 | 0.4375 |
| calc_roic_decomposition_status | UNCLASSIFIED_NONCONSECUTIVE | 43996 | 0.02311 |
| calc_roic_decomposition_status | UNCLASSIFIED_ZERO_IC | 218 | 0.0001145 |
| calc_tax_rate_quality_flag | VALID | 1361116 | 0.715 |
| calc_tax_rate_quality_flag | MISSING | 371295 | 0.195 |
| calc_tax_rate_quality_flag | NEGATIVE_TAX_RATE | 149299 | 0.07843 |
| calc_tax_rate_quality_flag | ABOVE_100_PERCENT | 21899 | 0.0115 |
| calc_roic_quality_flag | UNCLASSIFIED | 877131 | 0.4608 |
| calc_roic_quality_flag | VALID | 763725 | 0.4012 |
| calc_roic_quality_flag | TAX_RATE_OUT_OF_RANGE | 187138 | 0.09831 |
| calc_roic_quality_flag | NEGATIVE_IC_MECHANICAL_ONLY | 75615 | 0.03972 |
| calc_roic_effect_structure | UNCLASSIFIED | 877131 | 0.4608 |
| calc_roic_effect_structure | MIXED_NET_INCREASE | 377519 | 0.1983 |
| calc_roic_effect_structure | MIXED_NET_DECREASE | 294454 | 0.1547 |
| calc_roic_effect_structure | ALL_NEGATIVE | 209815 | 0.1102 |
| calc_roic_effect_structure | ALL_POSITIVE | 104793 | 0.05505 |
| calc_roic_effect_structure | SINGLE_POSITIVE_DRIVER | 17239 | 0.009056 |
| calc_roic_effect_structure | SINGLE_NEGATIVE_DRIVER | 16681 | 0.008763 |
| calc_roic_effect_structure | MIXED_FULL_OFFSET | 5132 | 0.002696 |
| calc_roic_effect_structure | NO_MATERIAL_CHANGE | 845 | 0.0004439 |
| calc_roic_dominant_driver | UNCLASSIFIED | 877131 | 0.4608 |
| calc_roic_dominant_driver | EBIT | 714407 | 0.3753 |
| calc_roic_dominant_driver | IC | 138794 | 0.07291 |
| calc_roic_dominant_driver | TAX | 129848 | 0.06821 |
| calc_roic_dominant_driver | BALANCED | 42584 | 0.02237 |
| calc_roic_dominant_driver | NONE | 845 | 0.0004439 |
| calc_roic_dominant_driver_effect | UNCLASSIFIED | 919715 | 0.4831 |
| calc_roic_dominant_driver_effect | NEGATIVE | 495874 | 0.2605 |
| calc_roic_dominant_driver_effect | POSITIVE | 487175 | 0.2559 |
| calc_roic_dominant_driver_effect | NEUTRAL | 845 | 0.0004439 |

- מפקד הרמות המוצהרות: 160 מתוך 160 נוצרו בפועל; השאר מפורטות ב-`label_family_census.csv` כרמות שלא נצפו.

## 9. ההסבר

- המשפטים נלקחים מקטלוג סגור של 1,083 אפשרויות (`explanation_catalogue.csv`), מתוכן 361 הופיעו בפועל. בדיקה אוכפת שאף שורה לא נופלת מחוץ לקטלוג.
- הניסוח עוקב אחרי סימני Shapley ולא אחרי התנועות הגולמיות: רבעון שבו EBIT עלה אבל בסיס ההון עלה מהר יותר נקרא כירידת ROIC שנגרמה מהון מושקע.

## 10. חוב, הון עצמי ו-D/E

- `equity = atq − ltq` (נכסים פחות התחייבויות). לפי זהות המאזן זהו ההון העצמי הכולל, כולל זכויות מיעוט.
- `calc_debt_value_quarterly = dlcq + dlttq`, רגל חסרה אחת נספרת כאפס ושתיהן חסרות נותנות NaN. `dlttq` כולל חכירות הוניות; חכירות תפעוליות (ASC 842) אינן בשני השדות.
- ⚠️ הון עצמי אפס נותן D/E = NaN לפי חוק המכנה, והון עצמי שלילי נותן D/E שלילי ומכני. אף שורה לא הוסרה ואף ערך לא נקטם:

| measure | rows | denominator | share_of_denominator |
|---|---|---|---|
| equity present | 1401548 | 1903609 | 0.7363 |
| equity == 0 (D/E is NaN by the denominator rule) | 854 | 1903609 | 0.0004486 |
| equity < 0 (D/E negative and mechanical) | 121417 | 1903609 | 0.06378 |
| D/E present | 1389220 | 1903609 | 0.7298 |
| D/E negative | 111629 | 1389220 | 0.08035 |
| calc_ic_raw < 0 (ratio mechanical, flagged) | 87662 | 1160864 | 0.07551 |

## 10א. תזרים מזומנים חופשי לבעלי המניות (FCFE)

התזרים נבנה **כולו משדות רבעוניים** ובלי סדרות YTD — כלומר בלי להזדקק ל-`fyearq`/`fqtr` שאינם במשיכה:

```
NWC_t  = (actq_t − cheq_t) − (lctq_t − dlcq_t)
FCFE_t = ibq_t − (NWC_t − NWC_t−1) − (ppentq_t − ppentq_t−1)

free_cash_flow_quarterly  = FCFE של הרבעון הבודד
calc_free_cash_flow_ttm   = סכום 4 רבעונים עוקבים (אחרת NaN, לא סכום חלקי)
calc_p_to_fcf_quarterly   = market_cap / free_cash_flow_quarterly
calc_p_to_fcf_ttm         = market_cap / calc_free_cash_flow_ttm
```

- השינוי ב-PPE נטו הוא הפרוקסי ל-CapEx בניכוי פחת — זה מה שמאפשר לבנות את כל התזרים מנתונים רבעוניים.
- שני יחסי המחיר מחלקים ב**שווי שוק ולא ב-EV**, כי FCFE הוא תזרים ממונף שמגיע לבעלי המניות; צימוד ל-EV היה סופר את החוב פעמיים.
- **ערך חסר אינו מפיל את החישוב** (החלטת החוקר 11/10). הנוסחה נפרסת לשישה איברים חתומים — הרווח וחמישה הפרשי מאזן — וכל איבר נכנס רק אם אפשר לחשב אותו; איבר שאי אפשר פשוט אינו תורם. תוצאה של 0 נשמרת כתוצאה.
- ⚠️ איבר הפרש שחסר לו הרבעון הקודם **מושמט** ואינו מוחלף ברמה עצמה: `ppentq_t − 0` היה נקרא כאילו החברה קנתה את כל בסיס הרכוש הקבוע ברבעון אחד — תזרים מומצא, לא שמרני.
- ⚠️ שורה שאין בה אף איבר שאפשר לחשב נשארת NaN. אפס מתוך כלום אינו תזרים של אפס.
- ⚠️ **שורה שנבנתה משני איברים אינה אותה מדידה כמו שורה שנבנתה משישה.** לכן ההתפלגות מדווחת, ולא רק מספר כיסוי אחד:

| measure | rows | denominator | share_of_denominator |
|---|---|---|---|
| ibq present | 1563816 | 1903609 | 0.8215 |
| cheq present | 1393963 | 1903609 | 0.7323 |
| free_cash_flow_quarterly present | 1569447 | 1903609 | 0.8245 |
| free_cash_flow_quarterly == 0 exactly | 2294 | 1569447 | 0.001462 |
| free_cash_flow_quarterly < 0 | 801375 | 1569447 | 0.5106 |
| calc_free_cash_flow_ttm present (4 consecutive quarters) | 1439614 | 1903609 | 0.7563 |
| calc_free_cash_flow_ttm < 0 | 728065 | 1439614 | 0.5057 |
| calc_p_to_fcf_quarterly present | 469274 | 1903609 | 0.2465 |
| calc_p_to_fcf_quarterly < 0 | 259009 | 469274 | 0.5519 |
| calc_p_to_fcf_ttm present | 458280 | 1903609 | 0.2407 |
| calc_p_to_fcf_ttm < 0 | 244554 | 458280 | 0.5336 |
| rows on a run of 5+ consecutive quarters (what TTM needs) | 1732319 | 1903609 | 0.91 |
| rows whose flow was built from 0 of 6 terms | 334162 | 1903609 | 0.1755 |
| rows whose flow was built from 1 of 6 terms | 206680 | 1903609 | 0.1086 |
| rows whose flow was built from 2 of 6 terms | 65342 | 1903609 | 0.03433 |
| rows whose flow was built from 3 of 6 terms | 63212 | 1903609 | 0.03321 |
| rows whose flow was built from 4 of 6 terms | 142590 | 1903609 | 0.07491 |
| rows whose flow was built from 5 of 6 terms | 52250 | 1903609 | 0.02745 |
| rows whose flow was built from 6 of 6 terms | 1039373 | 1903609 | 0.546 |

- ⚠️ ה-TTM נשאר **קפדני**: 4 רבעונים עוקבים או NaN. סכום של שלושה רבעונים שמסומן כנגרר-שנה הוא כמות אחרת, וזה עניין נפרד משדה חסר. בפועל הוא דורש **חמישה** רבעונים עוקבים, כי כל FCFE רבעוני בודד זקוק לרבעון שלפניו.

## 11. איך הפאנל מאמת את עצמו

אין יותר הצלבה מול מקור חיצוני (החלטת החוקר 11/10) — האימות עומד על בדיקות שנגזרות מהפאנל עצמו, ולכן אינו תלוי בשום קובץ נוסף:

- **22 בדיקות זהות** מחזירות כל נוסחה מהעמודות שנכתבו ומשוות לעמודה המפורסמת. כשלים: **0**.
- **מפקד הרמות**: 160 מתוך 160 הרמות המוצהרות נוצרו בפועל — זו העדות ל-9/9 ול-27/27 בכל משפחת תוויות.
- **שאריות ה-Shapley** נבדקות על כל השורות: הזהות מדויקת מהבנייה, ולכן שארית שאינה אפס היתה מעידה על חיווט שבור.
- **`no_inf_check.csv`**: אין `inf` באף עמודה — כל מכנה אפס או חסר הפך ל-NaN.
- **`precision_audit.csv`**: ההמרה מ-decimal ל-float64 בשדות המקור, עם מרווח מדוד מול גבול הדיוק.
- **מערך המבחנים** (`pytest`) מחשב את הנוסחאות מחדש באופן עצמאי, כולל חישוב ידני של FCFE מול הקובץ הגולמי, ונועל את המספרים שנמדדו כאן.

## 12. מה לא נבנה, ולמה

- המרת `oancfy`/`capxy` מ-YTD — לא נדרשה: מסלול ה-FCFE (§10א) בונה את התזרים משדות רבעוניים בלבד, ולכן `fyearq`/`fqtr` אינם חסרים לשום חישוב.
- `calc_enterprise_value_quarterly` אינו ברשימת העמודות; שני יחסי המחיר משתמשים בשווי שוק, שהוא המכנה הנכון ל-FCFE.
- תשואות, מומנטום, WACC, מיונים וטרסילים — מחוץ להיקף הבנייה הזאת.
- `sector` הוא קוד `gsector` כפי שהוא, בלי תרגום לשמות GICS.

## 13. שחזור


| file | path | exists | bytes | modified | sha256 | unchanged_during_run |
|---|---|---|---|---|---|---|
| input_parquet | C:\Users\User\roic_research\data\data.parquet | True | 77905989 | 2026-10-10T17:16:54.939969540 | 5efd0835bf73a057d8431d1e9ff33408ff747ebacd07ace4f3f9662ce15c0974 | True |

- `output/logs/run_manifest.json` — גרסאות הספריות, ספירות שורות שלב-אחר-שלב, ו-hashes. `output/logs/config_used.yaml` — עותק בייט של ההגדרות.
