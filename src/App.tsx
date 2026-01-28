import { useState, useRef, useEffect, useMemo, useCallback } from 'react';
import { CanvasTimeline, type CanvasTimelineRef } from './CanvasTimeline';
import type { TimelineEntry, GroupingMode, SourceReference } from './types';

// Load data from JSON files
import timelineEntriesData from '../data/timeline_entries.json';

const timelineEntries = timelineEntriesData as TimelineEntry[];

// --- Lazy chapter loading ---

const chapterModules = import.meta.glob<string>('/data/chapters/chapter_*.txt', { query: '?raw', import: 'default' });
const chapterCache = new Map<number, string[]>();

async function loadChapter(chapter: number): Promise<string[]> {
  const cached = chapterCache.get(chapter);
  if (cached) return cached;

  const key = `/data/chapters/chapter_${String(chapter).padStart(2, '0')}.txt`;
  const loader = chapterModules[key];
  if (!loader) return [];

  const raw = await loader();
  const lines = raw.split('\n');
  chapterCache.set(chapter, lines);
  return lines;
}

function formatDate(dateSpec: TimelineEntry['date_start']): string {
  const year = dateSpec.year;
  const sign = dateSpec.era === 'BCE' ? '-' : '';
  const circa = dateSpec.circa ? '~' : '';
  return `${circa}${sign}${Math.abs(year)}`;
}

// --- Icon components ---

function IconCopy({ size = 14 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>
      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>
    </svg>
  );
}

function IconClose({ size = 16 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 6 6 18"/><path d="m6 6 12 12"/>
    </svg>
  );
}

function IconSearch({ size = 16 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
    </svg>
  );
}

function IconChevronLeft({ size = 16 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m15 18-6-6 6-6"/>
    </svg>
  );
}

function IconChevronRight({ size = 16 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m9 18 6-6-6-6"/>
    </svg>
  );
}

function IconChevronDown({ size = 14 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m6 9 6 6 6-6"/>
    </svg>
  );
}

function IconCheck({ size = 14 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12"/>
    </svg>
  );
}

function IconTag({ size = 16 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2H2v10l9.29 9.29c.94.94 2.48.94 3.42 0l6.58-6.58c.94-.94.94-2.48 0-3.42L12 2Z"/>
      <path d="M7 7h.01"/>
    </svg>
  );
}

function IconFit({ size = 18 }: { size?: number }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
    </svg>
  );
}

// --- Reusable components ---

async function copyToClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Clipboard access can fail when page is unfocused or permissions are denied
  }
}

function CopyButton({ text, label }: { text: string; label: string }) {
  return (
    <button
      onClick={() => copyToClipboard(text)}
      className="p-1 text-foreground-muted hover:text-foreground hover:bg-surface-hover rounded transition-colors"
      title={`Copier ${label}`}
    >
      <IconCopy />
    </button>
  );
}

