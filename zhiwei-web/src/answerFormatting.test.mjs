import assert from "node:assert/strict";
import test from "node:test";

import { formatAnswer, formatInlineText } from "./answerFormatting.js";

test("formats headings, ordered steps, bullets, and inline emphasis", () => {
  const blocks = formatAnswer("**一、饮品选择**\n1. **柠檬水**：清爽解腻\n2. 酸梅汤\n- 冰镇后饮用");
  assert.deepEqual(blocks, [
    { type: "heading", text: "一、饮品选择" },
    { type: "ordered", items: ["**柠檬水**：清爽解腻", "酸梅汤"] },
    { type: "unordered", items: ["冰镇后饮用"] },
  ]);
  assert.deepEqual(formatInlineText("**柠檬水**：清爽"), [
    { text: "柠檬水", bold: true },
    { text: "：清爽", bold: false },
  ]);
});

test("keeps partial streaming content readable without raw Markdown markers", () => {
  assert.deepEqual(formatAnswer("**未完成标题"), [{ type: "paragraph", text: "未完成标题" }]);
  assert.deepEqual(formatAnswer("- "), []);
  assert.deepEqual(formatAnswer("普通回答\n继续说明"), [{ type: "paragraph", text: "普通回答 继续说明" }]);
});
