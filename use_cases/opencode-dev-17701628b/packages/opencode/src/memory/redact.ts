const SECRET_PATTERNS: RegExp[] = [
  /\b(sk-[a-zA-Z0-9]{20,})\b/g,
  /\b(sk-[a-zA-Z0-9][a-zA-Z0-9._-]{18,})\b/g,
  /\b(ghp_[a-zA-Z0-9]{36,})\b/g,
  /\b(github_pat_[a-zA-Z0-9_]{20,})\b/g,
  /\b(xox[baprs]-[a-zA-Z0-9-]{10,})\b/g,
  /\bAKIA[0-9A-Z]{16}\b/g,
  /\bBearer\s+[a-zA-Z0-9._-]{20,}\b/gi,
  /\b[\w.-]*(?:api[_-]?key|apikey|secret|token|password)\s*[:=]\s*\S{8,}/gi,
]

export function redactLikelySecrets(text: string): string {
  let out = text
  for (const re of SECRET_PATTERNS) {
    out = out.replace(re, "[REDACTED]")
  }
  return out
}
