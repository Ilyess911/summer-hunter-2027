import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"

import { AddContactDialog, ConfirmSubmission } from "@/components/os/dialogs"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function withQuery(ui: React.ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
}

afterEach(() => vi.unstubAllGlobals())

describe("ConfirmSubmission", () => {
  it("passes the chosen submission date only after explicit confirmation", async () => {
    const onConfirm = vi.fn()
    const onOpenChange = vi.fn()
    render(<ConfirmSubmission open onOpenChange={onOpenChange} onConfirm={onConfirm} title="Zipline, Sales Ops" />)
    expect(screen.getByText("Mark as submitted?")).toBeInTheDocument()
    expect(onConfirm).not.toHaveBeenCalled()
    const input = screen.getByLabelText("Submission date")
    await userEvent.clear(input)
    await userEvent.type(input, "2026-10-20")
    await userEvent.click(screen.getByRole("button", { name: "Yes, it was submitted" }))
    expect(onConfirm).toHaveBeenCalledWith("2026-10-20")
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
  it("does nothing on cancel", async () => {
    const onConfirm = vi.fn()
    render(<ConfirmSubmission open onOpenChange={() => {}} onConfirm={onConfirm} title="X" />)
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }))
    expect(onConfirm).not.toHaveBeenCalled()
  })
})

describe("AddContactDialog", () => {
  it("rejects a non-profile LinkedIn URL without calling the API", async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
    render(withQuery(<AddContactDialog open onOpenChange={() => {}} company="Intel" />))
    await userEvent.type(screen.getByLabelText("Name"), "Jane Doe")
    await userEvent.type(screen.getByLabelText("LinkedIn profile"), "https://www.linkedin.com/company/intel")
    await userEvent.click(screen.getByRole("button", { name: /add contact/i }))
    expect(await screen.findByText(/public profile URL/)).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })
  it("posts a valid contact with the CSRF header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: 3, name: "Jane Doe" }), { status: 200 }))
    vi.stubGlobal("fetch", fetchMock)
    const onOpenChange = vi.fn()
    render(withQuery(<AddContactDialog open onOpenChange={onOpenChange} company="Intel" opportunityId={12} />))
    await userEvent.type(screen.getByLabelText("Name"), "Jane Doe")
    await userEvent.type(screen.getByLabelText("LinkedIn profile"), "https://www.linkedin.com/in/jane-doe")
    await userEvent.click(screen.getByRole("button", { name: /add contact/i }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce())
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe("/api/contacts")
    expect(init.headers["X-SH-Request"]).toBe("1")
    expect(JSON.parse(init.body)).toMatchObject({ name: "Jane Doe", company: "Intel", opportunity_id: 12,
                                                  linkedin_url: "https://www.linkedin.com/in/jane-doe" })
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false))
  })
})
