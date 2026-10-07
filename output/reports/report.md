# פאנל ROIC מנתוני Compustat — דוח בנייה ואימות

נתונים: `data.parquet` · 1,905,162 שורות מקור · נכונים ל־2026-09-30 · ריצה 2026-10-05 20:39:27 (97.3 שניות) · הגדרות: `output/logs/config_used.yaml` · קובצי המקור לא השתנו במהלך הריצה (SHA-256 לפני/אחרי): **True**

## 0. תקציר

- **72 עמודות** בדיוק לפי הרשימה שהוגדרה, באותו סדר, **1,903,381 שורות** — שורה אחת לכל (חברה, רבעון).
- 69 עמודות חושבו; 3 ריקות במפורש, עם הסיבה לכל אחת (`schema_contract.csv`).
- רציפות: 97.7% מהשורות מקושרות לרבעון שקדם להן.
- ⚠️ מה שאינו במשיכה: `fyearq`, `fqtr`, `rdq`, `fyr`, `ivstq`. בלי `fqtr` אי אפשר לזהות איזה רבעון פותח את השנה הפיסקלית, ולכן FCF לא מחושב בבנייה הזאת.

| column | spec | reason |
|---|---|---|
| free_cash_flow | free_cash_flow | DISABLED; MISSING_INPUT:oancfy; MISSING_INPUT:capxy |
| calc_free_cash_flow_ttm | free_cash_flow | DISABLED; MISSING_INPUT:oancfy; MISSING_INPUT:capxy |
| calc_ev_to_fcf_quarterly | free_cash_flow | DISABLED; MISSING_INPUT:oancfy; MISSING_INPUT:capxy |

## 1. המקור, הטיפוסים והשדות

- `decimal128(18,4)` מומר ל-float64 **בתוך Arrow** לפני pandas. אחרת מתקבלים מיליוני אובייקטי `Decimal`, וחלוקה באפס זורקת חריגה במקום להחזיר NaN.
- כל עמודה בקובץ מדווחת: ממופה, חסרה, או קיימת-ולא-בשימוש (`input_fields.csv`).

| logical_field | source_candidates | source_found | status | n_non_null |
|---|---|---|---|---|
| gvkey | gvkey | gvkey | MAPPED | 1905162 |
| symbol | tic | tic | MAPPED | 1904779 |
| company | conm | conm | MAPPED | 1905162 |
| sector | gsector | gsector | MAPPED | 1597706 |
| fiscal_period_end_date | datadate | datadate | MAPPED | 1905162 |
| total_current_assets | actq | actq | MAPPED | 1164241 |
| total_current_liabilities | lctq | lctq | MAPPED | 1172990 |
| net_ppe | ppentq | ppentq | MAPPED | 1373790 |
| goodwill | gdwlq | gdwlq | MAPPED | 671525 |
| total_assets | atq | atq | MAPPED | 1410009 |
| total_liabilities | ltq | ltq | MAPPED | 1402438 |
| ebit | oiadpq | oiadpq | MAPPED | 1460964 |
| pretax_income | piq | piq | MAPPED | 1537980 |
| tax_provision_raw | txtq | txtq | MAPPED | 1542163 |
| short_term_debt | dlcq | dlcq | MAPPED | 1346689 |
| long_term_debt | dlttq | dlttq | MAPPED | 1423899 |
| market_cap | mkvaltq | mkvaltq | MAPPED | 469926 |
|  |  | capxy | UNMAPPED_SOURCE_COLUMN | 1155326 |
|  |  | cheq | UNMAPPED_SOURCE_COLUMN | 1394772 |
|  |  | consol | UNMAPPED_SOURCE_COLUMN | 1905162 |
|  |  | costat | UNMAPPED_SOURCE_COLUMN | 1905162 |
|  |  | curcdq | UNMAPPED_SOURCE_COLUMN | 1905162 |
|  |  | datafmt | UNMAPPED_SOURCE_COLUMN | 1905162 |
|  |  | exchg | UNMAPPED_SOURCE_COLUMN | 1904767 |
|  |  | indfmt | UNMAPPED_SOURCE_COLUMN | 1905162 |
|  |  | oancfy | UNMAPPED_SOURCE_COLUMN | 1054842 |
|  |  | seqq | UNMAPPED_SOURCE_COLUMN | 1441132 |
|  |  | teqq | UNMAPPED_SOURCE_COLUMN | 523822 |
|  |  | xintq | UNMAPPED_SOURCE_COLUMN | 1205551 |

