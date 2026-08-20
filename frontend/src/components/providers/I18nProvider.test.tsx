import { renderToString } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { I18nProvider } from './I18nProvider'

describe('I18nProvider SSR shell', () => {
  it('preserves the exact launcher marker for the new-course route', () => {
    const html = renderToString(
      <I18nProvider initialPathname="/courses/new"><div>application</div></I18nProvider>
    )
    expect(html).toContain('data-course-workbench-ready="new-course"')

    const otherRoute = renderToString(
      <I18nProvider initialPathname="/courses"><div>application</div></I18nProvider>
    )
    expect(otherRoute).not.toContain('data-course-workbench-ready="new-course"')
  })
})
