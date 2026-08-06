const ORDERED_ITEM = /^\s*(?:\d+[.)、]|[一二三四五六七八九十]+[、.．])\s+(.+)$/;
const UNORDERED_ITEM = /^\s*[-*+•]\s+(.+)$/;
const MARKDOWN_HEADING = /^\s{0,3}#{1,6}\s+(.+)$/;
const BOLD_HEADING = /^\s*\*\*(.+?)\*\*\s*$/;

function removeUnpairedBoldMarkers(value) {
  const preserved = [];
  const protectedValue = value.replace(/\*\*[^*]+\*\*/g, (match) => {
    preserved.push(match);
    return `@@zhiwei-bold-${preserved.length - 1}@@`;
  });
  return protectedValue
    .replace(/\*\*/g, "")
    .replace(/@@zhiwei-bold-(\d+)@@/g, (_match, index) => preserved[Number(index)]);
}

function cleanText(value) {
  return value
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/`/g, "")
    .replace(/^\s*[-*+]\s*$/, "")
    .replace(/.+/, removeUnpairedBoldMarkers)
    .trim();
}

function pushBlock(blocks, type, text) {
  const value = cleanText(text);
  if (!value) return;
  const previous = blocks.at(-1);
  if (previous?.type === type && (type === "ordered" || type === "unordered")) {
    previous.items.push(value);
    return;
  }
  if (previous?.type === "paragraph" && type === "paragraph") {
    previous.text = `${previous.text} ${value}`;
    return;
  }
  blocks.push(type === "paragraph" || type === "heading" ? { type, text: value } : { type, items: [value] });
}

/** Convert common model Markdown into safe, presentation-ready content blocks. */
export function formatAnswer(text) {
  if (!text?.trim()) return [];
  const blocks = [];
  for (const rawLine of text.replace(/\r\n?/g, "\n").split("\n")) {
    const line = rawLine.trim();
    if (!line || /^```/.test(line)) continue;

    const markdownHeading = line.match(MARKDOWN_HEADING);
    const boldHeading = line.match(BOLD_HEADING);
    const ordered = line.match(ORDERED_ITEM);
    const unordered = line.match(UNORDERED_ITEM);
    if (markdownHeading || boldHeading) pushBlock(blocks, "heading", (markdownHeading || boldHeading)[1]);
    else if (ordered) pushBlock(blocks, "ordered", ordered[1]);
    else if (unordered) pushBlock(blocks, "unordered", unordered[1]);
    else pushBlock(blocks, "paragraph", line);
  }
  return blocks;
}

/** Split inline bold spans without interpreting model-supplied HTML. */
export function formatInlineText(text) {
  return cleanText(text).split(/(\*\*[^*]+\*\*)/).filter(Boolean).map((part) => {
    const bold = part.match(/^\*\*([^*]+)\*\*$/);
    return bold
      ? { text: cleanText(bold[1]), bold: true }
      : { text: cleanText(part).replace(/\*\*/g, ""), bold: false };
  });
}
