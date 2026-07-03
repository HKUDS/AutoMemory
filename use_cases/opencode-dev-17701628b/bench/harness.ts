#!/usr/bin/env bun
import fs from "node:fs/promises"
import fsSync from "node:fs"
import path from "node:path"
import { spawn, spawnSync } from "node:child_process"
import { writeShowcaseReport } from "./metrics"

type Condition = "memory-on" | "memory-off"
type ShowcaseName = "showcase1" | "showcase2"

const MEMORY_FINALIZE_MODES = ["none", "summarize", "archive", "summarize-archive"] as const
type MemoryFinalize = (typeof MEMORY_FINALIZE_MODES)[number]

type Showcase1Session = {
  order: number
  instance_id: string
  difficulty: string
  focus: string
  expected_files: string[]
  prompt: string
}

type Showcase2Session = {
  order: number
  task: string
  prompt_file: string
  goal: string
  expected_files: string[]
  should_verify: boolean
}

type Instance = {
  instance_id: string
  repo: string
  base_commit: string
  problem_statement: string
  FAIL_TO_PASS?: string[] | string
  PASS_TO_PASS?: string[] | string
}

type RunOptions = {
  benchDir: string
  repoRoot: string
  showcase: ShowcaseName | "all"
  condition: Condition | "all"
  execute: boolean
  prepareWorkspaces: boolean
  verify: boolean
  /** Skip agent runs; only write predictions + SWE-bench verify (needs patches already on disk). */
  verifyOnly: boolean
  force: boolean
  model?: string
  agent?: string
  /**
   * Opt-in native consolidation after each successful memory-on session (`none` = unchanged bench behavior).
   */
  memoryFinalize: MemoryFinalize
}

type BenchPythonEnv = {
  path: string
  bin: string
  python: string
  sourcePython: string
  sourcePythonVersion: string
  prepared: boolean
}

const benchStartedAt = Date.now()

function formatDuration(ms: number) {
  const seconds = Math.floor(ms / 1000)
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  if (minutes === 0) return `${rest}s`
  return `${minutes}m${rest.toString().padStart(2, "0")}s`
}

function progress(message: string, details?: Record<string, string | number | boolean | undefined>) {
  const attrs = Object.entries(details ?? {})
    .filter(([, value]) => value !== undefined)
    .map(([key, value]) => `${key}=${value}`)
  const suffix = attrs.length ? ` (${attrs.join(", ")})` : ""
  console.log(`[bench +${formatDuration(Date.now() - benchStartedAt)}] ${message}${suffix}`)
}

function parseArgs(): RunOptions {
  const args = new Map<string, string | boolean>()
  for (let i = 2; i < process.argv.length; i++) {
    const key = process.argv[i]
    if (!key?.startsWith("--")) continue
    const next = process.argv[i + 1]
    if (!next || next.startsWith("--")) args.set(key, true)
    else {
      args.set(key, next)
      i++
    }
  }
  const benchDir = path.resolve(String(args.get("--bench-dir") ?? import.meta.dir))
  const flag = (name: string, fallback = false) => {
    const value = args.get(name)
    if (value === undefined) return fallback
    if (value === true) return true
    return !["0", "false", "no", "off"].includes(String(value).toLowerCase())
  }
  const memoryFinalizeRaw = String(args.get("--memory-finalize") ?? "none")
  if (!MEMORY_FINALIZE_MODES.includes(memoryFinalizeRaw as MemoryFinalize)) {
    throw new Error(
      `Invalid --memory-finalize: ${memoryFinalizeRaw}. Use one of: ${MEMORY_FINALIZE_MODES.join(", ")}`,
    )
  }
  return {
    benchDir,
    repoRoot: path.resolve(benchDir, ".."),
    showcase: String(args.get("--showcase") ?? "all") as RunOptions["showcase"],
    condition: String(args.get("--condition") ?? "all") as RunOptions["condition"],
    execute: flag("--execute"),
    prepareWorkspaces: args.has("--prepare-workspaces") ? flag("--prepare-workspaces") : true,
    verify: flag("--verify"),
    verifyOnly: flag("--verify-only"),
    force: flag("--force"),
    model: typeof args.get("--model") === "string" ? String(args.get("--model")) : undefined,
    agent: typeof args.get("--agent") === "string" ? String(args.get("--agent")) : undefined,
    memoryFinalize: memoryFinalizeRaw as MemoryFinalize,
  }
}

async function exists(file: string) {
  try {
    await fs.access(file)
    return true
  } catch {
    return false
  }
}

