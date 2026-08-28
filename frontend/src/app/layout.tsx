import type { Metadata } from "next";
import { cookies, headers } from "next/headers";
import Script from "next/script";
import "./globals.css";
import "katex/dist/katex.min.css";
import { Toaster } from "@/components/ui/sonner";
import { QueryProvider } from "@/components/providers/QueryProvider";
import { ThemeProvider } from "@/components/providers/ThemeProvider";
import { PrefsHydrator } from "@/components/providers/PrefsHydrator";
import { ErrorBoundary } from "@/components/common/ErrorBoundary";
import { ConnectionGuard } from "@/components/common/ConnectionGuard";
import { themeScript } from "@/lib/theme-script";
import { I18nProvider } from "@/components/providers/I18nProvider";
import { PREFS_COOKIE, parsePrefs } from "@/lib/stores/prefs-cookie";
import { COURSE_PATHNAME_HEADER } from "@/proxy";

export const metadata: Metadata = {
  title: "STEM Course Workbench",
  description: "Source-grounded STEM courses, powered by Open Notebook",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Reading the prefs cookie makes every route render dynamically — required
  // so SSR markup carries the same persisted values the client hydrates with
  // (no hydration mismatch, no flash of default state).
  const cookieStore = await cookies();
  const requestHeaders = await headers();
  const initialPrefs = parsePrefs(cookieStore.get(PREFS_COOKIE)?.value);
  const initialPathname = requestHeaders.get(COURSE_PATHNAME_HEADER) ?? undefined;

  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <Script
          id="open-notebook-theme"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{ __html: themeScript }}
        />
      </head>
      <body className="font-sans">
        <PrefsHydrator initial={initialPrefs}>
          <ErrorBoundary>
            <ThemeProvider>
              <QueryProvider>
                <I18nProvider initialPathname={initialPathname}>
                  <ConnectionGuard initialPathname={initialPathname}>
                    {children}
                    <Toaster />
                  </ConnectionGuard>
                </I18nProvider>
              </QueryProvider>
            </ThemeProvider>
          </ErrorBoundary>
        </PrefsHydrator>
      </body>
    </html>
  );
}
