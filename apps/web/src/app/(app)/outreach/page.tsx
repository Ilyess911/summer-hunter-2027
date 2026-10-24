"use client"

import { useQuery } from "@tanstack/react-query"
import { PenLine } from "lucide-react"
import Link from "next/link"

import { EmptyState, ErrorState, PageHeader, RowsSkeleton, Score, TimingBadge } from "@/components/os/bits"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { date } from "@/lib/format"
import type { ContactRow } from "@/lib/types"

export default function OutreachIndex() {
  const q = useQuery({ queryKey: ["networking"], queryFn: () => api.get<ContactRow[]>("/networking") })
  const contacts = useQuery({ queryKey: ["contacts", ""], queryFn: () => api.get<ContactRow[]>("/contacts") })
  const rows = q.data ?? []
  const others = (contacts.data ?? []).filter((c) => !rows.some((r) => r.id === c.id))
  return (
    <div>
      <PageHeader title="Messages" description="Rédige, relis et suis chaque message LinkedIn. C’est toi qui les envoies." />
      {q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : q.isLoading ? <RowsSkeleton rows={8} /> : !rows.length && !others.length ? (
        <EmptyState title="Personne à qui écrire pour l’instant" description="Ajoute des contacts depuis une offre : ils apparaîtront ici avec un message recommandé."
                    action={<Button size="sm" variant="outline" render={<Link href="/networking" />}>Aller au réseau</Button>} />
      ) : (
        <ul className="divide-y">
          {[...rows, ...others].map((c) => (
            <li key={`${c.id}-${c.opportunity?.id ?? 0}`} className="flex items-center gap-4 px-6 py-3 transition-colors hover:bg-subtle lg:px-8">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{c.name} <span className="font-normal text-muted-foreground">· {c.company}</span></div>
                <div className="truncate text-xs text-muted-foreground">{c.opportunity?.title ?? c.category_label}</div>
              </div>
              <div className="hidden w-56 text-xs md:block"><TimingBadge action={c.timing?.action} />{c.timing?.date && <span className="text-muted-foreground"> · {date(c.timing.date)}</span>}</div>
              <Score value={c.score} />
              <Button size="sm" variant="outline" render={<Link href={`/outreach/${c.id}${c.opportunity ? `?opportunity=${c.opportunity.id}` : ""}`} />}><PenLine /> Ouvrir</Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
