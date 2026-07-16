export const RECIPE_QUESTION_MARKERS = [
  "怎么做",
  "做法",
  "食材",
  "原料",
  "制作步骤",
  "步骤",
  "食谱",
  "菜谱",
  "如何做",
  "需要什么",
  "需要哪些",
  "要什么",
];

export function hasRecipeQuestionIntent(query) {
  const value = query.trim();
  return RECIPE_QUESTION_MARKERS.some((marker) => value.includes(marker));
}

export function chooseSearchMode(query, results) {
  if (hasRecipeQuestionIntent(query)) return "recipe";
  return results?.length ? "cards" : "recipe";
}
