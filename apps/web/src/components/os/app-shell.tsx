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
import { BrandMark, Kbd, Pulse } from "@/components/os/bits"
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
const NAV_ITEM = "relative h-8 rounded-none font-mono text-[11.5px] uppercase tracking-[0.12em] text-sidebar-foreground "
  + "before:absolute before:inset-y-1 before:left-0 before:w-[2px] before:bg-transparent "
  + "hover:text-foreground data-active:bg-sidebar-accent data-active:font-normal data-active:text-tac-cyan "
  + "data-active:before:bg-tac-cyan data-active:before:shadow-[0_0_10px_var(--tac-cyan)]"

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
      <Sidebar collapsible="icon" variant="sidebar">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" render={<Link href="/" />}>
                <BrandMark />
                <span className="flex flex-col leading-tight">
                  <span className="font-display text-[12px] font-semibold uppercase tracking-[0.14em]">Wayne//Ops</span>
                  <span className="hud-label text-[9px] text-muted-foreground">Summer Hunter · 2027</span>
                </span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel className="hud-label text-muted-foreground/70">{"// Opérations"}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {NAV.map((n, i) => (
                  <SidebarMenuItem key={n.href}>
                    <SidebarMenuButton className={NAV_ITEM} isActive={active(path, n.href)} tooltip={`${n.label} (${i + 1})`} render={<Link href={n.href} />}>
                      <n.icon /> <span className="w-5 text-[10px] text-muted-foreground/70 group-data-[collapsible=icon]:hidden">{String(i + 1).padStart(2, "0")}</span><span>{n.label}</span>
                    </SidebarMenuButton>
                    {n.href === "/" && pending > 0 && <SidebarMenuBadge>{pending}</SidebarMenuBadge>}
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
          <SidebarGroup>
            <SidebarGroupLabel className="hud-label text-muted-foreground/70">{"// Analyse"}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {INSIGHTS.map((n, i) => (
                  <SidebarMenuItem key={n.href}>
                    <SidebarMenuButton className={NAV_ITEM} isActive={active(path, n.href)} tooltip={n.label} render={<Link href={n.href} />}>
                      <n.icon /> <span className="w-5 text-[10px] text-muted-foreground/70 group-data-[collapsible=icon]:hidden">{String(i + 6).padStart(2, "0")}</span><span>{n.label}</span>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>
          <div className="mx-1 mb-1 border-t border-sidebar-border px-1 pt-3 font-mono text-[10.5px] leading-5 text-muted-foreground group-data-[collapsible=icon]:hidden">
            <div className="flex items-center gap-1.5 text-foreground/80">
              <Pulse className="bg-tac-cyan" />
              {meta.data?.mode === "hosted" ? `En ligne · synchronisé ${relative(meta.data.last_sync)}` : "Local · base en direct"}
            </div>
            <div>Dernière recherche {relative(meta.data?.last_run?.finished ?? meta.data?.last_run?.started)}</div>
            <div>Mode {meta.data?.recruitment_mode === "exploration" ? "exploration" : meta.data?.recruitment_mode === "conversion" ? "conversion" : "…"}</div>
            {!!meta.data?.sync_conflicts && (
              <div className="text-tac-amber" title="Des modifications en ligne plus anciennes qu’un changement fait sur le Mac ont été mises de côté. Détail : summer-hunter os sync">
                {meta.data.sync_conflicts} conflit{meta.data.sync_conflicts > 1 ? "s" : ""} de synchro mis de côté
              </div>
            )}
          </div>
        </SidebarFooter>
      </Sidebar>
      <SidebarInset className="min-w-0">
        <header className="sticky top-0 z-20 flex h-12 items-center gap-2 border-b bg-background/85 px-3 backdrop-blur-md">
          <SidebarTrigger />
          <button onClick={() => setPalette(true)}
                  className="hud-cut hud-hover flex h-8 w-full max-w-md items-center gap-2 border bg-card px-2.5 text-[13px] text-muted-foreground">
            <Search className="size-4" /><span className="font-mono text-[12px]"><span className="text-tac-cyan">&gt;</span> rechercher entreprise, poste, contact_</span>
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
        <main className="hud-grid min-w-0 flex-1">{children}</main>
      </SidebarInset>
      <CommandPalette open={palette} onOpenChange={setPalette} />
    </SidebarProvider>
  )
}
