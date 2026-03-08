Gmail Job Agent

סורק את Gmail, מזהה מיילי משרות, מבצע התאמה לפי קורות חיים (DOCX), מדרג הזדמנויות ומציג Dashboard אינטראקטיבי.

יכולות עיקריות

✔ סינון רעש (LinkedIn שאינו משרה וכו')
✔ ניקוד לפי 3 מסלולים: IT / תפעול / אחזקה
✔ התאמה חכמה לפי קובצי DOCX
✔ חישוב ציון סופי (final_score)
✔ הסבר מילולי להתאמה (match_reasons)
✔ Dashboard גרפי עם TOP 10 הזדמנויות

הרצה מלאה (Windows)
1. הפעלה אוטומטית

לחיצה כפולה על:

run_job_agent.bat

או ידנית:

cd /d "%USERPROFILE%\OneDrive\Documents\gmail-job-agent"
.venv\Scripts\activate
python 02_scan_jobs.py
streamlit run 03_dashboard.py
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
קבצי קו״ח

המערכת טוענת קובצי DOCX מתוך התיקייה:

קורות חיים - IT.docx

קורות חיים - תפעול.docx

שלום חכמון - אחזקה.docx

קורות חיים - כללי.docx

פרופיל.docx

שמות חייבים להיות זהים לשמות בקוד.

Dashboard
streamlit run 03_dashboard.py

ה-Dashboard כולל:

KPI לפי final_score

גרף TOP 10 הזדמנויות

סינון לפי מסלול

סף מינימלי לציון

קישור ישיר ל-Gmail