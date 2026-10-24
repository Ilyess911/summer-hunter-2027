"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, CalendarPlus, Check, ExternalLink, Save, Send } from "lucide-react"
import Link from "next/link"
import { useParams, useRouter } from "next/navigation"
import { useState } from "react"
import { toast } from "sonner"

import { ErrorState, PageHeader, RowsSkeleton, Section, StatusBadge } from "@/components/os/bits"
import { ConfirmSubmission } from "@/components/os/dialogs"
import { WorkspaceGrid } from "@/components/os/opportunity-workspace"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { api } from "@/lib/api"
import { STATUS_LABEL, date } from "@/lib/format"
import type { ApplicationDetail } from "@/lib/types"

export default function ApplicationPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ["application", Number(id)], queryFn: () => api.get<ApplicationDetail>(`/applications/${id}`) })
  const [submitting, setSubmitting] = useState(false)
  const [interview, setInterview] = useState(false)
  const [when, setWhen] = useState("")
  const [final, setFinal] = useState(false)
  const [notes, setNotes] = useState("")
  const [next, setNext] = useState({ action: "", date: "" })
  // Reset the editable fields whenever fresh data arrives (adjusting state during render, not in an effect).
  const [loaded, setLoaded] = useState<ApplicationDetail | null>(null)
  if (q.data && q.data !== loaded) {
    setLoaded(q.data)
    setNotes(q.data.notes ?? "")
    setNext({ action: q.data.next_action ?? "", date: q.data.next_action_date ?? "" })
  }
  const refresh = () => qc.invalidateQueries()
  const status = useMutation({
    mutationFn: (v: { status: string; appliedOn?: string }) =>
      api.post(`/applications/${id}/status`, { status: v.status, confirm_submission: !!v.appliedOn, applied_on: v.appliedOn }),
    onSuccess: () => { toast.success("Status updated"); refresh() },
    onError: (e) => toast.error((e as Error).message),
  })
  const addInterview = useMutation({
    mutationFn: () => api.post(`/applications/${id}/interview`, { at: when.replace("T", " "), final }),
    onSuccess: () => { toast.success("Interview recorded"); setInterview(false); refresh() },
    onError: (e) => toast.error((e as Error).message),
  })
  const save = useMutation({
    mutationFn: () => api.patch(`/applications/${id}`, { notes, next_action: next.action || null, next_action_date: next.date || null }),
    onSuccess: () => { toast.success("Saved"); refresh() },
    onError: (e) => toast.error((e as Error).message),
  })
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />
  const a = q.data
  return (
    <div>
      <div className="px-6 pt-4"><Button variant="ghost" size="xs" onClick={() => router.back()}><ArrowLeft /> Back</Button></div>
      {!a ? <RowsSkeleton rows={10} /> : (
        <>
          <PageHeader title={`${a.code} · ${a.title}`} description={<>{a.company} · <StatusBadge status={a.status} />{a.date_applied && <> · submitted {date(a.date_applied, true)}</>}</>}
                      actions={<>
                        {!a.date_applied && <Button size="sm" onClick={() => setSubmitting(true)}><Send /> Mark applied</Button>}
                        <Button size="sm" variant="outline" onClick={() => setInterview(true)}><CalendarPlus /> Add interview</Button>
                        <select aria-label="Update status" value="" onChange={(e) => {
                          const v = e.target.value
                          if (!v) return
                          if (!a.date_applied && ["APPLIED", "ONLINE_ASSESSMENT", "INTERVIEW", "FINAL_INTERVIEW", "OFFER_RECEIVED"].includes(v)) setSubmitting(true)
                          else status.mutate({ status: v })
                        }} className="h-7 rounded-md border bg-background px-2 text-sm">
                          <option value="">Update status…</option>
                          {a.allowed_statuses.filter((s) => s !== a.status).map((s) => <option key={s} value={s}>{STATUS_LABEL[s] ?? s}</option>)}
                        </select>
                        {a.opportunity.apply_url && <Button size="sm" variant="ghost" render={<a href={a.opportunity.apply_url} target="_blank" rel="noopener noreferrer" />}><ExternalLink /> Posting</Button>}
                      </>} />
          <ConfirmSubmission open={submitting} onOpenChange={setSubmitting} title={`${a.company}, ${a.title}`}
                             onConfirm={(day) => status.mutate({ status: "APPLIED", appliedOn: day })} />
          <Dialog open={interview} onOpenChange={setInterview}>
            <DialogContent className="sm:max-w-sm">
              <DialogHeader><DialogTitle>Add an interview</DialogTitle></DialogHeader>
              <div className="space-y-3">
                <div className="space-y-1.5"><Label htmlFor="iv">Date and time (Paris)</Label><Input id="iv" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></div>
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={final} onChange={(e) => setFinal(e.target.checked)} /> Final interview</label>
              </div>
              <DialogFooter><Button disabled={!when || addInterview.isPending} onClick={() => addInterview.mutate()}>Save interview</Button></DialogFooter>
            </DialogContent>
          </Dialog>

          <div className="grid gap-5 px-6 pt-6 xl:grid-cols-3">
            <Section title="Notes and next action" className="xl:col-span-2" action={<Button size="xs" variant="ghost" onClick={() => save.mutate()} disabled={save.isPending}><Save /> Save</Button>}>
              <div className="grid gap-3 p-4 sm:grid-cols-3">
                <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="na">Next action</Label><Input id="na" value={next.action} maxLength={300} onChange={(e) => setNext({ ...next, action: e.target.value })} placeholder="Follow up with the recruiter" /></div>
                <div className="space-y-1.5"><Label htmlFor="nd">On</Label><Input id="nd" type="date" value={next.date} onChange={(e) => setNext({ ...next, date: e.target.value })} /></div>
                <div className="space-y-1.5 sm:col-span-3"><Label htmlFor="notes">Notes</Label><Textarea id="notes" rows={4} maxLength={4000} value={notes} onChange={(e) => setNotes(e.target.value)} /></div>
              </div>
            </Section>
            <Section title="Activity timeline">
              <ol className="max-h-80 space-y-0 overflow-y-auto px-4 py-3">
                {a.timeline.map((e, i) => (
                  <li key={i} className="relative border-l pb-3 pl-4 last:pb-0">
                    <span className="absolute -left-[4.5px] top-1 size-2 rounded-full bg-border" />
                    <div className="text-xs text-muted-foreground">{date(e.at, true)} · {e.source}</div>
                    <div className="text-sm">{e.type.replaceAll("_", " ")}{e.new ? `: ${e.previous ? `${e.previous} → ` : ""}${e.new}` : ""}</div>
                  </li>
                ))}
              </ol>
              {a.documents.submitted_folder && <p className="flex items-center gap-1.5 border-t px-4 py-2 text-xs text-muted-foreground"><Check className="size-3 text-success" /> Submitted CV and letter frozen</p>}
            </Section>
          </div>
          <WorkspaceGrid o={a.opportunity} />
          <div className="px-6 pb-6 text-xs text-muted-foreground"><Link href={`/opportunities/${a.opportunity_id}`} className="hover:underline">Open the opportunity view</Link></div>
        </>
      )}
    </div>
  )
}
