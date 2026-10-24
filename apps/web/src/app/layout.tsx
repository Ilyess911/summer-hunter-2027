import type { Metadata, Viewport } from "next"
import { Geist, JetBrains_Mono, Orbitron } from "next/font/google"

import { Providers } from "@/components/os/providers"
import "./globals.css"

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] })
const mono = JetBrains_Mono({ variable: "--font-tac-mono", subsets: ["latin"] })
const display = Orbitron({ variable: "--font-tac-display", subsets: ["latin"], weight: ["500", "600", "700"] })

export const metadata: Metadata = {
  title: { default: "Summer Hunter OS", template: "%s · Summer Hunter OS" },
  description: "Système personnel de recherche de stage",
  robots: { index: false, follow: false },
}

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fbfcfd" },
    { media: "(prefers-color-scheme: dark)", color: "#000000" },
  ],
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr" suppressHydrationWarning className={`${geistSans.variable} ${mono.variable} ${display.variable} h-full`}>
      <body className="min-h-full">
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