- דיוק ההמרה: הערך המוחלט הגדול ביותר בשדות המקור הוא 4,921,519, מול גבול ההמרה המדויקת 900,719,925,474 — פי 183,017 מרווח. ההמרה מ-decimal ל-float64 אינה מאבדת ספרה (`precision_audit.csv`).
- ⚠️ ערכי ROIC ו-D/E נגזרים יכולים לצאת עצומים כשבסיס ההון קרוב לאפס (אך אינו אפס בדיוק, שאז הכלל מחזיר NaN). לפי כלליך לא נעשתה שום קטימה ולא הוסרה שום שורה — הסדרי הגודל מדווחים במקום:

| measure | rows | share_of_defined | median_abs_avg_ic_on_those_rows |
|---|---|---|---|
| \|quarterly ROIC\| > 1 | 32328 | 0.03026 | 0.428 |
| \|quarterly ROIC\| > 10 | 4127 | 0.003863 | 0.0495 |
| \|quarterly ROIC\| > 100 | 519 | 0.0004858 | 0.007 |
| \|quarterly ROIC\| > 1e+06 | 18 | 1.685e-05 | 2.776e-17 |
| \|D/E\| > 100 | 3386 | 0.002438 |  |

## 2. המפתח והרבעון

- `period_key` = הרבעון הקלנדרי של (סוף התקופה הפיסקלית פחות שני חודשים). `KEY = period_key + "_" + symbol`, למשל `2016Q4_A`.
- ⚠️ הכפילות מוכרעת על `(gvkey, period_key)` ולא על ה-KEY עצמו: 383 שורות (נאמנויות, ETNs) אינן נושאות טיקר, ושרשור טיקר חסר היה מאחד ישויות שונות למפתח אחד. השורות האלה נשמרות עם KEY ריק.

## 3. כפילויות — מיזוג בשני שלבים

- שלב 1 (`gvkey` + תאריך זהים): השורה הראשונה נשארת, וערך חסר בה מושלם מהשורות העוקבות באותה קבוצה.
- שלב 2 (אותו רבעון, תאריכים שונים — שינוי סוף שנה פיסקלית): השורה הראשונה קובעת והערכים שלה קובעים; שום ערך לא מעורבב בין תקופות פיסקליות שונות.
- כל שורה שהוסרה נרשמת במלואה, עם כל ערכיה ועם השורה שגברה עליה (`duplicates_audit.csv`) — ההכרעה ניתנת לשחזור ולביטול.

| measure | value |
|---|---|
| rows in source | 1905162 |
| removed in step 1 (same gvkey and date, values merged) | 1108 |
| rows after step 1 | 1904054 |
| removed in step 2 (same period, different date, first row wins) | 673 |
| rows in panel | 1903381 |

## 4. רציפות

- שורה מקושרת לרבעון שקדם לה רק כאשר צעד הרבעון הוא 1 **וגם** פער הימים בטווח שבקונפיג. חישוב חשבוני על חודשים, לא על ימים, היה מסמן 31/12→28/02 בטעות.

| measure | value |
|---|---|
| rows | 1,903,381 |
| rows linked to the previous quarter | 1,859,282 |
| share linked | 0.9768 |
| within-firm pairs | 1,862,355 |
| pairs with quarter step == 1 | 1,859,814 |
| pairs with day gap in [60, 130] | 1,859,417 |
| day gap p01 | 89 |
| day gap median | 92 |
| day gap p99 | 92 |
| rows with run_len >= 2 | 1,859,282 |
| rows with run_len >= 4 | 1,773,575 |

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
| tax expense present | 1540896 | 1903381 | 0.8096 |
| tax expense < 0 (benefit) | 208851 | 1540896 | 0.1355 |
| tax expense == 0 | 369516 | 1540896 | 0.2398 |
| tax expense > 0 | 962529 | 1540896 | 0.6247 |
| tax rate defined | 1532010 | 1903381 | 0.8049 |
| tax rate < 0 | 149251 | 1532010 | 0.09742 |
| tax rate in [0, 1] | 1360867 | 1532010 | 0.8883 |
| tax rate > 1 (above 100%) | 21892 | 1532010 | 0.01429 |
| negative rate: benefit on a profit (tax<0, pretax>0) | 45861 | 149251 | 0.3073 |
| negative rate: tax on a pretax loss (tax>0, pretax<0) | 103390 | 149251 | 0.6927 |

## 7. הפירוקים

