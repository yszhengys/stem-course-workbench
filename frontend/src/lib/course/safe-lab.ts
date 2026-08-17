import math from 'mathjs/lib/browser/math.js'
import type {
  ConstantNode,
  FunctionNode,
  MathNode,
  OperatorNode,
  ParenthesisNode,
  SymbolNode,
} from 'mathjs'

import { labSpecSchema, type LabSpec } from '@/lib/types/course'

const MAX_ABSOLUTE_RESULT = 1_000_000_000_000
export const MAX_TOTAL_LAB_SAMPLES = 1000

const FUNCTIONS: Record<string, (...values: number[]) => number> = {
  sin: Math.sin,
  cos: Math.cos,
  tan: Math.tan,
  sqrt: Math.sqrt,
  abs: Math.abs,
  exp: Math.exp,
  log: Math.log,
  floor: Math.floor,
  ceil: Math.ceil,
  min: Math.min,
  max: Math.max,
}

function finite(value: number): number {
  if (!Number.isFinite(value)) throw new Error('Expression result must be finite')
  if (Math.abs(value) > MAX_ABSOLUTE_RESULT) throw new Error('Expression result is outside the safe range')
  return value
}

const BINARY_OPERATORS: Record<string, (left: number, right: number) => number> = {
  '+': (left, right) => left + right,
  '-': (left, right) => left - right,
  '*': (left, right) => left * right,
  '/': (left, right) => left / right,
  '^': (left, right) => left ** right,
}

function interpretNode(node: MathNode, variables: Record<string, number>): number {
  switch (node.type) {
    case 'ConstantNode': {
      const value = (node as ConstantNode).value
      if (typeof value !== 'number') throw new Error('Only finite numeric constants are supported')
      return finite(value)
    }
    case 'SymbolNode': {
      const name = (node as SymbolNode).name
      if (name === 'pi') return Math.PI
      if (name === 'e') return Math.E
      if (!Object.prototype.hasOwnProperty.call(variables, name)) {
        throw new Error(`Unknown symbol: ${name}`)
      }
      return finite(variables[name])
    }
    case 'ParenthesisNode':
      return interpretNode((node as ParenthesisNode).content, variables)
    case 'OperatorNode': {
      const operation = node as OperatorNode
      if (operation.implicit) throw new Error('Implicit operators are not supported')
      if ((operation.op === '+' || operation.op === '-') && operation.args.length === 1) {
        const value = interpretNode(operation.args[0], variables)
        return finite(operation.op === '-' ? -value : value)
      }
      const implementation = BINARY_OPERATORS[operation.op]
      if (!implementation || operation.args.length !== 2) {
        throw new Error(`Unsupported operator: ${operation.op}`)
      }
      return finite(implementation(
        interpretNode(operation.args[0], variables),
        interpretNode(operation.args[1], variables),
      ))
    }
    case 'FunctionNode': {
      const functionNode = node as FunctionNode
      if (functionNode.fn.type !== 'SymbolNode') {
        throw new Error('Only direct allowlisted function calls are supported')
      }
      const name = (functionNode.fn as SymbolNode).name
      const implementation = FUNCTIONS[name]
      if (!implementation) throw new Error(`Unsupported function: ${name}`)
      const values = functionNode.args.map((argument) => interpretNode(argument, variables))
      if (values.length === 0 || ((name !== 'min' && name !== 'max') && values.length !== 1)) {
        throw new Error(`Invalid argument count for ${name}`)
      }
      return finite(implementation(...values))
    }
    // Assignment, function assignment, accessors/properties, arrays, objects,
    // blocks, conditionals, ranges, indices and every future node type fail closed.
    default:
      throw new Error(`Unsupported expression node: ${node.type}`)
  }
}

export function evaluateSafeExpression(expression: string, variables: Record<string, number>): number {
  if (!expression || expression.length > 500) throw new Error('Expression length is invalid')
  const normalizedVariables = Object.fromEntries(
    Object.entries(variables).map(([key, value]) => [key, finite(value)])
  )
  return interpretNode(math.parse(expression), normalizedVariables)
}

function defaultVariables(spec: LabSpec, controlValues: Record<string, number> = {}) {
  const variables: Record<string, number> = {}
  for (const [key, [minimum, maximum]] of Object.entries(spec.domain)) {
    variables[key] = (minimum + maximum) / 2
  }
  for (const control of spec.controls) {
    variables[control.key] = controlValues[control.key] ?? control.value
  }
  return variables
}

