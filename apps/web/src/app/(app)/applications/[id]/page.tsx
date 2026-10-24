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
import { EVENT_LABEL, SOURCE_LABEL, STATUS_LABEL, date } from "@/lib/format"
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
    onSuccess: () => { toast.success("Statut mis à jour"); refresh() },
    onError: (e) => toast.error((e as Error).message),
  })
  const addInterview = useMutation({
    mutationFn: () => api.post(`/applications/${id}/interview`, { at: when.replace("T", " "), final }),
    onSuccess: () => { toast.success("Entretien enregistré"); setInterview(false); refresh() },
    onError: (e) => toast.error((e as Error).message),
  })
  const save = useMutation({
    mutationFn: () => api.patch(`/applications/${id}`, { notes, next_action: next.action || null, next_action_date: next.date || null }),
    onSuccess: () => { toast.success("Enregistré"); refresh() },
    onError: (e) => toast.error((e as Error).message),
  })
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />
  const a = q.data
  return (
    <div>
      <div className="px-6 pt-4 lg:px-8"><Button variant="ghost" size="xs" className="-ml-2 text-muted-foreground" onClick={() => router.back()}><ArrowLeft /> Retour</Button></div>
      {!a ? <RowsSkeleton rows={10} /> : (
        <>
          <PageHeader title={`${a.code} · ${a.title}`} description={<>{a.company} · <StatusBadge status={a.status} />{a.date_applied && <> · envoyée le {date(a.date_applied, true)}</>}</>}
                      actions={<>
                        {!a.date_applied && <Button size="sm" onClick={() => setSubmitting(true)}><Send /> Marquer envoyée</Button>}
                        <Button size="sm" variant="outline" onClick={() => setInterview(true)}><CalendarPlus /> Ajouter un entretien</Button>
                        <select aria-label="Changer le statut" value="" onChange={(e) => {
                          const v = e.target.value
                          if (!v) return
                          if (!a.date_applied && ["APPLIED", "ONLINE_ASSESSMENT", "INTERVIEW", "FINAL_INTERVIEW", "OFFER_RECEIVED"].includes(v)) setSubmitting(true)
                          else status.mutate({ status: v })
                        }} className="h-7 rounded-lg border bg-card px-2 text-[13px]">
                          <option value="">Changer le statut…</option>
                          {a.allowed_statuses.filter((s) => s !== a.status).map((s) => <option key={s} value={s}>{STATUS_LABEL[s] ?? s}</option>)}
                        </select>
                        {a.opportunity.apply_url && <Button size="sm" variant="ghost" render={<a href={a.opportunity.apply_url} target="_blank" rel="noopener noreferrer" />}><ExternalLink /> Offre</Button>}
                      </>} />
          <ConfirmSubmission open={submitting} onOpenChange={setSubmitting} title={`${a.company}, ${a.title}`}
                             onConfirm={(day) => status.mutate({ status: "APPLIED", appliedOn: day })} />
          <Dialog open={interview} onOpenChange={setInterview}>
            <DialogContent className="sm:max-w-sm">
              <DialogHeader><DialogTitle>Ajouter un entretien</DialogTitle></DialogHeader>
              <div className="space-y-3">
                <div className="space-y-1.5"><Label htmlFor="iv">Date et heure (Paris)</Label><Input id="iv" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></div>
                <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={final} onChange={(e) => setFinal(e.target.checked)} /> Entretien final</label>
              </div>
              <DialogFooter><Button disabled={!when || addInterview.isPending} onClick={() => addInterview.mutate()}>Enregistrer l’entretien</Button></DialogFooter>
            </DialogContent>
          </Dialog>

          <div className="grid gap-5 px-6 pt-4 lg:px-8 xl:grid-cols-3">
            <Section title="Notes et prochaine action" className="xl:col-span-2" action={<Button size="xs" variant="ghost" onClick={() => save.mutate()} disabled={save.isPending}><Save /> Enregistrer</Button>}>
              <div className="grid gap-3 p-4 sm:grid-cols-3">
                <div className="space-y-1.5 sm:col-span-2"><Label htmlFor="na">Prochaine action</Label><Input id="na" value={next.action} maxLength={300} onChange={(e) => setNext({ ...next, action: e.target.value })} placeholder="Relancer le recruteur" /></div>
                <div className="space-y-1.5"><Label htmlFor="nd">Le</Label><Input id="nd" type="date" value={next.date} onChange={(e) => setNext({ ...next, date: e.target.value })} /></div>
                <div className="space-y-1.5 sm:col-span-3"><Label htmlFor="notes">Notes</Label><Textarea id="notes" rows={4} maxLength={4000} value={notes} onChange={(e) => setNotes(e.target.value)} /></div>
              </div>
            </Section>
            <Section title="Historique">
              <ol className="max-h-80 space-y-0 overflow-y-auto px-4 py-3">
                {a.timeline.map((e, i) => (
                  <li key={i} className="relative border-l pb-3 pl-4 last:pb-0">
                    <span className="absolute -left-[4.5px] top-1 size-2 rounded-full bg-foreground/25" />
                    <div className="text-xs text-muted-foreground">{date(e.at, true)} · {SOURCE_LABEL[e.source] ?? e.source}</div>
                    <div className="text-sm">{EVENT_LABEL[e.type] ?? e.type.replaceAll("_", " ")}{e.new ? ` : ${e.previous ? `${STATUS_LABEL[e.previous] ?? e.previous} → ` : ""}${STATUS_LABEL[e.new] ?? e.new}` : ""}</div>
                  </li>
                ))}
              </ol>
              {a.documents.submitted_folder && <p className="flex items-center gap-1.5 border-t px-4 py-2 text-xs text-muted-foreground"><Check className="size-3 text-success" /> CV et lettre envoyés figés</p>}
            </Section>
          </div>
          <WorkspaceGrid o={a.opportunity} />
          <div className="px-6 pb-8 text-xs text-muted-foreground lg:px-8"><Link href={`/opportunities/${a.opportunity_id}`} className="hover:underline">Ouvrir la fiche de l’offre</Link></div>
        </>
      )}
    </div>
  )
}