function venvPaths(repoDir: string) {
  const venv = path.join(repoDir, ".bench-venv")
  const bin = path.join(venv, "bin")
  return {
    path: venv,
    bin,
    python: path.join(bin, "python"),
  }
}

/** Env for OpenCode so Python/bash steps resolve packages from this session's repo and prepared venv. */
async function benchOpencodeEnv(repoDir: string): Promise<NodeJS.ProcessEnv> {
  const env: NodeJS.ProcessEnv = { ...process.env }
  env.PYTHONNOUSERSITE = "1"
  env.PIP_DISABLE_PIP_VERSION_CHECK = "1"
  const venv = venvPaths(repoDir)
  if (await exists(venv.python)) {
    env.VIRTUAL_ENV = venv.path
    env.PATH = `${venv.bin}${path.delimiter}${env.PATH ?? ""}`
  }
  delete env.PYTHONPATH
  const pytestSrc = path.join(repoDir, "src", "_pytest")
  const djangoPkg = path.join(repoDir, "django")
  if (await exists(pytestSrc)) {
    env.PYTHONPATH = path.join(repoDir, "src")
  } else if (await exists(djangoPkg)) {
    env.PYTHONPATH = repoDir
  }
  return env
}

async function readJson<T>(file: string): Promise<T> {
  return JSON.parse(await fs.readFile(file, "utf8")) as T
}

async function writeJson(file: string, value: unknown) {
  await fs.mkdir(path.dirname(file), { recursive: true })
  await fs.writeFile(file, JSON.stringify(value, null, 2) + "\n", "utf8")
}

function runChecked(
  command: string,
  args: string[],
  cwd: string,
  opts?: { allowFailure?: boolean; env?: NodeJS.ProcessEnv; timeoutMs?: number },
) {
  const res = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    env: opts?.env ?? process.env,
    timeout: opts?.timeoutMs,
  })
  if (res.status !== 0 && !opts?.allowFailure) {
    const detail = res.error ? ` (${res.error.message})` : res.signal ? ` (signal ${res.signal})` : ""
    throw new Error(`${command} ${args.join(" ")} failed with ${res.status}${detail}`)
  }
  return res.status ?? 0
}

function pythonVersion(python: string): string {
  const res = spawnSync(python, ["-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"], {
    encoding: "utf8",
    env: process.env,
  })
  return res.status === 0 ? (res.stdout ?? "").trim() : "unknown"
}

function resolveBenchPython() {
  const override = process.env.OPENCODE_BENCH_PYTHON?.trim()
  const candidates = override ? [override] : ["python3.10", "python3", "python"]
  for (const exe of candidates) {
    const res = spawnSync(exe, ["-c", "import sys; print(sys.executable)"], { encoding: "utf8", env: process.env })
    if (res.status === 0) return (res.stdout ?? "").trim() || exe
  }
  if (override) throw new Error(`OPENCODE_BENCH_PYTHON was set but is not executable: ${override}`)
  throw new Error("Could not find a Python executable for bench venv setup. Set OPENCODE_BENCH_PYTHON.")
}

function venvEnv(repoDir: string): NodeJS.ProcessEnv {
  const venv = venvPaths(repoDir)
  const env: NodeJS.ProcessEnv = { ...process.env }
  env.VIRTUAL_ENV = venv.path
  env.PATH = `${venv.bin}${path.delimiter}${env.PATH ?? ""}`
  env.PYTHONNOUSERSITE = "1"
  env.PIP_DISABLE_PIP_VERSION_CHECK = "1"
  delete env.PYTHONPATH
  return env
}

async function preparePythonEnvironment(repoDir: string, instance: Instance): Promise<BenchPythonEnv> {
  const venv = venvPaths(repoDir)
  const markerPath = path.join(venv.path, ".bench-prepared.json")
  if (await exists(markerPath)) {
    const marker = await readJson<BenchPythonEnv>(markerPath)
    if (await exists(marker.python)) {
      const pkgResources = spawnSync(marker.python, ["-c", "import pkg_resources"], { cwd: repoDir, env: venvEnv(repoDir) })
      if (pkgResources.status === 0) return marker
      progress("bench venv missing pkg_resources; repairing", { instance: instance.instance_id, venv: venv.path })
    }
  }

  const sourcePython = resolveBenchPython()
  progress("preparing Python bench venv", {
    instance: instance.instance_id,
    python: sourcePython,
    venv: venv.path,
  })
  if (!(await exists(venv.python))) {
    runChecked(sourcePython, ["-m", "venv", venv.path], repoDir)
  }
  const env = venvEnv(repoDir)
  // Old SWE-bench projects can import pkg_resources during pytest startup. Setuptools 81+
  // removed that module, so keep the bench venv on a compatible setuptools release.
  runChecked(venv.python, ["-m", "pip", "install", "--upgrade", "pip", "setuptools<81", "wheel"], repoDir, { env })
  runChecked(venv.python, ["-m", "pip", "install", "pytest"], repoDir, { env })
  runChecked(venv.python, ["-c", "import pkg_resources"], repoDir, { env })
  if (await exists(path.join(repoDir, "pyproject.toml")) || await exists(path.join(repoDir, "setup.py"))) {
    runChecked(venv.python, ["-m", "pip", "install", "-e", "."], repoDir, { env })
  }
  const metadata: BenchPythonEnv = {
    ...venv,
    sourcePython,
    sourcePythonVersion: pythonVersion(venv.python),
    prepared: true,
  }
  await writeJson(markerPath, metadata)
  return metadata
}