- `NOPAT = E·Q` (שני גורמים) ו-`ROIC = E·Q/I` (שלושה). שני הגשרים משתמשים באותה מטריצת מקדמי Shapley: ⅓ ו-⅙, על 2ⁿ ערכי קואליציה שמחושבים פעם אחת.
- `C_EBIT = ΔE·[⅓Q₀/I₀ + ⅙Q₁/I₀ + ⅙Q₀/I₁ + ⅓Q₁/I₁]` ושתי אחיותיה.
- ה-residual הוא **בדיקת חיווט ולא שארית**: הזהות מדויקת מהבנייה.

| check | n_tested | n_passed | n_failed | share_passed | note |
|---|---|---|---|---|---|
| calc_raw_tax_rate_quarterly = tax expense / pretax income | 1532010 | 1532010 | 0 | 1 |  |
| calc_nopat_quarterly = ebit * (1 - tax rate) | 1453102 | 1453102 | 0 | 1 |  |
| calc_ic_raw = TCA - TCL + PPE + goodwill (PPE/GW missing = 0) | 1160598 | 1160598 | 0 | 1 |  |
| calc_average_ic_raw_quarterly = (IC(t-1) + IC(t)) / 2 | 1087160 | 1087160 | 0 | 1 | defined only across a consecutive pair |
| calc_roic_pretax_quarterly_ic_raw = numerator / avg IC | 1071134 | 1071134 | 0 | 1 |  |
| calc_roic_pretax_annualized_ic_raw = (1+r)^4 - 1 | 1053970 | 1053970 | 0 | 1 | NaN when 1+r <= 0 |
| calc_roic_pretax_annualized is NaN exactly when 1+r <= 0 | 1903381 | 1903381 | 0 | 1 |  |
| calc_roic_posttax_quarterly_ic_raw = numerator / avg IC | 1068334 | 1068334 | 0 | 1 |  |
| calc_roic_posttax_annualized_ic_raw = (1+r)^4 - 1 | 1051118 | 1051118 | 0 | 1 | NaN when 1+r <= 0 |
| calc_roic_posttax_annualized is NaN exactly when 1+r <= 0 | 1903381 | 1903381 | 0 | 1 |  |
| NOPAT bridge: C_EBIT + C_TAX = change (Shapley efficiency) | 1361502 | 1361502 | 0 | 1 |  |
| ROIC bridge: C_EBIT + C_TAX + C_IC = change (Shapley efficiency) | 1026266 | 1026266 | 0 | 1 |  |
| calc_roic_total_absolute_contribution = sum of \|C_i\| | 1026266 | 1026266 | 0 | 1 |  |
| absolute shares sum to 1 where they are defined | 1025421 | 1025421 | 0 | 1 |  |
| positive + negative + neutral driver counts = 3 | 1026266 | 1026266 | 0 | 1 |  |
| calc_roic_offset_ratio is inside [0, 1] | 1025421 | 1025421 | 0 | 1 | triangle inequality \|dROIC\| <= sum \|C_i\| |
| calc_debt_value_quarterly = short + long (missing leg = 0) | 1428057 | 1428057 | 0 | 1 |  |
| calc_debt_value_quarterly is NaN exactly when both legs are | 1903381 | 1903381 | 0 | 1 |  |
| calc_debt_to_equity_quarterly = debt / equity | 1388919 | 1388919 | 0 | 1 | zero or missing equity -> NaN |
| period_key = calendar quarter of (fiscal end - 2 months) | 1903381 | 1903381 | 0 | 1 |  |
| KEY = period_key + '_' + symbol | 1902998 | 1902998 | 0 | 1 | rows with no ticker carry no KEY |
| KEY is unique among rows that have a ticker | 1902998 | 1902998 | 0 | 1 |  |

## 8. התוויות

- כל תווית כיוונית מגיעה ממסווג אחד מול רצועת מהותיות. ערך בדיוק על הרצועה הוא ניטרלי.
- ⚠️ **הבדל השערים**: תנועות EBIT והמס מותנות בסטטוס ה-NOPAT (בסיס הון אפס לא אומר כלום על אם EBIT עלה), ורק תנועת ההון המושקע והקומבינציה מותנות בסטטוס ה-ROIC. משטרי הסימן תלויים רק בדוח רווח והפסד ולכן שורדים הון אפס, שלילי או חסר.
- ⚠️ `*_effect` נקרא מסימן התרומה ולא מכיוון התנועה: כששיעור ההשארה שלילי, EBIT עולה מוריד את NOPAT.

