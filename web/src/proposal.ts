// A command an AI proposed, as its block shows it (0.1.30, the security
// review): in a word with Latin letters, a character beyond ASCII is marked
// (a Cyrillic а in place of an a, a slash that only looks like one), so the
// person sees the word is not what it looks like. A word with no Latin
// letter in it (a commit message in Russian) shows as it is.

/** A piece of the command; `odd`, characters marked. */
export interface Part {
  text: string;
  odd: boolean;
}

/** The command in pieces, the odd characters apart. */
export function oddParts(command: string): Part[] {
  const parts: Part[] = [];
  const push = (text: string, odd: boolean) => {
    const last = parts[parts.length - 1];
    if (last && last.odd === odd) last.text += text;
    else parts.push({ text, odd });
  };
  for (const word of command.split(/(\s+)/)) {
    if (!word) continue;
    if (!/[A-Za-z]/.test(word) || !/[^\x00-\x7f]/.test(word)) {
      push(word, false);
      continue;
    }
    for (const ch of word) push(ch, (ch.codePointAt(0) ?? 0) > 0x7f);
  }
  return parts;
}

/** Whether anything in the command is marked. */
export function hasOdd(parts: Part[]): boolean {
  return parts.some((p) => p.odd);
}

/** How long a proposal's Run and Insert wait after it shows, or after
 * another takes its place: a click meant for what was there does not land
 * on it (the core waits a second, PROPOSAL_SETTLE_SECONDS). */
export const SETTLE_MS = 1500;
