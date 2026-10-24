"use client"

import { useQuery } from "@tanstack/react-query"
import {
  Activity, BarChart3, Bell, Briefcase, Gauge, KanbanSquare, LayoutDashboard, LogOut, MessagesSquare, Monitor,
  Moon, Search, Sun, Users,
} from "lucide-react"
import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { useTheme } from "next-themes"
import { useEffect, useState } from "react"

import { CommandPalette } from "@/components/os/command-palette"
import { BrandMark, Kbd } from "@/components/os/bits"
import { Button } from "@/components/ui/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarInset,
  SidebarMenu, SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger,
} from "@/components/ui/sidebar"
import { api } from "@/lib/api"
import { relative } from "@/lib/format"
import type { CommandCenter, Meta } from "@/lib/types"

const NAV = [
  { href: "/", label: "Aujourd’hui", icon: LayoutDashboard },
  { href: "/opportunities", label: "Opportunités", icon: Briefcase },
  { href: "/pipeline", label: "Candidatures", icon: KanbanSquare },
  { href: "/networking", label: "Réseau", icon: Users },
  { href: "/outreach", label: "Messages", icon: MessagesSquare },
]
const INSIGHTS = [
  { href: "/analytics", label: "Statistiques", icon: BarChart3 },
  { href: "/performance", label: "Performance", icon: Gauge },
  { href: "/activity", label: "Journal", icon: Activity },
]

// Terminal menu item: mono caps, index prefix, neon rail on the left when active.
// Sidebar item: rounded pill, frosted highlight when active (same language as the Mini App tabs).
const NAV_ITEM = "h-9 rounded-xl text-[14px] font-medium text-sidebar-foreground transition-colors hover:text-foreground "
  + "data-active:bg-sidebar-accent data-active:text-foreground data-active:shadow-[0_2px_8px_-2px_rgb(0_0_0/0.18),inset_0_1px_0_var(--edge-top)]"