/**
 * SWE-bench 3.x uses PEP 604 annotations (`X | Y`); Python < 3.10 crashes at import time.
 * Prefer OPENCODE_SWEBENCH_PYTHON, then any interpreter that is both >= 3.10 and has `swebench`.
 */
function pythonForSwebench(benchDir: string): string {
  const override = process.env.OPENCODE_SWEBENCH_PYTHON?.trim()
  if (override) return override
  const script =
    "import sys\nif sys.version_info < (3, 10):\n  raise SystemExit(1)\nimport swebench\nraise SystemExit(0)\n"
  const localVenv = path.join(benchDir, "swebench-verify", ".venv", "bin", "python")
  for (const exe of [localVenv, "python3.13", "python3.12", "python3.11", "python3.10", "python3"]) {
    const res = spawnSync(exe, ["-c", script], { encoding: "utf8", env: process.env })
    if (res.status === 0) return exe
  }
  throw new Error(
    "SWE-bench verification needs Python >= 3.10 with package `swebench` installed (current `python3` may be 3.9). " +
      "Install e.g. `python3.10 -m pip install --user swebench` or set OPENCODE_SWEBENCH_PYTHON to that interpreter.",
  )
}

function unique<T>(items: T[]) {
  return [...new Set(items)]
}

/** One temp tree per process when `execute=false`; real `runs/` stays untouched. */
let dryRunStagingRoot: string | undefined

async function ensureDryRunStagingRoot(benchDir: string): Promise<string> {
  if (dryRunStagingRoot) return dryRunStagingRoot
  await fs.mkdir(path.join(benchDir, ".dryrun"), { recursive: true })
  dryRunStagingRoot = await fs.mkdtemp(path.join(benchDir, ".dryrun", "run-"))
  progress("dry-run staging directory", { path: dryRunStagingRoot })
  return dryRunStagingRoot
}

async function ensureInstances(benchDir: string, ids: string[]) {
  const out = path.join(benchDir, "data", "instances.json")
  let current: Record<string, Instance> = {}
  if (await exists(out)) {
    current = await readJson<Record<string, Instance>>(out)
    if (ids.every((id) => current[id])) return current
  }
  const missing = ids.filter((id) => !current[id])
  const fetchOut = path.join(benchDir, "data", "instances.fetch.json")
  const script = path.join(benchDir, "scripts", "fetch_instances.py")
  const status = runChecked(
    "python3",
    [script, "--ids", ...missing, "--out", fetchOut],
    benchDir,
    { allowFailure: true },
  )
  if (status !== 0) {
    const fallback: Record<string, Instance> = {}
    for (const id of missing) {
      fallback[id] = {
        instance_id: id,
        repo: id.startsWith("pytest-dev__pytest") ? "pytest-dev/pytest" : "django/django",
        base_commit: "UNKNOWN_BASE_COMMIT",
        problem_statement: `Dataset metadata for ${id} could not be fetched. Re-run after HuggingFace access is available.`,
      }
    }
    current = { ...current, ...fallback }
    await writeJson(out, current)
    return current
  }
  const fetched = await readJson<Record<string, Instance>>(fetchOut)
  current = { ...current, ...fetched }
  await writeJson(out, current)
  await fs.rm(fetchOut, { force: true })
  return current
}

function renderPrompt(template: string, instance: Instance) {
  return withEnvironmentRules(
    template
    .replaceAll("{problem_statement}", instance.problem_statement ?? "")
    .replaceAll("{base_commit}", instance.base_commit ?? "")
      .replaceAll("{instance_id}", instance.instance_id),
  )
}

