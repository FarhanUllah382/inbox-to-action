"use client";

// Shown while page.tsx is still waiting to hear back from GET /api/info —
// either a genuinely cold Render free-tier instance (can take up to a
// minute to wake) or, previously, a browser ad/privacy blocker silently
// failing the mode-detection call outright (fixed by moving that call off
// a "/health"-shaped path — see app/main.py's GET /api/info). Replaces a
// blank screen / silent fallback to the login screen with an honest status.

import styles from "./WakingUpScreen.module.css";

type Props = {
  phase: "waking" | "timed_out";
  onRetry: () => void;
};

export default function WakingUpScreen({ phase, onRetry }: Props) {
  return (
    <main className={styles.main}>
      <div className={styles.card}>
        {phase === "waking" ? (
          <>
            <div className={styles.spinner} aria-hidden="true" />
            <p className={styles.message}>
              Waking up the demo server — free tier, this can take up to a minute…
            </p>
          </>
        ) : (
          <>
            <p className={styles.message}>The demo server didn&apos;t respond — please refresh in a minute.</p>
            <button className="primary" onClick={onRetry}>
              Retry
            </button>
          </>
        )}
      </div>
    </main>
  );
}
