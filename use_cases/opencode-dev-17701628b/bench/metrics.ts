import fs from "node:fs/promises"
import path from "node:path"

type Json = Record<string, any>

export type SessionMetrics = {
  showcase: string
  condition: string
  session: number
  instanceID: string
  dryRun: boolean
  resolved: boolean | null
  /** Where `resolved` came from: session file vs SWE-bench aggregate JSON. */
  resolvedSource: "session" | "swebench" | "none"
  tokenTotal: number | null
  /** Plan: tokens_sent (prompt / context to model). */
  tokensSent: number | null
  /** Plan: tokens_received (model output + reasoning). */
  tokensReceived: number | null
  inputTokens: number | null
  outputTokens: number | null
  /** Sum of `part.cost` on each `step_finish` (OpenCode CLI export). */
  costTotal: number | null
  /** Number of `step_finish` events — proxy for assistant steps / turns. */
  totalAssistantSteps: number | null
  toolCalls: Record<string, number>
  /** Plan table: bash / grep / read / edit (+ write if used). */
  toolCallsCore: {
    bash: number
    grep: number
    read: number
    edit: number
    write: number
  }
  totalToolCalls: number
  /** tool_use with terminal status error, or bash with non-zero exit when present. */
  failedToolCalls: number
  /**
   * Steps (assistant rounds): count `step_start` until first `read` or `grep` whose args mention an expected file.
   */
  stepsToFirstTouch: number | null
  preference: {
    pytestXvs: boolean
    flake8: boolean
  }
  patchBytes: number
  /** Optional: lines in `.opencode/memory/tool_stats.jsonl` if present (memory / tooling signal). */
  memoryToolStatsLines: number | null
  /** Optional: lines in `.opencode/memory/facts.jsonl` if present. */
  memoryFactsLines: number | null
  logPath: string
  patchPath: string
}

/** Aggregates for plan § measurement (resolve rate, FT, token/tool totals per condition). */
export type ShowcaseEvaluation = {
  showcase: string
  resolveRate: {
    memoryOn: { resolved: number; total: number; rate: number | null }
    memoryOff: { resolved: number; total: number; rate: number | null }
  }
  forwardTransferByTask: Array<{
    session: number
    instanceID: string
    resolvedMemoryOn: boolean | null
    resolvedMemoryOff: boolean | null
    /** (resolved_mem ? 1 : 0) - (resolved_off ? 1 : 0); null if either side unknown. */
    forwardTransfer: number | null
  }>
  /** Mean of `forwardTransfer` over tasks with both sides known. */
  forwardTransferMean: number | null
  tokenTotals: { memoryOn: number | null; memoryOff: number | null }
  toolCallsTotals: { memoryOn: number; memoryOff: number }
}

async function exists(file: string): Promise<boolean> {
  try {
    await fs.access(file)
    return true
  } catch {
    return false
  }
}

async function readJsonl(file: string): Promise<Json[]> {
  if (!(await exists(file))) return []
  const text = await fs.readFile(file, "utf8")
  return text
    .split(/\r?\n/g)
    .map((line) => line.trim())
    .filter(Boolean)
    .flatMap((line) => {
      try {
        return [JSON.parse(line)]
      } catch {
        return []
      }
    })
}

/**
 * OpenCode `opencode run --format json` writes per-step usage on `step_finish` as `part.tokens` / `part.cost`,
 * not `usage` on tool parts. Older/alternate streams may still use `usage` blobs.
 */
function collectTokensAndCost(events: Json[]) {
  let input = 0
  let output = 0
  let reasoning = 0
  let totalFromSteps = 0
  let cost = 0
  for (const event of events) {
    const part = event.part ?? event.properties?.part ?? event
    const usage = part?.usage ?? part?.state?.usage ?? part?.metadata?.usage
    if (usage && typeof usage === "object") {
      input += Number(usage.inputTokens ?? usage.input_tokens ?? usage.promptTokens ?? usage.prompt_tokens ?? 0)
      output += Number(usage.outputTokens ?? usage.output_tokens ?? usage.completionTokens ?? usage.completion_tokens ?? 0)
      continue
    }
    if (event.type === "step_finish" && part?.tokens && typeof part.tokens === "object") {
      const t = part.tokens as Record<string, number>
      input += Number(t.input ?? 0)
      output += Number(t.output ?? 0)
      reasoning += Number(t.reasoning ?? 0)
      totalFromSteps += Number(t.total ?? 0)
    }
    if (event.type === "step_finish" && typeof part?.cost === "number" && !Number.isNaN(part.cost)) {
      cost += part.cost
    }
  }
  const summedLine = input + output + reasoning
  const tokenTotal = totalFromSteps > 0 ? totalFromSteps : summedLine
  return {
    inputTokens: input || null,
    outputTokens: output + reasoning || null,
    tokenTotal: tokenTotal || null,
    costTotal: cost > 0 ? cost : null,
  }
}

