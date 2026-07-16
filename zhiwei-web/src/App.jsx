import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ArrowsClockwise,
  CheckCircle,
  Clock,
  Lightbulb,
  MagnifyingGlass,
  Pause,
  Sparkle,
  UsersThree,
  X,
} from "@phosphor-icons/react";
import roomImage from "./assets/warm-interior.png";
import aromaImage from "./assets/aroma-chopsticks-transparent.png";
import { chooseSearchMode, hasRecipeQuestionIntent } from "./searchRouting.js";

const quickQuestions = ["推荐几道简单的汤", "今晚想吃点辣的", "适合夏天的饮品"];
const MAX_CHAT_LENGTH = 1000;
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/+$/, "");
const apiUrl = (path) => `${API_BASE_URL}${path}`;

function recipeImageUrl(imageUrl) {
  return typeof imageUrl === "string" && imageUrl.startsWith("/recipe-images/")
    ? apiUrl(imageUrl)
    : "";
}

function parseLocation(historyState = window.history.state) {
  const path = window.location.pathname;
  if (path.startsWith("/category/")) {
    return { type: "category", categoryName: decodeURIComponent(path.slice("/category/".length)) };
  }
  if (path.startsWith("/recipe/")) {
    return { type: "recipe", dishName: decodeURIComponent(path.slice("/recipe/".length)) };
  }
  if (path === "/search") {
    return { type: "search", query: new URLSearchParams(window.location.search).get("q") || "" };
  }
  if (path === "/answer") {
    const params = new URLSearchParams(window.location.search);
    return {
      type: "answer",
      query: params.get("q") || "",
      mode: ["recipe", "cards"].includes(params.get("mode")) ? params.get("mode") : "assistant",
      recipeResults: Array.isArray(historyState?.recipeResults) ? historyState.recipeResults : undefined,
    };
  }
  return { type: "home" };
}

function CategoryArtwork({ category, className = "" }) {
  return (
    <div className={`category-artwork is-empty ${className}`}>
      <span>{category}</span>
    </div>
  );
}

function RecipeArtwork({ recipe, className = "" }) {
  const [failed, setFailed] = useState(false);
  const imageUrl = recipeImageUrl(recipe.image_url);

  useEffect(() => setFailed(false), [recipe.image_url]);

  if (!imageUrl || failed) {
    return <CategoryArtwork category={recipe.category} className={className} />;
  }
  return (
    <div className={`category-artwork recipe-artwork has-image ${className}`}>
      <img
        src={imageUrl}
        alt={`${recipe.dish_name}成品图`}
        loading="lazy"
        onError={() => setFailed(true)}
      />
    </div>
  );
}

function RecipeCard({ recipe, onOpen, className = "" }) {
  return (
    <button className={`recipe-card ${className}`} onClick={() => onOpen(recipe)}>
      <RecipeArtwork recipe={recipe} />
      <span className="recipe-copy">
        <small>{recipe.category} · {recipe.difficulty}</small>
        <strong>{recipe.dish_name}</strong>
        {recipe.description && <p>{recipe.description}</p>}
        <i>查看完整菜谱 <ArrowRight size={15} /></i>
      </span>
    </button>
  );
}

async function readEventStream(response, handlers, signal) {
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail || `请求失败（${response.status}）`);
  }
  if (!response.body) throw new Error("浏览器未收到流式响应");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    if (signal.aborted) return;
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const rawEvent of events) {
      const lines = rawEvent.split("\n");
      const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
      const data = lines
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (event && handlers[event]) handlers[event](data);
    }
    if (done) break;
  }
}

