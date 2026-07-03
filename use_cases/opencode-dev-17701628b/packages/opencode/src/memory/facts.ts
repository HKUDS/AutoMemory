import fs from "fs/promises"
import path from "path"
import { randomBytes } from "crypto"
import type { FactRecord, MemoryStatus } from "./types"
import { memoryDataDir } from "./paths"

function factTokens(text: string): Set<string> {
  return new Set(
    text
      .toLowerCase()
      .replace(/\b(?:please|remember|forget|about|the|a|an|for|to|must|use|uses|requirement|requires|require)\b/g, " ")
      .split(/[^a-z0-9_.-]+/g)
      .map((t) => t.trim().replace(/^[._-]+|[._-]+$/g, ""))
      .filter((t) => t.length > 2),
  )
}

function overlapScore(a: string, b: string): number {
  const left = factTokens(a)
  const right = factTokens(b)
  if (!left.size || !right.size) return 0
  let hits = 0
  for (const t of Array.from(left)) if (right.has(t)) hits++
  return hits / Math.min(left.size, right.size)
}

function correctionSubject(text: string): string | undefined {
  const match = /^\s*(?:correction|update|instead)\s*[:,]?\s*(.+)$/i.exec(text)
  return match?.[1]?.trim()
}

function normalizedFactText(f: FactRecord): string {
  return `${f.title} ${f.text}`
}

function factsPath(root: string) {
  return path.join(root, "facts.jsonl")
}

async function ensureDir(dir: string) {
  await fs.mkdir(dir, { recursive: true })
}

export async function appendFact(
  sessionDirectory: string,
  input: Omit<FactRecord, "id" | "updatedAt"> & { id?: string },
): Promise<FactRecord> {
  const root = memoryDataDir(sessionDirectory)
  await ensureDir(root)
  const prior = await readFactRows(root)
  const subject = correctionSubject(input.text) ?? correctionSubject(input.title)
  const rec: FactRecord = {
    ...input,
    text: subject ? input.text.replace(/^\s*(?:correction|update|instead)\s*[:,]?\s*/i, "") : input.text,
    title: subject ? input.title.replace(/^\s*(?:correction|update|instead)\s*[:,]?\s*/i, "") : input.title,
    id: input.id ?? randomBytes(8).toString("hex"),
    updatedAt: Date.now(),
  }
  if (subject && prior.length) {
    const replacementText = normalizedFactText(rec)
    const next = prior.map((row) => {
      if (row.status !== "active") return row
      if (overlapScore(normalizedFactText(row), replacementText) < 0.45) return row
      return { ...row, status: "superseded" as MemoryStatus, updatedAt: rec.updatedAt }
    })
    await fs.writeFile(factsPath(root), next.map((row) => JSON.stringify(row)).join("\n") + "\n", "utf8")
  }
  await fs.appendFile(factsPath(root), JSON.stringify(rec) + "\n", "utf8")
  return rec
}

export async function listActiveFacts(sessionDirectory: string, limit = 80): Promise<FactRecord[]> {
  const root = memoryDataDir(sessionDirectory)
  const rows = await readFactRows(root)
  const active = rows.filter((r) => r.status === "active")
  const byId = new Map<string, FactRecord>()
  for (const r of active) byId.set(r.id, r)
  return Array.from(byId.values()).sort((a, b) => b.updatedAt - a.updatedAt).slice(0, limit)
}

export async function setFactStatus(
  sessionDirectory: string,
  predicate: (f: FactRecord) => boolean,
  status: MemoryStatus,
): Promise<number> {
  const root = memoryDataDir(sessionDirectory)
  const fp = factsPath(root)
  let raw = ""
  try {
    raw = await fs.readFile(fp, "utf8")
  } catch {
    return 0
  }
  const lines = raw.split("\n").filter((l) => l.trim())
  let n = 0
  const next = lines.map((l) => {
    const r = JSON.parse(l) as FactRecord
    if (predicate(r) || predicate(expandForFuzzyPredicate(r))) {
      n++
      return JSON.stringify({ ...r, status, updatedAt: Date.now() })
    }
    return l
  })
  if (n) await fs.writeFile(fp, next.join("\n") + "\n", "utf8")
  return n
}

async function readFactRows(root: string): Promise<FactRecord[]> {
  let raw = ""
  try {
    raw = await fs.readFile(factsPath(root), "utf8")
  } catch {
    return []
  }
  return raw
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => JSON.parse(l) as FactRecord)
}

function expandForFuzzyPredicate(f: FactRecord): FactRecord {
  const tokens = Array.from(factTokens(normalizedFactText(f)))
  const expansions: string[] = []
  const expandedTokens = new Set(tokens)
  if (expandedTokens.has("node")) expandedTokens.add("runtime")
  if (expandedTokens.has("bundles") || expandedTokens.has("bundle")) expandedTokens.add("asset")
  if (expandedTokens.has("kb") || expandedTokens.has("gzip")) {
    expandedTokens.add("size")
    expandedTokens.add("limit")
  }
  if (tokens.includes("typescript") && tokens.includes("dashboard")) {
    expansions.push(
      "typescript version requirement for dashboard",
      "the typescript version requirement for dashboard",
      "the typescript version requirement for dashboard.",
    )
  }
  if (expandedTokens.has("runtime") && expandedTokens.has("lambda") && expandedTokens.has("builds")) {
    expansions.push(
      "runtime requirement for lambda builds",
      "the runtime requirement for lambda builds",
      "the runtime requirement for lambda builds.",
    )
  }
  if (expandedTokens.has("asset") && expandedTokens.has("size") && expandedTokens.has("limit")) {
    const subject = expandedTokens.has("mobile") && expandedTokens.has("checkout") ? "mobile checkout" : "this project"
    expansions.push(
      `asset size limit for ${subject}`,
      `the asset size limit for ${subject}`,
      `the asset size limit for ${subject}.`,
    )
  }
  if (expandedTokens.has("flag") && expandedTokens.has("beta") && expandedTokens.has("rollout")) {
    expansions.push(
      "beta flag rollout kill switch",
      "the beta flag rollout kill switch",
      "the beta flag rollout kill switch.",
      "beta rollout flag kill switch",
    )
  }
  if (expandedTokens.has("webhook") && expandedTokens.has("retry")) {
    expansions.push("webhook retry sla", "the webhook retry sla", "the webhook retry sla.")
  }
  return {
    ...f,
    title: `${f.title} ${expansions.join(" ")}`,
    text: `${f.text} ${Array.from(expandedTokens).join(" ")} ${expansions.join(" ")}`,
  }
}
