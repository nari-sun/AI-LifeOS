export function notionContextForSavedAssistant<T>(assistantSaved: boolean, context: T | null): T | null {
  return assistantSaved ? context : null
}

export function consumeNotionReference(value: boolean) {
  return { requested: value, next: value } as const
}
