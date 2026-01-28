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
  type: string;
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

export interface Person {
  id: string;
  name: string;
  aliases: string[];
  dynasty?: string;
  titles: string[];
  birth?: DateSpec;
  death?: DateSpec;
  reign?: {
    start: DateSpec;
    end: DateSpec;
  };
  relations: {
    type: string;
    person: string;
  }[];
  image_url?: string;
  source_lines: number[];
}

export interface Dynasty {
  id: string;
  name: string;
  period_start: DateSpec;
  period_end: DateSpec;
  color: string;
}

export interface TimelineGroup {
  id: string;
  label: string;
  type: "dynasty" | "era";
  order: number;
  color?: string;
  chapter?: number;
}

export interface Groups {
  dynasty: TimelineGroup[];
  era: TimelineGroup[];
}

export type GroupingMode = "dynasty" | "era";
