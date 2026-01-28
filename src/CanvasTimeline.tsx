import { useEffect, useRef, useState, useMemo, useCallback, useImperativeHandle, forwardRef } from 'react';
import type { TimelineEntry, GroupingMode } from './types';

interface CanvasTimelineProps {
  entries: TimelineEntry[];
  groupingMode: GroupingMode;
  selectedEntry: TimelineEntry | null;
  onSelectEntry: (entry: TimelineEntry | null) => void;
  onDraw?: () => void;
}

export interface CanvasTimelineRef {
  zoomIn: () => void;
  zoomOut: () => void;
  fit: () => void;
  scrollToEntry: (entry: TimelineEntry) => void;
}

interface Group {
  key: string;
  entries: TimelineEntry[];
  y: number;
  height: number;
}

interface LayoutEntry {
  entry: TimelineEntry;
  row: number;
  group: string;
}

interface PositionedEvent {
  entry: TimelineEntry;
  x: number;
  y: number;
  width: number;
  height: number;
  group: string;
  row: number;
}

interface BadgeHitRect {
  x: number;
  y: number;
  w: number;
  h: number;
  minYear: number;
  maxYear: number;
  rightmostLabelWidth: number;
}

// --- Constants ---

const ROW_HEIGHT = 28;
const MARKER_PADDING = 4;
const AXIS_HEIGHT = 40;
const GROUP_LABEL_WIDTH = 160;
const SCROLL_SPEED = 0.75;
const ZOOM_SPEED = 0.025;
const LERP_FACTOR = 0.25;
const DOT_RADIUS = 5;
const EVENT_PADDING = 12;
const ICON_SIZE = 20;
const TOOLTIP_MAX_WIDTH = 280;
const LAYOUT_SCALE = 0.5;
const OVERLAP_GAP = 5;

// --- Color resolution from CSS custom properties ---

let _resolvedColors: ReturnType<typeof resolveColors> | null = null;

function resolveColors() {
  const style = getComputedStyle(document.documentElement);
  const get = (name: string) => style.getPropertyValue(name).trim();
  return {
    background: get('--color-bg-primary') || '#0a0a0a',
    backgroundAlt: get('--color-bg-secondary') || '#171717',
    groupLabel: get('--color-bg-secondary') || '#171717',
    axis: '#333',
    axisText: '#888',
    axisTextMajor: '#aaa',
    markerText: '#fff',
    period: 'rgba(139, 92, 246, 0.15)',
    periodBorder: 'rgba(139, 92, 246, 0.4)',
    selected: get('--color-accent') || '#a78bfa',
    importance: {
      5: get('--color-importance-5') || '#8b7355',
      4: get('--color-importance-4') || '#6d6d6d',
      3: get('--color-importance-3') || '#5a6370',
      2: get('--color-importance-2') || '#4a5568',
      1: get('--color-importance-1') || '#3d4a5c',
    } as Record<number, string>,
  };
}

function getColors() {
  if (!_resolvedColors) _resolvedColors = resolveColors();
  return _resolvedColors;
}

// --- Lazy text measurement canvas ---

let _measureCtx: CanvasRenderingContext2D | null = null;

function getMeasureCtx(): CanvasRenderingContext2D {
  if (!_measureCtx) {
    _measureCtx = document.createElement('canvas').getContext('2d')!;
  }
  return _measureCtx;
}

// --- Pure utility functions ---

function getYear(entry: TimelineEntry, isEnd = false): number {
  const date = isEnd && entry.date_end ? entry.date_end : entry.date_start;
  return date.era === 'BCE' ? -Math.abs(date.year) : date.year;
}

function formatYear(dateSpec: { year: number; era: 'BCE' | 'CE' }): string {
  if (dateSpec.era === 'BCE') {
    return `-${Math.abs(dateSpec.year)}`;
  }
  return `${dateSpec.year}`;
}

function measureEventWidth(entry: TimelineEntry): number {
  const ctx = getMeasureCtx();
  const yearStr = formatYear(entry.date_start);

  ctx.font = 'bold 10px system-ui, sans-serif';
  const yearWidth = ctx.measureText(yearStr).width;

  ctx.font = '11px system-ui, sans-serif';
  const titleWidth = ctx.measureText(entry.title).width;

  const leftWidth = entry.image_url ? ICON_SIZE + ICON_SIZE / 2 + 4 : DOT_RADIUS;
  return leftWidth + EVENT_PADDING + yearWidth + 6 + titleWidth + EVENT_PADDING;
}

function pixelsToYears(px: number, scale: number): number {
  return px / (scale * 10);
}

function lerp(start: number, end: number, t: number): number {
  return start + (end - start) * t;
}

// --- Tooltip component ---

function TooltipCard({ entry, x, y, eventBottom, containerHeight }: {
  entry: TimelineEntry;
  x: number;
  y: number;
  eventBottom: number;
  containerHeight: number;
}) {
  const showAbove = (y - AXIS_HEIGHT) > (containerHeight - eventBottom);

  return (
    <div
      style={{
        position: 'absolute',
        left: x,
        ...(showAbove
          ? { bottom: containerHeight - y + 6 }
          : { top: eventBottom + 6 }),
        transform: 'translateX(-50%)',
        maxWidth: TOOLTIP_MAX_WIDTH,
        pointerEvents: 'none',
        zIndex: 50,
      }}
      className="rounded-lg shadow-lg shadow-black/40 overflow-hidden"
    >
      {entry.image_url && (
        <img src={entry.image_url} alt="" className="w-full h-32 object-cover" />
      )}
      <div className="px-3 py-2 bg-surface-secondary" style={{ borderTop: entry.image_url ? '1px solid var(--color-border-primary)' : 'none' }}>
        <p className="text-xs leading-relaxed text-foreground">
          {entry.description}
        </p>
      </div>
    </div>
  );
}

