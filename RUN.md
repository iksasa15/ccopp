# RUN — تشغيل المنصّة محلياً

دليل سريع لتشغيل **COA SOC Console** (Council FastAPI + COA Flask + واجهة React موحّدة).

---

## 1) المتطلّبات

- **Python 3.11+**
- **Node.js 18+** و**npm**
- **Ollama** يعمل محلياً مع موديل (مثلاً `qwen2.5:7b-instruct-q5_K_M`)
- **macOS / Linux** (للويندوز راجع `WINDOWS_QUICKSTART.md`)

تأكّد من Ollama:

```bash
ollama serve            # في تيرمنال منفصل
ollama pull qwen2.5:7b-instruct-q5_K_M
```

---

## 2) التثبيت لأول مرّة

من جذر المشروع:

```bash
make setup
```

ما يفعله:

- `pip install -r requirements.txt` (Council).
- `pip install -r COA/COA_Project/requirements.txt` داخل venv الـ COA.
- `npm install` داخل `web/`.

> إذا لم يكن لـ COA venv: `cd COA/COA_Project && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt`.

---

## 3) التشغيل اليومي

```bash
make dev
```

يشغّل **3 خدمات معاً**:

| الخدمة          | المنفذ | الرابط                          |
| --------------- | ------ | ------------------------------- |
| Council FastAPI | 8765   | http://127.0.0.1:8765/api/health |
| COA Flask       | 5050   | http://127.0.0.1:5050/api/health |
| Vite UI الموحّدة | 5173   | http://127.0.0.1:5173            |

افتح المتصفح على **http://127.0.0.1:5173** وستجد:

- **لوحة المعلومات** (Risk Score, KPIs, سجل الفحوصات)
- **فحص النظام** (سريع / عميق LLM + Agent)
- **أدوات Council** (verify-audit, baseline, quarantine, scan-archive)
- **أدوات COA** (full scan, defense/MITRE/OT, تنزيل التقارير)
- **النتيجة** (تظهر تلقائياً بعد كل فحص)

---

## 4) إيقاف الخدمات

```bash
make stop
```

أو ضغطة `Ctrl+C` داخل تيرمنال `make dev`.

---

## 5) أوامر مفيدة

| الأمر          | الوصف                              |
| -------------- | ---------------------------------- |
| `make dev`     | تشغيل كل الخدمات                   |
| `make setup`   | تثبيت كل التبعيات                  |
| `make build`   | بناء الواجهة للإنتاج (`web/dist/`) |
| `make test`    | تشغيل اختبارات pytest              |
| `make stop`    | إيقاف Council + COA                |
| `make clean`   | حذف `__pycache__` و`web/dist`      |

---

## 6) تشغيل يدوي (بدون Make)

```bash
# 1) Council FastAPI
uvicorn api.app:app --host 127.0.0.1 --port 8765

# 2) COA Flask (تيرمنال آخر)
cd COA/COA_Project && ./venv/bin/python3 web_api.py

# 3) Vite UI (تيرمنال ثالث)
cd web && npm run dev
```

أو السكريبت الموحّد:

```bash
bash scripts/start_merged.sh
```

---

## 7) استكشاف الأعطال

| العَرَض                                  | الحل                                                                 |
| ---------------------------------------- | -------------------------------------------------------------------- |
| الواجهة تقول «LLM غير متصل»              | تأكّد أن `ollama serve` شغّال وأن الموديل مسحوب (`ollama list`).      |
| `make dev` يتعلّق على «Waiting for Council» | أعد تشغيل الترمنال، تأكّد أن المنفذ 8765 ليس مستخدَماً.                 |
| الفحص العميق يعطي 0 تهديدات              | طبيعي على نظام نظيف؛ راجع بطاقة `deep_file_analysis` في صفحة النتيجة. |
| فشل `git push` بسبب ملفات داتا           | الـ `.gitignore` يستثني `data/datasets/` و`*.csv` فلا تتجاهله.        |
| المنفذ 5050 محجوز                        | `lsof -i :5050` ثم `kill <PID>` أو غيّر `COA_FLASK_URL`.             |

---

## 8) متغيّرات بيئة اختيارية (`.env` في الجذر)

```bash
# مسار مشروع COA إذا كان خارج المسار الافتراضي
COA_PROJECT_DIR=/absolute/path/to/COA/COA_Project

# عنوان COA Flask (الافتراضي: http://127.0.0.1:5050)
COA_FLASK_URL=http://127.0.0.1:5050
```

---

## ملاحظات

- بعد الفحص، الواجهة **تنتقل تلقائياً** إلى تبويب «النتيجة» وتعرض ملخّصاً (وقت + وضع + مدّة + Scan ID).
- سجل الفحوصات يُحفظ في `localStorage` للمتصفح فقط (يمكن مسحه من الـ Dashboard).
- لا يحتاج المشروع إنترنت لتشغيل Ollama، كل التحليل LLM **محلي**.
