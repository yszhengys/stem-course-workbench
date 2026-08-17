import type { Metadata } from "next";
import {
  Bricolage_Grotesque,
  Instrument_Sans,
  Spline_Sans_Mono,
} from "next/font/google";
import { cookies } from "next/headers";
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

const instrumentSans = Instrument_Sans({
  subsets: ["latin"],
  variable: "--font-instrument-sans",
});

const bricolageGrotesque = Bricolage_Grotesque({
  subsets: ["latin"],
  weight: ["600", "700"],
  variable: "--font-bricolage",
});

const splineSansMono = Spline_Sans_Mono({
  subsets: ["latin"],
  variable: "--font-spline-mono",
});

export const metadata: Metadata = {
  title: "Open Notebook",
  description: "Privacy-focused research and knowledge management",
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
  const initialPrefs = parsePrefs(cookieStore.get(PREFS_COOKIE)?.value);

  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body
        className={`${instrumentSans.variable} ${bricolageGrotesque.variable} ${splineSansMono.variable} font-sans`}
      >
        <PrefsHydrator initial={initialPrefs}>
          <ErrorBoundary>
            <ThemeProvider>
              <QueryProvider>
                <I18nProvider>
                  <ConnectionGuard>
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
