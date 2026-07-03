import { Effect, Schema } from "effect"
import * as Tool from "./tool"
import { Session } from "../session"
import { buildMemoryContextBlocks } from "../memory"

export const Parameters = Schema.Struct({
  query: Schema.String,
})

export const MemoryRecallTool = Tool.define(
  "memory_recall",
  Effect.gen(function* () {
    const session = yield* Session.Service
    return {
      description:
        "Retrieve long-term workspace memory: remembered facts, task handoff, past observations, tool usage stats, and related prior sessions. Use when the user asks what was remembered or you need more context than the automatic recall block.",
      parameters: Parameters,
      execute: (params: { query: string }, ctx: Tool.Context) =>
        Effect.gen(function* () {
          const sess = yield* session.get(ctx.sessionID)
          const blocks = yield* Effect.promise(() =>
            buildMemoryContextBlocks({
              sessionDirectory: sess.directory,
              workspaceID: sess.workspaceID ?? undefined,
              userPrompt: params.query,
              currentSessionID: ctx.sessionID,
            }),
          )
          const output = blocks.filter(Boolean).join("\n\n") || "(No matching memory entries.)"
          return { title: "Workspace memory", output, metadata: {} }
        }),
    }
  }),
)