const environmentRules = [
  "Environment rules:",
  "Do not install system packages.",
  "Do not modify Python/conda/venv environment.",
  "Do not run pip install unless explicitly required by the task.",
  "The repository environment is already prepared.",
  "When running Python or tests from a shell, use the prepared interpreter only: from the repo root run `./.bench-venv/bin/python` or `./.bench-venv/bin/python -m pytest` (never rely on bare `python`, `python3`, or `pytest` on PATH — conda or system Python may take precedence).",
  "Focus only on fixing the issue.",
].join("\n")

function withEnvironmentRules(prompt: string) {
  return `${prompt.trimEnd()}\n\n${environmentRules}\n`
}

function gitPorcelain(repoDir: string): string {
  const res = spawnSync("git", ["status", "--porcelain"], { cwd: repoDir, encoding: "utf8" })
  return (res.stdout ?? "").trim()
}

/** Only clears when the directory exists and has at least one entry (previous run left files). */
async function clearDirectoryContentsIfResidual(dir: string) {
  if (!(await exists(dir))) return 0
  const entries = await fs.readdir(dir)
  if (entries.length === 0) return 0
  await Promise.all(entries.map((name) => fs.rm(path.join(dir, name), { recursive: true, force: true })))
  return entries.length
}

/**
 * memory-on: shared store for all sessions under this showcase+condition.
 * Clear once per harness invocation of this (showcase, condition), only if non-empty; does not touch other showcases.
 */
async function clearSharedMemoryIfResidual(benchDir: string, showcase: ShowcaseName, condition: Condition) {
  if (condition !== "memory-on") return 0
  return clearDirectoryContentsIfResidual(path.join(benchDir, showcase, "runs", condition, "shared-memory"))
}

/** memory-off: isolated `.opencode/memory` per session repo; clear only when it has leftover files. */
async function clearLocalMemoryOffIfResidual(repoDir: string, condition: Condition) {
  if (condition !== "memory-off") return 0
  return clearDirectoryContentsIfResidual(path.join(repoDir, ".opencode", "memory"))
}

/**
 * If the cloned repo still has local changes from a previous bench run, reset to the task base commit.
 * Skips when clean or when there is no git checkout (placeholder workspace).
 */
async function resetGitWorkspaceIfResidual(repoDir: string, baseCommit: string | undefined) {
  if (!baseCommit || baseCommit === "UNKNOWN_BASE_COMMIT") return false
  if (!(await exists(path.join(repoDir, ".git")))) return false
  if (!gitPorcelain(repoDir)) return false
  runChecked("git", ["reset", "--quiet", "--hard", baseCommit], repoDir)
  runChecked("git", ["clean", "-fd"], repoDir)
  return true
}

async function copyConfig(benchDir: string, condition: Condition, workdir: string) {
  const source = path.join(benchDir, "config", `${condition}.jsonc`)
  await fs.copyFile(source, path.join(workdir, "opencode.jsonc"))
}

async function linkSharedMemory(benchDir: string, showcase: string, condition: Condition, workdir: string) {
  const opencodeDir = path.join(workdir, ".opencode")
  const memoryPath = path.join(opencodeDir, "memory")
  await fs.mkdir(opencodeDir, { recursive: true })
  if (condition === "memory-off") {
    if (!(await exists(memoryPath))) await fs.mkdir(memoryPath, { recursive: true })
    return
  }
  const shared = path.join(benchDir, showcase, "runs", condition, "shared-memory")
  await fs.mkdir(shared, { recursive: true })
  if (await exists(memoryPath)) return
  await fs.symlink(shared, memoryPath, "dir")
}

async function prepareRepo(instance: Instance, workdir: string, force: boolean) {
  const repoDir = path.join(workdir, "repo")
  if (await exists(repoDir)) {
    if (!force) return repoDir
    progress("removing existing workspace", { repoDir })
    await fs.rm(repoDir, { recursive: true, force: true })
  }
  if (!instance.base_commit || instance.base_commit === "UNKNOWN_BASE_COMMIT") {
    await fs.mkdir(repoDir, { recursive: true })
    await fs.writeFile(path.join(repoDir, "README.dataset-missing.md"), `Dataset metadata for ${instance.instance_id} was unavailable.\n`, "utf8")
    return repoDir
  }
  const url = `https://github.com/${instance.repo}.git`
  progress("cloning workspace", { repo: instance.repo, repoDir })
  runChecked("git", ["clone", "--quiet", url, repoDir], workdir)
  progress("checking out base commit", { instance: instance.instance_id, baseCommit: instance.base_commit.slice(0, 12) })
  runChecked("git", ["checkout", "--quiet", instance.base_commit], repoDir)
  return repoDir
}

