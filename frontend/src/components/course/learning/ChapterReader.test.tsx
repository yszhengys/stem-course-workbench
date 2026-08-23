import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ChapterReader } from './ChapterReader'

vi.mock('@/components/ui/markdown-renderer', () => ({
  MarkdownRenderer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

const artifact = {
  purpose: 'Understand limits.',
  prerequisites: [],
  objectives: ['Evaluate limits'],
  definitions: [],
  sections: [{
    block_key: 'limit-definition', title: 'Definition', markdown: 'Grounded.',
    anchor_ids: ['anchor:one'], provenance: 'adapted' as const,
  }],
  formulas: [], worked_examples: [], misconceptions: [], pitfalls: [],
  quick_reference: [], citations: ['anchor:one'],
}

describe('ChapterReader reading position', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('records a focusable section when it becomes meaningfully visible', () => {
    const onPosition = vi.fn()
    let callback: IntersectionObserverCallback | undefined
    const disconnect = vi.fn()
    const observe = vi.fn()
    vi.stubGlobal('IntersectionObserver', class {
      constructor(next: IntersectionObserverCallback) {
        callback = next
      }
      observe = observe
      disconnect = disconnect
      unobserve = vi.fn()
      takeRecords = vi.fn(() => [])
      root = null
      rootMargin = '0px'
      thresholds = [0.6]
    })

    render(<ChapterReader artifact={artifact} labs={[]} onPosition={onPosition} />)
    const section = screen.getByRole('region', { name: 'Definition' })
    expect(section).toHaveAttribute('tabindex', '0')
    expect(observe).toHaveBeenCalledWith(section)

    callback?.([{
      target: section,
      isIntersecting: true,
      intersectionRatio: 0.7,
    } as unknown as IntersectionObserverEntry], {} as IntersectionObserver)
    expect(onPosition).toHaveBeenCalledWith('limit-definition')

    fireEvent.focus(section)
    expect(onPosition).toHaveBeenCalledTimes(2)
  })
})
