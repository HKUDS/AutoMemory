import fs from "fs/promises"
import path from "path"
import type { HandoffRecord } from "./types"
import { memoryDataDir } from "./paths"

function handoffFile(root: string) {
  return path.join(root, "handoff.json")
}

async function ensureDir(dir: string) {
  await fs.mkdir(dir, { recursive: true })
}

export async function readHandoff(sessionDirectory: string): Promise<HandoffRecord | undefined> {
  const root = memoryDataDir(sessionDirectory)
  try {
    const raw = await fs.readFile(handoffFile(root), "utf8")
    return JSON.parse(raw) as HandoffRecord
  } catch {
    return undefined
  }
}

export async function writeHandoff(sessionDirectory: string, rec: HandoffRecord): Promise<void> {
  const root = memoryDataDir(sessionDirectory)
  await ensureDir(root)
  await fs.writeFile(handoffFile(root), JSON.stringify(rec, null, 0), "utf8")
}

export function formatHandoffForPrompt(h: HandoffRecord, maxChars: number): string {
  if (maxChars <= 700) return formatCompactHandoff(h, maxChars)
  const lines = [
    "Current Task State (handoff):",
    ...formatListSection("Next steps", [...h.nextSteps].sort((a, b) => a.length - b.length), 100),
    ...formatListSection("Decisions", h.decisions, 120),
    h.files.length ? `Files: ${h.files.join(", ")}` : "",
    ...formatListSection("Blockers", h.blockers, 120),
    ...formatListSection("In progress", h.inProgress, 120),
    `Goal: ${h.goal}`,
    h.constraints ? `Constraints: ${h.constraints}` : "",
    ...formatListSection("Done", h.done, 120),
  ].filter(Boolean)
  return clampHandoffLines(lines, maxChars)
}

function formatCompactHandoff(h: HandoffRecord, maxChars: number): string {
  const conciseSteps = h.nextSteps.filter((step) => step.length <= 120)
  const stepSource = conciseSteps.length ? conciseSteps : h.nextSteps
  const steps = stepSource.slice(0, h.nextSteps.length > 5 && maxChars < 500 ? 1 : 4)
  const files = h.files.slice(0, maxChars <= 400 ? 1 : 2)
  const lines = [
    "Current Task State (handoff):",
    steps.length ? `Next: ${steps.join("; ")}` : "",
    h.decisions.length ? `Decide: ${h.decisions[0]}` : "",
    files.length ? `Files: ${files.join(", ")}` : "",
    h.blockers.length ? `Blocker: ${h.blockers[0]}` : "",
    h.inProgress.length ? `Progress: ${h.inProgress[0]}` : "",
  ].filter(Boolean)
  return clampHandoffLines(lines, maxChars)
}

function formatListSection(title: string, items: string[], itemMax: number): string[] {
  if (!items.length) return []
  return [
    `${title}:`,
    ...items.map((item) => {
      const text = item.length <= itemMax ? item : `${item.slice(0, itemMax - 1)}…`
      return `- ${text}`
    }),
  ]
}

function clampHandoffLines(lines: string[], maxChars: number): string {
  const out: string[] = []
  let size = 0
  for (const line of lines) {
    const remaining = maxChars - size
    if (remaining <= 1) break
    const next = line.length <= remaining ? line : `${line.slice(0, Math.max(0, remaining - 2))}\n…`
    out.push(next)
    size += next.length + 1
    if (next !== line) break
  }
  return out.join("\n")
}
