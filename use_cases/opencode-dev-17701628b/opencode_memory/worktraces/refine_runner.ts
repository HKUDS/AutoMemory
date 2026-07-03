#!/usr/bin/env bun
import * as fs from "fs/promises"
import * as path from "path"

import {
  appendFact,
  appendObservation,
  appendSessionCard,
  buildMemoryContextBlocks,
  formatHandoffForPrompt,
  hashEvidence,
  listActiveFacts,
  listObservations,
  listSessionCards,
  readHandoff,
  readToolLessonsText,
  recordToolOutcome,
  setFactStatus,
  writeHandoff,
} from "../../packages/opencode/src/memory/index"
import { persistSessionTrajectory } from "../../packages/opencode/src/memory/session-archive"

type Matcher = "substring" | "regex" | "exact"
type Expected = { value: string; matcher?: Matcher; description?: string }
type Trace = {
  trace_id: string
  eval_mode?: "qa" | "hit_rate"
  category?: string
  description?: string
  turns?: Turn[]
  recall_queries?: RecallQuery[]
  checks?: Check[]
}
type Turn = {
  role: string
  content: string
  used_for_evaluation?: boolean
  metadata?: Record<string, any>
}
type RecallQuery = { query: string; expected_information: Expected[]; metadata?: Record<string, any> }
type Check = { description: string; write_input: any; read_input?: any; expected_output: Expected }
type RegistryRow = { trace_id: string; memory_path: string; focus: string; created_in_phase: number }

const defaultStatePath = "/data2/junzhou2/opencode/opencode_memory/worktraces/refine_state.json"

function argValue(name: string, fallback?: string) {
  const i = process.argv.indexOf(name)
  return i >= 0 ? process.argv[i + 1] : fallback
}

function argList(name: string) {
  const i = process.argv.indexOf(name)
  if (i < 0 || !process.argv[i + 1]) return [] as string[]
  return process.argv[i + 1].split(",").map((s) => s.trim()).filter(Boolean)
}

function unwrapState(raw: any) {
  return raw.refine_state ?? raw
}

async function readJson(file: string) {
  return JSON.parse(await fs.readFile(file, "utf8"))
}

function stringify(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2)
}

function matches(output: unknown, expected: Expected) {
  const actual = stringify(output)
  const matcher = expected.matcher ?? "substring"
  if (matcher === "exact") return actual === expected.value
  if (matcher === "regex") return new RegExp(expected.value, "is").test(actual)
  return actual.toLowerCase().includes(expected.value.toLowerCase())
}

function factFromText(content: string, sessionID = "trace") {
  const remember = /^(?:please\s+)?remember\s*[:,]?\s*(.+)$/i.exec(content) ?? /^memorize\s*[:,]?\s*(.+)$/i.exec(content)
  const fact = remember?.[1]?.trim() ?? content.trim()
  return {
    owner: "user" as const,
    scope: "project" as const,
    title: fact.slice(0, 120),
    text: fact.slice(0, 2000),
    confidence: 1,
    status: "active" as const,
    evidence: { sessionID },
  }
}

async function applyTurn(sessionDir: string, turn: Turn) {
  const meta = turn.metadata ?? {}
  if (meta.fact) await appendFact(sessionDir, meta.fact)
  if (meta.forget_substring) {
    const needle = String(meta.forget_substring).toLowerCase()
    await setFactStatus(sessionDir, (f) => f.title.toLowerCase().includes(needle) || f.text.toLowerCase().includes(needle), "inactive")
  }
  if (/^(?:please\s+)?remember\b|^memorize\b/i.test(turn.content)) await appendFact(sessionDir, factFromText(turn.content, meta.session_id ?? "trace"))
  if (/^(?:please\s+)?forget\b|^delete\s+memory\b|^remove\s+the\s+memory\b/i.test(turn.content)) {
    const forget =
      /^(?:please\s+)?forget\s+(?:about\s+)?(.+)$/i.exec(turn.content.trim()) ??
      /^delete\s+memory\s+(?:about\s+)?(.+)$/i.exec(turn.content.trim()) ??
      /^remove\s+the\s+memory\s+(?:about\s+)?(.+)$/i.exec(turn.content.trim())
    const needle = forget?.[1]?.trim().toLowerCase()
    if (needle) {
      await setFactStatus(
        sessionDir,
        (f) => f.title.toLowerCase().includes(needle) || f.text.toLowerCase().includes(needle),
        "inactive",
      )
    }
  }
  if (meta.handoff) await writeHandoff(sessionDir, { ...meta.handoff, freshness: meta.handoff.freshness ?? Date.now() })
  if (meta.tool_outcome) await recordToolOutcome(sessionDir, meta.tool_outcome)
  if (meta.observation) await appendObservation(sessionDir, meta.observation)
  if (meta.session_card) await appendSessionCard(sessionDir, meta.session_card)
}

