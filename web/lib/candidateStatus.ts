// Single source of truth for candidate status -> label/color (Design
// Decisions V2.7, Decision 6: "the same [colors] used consistently across
// the candidate list, the dashboard summary, and toasts — never redefined
// per-component"). CandidateCard, DashboardSummary, and Toast all read
// from getStatusInfo() instead of deciding colors themselves.
//
// "flagged" takes priority over every other status (Decision 7 item 3) —
// a candidate injection_suspected or sender_trust_signal === "suspicious"
// must read as flagged even if it's also, say, still "pending".

import type { Candidate } from "./api";

export type StatusKey = "flagged" | "approved" | "dismissed" | "scheduled" | "edited" | "pending";

export type StatusInfo = {
  key: StatusKey;
  label: string;
  bgVar: string;
  fgVar: string;
};

export function isFlagged(candidate: Candidate): boolean {
  return Boolean(candidate.injection_suspected) || candidate.sender_trust_signal === "suspicious";
}

export function getStatusInfo(candidate: Candidate): StatusInfo {
  if (isFlagged(candidate)) {
    // TASK 3: name the specific reason instead of a generic "Flagged for
    // review" — the two V1 safety checks (injection vs. sender-identity
    // mismatch) look identical today, which hides what was actually
    // caught. injection_suspected takes priority if both are ever true at
    // once (isFlagged's own OR means at least one is always true here, so
    // the sender-mismatch branch below is never reached with a blank
    // reason).
    const label = candidate.injection_suspected
      ? "Flagged — possible prompt injection"
      : "Flagged — sender identity doesn't match";
    return { key: "flagged", label, bgVar: "--status-flagged-bg", fgVar: "--status-flagged-fg" };
  }
  if (candidate.status === "approved") {
    return { key: "approved", label: "Approved", bgVar: "--status-approved-bg", fgVar: "--status-approved-fg" };
  }
  if (candidate.status === "dismissed") {
    return { key: "dismissed", label: "Dismissed", bgVar: "--status-dismissed-bg", fgVar: "--status-dismissed-fg" };
  }
  if (candidate.proposed_meeting_phrase) {
    return { key: "scheduled", label: "Meeting proposed", bgVar: "--status-scheduled-bg", fgVar: "--status-scheduled-fg" };
  }
  if (candidate.status === "edited") {
    return { key: "edited", label: "Edited", bgVar: "--status-pending-bg", fgVar: "--status-pending-fg" };
  }
  return { key: "pending", label: "Pending review", bgVar: "--status-pending-bg", fgVar: "--status-pending-fg" };
}
