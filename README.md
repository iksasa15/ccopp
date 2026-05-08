# مجلس الوكلاء — Council of Agents v0.2.1

نظام أمني محلي بالكامل يعتمد على وكلاء ذكاء اصطناعي متعاونين لحماية أجهزة ويندوز.

A local-first multi-agent AI security system for Windows. Acts as a mini-SOC running entirely offline.

> **⚡ بدء سريع للويندوز**: راجع [WINDOWS_QUICKSTART.md](WINDOWS_QUICKSTART.md)

---

## ⚠️ مهم: التشغيل كـ Administrator

النظام **يحتاج صلاحيات Administrator** لـ:
- رؤية عمليات النظام (svchost, lsass, services)
- مراقبة اتصالات الشبكة الكاملة
- نقل ملفات لـ Quarantine في مجلدات النظام

**أسهل طريقة**: انقر مرتين على `run_as_admin.bat` — سيطلب UAC تلقائياً.

---

## ما الجديد في v0.2.1

### 🐛 إصلاحات حرجة (مكتشفة عند الاختبار الفعلي):

1. **`ResourceWarden` أُعيد بناؤه بالكامل** ليتوافق مع v0.2 stack (Pydantic + resilience + heuristic fallback)
2. **اكتشاف صلاحيات Administrator** مع تحذير واضح للمستخدم
3. **`SystemProbe` مُحسّن**: lazy WMI client، CPU warmup، per-process error isolation
4. **LangGraph parallel updates**: تم إصلاح `InvalidUpdateError` باستخدام `Annotated` reducers
5. **Agent Registry خارجي**: حل مشكلة msgpack serialization (agent objects خارج state)
6. **POLYGLOT_PATTERNS موسّع**: من 6 إلى 25 نمط (تم اكتشاف الثغرة في الاختبار)
7. **CLI fixes**: `compression_ratio`, `name`/`details` keys

### ✨ الميزات الكاملة

#### 🛡️ التحقق والصمود
- **Pydantic schemas** صارمة لكل LLM outputs
- **Self-correction loop**: عند فشل JSON، نطلب من LLM التصحيح
- **Circuit Breaker + Retry + Timeout** لكل LLM call
- **Heuristic Fallback**: عند فشل LLM، نرجع لقواعد منطقية تلقائياً

#### 🤖 التنسيق متعدد الوكلاء
- **LangGraph StateGraph** مع `Annotated` reducers للتحديثات المتوازية
- **Cross-reference**: PID/ملف يظهر عند 2+ وكلاء = +0.2 confidence
- **Arbitrator**: قائد المجلس بإخراج عربي وإنجليزي
- **Iterative deliberation** (حد أقصى 3 جولات)

#### 🧠 الذكاء
- **Trust Manager**: قائمة Microsoft + Adobe + Google ناشرين موثوقين
- **Impersonation Detection**: `svchost.exe` خارج `System32` = malicious
- **Behavioral Baseline**: يتعلم سلوكك خلال 5 أيام (z-score anomalies)
- **Reputation Engine** (SQLite): تاريخ كل ملف على جهازك

#### 🔐 الأمان والتخزين
- **SQLAlchemy async ORM**: ScanHistory, Findings, Quarantine, Audit
- **Tamper-evident Audit Log**: hash chain (مقاوم للتلاعب)
- **Encrypted Quarantine**: AES-256-GCM لكل ملف
- **Secure Delete**: zeros + random overwrite passes

#### 🔔 الإشعارات الذكية
- Multi-channel: Toast, tray, in-app, WebSocket, sound
- Severity-based throttling
- Quiet hours (22:00 - 07:00) مع override للحرج
- Digest mode للإشعارات الأقل خطورة

#### 🔌 التوسع
- **Plugin Registry**: 3 طرق لإضافة وكيل (decorator, entry_points, YAML)

---

## التثبيت السريع

### ويندوز (موصى به):

```cmd
REM 1. نزّل واستخرج المشروع
REM 2. شغّل الإعداد:
setup_windows.bat

REM 3. شغّل النظام:
run_as_admin.bat scan-system
```

### يدوياً:

```bash
pip install -r requirements.txt
ollama pull qwen2.5:7b-instruct-q5_K_M
ollama pull nomic-embed-text
```

---

## الأوامر

```bash
python run.py scan-system            # فحص شامل للنظام
python run.py scan-archive <path>    # فحص أرشيف قبل فك الضغط
python run.py verify-audit           # التحقق من سلامة سجل التدقيق
python run.py baseline-stats         # عرض إحصائيات التعلم
python run.py list-quarantine        # عرض الملفات في الحجر
```

---

## Merged Mode — Council + COA (`COA/COA_Project`)

