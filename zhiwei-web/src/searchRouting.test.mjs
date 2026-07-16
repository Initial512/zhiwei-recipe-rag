import assert from "node:assert/strict";
import test from "node:test";

import { chooseSearchMode, hasRecipeQuestionIntent } from "./searchRouting.js";

test("菜名问做法时优先进入知识库回答", () => {
  assert.equal(hasRecipeQuestionIntent("宫保鸡丁怎么做"), true);
  assert.equal(chooseSearchMode("宫保鸡丁怎么做", [{ dish_name: "宫保鸡丁" }]), "recipe");
});

test("纯菜名命中时展示菜谱卡片", () => {
  assert.equal(chooseSearchMode("宫保鸡丁", [{ dish_name: "宫保鸡丁" }]), "cards");
  assert.equal(chooseSearchMode("不存在的菜", []), "recipe");
});