function countStepFinishes(events: Json[]): number {
  return events.filter((e) => e.type === "step_finish").length
}

function collectTools(events: Json[]) {
  const tools: Record<string, number> = {}
  const commands: string[] = []
  let failed = 0
  for (const event of events) {
    if (event.type !== "tool_use") continue
    const part = event.part
    const tool = String(part?.tool ?? "unknown")
    tools[tool] = (tools[tool] ?? 0) + 1
    const st = part?.state?.status
    const exit = part?.state?.metadata?.exit ?? part?.metadata?.output?.exit
    const bashFailed = tool === "bash" && typeof exit === "number" && exit !== 0
    if (st === "error" || bashFailed) failed += 1
    if (tool === "bash") {
      const command = part?.state?.input?.command ?? part?.state?.metadata?.command
      if (typeof command === "string") commands.push(command)
    }
  }
  return { tools, commands, failedToolCalls: failed }
}

/**
 * First touch: first `read` or `grep` that references a path in `expectedFiles` (session-meta).
 */
function stepsToFirstTouch(events: Json[], expectedFiles: string[]) {
  if (expectedFiles.length === 0) return null
  let steps = 0
  for (const event of events) {
    if (event.type === "step_start") steps++
    if (event.type !== "tool_use") continue
    const part = event.part
    const tool = String(part?.tool ?? "")
    if (tool !== "read" && tool !== "grep") continue
    const text = JSON.stringify(part ?? event)
    if (expectedFiles.some((file) => text.includes(file))) return Math.max(steps, 1)
  }
  return null
}

async function loadExpectedFiles(sessionDir: string): Promise<string[]> {
  const file = path.join(sessionDir, "session-meta.json")
  if (!(await exists(file))) return []
  const meta = JSON.parse(await fs.readFile(file, "utf8"))
  return Array.isArray(meta.expected_files) ? meta.expected_files : []
}

async function loadResolvedFromSwebench(benchDir: string, condition: string, instanceId: string): Promise<boolean | null> {
  const file = path.join(benchDir, "swebench-verify", `opencode-memory-eval-${condition}.opencode-memory-eval-${condition}.json`)
  if (!(await exists(file))) return null
  try {
    const j = JSON.parse(await fs.readFile(file, "utf8"))
    const r = new Set<string>((j.resolved_ids ?? []).map(String))
    const u = new Set<string>((j.unresolved_ids ?? []).map(String))
    const sub = new Set<string>((j.submitted_ids ?? []).map(String))
    if (r.has(instanceId)) return true
    if (u.has(instanceId)) return false
    if (sub.has(instanceId)) return false
  } catch {
    return null
  }
  return null
}

function toolCallsCoreFrom(tools: Record<string, number>) {
  const n = (k: string) => Number(tools[k] ?? 0)
  return {
    bash: n("bash"),
    grep: n("grep"),
    read: n("read"),
    edit: n("edit"),
    write: n("write"),
  }
}

async function countJsonlLines(file: string): Promise<number | null> {
  if (!(await exists(file))) return null
  const text = await fs.readFile(file, "utf8")
  const n = text.split(/\r?\n/g).filter((l) => l.trim()).length
  return n
}

async function countToolStatsArtifacts(memDir: string): Promise<number | null> {
  const jsonl = await countJsonlLines(path.join(memDir, "tool_stats.jsonl"))
  if (jsonl !== null) return jsonl
  const jp = path.join(memDir, "tool_stats.json")
  if (!(await exists(jp))) return null
  try {
    const j = JSON.parse(await fs.readFile(jp, "utf8"))
    if (Array.isArray(j)) return j.length
    if (j && typeof j === "object") return Object.keys(j as object).length
    return 1
  } catch {
    return null
  }
}

async function countFactsArtifacts(memDir: string): Promise<number | null> {
  const jsonl = await countJsonlLines(path.join(memDir, "facts.jsonl"))
  if (jsonl !== null) return jsonl
  const jp = path.join(memDir, "facts.json")
  if (!(await exists(jp))) return null
  try {
    const j = JSON.parse(await fs.readFile(jp, "utf8"))
    if (Array.isArray(j)) return j.length
    if (j && typeof j === "object") return Object.keys(j as object).length
    return 1
  } catch {
    return null
  }
}