// --- Main component ---

export const CanvasTimeline = forwardRef<CanvasTimelineRef, CanvasTimelineProps>(function CanvasTimeline({ entries, groupingMode, selectedEntry, onSelectEntry, onDraw }, ref) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const [viewState, setViewState] = useState({ offsetX: 0, offsetY: 0, scale: 0.3 });
  const viewStateRef = useRef(viewState);
  const [hoveredEvent, setHoveredEvent] = useState<TimelineEntry | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0, offsetX: 0, offsetY: 0 });
  const hasDraggedRef = useRef(false);
  const positionedEventsRef = useRef<PositionedEvent[]>([]);
  const groupsRef = useRef<Group[]>([]);
  const totalHeightRef = useRef(0);
  const badgeHitRectsRef = useRef<BadgeHitRect[]>([]);
  const tooltipTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [tooltipData, setTooltipData] = useState<{
    entry: TimelineEntry;
    x: number;
    y: number;
    eventBottom: number;
  } | null>(null);

  // Keep refs in sync
  viewStateRef.current = viewState;
  const onDrawRef = useRef(onDraw);
  onDrawRef.current = onDraw;

  // Image preloading
  const imagesCacheRef = useRef<Map<string, HTMLImageElement>>(new Map());
  const [imagesLoaded, setImagesLoaded] = useState(0);

  useEffect(() => {
    const cache = imagesCacheRef.current;
    for (const entry of entries) {
      if (!entry.image_url || cache.has(entry.id)) continue;
      const img = new Image();
      img.crossOrigin = 'anonymous';
      img.onload = () => {
        cache.set(entry.id, img);
        setImagesLoaded(n => n + 1);
      };
      img.src = entry.image_url;
    }
  }, [entries]);

  // Animation state
  const targetStateRef = useRef({ offsetX: 0, offsetY: 0, scale: 0.3 });
  const animationFrameRef = useRef<number | null>(null);
  const isAnimatingRef = useRef(false);

  // Animate toward target state
  const animateToTarget = useCallback(() => {
    const target = targetStateRef.current;

    setViewState((prev) => {
      const newOffsetX = lerp(prev.offsetX, target.offsetX, LERP_FACTOR);
      const newOffsetY = lerp(prev.offsetY, target.offsetY, LERP_FACTOR);
      const newScale = lerp(prev.scale, target.scale, LERP_FACTOR);

      const offsetXDiff = Math.abs(target.offsetX - newOffsetX);
      const offsetYDiff = Math.abs(target.offsetY - newOffsetY);
      const scaleDiff = Math.abs(target.scale - newScale);

      if (offsetXDiff < 0.5 && offsetYDiff < 0.5 && scaleDiff < 0.0001) {
        isAnimatingRef.current = false;
        return target;
      }

      return { offsetX: newOffsetX, offsetY: newOffsetY, scale: newScale };
    });

    if (isAnimatingRef.current) {
      animationFrameRef.current = requestAnimationFrame(animateToTarget);
    }
  }, []);

  // Start animation if not already running
  const startAnimation = useCallback(() => {
    if (!isAnimatingRef.current) {
      isAnimatingRef.current = true;
      animationFrameRef.current = requestAnimationFrame(animateToTarget);
    }
  }, [animateToTarget]);

  // Update target and start animation
  const setTargetState = useCallback((updater: (prev: typeof targetStateRef.current) => typeof targetStateRef.current) => {
    targetStateRef.current = updater(targetStateRef.current);
    startAnimation();
  }, [startAnimation]);

  // Cleanup animation on unmount
  useEffect(() => {
    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
      if (tooltipTimeoutRef.current) {
        clearTimeout(tooltipTimeoutRef.current);
      }
    };
  }, []);

  // Clear tooltip when view changes (scroll/zoom moves the event)
  useEffect(() => {
    setTooltipData(null);
  }, [viewState.offsetX, viewState.offsetY, viewState.scale]);

  // Get group key for entry
  const getGroupKey = useCallback((entry: TimelineEntry): string => {
    switch (groupingMode) {
      case 'dynasty': return entry.group_dynasty || 'Non classé';
      case 'era': return entry.group_era || 'Non classé';
    }
  }, [groupingMode]);

  // Find min/max years
  const { minYear, maxYear } = entries.length > 0
    ? entries.reduce(
        (acc, entry) => {
          const startYear = getYear(entry);
          const endYear = entry.date_end ? getYear(entry, true) : startYear;
          return {
            minYear: Math.min(acc.minYear, startYear),
            maxYear: Math.max(acc.maxYear, endYear),
          };
        },
        { minYear: Infinity, maxYear: -Infinity }
      )
    : { minYear: 0, maxYear: 100 };

  // Minimum scale: ensure entire timeline can always fit in viewport
  const minScale = Math.min(0.05, (dimensions.width - GROUP_LABEL_WIDTH - 100) / ((maxYear - minYear) * 10));

  // Convert year to x position
  const yearToX = useCallback(
    (year: number) => {
      const pixelsPerYear = viewState.scale * 10;
      return (year - minYear) * pixelsPerYear + viewState.offsetX + GROUP_LABEL_WIDTH + 50;
    },
    [minYear, viewState.offsetX, viewState.scale]
  );

  // Convert x position to year
  const xToYear = useCallback(
    (x: number) => {
      const pixelsPerYear = viewState.scale * 10;
      return (x - viewState.offsetX - GROUP_LABEL_WIDTH - 50) / pixelsPerYear + minYear;
    },
    [minYear, viewState.offsetX, viewState.scale]
  );

  // Stable row layout - only recomputes when entries/grouping changes
  const stableLayout = useMemo(() => {
    const groupMap = new Map<string, TimelineEntry[]>();
    const groupFirstYear = new Map<string, number>();

    for (const entry of entries) {
      const key = getGroupKey(entry);
      const year = getYear(entry);
      const existing = groupFirstYear.get(key);
      if (existing === undefined || year < existing) {
        groupFirstYear.set(key, year);
      }
      if (!groupMap.has(key)) {
        groupMap.set(key, []);
      }
      groupMap.get(key)!.push(entry);
    }

    const sortedGroupKeys = [...groupMap.keys()].sort((a, b) => {
      return (groupFirstYear.get(a) || 0) - (groupFirstYear.get(b) || 0);
    });

    const layoutEntries: LayoutEntry[] = [];
    const groups: { key: string; entries: TimelineEntry[]; rowCount: number }[] = [];

    for (const groupKey of sortedGroupKeys) {
      const groupEntries = groupMap.get(groupKey)!;
      groupEntries.sort((a, b) => getYear(a) - getYear(b));

      const rows: { endYear: number }[] = [];

      for (const entry of groupEntries) {
        const startYear = getYear(entry);
        const isPeriod = entry.date_end !== undefined;

        const textYearWidth = pixelsToYears(measureEventWidth(entry), LAYOUT_SCALE);
        let endYear: number;
        if (isPeriod) {
          endYear = Math.max(getYear(entry, true), startYear + textYearWidth);
        } else {
          endYear = startYear + textYearWidth;
        }

        let row = 0;
        const gap = pixelsToYears(5, LAYOUT_SCALE);
        while (rows[row] && rows[row].endYear > startYear - gap) {
          row++;
        }
        rows[row] = { endYear: endYear + gap };

        layoutEntries.push({ entry, row, group: groupKey });
      }

      groups.push({ key: groupKey, entries: groupEntries, rowCount: Math.max(rows.length, 1) });
    }

    let currentY = AXIS_HEIGHT;
    const groupPositions: Group[] = [];
    for (const g of groups) {
      const height = g.rowCount * ROW_HEIGHT;
      groupPositions.push({ key: g.key, entries: g.entries, y: currentY, height });
      currentY += height;
    }

    return { layoutEntries, groups: groupPositions, totalHeight: currentY };
  }, [entries, groupingMode, getGroupKey]);

  // Compute pixel positions from stable layout (zoom-dependent, but rows are fixed)
  const computeLayout = useCallback(() => {
    const { layoutEntries, groups, totalHeight } = stableLayout;

    const groupYMap = new Map<string, number>();
    for (const g of groups) {
      groupYMap.set(g.key, g.y);
    }

    const positioned: PositionedEvent[] = layoutEntries.map((le) => {
      const startX = yearToX(getYear(le.entry));
      const isPeriod = le.entry.date_end !== undefined;

      let width: number;
      if (isPeriod) {
        const endX = yearToX(getYear(le.entry, true));
        width = Math.max(endX - startX, measureEventWidth(le.entry));
      } else {
        width = measureEventWidth(le.entry);
      }

      const groupY = groupYMap.get(le.group) || AXIS_HEIGHT;

      return {
        entry: le.entry,
        x: startX,
        y: groupY + le.row * ROW_HEIGHT,
        width,
        height: ROW_HEIGHT - MARKER_PADDING,
        group: le.group,
        row: le.row,
      };
    });

    groupsRef.current = groups;
    positionedEventsRef.current = positioned;
    totalHeightRef.current = totalHeight;

    return { groups, positioned, totalHeight };
  }, [stableLayout, yearToX]);

  // Handle resize
  const initialFitDone = useRef(false);
  const hasMeasured = useRef(false);
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new ResizeObserver((resizeEntries) => {
      const { width, height } = resizeEntries[0].contentRect;
      hasMeasured.current = true;
      setDimensions({ width, height });
    });

    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  // Fit to view on initial load
  useEffect(() => {
    if (initialFitDone.current || !hasMeasured.current) return;
    initialFitDone.current = true;
    const yearRange = maxYear - minYear;
    if (yearRange <= 0) return;
    const availableWidth = dimensions.width - GROUP_LABEL_WIDTH - 100;
    const fitScale = availableWidth / (yearRange * 10);
    targetStateRef.current = { offsetX: 0, offsetY: 0, scale: Math.max(minScale, Math.min(5, fitScale)) };
    setViewState(targetStateRef.current);
  }, [dimensions.width, maxYear, minYear, minScale]);

  // Draw canvas
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const COLORS = getColors();

    const dpr = window.devicePixelRatio || 1;
    canvas.width = dimensions.width * dpr;
    canvas.height = dimensions.height * dpr;
    ctx.scale(dpr, dpr);

    // Clear
    ctx.fillStyle = COLORS.background;
    ctx.fillRect(0, 0, dimensions.width, dimensions.height);

    const { groups, positioned, totalHeight } = computeLayout();

    // Draw group backgrounds (alternating)
    for (let i = 0; i < groups.length; i++) {
      const group = groups[i];
      const y = group.y + viewState.offsetY;

      if (y + group.height < AXIS_HEIGHT || y > dimensions.height) continue;

      ctx.fillStyle = i % 2 === 0 ? COLORS.background : COLORS.backgroundAlt;
      ctx.fillRect(0, Math.max(y, AXIS_HEIGHT), dimensions.width, group.height);
    }

    // Draw group labels on left side
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, AXIS_HEIGHT, GROUP_LABEL_WIDTH, dimensions.height - AXIS_HEIGHT);
    ctx.clip();

    for (let i = 0; i < groups.length; i++) {
      const group = groups[i];
      const y = group.y + viewState.offsetY;

      if (y + group.height < AXIS_HEIGHT || y > dimensions.height) continue;

      ctx.fillStyle = i % 2 === 0 ? COLORS.background : COLORS.backgroundAlt;
      ctx.fillRect(0, Math.max(y, AXIS_HEIGHT), GROUP_LABEL_WIDTH, group.height);

      ctx.strokeStyle = COLORS.axis;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(GROUP_LABEL_WIDTH, y);
      ctx.lineTo(GROUP_LABEL_WIDTH, y + group.height);
      ctx.stroke();

      ctx.fillStyle = COLORS.axisTextMajor;
      ctx.font = '12px system-ui, sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';

      const labelY = Math.max(y + group.height / 2, AXIS_HEIGHT + 12);
      const clippedLabelY = Math.min(labelY, y + group.height - 12);

      let label = group.key;
      const maxWidth = GROUP_LABEL_WIDTH - 16;
      while (ctx.measureText(label).width > maxWidth && label.length > 0) {
        label = label.slice(0, -1);
      }
      if (label !== group.key) label += '...';

      ctx.fillText(label, 8, clippedLabelY);
    }
    ctx.restore();

    // Draw horizontal grid lines at group boundaries
    ctx.strokeStyle = COLORS.axis;
    ctx.lineWidth = 1;
    for (const group of groups) {
      const y = group.y + group.height + viewState.offsetY;
      if (y < AXIS_HEIGHT || y > dimensions.height) continue;
      ctx.beginPath();
      ctx.moveTo(GROUP_LABEL_WIDTH, y);
      ctx.lineTo(dimensions.width, y);
      ctx.stroke();
    }

    // Draw events (clipped to timeline area)
    ctx.save();
    ctx.beginPath();
    ctx.rect(GROUP_LABEL_WIDTH, AXIS_HEIGHT, dimensions.width - GROUP_LABEL_WIDTH, dimensions.height - AXIS_HEIGHT);
    ctx.clip();

    // Visibility culling
    const visibleSet = new Set<string>();

    const rowEvents = new Map<string, PositionedEvent[]>();
    for (const pos of positioned) {
      const rk = `${pos.group}:${pos.row}`;
      if (!rowEvents.has(rk)) rowEvents.set(rk, []);
      rowEvents.get(rk)!.push(pos);
    }

    const hiddenBadges: {
      afterPos: PositionedEvent; beforePos: PositionedEvent;
      count: number; yearMin: number; yearMax: number;
      rightmostLabelWidth: number;
    }[] = [];

    for (const [, events] of rowEvents) {
      visibleSet.add(events[0].entry.id);
      let lastVisibleIdx = 0;

      for (let i = 1; i < events.length; i++) {
        const prev = events[i - 1];
        const curr = events[i];
        if (curr.x >= prev.x + prev.width + OVERLAP_GAP) {
          const hiddenCount = i - lastVisibleIdx - 1;
          if (hiddenCount > 0) {
            const yMin = getYear(events[lastVisibleIdx].entry);
            const yMax = getYear(curr.entry, true) || getYear(curr.entry);
            hiddenBadges.push({
              afterPos: events[lastVisibleIdx], beforePos: curr,
              count: hiddenCount, yearMin: yMin, yearMax: yMax,
              rightmostLabelWidth: measureEventWidth(curr.entry),
            });
          }
          visibleSet.add(curr.entry.id);
          lastVisibleIdx = i;
        }
      }

      const trailingHidden = events.length - 1 - lastVisibleIdx;
      if (trailingHidden > 0) {
        const yMin = getYear(events[lastVisibleIdx].entry);
        const last = events[events.length - 1];
        const yMax = getYear(last.entry, true) || getYear(last.entry);
        hiddenBadges.push({
          afterPos: events[lastVisibleIdx], beforePos: events[lastVisibleIdx],
          count: trailingHidden, yearMin: yMin, yearMax: yMax,
          rightmostLabelWidth: measureEventWidth(last.entry),
        });
      }
    }

    // Always show the selected entry even if overlap-culled
    if (selectedEntry) visibleSet.add(selectedEntry.id);

    // Draw a single positioned event on the canvas
    const drawEvent = (pos: PositionedEvent, x: number, y: number) => {
      const isHovered = hoveredEvent === pos.entry;
      const isSelected = selectedEntry?.id === pos.entry.id;
      const isPeriod = pos.entry.date_end !== undefined;

      const importance = pos.entry.importance || 3;
      if (isSelected || isHovered) {
        ctx.fillStyle = COLORS.selected;
      } else if (isPeriod) {
        ctx.fillStyle = COLORS.period;
      } else {
        ctx.fillStyle = COLORS.importance[importance] || COLORS.importance[3];
      }

      const hasIcon = !!imagesCacheRef.current.get(pos.entry.id);
      const leftInset = hasIcon ? ICON_SIZE + 4 : DOT_RADIUS;

      if (isPeriod && !isSelected && !isHovered) {
        ctx.fillRect(x, y, pos.width, pos.height);
        ctx.strokeStyle = COLORS.periodBorder;
        ctx.lineWidth = 1;
        ctx.strokeRect(x, y, pos.width, pos.height);
      } else {
        const boxX = x + leftInset;
        const centerY = y + pos.height / 2;

        ctx.beginPath();
        ctx.moveTo(boxX, y);
        ctx.lineTo(x + pos.width - 4, y);
        ctx.arcTo(x + pos.width, y, x + pos.width, y + 4, 4);
        ctx.lineTo(x + pos.width, y + pos.height - 4);
        ctx.arcTo(x + pos.width, y + pos.height, x + pos.width - 4, y + pos.height, 4);
        ctx.lineTo(boxX, y + pos.height);
        ctx.arc(boxX, centerY, pos.height / 2, Math.PI / 2, -Math.PI / 2, false);
        ctx.closePath();
        ctx.fill();
      }

      if (hasIcon) {
        const img = imagesCacheRef.current.get(pos.entry.id)!;
        const centerY = y + pos.height / 2;
        const iconR = ICON_SIZE / 2;
        const iconCx = isPeriod ? x + iconR + 2 : x + leftInset;
        const iconCy = centerY;
        ctx.save();
        ctx.beginPath();
        if (isPeriod) {
          const ix = iconCx - iconR, iy = iconCy - iconR;
          const r = 3;
          ctx.moveTo(ix + r, iy);
          ctx.arcTo(ix + ICON_SIZE, iy, ix + ICON_SIZE, iy + r, r);
          ctx.arcTo(ix + ICON_SIZE, iy + ICON_SIZE, ix + ICON_SIZE - r, iy + ICON_SIZE, r);
          ctx.arcTo(ix, iy + ICON_SIZE, ix, iy + ICON_SIZE - r, r);
          ctx.arcTo(ix, iy, ix + r, iy, r);
        } else {
          ctx.arc(iconCx, iconCy, iconR, 0, Math.PI * 2);
        }
        ctx.clip();
        ctx.drawImage(img, iconCx - iconR, iconCy - iconR, ICON_SIZE, ICON_SIZE);
        ctx.restore();
      }

      const yearStr = formatYear(pos.entry.date_start);
      const iconOffset = hasIcon ? ICON_SIZE + 6 : 0;
      const textStartX = isPeriod ? x + iconOffset + 6 : x + leftInset + (hasIcon ? ICON_SIZE / 2 + 4 : 6);
      const availableWidth = isPeriod ? pos.width - iconOffset - 12 : pos.width - leftInset - (hasIcon ? ICON_SIZE / 2 + 4 + EVENT_PADDING : 12);

      ctx.fillStyle = 'rgba(255,255,255,0.6)';
      ctx.font = 'bold 10px system-ui, sans-serif';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';

      const yearWidth = ctx.measureText(yearStr).width;
      ctx.fillText(yearStr, textStartX, y + pos.height / 2);

      ctx.fillStyle = COLORS.markerText;
      ctx.font = '11px system-ui, sans-serif';

      const titleX = textStartX + yearWidth + 6;
      const maxTextWidth = availableWidth - yearWidth - 6;

      if (maxTextWidth > 20) {
        let title = pos.entry.title;
        const textWidth = ctx.measureText(title).width;

        if (textWidth > maxTextWidth) {
          while (ctx.measureText(title + '...').width > maxTextWidth && title.length > 0) {
            title = title.slice(0, -1);
          }
          title += '...';
        }

        ctx.fillText(title, titleX, y + pos.height / 2, maxTextWidth);
      }
    };

    // Draw in reverse order so earlier events paint on top of later ones
    // Defer the selected entry to draw it last (on top of overlapping events)
    let selectedPos: PositionedEvent | null = null;
    for (let pi = positioned.length - 1; pi >= 0; pi--) {
      const pos = positioned[pi];
      const x = pos.x;
      const y = pos.y + viewState.offsetY;

      if (!visibleSet.has(pos.entry.id)) continue;
      if (x + pos.width < GROUP_LABEL_WIDTH || x > dimensions.width) continue;
      if (y + pos.height < AXIS_HEIGHT || y > dimensions.height) continue;

      if (selectedEntry && pos.entry.id === selectedEntry.id) {
        selectedPos = pos;
        continue;
      }

      drawEvent(pos, x, y);
    }

    // Draw selected entry last so it appears on top of overlapping events
    if (selectedPos) {
      drawEvent(selectedPos, selectedPos.x, selectedPos.y + viewState.offsetY);
    }
    ctx.restore();

    // Draw axis background
    ctx.fillStyle = COLORS.background;
    ctx.fillRect(0, 0, dimensions.width, AXIS_HEIGHT);

    // Draw axis line
    ctx.strokeStyle = COLORS.axis;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(GROUP_LABEL_WIDTH, AXIS_HEIGHT);
    ctx.lineTo(dimensions.width, AXIS_HEIGHT);
    ctx.stroke();

    // Draw year markers
    ctx.fillStyle = COLORS.axisText;
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'center';

    const pixelsPerYear = viewState.scale * 10;
    let yearStep = 100;
    if (pixelsPerYear > 2) yearStep = 50;
    if (pixelsPerYear > 5) yearStep = 20;
    if (pixelsPerYear > 10) yearStep = 10;
    if (pixelsPerYear > 20) yearStep = 5;

    const startYear = Math.floor(xToYear(GROUP_LABEL_WIDTH) / yearStep) * yearStep;
    const endYear = Math.ceil(xToYear(dimensions.width) / yearStep) * yearStep;

    let lastLabelRight = -Infinity;

    for (let year = startYear; year <= endYear; year += yearStep) {
      const x = yearToX(year);
      if (x < GROUP_LABEL_WIDTH || x > dimensions.width) continue;

      ctx.strokeStyle = COLORS.axis;
      ctx.beginPath();
      ctx.moveTo(x, AXIS_HEIGHT - 5);
      ctx.lineTo(x, AXIS_HEIGHT);
      ctx.stroke();

      ctx.strokeStyle = 'rgba(255,255,255,0.05)';
      ctx.beginPath();
      ctx.moveTo(x, AXIS_HEIGHT);
      ctx.lineTo(x, dimensions.height);
      ctx.stroke();

      const label = year < 0 ? `${Math.abs(year)} av. J.-C.` : `${year}`;
      const labelWidth = ctx.measureText(label).width;
      const labelLeft = x - labelWidth / 2;

      if (labelLeft > lastLabelRight + 8) {
        ctx.fillStyle = COLORS.axisText;
        ctx.fillText(label, x, AXIS_HEIGHT - 15);
        lastLabelRight = x + labelWidth / 2;
      }
    }

    // Draw group label header
    ctx.fillStyle = COLORS.groupLabel;
    ctx.fillRect(0, 0, GROUP_LABEL_WIDTH, AXIS_HEIGHT);
    ctx.strokeStyle = COLORS.axis;
    ctx.beginPath();
    ctx.moveTo(GROUP_LABEL_WIDTH, 0);
    ctx.lineTo(GROUP_LABEL_WIDTH, AXIS_HEIGHT);
    ctx.stroke();

    // Scrollbar indicator if content overflows
    if (totalHeight > dimensions.height - AXIS_HEIGHT) {
      const scrollbarHeight = ((dimensions.height - AXIS_HEIGHT) / totalHeight) * (dimensions.height - AXIS_HEIGHT);
      const scrollbarY = AXIS_HEIGHT + (-viewState.offsetY / totalHeight) * (dimensions.height - AXIS_HEIGHT);

      ctx.fillStyle = 'rgba(255,255,255,0.2)';
      ctx.fillRect(dimensions.width - 8, scrollbarY, 6, Math.max(scrollbarHeight, 20));
    }

    // Hidden event badges
    ctx.save();
    ctx.beginPath();
    ctx.rect(GROUP_LABEL_WIDTH, AXIS_HEIGHT, dimensions.width - GROUP_LABEL_WIDTH, dimensions.height - AXIS_HEIGHT);
    ctx.clip();

    ctx.font = '9px system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    const badgeH = 16;
    const badgeHitRects: BadgeHitRect[] = [];

    for (const { afterPos, beforePos, count, yearMin, yearMax, rightmostLabelWidth } of hiddenBadges) {
      const y = afterPos.y + viewState.offsetY;

      if (y + afterPos.height < AXIS_HEIGHT || y > dimensions.height) continue;

      const afterEndX = afterPos.x + afterPos.width;
      const isTrailing = afterPos === beforePos;

      let bx: number;
      let gapWidth: number;

      if (isTrailing) {
        gapWidth = 40;
        bx = afterEndX + 4;
      } else {
        const beforeStartX = beforePos.x;
        gapWidth = beforeStartX - afterEndX;
        if (gapWidth < 24) continue;
        const badgeLabel = `+${count}`;
        const tw = ctx.measureText(badgeLabel).width;
        bx = afterEndX + (gapWidth - tw - 8) / 2;
      }

      if (bx > dimensions.width || bx + 40 < GROUP_LABEL_WIDTH) continue;

      const badgeLabel = `+${count}`;
      const tw = ctx.measureText(badgeLabel).width;
      const bw = tw + 8;
      const by = y + (afterPos.height - badgeH) / 2;

      ctx.fillStyle = 'rgba(255, 255, 255, 0.12)';
      ctx.beginPath();
      ctx.roundRect(bx, by, bw, badgeH, 3);
      ctx.fill();

      ctx.fillStyle = 'rgba(255, 255, 255, 0.5)';
      ctx.textAlign = 'center';
      ctx.fillText(badgeLabel, bx + bw / 2, by + badgeH / 2);

      badgeHitRects.push({ x: bx, y: by, w: bw, h: badgeH, minYear: yearMin, maxYear: yearMax, rightmostLabelWidth });
    }

    badgeHitRectsRef.current = badgeHitRects;
    ctx.restore();
    onDrawRef.current?.();

  }, [dimensions, viewState, entries, hoveredEvent, selectedEntry, computeLayout, yearToX, xToYear, imagesLoaded]);

  // Mouse handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    isAnimatingRef.current = false;
    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
    }
    if (tooltipTimeoutRef.current) {
      clearTimeout(tooltipTimeoutRef.current);
      tooltipTimeoutRef.current = null;
    }
    setTooltipData(null);

    setIsDragging(true);
    hasDraggedRef.current = false;
    setDragStart({
      x: e.clientX,
      y: e.clientY,
      offsetX: targetStateRef.current.offsetX,
      offsetY: targetStateRef.current.offsetY
    });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    if (isDragging) {
      const dx = e.clientX - dragStart.x;
      const dy = e.clientY - dragStart.y;

      if (Math.abs(dx) > 5 || Math.abs(dy) > 5) {
        hasDraggedRef.current = true;
      }

      const maxOffsetY = 0;
      const minOffsetY = Math.min(0, dimensions.height - AXIS_HEIGHT - totalHeightRef.current);
      const newOffsetX = dragStart.offsetX + dx;
      const newOffsetY = Math.max(minOffsetY, Math.min(maxOffsetY, dragStart.offsetY + dy));

      const newState = { offsetX: newOffsetX, offsetY: newOffsetY, scale: viewState.scale };
      setViewState(newState);
      targetStateRef.current = newState;
      return;
    }

    if (x < GROUP_LABEL_WIDTH || y < AXIS_HEIGHT) {
      setHoveredEvent(null);
      canvas.style.cursor = 'default';
      return;
    }

    let onBadge = false;
    for (const badge of badgeHitRectsRef.current) {
      if (x >= badge.x && x <= badge.x + badge.w && y >= badge.y && y <= badge.y + badge.h) {
        onBadge = true;
        break;
      }
    }

    let found: TimelineEntry | null = null;
    let foundPos: PositionedEvent | null = null;
    if (!onBadge) {
      for (const pos of positionedEventsRef.current) {
        const posY = pos.y + viewState.offsetY;
        if (
          x >= pos.x &&
          x <= pos.x + pos.width &&
          y >= posY &&
          y <= posY + pos.height
        ) {
          found = pos.entry;
          foundPos = pos;
          break;
        }
      }
    }
    setHoveredEvent(found);
    canvas.style.cursor = (found || onBadge) ? 'pointer' : 'grab';

    if (tooltipTimeoutRef.current) {
      clearTimeout(tooltipTimeoutRef.current);
      tooltipTimeoutRef.current = null;
    }
    if (found && foundPos) {
      const posY = foundPos.y + viewState.offsetY;
      const capturedEntry = found;
      const capturedPos = foundPos;
      tooltipTimeoutRef.current = setTimeout(() => {
        setTooltipData({
          entry: capturedEntry,
          x: capturedPos.x + capturedPos.width / 2,
          y: posY,
          eventBottom: posY + capturedPos.height,
        });
      }, 400);
    } else {
      setTooltipData(null);
    }
  };

  const handleMouseUp = () => {
    setIsDragging(false);
  };

  const handleMouseLeave = () => {
    setIsDragging(false);
    setHoveredEvent(null);
    if (tooltipTimeoutRef.current) {
      clearTimeout(tooltipTimeoutRef.current);
      tooltipTimeoutRef.current = null;
    }
    setTooltipData(null);
  };

  const handleClick = (e: React.MouseEvent) => {
    if (hasDraggedRef.current) return;

    const canvas = canvasRef.current;
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    if (x < GROUP_LABEL_WIDTH || y < AXIS_HEIGHT) return;

    for (const badge of badgeHitRectsRef.current) {
      if (x >= badge.x && x <= badge.x + badge.w && y >= badge.y && y <= badge.y + badge.h) {
        const yearRange = badge.maxYear - badge.minYear;
        const padding = Math.max(yearRange * 0.15, 20);
        const fitMin = badge.minYear - padding;
        const fitMax = badge.maxYear + padding;
        const fitRange = fitMax - fitMin;
        const availableWidth = dimensions.width - GROUP_LABEL_WIDTH - badge.rightmostLabelWidth;
        const newScale = Math.max(0.05, Math.min(5, availableWidth / (fitRange * 10)));
        const newOffsetX = -(fitMin - minYear) * newScale * 10 - 50;
        setTargetState((prev) => ({ ...prev, scale: newScale, offsetX: newOffsetX }));
        return;
      }
    }

    for (const pos of positionedEventsRef.current) {
      const posY = pos.y + viewState.offsetY;
      if (
        x >= pos.x &&
        x <= pos.x + pos.width &&
        y >= posY &&
        y <= posY + pos.height
      ) {
        onSelectEntry(pos.entry);
        return;
      }
    }
    onSelectEntry(null);
  };

  // Handle wheel zoom with native event listener — reads from ref to avoid re-registration
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const vs = viewStateRef.current;

      if (e.ctrlKey || e.metaKey) {
        const zoomFactor = 1 - e.deltaY * ZOOM_SPEED;

        const currentPixelsPerYear = vs.scale * 10;
        const yearAtMouse = (mouseX - vs.offsetX - GROUP_LABEL_WIDTH - 50) / currentPixelsPerYear + minYear;

        const newScale = Math.max(minScale, Math.min(5, vs.scale * zoomFactor));
        const newPixelsPerYear = newScale * 10;
        const newOffsetX = mouseX - (yearAtMouse - minYear) * newPixelsPerYear - GROUP_LABEL_WIDTH - 50;

        targetStateRef.current = { offsetX: newOffsetX, offsetY: vs.offsetY, scale: newScale };
        startAnimation();
        return;
      }

      const deltaX = e.deltaX * SCROLL_SPEED;
      const deltaY = e.deltaY * SCROLL_SPEED;

      if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        setTargetState((prev) => ({ ...prev, offsetX: prev.offsetX - deltaX }));
        return;
      }

      const maxOffsetY = 0;
      const minOffsetY = Math.min(0, dimensions.height - AXIS_HEIGHT - totalHeightRef.current);
      setTargetState((prev) => ({
        ...prev,
        offsetY: Math.max(minOffsetY, Math.min(maxOffsetY, prev.offsetY - deltaY)),
      }));
    };

    canvas.addEventListener('wheel', handleWheel, { passive: false });
    return () => canvas.removeEventListener('wheel', handleWheel);
  }, [setTargetState, startAnimation, minYear, minScale, dimensions.height]);

  // Expose methods to parent
  useImperativeHandle(ref, () => ({
    zoomIn: () => {
      setTargetState((prev) => ({ ...prev, scale: Math.min(5, prev.scale * 1.3) }));
    },
    zoomOut: () => {
      setTargetState((prev) => ({ ...prev, scale: Math.max(minScale, prev.scale / 1.3) }));
    },
    fit: () => {
      const yearRange = maxYear - minYear;
      const availableWidth = dimensions.width - GROUP_LABEL_WIDTH - 100;
      const newScale = availableWidth / (yearRange * 10);
      const finalScale = Math.max(minScale, Math.min(5, newScale));
      const contentHeight = stableLayout.totalHeight - AXIS_HEIGHT;
      const viewportHeight = dimensions.height - AXIS_HEIGHT;
      let offsetY: number;
      if (contentHeight <= viewportHeight) {
        offsetY = (viewportHeight - contentHeight) / 2;
      } else if (selectedEntry) {
        const pos = positionedEventsRef.current.find(p => p.entry.id === selectedEntry.id);
        if (pos) {
          offsetY = -(pos.y - dimensions.height / 2 + ROW_HEIGHT / 2 - AXIS_HEIGHT);
          const minOffsetY = Math.min(0, dimensions.height - AXIS_HEIGHT - stableLayout.totalHeight);
          offsetY = Math.max(minOffsetY, Math.min(0, offsetY));
        } else {
          offsetY = 0;
        }
      } else {
        offsetY = 0;
      }
      let offsetX = 0;
      if (selectedEntry) {
        const entryYear = selectedEntry.date_start.era === 'BCE'
          ? -selectedEntry.date_start.year
          : selectedEntry.date_start.year;
        const pixelsPerYear = finalScale * 10;
        offsetX = -(entryYear - minYear) * pixelsPerYear + (dimensions.width - GROUP_LABEL_WIDTH) / 2 - 50;
      }
      setTargetState(() => ({ offsetX, offsetY, scale: finalScale }));
    },
    scrollToEntry: (entry: TimelineEntry) => {
      const pos = positionedEventsRef.current.find(p => p.entry.id === entry.id);
      if (!pos) return;

      const entryYear = getYear(entry);
      const entryWidth = measureEventWidth(entry);

      // Find same-row neighbors to compute minimum zoom that avoids overlap
      const sameRow = positionedEventsRef.current
        .filter(p => p.group === pos.group && p.row === pos.row)
        .sort((a, b) => getYear(a.entry) - getYear(b.entry));
      const idx = sameRow.findIndex(p => p.entry.id === entry.id);

      let neededScale = 0;
      if (idx > 0) {
        const left = sameRow[idx - 1];
        const yearGap = entryYear - getYear(left.entry);
        if (yearGap > 0) {
          neededScale = Math.max(neededScale, (measureEventWidth(left.entry) + OVERLAP_GAP) / (yearGap * 10));
        }
      }
      if (idx < sameRow.length - 1) {
        const right = sameRow[idx + 1];
        const yearGap = getYear(right.entry) - entryYear;
        if (yearGap > 0) {
          neededScale = Math.max(neededScale, (entryWidth + OVERLAP_GAP) / (yearGap * 10));
        }
      }

      // Only change zoom when neighbors require it; otherwise keep current scale
      const finalScale = neededScale > 0
        ? Math.min(5, neededScale * 1.1)
        : targetStateRef.current.scale;
      const pixelsPerYear = finalScale * 10;
      const targetX = -(entryYear - minYear) * pixelsPerYear + (dimensions.width - GROUP_LABEL_WIDTH) / 2 - 50;
      const targetY = -(pos.y - dimensions.height / 2 + ROW_HEIGHT / 2 - AXIS_HEIGHT);
      setTargetState(() => ({ offsetX: targetX, offsetY: targetY, scale: finalScale }));
    },
  }), [maxYear, minYear, minScale, dimensions.width, dimensions.height, setTargetState, stableLayout.totalHeight, selectedEntry]);

  return (
    <div ref={containerRef} style={{ width: '100%', height: '100%', position: 'relative', overflow: 'hidden' }}>
      <canvas
        ref={canvasRef}
        style={{
          width: dimensions.width,
          height: dimensions.height,
          cursor: isDragging ? 'grabbing' : 'grab',
        }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseLeave}
        onClick={handleClick}
      />
      {tooltipData && (
        <TooltipCard
          entry={tooltipData.entry}
          x={tooltipData.x}
          y={tooltipData.y}
          eventBottom={tooltipData.eventBottom}
          containerHeight={dimensions.height}
        />
      )}
    </div>
  );
});
