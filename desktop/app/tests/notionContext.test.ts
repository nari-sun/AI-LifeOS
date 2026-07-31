import assert from "node:assert/strict"
import test from "node:test"

import { consumeNotionReference, notionContextForSavedAssistant } from "../src/notionContext.ts"

test("does not attach a failed or cancelled request to an older assistant", () => {
  const context = { requested: true, used: true, status: "ok" }

  assert.equal(notionContextForSavedAssistant(false, context), null)
})

test("keeps context for the assistant saved by the same request", () => {
  const context = { requested: true, used: true, status: "ok" }

  assert.equal(notionContextForSavedAssistant(true, context), context)
})

test("snapshots the Notion checkbox and preserves it for the next request", () => {
  assert.deepEqual(consumeNotionReference(true), { requested: true, next: true })
  assert.deepEqual(consumeNotionReference(false), { requested: false, next: false })
})
