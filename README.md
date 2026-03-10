Gmail Job Agent

סורק את Gmail, מזהה מיילי משרות, מבצע התאמה לפי קורות חיים (DOCX), מדרג הזדמנויות ומציג Dashboard אינטראקטיבי.

יכולות עיקריות

✔ סינון רעש (LinkedIn שאינו משרה וכו')
✔ ניקוד לפי 3 מסלולים: IT / תפעול / אחזקה
✔ התאמה חכמה לפי קובצי DOCX
✔ חישוב ציון סופי (final_score)
✔ הסבר מילולי להתאמה (match_reasons)
✔ Dashboard גרפי עם TOP 10 הזדמנויות

התקנה והגדרה ראשונית

1. שכפל את הפרויקט

```
git clone https://github.com/nanoo26/gmail-job-agent.git
cd gmail-job-agent
```

2. צור סביבה וירטואלית והתקן תלויות

```
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Mac/Linux
pip install -r requirements.txt
```

3. הגדר אימות Gmail

- עבור ל-[Google Cloud Console](https://console.cloud.google.com/) וצור פרויקט חדש
- הפעל את Gmail API
- צור OAuth 2.0 Client ID (Desktop app) והורד את הקובץ בשם `client_secret.json` לתיקיית הפרויקט
- הרץ: `python 01_auth.py`

4. הגדר קובץ `.env` (אופציונלי)

```
copy .env.example .env
```

ערוך את `.env` לפי הצורך (ראה הסברים בתוכו).

5. הכן קבצי קורות חיים

צור תיקייה `cvs` בתוך ספריית הפרויקט והוסף קבצי DOCX / PDF בשמות הבאים:

| קובץ | מסלול |
|------|-------|
| `קורות חיים - IT.docx` | IT |
| `קורות חיים - IT.pdf` | IT (אופציונלי) |
| `קורות חיים - תפעול.docx` | תפעול |
| `קורות חיים - תפעול.pdf` | תפעול (אופציונלי) |
| `קורות חיים - אחזקה.docx` | אחזקה |

ניתן לשנות את נתיב התיקייה על-ידי הגדרת `CV_DIR` ב-.env.

הרצה מלאה (Windows)

לחיצה כפולה על:

```
run_job_agent.bat
```

או ידנית:

```
.venv\Scripts\activate
python 02_scan_jobs.py
streamlit run 03_dashboard.py
```

פלט CSV
עמודה	תיאור
date	תאריך
from	שולח
subject	נושא
snippet	תקציר
it_score	ציון IT
ops_score	ציון תפעול
maint_score	ציון אחזקה
best_track	המסלול המוביל
cv_recommendation	קו״ח מומלץ
top_score	הציון הגבוה מבין המסלולים
cv_boost	בונוס התאמה לקו״ח
final_score	ציון סופי (top_score + cv_boost)
match_reasons	מילים חופפות בין משרה לקו״ח
link	קישור למייל

Dashboard

```
streamlit run 03_dashboard.py
```

ה-Dashboard כולל:

KPI לפי final_score

גרף TOP 10 הזדמנויות

סינון לפי מסלול

סף מינימלי לציון

קישור ישיר ל-Gmail