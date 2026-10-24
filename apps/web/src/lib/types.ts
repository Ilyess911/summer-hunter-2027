// Shapes returned by the API (src/summer_hunter/os/views.py).

export type Compensation = {
  status: string | null
  monthly_min_eur: number | null
  monthly_max_eur: number | null
  currency: string | null
  period: string | null
  basis: string | null
  confidence: string | null
  score: number | null
  balance_eur: number | null
  living_cost_eur: number | null
}

export type OpportunityRow = {
  id: number
  company: string
  title: string
  country: string | null
  country_code: string | null
  city: string | null
  region: string
  location: string | null
  family: string | null
  category: string | null
  track: string | null
  fit: number | null
  recruitability: number | null
  networking: number | null
  priority: number | null
  provisional: boolean
  eligibility: string | null
  compensation: Compensation
  next_action: string | null
  strategy: string | null
  deadline: string | null
  deadline_type: string | null
  discovered: string | null
  verified: string | null
  status: string
  user_status: string
  application: { id: number; code: string; status: string } | null
  apply_url: string | null
}

export type Criterion = { criterion: string; status: string; explanation: string; material?: boolean }

export type Timing = { action: string | null; date: string | null; reason: string | null; confidence: string | null }

export type ContactRow = {
  id: number
  code: string
  name: string
  company: string
  position: string | null
  category: string
  category_label: string
  linkedin_url: string | null
  verification: "verified" | "unverified"
  status: string
  next_follow_up: string | null
  dismissed: boolean
  score?: number | null
  components?: Record<string, number> | null
  explanation?: string | null
  timing?: Timing
  note?: string | null
  opportunity?: { id: number; title: string; company?: string; priority?: number | null } | null
  due?: boolean
}

export type OpportunityDetail = OpportunityRow & {
  description: string
  official_url: string | null
  sources: { source: string; url: string | null; first_seen: string | null; last_seen: string | null }[]
  criteria: Criterion[]
  sponsorship: string | null
  fit_report: { score: number; reasons: string[]; weaknesses: string[]; components: Record<string, number> } | null
  recruitability_report: { score: number; reasons: string[]; concerns: string[]; missing_requirements: string[];
    relevant_experience: string[]; components: Record<string, number>; note: string } | null
  compensation_report: Record<string, unknown> | null
  strategy: { action: string; category: string; why: string; arguments: string[]; gaps: string[]; compensation: string;
    urgency: string; networking: string; effort: string; caveat: string | null;
    networking_reco?: unknown; global?: { weights: Record<string, number>; components: Record<string, number>; notes: string[] } } | null
  contacts: ContactRow[]
  signals: { level: string; summary: string; url: string | null; published: string | null }[]
}

export type ApplicationRow = {
  id: number
  code: string
  status: string
  opportunity_id: number
  company: string
  title: string
  country: string | null
  priority: number | null
  compensation: { monthly_min_eur: number | null; status: string | null; basis: string | null }
  deadline: string | null
  date_applied: string | null
  next_action: string | null
  next_action_date: string | null
  interview_at: string | null
  version: number
  updated: string | null
}

export type ApplicationDetail = ApplicationRow & {
  opportunity: OpportunityDetail
  notes: string | null
  recruiter: string | null
  furthest_status: string
  documents: { cv: { version: number; generated: string | null; available: boolean };
    letter: { version: number; generated: string | null; available: boolean }; submitted_folder: boolean }
  timeline: { at: string; type: string; previous: string | null; new: string | null; source: string; notes: string | null }[]
  allowed_statuses: string[]
}

export type Message = {
  id: number
  contact_id: number
  opportunity_id: number | null
  kind: string
  variant: string
  text: string
  generator: string
  evidence: { kind: string; url?: string; term?: string; experience?: string; source?: string }[]
  validation: { ok?: boolean; issues?: string[]; chars?: number; words?: number; personalization?: string; checked?: string[] }
  personalization: string
  status: "draft" | "sent" | "dismissed"
  created: string
  sent: string | null
}

export type ContactDetail = ContactRow & {
  notes: string | null
  source: string
  evidence: { kind: string; url: string | null; excerpt: string | null; verified: boolean; retrieved: string | null; opportunity_id: number | null }[]
  opportunities: ({ opportunity: { id: number; title: string; company: string; country: string; priority: number | null } } &
    { score: number | null; components: Record<string, number> | null; explanation: string | null; timing: Timing; note: string | null })[]
  messages: Message[]
  interactions: { id: number; kind: string; at: string; note: string | null; opportunity_id: number | null; follow_up_on: string | null }[]
}

export type Priority = { key: string; kind: string; title: string; detail: string; href: string; rank: number }

export type CommandCenter = {
  counts: { active: number; new: number; awaiting: number; interviews: number; offers: number; paid: number; deadlines: number }
  priorities: Priority[]
  top: OpportunityRow[]
  networking: ContactRow[]
  last_run: { status: string; started: string; finished: string | null } | null
}

export type Meta = {
  mode: "local" | "hosted"
  last_sync: string | null
  last_run: { status: string; started: string; finished: string | null } | null
  features: { documents: boolean; ai_messages: boolean; contact_search: boolean }
  recruitment_mode: string
}

export type Paged<T> = { items: T[]; total: number; page: number; page_size: number;
  facets: { categories: string[]; countries: { code: string; name: string }[] } }
