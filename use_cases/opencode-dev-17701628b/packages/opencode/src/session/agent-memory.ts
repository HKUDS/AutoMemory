import { Context, Effect, Layer } from "effect"
import { generateObject } from "ai"
import type { ModelMessage } from "ai"
import z from "zod"
import * as MessageV2 from "./message-v2"
import * as Session from "./session"
import { Config } from "../config"
import { Provider } from "../provider"
import { Auth } from "../auth"
import type { ModelID, ProviderID } from "../provider/schema"
import { ProviderTransform } from "../provider"
import { appendFact, appendObservation, buildMemoryContextBlocks } from "../memory"
import { hashEvidence } from "../memory/observations"
import { redactLikelySecrets } from "../memory/redact"
import type { HandoffRecord } from "../memory/types"
import { recordToolOutcome } from "../memory/tool-stats"
import { formatHandoffForPrompt, readHandoff, writeHandoff } from "../memory/handoff"
import { setFactStatus } from "../memory/facts"
import { Log } from "../util"
import type { SessionID } from "./schema"

const log = Log.create({ service: "agent-memory" })

export interface AgentMemoryService {
  readonly recallSystemStrings: (input: {
    sessionID: SessionID
    userPrompt: string
    availableToolIds?: string[]
  }) => Effect.Effect<string[]>

  readonly ingestUserExplicitRemember: (input: { sessionID: SessionID; userText: string }) => Effect.Effect<void>

  readonly ingestUserForgetOrCorrect: (input: { sessionID: SessionID; userText: string }) => Effect.Effect<void>

  readonly onToolSettled: (input: {
    sessionID: SessionID
    tool: string
    ok: boolean
    durationMs: number
    error?: string
    output?: string
    args?: unknown
  }) => Effect.Effect<void>

  readonly afterCompaction: (input: {
    sessionID: SessionID
    headMessages: MessageV2.WithParts[]
    compactionSummary: string
    fallbackModel: { providerID: ProviderID; modelID: ModelID }
  }) => Effect.Effect<void>

  readonly handoffCompactionContext: (input: { sessionID: SessionID }) => Effect.Effect<string[]>
}

export class AgentMemory extends Context.Service<AgentMemory, AgentMemoryService>()("@opencode/AgentMemory") {}

function serializeTranscriptForExcerpt(messages: MessageV2.WithParts[], maxChars: number): string {
  const parts: string[] = []
  let size = 0
  for (const m of messages) {
    const role = m.info.role
    const head = `[${role} ${m.info.id}]`
    for (const p of m.parts) {
      let chunk = ""
      if (p.type === "text" && !("ignored" in p && p.ignored)) chunk = p.text
      else if (p.type === "tool") {
        const st = p.state
        if (st.status === "completed")
          chunk = `tool ${p.tool} ok output=${(st.output ?? "").slice(0, 1200)}`
        else if (st.status === "error") chunk = `tool ${p.tool} err=${st.error}`
        else continue
      } else continue
      if (!chunk.trim()) continue
      const line = `${head}\n${chunk}\n`
      if (size + line.length > maxChars) break
      parts.push(line)
      size += line.length
    }
    if (size >= maxChars) break
  }
  return redactLikelySecrets(parts.join("\n"))
}

const ExtractionSchema = z.object({
  facts: z
    .array(
      z.object({
        title: z.string(),
        text: z.string(),
        scope: z.enum(["global", "project", "workspace", "session"]),
        owner: z.enum(["user", "agent"]),
        confidence: z.number().min(0).max(1).optional(),
      }),
    )
    .max(8),
  observations: z
    .array(
      z.object({
        type: z.enum(["case", "pattern", "environment_quirk", "decision", "failure", "procedure"]),
        title: z.string(),
        l0: z.string(),
        l1: z.string(),
        commands: z.array(z.string()).optional(),
        tools: z.array(z.string()).optional(),
        files: z.array(z.string()).optional(),
        errorFingerprint: z.string().optional(),
      }),
    )
    .max(6),
  handoff: z
    .object({
      goal: z.string(),
      constraints: z.string(),
      done: z.array(z.string()),
      inProgress: z.array(z.string()),
      blockers: z.array(z.string()),
      decisions: z.array(z.string()),
      nextSteps: z.array(z.string()),
      files: z.array(z.string()),
    })
    .optional(),
})

