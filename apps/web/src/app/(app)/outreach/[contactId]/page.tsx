"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ArrowLeft, BadgeCheck, CalendarPlus, Check, CircleAlert, Copy, ExternalLink, Loader2, MessageSquareReply, Send, Sparkles, Wand2, X,
} from "lucide-react"
import Link from "next/link"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import { Suspense, useMemo, useState } from "react"
import { toast } from "sonner"

import { EmptyState, ErrorState, PageHeader, RowsSkeleton, Score, Section, TimingBadge } from "@/components/os/bits"
import { ConfirmDialog } from "@/components/os/dialogs"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import { MESSAGE_KIND, date } from "@/lib/format"
import type { ContactDetail, Message, Meta } from "@/lib/types"
import { cn } from "@/lib/utils"

const VARIANTS: [string, string][] = [["default", "Default"], ["shorter", "Shorter"], ["more_direct", "More direct"],
                                      ["more_formal", "More formal"], ["more_natural", "More natural"]]
const EVIDENCE_LABEL: Record<string, string> = {
  public_post: "Their public post about this opening", vacancy: "Named on the vacancy", shared_school: "Shared school (ESILV)",
  shared_employer: "Shared employer", team_owns_role: "Their team owns the role", posting_focus: "A responsibility stated in the posting",
}