| column | level | rows | share |
|---|---|---|---|
| calc_nopat_decomposition_status | VALID | 1361502 | 0.7153 |
| calc_nopat_decomposition_status | UNCLASSIFIED_MISSING_DATA | 497780 | 0.2615 |
| calc_nopat_decomposition_status | UNCLASSIFIED_NONCONSECUTIVE | 44099 | 0.02317 |
| calc_roic_decomposition_status | VALID | 1026266 | 0.5392 |
| calc_roic_decomposition_status | UNCLASSIFIED_MISSING_DATA | 832798 | 0.4375 |
| calc_roic_decomposition_status | UNCLASSIFIED_NONCONSECUTIVE | 44099 | 0.02317 |
| calc_roic_decomposition_status | UNCLASSIFIED_ZERO_IC | 218 | 0.0001145 |
| calc_tax_rate_quality_flag | VALID | 1360867 | 0.715 |
| calc_tax_rate_quality_flag | MISSING | 371371 | 0.1951 |
| calc_tax_rate_quality_flag | NEGATIVE_TAX_RATE | 149251 | 0.07841 |
| calc_tax_rate_quality_flag | ABOVE_100_PERCENT | 21892 | 0.0115 |
| calc_roic_quality_flag | UNCLASSIFIED | 877115 | 0.4608 |
| calc_roic_quality_flag | VALID | 763586 | 0.4012 |
| calc_roic_quality_flag | TAX_RATE_OUT_OF_RANGE | 187084 | 0.09829 |
| calc_roic_quality_flag | NEGATIVE_IC_MECHANICAL_ONLY | 75596 | 0.03972 |
| calc_roic_effect_structure | UNCLASSIFIED | 877115 | 0.4608 |
| calc_roic_effect_structure | MIXED_NET_INCREASE | 377453 | 0.1983 |
| calc_roic_effect_structure | MIXED_NET_DECREASE | 294382 | 0.1547 |
| calc_roic_effect_structure | ALL_NEGATIVE | 209770 | 0.1102 |
| calc_roic_effect_structure | ALL_POSITIVE | 104771 | 0.05504 |
| calc_roic_effect_structure | SINGLE_POSITIVE_DRIVER | 17234 | 0.009054 |
| calc_roic_effect_structure | SINGLE_NEGATIVE_DRIVER | 16679 | 0.008763 |
| calc_roic_effect_structure | MIXED_FULL_OFFSET | 5132 | 0.002696 |
| calc_roic_effect_structure | NO_MATERIAL_CHANGE | 845 | 0.0004439 |
| calc_roic_dominant_driver | UNCLASSIFIED | 877115 | 0.4608 |
| calc_roic_dominant_driver | EBIT | 714278 | 0.3753 |
| calc_roic_dominant_driver | IC | 138758 | 0.0729 |
| calc_roic_dominant_driver | TAX | 129809 | 0.0682 |
| calc_roic_dominant_driver | BALANCED | 42576 | 0.02237 |
| calc_roic_dominant_driver | NONE | 845 | 0.0004439 |
| calc_roic_dominant_driver_effect | UNCLASSIFIED | 919691 | 0.4832 |
| calc_roic_dominant_driver_effect | NEGATIVE | 495760 | 0.2605 |
| calc_roic_dominant_driver_effect | POSITIVE | 487085 | 0.2559 |
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
| equity present | 1401251 | 1903381 | 0.7362 |
| equity == 0 (D/E is NaN by the denominator rule) | 854 | 1903381 | 0.0004487 |
| equity < 0 (D/E negative and mechanical) | 121389 | 1903381 | 0.06378 |
| D/E present | 1388919 | 1903381 | 0.7297 |
| D/E negative | 111604 | 1388919 | 0.08035 |
| calc_ic_raw < 0 (ratio mechanical, flagged) | 87637 | 1160598 | 0.07551 |

## 11. הצלבה מול GuruFocus

GuruFocus מפרסם בעצמו את עמודות ה-`calc_*`, ולכן ההצלבה אינה רק על מספרים אלא גם **הסכמה בין תוויות** מול פאנל שחושב ממקור אחר לגמרי.

| measure | value |
|---|---|
| panel rows | 1903381 |
| panel rows with an ambiguous stripped symbol | 62927 |
| gurufocus rows | 23214 |
| matched rows | 22638 |

