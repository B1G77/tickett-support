// Shapes of the JSON that src/api.py sends (state_json).

export type Draft = {
  id: string;
  version: string;
  folder: string;
  subject: string;
  from_name: string;
  from_email: string;
  received: string;
  text: string; // newest message only
  full: string; // whole conversation
  reply: string;
  note: string; // internal note for the team, never sent
  blocked: string; // "" when it may be sent, else why not
  to: string;
  to_differs: boolean;
  rewrites: number;
  edited: boolean;
  auto_at: string; // "HH:MM" (or "dd/mm HH:MM") when it goes out automatically, else ""
};

export type Person = {
  id: string;
  subject: string;
  from_name: string;
  from_email: string;
  received: string;
  reason: string;
  note: string;
  since: string;
  text: string;
};

export type AppState = {
  generated_at: string;
  mailbox: string;
  drafts: Draft[];
  people: Person[];
  health: { overall: "ok" | "warn" | "bad" };
  numbers: Record<string, number | null>;
};

/** "Name <email>", or just the email when there is no name. */
export function sender(name: string, email: string): string {
  return name ? `${name} <${email}>` : email;
}
