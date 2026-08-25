import { renderToString } from 'react-dom/server'
import { act, render, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import i18n from '@/lib/i18n'
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

  it('renders a locale-stable shell before client hydration', async () => {
    await i18n.changeLanguage('zh-CN')

    const html = renderToString(
      <I18nProvider initialPathname="/courses"><div>application</div></I18nProvider>
    )

    expect(html).toContain('Loading...')
    expect(html).not.toContain('加载中...')
    await i18n.changeLanguage('en-US')
  })

  it('keeps the document language synchronized after hydration and switching', async () => {
    await act(() => i18n.changeLanguage('zh-CN'))
    const view = render(
      <I18nProvider initialPathname="/courses"><div>application</div></I18nProvider>
    )

    await waitFor(() => expect(document.documentElement.lang).toBe('zh-CN'))
    await act(() => i18n.changeLanguage('pt-BR'))
    await waitFor(() => expect(document.documentElement.lang).toBe('pt-BR'))

    view.unmount()
    await act(() => i18n.changeLanguage('en-US'))
    document.documentElement.lang = 'en'
  })
})