async function gitDiff(repoDir: string, patchPath: string) {
  const res = spawnSync("git", ["diff", "--binary"], { cwd: repoDir, encoding: "utf8" })
  await fs.writeFile(patchPath, res.stdout ?? "", "utf8")
}

async function runOpencode(input: {
  repoRoot: string
  repoDir: string
  prompt: string
  logPath: string
  model?: string
  agent?: string
}) {
  const env = await benchOpencodeEnv(input.repoDir)
  await new Promise<void>((resolve, reject) => {
    const args = [
      "run",
      "--format",
      "json",
      "--dangerously-skip-permissions",
      "--dir",
      input.repoDir,
    ]
    if (input.model) args.push("--model", input.model)
    if (input.agent) args.push("--agent", input.agent)
    const child = spawn("bun", ["run", "--cwd", "packages/opencode", "--conditions=browser", "src/index.ts", ...args], {
      cwd: input.repoRoot,
      stdio: ["pipe", "pipe", "pipe"],
      env,
    })
    const out = fsSync.createWriteStream(input.logPath, { flags: "w" })
    child.stdout.pipe(out)
    child.stderr.on("data", (chunk) => out.write(JSON.stringify({ type: "stderr", text: String(chunk), timestamp: Date.now() }) + "\n"))
    child.on("error", reject)
    child.on("close", (code) => {
      out.end()
      if (code === 0) resolve()
      else reject(new Error(`opencode exited with ${code}`))
    })
    child.stdin.end(input.prompt)
  })
}

async function dryRunLog(logPath: string, prompt: string) {
  const events = [
    { type: "dry_run", timestamp: Date.now(), message: "Model execution skipped. Re-run harness with --execute." },
    { type: "prompt", timestamp: Date.now(), prompt },
  ]
  await fs.writeFile(logPath, events.map((event) => JSON.stringify(event)).join("\n") + "\n", "utf8")
}

/** True when OpenCode logged a fatal provider outage after any internal retries (e.g. 502 / provider_unavailable). */
function errorEventIndicatesProviderUnavailable(ev: Record<string, unknown>): boolean {
  if (ev.type !== "error") return false
  const err = ev.error as Record<string, unknown> | undefined
  if (!err || typeof err !== "object") return false
  const data = err.data as Record<string, unknown> | undefined
  if (!data || typeof data !== "object") return false
  const meta = data.metadata as Record<string, unknown> | undefined
  if (meta?.error_type === "provider_unavailable") return true
  const msg = data.message
  if (typeof msg === "string") {
    try {
      const inner = JSON.parse(msg) as Record<string, unknown>
      const im = inner.metadata as Record<string, unknown> | undefined
      if (im?.error_type === "provider_unavailable") return true
    } catch {
      /* message may be plain text */
    }
  }
  return false
}

function sessionLogIndicatesProviderUnavailable(content: string): boolean {
  for (const line of content.split("\n")) {
    const t = line.trim()
    if (!t) continue
    let ev: Record<string, unknown>
    try {
      ev = JSON.parse(t) as Record<string, unknown>
    } catch {
      continue
    }
    if (ev.type === "stderr") continue
    if (errorEventIndicatesProviderUnavailable(ev)) return true
  }
  return false
}

/** First non-empty `sessionID` in JSONL (skips `type: stderr` lines). */
function parseSessionIdFromSessionLogContent(content: string): string | undefined {
  for (const line of content.split("\n")) {
    const t = line.trim()
    if (!t) continue
    let ev: Record<string, unknown>
    try {
      ev = JSON.parse(t) as Record<string, unknown>
    } catch {
      continue
    }
    if (ev.type === "stderr") continue
    const sid = ev.sessionID
    if (typeof sid === "string" && sid.length > 0) return sid
  }
  return undefined
}

function runNativeMemoryFinalize(input: {
  repoRoot: string
  repoDir: string
  sessionID: string
  mode: Exclude<MemoryFinalize, "none">
  model?: string
}) {
  const innerTimeoutMs = Number(process.env.OPENCODE_BENCH_MEMORY_FINALIZE_TIMEOUT_MS ?? 3 * 60 * 1000)
  const timeoutMs = innerTimeoutMs + 30_000
  const script = path.join(input.repoRoot, "packages", "opencode", "script", "bench-memory-finalize.ts")
  const args = [
    "run",
    "--cwd",
    path.join(input.repoRoot, "packages", "opencode"),
    "--conditions=browser",
    script,
    input.repoDir,
    input.sessionID,
    input.mode,
    input.model ?? "",
  ]
  runChecked("bun", args, input.repoRoot, { timeoutMs })
}