export function App() {
  const [page, setPage] = useState(parseLocation);
  const [categories, setCategories] = useState([]);
  const [activeCategory, setActiveCategory] = useState(() => parseLocation().categoryName || "");
  const [recipes, setRecipes] = useState([]);
  const [recommendations, setRecommendations] = useState([]);
  const [recommendationsLoading, setRecommendationsLoading] = useState(false);
  const [recommendationsError, setRecommendationsError] = useState("");
  const [recipeQuery, setRecipeQuery] = useState("");
  const [chatInput, setChatInput] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState([]);
  const [searchError, setSearchError] = useState("");
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [detailQuestion, setDetailQuestion] = useState("");
  const [answerInput, setAnswerInput] = useState("");
  const [answerOpen, setAnswerOpen] = useState(false);
  const [answerTitle, setAnswerTitle] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState([]);
  const [answerRecipeResults, setAnswerRecipeResults] = useState([]);
  const [answerRecipeLoading, setAnswerRecipeLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const [categoryMenuOpen, setCategoryMenuOpen] = useState(false);
  const [searchMenuOpen, setSearchMenuOpen] = useState(false);
  const [nameSearchInput, setNameSearchInput] = useState("");
  const [lastRequest, setLastRequest] = useState(null);
  const abortRef = useRef(null);
  const searchMenuRef = useRef(null);
  const categoryMenuRef = useRef(null);

  useEffect(() => {
    const onPopState = (event) => {
      const nextPage = parseLocation(event.state);
      setPage(nextPage);
      setActiveCategory(nextPage.categoryName || "");
      setRecipeQuery("");
      window.requestAnimationFrame(() => window.scrollTo({ top: event.state?.scrollY || 0 }));
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (!searchMenuOpen && !categoryMenuOpen) return undefined;

    const closeMenusOutside = (event) => {
      if (!searchMenuRef.current?.contains(event.target)) setSearchMenuOpen(false);
      if (!categoryMenuRef.current?.contains(event.target)) setCategoryMenuOpen(false);
    };

    document.addEventListener("pointerdown", closeMenusOutside);
    return () => document.removeEventListener("pointerdown", closeMenusOutside);
  }, [searchMenuOpen, categoryMenuOpen]);

  useEffect(() => {
    fetch(apiUrl("/api/categories"))
      .then((response) => {
        if (!response.ok) throw new Error("分类加载失败");
        return response.json();
      })
      .then(setCategories)
      .catch((requestError) => {
        console.error("加载分类失败", requestError);
        setCatalogError("暂时无法连接菜谱知识库，请确认后端服务已启动。");
      });
  }, []);

  const loadRecommendations = useCallback(() => {
    setRecommendationsLoading(true);
    setRecommendationsError("");
    fetch(apiUrl("/api/recommendations?limit=6"))
      .then((response) => {
        if (!response.ok) throw new Error("推荐加载失败");
        return response.json();
      })
      .then(setRecommendations)
      .catch((requestError) => {
        console.error("加载推荐失败", requestError);
        setRecommendationsError("暂时无法获取推荐菜，请稍后重试。");
      })
      .finally(() => setRecommendationsLoading(false));
  }, []);

  useEffect(() => {
    loadRecommendations();
  }, [loadRecommendations]);

  useEffect(() => {
    if (page.type !== "category" || !activeCategory) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ category: activeCategory });
    if (recipeQuery.trim()) params.set("query", recipeQuery.trim());
    const timer = window.setTimeout(() => {
      fetch(apiUrl(`/api/recipes?${params}`), { signal: controller.signal })
        .then((response) => {
          if (!response.ok) throw new Error("菜谱加载失败");
          return response.json();
        })
        .then(setRecipes)
        .catch((requestError) => {
          if (requestError.name !== "AbortError") {
            console.error("加载菜谱列表失败", requestError);
            setCatalogError("菜谱列表加载失败，请稍后重试。");
          }
        });
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [activeCategory, page.type, recipeQuery]);

  useEffect(() => {
    if (page.type !== "recipe") return;
    const controller = new AbortController();
    setDetail(null);
    setDetailError("");
    setDetailLoading(true);
    fetch(apiUrl(`/api/recipes/${encodeURIComponent(page.dishName)}`), { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          const body = await response.json().catch(() => null);
          throw new Error(body?.detail || "菜谱加载失败");
        }
        return response.json();
      })
      .then(setDetail)
      .catch((requestError) => {
        if (requestError.name !== "AbortError") {
          console.error("加载菜谱详情失败", requestError);
          setDetailError(requestError.message);
        }
      })
      .finally(() => setDetailLoading(false));
    return () => controller.abort();
  }, [page]);

  useEffect(() => {
    if (page.type !== "search" || !page.query) return;
    const controller = new AbortController();
    setSearchError("");
    setSearchResults([]);
    setSearching(true);
    fetch(apiUrl(`/api/search/recipes?${new URLSearchParams({ query: page.query, limit: "12" })}`), {
      signal: controller.signal,
    })
      .then((response) => {
        if (!response.ok) throw new Error("搜索失败");
        return response.json();
      })
      .then((data) => setSearchResults(data.results || []))
      .catch((requestError) => {
        if (requestError.name !== "AbortError") {
          console.error("搜索菜谱失败", requestError);
          setSearchError("暂时无法搜索菜谱，请稍后重试。");
        }
      })
      .finally(() => setSearching(false));
    return () => controller.abort();
  }, [page]);

  const stopStream = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }, []);

  const loadCardResults = useCallback(async (query, signal) => {
    setAnswerRecipeResults([]);
    setAnswerRecipeLoading(true);
    setError("");
    try {
      const response = await fetch(
        apiUrl(`/api/search/recipes?${new URLSearchParams({ query, limit: "12" })}`),
        { signal },
      );
      if (!response.ok) throw new Error("菜谱加载失败");
      const data = await response.json();
      setAnswerRecipeResults(data.results || []);
    } catch (requestError) {
      if (requestError.name !== "AbortError") {
        console.error("加载菜谱卡片失败", requestError);
        setError(requestError.message || "菜谱加载失败");
      }
    } finally {
      setAnswerRecipeLoading(false);
    }
  }, []);

  const startStream = useCallback(async (requestConfig) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLastRequest(requestConfig);
    setAnswerTitle(requestConfig.title);
    setAnswer("");
    setSources([]);
    setError("");
    setAnswerOpen(requestConfig.openPanel !== false);
    setStreaming(true);
    try {
      const response = await fetch(apiUrl(requestConfig.url), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestConfig.body),
        signal: controller.signal,
      });
      await readEventStream(response, {
        sources: (data) => {
          try {
            setSources(JSON.parse(data));
          } catch (parseError) {
            console.error("忽略无效的 SSE sources 帧", parseError);
          }
        },
        delta: (data) => setAnswer((current) => current + data),
        error: (data) => {
          try {
            setError(JSON.parse(data).message || "生成回答失败");
          } catch (parseError) {
            console.error("忽略无效的 SSE error 帧", parseError);
          }
        },
        done: () => setStreaming(false),
      }, controller.signal);
    } catch (requestError) {
      if (requestError.name !== "AbortError") {
        console.error("读取流式回答失败", requestError);
        setError(requestError.message || "生成回答失败");
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setStreaming(false);
    }
  }, []);

  useEffect(() => {
    if (page.type !== "answer" || !page.query) return;
    if (page.mode === "cards") {
      if (Array.isArray(page.recipeResults)) {
        setAnswerRecipeResults(page.recipeResults);
        setAnswerRecipeLoading(false);
        setError("");
        return undefined;
      }
      const controller = new AbortController();
      loadCardResults(page.query, controller.signal);
      return () => controller.abort();
    }
    startStream({
      title: page.query,
      url: page.mode === "recipe" ? "/api/chat/stream" : "/api/assistant/stream",
      body: { question: page.query },
      openPanel: false,
    });
    return () => abortRef.current?.abort();
  }, [page, loadCardResults, startStream]);

  const saveScrollPosition = () => {
    window.history.replaceState({ ...window.history.state, scrollY: window.scrollY }, "");
  };

  const openRecipe = useCallback((recipe) => {
    const dishName = recipe.dish_name || recipe;
    saveScrollPosition();
    window.history.pushState({}, "", `/recipe/${encodeURIComponent(dishName)}`);
    setPage({ type: "recipe", dishName });
    setCategoryMenuOpen(false);
    setSearchMenuOpen(false);
    window.scrollTo({ top: 0 });
  }, []);

  const goHome = useCallback((smooth = false) => {
    if (window.location.pathname !== "/") {
      window.history.pushState({}, "", "/");
      setPage({ type: "home" });
    }
    setActiveCategory("");
    setRecipes([]);
    setRecipeQuery("");
    setCategoryMenuOpen(false);
    setSearchMenuOpen(false);
    window.scrollTo({ top: 0, behavior: smooth ? "smooth" : "auto" });
  }, []);

  const openAnswerPage = useCallback((question, mode, recipeResults) => {
    saveScrollPosition();
    const params = new URLSearchParams({ q: question, mode });
    const state = Array.isArray(recipeResults) ? { recipeResults } : {};
    window.history.pushState(state, "", `/answer?${params}`);
    setAnswerOpen(false);
    setPage({ type: "answer", query: question, mode, recipeResults });
    window.scrollTo({ top: 0 });
  }, []);

  const submitSearch = async (question) => {
    const value = question.trim();
    if (!value || searching) return;
    setChatInput("");
    setSearchError("");
    if (hasRecipeQuestionIntent(value)) {
      openAnswerPage(value, "recipe");
      return;
    }
    setSearching(true);
    try {
      const response = await fetch(
        apiUrl(`/api/search/recipes?${new URLSearchParams({ query: value, limit: "12" })}`),
      );
      if (!response.ok) throw new Error("Recipe lookup failed");
      const data = await response.json();
      const results = data.results || [];
      openAnswerPage(value, chooseSearchMode(value, results), results);
    } catch (requestError) {
      console.error("检索模式判断失败", requestError);
      setSearchError("检索失败，请重试。");
    } finally {
      setSearching(false);
    }
  };

  const activeCount = useMemo(
    () => categories.find((item) => item.name === activeCategory)?.count || 0,
    [activeCategory, categories],
  );

  const goToCategory = (categoryName) => {
    if (!categoryName) return;
    const categoryPath = `/category/${encodeURIComponent(categoryName)}`;
    const isCurrentCategory = page.type === "category" && activeCategory === categoryName;
    const isClearingQuery = Boolean(recipeQuery.trim());
    if (window.location.pathname !== categoryPath) {
      saveScrollPosition();
      window.history.pushState({}, "", categoryPath);
    }
    setPage({ type: "category", categoryName });
    setActiveCategory(categoryName);
    if (!isCurrentCategory || isClearingQuery) setRecipes([]);
    setRecipeQuery("");
    setCatalogError("");
    setCategoryMenuOpen(false);
    setSearchMenuOpen(false);
    window.scrollTo({ top: 0 });
  };

  const submitNameSearch = async (event) => {
    event.preventDefault();
    const value = nameSearchInput.trim();
    if (!value) return;
    saveScrollPosition();
    window.history.pushState({}, "", `/search?${new URLSearchParams({ q: value })}`);
    setNameSearchInput("");
    setSearchMenuOpen(false);
    setPage({ type: "search", query: value });
    window.scrollTo({ top: 0 });
  };

  const askAboutDetail = (event) => {
    event.preventDefault();
    const value = detailQuestion.trim();
    if (!value || !detail) return;
    setDetailQuestion("");
    startStream({
      title: `${detail.dish_name} · ${value}`,
      url: "/api/chat/stream",
      body: { question: `关于${detail.dish_name}：${value}` },
    });
  };

  const detailAnswerOpen = page.type === "recipe" && answerOpen;

  return (
    <div className={`app-shell ${detailAnswerOpen ? "detail-answer-open" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <span>知味 AI</span><i>雅</i>
        </div>
        <nav aria-label="主要导航">
          <button className="nav-link" onClick={() => goHome(true)}>首页</button>
          <div ref={searchMenuRef} className={searchMenuOpen ? "search-menu open" : "search-menu"}>
            <button
              className="nav-link"
              type="button"
              onClick={() => {
                setSearchMenuOpen((value) => !value);
                setCategoryMenuOpen(false);
              }}
              aria-haspopup="dialog"
              aria-expanded={searchMenuOpen}
            >
              搜索
            </button>
            <div className="name-search-panel" role="dialog" aria-label="按菜名搜索">
              <form onSubmit={submitNameSearch}>
                <input
                  value={nameSearchInput}
                  onChange={(event) => setNameSearchInput(event.target.value)}
                  placeholder="输入菜名关键词"
                  aria-label="菜名关键词"
                />
                <button type="submit" disabled={!nameSearchInput.trim()} aria-label="搜索菜名">
                  <MagnifyingGlass size={18} weight="bold" />
                </button>
              </form>
              <p className="name-search-message">按回车或点击图标搜索菜名</p>
            </div>
          </div>
          <div
            ref={categoryMenuRef}
            className={categoryMenuOpen ? "nav-menu open" : "nav-menu"}
          >
            <div className="nav-menu-trigger">
              <button
                className="nav-link"
                onClick={() => {
                  setCategoryMenuOpen((value) => !value);
                  setSearchMenuOpen(false);
                }}
                aria-haspopup="menu"
                aria-expanded={categoryMenuOpen}
              >
                菜类
              </button>
            </div>
            <div className="category-menu" role="menu" aria-label="菜类分类">
              {categories.map((category) => (
                <button
                  key={category.name}
                  role="menuitem"
                  className={activeCategory === category.name ? "active" : ""}
                  onClick={() => goToCategory(category.name)}
                >
                  <span>{category.name}</span><small>{category.count} 道</small>
                </button>
              ))}
            </div>
          </div>
        </nav>
        <div className="header-actions" />
      </header>

      {page.type === "home" && (
        <>
          <section className="hero" aria-label="知味 AI 首页">
            <img className="room-backdrop" src={roomImage} alt="" />
            <main className="hero-content">
              <p className="eyebrow"><Sparkle size={15} weight="fill" /> AI 懂你的每一次选择</p>
              <h1>懂你的口味<br />找到对的美味</h1>
              <p className="subtitle">告诉我此刻的心情与口味，让菜谱知识库为你找到下一餐。</p>
              <img className="aroma-mark" src={aromaImage} alt="" />
              <form className="chat-input-container" onSubmit={(event) => {
                event.preventDefault();
                submitSearch(chatInput);
              }}>
                <div className="chat-input-wrapper">
                  <span className="chat-input-symbol" aria-hidden="true"><Sparkle size={17} weight="fill" /></span>
                  <input
                    className="chat-input"
                    placeholder="想吃什么？问问知味"
                  value={chatInput}
                    onChange={(event) => setChatInput(event.target.value.slice(0, MAX_CHAT_LENGTH))}
                    maxLength={MAX_CHAT_LENGTH}
                    aria-label="输入菜谱问题"
                  />
                  <button className="chat-send-button" disabled={!chatInput.trim() || searching} aria-label="搜索菜谱">
                    {searching ? <span className="button-loader" /> : <ArrowRight size={21} weight="bold" />}
                  </button>
                </div>
                <div className="chat-suggestions" aria-label="快捷问题">
                  <span>灵感</span>
                  {quickQuestions.map((question) => (
                    <button type="button" key={question} onClick={() => submitSearch(question)}>{question}</button>
                  ))}
                </div>
              </form>
            </main>
          </section>

          <section className="recommendations-section" id="recommendations">
            <div className="section-heading">
              <div><p>TODAY&apos;S SELECTION</p><h2>今日推荐</h2></div>
              <div className="recommendation-heading-actions">
                <span>从菜谱知识库中随机挑选六道</span>
                <button onClick={loadRecommendations} disabled={recommendationsLoading}>
                  <ArrowsClockwise size={17} className={recommendationsLoading ? "spinning" : ""} />换一换
                </button>
              </div>
            </div>
            {recommendationsError && (
              <div className="catalog-notice recommendation-error" role="status">
                <span>{recommendationsError}</span><button onClick={loadRecommendations}>重新加载</button>
              </div>
            )}
            <div className="recommendation-grid" aria-busy={recommendationsLoading}>
              {recommendations.map((recipe) => (
                <RecipeCard
                  className="recommendation-card"
                  key={`${recipe.category}-${recipe.dish_name}`}
                  recipe={recipe}
                  onOpen={openRecipe}
                />
              ))}
            </div>
            {recommendationsLoading && !recommendations.length && <p className="empty-state">正在挑选今日推荐…</p>}
          </section>

        </>
      )}

      {page.type === "category" && (
        <main className="category-page">
          <section className="recipes-section" id="recipes">
            {catalogError && <p className="catalog-notice" role="status">{catalogError}</p>}
            <div className="recipes-toolbar">
              <div><p>RECIPE LIBRARY</p><h2>{activeCategory}<span>{activeCount} 道</span></h2></div>
              <div className="recipe-search-container">
                <div className="recipe-search-orbit">
                  <span className="recipe-search-glow" aria-hidden="true" />
                  <span className="recipe-search-rim" aria-hidden="true" />
                  <div className="recipe-search-wrapper">
                    <span className="recipe-search-symbol" aria-hidden="true"><MagnifyingGlass size={17} weight="bold" /></span>
                    <input
                      value={recipeQuery}
                      onChange={(event) => setRecipeQuery(event.target.value)}
                      placeholder={`在${activeCategory}中搜索`}
                      aria-label="搜索菜谱"
                    />
                  </div>
                </div>
              </div>
            </div>
            <div className="recipe-grid">
              {recipes.map((recipe) => <RecipeCard key={recipe.dish_name} recipe={recipe} onOpen={openRecipe} />)}
            </div>
            {!recipes.length && !catalogError && <p className="empty-state">没有找到符合条件的菜谱。</p>}
          </section>
        </main>
      )}

      {page.type === "search" && (
        <main className="search-page">
          <button className="page-back" onClick={() => window.history.back()}><ArrowLeft size={18} />返回</button>
          <div className="search-page-heading">
            <p>RECIPE SEARCH</p>
            <h1>“{page.query}”的搜索结果</h1>
            <span>{searching ? "正在检索菜谱知识库…" : `共找到 ${searchResults.length} 道相关菜谱`}</span>
          </div>
          {searchError && <div className="catalog-notice" role="alert">{searchError}</div>}
          <div className="search-results-grid">
            {searchResults.map((recipe) => (
              <RecipeCard key={`${recipe.category}-${recipe.dish_name}`} recipe={recipe} onOpen={openRecipe} />
            ))}
          </div>
          {!searching && !searchResults.length && !searchError && <p className="empty-state">没有找到结构化菜谱。</p>}
        </main>
      )}

      {page.type === "answer" && (
        <main className="ai-answer-page">
          <div className="answer-page-statusbar">
            <span>
              <Sparkle size={21} weight="fill" />
              {page.mode === "cards"
                ? (answerRecipeLoading
                  ? "正在查找本地菜谱"
                  : error
                    ? "本地菜谱搜索失败"
                    : answerRecipeResults.length
                      ? "已找到本地菜谱"
                      : "未找到本地菜谱")
                : (streaming ? "知味 AI 正在生成回答" : "知味 AI 已完成回答")}
            </span>
            <button className="page-back" onClick={() => window.history.back()}>
              <ArrowLeft size={18} />返回上一页
            </button>
          </div>

          <article className="ai-answer-shell">
            <header className="ai-answer-header">
              <div className="ai-answer-mark"><Sparkle size={31} weight="fill" /></div>
              <div>
                <p>{page.mode === "cards" ? "RECIPE RESULTS" : page.mode === "recipe" ? "RECIPE KNOWLEDGE" : "ZHIWEI ASSISTANT"}</p>
                <h1>{page.query}</h1>
                <div className="ai-answer-tags">
                  <span>知味 AI</span>
                  <span>{page.mode === "cards" ? "本地菜谱匹配" : page.mode === "recipe" ? "菜谱知识库回答" : "饮食助手回答"}</span>
                </div>
              </div>
            </header>

            <section className="ai-answer-body" aria-live="polite">
              <div className="ai-answer-body-heading">
                <h2><Sparkle size={22} />{page.mode === "cards" ? "为你找到这些菜谱" : "知味回答"}</h2>
                {streaming && <span><i />生成中</span>}
              </div>
              {page.mode === "cards" ? (
                <>
                  {answerRecipeLoading && <p className="answer-waiting">正在从菜谱库匹配菜名…</p>}
                  {!answerRecipeLoading && answerRecipeResults.length > 0 && (
                    <div className="answer-page-recipe-grid">
                      {answerRecipeResults.map((recipe) => (
                        <RecipeCard key={`${recipe.category}-${recipe.dish_name}`} recipe={recipe} onOpen={openRecipe} />
                      ))}
                    </div>
                  )}
                  {!answerRecipeLoading && !answerRecipeResults.length && !error && (
                    <p className="empty-state">没有找到匹配的本地菜谱。</p>
                  )}
                </>
              ) : (
                <div className={`ai-answer-text ${streaming ? "is-streaming" : ""}`}>
                  {answer || (!error && <span className="answer-waiting">正在理解你的问题…</span>)}
                </div>
              )}
              {error && (
                <div className="answer-error" role="alert">
                  <span>{error}</span>
                  <button onClick={() => (
                    page.mode === "cards"
                      ? loadCardResults(page.query)
                      : lastRequest && startStream(lastRequest)
                  )}>{page.mode === "cards" ? "重新搜索" : "重新生成"}</button>
                </div>
              )}
              {streaming && (
                <button className="answer-page-stop" onClick={stopStream}>
                  <Pause size={16} weight="fill" />停止生成
                </button>
              )}
            </section>

            {page.mode === "recipe" && sources.length > 0 && (
              <section className="answer-page-sources">
                <p>相关菜谱</p>
                <div className="answer-page-recipe-grid">
                  {sources.map((source) => (
                    <RecipeCard key={`${source.category}-${source.dish_name}`} recipe={source} onOpen={openRecipe} />
                  ))}
                </div>
              </section>
            )}
          </article>

          <form className="answer-page-composer" onSubmit={(event) => {
            event.preventDefault();
            const value = answerInput.trim();
            if (!value) return;
            setAnswerInput("");
            submitSearch(value);
          }}>
            <span><Sparkle size={21} weight="fill" /></span>
            <input
              value={answerInput}
              onChange={(event) => setAnswerInput(event.target.value.slice(0, MAX_CHAT_LENGTH))}
              maxLength={MAX_CHAT_LENGTH}
              placeholder="继续问知味"
              aria-label="继续向知味提问"
            />
            <button disabled={!answerInput.trim() || searching} aria-label="发送新问题">
              <ArrowRight size={21} weight="bold" />
            </button>
          </form>
          <p className="ai-disclaimer">AI 生成内容仅供参考；饮食建议请结合个人健康情况判断。</p>
        </main>
      )}

      {page.type === "recipe" && (
        <main className="recipe-detail-page">
          <div className="detail-statusbar">
            <span><CheckCircle size={22} weight="fill" />已从菜谱数据库中检索到结果</span>
            <button className="page-back" onClick={() => window.history.back()}><ArrowLeft size={18} />返回上一页</button>
          </div>

          {detailLoading && <div className="detail-state"><span className="large-loader" />正在读取菜谱…</div>}
          {detailError && (
            <div className="detail-state error-state">
              <strong>没有找到这道菜谱</strong><span>{detailError}</span>
              <button onClick={() => goHome()}>返回首页</button>
            </div>
          )}
          {detail && (
            <>
              <article className="detail-shell">
                <section className="detail-hero">
                  <RecipeArtwork recipe={detail} className="detail-image-slot" />
                  <div className="detail-summary">
                    <p className="detail-kicker">知味菜谱 · {detail.category}</p>
                    <h1>{detail.dish_name}<span aria-hidden="true" /></h1>
                    {detail.description && <p className="detail-description">{detail.description}</p>}
                    <div className="detail-tags">
                      <span>{detail.category}</span><span>{detail.difficulty}</span>
                    </div>
                    {(detail.cook_time || detail.servings) && (
                      <div className="detail-metrics">
                        {detail.cook_time && <div><Clock size={27} /><strong>{detail.cook_time}</strong><small>烹饪时间</small></div>}
                        {detail.servings && <div><UsersThree size={28} /><strong>{detail.servings}</strong><small>参考份量</small></div>}
                      </div>
                    )}
                  </div>
                </section>

                <section className="detail-columns">
                  <div className="detail-card ingredients-card">
                    <h2><Sparkle size={24} />所需食材</h2>
                    {detail.ingredient_groups.length ? detail.ingredient_groups.map((group, groupIndex) => (
                      <div className="ingredient-group" key={`${group.name}-${groupIndex}`}>
                        {detail.ingredient_groups.length > 1 && <h3>{group.name}</h3>}
                        <ul>
                          {group.items.map((item, index) => (
                            <li key={`${item.name}-${index}`}>
                              <span>{item.name}</span><i /><strong>{item.amount}</strong>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )) : <p className="missing-content">原文未提供可解析的食材清单。</p>}
                  </div>

                  <div className="detail-card steps-card">
                    <h2><Sparkle size={24} />制作步骤</h2>
                    {detail.step_groups.length ? detail.step_groups.map((group, groupIndex) => (
                      <div className="step-group" key={`${group.name}-${groupIndex}`}>
                        {detail.step_groups.length > 1 && <h3>{group.name}</h3>}
                        <ol>
                          {group.steps.map((step, index) => <li key={`${index}-${step}`}><span>{index + 1}</span><p>{step}</p></li>)}
                        </ol>
                      </div>
                    )) : <p className="missing-content">原文未提供可解析的制作步骤。</p>}
                  </div>
                </section>

                {detail.tips.length > 0 && (
                  <section className="detail-tips">
                    <Lightbulb size={31} />
                    <div><h2>小贴士</h2>{detail.tips.map((tip, index) => <p key={`${index}-${tip}`}>{tip}</p>)}</div>
                  </section>
                )}
              </article>

              <div className="detail-composer-dock">
                <form className="detail-ai-composer" onSubmit={askAboutDetail}>
                  <span><Sparkle size={22} weight="fill" /></span>
                  <input
                    value={detailQuestion}
                    onChange={(event) => setDetailQuestion(event.target.value.slice(0, MAX_CHAT_LENGTH))}
                    maxLength={MAX_CHAT_LENGTH}
                    placeholder={`继续问知味：${detail.dish_name}还能怎么做？`}
                    aria-label={`询问关于${detail.dish_name}的问题`}
                  />
                  <button disabled={!detailQuestion.trim()} aria-label="发送问题"><ArrowRight size={22} weight="bold" /></button>
                </form>
                <p className="ai-disclaimer">AI 生成内容仅供参考，请根据个人口味和实际情况调整。</p>
              </div>
            </>
          )}
        </main>
      )}

      {answerOpen && (
        <div className={`overlay answer-overlay ${detailAnswerOpen ? "detail-answer-overlay" : ""}`}>
          <section
            className="answer-panel"
            role={detailAnswerOpen ? "complementary" : "dialog"}
            aria-modal={detailAnswerOpen ? undefined : "true"}
            aria-labelledby="answer-title"
          >
            <header>
              <div><p><Sparkle size={15} weight="fill" /> 知味正在翻阅菜谱</p><h2 id="answer-title">{answerTitle}</h2></div>
              <button className="close-button" onClick={() => {
                stopStream();
                setAnswerOpen(false);
              }} aria-label="关闭回答"><X size={21} /></button>
            </header>
            <div className="answer-scroll">
              {sources.length > 0 && (
                <div className="source-list">
                  <span>参考菜谱</span>
                  <div>
                    {sources.map((source) => (
                      <button key={`${source.category}-${source.dish_name}`} onClick={() => {
                        stopStream();
                        setAnswerOpen(false);
                        openRecipe(source);
                      }}>
                        <RecipeArtwork recipe={source} />
                        <span><strong>{source.dish_name}</strong><small>{source.category} · {source.difficulty}</small></span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className={`answer-content ${streaming ? "is-streaming" : ""}`}>
                <strong className="answer-content-heading"><Sparkle size={19} />知味回答</strong>
                <div className="answer-content-copy">
                  {answer || (!error && <span className="answer-waiting">正在检索相关菜谱…</span>)}
                </div>
              </div>
              {error && (
                <div className="answer-error" role="alert">
                  <span>{error}</span><button onClick={() => lastRequest && startStream(lastRequest)}>重新生成</button>
                </div>
              )}
            </div>
            <footer>
              {streaming
                ? <button className="stop-button" onClick={stopStream}><Pause size={17} weight="fill" />停止生成</button>
                : <span>回答来自本地菜谱知识库</span>}
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}
