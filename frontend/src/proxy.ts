import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

export const COURSE_PATHNAME_HEADER = 'x-course-workbench-pathname'

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl

  // Redirect root to notebooks
  if (pathname === '/') {
    return NextResponse.redirect(new URL('/notebooks', request.url))
  }

  const requestHeaders = new Headers(request.headers)
  requestHeaders.set(COURSE_PATHNAME_HEADER, pathname)
  return NextResponse.next({ request: { headers: requestHeaders } })
}

export const config = {
  matcher: [
    '/((?!api|_next/static|_next/image|favicon.ico).*)',
  ],
}
