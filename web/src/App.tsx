import { useCallback, useState } from "react";
import "./App.css";

type Status = "idle" | "loading" | "error";

async function parseJsonSafe(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

export default function App() {
  const [archivePath, setArchivePath] = useState("");
  const [output, setOutput] = useState<string>("");
  const [status, setStatus] = useState<Status>("idle");
  const [lastError, setLastError] = useState<string | null>(null);

  const showResult = useCallback((data: unknown) => {
    setOutput(JSON.stringify(data, null, 2));
  }, []);

  const run = useCallback(
    async (
      label: string,
      fn: () => Promise<Response>
    ) => {
      setStatus("loading");
      setLastError(null);
      setOutput(`… جاري التنفيذ: ${label}`);
      try {
        const res = await fn();
        const body = await parseJsonSafe(res);
        if (!res.ok) {
          setStatus("error");
          setLastError(`${res.status} ${res.statusText}`);
          showResult(body);
          return;
        }
        setStatus("idle");
        showResult(body);
      } catch (e) {
        setStatus("error");
        const msg = e instanceof Error ? e.message : String(e);
        setLastError(msg);
        showResult({ error: msg, hint: "تأكد أن الخادم يعمل: uvicorn api.app:app --port 8765" });
      }
    },
    [showResult]
  );

  return (
    <div className="app">
      <header className="hero">
        <h1>مجلس الوكلاء</h1>
        <p className="subtitle">
          تشغيل أوامر الفحص من الواجهة بدل الطرفية. يتطلب خادم API محلي على المنفذ{" "}
          <code>8765</code> وواجهة Vite على <code>5173</code> (الوكيل يوجّه{" "}
          <code>/api</code> تلقائياً).
        </p>
      </header>

      <section className="actions">
        <button
          type="button"
          className="btn primary"
          disabled={status === "loading"}
          onClick={() =>
            run("فحص النظام", () =>
              fetch("/api/scan-system", { method: "POST" })
            )
          }
        >
          فحص النظام — scan-system
        </button>

        <button
          type="button"
          className="btn"
          disabled={status === "loading"}
          onClick={() =>
            run("التحقق من التدقيق", () => fetch("/api/verify-audit"))
          }
        >
          verify-audit
        </button>

        <button
          type="button"
          className="btn"
          disabled={status === "loading"}
          onClick={() =>
            run("إحصائيات baseline", () => fetch("/api/baseline-stats"))
          }
        >
          baseline-stats
        </button>

        <button
          type="button"
          className="btn"
          disabled={status === "loading"}
          onClick={() =>
            run("قائمة الحجر", () => fetch("/api/list-quarantine"))
          }
        >
          list-quarantine
        </button>

        <button
          type="button"
          className="btn ghost"
          disabled={status === "loading"}
          onClick={() => run("مساعدة الأوامر", () => fetch("/api/commands"))}
        >
          أوامر API (مساعدة)
        </button>
      </section>

      <section className="archive-row">
        <label htmlFor="archive-path">مسار الأرشيف — scan-archive</label>
        <div className="archive-input">
          <input
            id="archive-path"
            type="text"
            placeholder="/مسار/كامل/إلى/file.zip"
            value={archivePath}
            onChange={(e) => setArchivePath(e.target.value)}
            dir="ltr"
          />
          <button
            type="button"
            className="btn primary"
            disabled={status === "loading" || !archivePath.trim()}
            onClick={() =>
              run("فحص الأرشيف", () =>
                fetch("/api/scan-archive", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ path: archivePath.trim() }),
                })
              )
            }
          >
            فحص الأرشيف
          </button>
        </div>
      </section>

      {lastError && (
        <p className="banner error" role="alert">
          خطأ: {lastError}
        </p>
      )}

      <section className="output-panel">
        <div className="output-head">
          <span>النتيجة (JSON)</span>
          {status === "loading" && <span className="pulse">جاري التحميل…</span>}
        </div>
        <pre className="json-out" dir="ltr">
          {output || "// اضغط أحد الأزرار أعلاه"}
        </pre>
      </section>
    </div>
  );
}
