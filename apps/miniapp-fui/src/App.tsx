import { useEffect, useMemo, useState } from "react"

import { JOBS, type Job, type Signal, STAGES } from "./mock"

// Telegram WebApp (optional: the page also runs in a plain browser for design review).
type TelegramWebApp = {
  ready: () => void
  expand: () => void
  setHeaderColor?: (c: string) => void
  setBackgroundColor?: (c: string) => void
  HapticFeedback?: { selectionChanged: () => void; impactOccurred: (s: "light" | "medium") => void }
}
const tg = (): TelegramWebApp | undefined => (window as unknown as { Telegram?: { WebApp?: TelegramWebApp } }).Telegram?.WebApp

const TONE: Record<Signal, { text: string; dot: string; bar: string; corner: string; chip: string }> = {
  cyan: { text: "text-tac-cyan", dot: "bg-tac-cyan", bar: "bg-tac-cyan", corner: "border-tac-cyan/70", chip: "bg-tac-cyan/10 ring-tac-cyan/30" },
  amber: { text: "text-tac-amber", dot: "bg-tac-amber", bar: "bg-tac-amber", corner: "border-tac-amber/70", chip: "bg-tac-amber/10 ring-tac-amber/30" },
  red: { text: "text-tac-red", dot: "bg-tac-red", bar: "bg-tac-red", corner: "border-tac-red/70", chip: "bg-tac-red/10 ring-tac-red/30" },
}

type Filter = "all" | "active" | "alert"
const FILTERS: [Filter, string][] = [["all", "Tout"], ["active", "En cours"], ["alert", "Alertes"]]

const fmt = new Intl.NumberFormat("fr-FR")
const pad = (n: number) => String(n).padStart(2, "0")

function daysUntil(iso: string): number {
  return Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000)
}

function ago(iso: string): string {
  const h = Math.round((Date.now() - new Date(iso).getTime()) / 3_600_000)
  return h < 1 ? "à l’instant" : h < 24 ? `il y a ${h} h` : `il y a ${Math.round(h / 24)} j`
}

function haptic(kind: "select" | "tap") {
  const h = tg()?.HapticFeedback
  if (!h) return
  if (kind === "select") h.selectionChanged()
  else h.impactOccurred("light")
}

/** Live status light: a solid dot with a subtle radar ping behind it. */
function Ping({ signal, still }: { signal: Signal; still?: boolean }) {
  return (
    <span className="relative flex size-2">
      {!still && <span className={`absolute inline-flex size-full animate-ping rounded-full opacity-50 ${TONE[signal].dot}`} />}
      <span className={`relative inline-flex size-2 rounded-full ${TONE[signal].dot}`} />
    </span>
  )
}

/** Corner reticles: four L-brackets plus a hairline crosshair, drawn in pure CSS borders. */
function Reticles({ tone }: { tone: string }) {
  const base = `pointer-events-none absolute size-2.5 ${tone}`
  return (
    <>
      <span className={`${base} -left-px -top-px border-l border-t`} />
      <span className={`${base} -right-px -top-px border-r border-t`} />
      <span className={`${base} -bottom-px -left-px border-b border-l`} />
      <span className={`${base} -bottom-px -right-px border-b border-r`} />
      <span className="pointer-events-none absolute right-3 top-3 size-2" aria-hidden>
        <span className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-zinc-700" />
        <span className="absolute left-0 top-1/2 h-px w-full -translate-y-1/2 bg-zinc-700" />
      </span>
    </>
  )
}

function Clock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  return <span className="num">{pad(now.getHours())}:{pad(now.getMinutes())}:{pad(now.getSeconds())}</span>
}

function Field({ label, value, tone, wrap }: { label: string; value: string; tone?: string; wrap?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="num text-[9px] uppercase tracking-[0.18em] text-zinc-500">{label}</div>
      <div className={`num mt-0.5 text-[12px] ${wrap ? "leading-snug" : "truncate"} ${tone ?? "text-zinc-200"}`}>{value}</div>
    </div>
  )
}

