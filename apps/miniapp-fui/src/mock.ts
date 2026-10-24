// MOCK DATA for the design prototype only. Fictional companies (DC universe), never shown in the live Mini App.

export type Signal = "cyan" | "amber" | "red"

export type Job = {
  id: string
  company: string
  role: string
  location: string
  mode: "Sur site" | "Hybride" | "Remote"
  pay: { min: number; max: number; currency: "EUR" | "USD"; period: "mois" }
  stack: string[]
  status: string
  signal: Signal
  stage: number // 1..STAGES.length
  match: number // 0-100
  deadline: string | null // ISO date
  nextStep: string
  updated: string // ISO datetime
  log: { at: string; text: string }[]
}

export const STAGES = ["Repérée", "Postulée", "Entretien", "Final", "Offre"] as const

export const JOBS: Job[] = [
  {
    id: "JB-0007",
    company: "Wayne Enterprises",
    role: "Stagiaire R&D, Applied Sciences",
    location: "Gotham City",
    mode: "Sur site",
    pay: { min: 2400, max: 2800, currency: "EUR", period: "mois" },
    stack: ["Python", "ROS2", "Data analysis", "Lean"],
    status: "Entretien planifié",
    signal: "cyan",
    stage: 3,
    match: 92,
    deadline: null,
    nextStep: "Entretien technique · 04/11 · 14:00",
    updated: "2026-10-24T18:20:00Z",
    log: [
      { at: "2026-10-12", text: "Offre repérée (flux carrière)" },
      { at: "2026-10-14", text: "Candidature envoyée" },
      { at: "2026-10-22", text: "Appel RH validé" },
      { at: "2026-10-24", text: "Entretien technique fixé" },
    ],
  },
  {
    id: "JB-0004",
    company: "Kord Industries",
    role: "Stagiaire Robotique industrielle",
    location: "Chicago, US",
    mode: "Hybride",
    pay: { min: 3100, max: 3100, currency: "USD", period: "mois" },
    stack: ["PLC", "Automation", "KPI", "Excel"],
    status: "En attente de réponse",
    signal: "amber",
    stage: 2,
    match: 81,
    deadline: null,
    nextStep: "Relance conseillée dans 2 j ouvrés",
    updated: "2026-10-18T09:05:00Z",
    log: [
      { at: "2026-10-09", text: "Offre repérée (ATS public)" },
      { at: "2026-10-18", text: "Candidature envoyée, CV adapté" },
    ],
  },
  {
    id: "JB-0011",
    company: "Queen Consolidated",
    role: "Business Developer Intern",
    location: "Star City",
    mode: "Hybride",
    pay: { min: 1800, max: 2200, currency: "EUR", period: "mois" },
    stack: ["CRM", "Prospection", "SQL", "Anglais C1"],
    status: "Date limite proche",
    signal: "red",
    stage: 1,
    match: 74,
    deadline: "2026-10-27",
    nextStep: "Postuler avant le 27/10",
    updated: "2026-10-23T21:40:00Z",
    log: [{ at: "2026-10-23", text: "Offre repérée, date limite détectée" }],
  },
]