/** Aborts the entire harness run when the session log contains provider_unavailable (model unavailable after OpenCode retries). */
async function abortIfProviderUnavailableInSessionLog(logPath: string, sessionLabel: string) {
  if (!(await exists(logPath))) return
  const content = await fs.readFile(logPath, "utf8")
  if (!sessionLogIndicatesProviderUnavailable(content)) return
  const detail = `provider_unavailable in session log; aborting bench run (${sessionLabel}). Log: ${logPath}`
  progress(detail)
  throw new Error(detail)
}

async function runSession(input: {
  opts: RunOptions
  showcase: ShowcaseName
  condition: Condition
  order: number
  totalSessions: number
  instance: Instance
  prompt: string
  expectedFiles: string[]
  shouldVerify: boolean
}) {
  const sessionLabel = `${input.showcase}/${input.condition}/session-${input.order}`
  progress(`starting ${sessionLabel}`, {
    session: `${input.order}/${input.totalSessions}`,
    instance: input.instance.instance_id,
    execute: input.opts.execute,
  })
  const canonicalRoot = path.join(input.opts.benchDir, input.showcase, "runs", input.condition, `session-${input.order}`)
  const root = input.opts.execute
    ? canonicalRoot
    : path.join(
        await ensureDryRunStagingRoot(input.opts.benchDir),
        input.showcase,
        "runs",
        input.condition,
        `session-${input.order}`,
      )
  await fs.mkdir(root, { recursive: true })
  progress(`preparing workspace for ${sessionLabel}`)
  const repoDir = input.opts.prepareWorkspaces ? await prepareRepo(input.instance, root, input.opts.force) : path.join(root, "repo")
  await fs.mkdir(repoDir, { recursive: true })
  if (input.opts.prepareWorkspaces) {
    const reset = await resetGitWorkspaceIfResidual(repoDir, input.instance.base_commit)
    if (reset) progress(`reset dirty workspace for ${sessionLabel}`, { baseCommit: input.instance.base_commit.slice(0, 12) })
    const cleared = await clearLocalMemoryOffIfResidual(repoDir, input.condition)
    if (cleared) progress(`cleared local memory for ${sessionLabel}`, { entries: cleared })
  }
  const pythonEnv = input.opts.execute
    ? await preparePythonEnvironment(repoDir, input.instance)
    : {
        ...venvPaths(repoDir),
        sourcePython: process.env.OPENCODE_BENCH_PYTHON?.trim() ?? "not-prepared",
        sourcePythonVersion: "not-prepared",
        prepared: false,
      }
  await copyConfig(input.opts.benchDir, input.condition, repoDir)
  await linkSharedMemory(input.opts.benchDir, input.showcase, input.condition, repoDir)
  progress(`workspace ready for ${sessionLabel}`, { repoDir, venv: pythonEnv.path, python: pythonEnv.sourcePythonVersion })

  const promptPath = path.join(root, "prompt.txt")
  const logPath = path.join(root, "session-log.jsonl")
  const patchPath = path.join(root, "patch.diff")
  await fs.writeFile(promptPath, input.prompt, "utf8")
  await writeJson(path.join(root, "session-meta.json"), {
    showcase: input.showcase,
    condition: input.condition,
    order: input.order,
    instance_id: input.instance.instance_id,
    repo: input.instance.repo,
    base_commit: input.instance.base_commit,
    expected_files: input.expectedFiles,
    should_verify: input.shouldVerify,
    dry_run: !input.opts.execute,
    python_env: pythonEnv,
  })

  if (input.opts.execute) {
    progress(`running opencode for ${sessionLabel}`, { log: logPath })
    try {
      await runOpencode({ repoRoot: input.opts.repoRoot, repoDir, prompt: input.prompt, logPath, model: input.opts.model, agent: input.opts.agent })
    } catch (error) {
      await abortIfProviderUnavailableInSessionLog(logPath, sessionLabel)
      progress(`opencode failed for ${sessionLabel}`, { log: logPath })
      throw error
    }
    await abortIfProviderUnavailableInSessionLog(logPath, sessionLabel)

    if (
      input.opts.memoryFinalize !== "none" &&
      input.condition === "memory-on" &&
      input.opts.execute
    ) {
      const logContent = await fs.readFile(logPath, "utf8")
      const sessionID = parseSessionIdFromSessionLogContent(logContent)
      if (!sessionID) {
        throw new Error(
          `controlled memory finalize (${input.opts.memoryFinalize}): could not parse sessionID from ${logPath}`,
        )
      }
      const finalizePath = path.join(root, "memory-finalize.json")
      const mode = input.opts.memoryFinalize
      try {
        progress(`memory finalize (${mode}) for ${sessionLabel}`, { sessionID })
        runNativeMemoryFinalize({
          repoRoot: input.opts.repoRoot,
          repoDir,
          sessionID,
          mode,
          model: input.opts.model,
        })
        await writeJson(finalizePath, {
          mode,
          sessionID,
          status: "ok",
          timestamp: Date.now(),
        })
      } catch (error) {
        await writeJson(finalizePath, {
          mode,
          sessionID,
          status: "error",
          error: String(error),
          timestamp: Date.now(),
        })
        throw error
      }
    }

    progress(`collecting patch for ${sessionLabel}`, { patch: patchPath })
    await gitDiff(repoDir, patchPath)
    const patchBytes = (await fs.stat(patchPath)).size
    progress(`finished ${sessionLabel}`, { patchBytes })
  } else {
    await dryRunLog(logPath, input.prompt)
    if (!(await exists(patchPath))) await fs.writeFile(patchPath, "", "utf8")
    progress(`finished dry-run ${sessionLabel}`, { prompt: promptPath, log: logPath })
  }
}