async function loadMemoryArtifactCounts(sessionDir: string) {
  /** Harness links `repo/.opencode/memory` → `runs/<condition>/shared-memory`. */
  const bases = [path.join(sessionDir, "repo", ".opencode", "memory"), path.join(sessionDir, ".opencode", "memory")]
  for (const base of bases) {
    if (!(await exists(base))) continue
    const toolStatsLines = await countToolStatsArtifacts(base)
    const factsLines = await countFactsArtifacts(base)
    if (toolStatsLines !== null || factsLines !== null) {
      return { toolStatsLines, factsLines }
    }
  }
  return { toolStatsLines: null, factsLines: null }
}

export async function collectSessionMetrics(input: {
  benchDir: string
  showcase: string
  condition: string
  session: number
}): Promise<SessionMetrics> {
  const sessionDir = path.join(input.benchDir, input.showcase, "runs", input.condition, `session-${input.session}`)
  const logPath = path.join(sessionDir, "session-log.jsonl")
  const patchPath = path.join(sessionDir, "patch.diff")
  const metaPath = path.join(sessionDir, "session-meta.json")
  const events = await readJsonl(logPath)
  const expectedFiles = await loadExpectedFiles(sessionDir)
  const { inputTokens, outputTokens, tokenTotal, costTotal } = collectTokensAndCost(events)
  const totalAssistantSteps = events.length === 0 ? null : countStepFinishes(events)
  const { tools, commands, failedToolCalls } = collectTools(events)
  const toolCallsCore = toolCallsCoreFrom(tools)
  const patchBytes = (await exists(patchPath)) ? (await fs.stat(patchPath)).size : 0
  const meta = (await exists(metaPath)) ? JSON.parse(await fs.readFile(metaPath, "utf8")) : {}
  const instanceID = String(meta.instance_id ?? "unknown")
  const resolvedPath = path.join(sessionDir, "resolved.json")
  let resolved: boolean | null = null
  let resolvedSource: SessionMetrics["resolvedSource"] = "none"
  if (await exists(resolvedPath)) {
    resolved = Boolean(JSON.parse(await fs.readFile(resolvedPath, "utf8")).resolved)
    resolvedSource = "session"
  } else {
    const fromBench = await loadResolvedFromSwebench(input.benchDir, input.condition, instanceID)
    if (fromBench !== null) {
      resolved = fromBench
      resolvedSource = "swebench"
    }
  }
  const { toolStatsLines, factsLines } = await loadMemoryArtifactCounts(sessionDir)

  return {
    showcase: input.showcase,
    condition: input.condition,
    session: input.session,
    instanceID,
    dryRun: Boolean(meta.dry_run),
    resolved,
    resolvedSource,
    tokenTotal,
    tokensSent: inputTokens,
    tokensReceived: outputTokens,
    inputTokens,
    outputTokens,
    costTotal,
    totalAssistantSteps,
    toolCalls: tools,
    toolCallsCore,
    totalToolCalls: Object.values(tools).reduce((sum, n) => sum + n, 0),
    failedToolCalls,
    stepsToFirstTouch: stepsToFirstTouch(events, expectedFiles),
    preference: {
      pytestXvs: commands.some((cmd) => cmd.includes("pytest -xvs") || cmd.includes("python -m pytest -xvs")),
      flake8: commands.some((cmd) => /(^|\s)flake8(\s|$)/.test(cmd)),
    },
    patchBytes,
    memoryToolStatsLines: toolStatsLines,
    memoryFactsLines: factsLines,
    logPath: path.relative(input.benchDir, logPath),
    patchPath: path.relative(input.benchDir, patchPath),
  }
}

function table(rows: SessionMetrics[]) {
  const header =
    "| Session | Task | Condition | Resolved | src | DryRun | Tokens | Cost | core b/g/r/e/w | touch | steps | xvs | flake8 |"
  const sep = "|---:|---|---|---|---|---:|---:|---|:---:|---:|---:|---|---|"
  const body = rows
    .map((r) => {
      const tc = r.toolCallsCore
      const core = `${tc.bash}/${tc.grep}/${tc.read}/${tc.edit}/${tc.write}`
      const tok = r.tokenTotal != null ? String(r.tokenTotal) : "n/a"
      return `| ${r.session} | ${r.instanceID} | ${r.condition} | ${r.resolved ?? "n/a"} | ${r.resolvedSource} | ${r.dryRun} | ${tok} | ${r.costTotal ?? "n/a"} | ${core} | ${r.stepsToFirstTouch ?? "n/a"} | ${r.totalAssistantSteps ?? "n/a"} | ${r.preference.pytestXvs} | ${r.preference.flake8} |`
    })
    .join("\n")
  return [header, sep, body].join("\n")
}

