'use client'

import { FormEvent, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ArrowLeft, GraduationCap } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useCreateCourse } from '@/lib/hooks/use-courses'
import { useTranslation } from '@/lib/hooks/use-translation'

export default function NewCoursePage() {
  const { t } = useTranslation()
  const router = useRouter()
  const createCourse = useCreateCourse()
  const [title, setTitle] = useState('')
  const [subject, setSubject] = useState('math')
  const [description, setDescription] = useState('')
  const [language, setLanguage] = useState('zh-CN')

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const normalizedTitle = title.trim()
    if (!normalizedTitle) return
    const course = await createCourse.mutateAsync({
      title: normalizedTitle,
      subject,
      description: description.trim() || null,
      language,
    })
    router.push(`/courses/${encodeURIComponent(course.id)}/outline`)
  }

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div
          className="mx-auto max-w-3xl space-y-6 p-6"
          data-testid="new-course-ready"
          data-course-workbench-ready="new-course"
        >
          <Button asChild variant="ghost" size="sm">
            <Link href="/courses">
              <ArrowLeft className="mr-2 h-4 w-4" />
              {t('course.backToCourses')}
            </Link>
          </Button>

          <Card>
            <CardHeader>
              <div className="mb-2 flex size-10 items-center justify-center rounded-md bg-fern-tint text-fern">
                <GraduationCap className="size-5" />
              </div>
              <CardTitle className="font-display text-2xl">{t('course.newTitle')}</CardTitle>
              <CardDescription>{t('course.newDescription')}</CardDescription>
            </CardHeader>
            <CardContent>
              <form className="space-y-5" onSubmit={handleSubmit}>
                <div className="space-y-2">
                  <Label htmlFor="course-title">{t('course.titleLabel')}</Label>
                  <Input
                    id="course-title"
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    required
                    autoFocus
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="course-subject">{t('course.subjectLabel')}</Label>
                  <select
                    id="course-subject"
                    value={subject}
                    onChange={(event) => setSubject(event.target.value)}
                    className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
                  >
                    <option value="math">{t('course.subjectMath')}</option>
                    <option value="physics">{t('course.subjectPhysics')}</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="course-language">{t('course.contentLanguage')}</Label>
                  <select
                    id="course-language"
                    value={language}
                    onChange={(event) => setLanguage(event.target.value)}
                    className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
                  >
                    <option value="zh-CN">简体中文</option>
                    <option value="en-US">English</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="course-description">{t('common.description')}</Label>
                  <Textarea
                    id="course-description"
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                    rows={4}
                  />
                </div>

                <Button type="submit" disabled={!title.trim() || createCourse.isPending}>
                  {createCourse.isPending ? t('common.creating') : t('course.create')}
                </Button>
              </form>
            </CardContent>
          </Card>
        </div>
      </div>
    </AppShell>
  )
}
