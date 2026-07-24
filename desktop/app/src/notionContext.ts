export function notionContextForSavedAssistant<T>(assistantSaved: boolean, context: T | null): T | null {
  return assistantSaved ? context : null
}
