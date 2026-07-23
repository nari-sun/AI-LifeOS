import assert from "node:assert/strict"
import test from "node:test"

import {
  filterChatGptImportConversations,
  hasInvalidChatGptImportDateRange,
  isChatGptImportEligible,
  type ChatGptImportFilterItem,
  type ChatGptImportState,
} from "../src/chatgptImportFilters.ts"

function conversation(
  sourceId: string,
  createdDate: string | null,
  importState: ChatGptImportState = "new",
): ChatGptImportFilterItem {
  return {
    source_id: sourceId,
    title: `Title ${sourceId}`,
    created_at: createdDate ? `${createdDate}T12:00:00Z` : null,
    import_state: importState,
  }
}

test("date-only filter applies even when query is blank", () => {
  const items = [
    conversation("old", "2025-01-01"),
    conversation("target", "2026-02-03"),
    conversation("new", "2027-01-01"),
  ]

  const filtered = filterChatGptImportConversations(items, {
    query: "",
    fromDate: "2026-01-01",
    toDate: "2026-12-31",
  })

  assert.deepEqual(filtered.map((item) => item.source_id), ["target"])
})

test("invalid date range returns no selectable rows", () => {
  const items = [conversation("target", "2026-02-03")]

  assert.equal(hasInvalidChatGptImportDateRange("2026-12-31", "2026-01-01"), true)
  assert.deepEqual(
    filterChatGptImportConversations(items, {
      query: "",
      fromDate: "2026-12-31",
      toDate: "2026-01-01",
    }),
    [],
  )
})

test("only new and updated rows are eligible for apply", () => {
  assert.equal(isChatGptImportEligible(conversation("new", "2026-01-01", "new")), true)
  assert.equal(isChatGptImportEligible(conversation("updated", "2026-01-01", "updated")), true)
  assert.equal(isChatGptImportEligible(conversation("duplicate", "2026-01-01", "duplicate")), false)
  assert.equal(isChatGptImportEligible(conversation("conflict", "2026-01-01", "conflict")), false)
})