function SourceReaderModal({ source, onClose }: { source: SourceReference; onClose: () => void }) {
  const [lines, setLines] = useState<string[] | null>(null);
  const highlightRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    loadChapter(source.chapter).then(setLines);
  }, [source.chapter]);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  useEffect(() => {
    if (lines && highlightRef.current) {
      highlightRef.current.scrollIntoView({ block: 'center' });
    }
  }, [lines]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60" />
      <div
        className="relative w-full max-w-2xl max-h-[80vh] bg-surface-secondary border border-border rounded-lg shadow-2xl flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-border">
          <span className="text-sm font-medium text-foreground">
            Chapitre {source.chapter}
          </span>
          <button
            onClick={onClose}
            className="p-1 text-foreground-muted hover:text-foreground hover:bg-surface-hover rounded transition-colors"
            title="Fermer (Esc)"
          >
            <IconClose />
          </button>
        </div>
        {/* Body */}
        <div className="overflow-y-auto p-5 text-base leading-relaxed font-serif">
          {!lines ? (
            <p className="text-foreground-muted">Chargement...</p>
          ) : (
            <>
              {lines.slice(2).map((line, i) => {
                const lineNum = i + 3;
                const isHighlighted = lineNum >= source.line_start && lineNum <= source.line_end;
                return (
                  <div
                    key={lineNum}
                    ref={isHighlighted && lineNum === source.line_start ? highlightRef : undefined}
                    className={
                      isHighlighted
                        ? 'bg-accent/5 border-l border-accent/40 pl-3 -ml-3.5 text-foreground'
                        : 'text-foreground-secondary'
                    }
                  >
                    {line || '\u00A0'}
                  </div>
                );
              })}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function getCommonsUrl(imageUrl: string): string | undefined {
  const parts = new URL(imageUrl).pathname.split('/');
  const filename = parts.includes('thumb') ? parts[parts.length - 2] : parts[parts.length - 1];
  return filename
    ? `https://commons.wikimedia.org/wiki/File:${decodeURIComponent(filename)}`
    : undefined;
}

// Importance level configuration (5 = highest, 1 = lowest)
const importanceLevels = [
  { level: 5, label: 'Fondateur', color: 'var(--color-importance-5)' },
  { level: 4, label: 'Majeur', color: 'var(--color-importance-4)' },
  { level: 3, label: 'Significatif', color: 'var(--color-importance-3)' },
  { level: 2, label: 'Mineur', color: 'var(--color-importance-2)' },
  { level: 1, label: 'Contexte', color: 'var(--color-importance-1)' },
] as const;

const groupModes: { key: GroupingMode; label: string; title: string }[] = [
  { key: 'dynasty', label: 'Règnes', title: 'Grouper par règne / dynastie' },
  { key: 'era', label: 'Chapitres', title: 'Grouper par chapitre' },
];

function App() {
  const [groupingMode, setGroupingMode] = useState<GroupingMode>('dynasty');
  const [selectedEntry, setSelectedEntry] = useState<TimelineEntry | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [minImportance, setMinImportance] = useState<number>(5);
  const [importanceDropdownOpen, setImportanceDropdownOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeTags, setActiveTags] = useState<Set<string>>(new Set());
  const [searchExpanded, setSearchExpanded] = useState(false);
  const [tagCloudOpen, setTagCloudOpen] = useState(false);
  const [sourceReaderOpen, setSourceReaderOpen] = useState(false);
  const [timelineReady, setTimelineReady] = useState(false);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const canvasTimelineRef = useRef<CanvasTimelineRef>(null);
  const sortedEntriesRef = useRef<TimelineEntry[]>([]);
  const selectedIndexRef = useRef<number | null>(null);
  const navigateRef = useRef<(index: number) => void>(() => {});

  const toggleTag = useCallback((tag: string) => {
    setActiveTags(prev => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }, []);

  // Combined filter: importance + tags + search
  const filteredEntries = useMemo(() => {
    const query = searchQuery.toLowerCase().trim();
    return timelineEntries.filter(e => {
      if ((e.importance || 3) < minImportance) return false;
      if (activeTags.size > 0) {
        if (!e.tags?.some(t => activeTags.has(t))) return false;
      }
      if (query) {
        const haystack = [
          e.title,
          e.description,
          ...(e.people || []),
          ...(e.locations || []),
        ].join(' ').toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    });
  }, [minImportance, activeTags, searchQuery]);

  // All tags with counts (from importance-filtered entries, ignoring active tag filter)
  const allTags = useMemo(() => {
    const counts = new Map<string, number>();
    for (const e of timelineEntries) {
      if ((e.importance || 3) < minImportance) continue;
      for (const t of e.tags || []) {
        counts.set(t, (counts.get(t) || 0) + 1);
      }
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([tag, count]) => ({ tag, count }));
  }, [minImportance]);

  // Keep sorted entries ref in sync with filtered entries
  const sortedEntries = useMemo(() => {
    return [...filteredEntries].sort((a, b) => {
      const yearA = a.date_start.era === 'BCE' ? -a.date_start.year : a.date_start.year;
      const yearB = b.date_start.era === 'BCE' ? -b.date_start.year : b.date_start.year;
      return yearA - yearB;
    });
  }, [filteredEntries]);

  sortedEntriesRef.current = sortedEntries;
  selectedIndexRef.current = selectedIndex;

  // Re-sync selection when the filtered list changes
  useEffect(() => {
    if (!selectedEntry) return;
    const idx = sortedEntries.findIndex(e => e.id === selectedEntry.id);
    if (idx >= 0) {
      setSelectedIndex(idx);
    } else {
      // Entry was filtered out — clear selection
      setSelectedEntry(null);
      setSelectedIndex(null);
    }
  }, [sortedEntries]);

  // Select an entry and sync both selectedEntry + selectedIndex atomically
  const selectEntry = useCallback((entry: TimelineEntry | null) => {
    setSourceReaderOpen(false);
    setSelectedEntry(entry);
    if (entry) {
      const idx = sortedEntriesRef.current.findIndex(e => e.id === entry.id);
      setSelectedIndex(idx >= 0 ? idx : null);
    } else {
      setSelectedIndex(null);
    }
  }, []);

  const navigateToEntry = useCallback((index: number) => {
    const entries = sortedEntriesRef.current;
    if (index < 0 || index >= entries.length) return;

    const entry = entries[index];
    setSelectedEntry(entry);
    setSelectedIndex(index);
    canvasTimelineRef.current?.scrollToEntry(entry);
  }, []);

  navigateRef.current = navigateToEntry;

  const handleFit = () => {
    canvasTimelineRef.current?.fit();
  };

  const handleZoomIn = () => {
    canvasTimelineRef.current?.zoomIn();
  };

  const handleZoomOut = () => {
    canvasTimelineRef.current?.zoomOut();
  };

  const handlePrevEntry = () => {
    if (selectedIndex === null) {
      navigateToEntry(0);
    } else if (selectedIndex > 0) {
      navigateToEntry(selectedIndex - 1);
    }
  };

  const handleNextEntry = () => {
    if (selectedIndex === null) {
      navigateToEntry(0);
    } else if (selectedIndex < sortedEntriesRef.current.length - 1) {
      navigateToEntry(selectedIndex + 1);
    }
  };

  // Keyboard navigation — uses refs so the listener is registered once
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const inSearch = document.activeElement === searchInputRef.current;

      if (e.key === 'Escape') {
        if (inSearch) {
          setSearchQuery('');
          setSearchExpanded(false);
          searchInputRef.current?.blur();
        } else {
          setSelectedEntry(null);
          setSelectedIndex(null);
        }
        return;
      }

      // Don't capture shortcuts when typing in search
      if (inSearch) return;

      if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        e.preventDefault();
        const idx = selectedIndexRef.current;
        if (idx === null) {
          navigateRef.current(0);
        } else if (idx > 0) {
          navigateRef.current(idx - 1);
        }
      } else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        e.preventDefault();
        const idx = selectedIndexRef.current;
        if (idx === null) {
          navigateRef.current(0);
        } else if (idx < sortedEntriesRef.current.length - 1) {
          navigateRef.current(idx + 1);
        }
      } else if (e.key === '=' || e.key === '+') {
        e.preventDefault();
        canvasTimelineRef.current?.zoomIn();
      } else if (e.key === '-') {
        e.preventDefault();
        canvasTimelineRef.current?.zoomOut();
      } else if (e.key === 'f') {
        e.preventDefault();
        canvasTimelineRef.current?.fit();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const getEntryMarkdown = (entry: TimelineEntry) => {
    const dateStr = entry.date_end
      ? `${formatDate(entry.date_start)} → ${formatDate(entry.date_end)}`
      : formatDate(entry.date_start);
    const impLabel = importanceLevels.find(l => l.level === (entry.importance || 3))?.label || 'Significatif';

    let md = `## ${entry.title}\n\n`;
    md += `**Date:** ${dateStr}\n\n`;
    md += `**Type:** ${entry.type}\n\n`;
    md += `**Dynastie:** ${entry.group_dynasty}\n\n`;
    md += `**Importance:** ${impLabel}\n\n`;
    md += `${entry.description}\n\n`;

    if (entry.people.length > 0) {
      md += `**Personnes:** ${entry.people.join(', ')}\n\n`;
    }
    if (entry.locations && entry.locations.length > 0) {
      md += `**Lieux:** ${entry.locations.join(', ')}\n\n`;
    }

    md += `> "${entry.source.excerpt}"\n>\n`;
    md += `> — Chapitre ${entry.source.chapter}, lignes ${entry.source.line_start}-${entry.source.line_end}`;

    return md;
  };

  return (
    <div className="flex flex-col h-screen bg-surface text-foreground">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 bg-surface-secondary border-b border-border">
        <h1 className="text-xl font-semibold tracking-tight text-accent">
          Histoire de France <span className="text-foreground-muted font-normal">&mdash; d'apr&egrave;s Jacques Bainville</span>
        </h1>
        <div className="flex items-center gap-3">
          {/* Group selector */}
          <div className="flex gap-1">
            {groupModes.map(({ key, label, title }) => (
              <button
                key={key}
                onClick={() => setGroupingMode(key)}
                title={title}
                className={`px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                  groupingMode === key
                    ? 'bg-accent text-surface'
                    : 'bg-surface-tertiary text-foreground-secondary hover:bg-surface-hover hover:text-foreground'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="w-px h-6 bg-border" />
          {/* Search */}
          {searchExpanded ? (
            <div className="relative">
              <input
                ref={searchInputRef}
                type="text"
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
                placeholder="Rechercher..."
                className="w-48 px-3 py-1.5 pl-8 text-sm bg-surface-tertiary text-foreground border border-border rounded-md focus:outline-none focus:border-accent placeholder:text-foreground-muted"
                autoFocus
              />
              <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-foreground-muted">
                <IconSearch size={14} />
              </span>
              {searchQuery && (
                <button
                  onClick={() => { setSearchQuery(''); searchInputRef.current?.focus(); }}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-foreground-muted hover:text-foreground"
                >
                  <IconClose size={12} />
                </button>
              )}
            </div>
          ) : (
            <button
              onClick={() => setSearchExpanded(true)}
              className="p-1.5 text-foreground-secondary hover:text-foreground hover:bg-surface-hover rounded-md transition-colors"
              title="Rechercher"
            >
              <IconSearch />
            </button>
          )}
          <div className="w-px h-6 bg-border" />
          {/* Tags */}
          <div className="flex items-center gap-2">
            <div className="relative">
              <button
                onClick={() => setTagCloudOpen(!tagCloudOpen)}
                className={`p-1.5 rounded-md transition-colors ${
                  tagCloudOpen || activeTags.size > 0
                    ? 'text-accent bg-surface-hover'
                    : 'text-foreground-secondary hover:text-foreground hover:bg-surface-hover'
                }`}
                title="Filtrer par tags"
              >
                <IconTag />
              </button>
              {tagCloudOpen && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setTagCloudOpen(false)} />
                  <div className="absolute right-0 mt-1 w-64 p-3 bg-surface-secondary border border-border rounded-md shadow-lg z-20">
                    <div className="flex flex-wrap gap-1.5">
                      {allTags.map(({ tag, count }) => (
                        <button
                          key={tag}
                          onClick={() => toggleTag(tag)}
                          className={`px-2 py-0.5 text-xs font-medium rounded-full transition-colors ${
                            activeTags.has(tag)
                              ? 'bg-accent text-surface'
                              : 'bg-surface-tertiary text-foreground-secondary hover:bg-surface-hover hover:text-foreground'
                          }`}
                        >
                          {tag} <span className="opacity-60">{count}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
          <div className="w-px h-6 bg-border" />
          {/* Importance filter dropdown */}
          <div className="relative">
            <button
              onClick={() => setImportanceDropdownOpen(!importanceDropdownOpen)}
              className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium bg-surface-tertiary text-foreground-secondary rounded-md hover:bg-surface-hover hover:text-foreground transition-colors"
              title="Filtrer par importance"
            >
              <span>{importanceLevels.find(l => l.level === minImportance)?.label}+</span>
              <span className="text-xs text-foreground-muted">({filteredEntries.length})</span>
              <span className={`transition-transform ${importanceDropdownOpen ? 'rotate-180' : ''}`}>
                <IconChevronDown />
              </span>
            </button>
            {importanceDropdownOpen && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setImportanceDropdownOpen(false)} />
                <div className="absolute right-0 mt-1 w-48 py-1 bg-surface-secondary border border-border rounded-md shadow-lg z-20">
                  {importanceLevels.map(({ level, label }) => (
                    <button
                      key={level}
                      onClick={() => { setImportanceDropdownOpen(false); setTimelineReady(false); requestAnimationFrame(() => { setMinImportance(level); requestAnimationFrame(() => canvasTimelineRef.current?.fit()); }); }}
                      className={`w-full px-3 py-2 text-left text-sm hover:bg-surface-hover transition-colors flex items-center justify-between ${
                        minImportance === level ? 'text-accent' : 'text-foreground-secondary'
                      }`}
                    >
                      <span>{label}</span>
                      {minImportance === level && <IconCheck />}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
          <div className="w-px h-6 bg-border" />
          {/* Zoom controls */}
          <div className="flex gap-1">
            <button
              onClick={handleZoomOut}
              className="px-3 py-1.5 text-sm font-medium bg-surface-tertiary text-foreground-secondary rounded-md hover:bg-surface-hover hover:text-foreground transition-colors"
              title="Dézoomer (-)"
            >
              -
            </button>
            <button
              onClick={handleZoomIn}
              className="px-3 py-1.5 text-sm font-medium bg-surface-tertiary text-foreground-secondary rounded-md hover:bg-surface-hover hover:text-foreground transition-colors"
              title="Zoomer (+)"
            >
              +
            </button>
            <button
              onClick={handleFit}
              className="px-2 py-1.5 bg-surface-tertiary text-foreground-secondary rounded-md hover:bg-surface-hover hover:text-foreground transition-colors"
              title="Fit to view"
            >
              <IconFit />
            </button>
          </div>
        </div>
      </header>

      {/* Main content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Timeline */}
        <div className="flex-1 min-w-0 min-h-0 bg-surface relative">
          <CanvasTimeline
            ref={canvasTimelineRef}
            entries={filteredEntries}
            groupingMode={groupingMode}
            selectedEntry={selectedEntry}
            onSelectEntry={selectEntry}
            onDraw={useCallback(() => setTimelineReady(true), [])}
          />
          {!timelineReady && (
            <div className="absolute inset-0 flex items-center justify-center bg-surface">
              <div className="flex flex-col items-center gap-3">
                <div className="w-6 h-6 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
                <span className="text-sm text-foreground-muted">Chargement...</span>
              </div>
            </div>
          )}
        </div>

        {/* Detail panel */}
        {selectedEntry && (
          <div className="w-96 bg-surface-secondary border-l border-border p-6 overflow-y-auto relative flex flex-col">
            {/* Header with navigation and close */}
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-1">
                <button
                  onClick={handlePrevEntry}
                  disabled={selectedIndex === null || selectedIndex === 0}
                  className="p-1.5 text-foreground-muted hover:text-foreground hover:bg-surface-hover rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                  title="Précédent (←)"
                >
                  <IconChevronLeft />
                </button>
                <span className="text-xs text-foreground-muted px-1">
                  {selectedIndex !== null ? `${selectedIndex + 1}/${sortedEntriesRef.current.length}` : ''}
                </span>
                <button
                  onClick={handleNextEntry}
                  disabled={selectedIndex === null || selectedIndex === sortedEntriesRef.current.length - 1}
                  className="p-1.5 text-foreground-muted hover:text-foreground hover:bg-surface-hover rounded transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                  title="Suivant (→)"
                >
                  <IconChevronRight />
                </button>
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => copyToClipboard(getEntryMarkdown(selectedEntry))}
                  className="p-1.5 text-foreground-muted hover:text-foreground hover:bg-surface-hover rounded transition-colors"
                  title="Copier tout (Markdown)"
                >
                  <IconCopy size={16} />
                </button>
                <button
                  onClick={() => selectEntry(null)}
                  className="p-1.5 text-foreground-muted hover:text-foreground hover:bg-surface-hover rounded transition-colors"
                  title="Fermer (Esc)"
                >
                  <IconClose />
                </button>
              </div>
            </div>

            {/* Title with copy */}
            <div className="flex items-start gap-2 mb-4">
              <h2 className="text-lg font-semibold text-accent flex-1">
                {selectedEntry.title}
              </h2>
              <CopyButton text={selectedEntry.title} label="titre" />
            </div>

            {/* Image */}
            {selectedEntry.image_url && (
              <div className="mb-4">
                <a href={getCommonsUrl(selectedEntry.image_url)} target="_blank" rel="noopener noreferrer" className="flex justify-center cursor-pointer">
                  <img
                    src={selectedEntry.image_url}
                    alt={selectedEntry.title}
                    className="max-h-56 rounded-lg object-contain hover:opacity-80 transition-opacity"
                    loading="lazy"
                  />
                </a>
                {selectedEntry.image_attribution && (
                  <p className="text-xs text-foreground-muted mt-1.5 leading-snug">
                    {selectedEntry.image_attribution}
                  </p>
                )}
              </div>
            )}

            {/* Date badges with copy */}
            <div className="flex flex-wrap items-center gap-2 mb-4">
              <span className="px-2 py-1 text-xs font-medium bg-surface-tertiary text-foreground-secondary rounded">
                {formatDate(selectedEntry.date_start)}
              </span>
              {selectedEntry.date_end && (
                <span className="px-2 py-1 text-xs font-medium bg-surface-tertiary text-foreground-secondary rounded">
                  → {formatDate(selectedEntry.date_end)}
                </span>
              )}
              <CopyButton
                text={selectedEntry.date_end
                  ? `${formatDate(selectedEntry.date_start)} → ${formatDate(selectedEntry.date_end)}`
                  : formatDate(selectedEntry.date_start)}
                label="date"
              />
              <span className="px-2 py-1 text-xs font-medium bg-surface-tertiary text-foreground-secondary rounded">
                {selectedEntry.type}
              </span>
              <span className="px-2 py-1 text-xs font-medium bg-surface-tertiary text-foreground-secondary rounded">
                {selectedEntry.group_dynasty}
              </span>
              <span
                className="px-2 py-1 text-xs font-medium rounded"
                style={{
                  backgroundColor: `var(--color-importance-${selectedEntry.importance || 3})`,
                  color: 'var(--color-surface)',
                }}
              >
                {importanceLevels.find(l => l.level === (selectedEntry.importance || 3))?.label}
              </span>
            </div>

            {selectedEntry.tags && selectedEntry.tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-4">
                {selectedEntry.tags.map((tag) => (
                  <button
                    key={tag}
                    onClick={() => toggleTag(tag)}
                    className={`px-2 py-0.5 text-xs font-medium rounded-full transition-colors ${
                      activeTags.has(tag)
                        ? 'bg-accent text-surface'
                        : 'bg-accent-muted/20 text-accent-muted hover:bg-accent-muted/40'
                    }`}
                  >
                    {tag}
                  </button>
                ))}
              </div>
            )}

            {/* Description with copy */}
            <div className="flex items-start gap-2 mb-4">
              <p className="text-foreground-secondary leading-relaxed flex-1">
                {selectedEntry.description}
              </p>
              <CopyButton text={selectedEntry.description} label="description" />
            </div>

            {selectedEntry.people.length > 0 && (
              <div className="flex items-start gap-2 mb-4">
                <p className="text-foreground-secondary flex-1">
                  <span className="font-medium text-foreground">Personnes:</span>{' '}
                  {selectedEntry.people.join(', ')}
                </p>
                <CopyButton text={selectedEntry.people.join(', ')} label="personnes" />
              </div>
            )}

            {selectedEntry.locations && selectedEntry.locations.length > 0 && (
              <div className="flex items-start gap-2 mb-4">
                <p className="text-foreground-secondary flex-1">
                  <span className="font-medium text-foreground">Lieux:</span>{' '}
                  {selectedEntry.locations.join(', ')}
                </p>
                <CopyButton text={selectedEntry.locations.join(', ')} label="lieux" />
              </div>
            )}

            {/* Source quote — click to open reader */}
            <div
              className="bg-surface-tertiary rounded-lg p-4 border-l-2 border-accent-muted relative group cursor-pointer hover:bg-surface-hover transition-colors"
              onClick={() => setSourceReaderOpen(true)}
              title="Ouvrir le texte source"
            >
              <button
                onClick={(e) => { e.stopPropagation(); copyToClipboard(`"${selectedEntry.source.excerpt}"\n— Chapitre ${selectedEntry.source.chapter}, lignes ${selectedEntry.source.line_start}-${selectedEntry.source.line_end}`); }}
                className="absolute top-2 right-2 p-1 text-foreground-muted hover:text-foreground hover:bg-surface-hover rounded transition-colors opacity-0 group-hover:opacity-100"
                title="Copier citation"
              >
                <IconCopy />
              </button>
              <div className="text-xs text-foreground-muted mb-2">
                Source: Chapitre {selectedEntry.source.chapter}, lignes {selectedEntry.source.line_start}–{selectedEntry.source.line_end}
              </div>
              <p className="text-foreground-secondary italic leading-relaxed text-base font-serif">
                "{selectedEntry.source.excerpt}"
              </p>
            </div>

            {sourceReaderOpen && (
              <SourceReaderModal
                source={selectedEntry.source}
                onClose={() => setSourceReaderOpen(false)}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