function memoryEnabled(cfg: { memory?: { enabled?: boolean } }): boolean {
  return cfg.memory?.enabled !== false
}

const emptyExtraction = { facts: [], observations: [], handoff: undefined } satisfies z.infer<typeof ExtractionSchema>

export const layer = Layer.effect(
  AgentMemory,
  Effect.gen(function* () {
    const config = yield* Config.Service
    const provider = yield* Provider.Service
    const auth = yield* Auth.Service
    const session = yield* Session.Service

    const resolveExtractionModel = Effect.fn("AgentMemory.resolveExtractionModel")(function* (fallback: {
      providerID: ProviderID
      modelID: ModelID
    }) {
      const cfg = yield* config.get()
      const spec = cfg.memory_model ?? cfg.model
      if (spec) {
        const parsed = Provider.parseModel(spec)
        return yield* provider.getModel(parsed.providerID, parsed.modelID)
      }
      return yield* provider.getModel(fallback.providerID, fallback.modelID)
    })

    const runExtraction = Effect.fn("AgentMemory.runExtraction")(function* (input: {
      excerpt: string
      summary: string
      fallbackModel: { providerID: ProviderID; modelID: ModelID }
      sessionDirectory: string
      sessionID: string
    }) {
      const cfg = yield* config.get()
      if (!memoryEnabled(cfg)) return

      const model = yield* resolveExtractionModel(input.fallbackModel)
      const language = yield* provider.getLanguage(model)
      const authInfo = yield* auth.get(model.providerID).pipe(Effect.orDie)
      const isOpenaiOauth = model.providerID === "openai" && authInfo?.type === "oauth"

      const system = [
        "You extract durable memory from a coding agent transcript. Return structured JSON only.",
        "Classify owner: user for preferences/constraints the human stated; agent for operational/repo discoveries.",
        "Never include secrets, tokens, keys, or env values.",
        "Prefer concise facts and observations that help future sessions in the same repo.",
      ].join("\n")
      const user = [
        "Transcript excerpt (may be truncated):\n",
        input.excerpt.slice(0, 24_000),
        "\n\nNew compaction summary:\n",
        input.summary.slice(0, 12_000),
      ].join("")

      const messages: ModelMessage[] = [{ role: "user", content: user }]

      const obj = yield* Effect.promise(async () => {
        try {
          const params = {
            model: language,
            temperature: 0.1,
            system,
            messages,
            schema: ExtractionSchema,
          } as const
          if (isOpenaiOauth) {
            const { streamObject } = await import("ai")
            const result = streamObject({
              ...params,
              providerOptions: ProviderTransform.providerOptions(model, {
                instructions: system,
                store: false,
              }),
              onError: () => {},
            })
            for await (const part of result.fullStream) {
              if (part.type === "error") throw part.error
            }
            return result.object
          }
          return await generateObject(params).then((r) => r.object)
        } catch (e) {
          log.warn("memory extraction failed", { error: String(e) })
          return emptyExtraction
        }
      })

      for (const f of obj.facts) {
        yield* Effect.promise(() =>
          appendFact(input.sessionDirectory, {
            owner: f.owner,
            scope: f.scope,
            title: f.title.slice(0, 200),
            text: redactLikelySecrets(f.text).slice(0, 2000),
            confidence: f.confidence ?? 0.6,
            status: "active",
            evidence: { sessionID: input.sessionID },
          }),
        ).pipe(Effect.ignore)
      }

      for (const o of obj.observations) {
        const evidenceHash = hashEvidence([o.title, o.l1, ...(o.commands ?? [])])
        yield* Effect.promise(() =>
          appendObservation(input.sessionDirectory, {
            owner: "agent",
            type: o.type,
            title: o.title.slice(0, 200),
            l0: redactLikelySecrets(o.l0).slice(0, 400),
            l1: redactLikelySecrets(o.l1).slice(0, 4000),
            commands: o.commands?.map((c: string) => redactLikelySecrets(c).slice(0, 500)) ?? [],
            tools: o.tools ?? [],
            files: o.files?.slice(0, 40) ?? [],
            errorFingerprint: o.errorFingerprint ? redactLikelySecrets(o.errorFingerprint).slice(0, 400) : undefined,
            evidence: { sessionID: input.sessionID },
            evidenceHash,
          }),
        ).pipe(Effect.ignore)
      }

      if (obj.handoff) {
        const h: HandoffRecord = {
          goal: redactLikelySecrets(obj.handoff.goal).slice(0, 2000),
          constraints: redactLikelySecrets(obj.handoff.constraints).slice(0, 2000),
          done: obj.handoff.done.map((x: string) => redactLikelySecrets(x).slice(0, 500)),
          inProgress: obj.handoff.inProgress.map((x: string) => redactLikelySecrets(x).slice(0, 500)),
          blockers: obj.handoff.blockers.map((x: string) => redactLikelySecrets(x).slice(0, 500)),
          decisions: obj.handoff.decisions.map((x: string) => redactLikelySecrets(x).slice(0, 500)),
          nextSteps: obj.handoff.nextSteps.map((x: string) => redactLikelySecrets(x).slice(0, 500)),
          files: obj.handoff.files.map((x: string) => redactLikelySecrets(x).slice(0, 500)),
          evidenceSessionID: input.sessionID,
          freshness: Date.now(),
        }
        yield* Effect.promise(() => writeHandoff(input.sessionDirectory, h)).pipe(Effect.ignore)
      }

      return undefined
    })

    const svc: AgentMemoryService = {
      recallSystemStrings: (input) =>
        Effect.gen(function* () {
          const cfg = yield* config.get()
          if (!memoryEnabled(cfg)) return [] as string[]
          const sess = yield* session.get(input.sessionID)
          return yield* Effect.promise(() =>
            buildMemoryContextBlocks({
              sessionDirectory: sess.directory,
              workspaceID: sess.workspaceID ?? undefined,
              userPrompt: input.userPrompt,
              currentSessionID: input.sessionID,
              availableToolIds: input.availableToolIds,
            }),
          ).pipe(Effect.catch(() => Effect.succeed([] as string[])))
        }),

      ingestUserExplicitRemember: (input) =>
        Effect.gen(function* () {
          const cfg = yield* config.get()
          if (!memoryEnabled(cfg)) return
          const text = input.userText.trim()
          const remember =
            /^(?:please\s+)?remember\s*[:,]?\s*(.+)$/i.exec(text) ?? /^memorize\s*[:,]?\s*(.+)$/i.exec(text)
          if (!remember) return
          const fact = remember[1]?.trim()
          if (!fact) return
          const sess = yield* session.get(input.sessionID)
          yield* Effect.promise(() =>
            appendFact(sess.directory, {
              owner: "user",
              scope: "project",
              title: fact.slice(0, 120),
              text: redactLikelySecrets(fact).slice(0, 2000),
              confidence: 1,
              status: "active",
              evidence: { sessionID: input.sessionID },
            }),
          )
        }).pipe(Effect.ignore),

      ingestUserForgetOrCorrect: (input) =>
        Effect.gen(function* () {
          const cfg = yield* config.get()
          if (!memoryEnabled(cfg)) return
          const forget =
            /^(?:please\s+)?forget\s+(?:about\s+)?(.+)$/i.exec(input.userText.trim()) ??
            /^delete\s+memory\s+(?:about\s+)?(.+)$/i.exec(input.userText.trim()) ??
            /^remove\s+the\s+memory\s+(?:about\s+)?(.+)$/i.exec(input.userText.trim())
          if (!forget) return
          const needle = forget[1]?.trim().toLowerCase()
          if (!needle) return
          const sess = yield* session.get(input.sessionID)
          yield* Effect.promise(() =>
            setFactStatus(
              sess.directory,
              (f) => f.title.toLowerCase().includes(needle) || f.text.toLowerCase().includes(needle),
              "inactive",
            ),
          ).pipe(Effect.ignore)
        }).pipe(Effect.ignore),

      onToolSettled: (input) =>
        Effect.gen(function* () {
          const cfg = yield* config.get()
          if (!memoryEnabled(cfg)) return
          const sess = yield* session.get(input.sessionID)
          yield* Effect.promise(() =>
            recordToolOutcome(sess.directory, {
              tool: input.tool,
              ok: input.ok,
              durationMs: input.durationMs,
              error: input.error,
            }),
          )
          const big = (input.output?.length ?? 0) > 800 || !input.ok
          if (!big) return
          const excerpt = redactLikelySecrets(
            [
              `tool=${input.tool}`,
              `ok=${input.ok}`,
              input.error ? `error=${input.error}` : "",
              input.output ? `output=${input.output.slice(0, 4000)}` : "",
              input.args ? `args=${JSON.stringify(input.args).slice(0, 2000)}` : "",
            ].join("\n"),
          )
          const fallback = yield* provider.defaultModel()
          const model = yield* resolveExtractionModel(fallback)
          const language = yield* provider.getLanguage(model)
          const schema = z.object({
            observations: ExtractionSchema.shape.observations,
          })
          const msgs: ModelMessage[] = [{ role: "user", content: excerpt.slice(0, 8000) }]
          const r = yield* Effect.promise(async () => {
            try {
              return await generateObject({
                model: language,
                temperature: 0,
                system: "Extract 0-2 reusable agent observations (no user preferences). Omit empty array if none.",
                messages: msgs,
                schema,
              })
            } catch (e) {
              log.warn("tool observation extract failed", { error: String(e) })
              return { object: { observations: [] as z.infer<typeof ExtractionSchema>["observations"] } }
            }
          })
          for (const o of r.object.observations) {
            const evidenceHash = hashEvidence([o.title, o.l1, input.sessionID, ...(o.commands ?? [])])
            yield* Effect.promise(() =>
              appendObservation(sess.directory, {
                owner: "agent",
                type: o.type,
                title: o.title.slice(0, 200),
                l0: redactLikelySecrets(o.l0).slice(0, 400),
                l1: redactLikelySecrets(o.l1).slice(0, 4000),
                commands: o.commands?.map((c: string) => redactLikelySecrets(c).slice(0, 500)) ?? [],
                tools: o.tools?.length ? o.tools : [input.tool],
                files: o.files?.slice(0, 40) ?? [],
                errorFingerprint: o.errorFingerprint,
                evidence: { sessionID: input.sessionID },
                evidenceHash,
              }),
            ).pipe(Effect.ignore)
          }
        }).pipe(Effect.ignore),

      afterCompaction: (input) =>
        Effect.gen(function* () {
          const cfg = yield* config.get()
          if (!memoryEnabled(cfg)) return
          const sess = yield* session.get(input.sessionID)
          yield* runExtraction({
            excerpt: serializeTranscriptForExcerpt(input.headMessages, 28_000),
            summary: input.compactionSummary,
            fallbackModel: input.fallbackModel,
            sessionDirectory: sess.directory,
            sessionID: input.sessionID,
          })
        }),

      handoffCompactionContext: (input) =>
        Effect.gen(function* () {
          const cfg = yield* config.get()
          if (!memoryEnabled(cfg)) return [] as string[]
          const sess = yield* session.get(input.sessionID)
          return yield* Effect.promise(async () => {
            const h = await readHandoff(sess.directory)
            if (!h) return [] as string[]
            return [formatHandoffForPrompt(h, 2800)]
          })
        }),
    }

    return AgentMemory.of(svc)
  }),
)

export const defaultLayer = layer.pipe(
  Layer.provide(Config.defaultLayer),
  Layer.provide(Provider.defaultLayer),
  Layer.provide(Auth.defaultLayer),
  Layer.provide(Session.defaultLayer),
)

/** Test / partial stacks: satisfies compaction + processor without LLM side effects. */
export const noopAgentMemoryLayer = Layer.succeed(
  AgentMemory,
  AgentMemory.of({
    recallSystemStrings: () => Effect.succeed([]),
    ingestUserExplicitRemember: () => Effect.void,
    ingestUserForgetOrCorrect: () => Effect.void,
    onToolSettled: () => Effect.void,
    afterCompaction: () => Effect.void,
    handoffCompactionContext: () => Effect.succeed([]),
  }),
)