async function readPathOutput(memoryPath: string, sessionDir: string, query?: RecallQuery) {
  if (memoryPath === "fact_lifecycle") return await listActiveFacts(sessionDir, query?.metadata?.limit ?? 80)
  if (memoryPath === "handoff_state") {
    const h = await readHandoff(sessionDir)
    return h ? formatHandoffForPrompt(h, query?.metadata?.maxChars ?? 1200) : ""
  }
  if (memoryPath === "tool_operational_memory") {
    return await readToolLessonsText(sessionDir, query?.metadata?.availableToolIds, query?.metadata?.maxChars ?? 900)
  }
  if (memoryPath === "observations_store") return await listObservations(sessionDir, query?.metadata?.limit ?? 400)
  if (memoryPath === "session_trajectory_archive") return await listSessionCards(sessionDir, query?.metadata?.limit ?? 200)
  if (memoryPath === "e2e") {
    return await buildMemoryContextBlocks({
      sessionDirectory: sessionDir,
      workspaceID: query?.metadata?.workspaceID,
      userPrompt: query?.query ?? "",
      currentSessionID: query?.metadata?.currentSessionID,
      availableToolIds: query?.metadata?.availableToolIds,
      maxTotalChars: query?.metadata?.maxTotalChars,
    })
  }
  throw new Error(`unknown memory_path: ${memoryPath}`)
}

async function writeForHitRate(memoryPath: string, sessionDir: string, input: any) {
  const writes = Array.isArray(input?.writes) ? input.writes : [input]
  for (const write of writes) {
    if (memoryPath === "observations_store") {
      const evidenceHash = write.evidenceHash ?? hashEvidence([write.title, write.l1, ...(write.commands ?? [])])
      await appendObservation(sessionDir, { ...write, evidenceHash })
    } else if (memoryPath === "session_trajectory_archive") {
      if (write.session && write.messages) {
        await persistSessionTrajectory({
          ...write,
          session: { ...write.session, directory: write.session.directory ?? sessionDir },
        })
      }
      else await appendSessionCard(sessionDir, write)
    } else if (memoryPath === "fact_lifecycle") {
      await appendFact(sessionDir, write)
    } else {
      throw new Error(`hit_rate unsupported for path: ${memoryPath}`)
    }
  }
}

async function readForHitRate(memoryPath: string, sessionDir: string, input: any) {
  if (memoryPath === "observations_store") return await listObservations(sessionDir, input?.limit ?? 400)
  if (memoryPath === "session_trajectory_archive") return await listSessionCards(sessionDir, input?.limit ?? 200)
  if (memoryPath === "fact_lifecycle") return await listActiveFacts(sessionDir, input?.limit ?? 80)
  throw new Error(`hit_rate unsupported for path: ${memoryPath}`)
}

async function runQa(trace: Trace, row: RegistryRow, sessionDir: string) {
  for (const turn of trace.turns ?? []) await applyTurn(sessionDir, turn)
  const queryResults = []
  let matched = 0
  let total = 0
  let success = 0
  for (const q of trace.recall_queries ?? []) {
    const output = await readPathOutput(row.memory_path, sessionDir, q)
    const itemResults = q.expected_information.map((expected) => {
      const pass = matches(output, expected)
      if (pass) matched++
      total++
      return { expected, pass }
    })
    if (itemResults.every((r) => r.pass)) success++
    queryResults.push({ query: q.query, output, item_results: itemResults })
  }
  const queryCount = trace.recall_queries?.length ?? 0
  return {
    trace_id: trace.trace_id,
    memory_path: row.memory_path,
    eval_mode: "qa",
    support_coverage: total ? matched / total : 0,
    query_success: queryCount ? success / queryCount : 0,
    query_results: queryResults,
  }
}

async function runHitRate(trace: Trace, row: RegistryRow, sessionDir: string) {
  let hits = 0
  const checkResults = []
  for (const check of trace.checks ?? []) {
    await writeForHitRate(row.memory_path, sessionDir, check.write_input)
    const output = await readForHitRate(row.memory_path, sessionDir, check.read_input)
    const pass = matches(output, check.expected_output)
    if (pass) hits++
    checkResults.push({ description: check.description, output, expected: check.expected_output, pass })
  }
  const total = trace.checks?.length ?? 0
  return {
    trace_id: trace.trace_id,
    memory_path: row.memory_path,
    eval_mode: "hit_rate",
    hit_rate: total ? hits / total : 0,
    check_results: checkResults,
  }
}

async function main() {
  const statePath = path.resolve(argValue("--state", defaultStatePath)!)
  const workDir = path.dirname(statePath)
  const state = unwrapState(await readJson(statePath))
  const onlyPaths = new Set(argList("--paths"))
  const onlyTraces = new Set(argList("--traces"))
  const runRoot = path.join(workDir, "run-data", `run-${Date.now()}`)
  await fs.rm(runRoot, { recursive: true, force: true })
  await fs.mkdir(runRoot, { recursive: true })
  const registry = state.trace_registry as RegistryRow[]
  const selected = registry.filter((row) => {
    if (onlyPaths.size && !onlyPaths.has(row.memory_path)) return false
    if (onlyTraces.size && !onlyTraces.has(row.trace_id)) return false
    return true
  })
  const results = []
  for (const row of selected) {
    const tracePath = path.join(workDir, "traces", `${row.trace_id}.json`)
    const trace = (await readJson(tracePath)) as Trace
    const sessionDir = path.join(runRoot, trace.trace_id)
    await fs.mkdir(sessionDir, { recursive: true })
    const mode = trace.eval_mode ?? "qa"
    results.push(mode === "hit_rate" ? await runHitRate(trace, row, sessionDir) : await runQa(trace, row, sessionDir))
  }
  const out = { run_root: runRoot, results }
  const outputPath = argValue("--output", path.join(workDir, "latest-results.json"))!
  await fs.writeFile(outputPath, JSON.stringify(out, null, 2) + "\n", "utf8")
  console.log(JSON.stringify(out, null, 2))
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
