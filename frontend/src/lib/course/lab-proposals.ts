const LAB_KINDS = [
  'function-plot',
  'parametric-curve',
  'vector-field',
  'geometry',
  'kinematics',
] as const

export const SAFE_LAB_PROPOSAL_KEYS = LAB_KINDS.flatMap((kind) =>
  [1, 2, 3, 4].map((index) => `${kind}-${index}`)
)
