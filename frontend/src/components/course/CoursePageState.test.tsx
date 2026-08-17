import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { CoursePageNotFound } from './CoursePageState'

describe('Course page states', () => {
  it('renders an explicit visible 404 state', () => {
    render(<CoursePageNotFound />)
    expect(screen.getByText('course.notFoundTitle')).toBeVisible()
    expect(screen.getByText('course.notFoundDescription')).toBeVisible()
  })
})
