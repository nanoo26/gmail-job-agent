---
name: Gmail Job Agent - Project Context
description: Context and mandatory workflow for the Gmail Job Agent project
---

## 🎯 מטרת הפרויקט

לסרוק מיילים מ-Gmail, לזהות הזדמנויות עבודה אמיתיות, לחשב התאמה לפי 3 מסלולים (IT / תפעול / אחזקה) + התאמה לפי קובצי DOCX, ולדרג לפי final_score.  
להציג תוצאות ב-CSV וב-Dashboard אינטראקטיבי.

---

## ⚙️ עקרונות עבודה

- שפה: כל הטקסטים, UI, הודעות ודוחות – בעברית בלבד.
- אין להמציא תוצאות או פלט. אם חסר קובץ/עמודה/נתון → מדווחים.
- כל שינוי מחייב:
  1) עדכון קוד
  2) בדיקת הרצה
  3) עדכון README אם השתנה פלט/יכולת

---

## 🧠 לוגיקת מוצר (אסור לשבור)

- final_score הוא המדד הראשי בכל מקום:
  KPI, סינון, מיון, גרפים, TOP.
- top_score הוא פנימי בלבד.
- match_reasons חייב להיות טקסט קריא (מילים חופפות) ולא מידע טכני.
- אם עמודות חסרות ב-CSV → להוסיף defaults ולא לקרוס.
- הקוד חייב לעבוד ב-Windows CMD.

---

## 📂 קבצים מרכזיים בפרויקט

- 01_auth.py
- 02_scan_jobs.py
- 03_dashboard.py
- cv_loader.py
- job_emails.csv
- README.md

---

## 📄 קובצי קו״ח

אם קיימים בתיקייה, חובה לטעון:
- פרופיל.docx
- קורות חיים - כללי.docx
- קבצי המסלולים הייעודיים (IT / תפעול / אחזקה)

אין להתעלם מקובץ קיים בלי דיווח.

---

## ✅ Checklist לפני סיום (חובה)

לפני שאתה כותב "Done" חובה לבצע ולהציג:

1) הרצה:
   python 02_scan_jobs.py  
   לוודא שנוצר job_emails.csv עם עמודות:
   it_score, ops_score, maint_score, best_track,
   cv_recommendation, top_score, cv_boost,
   final_score, match_reasons, link

2) Dashboard:
   streamlit run 03_dashboard.py  
   לוודא שכל KPI / סינון / גרפים עובדים לפי final_score

3) קו"ח:
   לוודא ש-cv_loader.py טוען בפועל גם:
   פרופיל.docx וגם קורות חיים - כללי.docx (אם קיימים)
   אם קובץ חסר → לדווח במפורש.

4) תיעוד:
   אם נוספו עמודות או פיצ'רים → לעדכן README.md

---

## 📌 בסיום חובה להציג:

✅ אילו פקודות הורצו  
✅ מה יצא (קובץ/מסך/פלט)  
✅ אילו קבצים שונו  