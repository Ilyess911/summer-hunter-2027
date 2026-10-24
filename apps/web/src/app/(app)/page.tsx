"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ArrowRight, CalendarClock, Check, CircleDollarSign, FileSearch, MessageSquare, Send, ShieldCheck, Sparkles, Video,
} from "lucide-react"
import Link from "next/link"
import { toast } from "sonner"

import {
  ActionBadge, EligibilityBadge, EmptyState, ErrorState, PageHeader, Pay, RowsSkeleton, Score, Section, TimingBadge,
} from "@/components/os/bits"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { date } from "@/lib/format"
import type { CommandCenter, Priority } from "@/lib/types"
import { cn } from "@/lib/utils"

const KIND_ICON: Record<string, typeof Send> = {
  apply: Send, contact: MessageSquare, follow_up: MessageSquare, prepare_interview: Video, verify_compensation: CircleDollarSign,
  verify_eligibility: ShieldCheck, deadline: CalendarClock, next_action: Check,
}
const KIND_CTA: Record<string, string> = {
  apply: "Ouvrir", contact: "Écrire", follow_up: "Relancer", prepare_interview: "Préparer", verify_compensation: "Vérifier",
  verify_eligibility: "Vérifier", deadline: "Postuler", next_action: "Ouvrir",
}

function Stat({ label, value, href, tone }: { label: string; value: number | undefined; href: string; tone?: string }) {
  return (
    <Link href={href} className="min-w-0 rounded-2xl px-3 py-3 text-center transition-colors hover:bg-glass-strong">
      <div className={cn("tabular text-[26px] font-bold leading-none tracking-[-0.02em]", tone)}>{value ?? "–"}</div>
      <div className="mt-1.5 truncate text-[12px] text-muted-foreground">{label}</div>
    </Link>
  )
}

const URGENT = new Set(["deadline", "prepare_interview", "follow_up"])

function greeting(): string {
  const h = new Date().getHours()
  return h < 5 ? "Bonne nuit" : h < 18 ? "Bonjour" : "Bonsoir"
}

function PriorityRow({ p, onDone }: { p: Priority; onDone: (key: string) => void }) {
  const Icon = KIND_ICON[p.kind] ?? Sparkles
  return (
    <li className="group flex items-center gap-3 px-5 py-3 transition-colors hover:bg-glass-strong">
      <span className={cn("flex size-8 shrink-0 items-center justify-center rounded-full bg-foreground/6 text-muted-foreground",
                          URGENT.has(p.kind) && "bg-warning/15 text-warning")}><Icon className="size-3.5" /></span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{p.title}</div>
        <div className="truncate text-[12.5px] text-muted-foreground">{p.detail}</div>
      </div>
      <Button variant="ghost" size="xs" className="opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
              onClick={() => onDone(p.key)} aria-label="Marquer comme fait">Fait</Button>
      <Button size="sm" variant="outline" render={<Link href={p.href} />}>{KIND_CTA[p.kind] ?? "Open"} <ArrowRight /></Button>
    </li>
  )
}

