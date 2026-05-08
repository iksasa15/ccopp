import { useCallback, useMemo, useState } from "react";
import { BrandLogo, StatusPill } from "./Brand";
import "./App.css";

type LoadStatus = "idle" | "loading" | "error";
type MainTab = "council" | "coa" | "status";
type JsonRecord = Record<string, unknown>;
type ResultState =
  | { kind: "idle"; message: string }
  | { kind: "loading"; message: string }
  | { kind: "success"; data: unknown }
  | { kind: "error"; data: unknown };

function formatPrimitive(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "نعم" : "لا";
  if (typeof value === "number") return value.toLocaleString("en-US");
  if (typeof value === "string") return value;
  return "بيانات مركبة";
}

function severityClass(value: unknown): string {
  const v = String(value ?? "").toLowerCase();
  if (v.includes("critical")) return "is-critical";
  if (v.includes("high")) return "is-high";
  if (v.includes("medium")) return "is-medium";
  if (v.includes("low")) return "is-low";
  return "is-neutral";
}

async function parseJsonSafe(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

function filenameFromContentDisposition(cd: string | null): string | null {
  if (!cd) return null;
  const m = cd.match(/filename\*?=(?:UTF-8'')?["']?([^"';]+)/i);
  return m ? decodeURIComponent(m[1].replace(/['"]/g, "")) : null;
}

function getKnownCoaHint(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const err = String((body as JsonRecord).error ?? "").toLowerCase();
  if (err.includes("run a scan first")) {
    return "لا يوجد Scan سابق في COA. نفّذ أولاً: «فحص COA (POST /scan)» ثم أعد المحاولة.";
  }
  return null;
}

export default function App() {
  const [mainTab, setMainTab] = useState<MainTab>("council");
  const [archivePath, setArchivePath] = useState("");
  const [result, setResult] = useState<ResultState>({
    kind: "idle",
    message: "اختر تبويباً ثم نفّذ إجراءً",
  });
  const [loadStatus, setLoadStatus] = useState<LoadStatus>("idle");
  const [lastError, setLastError] = useState<string | null>(null);

  const [coaDryRun, setCoaDryRun] = useState(true);
  const [coaUseCouncil, setCoaUseCouncil] = useState(false);
  const [coaPresentationDemo, setCoaPresentationDemo] = useState(false);

  const showResult = useCallback((data: unknown) => {
    setResult((prev) => ({
      kind: prev.kind === "error" ? "error" : "success",
      data,
    }));
  }, []);

  const resultEntries = useMemo(() => {
    if (result.kind !== "success" && result.kind !== "error") return [];
    if (!result.data || typeof result.data !== "object") return [];
    return Object.entries(result.data as JsonRecord);
  }, [result]);

  const resultVariant =
    result.kind === "error" ? "danger" : result.kind === "success" ? "ok" : "neutral";

  const runJson = useCallback(
    async (label: string, fn: () => Promise<Response>, hint?: string) => {
      setLoadStatus("loading");
      setLastError(null);
      setResult({ kind: "loading", message: `جاري التنفيذ: ${label}` });
      try {
        const res = await fn();
        const body = await parseJsonSafe(res);
        if (!res.ok) {
          setLoadStatus("error");
          const knownHint = getKnownCoaHint(body);
          setLastError(knownHint ?? `${res.status} ${res.statusText}`);
          setResult({
            kind: "error",
            data: knownHint ? { ...(body as JsonRecord), user_hint: knownHint } : body,
          });
          showResult(
            knownHint ? { ...(body as JsonRecord), user_hint: knownHint } : body
          );
          return;
        }
        setLoadStatus("idle");
        showResult(body);
      } catch (e) {
        setLoadStatus("error");
        const msg = e instanceof Error ? e.message : String(e);
        setLastError(msg);
        showResult({
          error: msg,
          hint:
            hint ??
            "تأكد: uvicorn على 8765، و COA Flask على 5050 (مثلاً bash scripts/start_merged.sh)",
        });
      }
    },
    [showResult]
  );

  const runDownload = useCallback(
    async (label: string, url: string) => {
      setLoadStatus("loading");
      setLastError(null);
      setResult({ kind: "loading", message: `جاري تنزيل: ${label}` });
      try {
        const res = await fetch(url);
        if (!res.ok) {
          const body = await parseJsonSafe(res);
          setLoadStatus("error");
          const knownHint = getKnownCoaHint(body);
          setLastError(knownHint ?? `${res.status} ${res.statusText}`);
          setResult({
            kind: "error",
            data: knownHint ? { ...(body as JsonRecord), user_hint: knownHint } : body,
          });
          showResult(
            knownHint ? { ...(body as JsonRecord), user_hint: knownHint } : body
          );
          return;
        }
        const blob = await res.blob();
        const name =
          filenameFromContentDisposition(res.headers.get("content-disposition")) ??
          "COA_report.bin";
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = name;
        a.click();
        URL.revokeObjectURL(a.href);
        setLoadStatus("idle");
        showResult({
          ok: true,
          downloaded: name,
          bytes: blob.size,
          note: "تم بدء التنزيل في المتصفح",
        });
      } catch (e) {
        setLoadStatus("error");
        const msg = e instanceof Error ? e.message : String(e);
        setLastError(msg);
        setResult({ kind: "error", data: { error: msg } });
        showResult({ error: msg });
      }
    },
    [showResult]
  );

  return (
    <div className="app">
      <header className="brand-bar">
        <div className="brand-bar__left">
          <BrandLogo size={56} />
          <div className="brand-text">
            <h1>
              مجلس الوكلاء <span className="brand-sep">·</span> COA
            </h1>
            <p className="brand-tag">
              منصة SOC محلية متعددة الوكلاء — Local Multi-Agent SOC
            </p>
            <p className="subtitle brand-sub">
              <strong>Council</strong> عبر <code>/api</code> → FastAPI{" "}
              <code>8765</code>. <strong>COA</strong> عبر <code>/coa-api</code> → Flask{" "}
              <code>5050</code>. شغّل <code>make dev</code> أو{" "}
              <code>bash scripts/start_merged.sh</code>.
            </p>
          </div>
        </div>
        <div className="brand-status" aria-label="خدمات المنصة">
          <StatusPill kind="ok" label="FastAPI 8765" />
          <StatusPill kind="ok" label="COA 5050" />
        </div>
      </header>

      <nav className="tabs" role="tablist" aria-label="أقسام التطبيق">
        <button
          type="button"
          role="tab"
          aria-selected={mainTab === "council"}
          className={`tab ${mainTab === "council" ? "active" : ""}`}
          onClick={() => setMainTab("council")}
        >
          Council
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mainTab === "coa"}
          className={`tab ${mainTab === "coa" ? "active" : ""}`}
          onClick={() => setMainTab("coa")}
        >
          COA
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mainTab === "status"}
          className={`tab ${mainTab === "status" ? "active" : ""}`}
          onClick={() => setMainTab("status")}
        >
          الحالة / التكامل
        </button>
      </nav>

      {mainTab === "council" && (
        <>
          <section className="actions">
            <button
              type="button"
              className="btn primary"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("فحص النظام (Council)", () =>
                  fetch("/api/scan-system", { method: "POST" })
                )
              }
            >
              فحص النظام — scan-system
            </button>
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("verify-audit", () => fetch("/api/verify-audit"))
              }
            >
              verify-audit
            </button>
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("baseline-stats", () => fetch("/api/baseline-stats"))
              }
            >
              baseline-stats
            </button>
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("list-quarantine", () => fetch("/api/list-quarantine"))
              }
            >
              list-quarantine
            </button>
            <button
              type="button"
              className="btn ghost"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("مساعدة الأوامر", () => fetch("/api/commands"))
              }
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
                placeholder="/path/to/archive.zip"
                value={archivePath}
                onChange={(e) => setArchivePath(e.target.value)}
                dir="ltr"
              />
              <button
                type="button"
                className="btn primary"
                disabled={loadStatus === "loading" || !archivePath.trim()}
                onClick={() =>
                  runJson("فحص الأرشيف", () =>
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
        </>
      )}

      {mainTab === "coa" && (
        <section className="coa-panel">
          <p className="coa-hint">
            يتطلب تشغيل <code>web_api.py</code> على المنفذ <code>5050</code>. بعد{" "}
            <strong>فحص COA</strong> يمكن جلب defense / MITRE / OT من آخر مسح، أو تنزيل التقارير.
          </p>
          <div className="coa-options">
            <label className="chk">
              <input
                type="checkbox"
                checked={coaDryRun}
                onChange={(e) => setCoaDryRun(e.target.checked)}
              />
              dry_run
            </label>
            <label className="chk">
              <input
                type="checkbox"
                checked={coaUseCouncil}
                onChange={(e) => setCoaUseCouncil(e.target.checked)}
              />
              use_council (CrewAI — أبطأ)
            </label>
            <label className="chk">
              <input
                type="checkbox"
                checked={coaPresentationDemo}
                onChange={(e) => setCoaPresentationDemo(e.target.checked)}
              />
              presentation_demo (OT وهمي)
            </label>
          </div>
          <div className="actions">
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("COA health", () => fetch("/coa-api/health"), "شغّل COA Flask على 5050")
              }
            >
              COA /api/health
            </button>
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("Ollama diagnose", () =>
                  fetch("/coa-api/health/ollama")
                )
              }
            >
              health/ollama
            </button>
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("LLM diagnose", () => fetch("/coa-api/health/llm"))
              }
            >
              health/llm
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("COA full scan", () =>
                  fetch("/coa-api/scan", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                      dry_run: coaDryRun,
                      use_council: coaUseCouncil,
                      presentation_demo: coaPresentationDemo,
                    }),
                  })
                )
              }
            >
              فحص COA (POST /scan)
            </button>
          </div>
          <div className="actions">
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("آخر defense-context", () =>
                  fetch("/coa-api/last/defense-context")
                )
              }
            >
              last/defense-context
            </button>
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("آخر mitre-deep", () => fetch("/coa-api/last/mitre-deep"))
              }
            >
              last/mitre-deep
            </button>
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("آخر ot-ics", () => fetch("/coa-api/last/ot-ics"))
              }
            >
              last/ot-ics
            </button>
          </div>
          <div className="actions">
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runDownload("تقرير TXT", "/coa-api/reports/txt")
              }
            >
              تنزيل reports/txt
            </button>
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runDownload("تقرير HTML", "/coa-api/reports/html")
              }
            >
              تنزيل reports/html
            </button>
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runDownload("incident", "/coa-api/reports/incident")
              }
            >
              تنزيل reports/incident
            </button>
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("mitre-navigator.json", () =>
                  fetch("/coa-api/reports/mitre-navigator.json")
                )
              }
            >
              reports/mitre-navigator.json
            </button>
          </div>
        </section>
      )}

      {mainTab === "status" && (
        <section className="status-panel">
          <p className="section-desc">
            يجمع حالة Council FastAPI و COA Flask من الخادم نفسه (8765).
          </p>
          <div className="actions">
            <button
              type="button"
              className="btn primary"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("التكامل / integrations", () =>
                  fetch("/api/integrations")
                )
              }
            >
              جلب /api/integrations
            </button>
            <button
              type="button"
              className="btn"
              disabled={loadStatus === "loading"}
              onClick={() =>
                runJson("COA health-proxy (من FastAPI)", () =>
                  fetch("/api/coa/health-proxy")
                )
              }
            >
              /api/coa/health-proxy
            </button>
          </div>
        </section>
      )}

      {lastError && (
        <p className="banner error" role="alert">
          خطأ: {lastError}
        </p>
      )}

      <section className="output-panel">
        <div className="output-head">
          <span>نتيجة العملية</span>
          {loadStatus === "loading" && (
            <span className="pulse">جاري التحميل…</span>
          )}
        </div>
        <div className={`result-card result-card--${resultVariant}`}>
          {(result.kind === "idle" || result.kind === "loading") && (
            <p className="result-empty">{result.message}</p>
          )}

          {(result.kind === "success" || result.kind === "error") &&
            resultEntries.length > 0 && (
              <div className="result-grid">
                {resultEntries.map(([key, value]) => (
                  <article className="result-item" key={key}>
                    <h4>{key}</h4>
                    {key === "threats" && Array.isArray(value) ? (
                      value.length > 0 ? (
                        <div className="threats-list">
                          {value.map((threat, idx) => {
                            const t = (threat ?? {}) as JsonRecord;
                            const signals = Array.isArray(t.signals) ? t.signals : [];
                            return (
                              <div className="threat-item" key={`${String(t.source)}-${idx}`}>
                                <div className="threat-head">
                                  <p className="threat-title">{formatPrimitive(t.type)}</p>
                                  <span className={`severity-chip ${severityClass(t.severity)}`}>
                                    {formatPrimitive(t.severity)}
                                  </span>
                                </div>
                                <p>{formatPrimitive(t.source)}</p>
                                <p>{formatPrimitive(t.details)}</p>
                                {signals.length > 0 && (
                                  <p className="threat-signals">
                                    الإشارات: {signals.map((s) => String(s)).join(" · ")}
                                  </p>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      ) : (
                        <p>لا توجد تهديدات.</p>
                      )
                    ) : Array.isArray(value) ? (
                      value.length > 0 ? (
                        <ul className="result-list">
                          {value.map((item, idx) => (
                            <li key={`${key}-${idx}`}>{formatPrimitive(item)}</li>
                          ))}
                        </ul>
                      ) : (
                        <p>—</p>
                      )
                    ) : typeof value === "object" && value !== null ? (
                      <div className="result-subgrid">
                        {Object.entries(value as JsonRecord).map(([subKey, subValue]) => (
                          <p key={`${key}-${subKey}`}>
                            <strong>{subKey}:</strong> {formatPrimitive(subValue)}
                          </p>
                        ))}
                      </div>
                    ) : (
                      <p>{formatPrimitive(value)}</p>
                    )}
                  </article>
                ))}
              </div>
            )}

          {(result.kind === "success" || result.kind === "error") &&
            resultEntries.length === 0 && (
              <p className="result-empty">
                {formatPrimitive(result.data)}
              </p>
            )}
        </div>
      </section>

      <footer className="app-footer">
        v0.2.1 · Local SOC · 2026
      </footer>
    </div>
  );
}
