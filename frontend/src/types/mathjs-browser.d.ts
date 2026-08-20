declare module 'mathjs/lib/browser/math.js' {
  import type { MathNode } from 'mathjs'

  const math: {
    parse(expression: string): MathNode
  }

  export default math
}
