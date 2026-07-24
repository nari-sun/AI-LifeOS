import assert from "node:assert/strict"
import test from "node:test"

import { notionContextForSavedAssistant } from "../src/notionContext.ts"

test("does not attach a failed or cancelled request to an older assistant", () => {
  const context = { requested: true, used: true, status: "ok" }

  assert.equal(notionContextForSavedAssistant(false, context), null)
})

test("keeps context for the assistant saved by the same request", () => {
  const context = { requested: true, used: true, status: "ok" }

  assert.equal(notionContextForSavedAssistant(true, context), context)
})
