"use client";

import { useState } from "react";
import { apiCall } from "@/lib/api";
import styles from "./ResetDemoButton.module.css";

// TASK 1 — demo-mode-only control. Calls POST /demo/reset (backend wipes
// and repopulates the candidate list with the same fixed synthetic demo
// data a fresh startup would have — no live Claude calls). Only rendered
// when demoMode is true (see page.tsx), so this never shows against a
// real Gmail/Notion-connected backend. Follows the same busy/status
// pattern as handleScan/handleExtract in page.tsx.

type Props = {
  onReset: () => Promise<void> | void;
};

export default function ResetDemoButton({ onReset }: Props) {
  const [resetting, setResetting] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  async function handleReset() {
    setResetting(true);
    setStatus(null);
    try {
      await apiCall("/demo/reset", { method: "POST" });
      setStatus("Demo reset — reloading...");
      await onReset();
    } catch (e) {
      setStatus(e instanceof Error ? `Error: ${e.message}` : "Reset failed");
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className={styles.wrap}>
      <button onClick={handleReset} disabled={resetting}>
        {resetting ? "Resetting…" : "Reset demo"}
      </button>
      {status && <span className={styles.status}>{status}</span>}
    </div>
  );
}
