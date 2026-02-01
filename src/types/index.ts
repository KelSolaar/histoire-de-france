export interface DateSpec {
  year: number;
  month?: number;
  day?: number;
  circa: boolean;
  era: "BCE" | "CE";
  precision: "exact" | "year" | "decade" | "century";
}

export interface SourceReference {
  chapter: number;
  line_start: number;
  line_end: number;
  excerpt: string;
}

export interface TimelineEntry {
  id: string;
  type: "event" | "period";
  date_start: DateSpec;
  date_end?: DateSpec;
  title: string;
  description: string;
  importance: 1 | 2 | 3 | 4 | 5;
  source: SourceReference;
  people: string[];
  locations?: string[];
  group_dynasty: string;
  group_era: string;
  tags?: string[];
  image_url?: string;
  image_attribution?: string;
}

export type GroupingMode = "dynasty" | "era";
