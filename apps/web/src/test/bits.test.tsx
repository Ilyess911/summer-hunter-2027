import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { EligibilityBadge, ErrorState, Pay, Score, StatusBadge, TimingBadge } from "@/components/os/bits"

describe("Score", () => {
  it("renders an em dash for an unknown score, never zero", () => {
    render(<Score value={null} />)
    expect(screen.getByText("—")).toBeInTheDocument()
    expect(screen.queryByText("0")).not.toBeInTheDocument()
  })
  it("rounds and clamps", () => {
    render(<Score value={104.6} label="Career fit" />)
    expect(screen.getByText("100")).toBeInTheDocument()
    expect(screen.getByTitle("Career fit: 100")).toBeInTheDocument()
  })
})

describe("badges", () => {
  it("labels pipeline statuses", () => {
    render(<StatusBadge status="OFFER_RECEIVED" />)
    expect(screen.getByText("Offre")).toBeInTheDocument()
  })
  it("maps eligibility", () => {
    render(<><EligibilityBadge value="NEEDS_VERIFICATION" /><EligibilityBadge value={null} /></>)
    expect(screen.getByText("À vérifier")).toBeInTheDocument()
    expect(screen.getByText("Inconnu")).toBeInTheDocument()
  })
  it("shows networking timing", () => {
    render(<TimingBadge action="CONTACT_AFTER_APPLICATION" />)
    expect(screen.getByText("Après la candidature")).toBeInTheDocument()
  })
})

describe("Pay", () => {
  it("explains that unknown pay is not unpaid", () => {
    render(<Pay c={{ status: null, monthly_min_eur: null }} />)
    expect(screen.getByTitle("Une paie inconnue n’est pas un stage non payé")).toHaveTextContent("Non précisé")
  })
})

describe("ErrorState", () => {
  it("shows the message and retries", async () => {
    const retry = vi.fn()
    render(<ErrorState error={new Error("API unreachable")} retry={retry} />)
    expect(screen.getByRole("alert")).toHaveTextContent("API unreachable")
    await userEvent.click(screen.getByRole("button", { name: /réessayer/i }))
    expect(retry).toHaveBeenCalledOnce()
  })
})
