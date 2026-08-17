import {
  isSelectableModel,
  type CourseModelOption,
  type ModelSelection,
} from '@/lib/types/course'

export function selectableDefaultModel(
  options: CourseModelOption[],
  preferred: ModelSelection | undefined,
): ModelSelection | null {
  if (!preferred) return null
  const matching = options.find(
    (option) => option.adapter === preferred.adapter && option.model === preferred.model
  )
  return matching && isSelectableModel(matching) ? preferred : null
}