function Studio() {
  const { contactId } = useParams<{ contactId: string }>()
  const params = useSearchParams()
  const router = useRouter()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ["contact", Number(contactId)], queryFn: () => api.get<ContactDetail>(`/contacts/${contactId}`) })
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<Meta>("/meta") })
  const c = q.data
  const [chosenOpp, setOppId] = useState<number | null>(params.get("opportunity") ? Number(params.get("opportunity")) : null)
  const oppId = chosenOpp ?? c?.opportunities[0]?.opportunity.id ?? null
  const [kind, setKind] = useState<string>("")
  const [selected, setSelected] = useState<number | null>(null)
  const [followUp, setFollowUp] = useState(false)
  const [followDate, setFollowDate] = useState("")
  const [reply, setReply] = useState(false)
  const [replyNote, setReplyNote] = useState("")
  const [dismissing, setDismissing] = useState(false)

  const link = c?.opportunities.find((o) => o.opportunity.id === oppId)
  const drafts = useMemo(() => (c?.messages ?? []).filter((m) => m.opportunity_id === oppId && m.status !== "dismissed"), [c, oppId])
  const current: Message | undefined = drafts.find((m) => m.id === selected) ?? drafts.find((m) => m.status === "draft") ?? drafts[0]
  const refresh = () => qc.invalidateQueries({ queryKey: ["contact", Number(contactId)] })
  const err = (e: unknown) => toast.error((e as Error).message)

  const generate = useMutation({
    mutationFn: (variant: string) => api.post<Message>(`/contacts/${contactId}/messages`, { opportunity_id: oppId, kind: kind || null, variant }),
    onSuccess: (m) => { setSelected(m.id); refresh() }, onError: err,
  })
  const variant = useMutation({
    mutationFn: (v: string) => api.post<Message>(`/messages/${current!.id}/variant`, { variant: v }),
    onSuccess: (m) => { setSelected(m.id); refresh() }, onError: err,
  })
  const ai = useMutation({
    mutationFn: () => api.post<Message>(`/messages/${current!.id}/ai`),
    onSuccess: (m) => { setSelected(m.id); toast.success("Rewritten by the local model and re-validated"); refresh() }, onError: err,
  })
  const sent = useMutation({
    mutationFn: () => api.post<Message>(`/messages/${current!.id}/sent`),
    onSuccess: () => { toast.success("Recorded as sent. A follow-up will be suggested if there is no reply."); qc.invalidateQueries() }, onError: err,
  })
  const interact = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post(`/contacts/${contactId}/interactions`, { opportunity_id: oppId, ...body }),
    onSuccess: () => { toast.success("Interaction recorded"); setFollowUp(false); setReply(false); qc.invalidateQueries() }, onError: err,
  })
  const dismiss = useMutation({
    mutationFn: () => api.post(`/contacts/${contactId}/dismiss`),
    onSuccess: () => { toast.success("Contact dismissed"); qc.invalidateQueries(); router.push("/networking") }, onError: err,
  })
  const confirm = useMutation({ mutationFn: () => api.post(`/contacts/${contactId}/confirm`), onSuccess: () => qc.invalidateQueries(), onError: err })

  async function copy() {
    if (!current) return
    await navigator.clipboard.writeText(current.text)
    toast.success("Copied. Paste it in LinkedIn, send it, then mark it as sent.")
  }

  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />
  if (!c) return <RowsSkeleton rows={10} />
  const v = current?.validation ?? {}
  return (
    <div>
      <div className="px-6 pt-4"><Button variant="ghost" size="xs" onClick={() => router.back()}><ArrowLeft /> Back</Button></div>
      <PageHeader title={c.name}
                  description={<span className="inline-flex flex-wrap items-center gap-1.5">{c.category_label}{c.position && ` · ${c.position}`} · {c.company}
                    {c.verification === "verified" ? <BadgeCheck className="size-3.5 text-success" /> : <span className="rounded border border-warning/40 px-1 text-[10px] text-warning">Unverified</span>}
                    <span className="text-muted-foreground">· {c.status.replaceAll("_", " ").toLowerCase()}</span></span>}
                  actions={<>
                    {c.linkedin_url && <Button size="sm" variant="outline" render={<a href={c.linkedin_url} target="_blank" rel="noopener noreferrer" />}><ExternalLink /> Open LinkedIn</Button>}
                    {c.verification !== "verified" && <Button size="sm" variant="outline" onClick={() => confirm.mutate()}><BadgeCheck /> I checked this profile</Button>}
                    <Button size="sm" variant="ghost" onClick={() => setDismissing(true)}><X /> Dismiss</Button>
                  </>} />
      <ConfirmDialog open={dismissing} onOpenChange={setDismissing} destructive title={`Dismiss ${c.name}?`} confirm="Dismiss"
                     description="No more recommendations for this contact. History is kept." onConfirm={() => dismiss.mutate()} />

      <div className="grid gap-5 p-6 xl:grid-cols-3">
        <div className="space-y-5 xl:col-span-2">
          <Section title="Message" action={
            <div className="flex items-center gap-2">
              <select aria-label="Opportunity" value={oppId ?? ""} onChange={(e) => { setOppId(Number(e.target.value) || null); setSelected(null) }}
                      className="h-7 max-w-64 rounded-md border bg-background px-2 text-xs">
                {c.opportunities.map((o) => <option key={o.opportunity.id} value={o.opportunity.id}>{o.opportunity.title}</option>)}
                {!c.opportunities.length && <option value="">No linked opportunity</option>}
              </select>
              <select aria-label="Message type" value={kind} onChange={(e) => setKind(e.target.value)} className="h-7 rounded-md border bg-background px-2 text-xs">
                <option value="">Suggested type</option>
                {Object.entries(MESSAGE_KIND).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </div>
          }>
            {!current ? (
              <EmptyState title="No message yet" description={link ? `Recommended: ${link.timing.reason ?? ""}` : "Generate a message for this contact."}
                          action={<Button onClick={() => generate.mutate("default")} disabled={generate.isPending}>{generate.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />} Generate message</Button>} />
            ) : (
              <div className="space-y-3 p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="rounded-md bg-muted px-1.5 py-0.5 font-medium">{MESSAGE_KIND[current.kind] ?? current.kind}</span>
                  <span className="text-muted-foreground">{current.variant.replace("_", " ")} · {current.generator.startsWith("ollama") ? "rewritten by the local model" : "built from evidence"}</span>
                  {current.status === "sent" && <span className="rounded-md bg-success/10 px-1.5 py-0.5 font-medium text-success">Sent {date(current.sent)}</span>}
                  <span className={cn("ml-auto tabular", (v.chars ?? 0) > 250 && current.kind === "connection_request" ? "text-destructive" : "text-muted-foreground")}>
                    {v.chars} chars · {v.words} words
                  </span>
                </div>
                <Textarea readOnly value={current.text} rows={Math.min(14, current.text.split("\n").length + 3)} className="resize-none font-[450] leading-relaxed" />
                <div className={cn("flex items-start gap-2 rounded-md px-3 py-2 text-xs", v.ok ? "bg-success/8 text-success" : "bg-warning/10 text-warning")}>
                  {v.ok ? <Check className="mt-0.5 size-3.5" /> : <CircleAlert className="mt-0.5 size-3.5" />}
                  <div>{v.ok ? "Checked: facts, length, repetition, tone, purpose, appropriateness." : `Review before sending: ${(v.issues ?? []).join("; ")}`}
                    {current.personalization === "limited" && <span className="block text-warning">Limited personalization: no verified hook beyond the role. Add evidence (a post, a shared background) for a stronger message.</span>}
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <Button size="sm" onClick={copy}><Copy /> Copy message</Button>
                  {c.linkedin_url && <Button size="sm" variant="outline" render={<a href={c.linkedin_url} target="_blank" rel="noopener noreferrer" />}><ExternalLink /> Open LinkedIn</Button>}
                  <Button size="sm" variant="outline" disabled={current.status === "sent" || sent.isPending} onClick={() => sent.mutate()}><Send /> Mark as sent</Button>
                  <Button size="sm" variant="ghost" onClick={() => generate.mutate("default")} disabled={generate.isPending}><Sparkles /> Regenerate</Button>
                  {meta.data?.features.ai_messages && <Button size="sm" variant="ghost" onClick={() => ai.mutate()} disabled={ai.isPending}>{ai.isPending ? <Loader2 className="animate-spin" /> : <Wand2 />} Polish with local AI</Button>}
                </div>
                <div className="flex flex-wrap items-center gap-1 border-t pt-3">
                  <span className="mr-1 text-xs text-muted-foreground">Variation</span>
                  {VARIANTS.map(([id, label]) => (
                    <Button key={id} size="xs" variant={current.variant === id ? "secondary" : "ghost"} disabled={variant.isPending} onClick={() => variant.mutate(id)}>{label}</Button>
                  ))}
                </div>
                {current.evidence.length > 0 && (
                  <div className="border-t pt-3 text-xs">
                    <p className="mb-1 text-muted-foreground">Personalized with verified evidence</p>
                    <ul className="space-y-0.5">{current.evidence.map((e, i) => (
                      <li key={i} className="flex items-center gap-1.5"><BadgeCheck className="size-3 text-success" />{EVIDENCE_LABEL[e.kind] ?? e.kind}{e.term ? `: ${e.term}` : ""}
                        {e.url && <a href={e.url} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">source</a>}</li>
                    ))}</ul>
                  </div>
                )}
              </div>
            )}
          </Section>

          <Section title="Interaction history" action={<div className="flex gap-1">
            <Button size="xs" variant="ghost" onClick={() => setReply(true)}><MessageSquareReply /> Mark as replied</Button>
            <Button size="xs" variant="ghost" onClick={() => setFollowUp(true)}><CalendarPlus /> Schedule follow-up</Button>
          </div>}>
            {c.interactions.length ? (
              <ul className="divide-y">{c.interactions.map((i) => (
                <li key={i.id} className="flex gap-3 px-4 py-2 text-sm">
                  <span className="w-24 shrink-0 text-xs text-muted-foreground">{date(i.at, true)}</span>
                  <span className="font-medium">{i.kind.replaceAll("_", " ")}</span>
                  <span className="truncate text-muted-foreground">{i.note}{i.follow_up_on ? ` · ${date(i.follow_up_on)}` : ""}</span>
                </li>
              ))}</ul>
            ) : <EmptyState title="No interaction yet" description="Send a message on LinkedIn, then mark it as sent here." />}
          </Section>
        </div>

        <div className="space-y-5">
          <Section title="Why this person">
            <div className="space-y-3 p-4 text-sm">
              {link ? <>
                <div className="flex items-center justify-between"><span className="text-muted-foreground">Contact priority</span><Score value={link.score} /></div>
                <p>{link.explanation}</p>
                <div className="rounded-md bg-muted/60 px-3 py-2">
                  <div className="flex items-center justify-between"><TimingBadge action={link.timing.action} />
                    <span className="text-xs text-muted-foreground">{link.timing.date ? date(link.timing.date, true) : "no fixed date"} · confidence {link.timing.confidence}</span></div>
                  <p className="mt-1 text-xs text-foreground/80">{link.timing.reason}</p>
                </div>
              </> : <p className="text-muted-foreground">Not linked to an opportunity yet.</p>}
              <p className="text-[11px] text-muted-foreground">Priority is a ranking heuristic, not a probability of a reply.</p>
            </div>
          </Section>
          <Section title="Evidence">
            <ul className="divide-y">{c.evidence.map((e, i) => (
              <li key={i} className="space-y-0.5 px-4 py-2 text-xs">
                <div className="flex items-center gap-1.5 font-medium">{e.verified ? <BadgeCheck className="size-3 text-success" /> : <CircleAlert className="size-3 text-warning" />}{e.kind.replaceAll("_", " ")} · {date(e.retrieved)}</div>
                {e.excerpt && <p className="text-muted-foreground">“{e.excerpt}”</p>}
                {e.url && <a href={e.url} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">source</a>}
              </li>
            ))}</ul>
          </Section>
          {c.opportunities.length > 0 && (
            <Section title="Linked opportunities">
              <ul className="divide-y">{c.opportunities.map((o) => (
                <li key={o.opportunity.id} className="px-4 py-2 text-sm">
                  <Link href={`/opportunities/${o.opportunity.id}`} className="line-clamp-1 hover:underline">{o.opportunity.title}</Link>
                  <div className="text-xs text-muted-foreground">{o.opportunity.country} · <TimingBadge action={o.timing.action} /></div>
                </li>
              ))}</ul>
            </Section>
          )}
        </div>
      </div>

      <Dialog open={followUp} onOpenChange={setFollowUp}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader><DialogTitle>Schedule a follow-up</DialogTitle></DialogHeader>
          <div className="space-y-1.5"><Label htmlFor="fu">Date</Label><Input id="fu" type="date" value={followDate} onChange={(e) => setFollowDate(e.target.value)} /></div>
          <DialogFooter><Button disabled={!followDate} onClick={() => interact.mutate({ kind: "follow_up_scheduled", follow_up_on: followDate })}>Schedule</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={reply} onOpenChange={setReply}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle>They replied</DialogTitle></DialogHeader>
          <div className="space-y-1.5"><Label htmlFor="rn">What did they say? (optional)</Label><Textarea id="rn" rows={3} maxLength={2000} value={replyNote} onChange={(e) => setReplyNote(e.target.value)} /></div>
          <DialogFooter><Button onClick={() => interact.mutate({ kind: "reply", note: replyNote || null })}>Record reply</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default function OutreachStudioPage() {
  return <Suspense fallback={<RowsSkeleton rows={10} />}><Studio /></Suspense>
}
