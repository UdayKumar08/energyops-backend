import { expect, test } from "@playwright/test";
import { HARBOUR_ADMIN, HARBOUR_MANAGER, HARBOUR_TECH, HARBOUR_VIEWER, NORTH_MANAGER, signIn } from "./helpers";

test("an unknown email cannot sign in and a known one lands on the overview", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email address").fill("nobody@example.com");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("This account has not been given access.")).toBeVisible();
  await expect(page).toHaveURL(/login/);

  await signIn(page, HARBOUR_MANAGER);
  await expect(page.getByRole("heading", { name: "Harbour Head Office", level: 1 })).toBeVisible();
  await expect(page.getByRole("img", { name: /Average demand for each hour/ })).toBeVisible();
  await expect(page.getByText("Average energy per complete day")).toBeVisible();
});

test("pages need a sign-in, and signing out ends it", async ({ page }) => {
  await page.goto("/tickets");
  await expect(page).toHaveURL(/login/);
  await signIn(page, HARBOUR_VIEWER);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/login/);
  await page.goto("/");
  await expect(page).toHaveURL(/login/);
});

test("the session token is never readable by page scripts", async ({ page, context }) => {
  await signIn(page, HARBOUR_MANAGER);
  expect(await page.evaluate(() => document.cookie)).not.toContain("eo_session");
  const cookie = (await context.cookies()).find((c) => c.name === "eo_session");
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Lax");
});

test("the menu follows the role", async ({ page }) => {
  await signIn(page, HARBOUR_VIEWER);
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Overview" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Assistant" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "People" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Agents" })).toHaveCount(0);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/login/);

  await signIn(page, HARBOUR_ADMIN);
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "People" })).toBeVisible();
});

test("a technician limited to one site sees only that site", async ({ page }) => {
  await signIn(page, HARBOUR_TECH);
  const options = await page.getByLabel("Site", { exact: true }).locator("option").allTextContents();
  expect(options).toEqual(["Harbour Hotel"]);
});

test("the assistant answers a cost question through the savings advisor", async ({ page }) => {
  await signIn(page, HARBOUR_MANAGER);
  await page.getByRole("link", { name: "Assistant" }).click();
  await page.getByLabel("Your question").fill("How much could we save on our energy bill?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByText("Sent to")).toBeVisible();
  await expect(page.getByText("savings", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/This answer came from the built-in test model/).first()).toBeVisible();
});

test("the assistant declines what is out of scope with a fixed message", async ({ page }) => {
  await signIn(page, HARBOUR_MANAGER);
  await page.goto("/assistant");
  await page.getByLabel("Your question").fill("Write me a poem about winter.");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByText(/I can help with energy use/)).toBeVisible();
});

test("a forecast shows its limits and how it was tested", async ({ page }) => {
  await signIn(page, HARBOUR_VIEWER);
  await page.getByRole("link", { name: "Forecast" }).click();
  await expect(page.getByText("How good is this forecast?")).toBeVisible();
  await expect(page.getByText(/does not know about weather/)).toBeVisible();
});

test("a report is written for a chosen audience", async ({ page }) => {
  await signIn(page, HARBOUR_MANAGER);
  await page.getByRole("link", { name: "Reports" }).click();
  await page.getByRole("button", { name: "Write the report" }).click();
  await expect(page.getByText(/This answer came from the built-in test model/).first()).toBeVisible();
});

test("an upload is checked, refused when wrong, and accepted when right", async ({ page }) => {
  await signIn(page, HARBOUR_MANAGER);
  await page.getByRole("link", { name: "Upload readings" }).click();
  const input = page.getByLabel(/CSV file for/);

  await input.setInputFiles({ name: "bad.csv", mimeType: "text/csv", buffer: Buffer.from("timestamp,power_kw\n2026-04-01T00:00:00,5\n") });
  await page.getByRole("button", { name: "Upload readings" }).click();
  await expect(page.getByText(/nothing was stored/)).toBeVisible();
  await expect(page.getByText(/no UTC offset/)).toBeVisible();

  const good = "timestamp,power_kw\n2026-04-01T00:00:00Z,10\n2026-04-01T00:15:00Z,20\n";
  await input.setInputFiles({ name: "good.csv", mimeType: "text/csv", buffer: Buffer.from(good) });
  await page.getByRole("button", { name: "Check the file" }).click();
  await expect(page.getByText(/Nothing has been stored yet/)).toBeVisible();
  await page.getByRole("button", { name: "Upload readings" }).click();
  await expect(page.getByText(/^Uploaded\./)).toBeVisible();
  await page.reload();
  await expect(page.getByRole("cell", { name: "good.csv" })).toBeVisible();
});

