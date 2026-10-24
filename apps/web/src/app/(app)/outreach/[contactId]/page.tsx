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
import { MESSAGE_KIND, VARIANT_LABEL, date } from "@/lib/format"
import type { ContactDetail, Message, Meta } from "@/lib/types"
import { cn } from "@/lib/utils"

const VARIANTS: [string, string][] = Object.entries(VARIANT_LABEL)
const CONTACT_STATUS: Record<string, string> = {
  IDENTIFIED: "identifié", TO_CONTACT: "à contacter", MESSAGE_SENT: "message envoyé", CONNECTED: "connecté", REPLIED: "a répondu",
  CALL_SCHEDULED: "appel prévu", FOLLOW_UP_REQUIRED: "relance à faire", CLOSED: "clos",
}
const INTERACTION_LABEL: Record<string, string> = {
  message_sent: "Message envoyé", reply: "Réponse", call: "Appel", meeting: "Rendez-vous", note: "Note", follow_up_scheduled: "Relance prévue",
}
const CONFIDENCE: Record<string, string> = { high: "élevée", medium: "moyenne", low: "faible" }
const EVIDENCE_LABEL: Record<string, string> = {
  public_post: "Son post public sur ce poste", vacancy: "Cité dans l’offre", shared_school: "Même école (ESILV)",
  shared_employer: "Même employeur", team_owns_role: "Son équipe porte le poste", posting_focus: "Une mission citée dans l’offre",
  user: "Ajouté par toi", search: "Recherche publique",
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
    onSuccess: (m) => { setSelected(m.id); toast.success("Reformulé par le modèle local et revalidé"); refresh() }, onError: err,
  })
  const sent = useMutation({
    mutationFn: () => api.post<Message>(`/messages/${current!.id}/sent`),
    onSuccess: () => { toast.success("Noté comme envoyé. Une relance sera proposée sans réponse."); qc.invalidateQueries() }, onError: err,
  })
  const interact = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.post(`/contacts/${contactId}/interactions`, { opportunity_id: oppId, ...body }),
    onSuccess: () => { toast.success("Interaction enregistrée"); setFollowUp(false); setReply(false); qc.invalidateQueries() }, onError: err,
  })
  const dismiss = useMutation({
    mutationFn: () => api.post(`/contacts/${contactId}/dismiss`),
    onSuccess: () => { toast.success("Contact écarté"); qc.invalidateQueries(); router.push("/networking") }, onError: err,
  })
  const confirm = useMutation({ mutationFn: () => api.post(`/contacts/${contactId}/confirm`), onSuccess: () => qc.invalidateQueries(), onError: err })

  async function copy() {
    if (!current) return
    await navigator.clipboard.writeText(current.text)
    toast.success("Copié. Colle-le dans LinkedIn, envoie-le, puis marque-le comme envoyé.")
  }

  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />
  if (!c) return <RowsSkeleton rows={10} />
  const v = current?.validation ?? {}
  return (
    <div>
      <div className="px-6 pt-4 lg:px-8"><Button variant="ghost" size="xs" className="-ml-2 text-muted-foreground" onClick={() => router.back()}><ArrowLeft /> Retour</Button></div>
      <PageHeader title={c.name}
                  description={<span className="inline-flex flex-wrap items-center gap-1.5">{c.category_label}{c.position && ` · ${c.position}`} · {c.company}
                    {c.verification === "verified" ? <BadgeCheck className="size-3.5 text-success" /> : <span className="rounded bg-warning/12 px-1 text-[10px] font-medium text-warning">Non vérifié</span>}
                    <span className="text-muted-foreground">· {CONTACT_STATUS[c.status] ?? c.status.toLowerCase()}</span></span>}
                  actions={<>
                    {c.linkedin_url && <Button size="sm" variant="outline" render={<a href={c.linkedin_url} target="_blank" rel="noopener noreferrer" />}><ExternalLink /> Ouvrir LinkedIn</Button>}
                    {c.verification !== "verified" && <Button size="sm" variant="outline" onClick={() => confirm.mutate()}><BadgeCheck /> J’ai vérifié ce profil</Button>}
                    <Button size="sm" variant="ghost" onClick={() => setDismissing(true)}><X /> Écarter</Button>
                  </>} />
      <ConfirmDialog open={dismissing} onOpenChange={setDismissing} destructive title={`Écarter ${c.name} ?`} confirm="Écarter"
                     description="Plus aucune recommandation pour ce contact. L’historique est conservé." onConfirm={() => dismiss.mutate()} />

      <div className="grid gap-5 px-6 pb-8 pt-4 lg:px-8 xl:grid-cols-3">
        <div className="space-y-5 xl:col-span-2">
          <Section title="Message" action={
            <div className="flex items-center gap-2">
              <select aria-label="Offre" value={oppId ?? ""} onChange={(e) => { setOppId(Number(e.target.value) || null); setSelected(null) }}
                      className="h-7 max-w-64 rounded-lg border bg-card px-2 text-xs">
                {c.opportunities.map((o) => <option key={o.opportunity.id} value={o.opportunity.id}>{o.opportunity.title}</option>)}
                {!c.opportunities.length && <option value="">Aucune offre liée</option>}
              </select>
              <select aria-label="Type de message" value={kind} onChange={(e) => setKind(e.target.value)} className="h-7 rounded-lg border bg-card px-2 text-xs">
                <option value="">Type conseillé</option>
                {Object.entries(MESSAGE_KIND).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </div>
          }>
            {!current ? (
              <EmptyState title="Aucun message pour l’instant" description={link ? `Conseil : ${link.timing.reason ?? ""}` : "Génère un message pour ce contact."}
                          action={<Button onClick={() => generate.mutate("default")} disabled={generate.isPending}>{generate.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />} Générer le message</Button>} />
            ) : (
              <div className="space-y-3 p-4">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="rounded-md bg-muted px-1.5 py-0.5 font-medium">{MESSAGE_KIND[current.kind] ?? current.kind}</span>
                  <span className="text-muted-foreground">{VARIANT_LABEL[current.variant] ?? current.variant} · {current.generator.startsWith("ollama") ? "reformulé par le modèle local" : "construit à partir des preuves"}</span>
                  {current.status === "sent" && <span className="rounded-md bg-success/10 px-1.5 py-0.5 font-medium text-success">Envoyé le {date(current.sent)}</span>}
                  <span className={cn("ml-auto tabular", (v.chars ?? 0) > 250 && current.kind === "connection_request" ? "text-destructive" : "text-muted-foreground")}>
                    {v.chars} caractères · {v.words} mots
                  </span>
                </div>
                <Textarea readOnly value={current.text} rows={Math.min(14, current.text.split("\n").length + 3)} className="resize-none rounded-lg bg-subtle px-3.5 py-3 text-[14px] leading-relaxed" />
                <div className={cn("flex items-start gap-2 rounded-lg px-3 py-2 text-xs", v.ok ? "bg-success/8 text-success" : "bg-warning/10 text-warning")}>
                  {v.ok ? <Check className="mt-0.5 size-3.5" /> : <CircleAlert className="mt-0.5 size-3.5" />}
                  <div>{v.ok ? "Vérifié : faits, longueur, répétitions, ton, objectif, pertinence." : `À relire avant envoi : ${(v.issues ?? []).join(" ; ")}`}
                    {current.personalization === "limited" && <span className="block text-warning">Personnalisation limitée : aucune accroche vérifiée au-delà du poste. Ajoute une preuve (un post, un parcours commun) pour un message plus fort.</span>}
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <Button size="sm" onClick={copy}><Copy /> Copier le message</Button>
                  {c.linkedin_url && <Button size="sm" variant="outline" render={<a href={c.linkedin_url} target="_blank" rel="noopener noreferrer" />}><ExternalLink /> Ouvrir LinkedIn</Button>}
                  <Button size="sm" variant="outline" disabled={current.status === "sent" || sent.isPending} onClick={() => sent.mutate()}><Send /> Marquer comme envoyé</Button>
                  <Button size="sm" variant="ghost" onClick={() => generate.mutate("default")} disabled={generate.isPending}><Sparkles /> Régénérer</Button>
                  {meta.data?.features.ai_messages && <Button size="sm" variant="ghost" onClick={() => ai.mutate()} disabled={ai.isPending}>{ai.isPending ? <Loader2 className="animate-spin" /> : <Wand2 />} Affiner avec l’IA locale</Button>}
                </div>
                <div className="flex flex-wrap items-center gap-1 border-t pt-3">
                  <span className="mr-1 text-xs text-muted-foreground">Variante</span>
                  {VARIANTS.map(([id, label]) => (
                    <Button key={id} size="xs" variant={current.variant === id ? "secondary" : "ghost"} disabled={variant.isPending} onClick={() => variant.mutate(id)}>{label}</Button>
                  ))}
                </div>
                {current.evidence.length > 0 && (
                  <div className="border-t pt-3 text-xs">
                    <p className="mb-1 text-muted-foreground">Personnalisé avec des preuves vérifiées</p>
                    <ul className="space-y-0.5">{current.evidence.map((e, i) => (
                      <li key={i} className="flex items-center gap-1.5"><BadgeCheck className="size-3 text-success" />{EVIDENCE_LABEL[e.kind] ?? e.kind}{e.term ? `: ${e.term}` : ""}
                        {e.url && <a href={e.url} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">source</a>}</li>
                    ))}</ul>
                  </div>
                )}
              </div>
            )}
          </Section>

          <Section title="Historique des échanges" action={<div className="flex gap-1">
            <Button size="xs" variant="ghost" onClick={() => setReply(true)}><MessageSquareReply /> A répondu</Button>
            <Button size="xs" variant="ghost" onClick={() => setFollowUp(true)}><CalendarPlus /> Prévoir une relance</Button>
          </div>}>
            {c.interactions.length ? (
              <ul className="divide-y">{c.interactions.map((i) => (
                <li key={i.id} className="flex gap-3 px-4 py-2 text-sm">
                  <span className="w-24 shrink-0 text-xs text-muted-foreground">{date(i.at, true)}</span>
                  <span className="font-medium">{INTERACTION_LABEL[i.kind] ?? i.kind}</span>
                  <span className="truncate text-muted-foreground">{i.note}{i.follow_up_on ? ` · ${date(i.follow_up_on)}` : ""}</span>
                </li>
              ))}</ul>
            ) : <EmptyState title="Aucun échange pour l’instant" description="Envoie un message sur LinkedIn, puis marque-le comme envoyé ici." />}
          </Section>
        </div>

        <div className="space-y-5">
          <Section title="Pourquoi cette personne">
            <div className="space-y-3 p-4 text-sm">
              {link ? <>
                <div className="flex items-center justify-between"><span className="text-muted-foreground">Priorité du contact</span><Score value={link.score} /></div>
                <p>{link.explanation}</p>
                <div className="rounded-lg bg-subtle px-3 py-2 ring-1 ring-border">
                  <div className="flex items-center justify-between"><TimingBadge action={link.timing.action} />
                    <span className="text-xs text-muted-foreground">{link.timing.date ? date(link.timing.date, true) : "pas de date fixe"} · confiance {CONFIDENCE[link.timing.confidence ?? ""] ?? link.timing.confidence}</span></div>
                  <p className="mt-1 text-xs text-foreground/80">{link.timing.reason}</p>
                </div>
              </> : <p className="text-muted-foreground">Pas encore lié à une offre.</p>}
              <p className="text-[11px] text-muted-foreground">La priorité est une heuristique de classement, pas une probabilité de réponse.</p>
            </div>
          </Section>
          <Section title="Preuves">
            <ul className="divide-y">{c.evidence.map((e, i) => (
              <li key={i} className="space-y-0.5 px-4 py-2 text-xs">
                <div className="flex items-center gap-1.5 font-medium">{e.verified ? <BadgeCheck className="size-3 text-success" /> : <CircleAlert className="size-3 text-warning" />}{EVIDENCE_LABEL[e.kind] ?? e.kind.replaceAll("_", " ")} · {date(e.retrieved)}</div>
                {e.excerpt && <p className="text-muted-foreground">“{e.excerpt}”</p>}
                {e.url && <a href={e.url} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">source</a>}
              </li>
            ))}</ul>
          </Section>
          {c.opportunities.length > 0 && (
            <Section title="Offres liées">
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
          <DialogHeader><DialogTitle>Prévoir une relance</DialogTitle></DialogHeader>
          <div className="space-y-1.5"><Label htmlFor="fu">Date</Label><Input id="fu" type="date" value={followDate} onChange={(e) => setFollowDate(e.target.value)} /></div>
          <DialogFooter><Button disabled={!followDate} onClick={() => interact.mutate({ kind: "follow_up_scheduled", follow_up_on: followDate })}>Planifier</Button></DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog open={reply} onOpenChange={setReply}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle>Cette personne a répondu</DialogTitle></DialogHeader>
          <div className="space-y-1.5"><Label htmlFor="rn">Qu’a-t-elle dit ? (facultatif)</Label><Textarea id="rn" rows={3} maxLength={2000} value={replyNote} onChange={(e) => setReplyNote(e.target.value)} /></div>
          <DialogFooter><Button onClick={() => interact.mutate({ kind: "reply", note: replyNote || null })}>Enregistrer la réponse</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default function OutreachStudioPage() {
  return <Suspense fallback={<RowsSkeleton rows={10} />}><Studio /></Suspense>
}
