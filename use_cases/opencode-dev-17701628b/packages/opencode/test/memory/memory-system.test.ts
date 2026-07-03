import { afterEach, describe, expect, test } from "bun:test"
import { mkdtemp, rm } from "node:fs/promises"
import * as os from "node:os"
import * as path from "node:path"
import { generateObject } from "ai"
import { createOpenRouter } from "@openrouter/ai-sdk-provider"
import z from "zod"
import {
  appendFact,
  appendObservation,
  hashEvidence,
  listActiveFacts,
  listSessionCards,
  readHandoff,
  recordToolOutcome,
  readToolLessonsText,
  writeHandoff,
  buildMemoryContextBlocks,
  redactLikelySecrets,
  persistSessionTrajectory,
} from "../../src/memory"
import type { HandoffRecord } from "../../src/memory/types"

describe("memory filesystem stores", () => {
  let dir: string

  afterEach(async () => {
    if (dir) await rm(dir, { recursive: true, force: true })
  })

  test("appendFact round-trip and listActiveFacts", async () => {
    dir = await mkdtemp(path.join(os.tmpdir(), "oc-mem-"))
    await appendFact(dir, {
      owner: "user",
      scope: "project",
      title: "tabs",
      text: "Use spaces for indentation",
      confidence: 0.9,
      status: "active",
      evidence: {},
    })
    const facts = await listActiveFacts(dir)
    expect(facts.length).toBe(1)
    expect(facts[0]?.title).toBe("tabs")
  })

  test("handoff replace read", async () => {
    dir = await mkdtemp(path.join(os.tmpdir(), "oc-mem-"))
    const h: HandoffRecord = {
      goal: "Ship feature X",
      constraints: "None",
      done: ["setup"],
      inProgress: ["tests"],
      blockers: [],
      decisions: [],
      nextSteps: ["merge"],
      files: ["src/a.ts"],
      freshness: Date.now(),
    }
    await writeHandoff(dir, h)
    const r = await readHandoff(dir)
    expect(r?.goal).toBe("Ship feature X")
  })

  test("observation dedupe by evidenceHash", async () => {
    dir = await mkdtemp(path.join(os.tmpdir(), "oc-mem-"))
    const h = hashEvidence(["t1", "body"])
    const a = await appendObservation(dir, {
      owner: "agent",
      type: "procedure",
      title: "t1",
      l0: "x",
      l1: "body",
      commands: [],
      tools: ["bash"],
      files: [],
      evidence: {},
      evidenceHash: h,
    })
    const b = await appendObservation(dir, {
      owner: "agent",
      type: "procedure",
      title: "t1",
      l0: "x",
      l1: "body",
      commands: [],
      tools: ["bash"],
      files: [],
      evidence: {},
      evidenceHash: h,
    })
    expect(a).toBeDefined()
    expect(b).toBeUndefined()
  })

  test("tool stats deterministic merge", async () => {
    dir = await mkdtemp(path.join(os.tmpdir(), "oc-mem-"))
    await recordToolOutcome(dir, { tool: "bash", ok: true, durationMs: 10 })
    await recordToolOutcome(dir, { tool: "bash", ok: false, durationMs: 5, error: "e1" })
    const txt = await readToolLessonsText(dir, ["bash"], 2000)
    expect(txt).toContain("bash")
  })

  test("recall blocks include facts for keyword", async () => {
    dir = await mkdtemp(path.join(os.tmpdir(), "oc-mem-"))
    await appendFact(dir, {
      owner: "agent",
      scope: "project",
      title: "lint",
      text: "Run eslint before commit",
      confidence: 0.8,
      status: "active",
      evidence: {},
    })
    const blocks = await buildMemoryContextBlocks({
      sessionDirectory: dir,
      userPrompt: "eslint commit",
    })
    expect(blocks.some((b) => b.includes("eslint"))).toBe(true)
  })

  test("redactLikelySecrets masks bearer token", () => {
    const t = redactLikelySecrets("Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890")
    expect(t).toContain("[REDACTED]")
    expect(t).not.toContain("abcdefghijklmnopqrstuvwxyz1234567890")
  })

  test("persistSessionTrajectory appends session card; same dir survives re-init", async () => {
    dir = await mkdtemp(path.join(os.tmpdir(), "oc-mem-"))
    await persistSessionTrajectory({
      session: {
        id: "ses_test1",
        projectID: "proj_a",
        directory: dir,
        title: "fix login bug",
        time: { created: 1, updated: 2 },
      },
      messages: [{ role: "user", parts: [{ type: "text", text: "check auth.ts" }] }],
    })
    const cards1 = await listSessionCards(dir)
    expect(cards1.some((c) => c.sessionID === "ses_test1")).toBe(true)
    const cards2 = await listSessionCards(dir)
    expect(cards2.length).toBe(cards1.length)
  })

  test("lifecycle: facts persist across new reader on same directory", async () => {
    dir = await mkdtemp(path.join(os.tmpdir(), "oc-mem-"))
    await appendFact(dir, {
      owner: "user",
      scope: "project",
      title: "persist",
      text: "value A",
      confidence: 1,
      status: "active",
      evidence: {},
    })
    const again = await listActiveFacts(dir)
    expect(again.find((f) => f.title === "persist")?.text).toBe("value A")
  })
})

function openrouterKeyForMemoryTest(): string | undefined {
  return (
    process.env["OPENROUTER_API_KEY"] ??
    (globalThis as { __OPENCODE_STASHED_OPENROUTER_API_KEY__?: string }).__OPENCODE_STASHED_OPENROUTER_API_KEY__
  )
}

describe("memory LLM extraction (real provider)", () => {
  test.skipIf(!openrouterKeyForMemoryTest())(
    "generateObject schema extraction succeeds via OpenRouter",
    async () => {
      const apiKey = openrouterKeyForMemoryTest()!
      const openrouter = createOpenRouter({ apiKey })
      const modelId =
        process.env["OPENCODE_MEMORY_LLM_TEST_MODEL"]?.trim() || "minimax/minimax-m2.7"
      const model = openrouter(modelId)

      const schema = z.object({ facts: z.array(z.string()) })

      const r = await generateObject({
        model,
        temperature: 0,
        messages: [{ role: "user", content: 'Return JSON with facts: ["alpha","beta"] only. No prose.' }],
        schema,
      }).catch(() => null)

      expect(r).not.toBeNull()
      expect(r?.object.facts?.length).toBeGreaterThanOrEqual(1)
    },
  )
})