test("documents can be added, searched and are not visible to another organisation", async ({ page }) => {
  await signIn(page, HARBOUR_MANAGER);
  await page.getByRole("link", { name: "Knowledge" }).click();
  await page.getByLabel("Text or markdown file").setInputFiles({
    name: "boiler.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("Boiler lockout\n\nIf the boiler locks out, wait five minutes and press reset once."),
  });
  await page.getByRole("button", { name: "Add document" }).click();
  await expect(page.getByText(/Added "boiler"/)).toBeVisible();
  await page.getByLabel("What are you looking for").fill("boiler lockout reset");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByText(/press reset once/)).toBeVisible();
  await expect(page.getByText(/matches shared wording only/)).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/login/);

  await signIn(page, NORTH_MANAGER);
  await page.goto("/knowledge");
  await expect(page.getByText("No documents yet")).toBeVisible();
  await page.getByLabel("What are you looking for").fill("boiler lockout reset");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page.getByText(/No passage in your documents matches/)).toBeVisible();
});

test("an event can be turned into a ticket that a manager approves", async ({ page }) => {
  await signIn(page, NORTH_MANAGER);
  await expect(page.getByRole("heading", { name: "Unusual events" })).toBeVisible();
  await page.getByRole("button", { name: "Draft ticket" }).first().click();
  await expect(page.getByText(/Ticket drafted/)).toBeVisible();
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Tickets" }).click();
  await expect(page.getByText(/awaiting approval|pending approval/).first()).toBeVisible();
  await page.getByRole("button", { name: "Approve ticket" }).first().click();
  await expect(page.getByText("approved", { exact: true }).first()).toBeVisible();
});

test("an administrator adds a person who can then sign in", async ({ page, browser }) => {
  await signIn(page, HARBOUR_ADMIN);
  await page.getByRole("link", { name: "People" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Wren Walker");
  await page.getByLabel("Email address").fill("wren@harbour.example");
  await page.getByRole("button", { name: "Add person" }).click();
  await expect(page.getByText(/Wren Walker was added/)).toBeVisible();
  await expect(page.getByRole("cell", { name: /Wren Walker/ })).toBeVisible();

  const other = await browser.newPage({ baseURL: "http://127.0.0.1:3100" });
  await signIn(other, "wren@harbour.example");
  await expect(other.getByRole("heading", { level: 1 })).toBeVisible();
  await other.close();
});

test("on a phone the menu is folded away until it is opened", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await signIn(page, HARBOUR_MANAGER);
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav).toBeHidden();
  await page.getByRole("button", { name: "Menu" }).click();
  await expect(nav.getByRole("link", { name: "Overview" })).toBeVisible();
  await page.getByRole("button", { name: "Close menu" }).click();
  await expect(nav).toBeHidden();
});

// These tests replace the browser's speech recognition with a stand-in, because a headless
// browser has no microphone. They check our code's handling of what the browser reports, not
// that any real speech is recognised.
const fakeRecognition = (behaviour: "hears" | "blocked") => `
  class FakeRecognition {
    start() {
      setTimeout(() => {
        if ("${behaviour}" === "hears") {
          this.onresult({ results: [[{ transcript: "how much could we save on our bill" }]] });
        } else {
          this.onerror({ error: "not-allowed" });
        }
        this.onend();
      }, 20);
    }
    stop() { this.onend && this.onend(); }
  }
  window.SpeechRecognition = FakeRecognition;
  window.webkitSpeechRecognition = FakeRecognition;
`;

test("speaking fills in the question, and the page says the browser may send audio away", async ({ page }) => {
  await page.addInitScript(fakeRecognition("hears"));
  await signIn(page, HARBOUR_MANAGER);
  await page.goto("/assistant");
  await expect(page.getByText(/may send the audio to the browser/)).toBeVisible();
  await page.getByRole("button", { name: "Speak" }).click();
  await expect(page.getByLabel("Your question")).toHaveValue("how much could we save on our bill");
});

test("a blocked microphone is explained and typing still works", async ({ page }) => {
  await page.addInitScript(fakeRecognition("blocked"));
  await signIn(page, HARBOUR_MANAGER);
  await page.goto("/assistant");
  await page.getByRole("button", { name: "Speak" }).click();
  await expect(page.getByText(/microphone is blocked/)).toBeVisible();
  await page.getByLabel("Your question").fill("Write me a poem about winter.");
  await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeEnabled();
});

test("without speech support the page says so and offers typing", async ({ page }) => {
  await page.addInitScript(`
    delete window.SpeechRecognition;
    delete window.webkitSpeechRecognition;
  `);
  await signIn(page, HARBOUR_MANAGER);
  await page.goto("/assistant");
  await expect(page.getByText("Voice input is not available in this browser. Type your question.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Speak" })).toHaveCount(0);
});