async function runShowcase1(opts: RunOptions, condition: Condition) {
  progress("starting showcase condition", { showcase: "showcase1", condition })
  if (opts.execute) {
    const cleared = await clearSharedMemoryIfResidual(opts.benchDir, "showcase1", condition)
    if (cleared) progress("cleared shared memory", { showcase: "showcase1", condition, entries: cleared })
  }
  const tasks = await readJson<{ sessions: Showcase1Session[] }>(path.join(opts.benchDir, "showcase1", "tasks.json"))
  const ids = tasks.sessions.map((s) => s.instance_id)
  progress("loading instances", { showcase: "showcase1", count: ids.length })
  const instances = await ensureInstances(opts.benchDir, ids)
  for (const session of tasks.sessions) {
    const instance = instances[session.instance_id]
    if (!instance) throw new Error(`Missing instance metadata for ${session.instance_id}`)
    await runSession({
      opts,
      showcase: "showcase1",
      condition,
      order: session.order,
      totalSessions: tasks.sessions.length,
      instance,
      prompt: renderPrompt(session.prompt, instance),
      expectedFiles: session.expected_files,
      shouldVerify: true,
    })
  }
  progress("writing showcase report", { showcase: "showcase1" })
  await writeShowcaseReport(opts.benchDir, "showcase1", tasks.sessions.length)
  progress("finished showcase condition", { showcase: "showcase1", condition })
}

async function runShowcase2(opts: RunOptions, condition: Condition) {
  progress("starting showcase condition", { showcase: "showcase2", condition })
  if (opts.execute) {
    const cleared = await clearSharedMemoryIfResidual(opts.benchDir, "showcase2", condition)
    if (cleared) progress("cleared shared memory", { showcase: "showcase2", condition, entries: cleared })
  }
  const tasks = await readJson<{ tasks: { instance_id: string }[]; sessions: Showcase2Session[] }>(path.join(opts.benchDir, "showcase2", "tasks.json"))
  const ids = unique(tasks.tasks.map((t) => t.instance_id))
  progress("loading instances", { showcase: "showcase2", count: ids.length })
  const instances = await ensureInstances(opts.benchDir, ids)
  for (const session of tasks.sessions) {
    const instance = instances[session.task]
    if (!instance) throw new Error(`Missing instance metadata for ${session.task}`)
    const template = await fs.readFile(path.join(opts.benchDir, "showcase2", session.prompt_file), "utf8")
    await runSession({
      opts,
      showcase: "showcase2",
      condition,
      order: session.order,
      totalSessions: tasks.sessions.length,
      instance,
      prompt: renderPrompt(template, instance),
      expectedFiles: session.expected_files,
      shouldVerify: session.should_verify,
    })
  }
  progress("writing showcase report", { showcase: "showcase2" })
  await writeShowcaseReport(opts.benchDir, "showcase2", tasks.sessions.length)
  progress("finished showcase condition", { showcase: "showcase2", condition })
}

type PredictionRow = {
  model_name_or_path: string
  instance_id: string
  model_patch: string
}

