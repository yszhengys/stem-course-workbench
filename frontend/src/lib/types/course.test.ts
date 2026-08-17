import { describe, expect, it } from 'vitest'

import {
  courseModelOptionsSchema,
  courseOutlineArtifactSchema,
  courseSchema,
  eligibleCourseSourceSchema,
} from './course'

describe('Course V2 contracts', () => {
  it('parses record-based Course and outline version fields', () => {
    const course = courseSchema.parse({
      id: 'course:calculus',
      title: '微积分',
      notebook: 'notebook:calculus',
      subject: 'math',
      description: null,
      language: 'zh-CN',
      status: 'outline_ready',
      source_ids: ['source:textbook'],
      primary_source_ids: ['source:textbook'],
      supplement_source_ids: [],
      outline_version_id: 'course_version:v1',
      error_message: null,
      outline: null,
      config: null,
      created: '2026-08-18T00:00:00Z',
      updated: '2026-08-18T00:00:00Z',
    })

    expect(course.notebook).toBe('notebook:calculus')
    expect(course.outline_version_id).toBe('course_version:v1')
  })

  it('rejects malformed outline graphs and unknown fields', () => {
    expect(() => courseOutlineArtifactSchema.parse({
      title: 'Outline',
      chapters: [{
        key: 'limits',
        title: 'Limits',
        purpose: 'Learn limits',
        prerequisite_keys: [],
        objective_keys: ['continuity'],
        anchor_ids: ['anchor:one'],
        lab_keys: [],
      }],
      concepts: [{ key: 'continuity', label: 'Continuity', anchor_ids: ['anchor:one'] }],
      dependency_edges: [],
      injected: true,
    })).toThrow()
  })

  it('does not accept a server path in eligible Source metadata', () => {
    expect(() => eligibleCourseSourceSchema.parse({
      source_id: 'source:one',
      title: 'Book',
      filename: 'book.pdf',
      kind: 'pdf',
      role: null,
      associated: false,
      file_path: '/private/book.pdf',
    })).toThrow()
  })

  it('keeps an unconfigured model visible but unselectable', () => {
    const result = courseModelOptionsSchema.parse({
      defaults: {
        outline: { adapter: 'codex_cli', model: 'gpt-5.6-sol', reasoning_effort: 'max' },
      },
      options: [{
        adapter: 'open_notebook',
        model: null,
        display_name: 'deepseek-v4-pro',
        reasoning_effort: null,
        optional: true,
        configured: false,
        selectable: false,
      }],
    })

    expect(result.options[0].model).toBeNull()
    expect(result.options[0].selectable).toBe(false)
  })
})
