"use client"

import { useQuery } from "@tanstack/react-query"
import { type ColumnDef, flexRender, getCoreRowModel, useReactTable } from "@tanstack/react-table"
import { ArrowDown, ArrowUp, Bookmark, ChevronLeft, ChevronRight, Filter, Search, X } from "lucide-react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { Suspense, useEffect, useMemo, useState } from "react"
import { toast } from "sonner"

import { PromptDialog } from "@/components/os/dialogs"
import { ActionBadge, EligibilityBadge, EmptyState, ErrorState, PageHeader, Pay, RowsSkeleton, Score, StatusBadge } from "@/components/os/bits"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { api, qs } from "@/lib/api"
import { date } from "@/lib/format"
import type { OpportunityRow, Paged } from "@/lib/types"

type Filters = Record<string, string>

const PRESETS: { id: string; label: string; filters: Filters }[] = [
  { id: "all", label: "All ranked", filters: {} },
  { id: "best-paid", label: "Best paid", filters: { paid: "true", sort: "compensation" } },
  { id: "high-recruitability", label: "High recruitability", filters: { min_recruit: "75", sort: "recruitability" } },
  { id: "africa", label: "Africa", filters: { region: "africa" } },
  { id: "robotics", label: "Robotics", filters: { category: "Robotics" } },
  { id: "technical-sales", label: "Technical Sales", filters: { category: "Technical Sales" } },
  { id: "industrial-pm", label: "Industrial Project Management", filters: { category: "Industrial Project Management" } },
  { id: "apply-this-week", label: "Apply this week", filters: { deadline_days: "7", sort: "deadline", order: "asc" } },
]
const REGIONS: [string, string][] = [["", "Any region"], ["europe", "Europe"], ["africa", "Africa"], ["asia", "Asia-Pacific"],
                                      ["middle_east", "Middle East"], ["americas", "Americas"]]
const SAVED_KEY = "sh-os-saved-views"
const COLS: { id: string; label: string; sort?: string }[] = [
  { id: "company", label: "Company", sort: "company" }, { id: "title", label: "Position" }, { id: "location", label: "Location" },
  { id: "category", label: "Category" }, { id: "fit", label: "Career fit", sort: "fit" },
  { id: "recruitability", label: "Recruitability", sort: "recruitability" }, { id: "pay", label: "Pay", sort: "compensation" },
  { id: "value", label: "Attractiveness" }, { id: "eligibility", label: "Eligibility" }, { id: "priority", label: "Priority", sort: "priority" },
  { id: "discovered", label: "Discovered", sort: "discovered" }, { id: "status", label: "Status" },
]

function Select({ value, onChange, options, label }: { value: string; onChange: (v: string) => void; options: [string, string][]; label: string }) {
  return (
    <select aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}
            className="h-8 rounded-md border bg-background px-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
      {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
    </select>
  )
}

