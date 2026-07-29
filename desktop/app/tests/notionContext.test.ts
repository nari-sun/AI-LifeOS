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

test("snapshots the Notion checkbox and resets the next request to OFF", () => {
  assert.deepEqual(consumeNotionReference(true), { requested: true, next: false })
  assert.deepEqual(consumeNotionReference(false), { requested: false, next: false })
})
