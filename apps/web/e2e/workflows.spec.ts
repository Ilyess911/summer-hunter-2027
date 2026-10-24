import { expect, type Page, test } from "@playwright/test"

const PASSWORD = "e2e-password-123"

async function login(page: Page) {
  await page.goto("/login")
  await page.getByLabel("Password").fill(PASSWORD)
  await page.getByRole("button", { name: /sign in/i }).click()
  await expect(page.getByRole("heading", { name: "Command Center" })).toBeVisible()
}

test.describe.configure({ mode: "serial" })

test("protected pages redirect to login and a wrong password is refused", async ({ page }) => {
  await page.goto("/opportunities")
  await expect(page).toHaveURL(/\/login/)
  await page.getByLabel("Password").fill("wrong-password")
  await page.getByRole("button", { name: /sign in/i }).click()
  await expect(page.getByText(/incorrect|invalid|wrong/i)).toBeVisible()
})

test("track an opportunity, then confirm the submission when moving it on the board", async ({ page }) => {
  await login(page)
  await page.getByRole("link", { name: "Opportunities" }).first().click()
  await page.getByRole("link", { name: /Robotics Deployment Intern/ }).click()
  await page.getByRole("button", { name: "Track application" }).click()
  await expect(page.getByText(/tracked|added to your pipeline/i).first()).toBeVisible()

  await page.goto("/pipeline")
  const card = page.getByText("Robotics Deployment Intern")
  await expect(card).toBeVisible()
  const target = page.locator('[data-status="APPLIED"]')
  const from = (await card.boundingBox())!
  const to = (await target.boundingBox())!
  await page.mouse.move(from.x + 20, from.y + 10)
  await page.mouse.down()
  await page.mouse.move(from.x + 40, from.y + 20, { steps: 4 })
  await page.mouse.move(to.x + to.width / 2, to.y + 60, { steps: 12 })
  await page.mouse.up()

  await expect(page.getByText("Mark as submitted?")).toBeVisible()
  await page.getByRole("button", { name: "Yes, it was submitted" }).click()
  await expect(page.getByText(/moved to Applied/)).toBeVisible()
  await page.reload()
  await expect(page.locator('[data-status="APPLIED"]').getByText("Robotics Deployment Intern")).toBeVisible()
})

test("add a contact, write a validated message, copy it and record it as sent", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"])
  await login(page)
  await page.goto("/opportunities")
  await page.getByRole("link", { name: /Robotics Deployment Intern/ }).click()
  await page.getByRole("button", { name: "Add" }).click()
  await page.getByLabel("Name").fill("Jane Doe")
  await page.getByLabel("Position").fill("University Recruiter")
  await page.getByLabel("LinkedIn profile").fill("https://www.linkedin.com/company/formic")
  await page.getByRole("button", { name: /add contact/i }).click()
  await expect(page.getByText(/public profile URL/)).toBeVisible()
  await page.getByLabel("LinkedIn profile").fill("https://www.linkedin.com/in/jane-doe-e2e")
  await page.getByRole("button", { name: /add contact/i }).click()
  await expect(page.getByText("Jane Doe added")).toBeVisible()

  await page.getByRole("link", { name: "Message" }).first().click()
  await expect(page.getByRole("heading", { name: "Jane Doe" })).toBeVisible()
  await expect(page.getByText("No message yet")).toBeVisible()          // nothing is generated or sent without a click
  await page.getByRole("button", { name: "Generate message" }).click()
  const text = page.locator("textarea[readonly]").first()
  await expect(text).toHaveValue(/Robotics Deployment internship/)
  await expect(page.getByText(/Checked:/)).toBeVisible()

  await page.getByRole("button", { name: "Copy message" }).click()
  const clip = await page.evaluate(() => navigator.clipboard.readText())
  expect(clip).toContain("Robotics Deployment internship")

  await page.getByRole("button", { name: "Mark as sent" }).click()
  await expect(page.getByText(/Recorded as sent/)).toBeVisible()
  await expect(page.getByText(/message sent/i).first()).toBeVisible()
})

test("command palette finds an opportunity", async ({ page }) => {
  await login(page)
  await page.keyboard.press("ControlOrMeta+k")
  await page.getByPlaceholder(/search/i).last().fill("Stripe")
  await page.getByRole("option", { name: /Solutions Engineer Intern/ }).click()
  await expect(page.getByRole("heading", { name: "Solutions Engineer Intern" })).toBeVisible()
})
