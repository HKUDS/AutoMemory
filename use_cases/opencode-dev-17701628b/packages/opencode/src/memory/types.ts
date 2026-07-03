export type MemoryOwner = "user" | "agent"

export type MemoryScope = "global" | "project" | "workspace" | "session"

export type MemoryStatus = "active" | "superseded" | "rejected" | "inactive"

export type FactRecord = {
  id: string
  owner: MemoryOwner
  scope: MemoryScope
  title: string
  text: string
  confidence: number
  status: MemoryStatus
  evidence: { sessionID?: string; messageID?: string; partID?: string }
  updatedAt: number
}

export type ObservationType =
  | "case"
  | "pattern"
  | "environment_quirk"
  | "decision"
  | "failure"
  | "procedure"

export type ObservationRecord = {
  id: string
  owner: "agent"
  type: ObservationType
  title: string
  l0: string
  l1: string
  commands: string[]
  tools: string[]
  files: string[]
  errorFingerprint?: string
  evidence: { sessionID?: string }
  evidenceHash: string
  createdAt: number
}

export type HandoffRecord = {
  goal: string
  constraints: string
  done: string[]
  inProgress: string[]
  blockers: string[]
  decisions: string[]
  nextSteps: string[]
  files: string[]
  evidenceSessionID?: string
  freshness: number
}

export type ToolStatRecord = {
  tool: string
  calls: number
  successes: number
  errors: number
  totalDurationMs: number
  lastErrorSample?: string
  errorSamples?: Record<string, number>
  guideline?: string
  updatedAt: number
}

export type SessionCardRecord = {
  sessionID: string
  title: string
  goal: string
  outcome: string
  files: string[]
  commands: string[]
  concepts: string[]
  l0: string
  l1: string
  time: { start: number; end: number }
}
