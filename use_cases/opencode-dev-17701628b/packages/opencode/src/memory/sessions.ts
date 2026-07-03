import fs from "fs/promises"
import path from "path"
import { randomBytes } from "crypto"
import type { SessionCardRecord } from "./types"
import { memoryDataDir } from "./paths"
import { tokenize } from "./observations"

function sessionsPath(root: string) {
  return path.join(root, "sessions.jsonl")
}

async function ensureDir(dir: string) {
  await fs.mkdir(dir, { recursive: true })
}

export async function appendSessionCard(sessionDirectory: string, card: SessionCardRecord): Promise<void> {
  const root = memoryDataDir(sessionDirectory)
  await ensureDir(root)
  const existing = await readSessionRows(root)
  const next = existing.filter((row) => row.sessionID !== card.sessionID)
  next.push({ ...card, _id: randomBytes(4).toString("hex") })
  await fs.writeFile(sessionsPath(root), next.map((row) => JSON.stringify(row)).join("\n") + "\n", "utf8")
}

export async function listSessionCards(sessionDirectory: string, limit = 200): Promise<SessionCardRecord[]> {
  const root = memoryDataDir(sessionDirectory)
  return (await readSessionRows(root))
    .map((o) => {
      delete (o as { _id?: string })._id
      return o as SessionCardRecord
    })
    .slice(-limit)
}

async function readSessionRows(root: string): Promise<Array<SessionCardRecord & { _id?: string }>> {
  let raw = ""
  try {
    raw = await fs.readFile(sessionsPath(root), "utf8")
  } catch {
    return []
  }
  return raw
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => JSON.parse(l) as SessionCardRecord & { _id?: string })
}

export function scoreSessionCard(card: SessionCardRecord, tokens: Set<string>): number {
  let s = 0
  const scan = (t: string) => {
    for (const w of tokenize(t)) if (tokens.has(w)) s += 1
  }
  scan(card.title)
  scan(card.goal)
  scan(card.outcome)
  for (const c of card.concepts) if (tokens.has(c.toLowerCase())) s += 2
  for (const f of card.files) scan(f)
  for (const c of card.commands) scan(c)
  return s
}