/** Thin segmented progress: one segment per pipeline stage. */
function StageBar({ job }: { job: Job }) {
  const t = TONE[job.signal]
  return (
    <div>
      <div className="num flex items-center justify-between text-[9px] uppercase tracking-[0.18em] text-zinc-500">
        <span>Phase {pad(job.stage)}/{pad(STAGES.length)} · <span className={t.text}>{STAGES[job.stage - 1]}</span></span>
        <span>{Math.round((job.stage / STAGES.length) * 100)}%</span>
      </div>
      <div className="mt-1.5 grid grid-cols-5 gap-1">
        {STAGES.map((s, i) => (
          <span key={s} className={`h-[2px] rounded-full ${i < job.stage ? t.bar : "bg-zinc-800"}`} />
        ))}
      </div>
    </div>
  )
}

function DataCard({ job, open, onToggle }: { job: Job; open: boolean; onToggle: () => void }) {
  const t = TONE[job.signal]
  const pay = job.pay.min === job.pay.max ? fmt.format(job.pay.min) : `${fmt.format(job.pay.min)}–${fmt.format(job.pay.max)}`
  const left = job.deadline ? daysUntil(job.deadline) : null
  return (
    <article className="relative border border-zinc-800 bg-panel/80 backdrop-blur-md transition-colors active:bg-zinc-900/80">
      <Reticles tone={open ? t.corner : "border-zinc-500"} />
      <button className="block w-full p-4 text-left" onClick={() => { haptic("select"); onToggle() }} aria-expanded={open}>
        <div className="flex items-center gap-2 pr-6">
          <Ping signal={job.signal} still={job.signal === "cyan" && !open} />
          <span className={`num text-[10px] uppercase tracking-[0.2em] ${t.text}`}>{job.status}</span>
          <span className="num ml-auto text-[10px] text-zinc-600">{job.id}</span>
        </div>

        <div className="mt-3">
          <div className="num text-[10px] uppercase tracking-[0.22em] text-zinc-500">{job.company}</div>
          <h2 className="mt-1 text-[16px] font-medium leading-snug text-zinc-50">{job.role}</h2>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-dashed border-zinc-800 pt-3">
          <Field label="Rémunération" value={`${pay} ${job.pay.currency}/${job.pay.period}`} />
          <Field label="Adéquation" value={`${job.match}/100`} tone={job.match >= 85 ? "text-zinc-50" : undefined} />
          <Field label="Zone" value={`${job.location} · ${job.mode}`} wrap />
          <Field label="Échéance" value={left === null ? "—" : left <= 0 ? "Aujourd’hui" : `J-${left}`} tone={left !== null && left <= 3 ? "text-tac-red" : undefined} />
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {job.stack.map((s) => (
            <span key={s} className="num border border-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">{s}</span>
          ))}
        </div>

        <div className="mt-4"><StageBar job={job} /></div>

        <div className={`mt-4 flex items-center gap-2 px-2.5 py-2 ring-1 ${t.chip}`}>
          <span className={`num text-[10px] uppercase tracking-[0.18em] ${t.text}`}>Next</span>
          <span className="truncate text-[12.5px] text-zinc-100">{job.nextStep}</span>
        </div>
      </button>

      <div className={`grid transition-[grid-template-rows] duration-300 ease-out ${open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"}`}>
        <div className="overflow-hidden">
          <div className="border-t border-zinc-800 px-4 pb-4 pt-3">
            <div className="num text-[9px] uppercase tracking-[0.18em] text-zinc-500">Journal</div>
            <ol className="mt-2 space-y-1.5">
              {job.log.map((l) => (
                <li key={l.at + l.text} className="flex gap-3 text-[12px]">
                  <span className="num w-[72px] shrink-0 text-zinc-500">{l.at.slice(8, 10)}/{l.at.slice(5, 7)}</span>
                  <span className="text-zinc-300">{l.text}</span>
                </li>
              ))}
            </ol>
            <div className="mt-4 grid grid-cols-2 gap-2">
              <button className="num border border-zinc-700 py-2.5 text-[11px] uppercase tracking-[0.16em] text-zinc-200 active:bg-zinc-900"
                      onClick={() => haptic("tap")}>Ouvrir l’offre</button>
              <button className={`num border py-2.5 text-[11px] uppercase tracking-[0.16em] active:bg-zinc-900 ${t.corner} ${t.text}`}
                      onClick={() => haptic("tap")}>{job.signal === "red" ? "Postuler" : job.signal === "amber" ? "Relancer" : "Préparer"}</button>
            </div>
          </div>
        </div>
      </div>
      <div className="num flex items-center justify-between border-t border-zinc-900 px-4 py-2 text-[9px] uppercase tracking-[0.18em] text-zinc-600">
        <span>Maj {ago(job.updated)}</span>
        <span>{open ? "▲ Réduire" : "▼ Détails"}</span>
      </div>
    </article>
  )
}

