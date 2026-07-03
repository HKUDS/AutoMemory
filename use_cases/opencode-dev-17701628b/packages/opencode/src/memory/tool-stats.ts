import fs from "fs/promises"
import path from "path"
import type { ToolStatRecord } from "./types"
import { memoryDataDir } from "./paths"

function statsPath(root: string) {
  return path.join(root, "tool_stats.json")
}

async function ensureDir(dir: string) {
  await fs.mkdir(dir, { recursive: true })
}

type Store = Record<string, ToolStatRecord>

async function load(sessionDirectory: string): Promise<Store> {
  const root = memoryDataDir(sessionDirectory)
  try {
    const raw = await fs.readFile(statsPath(root), "utf8")
    return JSON.parse(raw) as Store
  } catch {
    return {}
  }
}

async function save(sessionDirectory: string, data: Store) {
  const root = memoryDataDir(sessionDirectory)
  await ensureDir(root)
  await fs.writeFile(statsPath(root), JSON.stringify(data, null, 0), "utf8")
}

export async function recordToolOutcome(
  sessionDirectory: string,
  input: { tool: string; ok: boolean; durationMs: number; error?: string },
): Promise<void> {
  const data = await load(sessionDirectory)
  const prev = data[input.tool] ?? {
    tool: input.tool,
    calls: 0,
    successes: 0,
    errors: 0,
    totalDurationMs: 0,
    updatedAt: 0,
  }
  prev.calls += 1
  if (input.ok) prev.successes += 1
  else {
    prev.errors += 1
    if (input.error) {
      const sample = input.error.slice(0, 400)
      prev.lastErrorSample = sample
      prev.errorSamples = prev.errorSamples ?? {}
      prev.errorSamples[sample] = (prev.errorSamples[sample] ?? 0) + 1
    }
  }
  prev.totalDurationMs += Math.max(0, input.durationMs)
  prev.updatedAt = Date.now()
  if (prev.errors >= 3 && prev.calls >= 3 && prev.errors / prev.calls > 0.4) {
    prev.guideline = `Tool "${input.tool}" has failed often in this workspace; double-check inputs and permissions before retrying.`
  } else {
    delete prev.guideline
  }
  data[input.tool] = prev
  await save(sessionDirectory, data)
}

export async function readToolLessonsText(
  sessionDirectory: string,
  toolIds: string[] | undefined,
  maxChars: number,
): Promise<string> {
  const data = await load(sessionDirectory)
  const lines: string[] = []
  const keys = toolIds?.length ? toolIds.filter((t) => data[t]) : Object.keys(data)
  const perToolMax = Math.max(90, Math.floor((maxChars - 36) / Math.max(1, keys.length)))
  for (const k of keys) {
    const r = data[k]
    if (!r) continue
    lines.push(formatToolLessonLine(r, perToolMax))
  }
  const body = lines.join("\n")
  if (!body) return ""
  const header = "Tool Lessons (workspace metrics):"
  const out = `${header}\n${body}`
  return out.length <= maxChars ? out : out.slice(0, maxChars) + "\n…"
}

function formatToolLessonLine(r: ToolStatRecord, maxChars: number): string {
    const avg = r.calls ? Math.round(r.totalDurationMs / r.calls) : 0
    const warning = r.guideline ? " failed often" : ""
    const commonErrors = Object.entries(r.errorSamples ?? {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
    const sampleMax =
      commonErrors.length > 1
        ? Math.min(140, Math.max(70, Math.floor(maxChars / commonErrors.length) - 24))
        : Math.min(180, Math.max(90, maxChars - 90))
    const commonErrorText = commonErrors.length
      ? ` commonErrors=${commonErrors.map(([sample, count]) => `${formatErrorSample(sample, sampleMax)} (x${count})`).join("; ")}`
      : ""
    const errorSample = r.lastErrorSample ? ` lastError=${r.lastErrorSample.slice(0, 60)}` : ""
    const line = `- ${r.tool}: calls=${r.calls} ok=${r.successes} err=${r.errors} avgMs=${avg}${warning}${commonErrorText}${errorSample}`
    return line.length <= maxChars ? line : line.slice(0, maxChars - 1) + "…"
}

function formatErrorSample(sample: string, maxChars: number): string {
  if (sample.length <= maxChars) return sample
  const markers = sample.match(/\b[A-Z0-9_]{8,}\b/g) ?? []
  const suffix = markers.length ? ` ${markers.slice(-2).join(" ")}` : ""
  const prefixChars = Math.max(24, maxChars - suffix.length - 2)
  return `${sample.slice(0, prefixChars)}…${suffix}`.slice(0, maxChars)
}
