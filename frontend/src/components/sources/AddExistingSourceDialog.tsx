'use client'

import { useState, useEffect, useMemo, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useDebounce } from 'use-debounce'
import { Search, Link2, LoaderIcon, FileText, Link as LinkIcon, Upload } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { searchApi } from '@/lib/api/search'
import { sourcesApi } from '@/lib/api/sources'
import { useSources, useAddSourcesToNotebook } from '@/lib/hooks/use-sources'
import { SourceListResponse } from '@/lib/types/api'
import { useTranslation } from '@/lib/hooks/use-translation'

interface AddExistingSourceDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  notebookId: string
  onSuccess?: (sourceIds: string[]) => void
  allowedFileExtensions?: readonly string[]
}

export function AddExistingSourceDialog({
  open,
  onOpenChange,
  notebookId,
  onSuccess,
  allowedFileExtensions,
}: AddExistingSourceDialogProps) {
  const { t } = useTranslation()
  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedSearchQuery] = useDebounce(searchQuery, 300)
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([])
  const [filteredSources, setFilteredSources] = useState<SourceListResponse[]>([])
  const [isSearching, setIsSearching] = useState(false)

  // All-sources list via React Query (replaces the manual loadAllSources):
  // lives under the ['sources'] prefix so create/delete invalidations refresh
  // it, and refetches each time the dialog opens (staleTime 0).
  const allSourcesQuery = useQuery({
    queryKey: ['sources', 'all'],
    queryFn: () =>
      sourcesApi.list({
        limit: 100,
        offset: 0,
        sort_by: 'created',
        sort_order: 'desc',
      }),
    enabled: open,
    staleTime: 0,
    retry: false,
  })
  const allSources = useMemo(
    () => allSourcesQuery.data ?? [],
    [allSourcesQuery.data]
  )
  const normalizedExtensions = useMemo(
    () => allowedFileExtensions?.map((extension) => extension.toLowerCase()) ?? [],
    [allowedFileExtensions]
  )
  const isAllowedSource = useCallback((source: SourceListResponse) => {
    if (normalizedExtensions.length === 0) return true
    const filePath = source.asset?.file_path?.toLowerCase()
    return Boolean(filePath && normalizedExtensions.some((extension) => filePath.endsWith(extension)))
  }, [normalizedExtensions])
  const allowedSources = useMemo(
    () => allSources.filter(isAllowedSource),
    [allSources, isAllowedSource]
  )
  const allSourcesById = useMemo(
    () => new Map(allSources.map((source) => [source.id, source])),
    [allSources]
  )
  const isLoadingAll = allSourcesQuery.isPending

  // Get sources already in this notebook
  const { data: currentNotebookSources } = useSources(notebookId)
  const currentSourceIds = useMemo(
    () => new Set(currentNotebookSources?.map(s => s.id) || []),
    [currentNotebookSources]
  )

  const addSources = useAddSourcesToNotebook()

  const performSearch = useCallback(async () => {
    if (!debouncedSearchQuery.trim()) {
      // Empty query - show all sources
      setFilteredSources(allowedSources)
      setIsSearching(false)
      return
    }

    try {
      setIsSearching(true)
      const response = await searchApi.search({
        query: debouncedSearchQuery,
        type: 'text',
        search_sources: true,
        search_notes: false,
        limit: 100,
        minimum_score: 0.01,
      })

      // Since we set search_sources=true and search_notes=false,
      // the API only returns sources, no need to filter
      const sources = response.results
        .map((result): SourceListResponse => allSourcesById.get(result.parent_id) ?? ({
          id: result.parent_id,
          title: result.title || 'Untitled',
          topics: [],
          asset: null,
          embedded: false,
          embedded_chunks: 0,
          insights_count: 0,
          created: result.created,
          updated: result.updated,
        }))
        .filter(isAllowedSource)

      setFilteredSources(sources)
    } catch (error) {
      console.error('Error searching sources:', error)
      // On error, fall back to showing all sources
      setFilteredSources(allowedSources)
    } finally {
      setIsSearching(false)
    }
  }, [debouncedSearchQuery, allowedSources, allSourcesById, isAllowedSource])

  // Filter sources when search query changes
  useEffect(() => {
    if (!debouncedSearchQuery) {
      setFilteredSources(allowedSources)
      setIsSearching(false)
      return
    }

    performSearch()
  }, [debouncedSearchQuery, allowedSources, performSearch])

  const handleToggleSource = (sourceId: string) => {
    setSelectedSourceIds(prev =>
      prev.includes(sourceId)
        ? prev.filter(id => id !== sourceId)
        : [...prev, sourceId]
    )
  }

  const handleAddSelected = async () => {
    if (selectedSourceIds.length === 0) return

    try {
      const linkedSourceIds = [...selectedSourceIds]
      const result = await addSources.mutateAsync({
        notebookId,
        sourceIds: linkedSourceIds,
      })
      if (result.failures > 0) return

      // Reset state
      setSelectedSourceIds([])
      setSearchQuery('')
      onOpenChange(false)
      onSuccess?.(linkedSourceIds)
    } catch (error) {
      // Error handled by the hook's onError
      console.error('Error adding sources:', error)
    }
  }

  const getSourceIcon = (source: SourceListResponse) => {
    // Derive type from asset
    if (source.asset?.url) {
      return <LinkIcon className="h-4 w-4" />
    }
    if (source.asset?.file_path) {
      return <Upload className="h-4 w-4" />
    }
    return <FileText className="h-4 w-4" />
  }

  const formatDate = (dateString: string) => {
    try {
      return new Date(dateString).toLocaleDateString()
    } catch {
      return ''
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl sm:max-w-2xl max-h-[80vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Link2 className="h-5 w-5" />
            {t('sources.addExistingTitle')}
          </DialogTitle>
          <DialogDescription>
            {t('sources.addExistingDesc')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 flex-1 overflow-hidden flex flex-col">
          {/* Search Input */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder={t('sources.searchPlaceholder')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
            {(isSearching || isLoadingAll) && (
              <LoaderIcon className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 animate-spin text-muted-foreground" />
            )}
          </div>

          {/* Source List */}
          <ScrollArea className="h-[400px] border rounded-md">
            {(isSearching || isLoadingAll) && filteredSources.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-[200px] text-muted-foreground">
                <LoaderIcon className="h-12 w-12 mb-2 animate-spin" />
                <p>{t('common.loading')}</p>
              </div>
            ) : filteredSources.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-[200px] text-muted-foreground">
                <FileText className="h-12 w-12 mb-2 opacity-50" />
                <p>{t('sources.noNotebooksFound')}</p>
              </div>
            ) : (
              <div className="space-y-2 p-4">
                {filteredSources.map((source) => {
                  const isAlreadyLinked = currentSourceIds.has(source.id)
                  const isSelected = selectedSourceIds.includes(source.id)

                  return (
                    <div
                      key={source.id}
                      className={`flex items-start gap-3 p-3 rounded-md transition-colors min-w-0 ${
                        isSelected ? 'bg-accent' : 'hover:bg-accent/50'
                      }`}
                    >
                      <Checkbox
                        checked={isSelected}
                        onCheckedChange={() => handleToggleSource(source.id)}
                        disabled={isAlreadyLinked}
                        className="mt-1"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start gap-2 mb-1">
                          <div className="shrink-0 mt-0.5">
                            {getSourceIcon(source)}
                          </div>
                          <h4 className="font-medium text-sm break-words line-clamp-2 flex-1 min-w-0">
                            {source.title}
                          </h4>
                          {isAlreadyLinked && (
                            <Badge variant="secondary" className="text-xs shrink-0">
                              {t('common.linked')}
                            </Badge>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground truncate">
                          {t('sources.added', { date: formatDate(source.created) })}
                        </p>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </ScrollArea>

          {/* Truncation Warning */}
          {allowedSources.length >= 100 && !debouncedSearchQuery && (
            <p className="text-xs text-muted-foreground">
              {t('sources.showingFirst100')}
            </p>
          )}

          {/* Selection Summary */}
          {selectedSourceIds.length > 0 && (
            <div className="text-sm text-muted-foreground">
              {t('sources.selectedCount', { count: selectedSourceIds.length })}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={addSources.isPending}
          >
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleAddSelected}
            disabled={selectedSourceIds.length === 0 || addSources.isPending}
          >
            {addSources.isPending ? (
              <>
                <LoaderIcon className="mr-2 h-4 w-4 animate-spin" />
                {t('common.adding')}
              </>
            ) : (
              <>{t('common.addSelected')}</>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
