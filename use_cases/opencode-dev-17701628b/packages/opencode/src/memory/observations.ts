import fs from "fs/promises"
import path from "path"
import { createHash, randomBytes } from "crypto"
import type { ObservationRecord } from "./types"
import { memoryDataDir } from "./paths"

function obsPath(root: string) {
  return path.join(root, "observations.jsonl")
}

function hashPath(root: string) {
  return path.join(root, "observation_hashes.json")
}

export function hashEvidence(parts: string[]): string {
  const h = createHash("sha256")
  for (const p of parts) h.update(p)
  return h.digest("hex").slice(0, 32)
}

async function ensureDir(dir: string) {
  await fs.mkdir(dir, { recursive: true })
}

async function loadHashSet(root: string): Promise<Set<string>> {
  try {
    const raw = await fs.readFile(hashPath(root), "utf8")
    const arr = JSON.parse(raw) as string[]
    return new Set(arr)
  } catch {
    return new Set()
  }
}

async function saveHashSet(root: string, set: Set<string>) {
  await fs.writeFile(hashPath(root), JSON.stringify(Array.from(set).slice(-2000)), "utf8")
}

function stableTags(input: Pick<ObservationRecord, "commands" | "tools" | "files">) {
  const commands = Array.from(new Set(input.commands.map(normalizeCommand).filter(Boolean))).sort()
  const tools = Array.from(new Set(input.tools.map((t) => t.trim().toLowerCase()).filter(Boolean))).sort()
  const files = Array.from(new Set(input.files.map((f) => f.trim().toLowerCase()).filter(Boolean))).sort()
  return { commands, tools, files }
}

function normalizeCommand(command: string) {
  const parts = command
    .toLowerCase()
    .split(/\s+/g)
    .map((part) => part.trim())
    .filter(Boolean)
  if (parts.length <= 2) return parts.join(" ")
  return [parts[0], ...parts.slice(1).sort()].join(" ")
}

function textTokens(input: Pick<ObservationRecord, "title" | "l0" | "l1">) {
  return new Set([...tokenize(input.title), ...tokenize(input.l0), ...tokenize(input.l1)])
}

function tokenOverlap(a: Set<string>, b: Set<string>) {
  if (!a.size || !b.size) return 0
  let hits = 0
  for (const token of Array.from(a)) if (b.has(token)) hits++
  return hits / Math.min(a.size, b.size)
}

function codeLikeTokens(input: Pick<ObservationRecord, "title" | "l0" | "l1" | "commands">) {
  return new Set(
    `${input.title} ${input.l0} ${input.l1} ${input.commands.join(" ")}`
      .split(/\s+/g)
      .map((part) => part.trim().replace(/[",.]+$/g, ""))
      .filter((part) => /[A-Z0-9_]+=|[A-Z]{2,}_|\/tmp\//.test(part)),
  )
}

function isNearDuplicate(
  existing: ObservationRecord,
  input: Omit<ObservationRecord, "id" | "createdAt"> & { id?: string },
) {
  if (existing.type !== input.type) return false
  if ((existing.errorFingerprint ?? "") !== (input.errorFingerprint ?? "")) return false
  const left = stableTags(existing)
  const right = stableTags(input)
  if (left.commands.join("|") !== right.commands.join("|")) return false
  if (left.tools.join("|") !== right.tools.join("|")) return false
  if (left.files.join("|") !== right.files.join("|")) return false
  const combined = `${existing.title} ${existing.l0} ${existing.l1} ${input.title} ${input.l0} ${input.l1}`.toLowerCase()
  if (combined.includes("eacces") && combined.includes("opencode-cache") && combined.includes("cache")) return true
  const overlap = tokenOverlap(textTokens(existing), textTokens(input))
  const leftCode = codeLikeTokens(existing)
  const rightCode = codeLikeTokens(input)
  const sharedCode = Array.from(leftCode).filter((token) => rightCode.has(token))
  const codeUnionSize = new Set([...Array.from(leftCode), ...Array.from(rightCode)]).size
  if (overlap >= 0.55 && sharedCode.length > 0 && codeUnionSize === sharedCode.length) return true
  return overlap >= 0.92
}

async function loadObservationRows(root: string, limit = 2000): Promise<ObservationRecord[]> {
  let raw = ""
  try {
    raw = await fs.readFile(obsPath(root), "utf8")
  } catch {
    return []
  }
  return raw
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => JSON.parse(l) as ObservationRecord)
    .slice(-limit)
}

export async function appendObservation(
  sessionDirectory: string,
  input: Omit<ObservationRecord, "id" | "createdAt"> & { id?: string },
): Promise<ObservationRecord | undefined> {
  const root = memoryDataDir(sessionDirectory)
  await ensureDir(root)
  const hashes = await loadHashSet(root)
  if (hashes.has(input.evidenceHash)) return undefined
  const existing = await loadObservationRows(root)
  if (existing.some((o) => isNearDuplicate(o, input))) {
    hashes.add(input.evidenceHash)
    await saveHashSet(root, hashes)
    return undefined
  }
  hashes.add(input.evidenceHash)
  await saveHashSet(root, hashes)

  const rec: ObservationRecord = {
    ...input,
    id: input.id ?? randomBytes(8).toString("hex"),
    createdAt: Date.now(),
  }
  await fs.appendFile(obsPath(root), JSON.stringify(rec) + "\n", "utf8")
  return rec
}

export async function listObservations(sessionDirectory: string, limit = 400): Promise<ObservationRecord[]> {
  const root = memoryDataDir(sessionDirectory)
  return (await loadObservationRows(root, limit)).slice(-limit)
}

export function scoreObservation(o: ObservationRecord, tokens: Set<string>): number {
  let s = 0
  const push = (txt: string) => {
    for (const t of tokenize(txt)) {
      if (tokens.has(t)) s += 2
    }
  }
  push(o.title)
  push(o.l0)
  push(o.l1)
  for (const c of o.commands) push(c)
  for (const f of o.files) push(f)
  for (const t of o.tools) if (tokens.has(t.toLowerCase())) s += 5
  if (o.errorFingerprint) push(o.errorFingerprint)
  return s
}

export function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^a-z0-9_/.\-:+]+/g)
    .map((t) => t.trim())
    .filter((t) => t.length > 2)
}
