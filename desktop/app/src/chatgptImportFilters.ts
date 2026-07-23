export type ChatGptImportState = "new" | "duplicate" | "updated" | "conflict"

export interface ChatGptImportFilterItem {
  source_id: string
  title: string
  created_at: string | null
  import_state: ChatGptImportState
}

export interface ChatGptImportFilters {
  query: string
  fromDate: string
  toDate: string
}

export function hasInvalidChatGptImportDateRange(fromDate: string, toDate: string) {
  return Boolean(fromDate && toDate && fromDate > toDate)
}

export function filterChatGptImportConversations<T extends ChatGptImportFilterItem>(
  conversations: readonly T[],
  filters: ChatGptImportFilters,
) {
  const { query, fromDate, toDate } = filters
  if (hasInvalidChatGptImportDateRange(fromDate, toDate)) {
    return []
  }

  const normalizedQuery = query.trim().toLocaleLowerCase()
  return conversations.filter((item) => {
    const matchesQuery = !normalizedQuery
      || item.title.toLocaleLowerCase().includes(normalizedQuery)
      || item.source_id.toLocaleLowerCase().includes(normalizedQuery)
    const createdDate = item.created_at?.slice(0, 10) ?? null
    const matchesFromDate = !fromDate || (createdDate !== null && createdDate >= fromDate)
    const matchesToDate = !toDate || (createdDate !== null && createdDate <= toDate)
    return matchesQuery && matchesFromDate && matchesToDate
  })
}

export function isChatGptImportEligible(item: ChatGptImportFilterItem) {
  return item.import_state === "new" || item.import_state === "updated"
}