function evaluationTable(ev: ShowcaseEvaluation) {
  const { resolveRate: rr, forwardTransferByTask, forwardTransferMean, tokenTotals, toolCallsTotals } = ev
  const lines = [
    "## Plan metrics summary",
    "",
    "### Resolve rate (SWE-bench docker, from `resolved` / `swebench-verify` JSON)",
    "",
    "| Condition | Resolved | Known | Rate |",
    "|---|---:|---:|---:|",
    `| memory-off | ${rr.memoryOff.resolved} | ${rr.memoryOff.total} | ${rr.memoryOff.rate != null ? rr.memoryOff.rate.toFixed(3) : "n/a"} |`,
    `| memory-on | ${rr.memoryOn.resolved} | ${rr.memoryOn.total} | ${rr.memoryOn.rate != null ? rr.memoryOn.rate.toFixed(3) : "n/a"} |`,
    "",
    "### Total tokens & tool calls (all sessions in showcase)",
    "",
    "| Condition | Σ tokens | Σ tool calls |",
    "|---|---:|---:|",
    `| memory-off | ${tokenTotals.memoryOff ?? "n/a"} | ${toolCallsTotals.memoryOff} |`,
    `| memory-on | ${tokenTotals.memoryOn ?? "n/a"} | ${toolCallsTotals.memoryOn} |`,
    "",
    "### Forward transfer per task: FT = (resolve_memory_on ? 1 : 0) − (resolve_memory_off ? 1 : 0)",
    "",
    "| Session | instance_id | mem-on | mem-off | FT |",
    "|---:|---|---|:---:|---:|",
    ...forwardTransferByTask.map(
      (t) =>
        `| ${t.session} | ${t.instanceID} | ${t.resolvedMemoryOn ?? "n/a"} | ${t.resolvedMemoryOff ?? "n/a"} | ${t.forwardTransfer ?? "n/a"} |`,
    ),
    "",
    `**Mean FT** (tasks with both resolves known): ${forwardTransferMean != null ? forwardTransferMean.toFixed(3) : "n/a"}`,
    "",
  ]
  return lines.join("\n")
}

export function buildShowcaseEvaluation(showcase: string, rows: SessionMetrics[]): ShowcaseEvaluation {
  const bySession = new Map<number, { on?: SessionMetrics; off?: SessionMetrics }>()
  for (const r of rows) {
    if (r.showcase !== showcase) continue
    const cur = bySession.get(r.session) ?? {}
    if (r.condition === "memory-on") cur.on = r
    if (r.condition === "memory-off") cur.off = r
    bySession.set(r.session, cur)
  }

  const forwardTransferByTask: ShowcaseEvaluation["forwardTransferByTask"] = []
  const ftVals: number[] = []
  for (const session of [...bySession.keys()].sort((a, b) => a - b)) {
    const pair = bySession.get(session)!
    const on = pair.on
    const off = pair.off
    const instanceID = on?.instanceID ?? off?.instanceID ?? "unknown"
    const ro = on?.resolved ?? null
    const rf = off?.resolved ?? null
    let ft: number | null = null
    if (ro !== null && rf !== null) {
      ft = (ro ? 1 : 0) - (rf ? 1 : 0)
      ftVals.push(ft)
    }
    forwardTransferByTask.push({
      session,
      instanceID,
      resolvedMemoryOn: ro,
      resolvedMemoryOff: rf,
      forwardTransfer: ft,
    })
  }

  function resolveStats(cond: "memory-on" | "memory-off") {
    const subset = rows.filter((r) => r.showcase === showcase && r.condition === cond)
    const known = subset.filter((r) => r.resolved !== null)
    const resolved = known.filter((r) => r.resolved === true).length
    return {
      resolved,
      total: known.length,
      rate: known.length ? resolved / known.length : null,
    }
  }

  function sumTokens(cond: "memory-on" | "memory-off") {
    const subset = rows.filter((r) => r.showcase === showcase && r.condition === cond)
    const nums = subset.map((r) => r.tokenTotal).filter((t): t is number => t !== null)
    if (nums.length === 0) return null
    return nums.reduce((a, b) => a + b, 0)
  }

  function sumTools(cond: "memory-on" | "memory-off") {
    return rows.filter((r) => r.showcase === showcase && r.condition === cond).reduce((s, r) => s + r.totalToolCalls, 0)
  }

  return {
    showcase,
    resolveRate: {
      memoryOn: resolveStats("memory-on"),
      memoryOff: resolveStats("memory-off"),
    },
    forwardTransferByTask,
    forwardTransferMean: ftVals.length ? ftVals.reduce((a, b) => a + b, 0) / ftVals.length : null,
    tokenTotals: {
      memoryOn: sumTokens("memory-on"),
      memoryOff: sumTokens("memory-off"),
    },
    toolCallsTotals: {
      memoryOn: sumTools("memory-on"),
      memoryOff: sumTools("memory-off"),
    },
  }
}

