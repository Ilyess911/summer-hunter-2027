"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { BadgeCheck, Download, ExternalLink, FileText, Loader2, PenLine, Search, UserPlus, X } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
import { toast } from "sonner"

import { EmptyState, Score, Section, TimingBadge } from "@/components/os/bits"
import { AddContactDialog } from "@/components/os/dialogs"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api } from "@/lib/api"
import { date } from "@/lib/format"
import type { ContactRow, Meta } from "@/lib/types"

const COMPONENT_LABEL: Record<string, string> = {
  involvement: "Involvement in hiring", relevance: "Relevance to this role", connection: "Link to your background",
  recruiting_activity: "Recruiting activity", accessibility: "Reachable publicly",
}

export function ContactsPanel({ opportunityId, company, contacts }: { opportunityId: number; company: string; contacts: ContactRow[] }) {
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<Meta>("/meta") })
  const [links, setLinks] = useState<{ label: string; url: string }[] | null>(null)
  const search = useMutation({
    mutationFn: () => api.post<{ status: string; added: number; links: { label: string; url: string }[]; detail?: string }>(
      `/opportunities/${opportunityId}/contacts/search`),
    onSuccess: (r) => {
      setLinks(r.links)
      if (r.status === "ok") toast.success(`${r.added} new public profile(s) found: confirm each before writing`)
      else toast.info("Search API not configured: use the LinkedIn searches below")
      qc.invalidateQueries({ queryKey: ["opportunity", opportunityId] })
    },
    onError: (e) => toast.error((e as Error).message),
  })
  const act = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "confirm" | "dismiss" }) => api.post(`/contacts/${id}/${action}`),
    onSuccess: () => qc.invalidateQueries(),
    onError: (e) => toast.error((e as Error).message),
  })
  return (
    <Section title="Who to contact" action={
      <div className="flex gap-1">
        <Button variant="ghost" size="xs" onClick={() => search.mutate()} disabled={search.isPending}>
          {search.isPending ? <Loader2 className="animate-spin" /> : <Search />} Find {meta.data?.features.contact_search ? "" : "(links)"}
        </Button>
        <Button variant="ghost" size="xs" onClick={() => setAdding(true)}><UserPlus /> Add</Button>
      </div>
    }>
      <AddContactDialog open={adding} onOpenChange={setAdding} company={company} opportunityId={opportunityId} />
      {contacts.length === 0 ? (
        <EmptyState title="No verified contact yet"
                    description="Nobody is invented. Run the LinkedIn searches, then add the recruiter or manager you find." />
      ) : (
        <ul className="divide-y">
          {contacts.map((c) => (
            <li key={c.id} className="space-y-1.5 px-4 py-3">
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 text-sm font-medium">
                    {c.name}
                    {c.verification === "verified" ? <BadgeCheck className="size-3.5 text-success" aria-label="Verified" />
                      : <span className="rounded border border-warning/40 px-1 text-[10px] font-medium text-warning">Unverified</span>}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{c.category_label}{c.position ? ` · ${c.position}` : ""}</div>
                </div>
                <Tooltip>
                  <TooltipTrigger render={<span className="cursor-help" />}><Score value={c.score} /></TooltipTrigger>
                  <TooltipContent className="max-w-64 space-y-1">
                    <p className="font-medium">Contact priority (ranking heuristic, not a reply probability)</p>
                    {Object.entries(c.components ?? {}).map(([k, v]) => <p key={k} className="flex justify-between gap-4"><span>{COMPONENT_LABEL[k] ?? k}</span><span className="tabular">{Math.round(v)}</span></p>)}
                  </TooltipContent>
                </Tooltip>
              </div>
              <p className="text-xs text-foreground/80"><span className="text-muted-foreground">Why: </span>{c.explanation}</p>
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <TimingBadge action={c.timing?.action} />
                {c.timing?.date && <span className="text-muted-foreground">from {date(c.timing.date)}</span>}
                <span className="text-muted-foreground">· {c.timing?.reason}</span>
              </div>
              <div className="flex flex-wrap gap-1.5 pt-1">
                <Button size="xs" render={<Link href={`/outreach/${c.id}?opportunity=${opportunityId}`} />}><PenLine /> Message</Button>
                {c.linkedin_url && <Button size="xs" variant="outline" render={<a href={c.linkedin_url} target="_blank" rel="noopener noreferrer" />}><ExternalLink /> LinkedIn</Button>}
                {c.verification !== "verified" && <Button size="xs" variant="outline" onClick={() => act.mutate({ id: c.id, action: "confirm" })}><BadgeCheck /> I checked this profile</Button>}
                <Button size="xs" variant="ghost" onClick={() => act.mutate({ id: c.id, action: "dismiss" })}><X /> Dismiss</Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {links && (
        <div className="space-y-1 border-t px-4 py-3">
          <p className="text-xs text-muted-foreground">Run these searches on LinkedIn yourself, then add who you find:</p>
          {links.map((l) => <a key={l.url} href={l.url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1.5 text-sm text-primary hover:underline"><ExternalLink className="size-3" />{l.label}</a>)}
        </div>
      )}
    </Section>
  )
}

type Docs = { files: { kind: string; name: string; size: number; modified: string }[]; letter_job: string | null }

export function DocumentsPanel({ opportunityId }: { opportunityId: number }) {
  const qc = useQueryClient()
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<Meta>("/meta") })
  const docs = useQuery({ queryKey: ["documents", opportunityId], queryFn: () => api.get<Docs>(`/opportunities/${opportunityId}/documents`),
                          refetchInterval: (q) => (q.state.data?.letter_job === "running" ? 5000 : false) })
  const gen = useMutation({
    mutationFn: (kind: "cv" | "letter") => api.post<{ status: string; detail?: string; compiled?: boolean }>(`/opportunities/${opportunityId}/documents/${kind}`),
    onSuccess: (r, kind) => {
      toast.success(kind === "cv" ? (r.compiled ? "Tailored CV ready" : "CV generated (LaTeX source; PDF did not compile)") : r.detail ?? "Started")
      qc.invalidateQueries({ queryKey: ["documents", opportunityId] })
    },
    onError: (e) => toast.error((e as Error).message),
  })
  const local = meta.data?.features.documents !== false
  return (
    <Section title="Documents" action={
      <div className="flex gap-1">
        <Button size="xs" variant="ghost" disabled={!local || gen.isPending} onClick={() => gen.mutate("cv")}><FileText /> Generate CV</Button>
        <Button size="xs" variant="ghost" disabled={!local || gen.isPending || docs.data?.letter_job === "running"} onClick={() => gen.mutate("letter")}>
          {docs.data?.letter_job === "running" ? <Loader2 className="animate-spin" /> : <PenLine />} Cover letter
        </Button>
      </div>
    }>
      {!local && <p className="px-4 pt-3 text-xs text-muted-foreground">Generation runs on your Mac (LaTeX and the local model); requests made here are queued for the next sync.</p>}
      {docs.data?.letter_job === "running" && <p className="px-4 pt-3 text-xs text-muted-foreground">Cover letter in progress with the local model (2 to 3 minutes)…</p>}
      {docs.data?.letter_job === "failed" && <p className="px-4 pt-3 text-xs text-destructive">The last cover letter run failed: check that Ollama is installed.</p>}
      {docs.data && docs.data.files.length ? (
        <ul className="divide-y">
          {docs.data.files.map((f) => (
            <li key={f.name} className="flex items-center gap-3 px-4 py-2 text-sm">
              <FileText className="size-4 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{f.name}</span>
              <span className="text-xs text-muted-foreground">{date(f.modified)}</span>
              <Button size="icon-xs" variant="ghost" aria-label="Download" render={<a href={`/api/opportunities/${opportunityId}/documents/file/${encodeURIComponent(f.name)}`} />}><Download /></Button>
            </li>
          ))}
        </ul>
      ) : <EmptyState title="No document yet" description="Generate a CV tailored to this posting, then a cover letter." />}
    </Section>
  )
}
