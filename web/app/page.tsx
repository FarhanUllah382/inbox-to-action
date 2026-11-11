"use client";

import { useEffect, useState } from "react";
import { API_URL, AuthError, apiCall, type Candidate, type EditForm } from "@/lib/api";
import { clearToken, getToken } from "@/lib/auth";
import { useEventSource, type LiveEvent } from "./hooks/useEventSource";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";
import DashboardSummary from "./components/DashboardSummary";
import CandidateList from "./components/CandidateList";
import LoginScreen from "./components/LoginScreen";
import ResetDemoButton from "./components/ResetDemoButton";
import Toast, { type ToastItem } from "./components/Toast";
import WakingUpScreen from "./components/WakingUpScreen";
import WhatYoureLookingAt from "./components/WhatYoureLookingAt";
import styles from "./page.module.css";

// Build-time only, set in render.yaml for inbox-to-action-demo-web (never
// for inbox-to-action-web). Decides exactly two things, both unconditional:
// never show LoginScreen, and show the demo banner. It does NOT skip the
// GET /api/info wake-up check below — the demo deployment is exactly where
// a cold Render free-tier instance is most likely to be hit, so it needs
// the same "waking up" screen as everyone else, not an early return straight
// into a candidate list that doesn't exist yet.
const DEMO_MODE_BUILD_FLAG = process.env.NEXT_PUBLIC_DEMO_MODE === "true";

// How long to keep retrying GET /api/info before giving up and showing the
// explicit "didn't respond" screen — Render's free tier can take up to
// ~60s to wake a cold instance, so this is deliberately longer than that.
const WAKE_RETRY_INTERVAL_MS = 3000;
const WAKE_MAX_WAIT_MS = 90000;

// The initial candidate load can land in the same cold-start window as the
// /api/info check above (the API answering /api/info doesn't guarantee the
// very next request won't also hit a still-warming-up backend) — retried a
// few times before actually surfacing an error, rather than every visitor's
// first load being a coin flip against a server that just woke up.
const INITIAL_LOAD_RETRY_ATTEMPTS = 3;
const INITIAL_LOAD_RETRY_INTERVAL_MS = 3000;

// V2.7 (Design Decisions V2.7): Gmail is now scanned+extracted
// automatically every ~2 minutes by app/scheduler.py — the manual
// Scan/Extract buttons below stay for on-demand use (e.g. "I know a new
// email just arrived, don't make me wait"), they no longer are the only
// way candidates appear. New candidates arrive via the SSE toast/badge
// below (useEventSource -> GET /events) without a page reload.

let toastCounter = 0;
function nextToastId(): string {
  toastCounter += 1;
  return `toast-${toastCounter}`;
}