function OpportunitiesView() {
  const router = useRouter()
  const params = useSearchParams()
  const filters: Filters = Object.fromEntries(params.entries())
  const preset = PRESETS.find((p) => p.id === filters.view)
  const effective: Filters = { ...(preset?.filters ?? {}), ...filters }
  delete effective.view
  const [search, setSearch] = useState(filters.q ?? "")
  const [saved, setSaved] = useState<{ name: string; query: string }[]>([])
  useEffect(() => {
    try { setSaved(JSON.parse(localStorage.getItem(SAVED_KEY) ?? "[]")) } catch { setSaved([]) }
  }, [])
  useEffect(() => {
    const t = setTimeout(() => { if ((filters.q ?? "") !== search) set({ q: search, page: "" }) }, 250)
    return () => clearTimeout(t)
  })

  function set(patch: Filters) {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v)
      else next.delete(k)
    }
    router.replace(`/opportunities?${next.toString()}`)
  }

  const page = Number(effective.page ?? 1)
  const q = useQuery({
    queryKey: ["opportunities", effective],
    queryFn: () => api.get<Paged<OpportunityRow>>(`/opportunities${qs({ ...effective, page, page_size: 50 })}`),
    placeholderData: (prev) => prev,
  })

  const columns = useMemo<ColumnDef<OpportunityRow>[]>(() => [
    { id: "company", cell: ({ row }) => <span className="font-medium">{row.original.company}</span> },
    { id: "title", cell: ({ row }) => <Link href={`/opportunities/${row.original.id}`} className="line-clamp-1 max-w-72 hover:underline">{row.original.title}</Link> },
    { id: "location", cell: ({ row }) => <span className="text-muted-foreground">{row.original.city ? `${row.original.city}, ` : ""}{row.original.country ?? "—"}</span> },
    { id: "category", cell: ({ row }) => <span className="text-muted-foreground">{row.original.category ?? "—"}</span> },
    { id: "fit", cell: ({ row }) => <Score value={row.original.fit} label="Career fit" /> },
    { id: "recruitability", cell: ({ row }) => <Score value={row.original.recruitability} label="Recruitability" /> },
    { id: "pay", cell: ({ row }) => <Pay c={row.original.compensation} compact /> },
    { id: "value", cell: ({ row }) => <Score value={row.original.compensation.score} label="Financial attractiveness" /> },
    { id: "eligibility", cell: ({ row }) => <EligibilityBadge value={row.original.eligibility} /> },
    { id: "priority", cell: ({ row }) => <Score value={row.original.priority} label="Global priority" provisional={row.original.provisional} /> },
    { id: "discovered", cell: ({ row }) => <span className="tabular text-muted-foreground">{date(row.original.discovered)}</span> },
    { id: "status", cell: ({ row }) => row.original.application ? <StatusBadge status={row.original.application.status} /> : <ActionBadge action={row.original.next_action} /> },
  ], [])
  const table = useReactTable({ data: q.data?.items ?? [], columns, getCoreRowModel: getCoreRowModel(), manualPagination: true })

  const [naming, setNaming] = useState(false)
  function saveView(name: string) {
    const next = [...saved.filter((s) => s.name !== name), { name, query: params.toString() }]
    setSaved(next)
    localStorage.setItem(SAVED_KEY, JSON.stringify(next))
    toast.success(`View "${name}" saved`)
  }

  const sort = effective.sort ?? "priority"
  const order = effective.order ?? "desc"
  const total = q.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / 50))
  const active = Object.keys(filters).filter((k) => !["page", "sort", "order"].includes(k)).length
  return (
    <div>
      <PromptDialog open={naming} onOpenChange={setNaming} title="Save this view" label="View name" onSubmit={saveView} />
      <PageHeader title="Opportunities" description={q.data ? `${total} ranked internships · eligibility failures and unpaid roles are hidden` : "Loading…"}
                  actions={
                    <DropdownMenu>
                      <DropdownMenuTrigger render={<Button variant="outline" size="sm" />}><Bookmark /> Views</DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-60">
                        {PRESETS.map((p) => <DropdownMenuItem key={p.id} onClick={() => router.replace(`/opportunities?view=${p.id}`)}>{p.label}</DropdownMenuItem>)}
                        {saved.length > 0 && <DropdownMenuSeparator />}
                        {saved.map((s) => <DropdownMenuItem key={s.name} onClick={() => router.replace(`/opportunities?${s.query}`)}>{s.name}</DropdownMenuItem>)}
                        <DropdownMenuSeparator />
                        <DropdownMenuItem onClick={() => setNaming(true)}>Save current filters…</DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  } />
      <div className="flex flex-wrap items-center gap-2 border-b px-6 py-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-2 size-4 text-muted-foreground" />
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Company, role, city" className="h-8 w-56 pl-8" />
        </div>
        <Select label="Region" value={effective.region ?? ""} onChange={(v) => set({ region: v, page: "" })} options={REGIONS} />
        <Select label="Country" value={effective.country ?? ""} onChange={(v) => set({ country: v, page: "" })}
                options={[["", "Any country"], ...(q.data?.facets.countries ?? []).map((c) => [c.code, c.name] as [string, string])]} />
        <Select label="Category" value={effective.category ?? ""} onChange={(v) => set({ category: v, page: "" })}
                options={[["", "Any category"], ...(q.data?.facets.categories ?? []).map((c) => [c, c] as [string, string])]} />
        <Select label="Pay" value={effective.paid ? "paid" : effective.min_monthly ?? ""} onChange={(v) => set(v === "paid" ? { paid: "true", min_monthly: "" } : { paid: "", min_monthly: v })}
                options={[["", "Any pay"], ["paid", "Confirmed paid"], ["1000", "≥ €1,000/mo"], ["2000", "≥ €2,000/mo"], ["3000", "≥ €3,000/mo"]]} />
        <Select label="Recruitability" value={effective.min_recruit ?? ""} onChange={(v) => set({ min_recruit: v, page: "" })}
                options={[["", "Any recruitability"], ["60", "≥ 60"], ["75", "≥ 75"], ["85", "≥ 85"]]} />
        <Select label="Eligibility" value={effective.eligibility ?? ""} onChange={(v) => set({ eligibility: v, page: "" })}
                options={[["", "Any eligibility"], ["ELIGIBLE", "Eligible"], ["NEEDS_VERIFICATION", "Needs verification"]]} />
        <Select label="Deadline" value={effective.deadline_days ?? ""} onChange={(v) => set({ deadline_days: v, page: "" })}
                options={[["", "Any deadline"], ["7", "Within 7 days"], ["14", "Within 14 days"], ["30", "Within 30 days"]]} />
        <Select label="Status" value={effective.status ?? ""} onChange={(v) => set({ status: v, page: "" })}
                options={[["", "Tracked or not"], ["untracked", "Not tracked"], ["tracked", "Tracked"]]} />
        {active > 0 && <Button variant="ghost" size="sm" onClick={() => router.replace("/opportunities")}><X /> Clear ({active})</Button>}
        {active === 0 && <span className="flex items-center gap-1 text-xs text-muted-foreground"><Filter className="size-3" /> No filter</span>}
      </div>
      {q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : q.isLoading ? <RowsSkeleton rows={12} /> : total === 0 ? (
        <EmptyState title="No opportunity matches these filters" action={<Button variant="outline" size="sm" onClick={() => router.replace("/opportunities")}>Clear filters</Button>} />
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-background text-xs text-muted-foreground">
                <tr className="border-b">
                  {COLS.map((c) => (
                    <th key={c.id} className="whitespace-nowrap px-3 py-2 text-left font-medium first:pl-6">
                      {c.sort ? (
                        <button className="inline-flex items-center gap-1 hover:text-foreground"
                                onClick={() => set({ sort: c.sort!, order: sort === c.sort && order === "desc" ? "asc" : "desc", page: "" })}>
                          {c.label}{sort === c.sort && (order === "desc" ? <ArrowDown className="size-3" /> : <ArrowUp className="size-3" />)}
                        </button>
                      ) : c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y">
                {table.getRowModel().rows.map((row) => (
                  <tr key={row.id} className="cursor-pointer hover:bg-muted/40" onClick={(e) => {
                    if (!(e.target as HTMLElement).closest("a,button")) router.push(`/opportunities/${row.original.id}`)
                  }}>
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="whitespace-nowrap px-3 py-2 first:pl-6">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between border-t px-6 py-3 text-xs text-muted-foreground">
            <span>Page {page} of {pages} · {total} results · * provisional priority (pay unknown)</span>
            <div className="flex gap-1">
              <Button variant="outline" size="icon-sm" disabled={page <= 1} onClick={() => set({ page: String(page - 1) })} aria-label="Previous page"><ChevronLeft /></Button>
              <Button variant="outline" size="icon-sm" disabled={page >= pages} onClick={() => set({ page: String(page + 1) })} aria-label="Next page"><ChevronRight /></Button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export default function OpportunitiesPage() {
  return <Suspense fallback={<RowsSkeleton rows={12} />}><OpportunitiesView /></Suspense>
}