يعمل المشروع الحالي (LangGraph + FastAPI) بجانب **COA** (Flask + React خاص به) دون دمج تبعيات Python في venv واحد. الواجهة الموحّدة في [`web/`](web/) تستخدم Vite كـ reverse proxy:

| الخدمة | المنفذ | الوصف |
|--------|--------|--------|
| Council FastAPI | **8765** | `uvicorn api.app:app` — مسارات `/api/*` |
| COA Flask (`web_api.py`) | **5050** | من مجلد `COA/COA_Project` — مسارات `/api/*` داخل Flask |
| واجهة Vite الموحّدة | **5173** | `npm run dev` داخل `web/` — يوجّه `/api` → 8765 و `/coa-api` → 5050 |

### إعداد سريع

```bash
cp .env.example .env   # اختياري
make setup             # تثبيت council + COA venv + npm في web/
make dev               # يشغّل 8765 + 5050 + 5173 (Ctrl+C يوقف الخلفيات)
```

أو يدوياً من جذر المستودع:

```bash
bash scripts/start_merged.sh
```

لإيقاف الخدمات الخلفية:

```bash
make stop
# أو
bash scripts/stop_merged.sh
```

### واجهة المتصفح

افتح **http://127.0.0.1:5173** — تبويب **Council** لأوامر المشروع الحالي، **COA** لمسارات Flask عبر `/coa-api/*`، **الحالة** لجلب `GET /api/integrations` و `GET /api/coa/health-proxy`.

### ملاحظات

- COA يحتاج **venv** داخل `COA/COA_Project` (أو `.venv`)؛ إن لم يوجد، ثبّت يدوياً: `cd COA/COA_Project && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt`
- نماذج Ollama قد تختلف بين المشروعين؛ راجع `config/settings.yaml` و `COA/COA_Project/.env`
- `gui.py` (Tkinter) اختياري على macOS وقد يحتاج Python مبني مع Tk

---

## نتائج الاختبارات

```
============================ 49 passed in 0.74s ============================

✅ Unit tests:        35/35
✅ Adversarial tests: 14/14
✅ End-to-end:        all CLI commands verified
```

---

## هيكل المشروع

```
council_of_agents_v2/
├── core/
│   ├── council_graph.py         # LangGraph state machine (TypedDict + Annotated)
│   └── agent_registry.py        # Non-serializable agent storage
│
├── agents/
│   ├── resource_warden.py       # Process monitor (v0.2 stack)
│   └── arbitrator.py            # Council leader
│
├── validation/
│   ├── schemas.py               # Pydantic models
│   └── validator.py             # LLM output self-correction
│
├── resilience/
│   ├── primitives.py            # CircuitBreaker, retry, timeout, bulkhead
│   └── heuristic_fallback.py    # Rule-based fallback
│
├── intelligence/
│   ├── trust_manager.py         # Whitelist + impersonation detection
│   ├── behavioral_baseline.py   # Per-device learning
│   └── reputation.py            # File history (SQLite)
│
├── persistence/
│   └── models.py                # SQLAlchemy async ORM
│
├── security/
│   ├── audit_log.py             # Hash-chained audit
│   └── quarantine.py            # AES-256-GCM
│
├── notifications/
│   └── manager.py               # Multi-channel + throttling
│
├── plugins/
│   └── registry.py              # Dynamic agent discovery
│
├── tools/
│   ├── system_probe.py          # psutil + WMI + admin detection
│   └── archive_inspector.py     # Pre-extraction zip/rar/7z scanner
│
├── tests/
│   ├── unit/                    # 35 tests
│   └── adversarial/             # 14 simulated attacks
│
├── config/
│   └── settings.yaml
│
├── data/                        # Runtime data (created at startup)
├── logs/
│
├── api/                         # FastAPI للواجهة الموحّدة (8765)
├── web/                         # React + Vite (5173، proxy لـ Council + COA)
├── COA/COA_Project/             # مشروع COA (Flask 5050، اختياري)
├── Makefile                   # setup / dev / build / test / clean / stop
├── scripts/start_merged.sh    # تشغيل الدمج (8765+5050+5173)
├── scripts/stop_merged.sh
├── .env.example
├── run.py                       # CLI entry point
├── run_as_admin.bat             # Windows launcher (UAC)
├── run_as_admin.ps1             # PowerShell launcher (UAC)
├── setup_windows.bat            # First-time setup
├── requirements.txt
├── README.md                    # هذا الملف
└── WINDOWS_QUICKSTART.md        # دليل البدء السريع للويندوز
```

---

## الترخيص

MIT — للاستخدام الشخصي والتعليمي.
# ccopp