מה שמאושר בבירור: `calc_tax_expense_quarterly` (מתאם 0.996, יחס חציוני 1.0000) — כלומר **הכרעת הסימן נכונה**, ו-92.4% מהשורות מקיימות `calc_tax_expense_quarterly == −tax_provision` בדיוק · `calc_ic_raw` ו-`calc_average_ic_raw_quarterly` (יחס 1.0000) — **נוסחת ההון המושקע נכונה** · `equity` (יחס 1.0000) · `pretax_income`, הנכסים וההתחייבויות השוטפים, מוניטין, `market_cap` — כולם ביחס חציוני 1.0000.

| column | n_overlap | nan_pattern_agreement | correlation | median_ratio | median_rel_deviation | share_within_1pct | share_within_5pct | max_abs_deviation | within_thresholds | expected_gap | note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| pretax_income | 21475 | 0.9583 | 0.998 | 1 | 0 | 0.9073 | 0.9482 | 1.065e+04 | True | False |  |
| ebit | 21357 | 0.9558 | 0.9184 | 1 | 0.07277 | 0.1811 | 0.422 | 9.776e+04 | False | True | GuruFocus EBIT measured as piq+xintq; here oiadpq per the dictionary |
| total_current_assets | 18489 | 0.987 | 0.9987 | 1 | 0 | 0.9951 | 0.9978 | 110,771 | True | False |  |
| total_current_liabilities | 18405 | 0.9845 | 0.9996 | 1 | 0 | 0.9944 | 0.9978 | 4.86e+04 | True | False |  |
| goodwill | 17832 | 0.8499 | 0.9993 | 1 | 0 | 0.9942 | 0.9952 | 4.048e+04 | True | False |  |
| net_ppe | 20322 | 0.9435 | 0.9676 | 1 | 1.317e-16 | 0.7703 | 0.8065 | 135,236 | True | False |  |
| short_term_debt | 14755 | 0.7377 | 0.9364 | 1.061 | 0.07445 | 0.3981 | 0.4632 | 2.766e+04 | False | True | dlcq includes the current portion of long-term debt |
| market_cap | 22197 | 0.9898 | 0.9997 | 1 | 1.276e-07 | 0.9343 | 0.9571 | 132,874 | True | False |  |
| equity | 22234 | 0.9956 | 0.9966 | 1 | 1.434e-16 | 0.8923 | 0.9426 | 104,138 | True | True | equity = atq - ltq (total equity incl. minority interest); GuruFocus used seqq |
| calc_tax_expense_quarterly | 21637 | 0.9663 | 0.9957 | 1 | 0 | 0.952 | 0.9726 | 4961 | True | False |  |
| calc_raw_tax_rate_quarterly | 21306 | 0.9518 | 1 | 0.9995 | 0.01292 | 0.4022 | 0.8768 | 121.3 | True | True | never clipped to [0,1] here; GuruFocus rounds to 2dp |
| calc_nopat_quarterly | 20263 | 0.9088 | 0.4563 | 1 | 0.07221 | 0.1792 | 0.4226 | 8,652,006 | False | True | downstream of the ebit definition |
| calc_ic_raw | 15752 | 0.8693 | 0.9823 | 1 | 1.344e-16 | 0.8028 | 0.9335 | 135,236 | True | False |  |
| calc_average_ic_raw_quarterly | 15181 | 0.8469 | 0.9831 | 1 | 1.162e-07 | 0.7798 | 0.9333 | 133,754 | True | False |  |
| calc_roic_pretax_quarterly_ic_raw | 15165 | 0.8463 | 0.515 | 0.994 | 0.108 | 0.06495 | 0.295 | 23.47 | False | True | downstream of ebit; GuruFocus rounds to 2 decimals |
| calc_roic_posttax_quarterly_ic_raw | 14839 | 0.8329 | 0.9078 | 0.9912 | 0.1148 | 0.05998 | 0.2713 | 276.6 | False | True | downstream of ebit; GuruFocus rounds to 2 decimals |
| calc_debt_value_quarterly | 22217 | 0.9907 | 0.9178 | 1 | 1.135e-07 | 0.7686 | 0.8787 | 725,918 | False | True | downstream of short_term_debt (median ratio 1.0000) |
| calc_debt_to_equity_quarterly | 20427 | 0.916 | 0.1163 | 0.9987 | 0.01377 | 0.4461 | 0.7117 | 1.192e+04 | False | True | denominator differs with equity; Pearson is dominated by near-zero-equity outliers while the median relative deviation is 1.4% |
| calc_nopat_change_quarterly | 19338 | 0.8702 | 0.4574 | 0.918 | 0.3425 | 0.09774 | 0.2036 | 8,652,962 | False | True | downstream of ebit; first differences amplify the gap |
| calc_roic_posttax_change_quarterly | 14182 | 0.8069 | 0.9089 | 0.8842 | 0.3886 | 0.0696 | 0.1678 | 276.6 | False | True | downstream of ebit; first differences amplify |
| calc_roic_ebit_contribution | 14182 | 0.8069 | 0.9985 | 0.7997 | 0.4439 | 0.06995 | 0.1622 | 143.5 | False | True | downstream of ebit (correlation 0.999, level ratio 0.80) |
| calc_roic_tax_contribution | 14182 | 0.8069 | 0.931 | 0.9994 | 0.09092 | 0.1302 | 0.3719 | 417 | False | True | downstream of ebit |
| calc_roic_ic_contribution | 14182 | 0.8069 | 0.5844 | 0.9997 | 0.1162 | 0.1209 | 0.332 | 29.35 | False | True | downstream of ebit |


