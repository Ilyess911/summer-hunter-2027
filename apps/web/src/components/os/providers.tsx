"use client"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "next-themes"
import { useState } from "react"

import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, retry: (n, err) => n < 1 && (err as { status?: number })?.status !== 401,
                                 refetchOnWindowFocus: false } },
  }))
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={client}>
        <TooltipProvider delay={300}>
          {children}
          <Toaster position="bottom-right" />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
