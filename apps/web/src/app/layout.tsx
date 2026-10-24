import type { Metadata, Viewport } from "next"

import { Providers } from "@/components/os/providers"
import "./globals.css"


export const metadata: Metadata = {
  title: { default: "Summer Hunter OS", template: "%s · Summer Hunter OS" },
  description: "Système personnel de recherche de stage",
  robots: { index: false, follow: false },
}

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#dde2eb" },
    { media: "(prefers-color-scheme: dark)", color: "#050608" },
  ],
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr" suppressHydrationWarning className="h-full">
      <body className="min-h-full">
        <div className="backdrop" aria-hidden><i /><i /><i /></div>
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
