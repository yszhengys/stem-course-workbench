import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

import {
  CHAPTER_KEY,
  COURSE_PATH_ID,
  installCourseApi,
} from './support/course-api'

async function expectNoAxeViolations(page: Page): Promise<void> {
  const results = await new AxeBuilder({ page })
    .withTags([
      'wcag2a',
      'wcag2aa',
      'wcag21a',
      'wcag21aa',
      'wcag22a',
      'wcag22aa',
    ])
    .analyze()
  const violations = results.violations.map((violation) => ({
    id: violation.id,
    impact: violation.impact,
    help: violation.help,
    targets: violation.nodes.map((node) => node.target),
  }))
  expect(violations).toEqual([])
}

test.describe('Course product-route keyboard and accessibility gate', () => {
  test('creates a course by keyboard on the real new-course form', async ({ page }) => {
    const api = await installCourseApi(page)
    await page.goto('/courses/new')

    await expect(page.getByTestId('new-course-ready')).toBeVisible()
    await expectNoAxeViolations(page)

    const title = page.getByLabel('Course title')
    await expect(title).toBeFocused()
    await page.keyboard.type('Keyboard physics')

    await page.keyboard.press('Tab')
    const subject = page.getByLabel('Subject')
    await expect(subject).toBeFocused()
    await page.keyboard.press('p')
    await expect(subject).toHaveValue('physics')

    await page.keyboard.press('Tab')
    const language = page.getByLabel('Course content language')
    await expect(language).toBeFocused()
    await page.keyboard.press('e')
    await expect(language).toHaveValue('en-US')

    await page.keyboard.press('Tab')
    const description = page.getByLabel('Description')
    await expect(description).toBeFocused()
    await page.keyboard.type('Created without a pointer device.')

    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: 'Create course' })).toBeFocused()
    await page.keyboard.press('Enter')

    await expect.poll(() => api.createRequests.length).toBe(1)
    expect(api.createRequests[0]).toEqual({
      title: 'Keyboard physics',
      subject: 'physics',
      description: 'Created without a pointer device.',
      language: 'en-US',
    })
  })

  test('opens the published chapter from Learn overview by keyboard', async ({ page }) => {
    const api = await installCourseApi(page)
    await page.goto(`/courses/${COURSE_PATH_ID}/learn`)

    await expect(page.getByRole('heading', { level: 1, name: 'Accessible vector mechanics' }))
      .toBeVisible()
    await expectNoAxeViolations(page)

    const chapterLink = page.getByRole('link', { name: 'Vectors' })
    await chapterLink.focus()
    await expect(chapterLink).toBeFocused()
    await page.keyboard.press('Enter')

    await expect(page).toHaveURL(new RegExp(`/learn/${CHAPTER_KEY}$`))
    await expect(page.getByRole('heading', { level: 1, name: 'Vectors' })).toBeVisible()
    expect(api.unknownRequests).toEqual([])
  })

  test('uses Lab, hint, reveal dialog, and deterministic answer by keyboard', async ({ page }) => {
    const api = await installCourseApi(page)
    await page.goto(`/courses/${COURSE_PATH_ID}/learn/${CHAPTER_KEY}`)

    await expect(page.getByRole('heading', { level: 1, name: 'Vectors' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Slope explorer' })).toBeVisible()
    await expectNoAxeViolations(page)

    const slider = page.getByRole('slider', { name: 'Slope' })
    await slider.focus()
    await expect(slider).toHaveValue('1')
    await page.keyboard.press('ArrowRight')
    await expect(slider).toHaveValue('2')
    await page.keyboard.press('Tab')
    await expect(page.getByRole('region', { name: 'Scrollable visualization data' }))
      .toBeFocused()

    const nextHint = page.getByRole('button', { name: 'Show next hint' })
    await nextHint.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByText('Substitute the initial velocity, acceleration, and time.'))
      .toBeVisible()

    const reveal = page.getByRole('button', { name: 'Reveal answer', exact: true })
    await reveal.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('alertdialog', { name: 'Reveal the complete answer?' }))
      .toBeVisible()
    const cancel = page.getByRole('button', { name: 'Cancel' })
    await expect(cancel).toBeFocused()
    await expectNoAxeViolations(page)

    await page.keyboard.press('Escape')
    await expect(reveal).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(cancel).toBeFocused()
    await page.keyboard.press('Tab')
    const confirm = page.getByRole('button', { name: 'Confirm and reveal' })
    await expect(confirm).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByText('Answer revealed', { exact: true })).toBeVisible()

    const answer = page.getByLabel('Your exercise answer')
    await answer.focus()
    await page.keyboard.type('12')
    const checkAnswer = page.getByRole('button', { name: 'Check answer' })
    await checkAnswer.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByText('Correct', { exact: true })).toBeVisible()

    await expectNoAxeViolations(page)
    expect(api.unknownRequests).toEqual([])
  })
})
