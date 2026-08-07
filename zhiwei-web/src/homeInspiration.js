export const HOME_INSPIRATION_POOL = [
  "推荐几道简单的汤",
  "今晚想吃点辣的",
  "适合夏天的饮品",
  "来一道十分钟快手菜",
  "冰箱里有鸡蛋能做什么",
  "想吃清淡又下饭的菜",
  "周末做一道有仪式感的菜",
  "推荐适合早餐的热食",
  "有什么适合一个人吃的菜",
  "帮我挑一道低油少盐的菜",
  "来一份暖胃的家常汤",
  "推荐一道简单甜点",
];

export function pickHomeInspirations(previous = [], count = 3, random = Math.random) {
  const previousSet = new Set(previous);
  const candidates = HOME_INSPIRATION_POOL.filter((item) => !previousSet.has(item));
  const pool = candidates.length >= count ? candidates : HOME_INSPIRATION_POOL;
  const shuffled = [...pool].sort(() => random() - 0.5);
  return shuffled.slice(0, count);
}