⚠️ **איך לקרוא את הסכמת התוויות למטה:** תווית יכולה להסכים רק במקום שבו הקלט מסכים. זהו הגבול העליון, והוא נמדד:

| input | n_comparable | share_within_1pct | n_only_mine | n_only_gurufocus |
|---|---|---|---|---|
| ebit | 21357 | 0.1811 | 958 | 42 |
| pretax_income | 21475 | 0.9073 | 928 | 17 |
| calc_tax_expense_quarterly | 21637 | 0.952 | 746 | 17 |
| total_current_assets | 18489 | 0.9951 | 174 | 121 |
| total_current_liabilities | 18405 | 0.9944 | 258 | 93 |
| net_ppe | 20322 | 0.7703 | 795 | 485 |
| goodwill | 17832 | 0.9942 | 3346 | 53 |
| ALL of the above within 1% (the label ceiling) | 22638 | 0.1774 |  |  |

כלומר הסכמה של 62%–94% בתוויות היא **מעל** תקרת הקלט, כי התוויות גסות (שלוש מגירות) ומסכימות גם כשהמספרים נבדלים. שלושת הגורמים: הגדרת `ebit`, העובדה ש-GuruFocus מפרסם את היחסים מעוגלים לשתי ספרות, ושורות שבהן לצד אחד יש נתון ולשני אין.

