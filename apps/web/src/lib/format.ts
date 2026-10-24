// Display helpers (French). Unknown values stay visibly unknown, never zero.

export const STATUS_LABEL: Record<string, string> = {
  DISCOVERED: "Découverte", SHORTLISTED: "Présélection", TO_APPLY: "À postuler", PREPARING: "En préparation", APPLIED: "Postulé",
  ONLINE_ASSESSMENT: "Test en ligne", INTERVIEW: "Entretien", FINAL_INTERVIEW: "Entretien final", OFFER_RECEIVED: "Offre",
  OFFER_ACCEPTED: "Offre acceptée", REJECTED: "Refusé", WITHDRAWN: "Retiré", CLOSED: "Clôturé",
}

export const ACTION_LABEL: Record<string, string> = {
  APPLY_NOW: "Postuler", PREPARE_APPLICATION: "Préparer", CONTACT_RECRUITER: "Contacter",
  VERIFY_ELIGIBILITY: "Vérifier l’éligibilité", VERIFY_COMPENSATION: "Vérifier la paie", MONITOR_OPENING: "Surveiller",
  LOW_PRIORITY: "Faible priorité", DISCARD: "Écarter",
}

export const TIMING_LABEL: Record<string, string> = {
  CONTACT_BEFORE_APPLICATION: "Avant de postuler", CONTACT_AFTER_APPLICATION: "Après la candidature",
  FOLLOW_UP: "Relancer", WAIT: "Attendre", DO_NOT_CONTACT: "Ne pas contacter",
}

export const MESSAGE_KIND: Record<string, string> = {
  connection_request: "Demande de connexion", initial: "Premier message", before_applying: "Avant candidature",
  after_applying: "Après candidature", referral_request: "Demande de recommandation", alumni: "Ancien élève", follow_up: "Relance",
  thank_you: "Remerciement", post_interview: "Après entretien",
}

export const VARIANT_LABEL: Record<string, string> = {
  default: "Standard", shorter: "Plus court", more_direct: "Plus direct", more_formal: "Plus formel", more_natural: "Plus naturel",
}

const NUM = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 })

export function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—"
  return `${NUM.format(Math.round(v))} €`
}

export function pay(c: { status: string | null; monthly_min_eur: number | null; monthly_max_eur?: number | null; basis?: string | null }): string {
  if (c.monthly_min_eur !== null && c.monthly_min_eur !== undefined) {
    const max = c.monthly_max_eur
    const range = max && Math.abs(max - c.monthly_min_eur) > Math.max(1, c.monthly_min_eur * 0.01)
      ? `${NUM.format(Math.round(c.monthly_min_eur))}–${NUM.format(Math.round(max))} €` : money(c.monthly_min_eur)
    return `${range}/mois`
  }
  switch (c.status) {
    case "UNPAID_CONFIRMED": return "Non payé"
    case "PAID_AMOUNT_UNKNOWN": return "Payé, montant ?"
    case "MIXED_OR_CONDITIONAL": return "Conditionnel"
    default: return "Non précisé"
  }
}

export function date(v: string | null | undefined, withYear = false): string {
  if (!v) return "—"
  const d = new Date(v)
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short", ...(withYear ? { year: "numeric" } : {}) })
}

export function relative(v: string | null | undefined): string {
  if (!v) return "jamais"
  const s = (Date.now() - new Date(v).getTime()) / 1000
  if (s < 60) return "à l’instant"
  if (s < 3600) return `il y a ${Math.round(s / 60)} min`
  if (s < 86400) return `il y a ${Math.round(s / 3600)} h`
  return `il y a ${Math.round(s / 86400)} j`
}

export function score(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : String(Math.round(v))
}

export function plural(n: number, one: string, many: string): string {
  return `${NUM.format(n)} ${n > 1 ? many : one}`
}

export const CAREER_LABEL: Record<string, string> = {
  "Business Development": "Développement commercial", "Industrial Digitalization": "Digitalisation industrielle",
  "Industrial Operations": "Opérations industrielles", "Industrial Project Management": "Gestion de projets industriels",
  Maintenance: "Maintenance", Other: "Autre", "Product Management": "Product management", "Product Marketing": "Marketing produit",
  Robotics: "Robotique", "Robotics (specialized engineering)": "Robotique (ingénierie spécialisée)", Strategy: "Stratégie",
  "Technical Sales": "Vente technique",
}

export function career(c: string | null | undefined): string {
  return c ? CAREER_LABEL[c] ?? c : "Non classé"
}

export const EVENT_LABEL: Record<string, string> = {
  application_created: "Candidature créée", contact_added: "Contact ajouté", contact_updated: "Contact modifié",
  document_updated: "Document mis à jour", documents_frozen: "Documents figés", field_updated: "Champ modifié",
  follow_up_completed: "Relance faite", interview_scheduled: "Entretien planifié", status_changed: "Statut changé",
}

export const SOURCE_LABEL: Record<string, string> = {
  web: "app web", sync: "synchro", telegram: "Telegram", miniapp: "Mini App", cli: "terminal", manual: "manuel", posting: "offre",
  excel: "Excel",
}

export const FAMILY_LABEL: Record<string, string> = {
  out_of_scope: "Hors périmètre", unclassified: "Non classé", software_engineering: "Ingénierie logicielle", go_to_market: "Go-to-market",
  technical_sales: "Vente technique", sales_general: "Vente", ai_solutions: "Solutions IA", design_engineering: "Ingénierie de conception",
  data_analytics: "Analyse de données", industrial_project_management: "Projets industriels", strategy_consulting: "Conseil en stratégie",
  product_management: "Product management", product_marketing: "Marketing produit", sales_development: "Prospection commerciale",
  marketing_general: "Marketing", industrial_operations: "Opérations industrielles", robotics_raas: "Robotique (RaaS)",
  program_management: "Gestion de programme", enterprise_sales: "Vente grands comptes", maintenance_reliability: "Maintenance et fiabilité",
  industrial_digital: "Digital industriel", robotics_specialized: "Robotique spécialisée", commercial_strategy: "Stratégie commerciale",
}

export function family(f: string): string {
  return FAMILY_LABEL[f.replaceAll(" ", "_")] ?? f
}

export const FUNNEL_LABEL: Record<string, string> = {
  Tracked: "Suivies", Submitted: "Envoyées", Response: "Réponse", Interview: "Entretien", Offer: "Offre", Accepted: "Acceptée",
}
