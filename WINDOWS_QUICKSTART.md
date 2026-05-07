# دليل التشغيل السريع — Council of Agents

## الخطوات بالترتيب

### 1️⃣ المتطلبات الأساسية

قبل التشغيل، تأكد من:

- **ويندوز 10/11** (نسخة 64-bit)
- **Python 3.11 أو أحدث** — من [python.org](https://python.org)
  - ⚠️ **مهم جداً**: عند التثبيت اختر "Add Python to PATH"
- **8 GB RAM على الأقل** (16 GB موصى به)
- **20 GB مساحة قرص فارغة** للنماذج

---

### 2️⃣ الإعداد الأولي (مرة واحدة فقط)

افتح **CMD** أو **PowerShell** وانتقل لمجلد المشروع:

```cmd
cd C:\path\to\council_of_agents_v2
```

شغّل سكربت الإعداد:

```cmd
setup_windows.bat
```

السكربت سيقوم بـ:
1. ✅ التحقق من Python
2. ✅ ترقية pip
3. ✅ تثبيت كل المكتبات المطلوبة (يأخذ 5-10 دقائق)
4. ✅ التحقق من Ollama وتحميل النماذج (يأخذ 10-20 دقيقة أول مرة)
5. ✅ إنشاء مجلدات البيانات

> ⚠️ **إذا Ollama غير مثبت**، حمّله من [ollama.com/download](https://ollama.com/download) ثم نفّذ هذي الأوامر يدوياً:
> ```cmd
> ollama pull qwen2.5:7b-instruct-q5_K_M
> ollama pull nomic-embed-text
> ```

---

### 3️⃣ التشغيل اليومي

#### الطريقة المُفضّلة: انقر مرتين على

```
run_as_admin.bat
```

سيطلب منك UAC الموافقة على صلاحيات الإدارة. هذا **ضروري** لرؤية عمليات النظام (svchost, lsass, إلخ).

#### أو من PowerShell:

```powershell
.\run_as_admin.ps1 scan-system
```

#### أو يدوياً (بعد فتح CMD كـ Administrator):

```cmd
python run.py scan-system
```

---

### 4️⃣ الأوامر المتاحة

| الأمر | الوصف |
|------|-------|
| `scan-system` | فحص شامل للنظام (عمليات، ذاكرة، شبكة) |
| `scan-archive <path>` | فحص أرشيف قبل فك ضغطه |
| `verify-audit` | التحقق من سلامة سجل التدقيق |
| `baseline-stats` | عرض إحصائيات التعلم السلوكي |
| `list-quarantine` | عرض الملفات في الحجر |

#### أمثلة:

```cmd
REM فحص النظام
run_as_admin.bat scan-system

REM فحص أرشيف مشبوه
run_as_admin.bat scan-archive "C:\Users\Me\Downloads\suspicious.zip"

REM التحقق من سجل التدقيق
run_as_admin.bat verify-audit
```

---

## ⚠️ تحذيرات مهمة

### لماذا أحتاج Administrator؟

بدون صلاحيات الإدارة:

- ❌ لن ترى عمليات النظام (svchost.exe, lsass.exe, services.exe)
- ❌ لن تستطيع مراقبة اتصالات الشبكة لكل العمليات
- ❌ لن تستطيع نقل ملفات لـ Quarantine في مجلدات النظام
- ❌ WMI queries محدودة

مع صلاحيات الإدارة:

- ✅ رؤية كاملة للعمليات
- ✅ مراقبة شاملة للشبكة
- ✅ القدرة على عزل التهديدات
- ✅ Authenticode signature verification

### Windows Defender

Windows Defender قد يبلّغ عن المشروع لأنه:

1. يستخدم WMI للوصول لمعلومات النظام
2. يحتوي على YARA rules للكشف عن البرمجيات الخبيثة
3. يفك تشفير الأرشيفات للفحص

**الحل**: أضف مجلد المشروع للاستثناءات في Windows Security:

```
Settings → Privacy & security → Windows Security → 
Virus & threat protection → Manage settings → 
Exclusions → Add or remove exclusions → 
Add an exclusion → Folder
```

---

## 🐛 حل المشاكل الشائعة

### المشكلة: `ModuleNotFoundError: No module named 'wmi'`

```cmd
pip install pywin32 wmi --upgrade
python -m win32com.client
```

### المشكلة: `Ollama: connection refused`

تأكد من تشغيل Ollama:

```cmd
REM في نافذة CMD منفصلة، شغّل:
ollama serve
```

أو افحص أنه يعمل:

```cmd
curl http://localhost:11434/api/tags
```

### المشكلة: `No suspicious candidates after pre-filter`

هذا **طبيعي** على نظام نظيف. النظام لا يولّد إنذارات كاذبة بسهولة بفضل:

- **Trust Manager**: يثق في Microsoft Signed binaries
- **Behavioral Baseline**: يتعلم سلوكك الطبيعي
- **Multi-agent voting**: لا يطلق إنذار إلا بإجماع

### المشكلة: التشغيل بطيء

النموذج 7B يحتاج RAM وقت تحميل. الحلول:

1. تأكد إن RAM متاح: `taskmgr` → Memory
2. استخدم نموذج أصغر:
   ```yaml
   # في config/settings.yaml
   primary_model: "qwen2.5:3b-instruct"  # بدلاً من 7b
   ```
3. زد عدد الـ pre-filter candidates لتقليل LLM calls:
   ```yaml
   pre_filter_top_n: 3  # بدلاً من 5
   ```

---

## 📊 ما يحدث وقت الفحص؟

```
1. SystemProbe يجمع 300+ عملية في نصف ثانية
   ↓
2. TrustManager يستبعد Microsoft Signed (يبقى ~30)
   ↓  
3. Pre-filter heuristics: red flags → ~5 مرشح
   ↓
4. BehavioralBaseline يقارن مع السلوك المعتاد
   ↓
5. ResourceWarden يستدعي LLM لكل مرشح (5-10 ثواني/مرشح)
   ↓ (لو فشل LLM → HeuristicEngine fallback)
6. CyberAnalyst + TrafficObserver بشكل متوازي
   ↓
7. CrossReference: ربط الأدلة بين الوكلاء
   ↓
8. Arbitrator يقرر بناءً على الأدلة + Voting
   ↓
9. CouncilDecision: عربي + إنجليزي + Technical Report
```

---

## 🔐 الأمان

كل البيانات **محلية بالكامل**:

- ❌ لا يرسل أي شيء لـ cloud
- ❌ لا يتصل بـ Internet (إلا للأرشيف الموجود محلياً)
- ✅ مفاتيح التشفير مولّدة ومحفوظة محلياً
- ✅ Quarantine مشفّرة بـ AES-256-GCM
- ✅ Audit log غير قابل للتعديل (hash chain)

---

## 📞 المساعدة

عند مواجهة مشكلة:

1. تحقق من سجلات التشغيل: `logs/council_*.log`
2. شغّل مع verbose logging:
   ```cmd
   set LOGURU_LEVEL=DEBUG
   run_as_admin.bat scan-system
   ```
3. تحقق من Ollama: `ollama list`
4. تحقق من إصدارات المكتبات: `pip list | findstr "langchain langgraph pydantic"`