function active(path: string, href: string) {
  return href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`)
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname()
  const router = useRouter()
  const [palette, setPalette] = useState(false)
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<Meta>("/meta") })
  const cc = useQuery({ queryKey: ["command-center"], queryFn: () => api.get<CommandCenter>("/command-center") })
  const { setTheme } = useTheme()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault()
        setPalette((v) => !v)
      }
      const target = e.target as HTMLElement
      if (target?.closest("input, textarea, [contenteditable]") || e.metaKey || e.ctrlKey || e.altKey) return
      const go: Record<string, string> = { "1": "/", "2": "/opportunities", "3": "/pipeline", "4": "/networking", "5": "/outreach" }
      if (go[e.key]) router.push(go[e.key])
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [router])

  async function logout() {
    await api.post("/auth/logout")
    router.replace("/login")
  }

  const pending = cc.data?.priorities.length ?? 0
  return (
    <SidebarProvider>
      <Sidebar collapsible="icon" variant="floating">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" render={<Link href="/" />}>
                <BrandMark />
                <span className="flex flex-col leading-tight">
                  <span className="text-[14px] font-semibold tracking-tight">Summer Hunter</span>
                  <span className="text-[11.5px] text-muted-foreground">Stages avril – août 2027</span>
                </span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>Espace de travail</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {NAV.map((n, i) => (
                  <SidebarMenuItem key={n.href}>
                    <SidebarMenuButton className={NAV_ITEM} isActive={active(path, n.href)} tooltip={`${n.label} (${i + 1})`} render={<Link href={n.href} />}>
                      <n.icon /> <span>{n.label}</span>
                    </SidebarMenuButton>
                    {n.href === "/" && pending > 0 && <SidebarMenuBadge>{pending}</SidebarMenuBadge>}
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
          <SidebarGroup>
            <SidebarGroupLabel>Analyse</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {INSIGHTS.map((n) => (
                  <SidebarMenuItem key={n.href}>
                    <SidebarMenuButton className={NAV_ITEM} isActive={active(path, n.href)} tooltip={n.label} render={<Link href={n.href} />}>
                      <n.icon /> <span>{n.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>
          <div className="glass-panel mx-1 mb-1 rounded-2xl px-3 py-2.5 text-[12px] leading-5 text-muted-foreground shadow-none group-data-[collapsible=icon]:hidden">
            <div className="flex items-center gap-1.5 text-foreground/80">
              <span className={`size-1.5 rounded-full ${meta.data?.mode === "hosted" ? "bg-info" : "bg-success"}`} />
              {meta.data?.mode === "hosted" ? `En ligne · synchronisé ${relative(meta.data.last_sync)}` : "Local · base en direct"}
            </div>
            <div>Dernière recherche {relative(meta.data?.last_run?.finished ?? meta.data?.last_run?.started)}</div>
            <div>Mode {meta.data?.recruitment_mode === "exploration" ? "exploration" : meta.data?.recruitment_mode === "conversion" ? "conversion" : "…"}</div>
            {!!meta.data?.sync_conflicts && (
              <div className="text-warning" title="Des modifications en ligne plus anciennes qu’un changement fait sur le Mac ont été mises de côté. Détail : summer-hunter os sync">
                {meta.data.sync_conflicts} conflit{meta.data.sync_conflicts > 1 ? "s" : ""} de synchro mis de côté
              </div>
            )}
          </div>
        </SidebarFooter>
      </Sidebar>
      <SidebarInset className="min-w-0 bg-transparent">
        <header className="sticky top-0 z-20 isolate flex h-14 items-center gap-2 px-4">
          <div className="pointer-events-none absolute inset-0 -z-10 bg-background/40 backdrop-blur-xl [mask-image:linear-gradient(#000_70%,transparent)]" aria-hidden />
          <SidebarTrigger />
          <button onClick={() => setPalette(true)}
                  className="glass-panel glass-hover flex h-10 w-full max-w-md items-center gap-2.5 rounded-2xl px-3.5 text-[14px] text-muted-foreground shadow-none">
            <Search className="size-4" /> Rechercher une entreprise, un poste, un contact…
            <span className="ml-auto"><Kbd>⌘K</Kbd></span>
          </button>
          <div className="ml-auto flex items-center gap-1">
            <Popover>
              <PopoverTrigger render={<Button variant="ghost" size="icon" aria-label="Centre d’actions" className="relative" />}>
                <Bell />
                {pending > 0 && <span className="absolute right-1.5 top-1.5 size-1.5 rounded-full bg-brand" />}
              </PopoverTrigger>
              <PopoverContent align="end" className="w-96 p-0">
                <div className="border-b px-3 py-2 text-[13px] font-medium">À faire</div>
                <div className="max-h-96 overflow-y-auto py-1">
                  {(cc.data?.priorities ?? []).slice(0, 12).map((p) => (
                    <Link key={p.key} href={p.href} className="block px-3 py-2 text-sm hover:bg-muted">
                      <div className="font-medium leading-snug">{p.title}</div>
                      <div className="truncate text-xs text-muted-foreground">{p.detail}</div>
                    </Link>
                  ))}
                  {pending === 0 && <p className="px-3 py-6 text-center text-sm text-muted-foreground">Rien ne t’attend pour l’instant.</p>}
                </div>
              </PopoverContent>
            </Popover>
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="ghost" size="icon" aria-label="Thème" />}>
                <Sun className="dark:hidden" /><Moon className="hidden dark:block" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => setTheme("light")}><Sun /> Clair</DropdownMenuItem>
                <DropdownMenuItem onClick={() => setTheme("dark")}><Moon /> Sombre</DropdownMenuItem>
                <DropdownMenuItem onClick={() => setTheme("system")}><Monitor /> Système</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <Button variant="ghost" size="icon" aria-label="Se déconnecter" onClick={logout}><LogOut /></Button>
          </div>
        </header>
        <main className="min-w-0 flex-1">{children}</main>
      </SidebarInset>
      <CommandPalette open={palette} onOpenChange={setPalette} />
    </SidebarProvider>
  )
}
