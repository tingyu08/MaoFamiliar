// session token：後端啟動時印出的網址帶著 ?token=，第一次開啟後存在這個分頁的 sessionStorage，
// 並從網址列移除，避免截圖或分享畫面時外洩。

const STORAGE_KEY = "familiar.token";

function safeGet(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function safeSet(token: string): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, token);
  } catch {
    // 無痕模式等情況存不了：只影響重新整理後要重新用啟動網址開啟
  }
}

export function readToken(location: Location = window.location, history: History = window.history): string | null {
  const url = new URL(location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    safeSet(fromUrl);
    url.searchParams.delete("token");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
    return fromUrl;
  }
  return safeGet();
}
