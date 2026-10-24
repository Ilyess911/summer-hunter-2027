// Display helpers. Unknown values stay visibly unknown, never zero.

export const STATUS_LABEL: Record<string, string> = {
  DISCOVERED: "Discovered", SHORTLISTED: "Shortlisted", TO_APPLY: "To apply", PREPARING: "Preparing", APPLIED: "Applied",
  ONLINE_ASSESSMENT: "Assessment", INTERVIEW: "Interview", FINAL_INTERVIEW: "Final interview", OFFER_RECEIVED: "Offer",
  OFFER_ACCEPTED: "Offer accepted", REJECTED: "Rejected", WITHDRAWN: "Withdrawn", CLOSED: "Closed",
}

export const ACTION_LABEL: Record<string, string> = {
  APPLY_NOW: "Apply now", PREPARE_APPLICATION: "Prepare application", CONTACT_RECRUITER: "Contact recruiter",
  VERIFY_ELIGIBILITY: "Verify eligibility", VERIFY_COMPENSATION: "Verify pay", MONITOR_OPENING: "Monitor",
  LOW_PRIORITY: "Low priority", DISCARD: "Discard",
}

export const TIMING_LABEL: Record<string, string> = {
  CONTACT_BEFORE_APPLICATION: "Contact before applying", CONTACT_AFTER_APPLICATION: "Contact after applying",
  FOLLOW_UP: "Follow up", WAIT: "Wait", DO_NOT_CONTACT: "Do not contact",
}

export const MESSAGE_KIND: Record<string, string> = {
  connection_request: "Connection request", initial: "Initial message", before_applying: "Before applying",
  after_applying: "After applying", referral_request: "Referral request", alumni: "Alumni outreach", follow_up: "Follow-up",
  thank_you: "Thank-you", post_interview: "Post-interview",
}

export function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—"
  return `€${Math.round(v).toLocaleString("en-GB")}`
}

export function pay(c: { status: string | null; monthly_min_eur: number | null; monthly_max_eur?: number | null; basis?: string | null }): string {
  if (c.monthly_min_eur !== null && c.monthly_min_eur !== undefined) {
    const max = c.monthly_max_eur
    const range = max && Math.abs(max - c.monthly_min_eur) > Math.max(1, c.monthly_min_eur * 0.01)
      ? `${money(c.monthly_min_eur)}–${Math.round(max).toLocaleString("en-GB")}` : money(c.monthly_min_eur)
    return `${range}/mo`
  }
  switch (c.status) {
    case "UNPAID_CONFIRMED": return "Unpaid"
    case "PAID_AMOUNT_UNKNOWN": return "Paid, amount ?"
    case "MIXED_OR_CONDITIONAL": return "Conditional"
    default: return "Not stated"
  }
}

export function date(v: string | null | undefined, withYear = false): string {
  if (!v) return "—"
  const d = new Date(v)
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", ...(withYear ? { year: "numeric" } : {}) })
}

export function relative(v: string | null | undefined): string {
  if (!v) return "never"
  const s = (Date.now() - new Date(v).getTime()) / 1000
  if (s < 60) return "just now"
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

export function score(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : String(Math.round(v))
}
