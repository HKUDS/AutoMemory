import type { FactRecord } from "./types"
import { listActiveFacts } from "./facts"
import { listObservations, scoreObservation, tokenize } from "./observations"
import { readHandoff, formatHandoffForPrompt } from "./handoff"
import { readToolLessonsText } from "./tool-stats"
import { listSessionCards, scoreSessionCard } from "./sessions"

export function tokenizePrompt(text: string): string[] {
  return tokenize(text)
}

export type RecallInput = {
  sessionDirectory: string
  workspaceID?: string
  userPrompt: string
  currentSessionID?: string
  availableToolIds?: string[]
  maxTotalChars?: number
}

export async function buildMemoryContextBlocks(input: RecallInput): Promise<string[]> {
  const max = input.maxTotalChars ?? 9000
  const tokens = new Set(tokenizePrompt(input.userPrompt))
  const blocks: string[] = []

  const facts = await listActiveFacts(input.sessionDirectory, 40)
  const factLines = formatFactsBlock(facts, input.workspaceID)
  if (factLines) blocks.push(factLines)

  const handoff = await readHandoff(input.sessionDirectory)
  if (handoff) {
    const h = formatHandoffForPrompt(handoff, 1200)
    if (h) blocks.push(h)
  }

  const obs = await listObservations(input.sessionDirectory, 500)
  const ranked = obs
    .map((o) => ({ o, s: scoreObservation(o, tokens) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s)
    .slice(0, 5)
  if (ranked.length) {
    const body = ranked
      .map(({ o }) => `### ${o.title}\n- (${o.type}) ${o.l0}\n${o.l1.slice(0, 900)}`)
      .join("\n\n")
    blocks.push(`Relevant Past Experience:\n${body}`)
  }

  const toolsTxt = await readToolLessonsText(input.sessionDirectory, input.availableToolIds, 900)
  if (toolsTxt) blocks.push(toolsTxt)

  const cards = (await listSessionCards(input.sessionDirectory)).filter(
    (c) => c.sessionID !== input.currentSessionID,
  )
  const cranked = cards
    .map((c) => ({ c, s: scoreSessionCard(c, tokens) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s)
    .slice(0, 3)
  if (cranked.length) {
    const body = cranked.map(({ c }) => `### ${c.title}\n${c.l0}\n${c.l1.slice(0, 600)}`).join("\n\n")
    blocks.push(`Related Prior Sessions:\n${body}`)
  }

  return clampBlocks(blocks, max)
}

function formatFactsBlock(facts: FactRecord[], workspaceID?: string): string {
  const scoped = facts.filter((f) => {
    if (f.scope === "workspace" && workspaceID && f.text.includes(workspaceID)) return true
    if (f.scope === "workspace" && !workspaceID) return true
    if (f.scope === "project" || f.scope === "global") return true
    if (f.scope === "session") return false
    return true
  })
  if (!scoped.length) return ""
  const lines = scoped.slice(0, 24).map((f) => `- [${f.owner}/${f.scope}] **${f.title}**: ${f.text.slice(0, 400)}`)
  return `Remembered User/Project Facts:\n${lines.join("\n")}`
}

function clampBlocks(blocks: string[], max: number): string[] {
  const out: string[] = []
  let n = 0
  for (const b of blocks) {
    if (n + b.length > max) {
      const rest = max - n
      if (rest > 80) out.push(b.slice(0, rest) + "\n…")
      break
    }
    out.push(b)
    n += b.length
  }
  return out
}
