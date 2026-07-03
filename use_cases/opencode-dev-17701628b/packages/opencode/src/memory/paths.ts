import path from "path"

/** Per-project durable memory directory (under the session working directory). */
export function memoryDataDir(sessionDirectory: string): string {
  return path.join(sessionDirectory, ".opencode", "memory")
}