| column | n_overlap | exact_agreement | input_agreement_ceiling | top_disagreements | expected_gap | note |
|---|---|---|---|---|---|---|
| calc_nopat_decomposition_status | 22427 | 0.868 | 0.1774 | VALID->UNCLASSIFIED_MISSING_DATA:2241; VALID->UNCLASSIFIED_NONCONSECUTIVE:647; UNCLASSIFIED_MISSING_DATA->VALID:49; UNCLASSIFIED_MISSING_DATA->UNCLASSIFIED_NONCONSECUTIVE:16; UNCLASSIFIED_NONCONSECUTIVE->UNCLASSIFIED_MISSING_DATA:6 | True | coverage differs; 1,303 of 1,465 flag gaps are one-sided NaN |
| calc_nopat_change_direction | 22427 | 0.7146 | 0.1774 | INCREASE->UNCLASSIFIED:1537; INCREASE->DECREASE:1515; DECREASE->INCREASE:1420; DECREASE->UNCLASSIFIED:1300; INCREASE->STABLE:152 | True | downstream of ebit; near-band rows flip |
| calc_nopat_ebit_effect | 22427 | 0.6979 | 0.1774 | POSITIVE->NEGATIVE:1698; NEGATIVE->POSITIVE:1476; POSITIVE->UNCLASSIFIED:1465; NEGATIVE->UNCLASSIFIED:1348; POSITIVE->NEUTRAL:176 | True | downstream of ebit |
| calc_nopat_tax_effect | 22427 | 0.8116 | 0.1774 | POSITIVE->UNCLASSIFIED:1127; NEGATIVE->UNCLASSIFIED:1060; NEUTRAL->UNCLASSIFIED:701; NEGATIVE->POSITIVE:451; POSITIVE->NEGATIVE:428 | True | downstream of ebit via the contribution weights |
| calc_nopat_effect_combination | 22427 | 0.6639 | 0.1774 | EBIT_POS__TAX_NEG->EBIT_NEG__TAX_NEG:688; EBIT_POS__TAX_POS->EBIT_NEG__TAX_POS:683; EBIT_POS__TAX_NEG->UNCLASSIFIED:597; EBIT_NEG__TAX_POS->EBIT_POS__TAX_POS:595; EBIT_NEG__TAX_POS->UNCLASSIFIED:587 | True | cross-product of the two effects above |
| calc_roic_decomposition_status | 22427 | 0.7978 | 0.1774 | VALID->UNCLASSIFIED_MISSING_DATA:3788; VALID->UNCLASSIFIED_NONCONSECUTIVE:508; UNCLASSIFIED_MISSING_DATA->UNCLASSIFIED_NONCONSECUTIVE:155; UNCLASSIFIED_MISSING_DATA->VALID:76; UNCLASSIFIED_NONCONSECUTIVE->UNCLASSIFIED_MISSING_DATA:8 | True | coverage differs on invested capital |
| calc_roic_posttax_change_direction | 22427 | 0.6929 | 0.1774 | INCREASE->UNCLASSIFIED:2197; DECREASE->UNCLASSIFIED:2063; INCREASE->DECREASE:1177; DECREASE->INCREASE:1170; INCREASE->STABLE:48 | True | downstream of ebit; near-band rows flip |
| calc_roic_ebit_effect | 22427 | 0.6785 | 0.1774 | POSITIVE->UNCLASSIFIED:2291; NEGATIVE->UNCLASSIFIED:1966; POSITIVE->NEGATIVE:1380; NEGATIVE->POSITIVE:1198; NEUTRAL->POSITIVE:92 | True | downstream of ebit |
| calc_roic_tax_effect | 22427 | 0.7626 | 0.1774 | POSITIVE->UNCLASSIFIED:1991; NEGATIVE->UNCLASSIFIED:1916; NEUTRAL->UNCLASSIFIED:389; NEGATIVE->POSITIVE:362; POSITIVE->NEGATIVE:329 | True | downstream of ebit |
| calc_roic_ic_effect | 22427 | 0.7388 | 0.1774 | NEGATIVE->UNCLASSIFIED:2502; POSITIVE->UNCLASSIFIED:1204; NEUTRAL->UNCLASSIFIED:590; POSITIVE->NEGATIVE:429; NEGATIVE->POSITIVE:256 | True | downstream of ebit |
| calc_roic_effect_combination | 22427 | 0.6221 | 0.1774 | EBIT_POS__TAX_NEG__IC_NEG->UNCLASSIFIED:673; EBIT_NEG__TAX_POS__IC_NEG->UNCLASSIFIED:579; EBIT_POS__TAX_POS__IC_NEG->UNCLASSIFIED:570; EBIT_NEG__TAX_NEG__IC_NEG->UNCLASSIFIED:452; EBIT_POS__TAX_POS__IC_NEG->EBIT_NEG__TAX_POS__IC_NEG:311 | True | cross-product of the three effects above |
| calc_ebit_movement | 22427 | 0.7003 | 0.1774 | UP->DOWN:1708; DOWN->UP:1480; UP->UNCLASSIFIED:1473; DOWN->UNCLASSIFIED:1349; UP->FLAT:162 | True | downstream of ebit |
| calc_tax_rate_movement | 22427 | 0.8552 | 0.1774 | DOWN->UNCLASSIFIED:1244; UP->UNCLASSIFIED:1231; FLAT->UNCLASSIFIED:413; UP->DOWN:141; DOWN->UP:135 | True | GuruFocus rates are rounded to 2dp; vendor inputs differ |
| calc_ic_movement | 22427 | 0.7753 | 0.1774 | UP->UNCLASSIFIED:2455; DOWN->UNCLASSIFIED:1342; FLAT->UNCLASSIFIED:499; FLAT->DOWN:129; FLAT->UP:128 | True | coverage and vintage differences in the capital base |
| calc_raw_movement_combination | 22427 | 0.6469 | 0.1774 | EBIT_UP__TAX_UP__IC_UP->UNCLASSIFIED:689; EBIT_UP__TAX_DOWN__IC_UP->UNCLASSIFIED:596; EBIT_DOWN__TAX_DOWN__IC_UP->UNCLASSIFIED:577; EBIT_DOWN__TAX_UP__IC_UP->UNCLASSIFIED:481; EBIT_UP__TAX_UP__IC_DOWN->UNCLASSIFIED:366 | True | cross-product of the three movements above |
| calc_roic_effect_structure | 22427 | 0.637 | 0.1774 | MIXED_NET_INCREASE->UNCLASSIFIED:1716; MIXED_NET_DECREASE->UNCLASSIFIED:1374; MIXED_NET_DECREASE->MIXED_NET_INCREASE:797; MIXED_NET_INCREASE->MIXED_NET_DECREASE:746; ALL_NEGATIVE->UNCLASSIFIED:652 | True | downstream of the three effects |
| calc_roic_dominant_driver | 22427 | 0.6559 | 0.1774 | EBIT->UNCLASSIFIED:2776; TAX->EBIT:1348; TAX->UNCLASSIFIED:1004; BALANCED->EBIT:409; EBIT->TAX:362 | True | downstream of the three contributions |
| calc_roic_dominant_driver_effect | 22427 | 0.6759 | 0.1774 | POSITIVE->UNCLASSIFIED:2393; NEGATIVE->UNCLASSIFIED:2195; POSITIVE->NEGATIVE:1015; NEGATIVE->POSITIVE:995; UNCLASSIFIED->POSITIVE:342 | True | downstream of the dominant driver |
| calc_ebit_sign_regime | 22427 | 0.7979 | 0.1774 | PROFIT_TO_PROFIT->UNCLASSIFIED:2485; PROFIT_TO_PROFIT->PROFIT_TO_LOSS:504; PROFIT_TO_PROFIT->LOSS_TO_PROFIT:482; LOSS_TO_LOSS->UNCLASSIFIED:270; PROFIT_TO_PROFIT->LOSS_TO_LOSS:118 | True | downstream of ebit |
| calc_nopat_sign_regime | 22427 | 0.7968 | 0.1774 | PROFIT_TO_PROFIT->UNCLASSIFIED:2382; PROFIT_TO_PROFIT->PROFIT_TO_LOSS:476; PROFIT_TO_PROFIT->LOSS_TO_PROFIT:450; LOSS_TO_LOSS->UNCLASSIFIED:263; LOSS_TO_PROFIT->UNCLASSIFIED:125 | True | downstream of ebit |
| calc_tax_rate_quality_flag | 22427 | 0.9441 | 0.1774 | VALID->MISSING:902; NEGATIVE_TAX_RATE->MISSING:147; VALID->NEGATIVE_TAX_RATE:73; NEGATIVE_TAX_RATE->VALID:50; ABOVE_100_PERCENT->MISSING:26 | True | 6.5% disagreement, 89% of it one-sided NaN coverage |
| calc_roic_quality_flag | 22427 | 0.7975 | 0.1774 | VALID->UNCLASSIFIED:3129; TAX_RATE_OUT_OF_RANGE->UNCLASSIFIED:1142; VALID->TAX_RATE_OUT_OF_RANGE:89; TAX_RATE_OUT_OF_RANGE->VALID:61; UNCLASSIFIED->VALID:57 | True | downstream of the ROIC status |

