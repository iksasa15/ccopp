import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { BrandLogo } from "./Brand";
import "./App.css";

type LoadStatus = "idle" | "loading" | "error";
type Section = "dashboard" | "scan" | "council" | "coa" | "result";
type JsonRecord = Record<string, unknown>;
type ResultState =
  | { kind: "idle"; message: string }
  | { kind: "loading"; message: string }
  | { kind: "success"; data: unknown }
  | { kind: "error"; data: unknown };
type LlmConnectionState = "checking" | "connected" | "disconnected";

function formatPrimitive(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "نعم" : "لا";
  if (typeof value === "number") return value.toLocaleString("en-US");
  if (typeof value === "string") return value;
  return "بيانات مركبة";
}

function formatDuration(ms: number): string {
  if (!ms || ms < 0) return "—";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} ث`;
  const m = Math.floor(s / 60);
  const rs = Math.floor(s % 60);
  return `${m} د ${rs} ث`;
}

function formatTimestamp(ts: number): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString("ar", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      day: "2-digit",
      month: "2-digit",
    });
  } catch {
    return new Date(ts).toISOString();
  }
}

function formatBytes(bytes: number): string {
  if (!bytes && bytes !== 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

const HISTORY_KEY = "nexus_scan_history_v1";
const HISTORY_MAX = 8;

interface ScanHistoryEntry {
  id: string;
  startedAt: number;
  finishedAt: number;
  durationMs: number;
  mode: "quick" | "deep" | "coa" | "other";
  label: string;
  ok: boolean;
  totalThreats: number;
  critical: number;
  high: number;
  score: number;
  scanId?: string;
}

function loadHistory(): ScanHistoryEntry[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as ScanHistoryEntry[]) : [];
  } catch {
    return [];
  }
}

function saveHistory(entries: ScanHistoryEntry[]): void {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, HISTORY_MAX)));
  } catch {
    // ignore persistence errors
  }
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
    return "لا يوجد Scan سابق في COA. نفّذ أولاً «فحص COA» ثم أعد المحاولة.";
  }
  return null;
}

type NavId = Section;

interface NavSpec {
  id: NavId;
  name: string;
  icon: JSX.Element;
}

const NAV_ITEMS: NavSpec[] = [
  {
    id: "dashboard",
    name: "لوحة المعلومات",
    icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="7" height="9" rx="1" />
        <rect x="14" y="3" width="7" height="5" rx="1" />
        <rect x="14" y="12" width="7" height="9" rx="1" />
        <rect x="3" y="16" width="7" height="5" rx="1" />
      </svg>
    ),
  },
  {
    id: "scan",
    name: "فحص النظام",
    icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-3.5-3.5" />
      </svg>
    ),
  },
  {
    id: "council",
    name: "أدوات Council",
    icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2 4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6z" />
      </svg>
    ),
  },
  {
    id: "coa",
    name: "أدوات COA",
    icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="4" width="18" height="16" rx="2" />
        <path d="M3 10h18M9 4v16" />
      </svg>
    ),
  },
  {
    id: "result",
    name: "النتيجة",
    icon: (
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 3v4a1 1 0 0 0 1 1h4" />
        <path d="M5 8a2 2 0 0 1 2-2h7l5 5v9a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z" />
        <path d="M9 13h6M9 17h4" />
      </svg>
    ),
  },
];

export default function App() {
  const [section, setSection] = useState<Section>("dashboard");
  const [archivePath, setArchivePath] = useState("");
  const [result, setResult] = useState<ResultState>({
    kind: "idle",
    message: "ابدأ فحصاً من قسم «فحص النظام» لرؤية أحدث النتائج هنا.",
  });
  const [loadStatus, setLoadStatus] = useState<LoadStatus>("idle");
  const [lastError, setLastError] = useState<string | null>(null);

  const [coaDryRun, setCoaDryRun] = useState(true);
  const [coaUseCouncil, setCoaUseCouncil] = useState(false);
  const [councilScanMode, setCouncilScanMode] = useState<"quick" | "deep">("deep");
  const [councilLlmState, setCouncilLlmState] =
    useState<LlmConnectionState>("checking");
  const [coaLlmState, setCoaLlmState] = useState<LlmConnectionState>("checking");

  const [scanMeta, setScanMeta] = useState<{
    label: string;
    mode: "quick" | "deep" | "coa" | "other";
    startedAt: number;
    finishedAt: number;
  } | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [history, setHistory] = useState<ScanHistoryEntry[]>(() => loadHistory());

  useEffect(() => {
    if (loadStatus !== "loading" || !scanMeta) {
      return;
    }
    const start = scanMeta.startedAt;
    setElapsedMs(Date.now() - start);
    const id = window.setInterval(() => {
      setElapsedMs(Date.now() - start);
    }, 250);
    return () => window.clearInterval(id);
  }, [loadStatus, scanMeta]);

  const ensureLlmReady = useCallback(
    async (target: "council" | "coa"): Promise<boolean> => {
      try {
        if (target === "coa") {
          const ollamaRes = await fetch("/coa-api/health/ollama");
          const ollamaBody = await parseJsonSafe(ollamaRes);
          if (
            !ollamaRes.ok ||
            (ollamaBody && typeof ollamaBody === "object" && (ollamaBody as JsonRecord).ok === false)
          ) {
            setLoadStatus("error");
            setLastError("LLM غير جاهزة في COA (Ollama).");
            setResult({
              kind: "error",
              data: {
                error: "LLM غير جاهزة في COA (Ollama)",
                hint: "شغّل Ollama وتأكد من تنزيل النموذج، ثم أعد المحاولة.",
                details: ollamaBody,
              },
            });
            return false;
          }
          const llmRes = await fetch("/coa-api/health/llm");
          const llmBody = await parseJsonSafe(llmRes);
          if (
            !llmRes.ok ||
            (llmBody && typeof llmBody === "object" && (llmBody as JsonRecord).ok === false)
          ) {
            setLoadStatus("error");
            setLastError("LLM غير جاهزة في COA.");
            setResult({
              kind: "error",
              data: {
                error: "LLM غير جاهزة في COA",
                hint: "تأكد من أن health/llm يرجع ok قبل الفحص.",
                details: llmBody,
              },
            });
            return false;
          }
          return true;
        }

        const integrationsRes = await fetch("/api/integrations");
        const integrationsBody = await parseJsonSafe(integrationsRes);
        const llmOk =
          integrationsRes.ok &&
          integrationsBody &&
          typeof integrationsBody === "object" &&
          (integrationsBody as JsonRecord).council &&
          typeof (integrationsBody as JsonRecord).council === "object" &&
          ((integrationsBody as JsonRecord).council as JsonRecord).fastapi === true;

        if (!llmOk) {
          setLoadStatus("error");
          setLastError("LLM غير جاهزة في Council.");
          setResult({
            kind: "error",
            data: {
              error: "LLM غير جاهزة في Council",
              hint: "تأكد من تكامل LLM عبر /api/integrations قبل scan-system.",
              details: integrationsBody,
            },
          });
          return false;
        }

        return true;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setLoadStatus("error");
        setLastError(msg);
        setResult({
          kind: "error",
          data: { error: "تعذر التحقق من جاهزية LLM", details: msg },
        });
        return false;
      }
    },
    []
  );

  const aggregateStats = useCallback((data: unknown) => {
    let critical = 0;
    let high = 0;
    let medium = 0;
    let low = 0;
    let total = 0;
    if (!data || typeof data !== "object") {
      return { critical, high, medium, low, total, score: 0 };
    }
    const obj = data as JsonRecord;
    critical += Number(obj.critical ?? 0) || 0;
    high += Number(obj.high ?? 0) || 0;
    medium += Number(obj.medium ?? 0) || 0;
    low += Number(obj.low ?? 0) || 0;
    total += Number(obj.total_threats ?? 0) || 0;

    const decision =
      obj.decision && typeof obj.decision === "object" ? (obj.decision as JsonRecord) : null;
    if (decision && Array.isArray(decision.primary_findings)) {
      for (const f of decision.primary_findings as JsonRecord[]) {
        const lvl = String(f.threat_level ?? "").toLowerCase();
        if (lvl === "critical") critical += 1;
        else if (lvl === "high") high += 1;
        else if (lvl === "medium") medium += 1;
        else if (lvl === "low") low += 1;
      }
      if (total === 0) total = (decision.primary_findings as unknown[]).length;
    }
    const fs =
      obj.filesystem_scan && typeof obj.filesystem_scan === "object"
        ? (obj.filesystem_scan as JsonRecord)
        : null;
    if (fs && Number(obj.total_threats ?? 0) === 0) {
      const fsCount = Number(fs.findings_count ?? 0) || 0;
      if (fsCount > 0) {
        medium += fsCount;
        total += fsCount;
      }
    }
    const dfa =
      obj.deep_file_analysis && typeof obj.deep_file_analysis === "object"
        ? (obj.deep_file_analysis as JsonRecord)
        : null;
    if (dfa) {
      const susp = Number(dfa.suspicious_count ?? 0) || 0;
      if (susp > 0) {
        high += susp;
        total += susp;
      }
    }
    if (total === 0) total = critical + high + medium + low;
    const score = Math.min(100, critical * 35 + high * 20 + medium * 8 + low * 3);
    return { critical, high, medium, low, total, score };
  }, []);

  const recordHistory = useCallback(
    (
      label: string,
      mode: "quick" | "deep" | "coa" | "other",
      startedAt: number,
      finishedAt: number,
      ok: boolean,
      data: unknown
    ) => {
      const stats = aggregateStats(data);
      const obj =
        data && typeof data === "object" ? (data as JsonRecord) : ({} as JsonRecord);
      const entry: ScanHistoryEntry = {
        id: `${startedAt}-${Math.random().toString(36).slice(2, 8)}`,
        startedAt,
        finishedAt,
        durationMs: Math.max(0, finishedAt - startedAt),
        mode,
        label,
        ok,
        totalThreats: stats.total,
        critical: stats.critical,
        high: stats.high,
        score: stats.score,
        scanId: typeof obj.scan_id === "string" ? obj.scan_id : undefined,
      };
      setHistory((prev) => {
        const next = [entry, ...prev].slice(0, HISTORY_MAX);
        saveHistory(next);
        return next;
      });
    },
    [aggregateStats]
  );

  const runJson = useCallback(
    async (
      label: string,
      fn: () => Promise<Response>,
      hint?: string,
      loadingMessage?: string,
      mode: "quick" | "deep" | "coa" | "other" = "other"
    ) => {
      const startedAt = Date.now();
      setScanMeta({ label, mode, startedAt, finishedAt: 0 });
      setElapsedMs(0);
      setLoadStatus("loading");
      setLastError(null);
      setResult({
        kind: "loading",
        message: loadingMessage ?? `جاري التنفيذ: ${label}`,
      });
      try {
        const res = await fn();
        const body = await parseJsonSafe(res);
        const finishedAt = Date.now();
        if (!res.ok) {
          setLoadStatus("error");
          const knownHint = getKnownCoaHint(body);
          setLastError(knownHint ?? `${res.status} ${res.statusText}`);
          setResult({
            kind: "error",
            data: knownHint ? { ...(body as JsonRecord), user_hint: knownHint } : body,
          });
          setScanMeta({ label, mode, startedAt, finishedAt });
          recordHistory(label, mode, startedAt, finishedAt, false, body);
          setSection("result");
          return;
        }
        setLoadStatus("idle");
        setResult({ kind: "success", data: body });
        setScanMeta({ label, mode, startedAt, finishedAt });
        recordHistory(label, mode, startedAt, finishedAt, true, body);
        setSection("result");
      } catch (e) {
        const finishedAt = Date.now();
        setLoadStatus("error");
        const msg = e instanceof Error ? e.message : String(e);
        setLastError(msg);
        const data = {
          error: msg,
          hint:
            hint ??
            "تأكد: uvicorn على 8765، و COA Flask على 5050 (مثلاً bash scripts/start_merged.sh)",
        };
        setResult({ kind: "error", data });
        setScanMeta({ label, mode, startedAt, finishedAt });
        recordHistory(label, mode, startedAt, finishedAt, false, data);
        setSection("result");
      }
    },
    [recordHistory]
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
          setSection("result");
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
        setResult({
          kind: "success",
          data: {
            ok: true,
            downloaded: name,
            bytes: blob.size,
            note: "تم بدء التنزيل في المتصفح",
          },
        });
        setSection("result");
      } catch (e) {
        setLoadStatus("error");
        const msg = e instanceof Error ? e.message : String(e);
        setLastError(msg);
        setResult({ kind: "error", data: { error: msg } });
        setSection("result");
      }
    },
    []
  );

  const refreshLlmConnectionStatus = useCallback(async () => {
    try {
      const [integrationsRes, coaOllamaRes, coaLlmRes] = await Promise.all([
        fetch("/api/integrations"),
        fetch("/coa-api/health/ollama"),
        fetch("/coa-api/health/llm"),
      ]);
      const [integrationsBody, coaOllamaBody, coaLlmBody] = await Promise.all([
        parseJsonSafe(integrationsRes),
        parseJsonSafe(coaOllamaRes),
        parseJsonSafe(coaLlmRes),
      ]);

      const councilOk =
        integrationsRes.ok &&
        integrationsBody &&
        typeof integrationsBody === "object" &&
        (integrationsBody as JsonRecord).council &&
        typeof (integrationsBody as JsonRecord).council === "object" &&
        ((integrationsBody as JsonRecord).council as JsonRecord).fastapi === true;
      const coaOllamaOk =
        coaOllamaRes.ok &&
        coaOllamaBody &&
        typeof coaOllamaBody === "object" &&
        (coaOllamaBody as JsonRecord).ok !== false;
      const coaLlmOk =
        coaLlmRes.ok &&
        coaLlmBody &&
        typeof coaLlmBody === "object" &&
        (coaLlmBody as JsonRecord).ok !== false;

      setCouncilLlmState(councilOk ? "connected" : "disconnected");
      setCoaLlmState(coaOllamaOk && coaLlmOk ? "connected" : "disconnected");
    } catch {
      setCouncilLlmState("disconnected");
      setCoaLlmState("disconnected");
    }
  }, []);

  useEffect(() => {
    void refreshLlmConnectionStatus();
    const id = window.setInterval(() => {
      void refreshLlmConnectionStatus();
    }, 30000);
    return () => window.clearInterval(id);
  }, [refreshLlmConnectionStatus]);

  const dashboardStats = useMemo(() => {
    const empty = {
      totalThreats: 0,
      critical: 0,
      high: 0,
      medium: 0,
      low: 0,
      highConfidence: 0,
      score: 0,
    };
    if (result.kind !== "success" && result.kind !== "error") return empty;
    const data = (result.data && typeof result.data === "object"
      ? (result.data as JsonRecord)
      : {}) as JsonRecord;

    // Quick scan: flat fields are present.
    let critical = Number(data.critical ?? 0) || 0;
    let high = Number(data.high ?? 0) || 0;
    let medium = Number(data.medium ?? 0) || 0;
    let low = Number(data.low ?? 0) || 0;
    let highConfidence = Number(data.high_confidence_threats ?? 0) || 0;
    let totalThreats = Number(data.total_threats ?? 0) || 0;

    // Deep scan: aggregate from decision.primary_findings.
    const decision =
      data.decision && typeof data.decision === "object"
        ? (data.decision as JsonRecord)
        : null;
    if (decision) {
      const findings = Array.isArray(decision.primary_findings)
        ? (decision.primary_findings as JsonRecord[])
        : [];
      for (const f of findings) {
        const lvl = String(f.threat_level ?? "").toLowerCase();
        if (lvl === "critical") critical += 1;
        else if (lvl === "high") high += 1;
        else if (lvl === "medium") medium += 1;
        else if (lvl === "low") low += 1;
        const conf = Number(f.confidence ?? 0);
        if (conf >= 0.7 && (lvl === "high" || lvl === "critical")) {
          highConfidence += 1;
        }
      }
      if (totalThreats === 0) totalThreats = findings.length;
    }

    // Filesystem heuristic findings count as medium-severity additions.
    const fs =
      data.filesystem_scan && typeof data.filesystem_scan === "object"
        ? (data.filesystem_scan as JsonRecord)
        : null;
    if (fs) {
      const fsCount = Number(fs.findings_count ?? 0) || 0;
      if (fsCount > 0) {
        // Only add to medium if not already counted in flat quick-scan response.
        if (Number(data.total_threats ?? 0) === 0) {
          medium += fsCount;
          totalThreats += fsCount;
        }
      }
    }

    // LLM deep-file analysis: each "suspicious" verdict adds to high.
    const dfa =
      data.deep_file_analysis && typeof data.deep_file_analysis === "object"
        ? (data.deep_file_analysis as JsonRecord)
        : null;
    if (dfa) {
      const susp = Number(dfa.suspicious_count ?? 0) || 0;
      if (susp > 0) {
        high += susp;
        highConfidence += susp;
        totalThreats += susp;
      }
    }

    if (totalThreats === 0) {
      totalThreats = critical + high + medium + low;
    }

    const score = Math.min(100, critical * 35 + high * 20 + medium * 8 + low * 3);
    return { totalThreats, critical, high, medium, low, highConfidence, score };
  }, [result]);

  const riskRingGradId = useId().replace(/:/g, "");
  const riskRingDash = useMemo(() => {
    const r = 44;
    const c = 2 * Math.PI * r;
    const p = Math.min(100, Math.max(0, dashboardStats.score));
    return { c, offset: c * (1 - p / 100) };
  }, [dashboardStats.score]);

  const startCouncilScan = useCallback(async () => {
    const label =
      councilScanMode === "deep"
        ? "فحص النظام العميق (Council)"
        : "فحص النظام السريع (Council)";
    const startedAt = Date.now();
    setScanMeta({ label, mode: councilScanMode, startedAt, finishedAt: 0 });
    setElapsedMs(0);
    setLoadStatus("loading");
    setLastError(null);
    setResult({ kind: "loading", message: "جاري الفحص…" });
    const ready = await ensureLlmReady("council");
    if (!ready) return;
    await runJson(
      label,
      () =>
        fetch("/api/scan-system", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode: councilScanMode }),
        }),
      undefined,
      "جاري الفحص…",
      councilScanMode
    );
  }, [councilScanMode, ensureLlmReady, runJson]);

  const startCoaScan = useCallback(async () => {
    const label = "COA full scan";
    const startedAt = Date.now();
    setScanMeta({ label, mode: "coa", startedAt, finishedAt: 0 });
    setElapsedMs(0);
    setLoadStatus("loading");
    setLastError(null);
    setResult({ kind: "loading", message: "جاري الفحص…" });
    const ready = await ensureLlmReady("coa");
    if (!ready) return;
    await runJson(
      label,
      () =>
        fetch("/coa-api/scan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            dry_run: coaDryRun,
            use_council: coaUseCouncil,
          }),
        }),
      undefined,
      "جاري الفحص…",
      "coa"
    );
  }, [coaDryRun, coaUseCouncil, ensureLlmReady, runJson]);

  const dotState = (s: LlmConnectionState): "ok" | "warn" | "danger" =>
    s === "connected" ? "ok" : s === "checking" ? "warn" : "danger";
  const dotLabel = (prefix: string, s: LlmConnectionState) =>
    s === "connected"
      ? `${prefix} متصل`
      : s === "checking"
        ? `${prefix} يفحص`
        : `${prefix} مفصول`;

  const resultEntries =
    result.kind === "success" || result.kind === "error"
      ? result.data && typeof result.data === "object"
        ? Object.entries(result.data as JsonRecord)
        : []
      : [];
  const resultVariant =
    result.kind === "error" ? "danger" : result.kind === "success" ? "ok" : "neutral";

  const scanStatusKind: "idle" | "loading" | "error" | "ok" =
    result.kind === "loading"
      ? "loading"
      : result.kind === "error"
        ? "error"
        : result.kind === "success"
          ? "ok"
          : "idle";
  const scanStatusText =
    result.kind === "loading"
      ? result.message
      : result.kind === "success"
        ? `اكتمل آخر فحص — ${dashboardStats.totalThreats} تهديد مكتشف`
        : result.kind === "error"
          ? "فشل آخر إجراء — راجع التفاصيل في «النتيجة»"
          : "لم يبدأ فحص بعد";

  return (
    <div className="layout" lang="ar">
      {/* —————————————————————— Top bar —————————————————————— */}
      <header className="topbar">
        <div className="topbar__brand">
          <BrandLogo size={32} />
          <h1 className="topbar__title">COA</h1>
        </div>
        <div className="topbar__status">
          <span
            className="dot-pill"
            data-state={dotState(councilLlmState)}
            title={dotLabel("Council", councilLlmState)}
          >
            Council
          </span>
          <span
            className="dot-pill"
            data-state={dotState(coaLlmState)}
            title={dotLabel("COA", coaLlmState)}
          >
            COA
          </span>
        </div>
      </header>

      {/* —————————————————————— Sidebar —————————————————————— */}
      <aside className="sidebar">
        <p className="sidebar__heading">SOC Console</p>
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`nav-item ${section === item.id ? "active" : ""}`}
            onClick={() => setSection(item.id)}
          >
            <span className="nav-item__icon">{item.icon}</span>
            <span className="nav-item__name">{item.name}</span>
            {item.id === "result" && dashboardStats.totalThreats > 0 && (
              <span className="nav-item__count">
                {dashboardStats.totalThreats}
              </span>
            )}
          </button>
        ))}
        <div className="sidebar__foot">v0.3 · 2026</div>
      </aside>

      {/* —————————————————————— Content —————————————————————— */}
      <main className="content">
        {lastError && (
          <p className="banner error" role="alert">
            خطأ: {lastError}
          </p>
        )}

        {section === "dashboard" && (
          <>
            <div className="page-head">
              <h1>لوحة المعلومات</h1>
              <p>قراءة سريعة للوضع الأمني الحالي</p>
            </div>

            <div className="meta-strip">
              <span className="meta-strip__item">
                <span className="meta-strip__label">آخر فحص</span>
                <span className="meta-strip__value">
                  {scanMeta && scanMeta.finishedAt
                    ? formatTimestamp(scanMeta.finishedAt)
                    : "—"}
                </span>
              </span>
              <span className="meta-strip__item">
                <span className="meta-strip__label">الوضع</span>
                <span className="meta-strip__value">
                  {scanMeta
                    ? scanMeta.mode === "deep"
                      ? "عميق"
                      : scanMeta.mode === "quick"
                        ? "سريع"
                        : scanMeta.mode === "coa"
                          ? "COA"
                          : "—"
                    : "—"}
                </span>
              </span>
              <span className="meta-strip__item">
                <span className="meta-strip__label">المدّة</span>
                <span className="meta-strip__value">
                  {scanMeta && scanMeta.finishedAt
                    ? formatDuration(scanMeta.finishedAt - scanMeta.startedAt)
                    : "—"}
                </span>
              </span>
              <span className="meta-strip__item">
                <span className="meta-strip__label">عدد الفحوصات</span>
                <span className="meta-strip__value">{history.length}</span>
              </span>
            </div>

            <div className="dash">
              <article className="card dash__risk">
                <p className="card__head">Risk Score</p>
                <div className="risk-ring">
                  <svg
                    className="risk-ring__svg"
                    width="120"
                    height="120"
                    viewBox="0 0 100 100"
                    aria-hidden
                  >
                    <circle
                      cx="50"
                      cy="50"
                      r="44"
                      fill="none"
                      stroke="rgba(45,90,72,0.55)"
                      strokeWidth="8"
                    />
                    <circle
                      cx="50"
                      cy="50"
                      r="44"
                      fill="none"
                      stroke={`url(#${riskRingGradId})`}
                      strokeWidth="8"
                      strokeLinecap="round"
                      strokeDasharray={riskRingDash.c}
                      strokeDashoffset={riskRingDash.offset}
                      transform="rotate(-90 50 50)"
                    />
                    <defs>
                      <linearGradient id={riskRingGradId} x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" stopColor="#6ee7b7" />
                        <stop offset="100%" stopColor="#22c55e" />
                      </linearGradient>
                    </defs>
                  </svg>
                  <div>
                    <p className="risk-ring__pct">{dashboardStats.score}%</p>
                    <p className="risk-ring__label">
                      مؤشر مبني على توزيع الشدّة
                    </p>
                  </div>
                </div>
              </article>

              <article className="card">
                <p className="card__head">المؤشرات</p>
                <div className="kpi-grid">
                  <div className="kpi">
                    <p className="kpi__label">إجمالي التهديدات</p>
                    <p className="kpi__value">{dashboardStats.totalThreats}</p>
                  </div>
                  <div className={`kpi ${dashboardStats.highConfidence > 0 ? "kpi--warn" : ""}`}>
                    <p className="kpi__label">High Confidence</p>
                    <p className="kpi__value">{dashboardStats.highConfidence}</p>
                  </div>
                  <div className={`kpi ${dashboardStats.critical > 0 ? "kpi--danger" : ""}`}>
                    <p className="kpi__label">Critical</p>
                    <p className="kpi__value">{dashboardStats.critical}</p>
                  </div>
                </div>
                <p className="card__head">توزيع الشدّة</p>
                <div className="severity-bars">
                  {(
                    [
                      { label: "Critical", key: "critical", mult: 18 },
                      { label: "High", key: "high", mult: 14 },
                      { label: "Medium", key: "medium", mult: 12 },
                      { label: "Low", key: "low", mult: 10 },
                    ] as const
                  ).map((row) => {
                    const value =
                      dashboardStats[row.key as keyof typeof dashboardStats] as number;
                    return (
                      <div className="severity-row" key={row.key}>
                        <span>{row.label}</span>
                        <div className="bar-track">
                          <div
                            className={`bar-fill ${row.key}`}
                            style={{ width: `${Math.min(100, value * row.mult)}%` }}
                          />
                        </div>
                        <strong>{value}</strong>
                      </div>
                    );
                  })}
                </div>
              </article>
            </div>

            <article className="card">
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: "0.5rem",
                }}
              >
                <p className="card__head">سجل الفحوصات</p>
                {history.length > 0 && (
                  <button
                    type="button"
                    className="link-btn"
                    onClick={() => {
                      setHistory([]);
                      saveHistory([]);
                    }}
                  >
                    مسح السجل
                  </button>
                )}
              </div>
              {history.length === 0 ? (
                <p className="card__meta">لا يوجد سجل بعد. ابدأ فحصاً لتراكم البيانات.</p>
              ) : (
                <div className="history-table">
                  <div className="history-row history-row--head">
                    <span>الوقت</span>
                    <span>النوع</span>
                    <span>المدّة</span>
                    <span>تهديدات</span>
                    <span>Critical</span>
                    <span>Score</span>
                    <span>الحالة</span>
                  </div>
                  {history.map((h) => (
                    <div className="history-row" key={h.id}>
                      <span>{formatTimestamp(h.finishedAt)}</span>
                      <span>
                        <span className={`mode-chip mode-chip--${h.mode}`}>
                          {h.mode === "deep"
                            ? "عميق"
                            : h.mode === "quick"
                              ? "سريع"
                              : h.mode === "coa"
                                ? "COA"
                                : "أمر"}
                        </span>
                      </span>
                      <span>{formatDuration(h.durationMs)}</span>
                      <span>{h.totalThreats}</span>
                      <span>{h.critical}</span>
                      <span>{h.score}%</span>
                      <span>
                        <span
                          className={`status-chip ${h.ok ? "status-chip--ok" : "status-chip--err"}`}
                        >
                          {h.ok ? "ناجح" : "فشل"}
                        </span>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </article>
          </>
        )}

        {section === "scan" && (
          <>
            <div className="page-head">
              <h1>فحص النظام</h1>
              <p>اختر الوضع ثم ابدأ الفحص</p>
            </div>

            <section className="scan-hero">
              <div className="scan-mode-switch" role="group" aria-label="وضع الفحص">
                <button
                  type="button"
                  className={`scan-mode-btn ${councilScanMode === "quick" ? "active" : ""}`}
                  onClick={() => setCouncilScanMode("quick")}
                  disabled={loadStatus === "loading"}
                >
                  فحص سريع
                </button>
                <button
                  type="button"
                  className={`scan-mode-btn ${councilScanMode === "deep" ? "active" : ""}`}
                  onClick={() => setCouncilScanMode("deep")}
                  disabled={loadStatus === "loading"}
                >
                  فحص عميق (LLM + Agent)
                </button>
              </div>
              <button
                type="button"
                className="btn primary btn-start"
                disabled={loadStatus === "loading"}
                onClick={startCouncilScan}
              >
                {councilScanMode === "deep" ? "ابدأ الفحص العميق" : "ابدأ الفحص السريع"}
              </button>
            </section>

            <div className="scan-status" data-kind={scanStatusKind}>
              <span className="scan-status__dot" />
              <span style={{ flex: 1 }}>{scanStatusText}</span>
              {loadStatus === "loading" && (
                <span className="scan-timer">{formatDuration(elapsedMs)}</span>
              )}
              {(result.kind === "success" || result.kind === "error") && (
                <button
                  type="button"
                  className="link-btn"
                  onClick={() => setSection("result")}
                >
                  عرض التفاصيل ←
                </button>
              )}
            </div>

            {history.length > 0 && (
              <article className="card">
                <p className="card__head">آخر الفحوصات</p>
                <div className="history-mini">
                  {history.slice(0, 4).map((h) => (
                    <div className="history-mini__row" key={h.id}>
                      <span className={`mode-chip mode-chip--${h.mode}`}>
                        {h.mode === "deep"
                          ? "عميق"
                          : h.mode === "quick"
                            ? "سريع"
                            : h.mode === "coa"
                              ? "COA"
                              : "أمر"}
                      </span>
                      <span style={{ flex: 1 }}>{h.label}</span>
                      <span style={{ color: "#8fb5a3" }}>
                        {formatTimestamp(h.finishedAt)}
                      </span>
                      <span style={{ color: "#8fb5a3" }}>
                        {formatDuration(h.durationMs)}
                      </span>
                      <span>
                        <strong>{h.totalThreats}</strong> تهديد
                      </span>
                    </div>
                  ))}
                </div>
              </article>
            )}
          </>
        )}

        {section === "council" && (
          <>
            <div className="page-head">
              <h1>أدوات Council</h1>
              <p>FastAPI · 8765</p>
            </div>

            <article className="card">
              <p className="card__head">الإجراءات</p>
              <div className="tools-grid">
                <button
                  type="button"
                  className="btn"
                  disabled={loadStatus === "loading"}
                  onClick={() => runJson("verify-audit", () => fetch("/api/verify-audit"))}
                >
                  verify-audit
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={loadStatus === "loading"}
                  onClick={() => runJson("baseline-stats", () => fetch("/api/baseline-stats"))}
                >
                  baseline-stats
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={loadStatus === "loading"}
                  onClick={() => runJson("list-quarantine", () => fetch("/api/list-quarantine"))}
                >
                  list-quarantine
                </button>
                <button
                  type="button"
                  className="btn ghost"
                  disabled={loadStatus === "loading"}
                  onClick={() => runJson("مساعدة الأوامر", () => fetch("/api/commands"))}
                >
                  أوامر API
                </button>
              </div>
            </article>

            <article className="card archive-row">
              <p className="card__head">فحص أرشيف</p>
              <label htmlFor="archive-path">مسار الملف — scan-archive</label>
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
                  ابدأ
                </button>
              </div>
            </article>
          </>
        )}

        {section === "coa" && (
          <>
            <div className="page-head">
              <h1>أدوات COA</h1>
              <p>Flask · 5050</p>
            </div>

            <article className="card">
              <p className="card__head">فحص COA كامل</p>
              <p className="coa-hint">
                يتطلب <code>web_api.py</code> على <code>5050</code>. بعد الفحص يمكن جلب آخر
                defense / MITRE / OT أو تنزيل التقارير.
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
              </div>
              <button
                type="button"
                className="btn primary"
                disabled={loadStatus === "loading"}
                onClick={startCoaScan}
                style={{ alignSelf: "flex-start" }}
              >
                ابدأ فحص COA
              </button>
            </article>

            <article className="card">
              <p className="card__head">صحة الخدمات</p>
              <div className="tools-grid tools-grid--3">
                <button
                  type="button"
                  className="btn"
                  disabled={loadStatus === "loading"}
                  onClick={() =>
                    runJson("COA health", () => fetch("/coa-api/health"), "شغّل COA Flask على 5050")
                  }
                >
                  /api/health
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={loadStatus === "loading"}
                  onClick={() =>
                    runJson("Ollama diagnose", () => fetch("/coa-api/health/ollama"))
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
              </div>
            </article>

            <article className="card">
              <p className="card__head">آخر مسح</p>
              <div className="tools-grid tools-grid--3">
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
                  defense-context
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={loadStatus === "loading"}
                  onClick={() =>
                    runJson("آخر mitre-deep", () => fetch("/coa-api/last/mitre-deep"))
                  }
                >
                  mitre-deep
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={loadStatus === "loading"}
                  onClick={() =>
                    runJson("آخر ot-ics", () => fetch("/coa-api/last/ot-ics"))
                  }
                >
                  ot-ics
                </button>
              </div>
            </article>

            <article className="card">
              <p className="card__head">التقارير</p>
              <div className="tools-grid tools-grid--4">
                <button
                  type="button"
                  className="btn"
                  disabled={loadStatus === "loading"}
                  onClick={() => runDownload("تقرير TXT", "/coa-api/reports/txt")}
                >
                  reports/txt
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={loadStatus === "loading"}
                  onClick={() => runDownload("تقرير HTML", "/coa-api/reports/html")}
                >
                  reports/html
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={loadStatus === "loading"}
                  onClick={() => runDownload("incident", "/coa-api/reports/incident")}
                >
                  incident
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
                  navigator.json
                </button>
              </div>
            </article>
          </>
        )}

        {section === "result" && (
          <>
            <div className="page-head">
              <h1>النتيجة</h1>
              <p>
                {result.kind === "success"
                  ? "اكتمل التنفيذ"
                  : result.kind === "error"
                    ? "فشل التنفيذ"
                    : result.kind === "loading"
                      ? "قيد التنفيذ…"
                      : "لا توجد نتيجة بعد"}
              </p>
            </div>

            {scanMeta && (
              <div className="result-summary">
                <div className="result-summary__item">
                  <span className="result-summary__label">العنوان</span>
                  <span className="result-summary__value">{scanMeta.label}</span>
                </div>
                <div className="result-summary__item">
                  <span className="result-summary__label">الوضع</span>
                  <span className={`mode-chip mode-chip--${scanMeta.mode}`}>
                    {scanMeta.mode === "deep"
                      ? "عميق"
                      : scanMeta.mode === "quick"
                        ? "سريع"
                        : scanMeta.mode === "coa"
                          ? "COA"
                          : "أمر"}
                  </span>
                </div>
                <div className="result-summary__item">
                  <span className="result-summary__label">المدّة</span>
                  <span className="result-summary__value">
                    {scanMeta.finishedAt
                      ? formatDuration(scanMeta.finishedAt - scanMeta.startedAt)
                      : formatDuration(elapsedMs)}
                  </span>
                </div>
                <div className="result-summary__item">
                  <span className="result-summary__label">التوقيت</span>
                  <span className="result-summary__value">
                    {formatTimestamp(scanMeta.finishedAt || scanMeta.startedAt)}
                  </span>
                </div>
                <div className="result-summary__item">
                  <span className="result-summary__label">الحالة</span>
                  <span
                    className={`status-chip ${result.kind === "success" ? "status-chip--ok" : result.kind === "error" ? "status-chip--err" : "status-chip--neutral"}`}
                  >
                    {result.kind === "success"
                      ? "ناجح"
                      : result.kind === "error"
                        ? "فشل"
                        : "قيد العمل"}
                  </span>
                </div>
                {result.kind === "success" || result.kind === "error" ? (
                  (() => {
                    const data =
                      result.data && typeof result.data === "object"
                        ? (result.data as JsonRecord)
                        : null;
                    const scanId =
                      data && typeof data.scan_id === "string" ? data.scan_id : null;
                    return scanId ? (
                      <div className="result-summary__item">
                        <span className="result-summary__label">Scan ID</span>
                        <span
                          className="result-summary__value"
                          style={{ fontFamily: "JetBrains Mono, monospace", fontSize: "0.78rem" }}
                        >
                          {scanId.slice(0, 12)}…
                        </span>
                      </div>
                    ) : null;
                  })()
                ) : null}
              </div>
            )}

            {(() => {
              if (result.kind !== "success" && result.kind !== "error") return null;
              const data =
                result.data && typeof result.data === "object"
                  ? (result.data as JsonRecord)
                  : null;
              if (!data) return null;
              const fs = data.filesystem_scan;
              if (!fs || typeof fs !== "object") return null;
              const obj = fs as JsonRecord;
              const scannedFiles = Number(obj.scanned_files ?? 0) || 0;
              const findingsCount = Number(obj.findings_count ?? 0) || 0;
              const roots = Array.isArray(obj.roots) ? (obj.roots as unknown[]) : [];
              const findings = Array.isArray(obj.findings)
                ? (obj.findings as JsonRecord[])
                : [];
              return (
                <article className="card">
                  <p className="card__head">فحص الملفات (Filesystem)</p>
                  <div className="kpi-grid">
                    <div className="kpi">
                      <p className="kpi__label">ملفات مفحوصة</p>
                      <p className="kpi__value">{scannedFiles}</p>
                    </div>
                    <div className={`kpi ${findingsCount > 0 ? "kpi--warn" : ""}`}>
                      <p className="kpi__label">نتائج مشبوهة</p>
                      <p className="kpi__value">{findingsCount}</p>
                    </div>
                    <div className="kpi">
                      <p className="kpi__label">مسارات مفحوصة</p>
                      <p className="kpi__value">{roots.length}</p>
                    </div>
                  </div>
                  {findings.length > 0 && (
                    <div className="finding-list">
                      {findings.slice(0, 8).map((f, idx) => (
                        <div className="finding-row" key={`fs-${idx}`}>
                          <span className="finding-row__path" dir="ltr">
                            {String(f.path ?? "—")}
                          </span>
                          <span style={{ color: "#8fb5a3", fontSize: "0.74rem" }}>
                            {Array.isArray(f.signals) ? f.signals.join(" · ") : "—"}
                          </span>
                          <span
                            className={`severity-chip ${severityClass(f.severity ?? "medium")}`}
                          >
                            {String(f.recommended_action ?? "investigate")}
                          </span>
                        </div>
                      ))}
                      {findings.length > 8 && (
                        <p className="card__meta">
                          + {findings.length - 8} أخرى…
                        </p>
                      )}
                    </div>
                  )}
                </article>
              );
            })()}

            {(() => {
              if (result.kind !== "success" && result.kind !== "error") return null;
              const data =
                result.data && typeof result.data === "object"
                  ? (result.data as JsonRecord)
                  : null;
              if (!data) return null;
              const dfa = data.deep_file_analysis;
              if (!dfa || typeof dfa !== "object") return null;
              const obj = dfa as JsonRecord;
              const results = Array.isArray(obj.results)
                ? (obj.results as JsonRecord[])
                : [];
              const filesAnalyzed = Number(obj.files_analyzed ?? results.length) || 0;
              const suspiciousCount = Number(obj.suspicious_count ?? 0) || 0;
              const duration = Number(obj.duration_seconds ?? 0) || 0;
              const model = String(obj.model ?? "—");
              const note = typeof obj.note === "string" ? (obj.note as string) : null;
              const error = typeof obj.error === "string" ? (obj.error as string) : null;
              return (
                <article className="card">
                  <p className="card__head">تحليل LLM للملفات (Deep)</p>
                  {error && (
                    <p
                      className="card__meta"
                      style={{ color: "#fecaca" }}
                    >
                      {error}
                    </p>
                  )}
                  {note && !error && <p className="card__meta">{note}</p>}
                  <div className="kpi-grid">
                    <div className="kpi">
                      <p className="kpi__label">ملفات حُلّلت</p>
                      <p className="kpi__value">{filesAnalyzed}</p>
                    </div>
                    <div className={`kpi ${suspiciousCount > 0 ? "kpi--warn" : ""}`}>
                      <p className="kpi__label">مشبوهة</p>
                      <p className="kpi__value">{suspiciousCount}</p>
                    </div>
                    <div className="kpi">
                      <p className="kpi__label">المدّة</p>
                      <p className="kpi__value" style={{ fontSize: "1.1rem" }}>
                        {duration ? `${duration} ث` : "—"}
                      </p>
                    </div>
                  </div>
                  <p className="card__meta" style={{ direction: "ltr", textAlign: "start" }}>
                    Model: <code>{model}</code>
                  </p>
                  {results.length > 0 && (
                    <div className="finding-list">
                      {results.map((r, idx) => {
                        const verdict = String(r.verdict ?? "unknown");
                        const path = String(r.path ?? "—");
                        const rationale = String(r.rationale ?? "");
                        const meta =
                          r.metadata && typeof r.metadata === "object"
                            ? (r.metadata as JsonRecord)
                            : null;
                        const size = meta && typeof meta.size_bytes === "number"
                          ? formatBytes(meta.size_bytes as number)
                          : null;
                        return (
                          <div className="finding-row" key={`dfa-${idx}`}>
                            <span className="finding-row__path" dir="ltr">
                              {path}
                            </span>
                            {size && (
                              <span style={{ color: "#8fb5a3", fontSize: "0.74rem" }}>
                                {size}
                              </span>
                            )}
                            {rationale && (
                              <span style={{ color: "#b8d4c8", fontSize: "0.78rem" }}>
                                {rationale}
                              </span>
                            )}
                            <span
                              className={`severity-chip ${
                                verdict === "suspicious"
                                  ? "is-high"
                                  : verdict === "benign"
                                    ? "is-low"
                                    : "is-neutral"
                              }`}
                            >
                              {verdict === "suspicious"
                                ? "مشبوه"
                                : verdict === "benign"
                                  ? "آمن"
                                  : "غير محدد"}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </article>
              );
            })()}

            <div className={`result-card result-card--${resultVariant}`}>
              {(result.kind === "idle" || result.kind === "loading") && (
                <p className="result-empty">{result.message}</p>
              )}

              {(result.kind === "success" || result.kind === "error") &&
                resultEntries.length > 0 && (
                  <div className="result-grid">
                    {resultEntries
                      .filter(
                        ([k]) => k !== "filesystem_scan" && k !== "deep_file_analysis"
                      )
                      .map(([key, value]) => (
                      <article className="result-item" key={key}>
                        <h4>{key}</h4>
                        {key === "threats" && Array.isArray(value) ? (
                          value.length > 0 ? (
                            <div className="threats-list">
                              {value.map((threat, idx) => {
                                const t = (threat ?? {}) as JsonRecord;
                                const signals = Array.isArray(t.signals) ? t.signals : [];
                                return (
                                  <div
                                    className="threat-item"
                                    key={`${String(t.source)}-${idx}`}
                                  >
                                    <div className="threat-head">
                                      <p className="threat-title">
                                        {formatPrimitive(t.type)}
                                      </p>
                                      <span
                                        className={`severity-chip ${severityClass(t.severity)}`}
                                      >
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
                  <p className="result-empty">{formatPrimitive(result.data)}</p>
                )}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