async function writePredictions(opts: RunOptions, showcases: ShowcaseName[], conditions: Condition[]) {
  const byCondition: Record<Condition, PredictionRow[]> = { "memory-on": [], "memory-off": [] }
  for (const showcase of showcases) {
    for (const condition of conditions) {
      const runs = path.join(opts.benchDir, showcase, "runs", condition)
      if (!(await exists(runs))) continue
      for (const entry of await fs.readdir(runs)) {
        if (!entry.startsWith("session-")) continue
        const dir = path.join(runs, entry)
        const metaPath = path.join(dir, "session-meta.json")
        const patchPath = path.join(dir, "patch.diff")
        if (!(await exists(metaPath)) || !(await exists(patchPath))) continue
        const meta = await readJson<any>(metaPath)
        if (!meta.should_verify) continue
        byCondition[condition].push({
          model_name_or_path: `opencode-memory-eval-${condition}`,
          instance_id: meta.instance_id,
          model_patch: await fs.readFile(patchPath, "utf8"),
        })
      }
    }
  }
  const verifyDir = path.join(opts.benchDir, "swebench-verify")
  await fs.mkdir(verifyDir, { recursive: true })
  const perCondition: { condition: Condition; path: string; count: number }[] = []
  for (const condition of conditions) {
    const rows = byCondition[condition]
    const outPath = path.join(verifyDir, `predictions-${condition}.jsonl`)
    await fs.writeFile(outPath, rows.map((p) => JSON.stringify(p)).join("\n") + (rows.length ? "\n" : ""), "utf8")
    perCondition.push({ condition, path: outPath, count: rows.length })
  }
  const combined = conditions.flatMap((c) => byCondition[c])
  const combinedPath = path.join(verifyDir, "predictions-all.jsonl")
  await fs.writeFile(
    combinedPath,
    combined.map((p) => JSON.stringify(p)).join("\n") + (combined.length ? "\n" : ""),
    "utf8",
  )
  /** @deprecated Combined file duplicates `instance_id` across conditions; prefer `predictions-<condition>.jsonl`. */
  const legacyPath = path.join(verifyDir, "predictions.jsonl")
  await fs.writeFile(
    legacyPath,
    combined.map((p) => JSON.stringify(p)).join("\n") + (combined.length ? "\n" : ""),
    "utf8",
  )
  return { perCondition, combinedPath, legacyPath, totalCount: combined.length }
}

async function maybeVerify(opts: RunOptions, showcases: ShowcaseName[], conditions: Condition[]) {
  progress("writing SWE-bench predictions")
  const predictions = await writePredictions(opts, showcases, conditions)
  progress("wrote SWE-bench predictions", {
    total: predictions.totalCount,
    combined: predictions.combinedPath,
    memoryOn: predictions.perCondition.find((b) => b.condition === "memory-on")?.count ?? 0,
    memoryOff: predictions.perCondition.find((b) => b.condition === "memory-off")?.count ?? 0,
  })
  if (!opts.verify) return
  const py = pythonForSwebench(opts.benchDir)
  const cwd = path.join(opts.benchDir, "swebench-verify")
  for (const block of predictions.perCondition) {
    if (block.count === 0) {
      progress("skip SWE-bench verification", { condition: block.condition, predictions: 0 })
      continue
    }
    progress("starting SWE-bench Docker verification", {
      condition: block.condition,
      predictions: block.count,
      predictionsPath: block.path,
      python: py,
    })
    runChecked(
      py,
      [
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        "SWE-bench/SWE-bench_Verified",
        "--split",
        "test",
        "--predictions_path",
        block.path,
        "--run_id",
        `opencode-memory-eval-${block.condition}`,
        "--max_workers",
        "1",
      ],
      cwd,
    )
    progress("finished SWE-bench Docker verification", { condition: block.condition })
  }
}

async function main() {
  const opts = parseArgs()
  const showcases = opts.showcase === "all" ? (["showcase1", "showcase2"] as ShowcaseName[]) : [opts.showcase]
  const conditions = opts.condition === "all" ? (["memory-on", "memory-off"] as Condition[]) : [opts.condition]

  progress("bench run starting", {
    showcase: opts.showcase,
    condition: opts.condition,
    execute: opts.execute,
    verify: opts.verify,
    verifyOnly: opts.verifyOnly,
    prepareWorkspaces: opts.prepareWorkspaces,
    force: opts.force,
    model: opts.model,
    agent: opts.agent,
    memory_finalize: opts.memoryFinalize,
  })
  if (opts.verifyOnly && !opts.verify) {
    throw new Error("--verify-only requires --verify")
  }
  if (!opts.verifyOnly) {
    for (const showcase of showcases) {
      for (const condition of conditions) {
        if (showcase === "showcase1") await runShowcase1(opts, condition)
        else await runShowcase2(opts, condition)
      }
    }
  }
  await maybeVerify(opts, showcases, conditions)
  if (dryRunStagingRoot) {
    progress("dry-run finished (real runs under showcase/*/runs/ were not modified)", { staging: dryRunStagingRoot })
  }
  progress("bench run complete", { execute: opts.execute, verify: opts.verify })
}

await main()
