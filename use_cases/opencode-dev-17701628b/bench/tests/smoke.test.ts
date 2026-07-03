import { expect, test } from "bun:test"
import fs from "node:fs"
import path from "node:path"

const bench = path.resolve(import.meta.dir, "..")

test("showcase task definitions are valid JSON", () => {
  const s1 = JSON.parse(fs.readFileSync(path.join(bench, "showcase1", "tasks.json"), "utf8"))
  const s2 = JSON.parse(fs.readFileSync(path.join(bench, "showcase2", "tasks.json"), "utf8"))
  expect(s1.sessions.map((s: any) => s.instance_id)).toEqual([
    "pytest-dev__pytest-5262",
    "pytest-dev__pytest-7432",
    "pytest-dev__pytest-7571",
    "pytest-dev__pytest-10051",
  ])
  expect(s2.tasks.map((t: any) => t.instance_id)).toContain("django__django-13964")
  expect(s2.tasks.map((t: any) => t.instance_id)).toContain("django__django-13933")
})

test("opencode prompts are English", () => {
  const promptDir = path.join(bench, "showcase2", "prompts")
  for (const file of fs.readdirSync(promptDir)) {
    const text = fs.readFileSync(path.join(promptDir, file), "utf8")
    expect(text).not.toContain("继续")
    expect(text).not.toContain("修改代码")
    expect(text).not.toContain("始终")
  }
})