פערים הגדרתיים נרשמו **מראש** בקונפיג ומסומנים `expected_gap` — הם תיעוד, לא כשל. הפרה בלי הסבר מוקדם עוצרת את הריצה.
✅ אין הפרת סף בלי הסבר מוקדם.

## 12. מה לא נבנה, ולמה

- FCF, CAPEX והמרת YTD — דורשים `fyearq`/`fqtr`. שלוש העמודות קיימות בסכמה וריקות, והדלקתן בעתיד היא שינוי קונפיג ולא שינוי קוד.
- `calc_enterprise_value_quarterly` אינו ברשימת העמודות ואין לו צורך בלי FCF.
- תשואות, מומנטום, WACC, מיונים וטרסילים — מחוץ להיקף הבנייה הזאת.
- `sector` הוא קוד `gsector` כפי שהוא, בלי תרגום לשמות GICS.

## 13. שחזור


| file | path | exists | bytes | modified | sha256 | unchanged_during_run |
|---|---|---|---|---|---|---|
| input_parquet | C:\Users\User\roic_research\data\data.parquet | True | 73220252 | 2026-10-05T05:25:42.828347921 | 903de7cf6b52802c87f6d0d6f0dd2c7d3c5960df6b7764f22688fd6b20b2a2e8 | True |
| gurufocus_csv | C:\Users\User\roic_research\data\GuruFocus_quarterly_data.csv | True | 24363937 | 2026-09-13T05:13:38.846275806 | 689674efc336d7914b632f6e466e5bd285cc0d5d589317bce413d8e4ae8abeb7 | True |

- `output/logs/run_manifest.json` — גרסאות הספריות, ספירות שורות שלב-אחר-שלב, ו-hashes. `output/logs/config_used.yaml` — עותק בייט של ההגדרות.
