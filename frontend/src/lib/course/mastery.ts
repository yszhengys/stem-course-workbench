import type {
  ConceptMastery,
  CourseExercise,
  CourseLearningOverview,
  GradeResult,
  MasteryStatus,
} from '@/lib/types/course'

type Translate = (key: string) => string

const MASTERY_KEYS: Record<MasteryStatus, string> = {
  not_started: 'course.masteryNotStarted',
  learning: 'course.masteryLearning',
  practiced: 'course.masteryPracticed',
  mastered: 'course.masteryMastered',
  review_due: 'course.masteryReviewDue',
}

const MASTERY_REASON_KEYS: Record<MasteryStatus, string> = {
  not_started: 'course.masteryReasonNotStarted',
  learning: 'course.masteryReasonLearning',
  practiced: 'course.masteryReasonPracticed',
  mastered: 'course.masteryReasonMastered',
  review_due: 'course.masteryReasonReviewDue',
}

const EXERCISE_TYPE_KEYS: Record<string, string> = {
  worked_source: 'course.difficultyCore',
  source_practice: 'course.difficultyCore',
  generated_core: 'course.difficultyCore',
  generated_challenge: 'course.difficultyChallenge',
  transfer: 'course.transferTask',
}

const GRADE_KEYS: Record<GradeResult['feedback_code'], string> = {
  correct: 'course.gradeCorrect',
  incorrect: 'course.gradeIncorrect',
  invalid_answer: 'course.gradeInvalid',
  advisory: 'course.gradeAdvisory',
}

export function selectResumeChapter(
  overview: CourseLearningOverview,
): CourseLearningOverview['chapters'][number] | undefined {
  const positioned = overview.chapters
    .filter((chapter) => chapter.latest_position !== null)
    .sort((left, right) => {
      const leftTime = Date.parse(left.latest_position?.occurred_at ?? '')
      const rightTime = Date.parse(right.latest_position?.occurred_at ?? '')
      return rightTime - leftTime
    })
  if (positioned[0]) return positioned[0]

  const dueChapterKey = overview.review_queue[0]?.chapter_key
  const dueChapter = overview.chapters.find(
    (chapter) => chapter.chapter_key === dueChapterKey,
  )
  if (dueChapter) return dueChapter

  const masteriesByChapter = new Map<string, ConceptMastery[]>()
  for (const mastery of overview.masteries) {
    const current = masteriesByChapter.get(mastery.chapter_key) ?? []
    current.push(mastery)
    masteriesByChapter.set(mastery.chapter_key, current)
  }
  return overview.chapters.find((chapter) => {
    const masteries = masteriesByChapter.get(chapter.chapter_key) ?? []
    return masteries.length === 0 || masteries.some(
      (mastery) => mastery.status !== 'mastered',
    )
  }) ?? overview.chapters[0]
}

export const masteryStatusLabel = (t: Translate, status: MasteryStatus) =>
  t(MASTERY_KEYS[status])

export const masteryReasonLabel = (t: Translate, mastery: ConceptMastery) =>
  t(MASTERY_REASON_KEYS[mastery.status])

export const exerciseTypeLabel = (t: Translate, value: string) =>
  t(EXERCISE_TYPE_KEYS[value] ?? 'course.statusUnknown')

export const gradeFeedbackLabel = (t: Translate, grade: GradeResult) =>
  t(GRADE_KEYS[grade.feedback_code])

export function conceptDisplayName(conceptKey: string) {
  const readable = conceptKey.replace(/[-_]+/g, ' ')
  return readable.charAt(0).toLocaleUpperCase() + readable.slice(1)
}

export function conceptLabel(
  overview: CourseLearningOverview,
  conceptKey: string,
): string | undefined {
  return overview.concepts.find((concept) => concept.key === conceptKey)?.label
}

const MASTERY_RANK: Record<MasteryStatus, number> = {
  not_started: 0,
  review_due: 0,
  learning: 1,
  practiced: 2,
  mastered: 3,
}

export function selectExerciseConcept(
  exercise: CourseExercise,
  overview: CourseLearningOverview,
): string | undefined {
  const exerciseConcepts = new Set(exercise.concept_keys)
  const due = overview.review_queue
    .filter((item) => (
      item.chapter_key === exercise.chapter_key
      && exerciseConcepts.has(item.concept_key)
    ))
    .sort((left, right) => (
      Date.parse(left.due_at) - Date.parse(right.due_at)
      || left.concept_key.localeCompare(right.concept_key)
    ))[0]
  if (due) return due.concept_key

  return [...exercise.concept_keys]
    .sort((left, right) => {
      const leftStatus = overview.masteries.find((mastery) => (
        mastery.chapter_key === exercise.chapter_key
        && mastery.concept_key === left
      ))?.status ?? 'not_started'
      const rightStatus = overview.masteries.find((mastery) => (
        mastery.chapter_key === exercise.chapter_key
        && mastery.concept_key === right
      ))?.status ?? 'not_started'
      return MASTERY_RANK[leftStatus] - MASTERY_RANK[rightStatus]
        || left.localeCompare(right)
    })[0]
}