/** Reads `bench/<showcase>/tasks.json` and returns `sessions.length` when present. */
async function sessionCountFromTasksFile(benchDir: string, showcase: string): Promise<number | null> {
  try {
    const raw = await fs.readFile(path.join(benchDir, showcase, "tasks.json"), "utf8")
    const data = JSON.parse(raw) as { sessions?: unknown[] }
    if (Array.isArray(data.sessions) && data.sessions.length > 0) return data.sessions.length
  } catch {
    /* missing or invalid */
  }
  return null
}

export async function writeShowcaseReport(benchDir: string, showcase: string, sessionCount: number) {
  const rows: SessionMetrics[] = []
  for (const condition of ["memory-off", "memory-on"]) {
    for (let session = 1; session <= sessionCount; session++) {
      rows.push(await collectSessionMetrics({ benchDir, showcase, condition, session }))
    }
  }
  const resultsDir = path.join(benchDir, showcase, "results")
  await fs.mkdir(resultsDir, { recursive: true })
  await fs.writeFile(path.join(resultsDir, "metrics.json"), JSON.stringify(rows, null, 2), "utf8")
  const evaluation = buildShowcaseEvaluation(showcase, rows)
  await fs.writeFile(path.join(resultsDir, "evaluation.json"), JSON.stringify(evaluation, null, 2), "utf8")
  const report = [
    `# ${showcase} Results`,
    "",
    "Per-session rows use **`tokensSent` + `tokensReceived`** (= stored `inputTokens` + `outputTokens`) as Total Tokens; **`stepsToFirstTouch`** counts assistant rounds until first **read/grep** touching an expected file from `session-meta.json`.",
    "",
    table(rows),
    "",
    evaluationTable(evaluation),
    "## Notes",
    "",
    "- **Resolve rate**: `resolved` is read from `session-*/resolved.json` if present, else from `bench/swebench-verify/opencode-memory-eval-<condition>.opencode-memory-eval-<condition>.json` (`resolved_ids` / `unresolved_ids`).",
    "- **Forward transfer**: per-task difference of resolve flags; mean taken over tasks where both conditions have a known outcome.",
    "- **Memory artifacts**: `memoryToolStatsLines` / `memoryFactsLines` count lines in shared `.opencode/memory/` when those files exist (tool experience / facts signal).",
    "- Showcase 2 additionally uses preference booleans (`pytest -xvs`, `flake8`) in `preference`.",
  ].join("\n")
  await fs.writeFile(path.join(resultsDir, "report.md"), report, "utf8")
  return rows
}

if (import.meta.main) {
  const args = new Map<string, string>()
  for (let i = 2; i < process.argv.length; i += 2) {
    const k = process.argv[i]
    const v = process.argv[i + 1]
    if (k !== undefined && v !== undefined) args.set(k, v)
  }
  const benchDir = args.get("--bench-dir") ?? path.resolve(import.meta.dir)
  const showcase = args.get("--showcase") ?? "showcase1"
  const sessionsArg = args.get("--sessions")
  let sessionCount: number
  if (sessionsArg !== undefined && sessionsArg !== "") {
    sessionCount = Number(sessionsArg)
    if (Number.isNaN(sessionCount)) {
      console.error("Invalid --sessions value")
      process.exit(1)
    }
  } else {
    sessionCount =
      (await sessionCountFromTasksFile(benchDir, showcase)) ?? (showcase === "showcase1" ? 6 : 3)
  }
  await writeShowcaseReport(benchDir, showcase, sessionCount)
}
