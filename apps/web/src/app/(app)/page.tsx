"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ArrowRight, CalendarClock, Check, CircleDollarSign, FileSearch, Inbox, MessageSquare, Send, ShieldCheck, Sparkles, Trophy, Video,
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

const KIND_ICON: Record<string, typeof Send> = {
  apply: Send, contact: MessageSquare, follow_up: MessageSquare, prepare_interview: Video, verify_compensation: CircleDollarSign,
  verify_eligibility: ShieldCheck, deadline: CalendarClock, next_action: Check,
}
const KIND_CTA: Record<string, string> = {
  apply: "Open", contact: "Write", follow_up: "Follow up", prepare_interview: "Prepare", verify_compensation: "Check",
  verify_eligibility: "Check", deadline: "Apply", next_action: "Open",
}

function Stat({ label, value, href, icon: Icon }: { label: string; value: number | undefined; href: string; icon: typeof Send }) {
  return (
    <Link href={href} className="group rounded-xl border bg-card px-4 py-3 transition-colors hover:bg-muted/40">
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="truncate">{label}</span> <Icon className="size-3.5 shrink-0 opacity-60" />
      </div>
      <div className="tabular mt-1 text-2xl font-semibold tracking-tight">{value ?? "–"}</div>
    </Link>
  )
}

function PriorityRow({ p, onDone }: { p: Priority; onDone: (key: string) => void }) {
  const Icon = KIND_ICON[p.kind] ?? Sparkles
  return (
    <li className="group flex items-center gap-3 px-4 py-2.5">
      <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground"><Icon className="size-3.5" /></span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{p.title}</div>
        <div className="truncate text-xs text-muted-foreground">{p.detail}</div>
      </div>
      <Button variant="ghost" size="xs" className="opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
              onClick={() => onDone(p.key)} aria-label="Mark done">Done</Button>
      <Button size="sm" variant="outline" render={<Link href={p.href} />}>{KIND_CTA[p.kind] ?? "Open"} <ArrowRight /></Button>
    </li>
  )
}

export default function CommandCenterPage() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ["command-center"], queryFn: () => api.get<CommandCenter>("/command-center") })
  const done = useMutation({
    mutationFn: (key: string) => api.post(`/actions/${encodeURIComponent(key)}/done`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["command-center"] }); toast.success("Marked as done") },
    onError: (e) => toast.error(String((e as Error).message)),
  })
  const d = q.data
  return (
    <div>
      <PageHeader title="Command Center" description="What needs your attention today, and the next best step for each." />
      {q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : (
        <div className="space-y-5 p-6">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7">
            <Stat label="Active applications" value={d?.counts.active} href="/pipeline" icon={Inbox} />
            <Stat label="New this week" value={d?.counts.new} href="/opportunities?sort=discovered" icon={Sparkles} />
            <Stat label="Awaiting response" value={d?.counts.awaiting} href="/pipeline?status=APPLIED" icon={CalendarClock} />
            <Stat label="Interviews" value={d?.counts.interviews} href="/pipeline?status=INTERVIEW" icon={Video} />
            <Stat label="Offers" value={d?.counts.offers} href="/pipeline?status=OFFER_RECEIVED" icon={Trophy} />
            <Stat label="Deadlines ≤ 14 d" value={d?.counts.deadlines} href="/pipeline" icon={CalendarClock} />
            <Stat label="Paid, ranked" value={d?.counts.paid} href="/opportunities?view=best-paid" icon={CircleDollarSign} />
          </div>

          <div className="grid gap-5 xl:grid-cols-5">
            <Section title="Today's priorities" className="xl:col-span-3"
                     action={<span className="text-xs text-muted-foreground">{d ? `${d.priorities.length} actions` : ""}</span>}>
              {q.isLoading ? <RowsSkeleton rows={5} /> : d && d.priorities.length ? (
                <ul className="divide-y">{d.priorities.map((p) => <PriorityRow key={p.key} p={p} onDone={(k) => done.mutate(k)} />)}</ul>
              ) : <EmptyState title="Nothing due today" description="New opportunities are evaluated every night between midnight and 6 am." />}
            </Section>

            <Section title="Networking opportunities" className="xl:col-span-2"
                     action={<Button variant="ghost" size="xs" render={<Link href="/networking" />}>All <ArrowRight /></Button>}>
              {q.isLoading ? <RowsSkeleton rows={4} /> : d && d.networking.length ? (
                <ul className="divide-y">
                  {d.networking.map((c) => (
                    <li key={`${c.id}-${c.opportunity?.id}`} className="flex items-center gap-3 px-4 py-2.5">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{c.name} <span className="font-normal text-muted-foreground">· {c.category_label}</span></div>
                        <div className="truncate text-xs text-muted-foreground">{c.company} · {c.opportunity?.title}</div>
                      </div>
                      <TimingBadge action={c.timing?.action} />
                      <Button size="sm" variant="outline" render={<Link href={`/outreach/${c.id}?opportunity=${c.opportunity?.id ?? ""}`} />}>Write</Button>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState title="No contacts to approach yet"
                            description="Contacts come from public evidence or from you. Open an opportunity to find recruiters and add the people you know."
                            action={<Button size="sm" variant="outline" render={<Link href="/networking" />}><FileSearch /> Find contacts</Button>} />
              )}
            </Section>
          </div>

          <Section title="Top opportunities" action={<Button variant="ghost" size="xs" render={<Link href="/opportunities" />}>All <ArrowRight /></Button>}>
            {q.isLoading ? <RowsSkeleton rows={5} /> : d && d.top.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-xs text-muted-foreground">
                    <tr className="border-b">
                      {["Company", "Position", "Country", "Recruitability", "Career fit", "Pay", "Eligibility", "Next step"].map((h) => (
                        <th key={h} className="px-4 py-2 text-left font-medium">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {d.top.map((o) => (
                      <tr key={o.id} className="hover:bg-muted/40">
                        <td className="px-4 py-2.5 font-medium">{o.company}</td>
                        <td className="max-w-80 px-4 py-2.5"><Link href={`/opportunities/${o.id}`} className="line-clamp-1 hover:underline">{o.title}</Link></td>
                        <td className="px-4 py-2.5 text-muted-foreground">{o.country ?? "—"}</td>
                        <td className="px-4 py-2.5"><Score value={o.recruitability} label="Recruitability" /></td>
                        <td className="px-4 py-2.5"><Score value={o.fit} label="Career fit" /></td>
                        <td className="px-4 py-2.5"><Pay c={o.compensation} compact /></td>
                        <td className="px-4 py-2.5"><EligibilityBadge value={o.eligibility} /></td>
                        <td className="px-4 py-2.5"><ActionBadge action={o.next_action} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <EmptyState title="No actionable opportunity yet" />}
          </Section>
          {d?.last_run && <p className="text-xs text-muted-foreground">Last discovery run {date(d.last_run.started)} · {d.last_run.status}</p>}
        </div>
      )}
    </div>
  )
}
