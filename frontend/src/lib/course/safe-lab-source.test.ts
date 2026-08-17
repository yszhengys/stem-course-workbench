import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('safe Lab source boundary', () => {
  it('uses mathjs only for parsing and never invokes an execution primitive', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/lib/course/safe-lab.ts'), 'utf8')

    expect(source).toMatch(/\bparse\(expression\)/)
    expect(source).not.toMatch(/\.evaluate\s*\(/)
    expect(source).not.toMatch(/\.compile\s*\(/)
    expect(source).not.toMatch(/\beval\s*\(/)
    expect(source).not.toMatch(/new\s+Function\b/)
  })
})
