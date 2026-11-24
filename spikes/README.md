# 第 0 週技術驗證（spikes）

這裡是用完即丟的驗證腳本，不是正式程式碼。目的是回答 SDD 第 12 節的四個問題：

| 腳本 | 回答的問題 | 大約消耗 |
|---|---|---|
| `spike_01_capabilities.py` | 三個模型在免費版能不能用：純文字、截圖＋自訂函式、函式結果回傳、原生 `computer_use`（desktop／browser） | 每個模型 5 次，共 15 次 |
| `spike_02_click_accuracy.py` | 看截圖點按鈕準不準（命中率、像素誤差、延遲） | 每個模型 8 次，共 24 次 |
| `spike_03_decisions.py` | Decisions API 判斷畫面狀態、判斷高風險按鈕的準確度 | 20 次 Decisions |
| `make_fixtures.py` | 產生合成測試畫面（不碰真實遊戲） | 不呼叫 API |
| `spike_04_decision_driven.py` | 只用 Decisions API 決定點哪裡：文字候選、編號標記、兩段式格子 | 約 60 次 Decisions |
| `diag_function_result.py` | 診斷回傳函式結果的各種格式是否被接受 | 約 10 次 |

驗證結果整理在 [docs/spike-report.md](../docs/spike-report.md)。

免費版的每分鐘、每日上限要到 AI Studio 控制台查看，腳本無法直接查詢；腳本會記錄遇到的 429 錯誤。

## 執行步驟

1. 把 `.env.example` 複製成 `.env`，填入免費版 API key。
2. 依序執行：

```powershell
uv run python make_fixtures.py
```

```powershell
uv run python spike_01_capabilities.py
```

```powershell
uv run python spike_02_click_accuracy.py
```

```powershell
uv run python spike_03_decisions.py
```

如果 spike 01 顯示原生 `computer_use` 可以用，再補跑一次原生後端的點擊準度：

```powershell
uv run python spike_02_click_accuracy.py --backend native
```

結果會存在 `results/`（已列入 `.gitignore`）。
