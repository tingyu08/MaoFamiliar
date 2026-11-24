# Familiar

通用 AI 電腦操作代理人：給它一個網址和一句話，它會自己看畫面、自己操作瀏覽器，而且**越用越快**——做過的事可以存成技能，下次直接重播，不必再花 AI 額度。

目前完成 P1（網頁代理人）。桌面應用程式與遊戲（P2）、即時模式（P3）、排程（P4）見 [規格文件](docs/)。

## 它怎麼省 AI 額度：四層執行

每一步都從最便宜的方式開始試：

| 層 | 做法 | 花費 |
|---|---|---|
| 1. 技能重播 | 照錄好的步驟操作（依 role＋名稱 → 名稱 → CSS → 座標找元素） | 不呼叫 AI |
| 2. 判斷畫面 | Decisions API 判斷目前是哪個畫面（P2） | Decisions（每天 10,000 次） |
| 3. 選候選 | 在截圖上替可點的元素畫編號，讓 Decisions API 選下一步要點哪個 | Decisions |
| 4. 代理人模型 | Gemini 看截圖決定動作（要打字、規劃、卡住時才用） | 代理人模型（每天只有幾十次） |

技能重播失敗時（例如網站改版），AI 會從失敗的那一步接手，修好後自動存成技能的新版本。

## 安全設計

- **只在本機執行**：後端只綁 `127.0.0.1`，每次啟動產生新的 token；檢查 Host 與 Origin，防 DNS rebinding。
- **高風險動作一定要你確認**：送出、付款、刪除這類點擊，由模型自評加上 Decisions API 獨立判斷，任何一方認為有風險就跳確認窗；重播技能時也一樣。
- **不碰密碼**：密碼欄位一律不輸入，改請你自己處理；輸入框的值不會送給模型。
- **網頁內容不是指令**：系統指示明確要求模型不照網頁上的文字行事，並開啟提示注入偵測。
- 可設定封鎖的網域（`config.yaml` 的 `browser.blocked_domains`）。

> 免費版 Gemini 的輸入內容（包含截圖）可能被 Google 用來改善產品。請不要讓它操作含有敏感資料的頁面。

## 需求

- Windows 11
- [uv](https://docs.astral.sh/uv/)（會自動準備 Python 3.12 以上）
- Node.js 24（npm）
- Gemini API key（[AI Studio](https://aistudio.google.com/) 免費版即可）

## 安裝

```powershell
cd backend
uv sync
uv run playwright install chromium
Copy-Item .env.example .env   # 然後在 .env 填入 GEMINI_API_KEY
```

```powershell
cd frontend
npm install
npm run build
```

## 啟動

```powershell
cd backend
uv run python -m familiar.main
```

終端機會印出 `http://127.0.0.1:8765/?token=…`，用瀏覽器開啟這個網址。token 每次啟動都會換，只在這台電腦上有效。

使用方式：

1. 在「新任務」輸入網址和想做的事，按「開始」。AI 會開一個 Chromium 視窗操作，畫面與每一步會即時顯示在網頁上。
2. 遇到高風險動作會跳確認窗；需要登入或驗證碼時，AI 會請你在它的瀏覽器視窗裡處理。
3. 任務成功後按「存成技能」，之後在左邊「技能」按「重播」即可。

AI 用的瀏覽器資料（含登入狀態）存在 `backend/data/browser-profile/`，只在本機。

## 設定

`backend/config.yaml`：

- `gemini.chains`：依工作分工的模型退階鏈（操作、規劃）。
- `gemini.models`：各模型的免費版配額（從 AI Studio 控制台抄錄）。
- `gemini.overload`：503 過載時的重試與冷卻（還有備援模型時只重試 1 次就換；冷卻從 60 秒起逐步加長）。
- `gemini.actor`：第 3 層選候選的開關與信心門檻。
- `gemini.reflex`：遊戲模式的反射層。代理人模型寫「畫面情況 → 按鍵」規則，Decisions 每輪約 1.5 秒看遊戲畫面選規則、程式立刻按鍵，不必每步等代理人 3–10 秒。
- `browser.blocked_domains`：禁止前往的網域。

## 測試

```powershell
cd backend
uv run pytest --cov          # 單元、整合、E2E（E2E 需要先 npm run build）
uv run pytest -m live -s tests/live   # 冒煙測試：會用真的 Gemini、消耗免費額度
```

```powershell
cd frontend
npm test
```

沒有 API key 也能看完整流程的示範伺服器（照劇本回應的假模型）：

```powershell
cd backend
uv run python tests/e2e/demo_server.py
```

## 專案結構

```
backend/
  familiar/
    api/          REST、WebSocket、token 驗證
    backends/     動作後端：custom_fc（預設）、native_cu
    core/         代理人迴圈、第 3 層銜接、任務管理、安全規則
    executors/    Playwright 瀏覽器執行器、元素表
    gemini/       模型路由與配額、Interactions／Decisions 轉接
    knowledge/    技能（錄製、儲存、重播、自我修復）、Decisions 選候選
    storage/      SQLite、截圖
    vision/       畫面雜湊、縮圖、編號標記
  tests/          unit／integration／e2e／live
frontend/         React + Vite 介面
docs/             PRD、SRS、SDD、技術驗證報告
spikes/           第 0 週技術驗證腳本
```

## 文件

- [PRD.md](docs/PRD.md)：產品需求、能力邊界、決策紀錄
- [SRS.md](docs/SRS.md)：需求規格
- [SDD.md](docs/SDD.md)：架構與設計
- [spike-report.md](docs/spike-report.md)：第 0 週技術驗證結果（免費配額、各模型能力、Decisions 準確度）

## 目前的限制

- 只支援網頁；桌面應用程式與遊戲在 P2。
- 免費配額很少（部分模型每天 20 次），長任務可能要等額度重置（太平洋時間午夜，台灣時間下午 3 或 4 點）。
- 第 3 層的準確度主要在合成畫面上驗證過，真實網站仍在測試中。
- 技能目前是「整個任務」一個；自動切成多個片段、讓 AI 主動呼叫技能在 P2。
