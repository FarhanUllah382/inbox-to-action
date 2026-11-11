import styles from "./WhatYoureLookingAt.module.css";

// TASK 2 — demo-mode-only explainer panel, rendered immediately after the
// existing "Demo mode" banner (unchanged, see page.tsx). Plain-English
// walkthrough of the three synthetic candidates a visitor will see in
// this demo, so the two safety checks (prompt-injection flag,
// spoofed-sender flag) and the ordinary approval flow read as deliberate
// features rather than being inferred from the UI alone.

export default function WhatYoureLookingAt() {
  return (
    <div className={styles.panel}>
      <p className={styles.title}>What you are looking at</p>
      <ul className={styles.list}>
        <li>
          One email hides a secret instruction trying to trick the assistant into
          taking an action nobody asked for out loud — it gets flagged and is never
          acted on.
        </li>
        <li>
          Another looks like an ordinary request from a colleague, but the real
          email address behind the display name does not match who it claims to
          be from — that mismatch is caught automatically and flagged too.
        </li>
        <li>
          A third is just a normal task sitting in the queue, waiting for a person
          to approve, edit, or dismiss it — nothing reaches Notion without that
          click.
        </li>
      </ul>
      <p className={styles.footer}>
        <a href="https://github.com/FarhanUllah382/inbox-to-action" target="_blank" rel="noopener noreferrer">
          View the source on GitHub ↗
        </a>
      </p>
    </div>
  );
}
