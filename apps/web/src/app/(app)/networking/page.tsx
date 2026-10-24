"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { BadgeCheck, ExternalLink, PenLine, RefreshCw, Search } from "lucide-react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useState } from "react"
import { toast } from "sonner"

import { EmptyState, ErrorState, PageHeader, RowsSkeleton, Score, TimingBadge } from "@/components/os/bits"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { api, qs } from "@/lib/api"
import { date } from "@/lib/format"
import type { ContactRow } from "@/lib/types"
import { cn } from "@/lib/utils"

function ContactLine({ c }: { c: ContactRow }) {
  return (
    <li className="grid grid-cols-[1fr_auto] items-center gap-4 px-6 py-3 md:grid-cols-[minmax(0,2fr)_minmax(0,2fr)_auto_auto]">
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 text-sm font-medium">
          {c.name}
          {c.verification === "verified" ? <BadgeCheck className="size-3.5 text-success" aria-label="Verified" />
            : <span className="rounded border border-warning/40 px-1 text-[10px] text-warning">Unverified</span>}
          {c.due && <span className="rounded bg-primary/10 px-1.5 text-[10px] font-medium text-primary">Due</span>}
        </div>
        <div className="truncate text-xs text-muted-foreground">{c.category_label}{c.position ? ` · ${c.position}` : ""} · {c.company}</div>
      </div>
      <div className="hidden min-w-0 md:block">
        <div className="truncate text-sm">{c.opportunity?.title ?? "No linked opportunity"}</div>
        <div className="flex items-center gap-2 truncate text-xs text-muted-foreground">
          <TimingBadge action={c.timing?.action} />{c.timing?.date && <span>· {date(c.timing.date)}</span>}<span className="truncate">· {c.timing?.reason}</span>
        </div>
      </div>
      <Score value={c.score} label="Contact priority" />
      <div className="flex gap-1">
        {c.linkedin_url && <Button size="icon-sm" variant="ghost" aria-label="Open LinkedIn" render={<a href={c.linkedin_url} target="_blank" rel="noopener noreferrer" />}><ExternalLink /></Button>}
        <Button size="sm" variant="outline" render={<Link href={`/outreach/${c.id}${c.opportunity ? `?opportunity=${c.opportunity.id}` : ""}`} />}><PenLine /> Write</Button>
      </div>
    </li>
  )
}

function NetworkingView() {
  const params = useSearchParams()
  const router = useRouter()
  const qc = useQueryClient()
  const tab = params.get("tab") ?? "queue"
  const [q, setQ] = useState("")
  const queue = useQuery({ queryKey: ["networking"], queryFn: () => api.get<ContactRow[]>("/networking"), enabled: tab === "queue" })
  const contacts = useQuery({ queryKey: ["contacts", q], queryFn: () => api.get<ContactRow[]>(`/contacts${qs({ q })}`), enabled: tab === "contacts" })
  const refresh = useMutation({
    mutationFn: () => api.post<{ created: number; scored: number }>("/networking/refresh"),
    onSuccess: (r) => { toast.success(`${r.created} new contact(s) imported from public evidence, ${r.scored} ranked`); qc.invalidateQueries() },
    onError: (e) => toast.error((e as Error).message),
  })
  const data = tab === "queue" ? queue : contacts
  return (
    <div>
      <PageHeader title="Networking intelligence"
                  description="Who to contact, why, and when. Contacts come only from public evidence or from you; nothing is sent automatically."
                  actions={<Button size="sm" variant="outline" onClick={() => refresh.mutate()} disabled={refresh.isPending}><RefreshCw className={cn(refresh.isPending && "animate-spin")} /> Refresh</Button>} />
      <div className="flex items-center gap-2 border-b px-6 py-2">
        {[["queue", "To contact"], ["contacts", "All contacts"]].map(([id, label]) => (
          <Button key={id} size="sm" variant={tab === id ? "secondary" : "ghost"} onClick={() => router.replace(`/networking?tab=${id}`)}>{label}</Button>
        ))}
        {tab === "contacts" && (
          <div className="relative ml-auto">
            <Search className="pointer-events-none absolute left-2 top-2 size-4 text-muted-foreground" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, company, role" className="h-8 w-60 pl-8" />
          </div>
        )}
      </div>
      {data.isError ? <ErrorState error={data.error} retry={() => data.refetch()} /> : data.isLoading ? <RowsSkeleton rows={8} /> : !data.data?.length ? (
        <EmptyState title={tab === "queue" ? "Nobody to contact right now" : "No contacts yet"}
                    description="Open an opportunity, run the LinkedIn searches it suggests, and add the recruiter or hiring manager you find. Public posts naming a recruiter are imported automatically."
                    action={<Button size="sm" variant="outline" render={<Link href="/opportunities?view=high-recruitability" />}>Open top opportunities</Button>} />
      ) : (
        <ul className="divide-y">{data.data.map((c) => <ContactLine key={`${c.id}-${c.opportunity?.id ?? 0}`} c={c} />)}</ul>
      )}
    </div>
  )
}

export default function NetworkingPage() {
  return <Suspense fallback={<RowsSkeleton rows={8} />}><NetworkingView /></Suspense>
}
