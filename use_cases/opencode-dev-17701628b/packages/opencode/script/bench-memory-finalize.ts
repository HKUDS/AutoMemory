#!/usr/bin/env bun
/**
 * Invoked by opencode/bench/harness.ts to run native session.summarize / session.update(archived)
 * after a benchmark session. Not for direct use.
 */
import { bootstrap } from "../src/cli/bootstrap"
import { createOpencodeClient, type OpencodeClient } from "@opencode-ai/sdk/v2"
import { Server } from "../src/server/server"
import { Provider } from "../src/provider"

type Mode = "summarize" | "archive" | "summarize-archive"

function usage(): never {
  console.error(
    "usage: bun script/bench-memory-finalize.ts <repoDir> <sessionID> <summarize|archive|summarize-archive> [model]",
  )
  process.exit(1)
}

async function resolveModel(sdk: OpencodeClient, repoDir: string, modelArg: string | undefined) {
  if (modelArg) return Provider.parseModel(modelArg)
  const r = await sdk.config.get({ directory: repoDir }, { throwOnError: true })
  const m = r.data?.model
  if (typeof m === "string" && m.length > 0) return Provider.parseModel(m)
  throw new Error("bench-memory-finalize: pass model as 4th arg or set `model` in opencode config for the session repo")
}

const repoDir = process.argv[2]
const sessionID = process.argv[3]
const mode = process.argv[4] as Mode
const modelArg = process.argv[5] || undefined
const FINALIZE_TIMEOUT_MS = Number(process.env.OPENCODE_BENCH_MEMORY_FINALIZE_TIMEOUT_MS ?? 3 * 60 * 1000)
const OP_TIMEOUT_MS = Math.max(FINALIZE_TIMEOUT_MS - 60_000, 60_000)

if (!repoDir || !sessionID || !mode) usage()
if (mode !== "summarize" && mode !== "archive" && mode !== "summarize-archive") usage()

function withTimeout<T>(label: string, ms: number, promise: Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`${label} timed out after ${ms}ms`))
    }, ms)
    promise.then(
      (value) => {
        clearTimeout(timer)
        resolve(value)
      },
      (error) => {
        clearTimeout(timer)
        reject(error)
      },
    )
  })
}

async function main() {
  await withTimeout("bench memory finalize", FINALIZE_TIMEOUT_MS, bootstrap(repoDir, async () => {
    const fetchFn = (async (input: RequestInfo | URL, init?: RequestInit) => {
      return Server.Default().app.fetch(new Request(input, init))
    }) as typeof globalThis.fetch
    const sdk = createOpencodeClient({ baseUrl: "http://opencode.internal", fetch: fetchFn })

    if (mode === "summarize" || mode === "summarize-archive") {
      const { providerID, modelID } = await resolveModel(sdk, repoDir, modelArg)
      await withTimeout(
        "session.summarize",
        OP_TIMEOUT_MS,
        sdk.session.summarize(
          { sessionID, directory: repoDir, providerID, modelID, auto: false },
          { throwOnError: true },
        ),
      )
    }
    if (mode === "archive" || mode === "summarize-archive") {
      await withTimeout(
        "session.update archived",
        OP_TIMEOUT_MS,
        sdk.session.update(
          { sessionID, directory: repoDir, time: { archived: Date.now() } },
          { throwOnError: true },
        ),
      )
    }
  }))
}

main().then(
  () => process.exit(0),
  (error) => {
    console.error("bench-memory-finalize failed:", error)
    process.exit(1)
  },
)
