import { updateItem, createItem, triggerSync } from '../api'
import type { Item } from '../api/types'

export interface ToolCall {
  name: string
  args: Record<string, unknown>
}

export interface ToolResult {
  toolCall: ToolCall
  result?: unknown
  error?: string
  requiresConfirmation: boolean
  confirmLabel?: string
  confirmed?: boolean
}

/**
 * Parse tool calls from LLM response text.
 * Looks for JSON blocks like: [tool_call: name, {...}]
 */
export function parseToolCalls(text: string): { clean: string; calls: ToolCall[] } {
  const calls: ToolCall[] = []
  const clean = text.replace(/\[tool_call:\s*(\w+),\s*(\{[^}]*\})\]/g, (_, name, argsStr) => {
    try {
      calls.push({ name, args: JSON.parse(argsStr) })
    } catch {}
    return ''
  }).trim()
  return { clean, calls }
}

/** Returns true if the tool needs a confirmation card before executing. */
export function needsConfirmation(name: string): boolean {
  return ['mark_item_done', 'create_item'].includes(name)
}

/** Human-readable description of what a tool call will do. */
export function describeToolCall(tc: ToolCall): string {
  switch (tc.name) {
    case 'mark_item_done':
      return `Mark "${tc.args.title ?? tc.args.id}" as done`
    case 'create_item':
      return `Create item: "${tc.args.title}" for ${tc.args.child}`
    case 'trigger_sync':
    case 'sync_source':
      return `Sync ${tc.args.sources ?? tc.args.source}`
    case 'query_items':
      return `Look up items for ${tc.args.child ?? 'all children'}`
    default:
      return tc.name
  }
}

const SYNC_TOKEN = (window as unknown as { __SYNC_TOKEN__?: string }).__SYNC_TOKEN__ ?? ''

/** Execute a tool call and return the result string for injecting back into LLM. */
export async function executeTool(tc: ToolCall): Promise<string> {
  switch (tc.name) {
    case 'mark_item_done': {
      const id = tc.args.id as number
      await updateItem(id, { completed: true })
      return `Item ${id} marked as done.`
    }
    case 'create_item': {
      const item = await createItem(tc.args as Partial<Item>)
      return `Created item id=${item.id}: "${item.title}"`
    }
    case 'trigger_sync':
    case 'sync_source': {
      const sources = (tc.args.sources ?? tc.args.source ?? 'ixl,sgy') as string
      const digest = (tc.args.digest ?? 'none') as string
      await triggerSync(sources, digest, SYNC_TOKEN)
      return `Sync started for: ${sources}. Check /sync for status.`
    }
    case 'query_items': {
      const { fetchItems } = await import('../api')
      const data = await fetchItems(tc.args.child as string | undefined)
      return `Items: ${JSON.stringify(data.items.slice(0, 10))}`
    }
    default:
      return `Unknown tool: ${tc.name}`
  }
}