export default function App() {
  const [filter, setFilter] = useState<Filter>("all")
  const [open, setOpen] = useState<string | null>(JOBS[0].id)

  useEffect(() => {
    const app = tg()
    app?.ready()
    app?.expand()
    app?.setHeaderColor?.("#000000")
    app?.setBackgroundColor?.("#000000")
  }, [])

  const jobs = useMemo(() => JOBS.filter((j) => filter === "all" || (filter === "alert" ? j.signal === "red" : j.signal !== "red")), [filter])
  const stats = {
    active: JOBS.length,
    interviews: JOBS.filter((j) => j.stage >= 3).length,
    alerts: JOBS.filter((j) => j.signal === "red").length,
  }

  return (
    <div className="fui-grid min-h-full">
      <div className="mx-auto max-w-md px-4 pb-[calc(env(safe-area-inset-bottom)+24px)] pt-[calc(env(safe-area-inset-top)+12px)]">
        {/* HUD bar */}
        <header className="num flex items-center justify-between border-b border-zinc-900 pb-3 text-[10px] uppercase tracking-[0.2em] text-zinc-500">
          <span className="flex items-center gap-2 text-zinc-300">
            <svg viewBox="0 0 16 16" className="size-3.5" aria-hidden><path d="M1 3l3.5 10L8 6l3.5 7L15 3" fill="none" stroke="currentColor" strokeWidth="1.4" /></svg>
            Wayne//Ops
          </span>
          <span className="flex items-center gap-2"><Ping signal="cyan" /> En ligne · <Clock /></span>
        </header>

        <section className="pt-6">
          <div className="num text-[10px] uppercase tracking-[0.24em] text-zinc-500">Recrutement · Saison 2027</div>
          <h1 className="mt-1.5 text-[28px] font-semibold leading-none tracking-tight text-zinc-50">Jobboard</h1>
        </section>

        {/* Telemetry */}
        <section className="mt-5 grid grid-cols-3 border border-zinc-800 bg-panel/80 backdrop-blur-md">
          {[["Dossiers", stats.active, "text-zinc-50"], ["Entretiens", stats.interviews, "text-tac-cyan"], ["Alertes", stats.alerts, "text-tac-red"]].map(([label, value, tone], i) => (
            <div key={label as string} className={`px-3 py-3 ${i ? "border-l border-zinc-800" : ""}`}>
              <div className="num text-[9px] uppercase tracking-[0.18em] text-zinc-500">{label}</div>
              <div className={`num mt-1 text-[22px] leading-none ${tone}`}>{pad(value as number)}</div>
            </div>
          ))}
        </section>

        {/* Filter */}
        <nav className="num mt-4 grid grid-cols-3 border border-zinc-800 p-0.5 text-[10px] uppercase tracking-[0.18em]" aria-label="Filtrer">
          {FILTERS.map(([id, label]) => (
            <button key={id} onClick={() => { haptic("select"); setFilter(id) }} aria-pressed={filter === id}
                    className={`py-2 transition-colors ${filter === id ? "bg-zinc-100 text-black" : "text-zinc-500 active:text-zinc-200"}`}>
              {label}
            </button>
          ))}
        </nav>

        <main className="mt-4 space-y-3">
          {jobs.map((j) => <DataCard key={j.id} job={j} open={open === j.id} onToggle={() => setOpen(open === j.id ? null : j.id)} />)}
          {!jobs.length && <p className="num border border-dashed border-zinc-800 py-10 text-center text-[11px] uppercase tracking-[0.18em] text-zinc-600">Aucun dossier</p>}
        </main>

        <footer className="num mt-6 text-center text-[9px] uppercase tracking-[0.22em] text-zinc-700">Prototype · données fictives</footer>
      </div>
    </div>
  )
}
