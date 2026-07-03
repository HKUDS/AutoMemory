import { redactLikelySecrets } from "./redact"
import { tokenizePrompt } from "./recall"
import { appendSessionCard } from "./sessions"

export type ArchiveMessagePart = {
  type: string
  text?: string
  ignored?: boolean
  synthetic?: boolean
}

export type ArchiveMessage = {
  role: "user" | "assistant"
  parts: ArchiveMessagePart[]
}

export type ArchiveSessionMeta = {
  id: string
  projectID: string
  directory: string
  title: string
  time: { created: number; updated: number }
}

function serializeForCard(messages: ArchiveMessage[], maxChars: number): string {
  const parts: string[] = []
  let size = 0
  for (const m of [...messages].reverse()) {
    const head = `[${m.role}]`
    for (const p of [...m.parts].reverse()) {
      if (p.type !== "text" || p.ignored || p.synthetic) continue
      const t = (p.text ?? "").trim()
      if (!t) continue
      const line = `${head}\n${t}\n`
      if (size + line.length > maxChars) {
        if (parts.length) continue
        const half = Math.max(200, Math.floor((maxChars - 6) / 2))
        const excerpt = `${line.slice(0, half)}\n…\n${line.slice(-half)}`
        parts.unshift(excerpt)
        size += excerpt.length
        continue
      }
      parts.unshift(line)
      size += line.length
    }
  }
  return redactLikelySecrets(parts.join("\n"))
}

/** Persist session trajectory card (no agent imports). */
export async function persistSessionTrajectory(input: {
  session: ArchiveSessionMeta
  messages: ArchiveMessage[]
}): Promise<void> {
  const safeTitle = redactLikelySecrets(input.session.title)
  const textBlob = redactLikelySecrets(
    input.messages
      .flatMap((m) => m.parts.filter((p) => p.type === "text" && !p.ignored && !p.synthetic).map((p) => p.text ?? ""))
      .join(" "),
  )
  const kws = tokenizePrompt(`${safeTitle} ${textBlob}`)
  const l0 = safeTitle.slice(0, 240)
  const l1 = serializeForCard(input.messages, 2000).slice(0, 2000)
  await appendSessionCard(input.session.directory, {
    sessionID: input.session.id,
    title: safeTitle,
    goal: l0,
    outcome: l1.slice(0, 1200),
    files: [],
    commands: kws.filter((k) => k.includes("/") || k.includes(".") || k === "bash").slice(0, 20),
    concepts: kws.slice(0, 30),
    l0,
    l1: l1.slice(0, 2000),
    time: { start: input.session.time.created, end: input.session.time.updated },
  })
}