export default function CommandCenterPage() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ["command-center"], queryFn: () => api.get<CommandCenter>("/command-center") })
  const done = useMutation({
    mutationFn: (key: string) => api.post(`/actions/${encodeURIComponent(key)}/done`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["command-center"] }); toast.success("Marqué comme fait") },
    onError: (e) => toast.error(String((e as Error).message)),
  })
  const d = q.data
  return (
    <div>
      <PageHeader title={`${greeting()} Ilyess`}
                  description={<span className="first-letter:uppercase">{new Date().toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long" })}
                    {d ? ` · ${d.priorities.length} action${d.priorities.length > 1 ? "s" : ""} à traiter` : ""}</span>} />
      {q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : (
        <div className="space-y-6 px-6 pb-8 pt-4 lg:px-8">
          <div className="glass-panel grid grid-cols-2 gap-1 p-1.5 sm:grid-cols-4 xl:grid-cols-7">
            <Stat label="Candidatures actives" value={d?.counts.active} href="/pipeline" />
            <Stat label="Nouvelles (7 j)" value={d?.counts.new} href="/opportunities?sort=discovered" />
            <Stat label="En attente de réponse" value={d?.counts.awaiting} href="/pipeline?status=APPLIED" />
            <Stat label="Entretiens" value={d?.counts.interviews} href="/pipeline?status=INTERVIEW" tone={d?.counts.interviews ? "text-brand" : undefined} />
            <Stat label="Offres" value={d?.counts.offers} href="/pipeline?status=OFFER_RECEIVED" tone={d?.counts.offers ? "text-success" : undefined} />
            <Stat label="Échéances ≤ 14 j" value={d?.counts.deadlines} href="/pipeline" tone={d?.counts.deadlines ? "text-destructive" : undefined} />
            <Stat label="Stages payés" value={d?.counts.paid} href="/opportunities?view=best-paid" />
            
          </div>

          <div className="grid gap-5 xl:grid-cols-5">
            <Section title="Priorités du jour" className="xl:col-span-3" alert={!!d?.priorities.some((p) => p.kind === "deadline")}
                     action={<span className="tabular text-xs text-muted-foreground">{d ? d.priorities.length : ""}</span>}>
              {q.isLoading ? <RowsSkeleton rows={5} /> : d && d.priorities.length ? (
                <ul className="divide-y">{d.priorities.map((p) => <PriorityRow key={p.key} p={p} onDone={(k) => done.mutate(k)} />)}</ul>
              ) : <EmptyState title="Rien d’urgent aujourd’hui" description="Les nouvelles offres sont évaluées chaque nuit entre minuit et 6 h." />}
            </Section>

            <Section title="Personnes à contacter" className="xl:col-span-2"
                     action={<Button variant="ghost" size="xs" render={<Link href="/networking" />}>Tout voir <ArrowRight /></Button>}>
              {q.isLoading ? <RowsSkeleton rows={4} /> : d && d.networking.length ? (
                <ul className="divide-y">
                  {d.networking.map((c) => (
                    <li key={`${c.id}-${c.opportunity?.id}`} className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-glass-strong">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{c.name} <span className="font-normal text-muted-foreground">· {c.category_label}</span></div>
                        <div className="truncate text-xs text-muted-foreground">{c.company} · {c.opportunity?.title}</div>
                      </div>
                      <TimingBadge action={c.timing?.action} />
                      <Button size="sm" variant="outline" render={<Link href={`/outreach/${c.id}?opportunity=${c.opportunity?.id ?? ""}`} />}>Écrire</Button>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState title="Aucun contact pour l’instant"
                            description="Les contacts viennent de preuves publiques ou de toi. Ouvre une offre pour trouver les recruteurs et ajouter les personnes que tu connais."
                            action={<Button size="sm" variant="outline" render={<Link href="/networking" />}><FileSearch /> Trouver des contacts</Button>} />
              )}
            </Section>
          </div>

          <Section title="Meilleures opportunités" action={<Button variant="ghost" size="xs" render={<Link href="/opportunities" />}>Tout voir <ArrowRight /></Button>}>
            {q.isLoading ? <RowsSkeleton rows={5} /> : d && d.top.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-[12px] text-muted-foreground">
                    <tr className="border-b">
                      {["Entreprise", "Poste", "Pays", "Recrutabilité", "Adéquation", "Paie", "Éligibilité", "Prochaine étape"].map((h) => (
                        <th key={h} className="whitespace-nowrap px-4 py-2.5 text-left font-medium first:pl-5">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {d.top.map((o) => (
                      <tr key={o.id} className="transition-colors hover:bg-glass-strong">
                        <td className="px-4 py-2.5 font-medium">{o.company}</td>
                        <td className="max-w-80 px-4 py-2.5"><Link href={`/opportunities/${o.id}`} className="line-clamp-1 hover:underline">{o.title}</Link></td>
                        <td className="px-4 py-2.5 text-muted-foreground">{o.country ?? "—"}</td>
                        <td className="px-4 py-2.5"><Score value={o.recruitability} label="Recrutabilité" /></td>
                        <td className="px-4 py-2.5"><Score value={o.fit} label="Adéquation" /></td>
                        <td className="px-4 py-2.5"><Pay c={o.compensation} compact /></td>
                        <td className="px-4 py-2.5"><EligibilityBadge value={o.eligibility} /></td>
                        <td className="px-4 py-2.5"><ActionBadge action={o.next_action} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <EmptyState title="Aucune opportunité à traiter pour l’instant" />}
          </Section>
          {d?.last_run && <p className="text-xs text-muted-foreground">Dernière recherche le {date(d.last_run.started)} · {d.last_run.status === "ok" || d.last_run.status === "success" ? "terminée" : d.last_run.status}</p>}
        </div>
      )}
    </div>
  )
}