export function validateLabSpec(value: unknown): LabSpec {
  const spec = labSpecSchema.parse(value)
  const variables = defaultVariables(spec)
  for (const expression of spec.expressions) evaluateSafeExpression(expression, variables)
  return spec
}

export interface LabPoint {
  x: number
  y: number
}

export interface SampledLab {
  paths: LabPoint[][]
  points: LabPoint[]
  totalSamples: number
}

function domain(spec: LabSpec, variable: string, fallback: [number, number]): [number, number] {
  return spec.domain[variable] ?? fallback
}

function sampleCurve(
  count: number,
  bounds: [number, number],
  pointAt: (value: number) => LabPoint,
): LabPoint[] {
  const points: LabPoint[] = []
  for (let index = 0; index < count; index += 1) {
    const value = bounds[0] + ((bounds[1] - bounds[0]) * index) / Math.max(1, count - 1)
    try {
      const point = pointAt(value)
      if (Number.isFinite(point.x) && Number.isFinite(point.y)) points.push(point)
    } catch {
      // Singular samples are omitted; expressions were already structurally validated.
    }
  }
  return points
}

export function sampleLab(spec: LabSpec, controlValues: Record<string, number> = {}): SampledLab {
  const variables = defaultVariables(spec, controlValues)
  const paths: LabPoint[][] = []
  const points: LabPoint[] = []
  let totalSamples = 0

  if (spec.kind === 'function_plot') {
    const count = Math.max(2, Math.min(250, Math.floor(MAX_TOTAL_LAB_SAMPLES / Math.max(1, spec.expressions.length))))
    for (const expression of spec.expressions) {
      const xBounds = domain(spec, 'x', [-10, 10])
      paths.push(sampleCurve(count, xBounds, (x) => ({
        x,
        y: evaluateSafeExpression(expression, { ...variables, x }),
      })))
      totalSamples += count
    }
  } else if (spec.kind === 'parametric_curve' || spec.kind === 'kinematics') {
    if (spec.expressions.length < 2) throw new Error('This Lab requires two expressions')
    const count = Math.min(500, MAX_TOTAL_LAB_SAMPLES)
    const tBounds = domain(spec, 't', [0, 10])
    paths.push(sampleCurve(count, tBounds, (t) => ({
      x: evaluateSafeExpression(spec.expressions[0], { ...variables, t }),
      y: evaluateSafeExpression(spec.expressions[1], { ...variables, t }),
    })))
    totalSamples = count
  } else if (spec.kind === 'vector_field') {
    if (spec.expressions.length < 2) throw new Error('A vector field requires two expressions')
    const side = 16
    const [xMin, xMax] = domain(spec, 'x', [-5, 5])
    const [yMin, yMax] = domain(spec, 'y', [-5, 5])
    for (let row = 0; row < side; row += 1) {
      for (let column = 0; column < side; column += 1) {
        const x = xMin + ((xMax - xMin) * column) / (side - 1)
        const y = yMin + ((yMax - yMin) * row) / (side - 1)
        const dx = evaluateSafeExpression(spec.expressions[0], { ...variables, x, y })
        const dy = evaluateSafeExpression(spec.expressions[1], { ...variables, x, y })
        const magnitude = Math.hypot(dx, dy) || 1
        paths.push([{ x, y }, { x: x + dx / magnitude * 0.3, y: y + dy / magnitude * 0.3 }])
      }
    }
    totalSamples = side * side
  } else {
    for (const object of spec.objects) {
      if (object.type === 'point' && typeof object.x === 'number' && typeof object.y === 'number') {
        points.push({ x: finite(object.x), y: finite(object.y) })
      } else if (
        object.type === 'segment' &&
        typeof object.x1 === 'number' && typeof object.y1 === 'number' &&
        typeof object.x2 === 'number' && typeof object.y2 === 'number'
      ) {
        paths.push([
          { x: finite(object.x1), y: finite(object.y1) },
          { x: finite(object.x2), y: finite(object.y2) },
        ])
      } else {
        throw new Error('Unsupported geometry object')
      }
    }
    totalSamples = points.length + paths.reduce((total, path) => total + path.length, 0)
  }

  if (totalSamples > MAX_TOTAL_LAB_SAMPLES) throw new Error('Lab sample limit exceeded')
  return { paths, points, totalSamples }
}