export default function ReviewPage() {
  // loggedIn stays null until wakePhase reaches "ready" — see the effect
  // below. Kept separate from wakePhase (rather than folding "not checked
  // yet" into loggedIn===null the way the old /health-based check did) so
  // the demo build can go through the exact same wake-up wait as everyone
  // else without it being mistaken for "needs a login".
  const [loggedIn, setLoggedIn] = useState<boolean | null>(null);
  const [demoMode, setDemoMode] = useState(DEMO_MODE_BUILD_FLAG);
  const [wakePhase, setWakePhase] = useState<"waking" | "ready" | "timed_out">("waking");
  const [wakeRetryToken, setWakeRetryToken] = useState(0);

  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // GET /api/info needs no token (app/main.py — same payload as /health,
  // deliberately not on a "/health"-shaped path some browser ad/privacy
  // blockers silently fail requests to). Runs unconditionally, demo build
  // included — DEMO_MODE_BUILD_FLAG only decides what happens once this
  // succeeds (never show LoginScreen), not whether to wait for it at all.
  // Retries every WAKE_RETRY_INTERVAL_MS for up to WAKE_MAX_WAIT_MS — a
  // cold Render free-tier instance can take up to ~60s to answer at all —
  // rather than falling back to the login screen on the first failed
  // attempt.
  useEffect(() => {
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout>;
    const startedAt = Date.now();

    async function attempt() {
      try {
        const res = await fetch(`${API_URL}/api/info`);
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        if (DEMO_MODE_BUILD_FLAG) {
          // Authoritative regardless of what the API actually reports —
          // the build flag's whole job is "never show LoginScreen" on this
          // deployment. Reaching the API at all is enough to proceed.
          setLoggedIn(true);
        } else {
          const isDemo = Boolean(data.demo_mode);
          setDemoMode(isDemo);
          setLoggedIn(isDemo ? true : getToken() !== null);
        }
        setWakePhase("ready");
      } catch {
        if (cancelled) return;
        if (Date.now() - startedAt >= WAKE_MAX_WAIT_MS) {
          setWakePhase("timed_out");
          return;
        }
        timeoutId = setTimeout(attempt, WAKE_RETRY_INTERVAL_MS);
      }
    }

    attempt();
    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [wakeRetryToken]);

  function retryWakeUp() {
    // Set synchronously from the click handler, not inside the effect
    // body — wakePhase is already "waking" on first mount (its initial
    // state), this only needs to reset it back from "timed_out" when the
    // Retry button bumps wakeRetryToken and re-runs the effect above.
    setWakePhase("waking");
    setWakeRetryToken((n) => n + 1);
  }

  function handleAuthError(e: unknown): boolean {
    if (e instanceof AuthError) {
      setLoggedIn(false);
      return true;
    }
    return false;
  }

  function logout() {
    clearToken();
    setLoggedIn(false);
  }

  const [scanStatus, setScanStatus] = useState<string | null>(null);
  const [extractStatus, setExtractStatus] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [extracting, setExtracting] = useState(false);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<EditForm>({ task: "", deadline_phrase: "", assignee: "" });
  const [busyId, setBusyId] = useState<number | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<Record<number, string>>({});

  const [toasts, setToasts] = useState<ToastItem[]>([]);

  // silent=true skips setError on failure — used only by the initial-load
  // retry wrapper below, so a transient failure right after the server
  // just woke up doesn't flash an error the next attempt is about to
  // clear anyway. Every other call site (manual Refresh, after
  // approve/dismiss/edit/scan/extract) keeps today's single-attempt,
  // error-surfacing behavior.
  async function loadCandidates(opts: { silent?: boolean } = {}): Promise<boolean> {
    setLoading(true);
    if (!opts.silent) setError(null);
    try {
      const data = await apiCall("/candidates");
      setCandidates(data);
      setError(null);
      return true;
    } catch (e) {
      if (handleAuthError(e)) return true; // auth flow takes over; not a retry case
      if (!opts.silent) {
        setError(e instanceof Error ? e.message : "Failed to load candidates");
      }
      return false;
    } finally {
      setLoading(false);
    }
  }

  async function loadCandidatesInitial(attemptsLeft = INITIAL_LOAD_RETRY_ATTEMPTS) {
    const isLastAttempt = attemptsLeft <= 1;
    const ok = await loadCandidates({ silent: !isLastAttempt });
    if (ok || isLastAttempt) return;
    await new Promise((resolve) => setTimeout(resolve, INITIAL_LOAD_RETRY_INTERVAL_MS));
    await loadCandidatesInitial(attemptsLeft - 1);
  }

  useEffect(() => {
    if (!loggedIn) return;
    // loadCandidatesInitial() sets state (setLoading/setError) before its
    // first await — calling it directly here would run that synchronously
    // within the effect body, which react-hooks now flags. Deferring
    // through a microtask keeps this an on-mount fetch (fires before the
    // next paint) without a synchronous setState-in-effect call.
    queueMicrotask(() => {
      loadCandidatesInitial();
    });
  }, [loggedIn]);

  function pushToast(message: string, kind: ToastItem["kind"]) {
    setToasts((prev) => {
      // Don't stack duplicate persistent auth-error prompts (V2.7 Decision
      // 2 — the backend already debounces publishing these, but guard here
      // too in case of a reconnect replaying state).
      if (kind === "auth_error" && prev.some((t) => t.kind === "auth_error")) return prev;
      const id = nextToastId();
      if (kind === "info") {
        setTimeout(() => setToasts((cur) => cur.filter((t) => t.id !== id)), 6000);
      }
      return [...prev, { id, message, kind }];
    });
  }

  const { connected } = useEventSource((event: LiveEvent) => {
    if (event.type === "new_candidate") {
      pushToast(`New email: "${event.task || event.email_subject}" — 1 new task to review`, "info");
      loadCandidates();
    } else if (event.type === "auth_error") {
      pushToast(event.message, "auth_error");
    }
  }, loggedIn === true);

  async function handleScan() {
    setScanning(true);
    setScanStatus(null);
    try {
      const result = await apiCall("/scan", { method: "POST" });
      setScanStatus(`Fetched ${result.fetched}, ${result.new} new (${result.skipped} already stored)`);
    } catch (e) {
      if (!handleAuthError(e)) {
        setScanStatus(e instanceof Error ? `Error: ${e.message}` : "Scan failed");
      }
    } finally {
      setScanning(false);
    }
  }

  async function handleExtract() {
    setExtracting(true);
    setExtractStatus(null);
    try {
      const result = await apiCall("/extract", { method: "POST" });
      setExtractStatus(
        `Processed ${result.processed}, ${result.candidates_created} candidate(s) created ` +
          `(${result.not_actionable} not actionable, ${result.failed} failed)`
      );
      await loadCandidates();
    } catch (e) {
      if (!handleAuthError(e)) {
        setExtractStatus(e instanceof Error ? `Error: ${e.message}` : "Extraction failed");
      }
    } finally {
      setExtracting(false);
    }
  }

  function startEdit(candidate: Candidate) {
    setEditingId(candidate.id);
    setEditForm({
      task: candidate.task || "",
      deadline_phrase: candidate.deadline_phrase || "",
      assignee: candidate.assignee || "",
    });
  }

  function cancelEdit() {
    setEditingId(null);
  }

  async function saveEdit(id: number) {
    setBusyId(id);
    try {
      await apiCall(`/candidates/${id}`, {
        method: "PATCH",
        body: JSON.stringify(editForm),
      });
      setEditingId(null);
      await loadCandidates();
    } catch (e) {
      if (!handleAuthError(e)) {
        setError(e instanceof Error ? e.message : "Failed to save edit");
      }
    } finally {
      setBusyId(null);
    }
  }

  async function approve(id: number) {
    setBusyId(id);
    try {
      await apiCall(`/candidates/${id}/approve`, { method: "POST" });
      await loadCandidates();
    } catch (e) {
      if (!handleAuthError(e)) {
        setError(e instanceof Error ? e.message : "Failed to approve candidate");
      }
    } finally {
      setBusyId(null);
    }
  }

  async function dismiss(id: number) {
    setBusyId(id);
    try {
      await apiCall(`/candidates/${id}/dismiss`, { method: "POST" });
      await loadCandidates();
    } catch (e) {
      if (!handleAuthError(e)) {
        setError(e instanceof Error ? e.message : "Failed to dismiss candidate");
      }
    } finally {
      setBusyId(null);
    }
  }

  // Separate, explicit action from Approve — booking a calendar event is a
  // distinct commitment from creating a task (V2.6 Decision 5). Never
  // triggered automatically or bundled into approve().
  async function bookCalendar(id: number, slot: string) {
    setBusyId(id);
    try {
      await apiCall(`/candidates/${id}/book-calendar`, {
        method: "POST",
        body: JSON.stringify({ slot }),
      });
      await loadCandidates();
    } catch (e) {
      if (!handleAuthError(e)) {
        setError(e instanceof Error ? e.message : "Failed to add to calendar");
      }
    } finally {
      setBusyId(null);
    }
  }

  function selectSlot(id: number, slot: string) {
    setSelectedSlot((prev) => ({ ...prev, [id]: slot }));
  }

  useKeyboardShortcuts({
    candidateIds: candidates.map((c) => c.id),
    selectedId,
    onSelect: setSelectedId,
    onApprove: approve,
    onDismiss: dismiss,
    onEdit: (id) => {
      const candidate = candidates.find((c) => c.id === id);
      if (candidate) startEdit(candidate);
    },
    enabled: editingId === null && busyId === null,
  });

  if (wakePhase !== "ready") {
    // GET /api/info hasn't answered yet (cold instance, still retrying)
    // or every retry within WAKE_MAX_WAIT_MS failed — see the effect
    // above. loggedIn is guaranteed non-null once wakePhase is "ready"
    // (both are set together in the same attempt()), so there's no
    // separate "sessionStorage check in flight" case left to handle here.
    return <WakingUpScreen phase={wakePhase} onRetry={retryWakeUp} />;
  }

  if (!loggedIn) {
    return (
      <>
        {demoMode && (
          <>
            <div className={styles.demoBanner}>Demo mode — synthetic data, no real Gmail/Notion access</div>
            <WhatYoureLookingAt />
          </>
        )}
        <LoginScreen onSuccess={() => setLoggedIn(true)} />
      </>
    );
  }

  return (
    <main className={styles.main}>
      {demoMode && (
        <>
          <div className={styles.demoBanner}>Demo mode — synthetic data, no real Gmail/Notion access</div>
          <WhatYoureLookingAt />
        </>
      )}
      <div className={styles.headerRow}>
        <h1 className={styles.title}>Inbox-to-Action</h1>
        <span className={styles.liveIndicator}>
          <span className={`${styles.liveDot} ${connected ? styles.connected : ""}`} />
          {connected ? "Live" : "Connecting…"}
        </span>
        {!demoMode && <button onClick={logout}>Log out</button>}
      </div>
      <p className={styles.subtitle}>
        Gmail is checked automatically every ~2 minutes. Review task candidates before they go to
        Notion — nothing is written to Notion here.
      </p>
      <p className={styles.shortcutHint}>Shortcuts: j/k navigate · a approve · d dismiss · e edit</p>

      <div className={styles.manualControls}>
        <button onClick={handleScan} disabled={scanning}>
          {scanning ? "Scanning…" : "Scan Gmail now"}
        </button>
        <button onClick={handleExtract} disabled={extracting}>
          {extracting ? "Extracting…" : "Extract tasks now"}
        </button>
        <button onClick={() => loadCandidates()} disabled={loading}>
          Refresh
        </button>
        {demoMode && <ResetDemoButton onReset={async () => { await loadCandidates(); }} />}
      </div>

      {(scanStatus || extractStatus) && (
        <div className={styles.manualStatus}>
          {scanStatus && <div>Scan: {scanStatus}</div>}
          {extractStatus && <div>Extract: {extractStatus}</div>}
        </div>
      )}

      {error && <p className={styles.errorText}>{error}</p>}

      <DashboardSummary candidates={candidates} />

      <CandidateList
        candidates={candidates}
        loading={loading}
        selectedId={selectedId}
        editingId={editingId}
        busyId={busyId}
        editForm={editForm}
        selectedSlot={selectedSlot}
        onSelect={setSelectedId}
        onStartEdit={startEdit}
        onCancelEdit={cancelEdit}
        onEditFormChange={setEditForm}
        onSaveEdit={saveEdit}
        onApprove={approve}
        onDismiss={dismiss}
        onBookCalendar={bookCalendar}
        onSelectSlot={selectSlot}
      />

      <Toast toasts={toasts} onDismiss={(id) => setToasts((prev) => prev.filter((t) => t.id !== id))} />
    </main>
  );
}
