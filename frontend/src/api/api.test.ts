import { describe, expect, it, vi } from "vitest";
import { ApiError, createApi } from "./client";
import { LiveSocket } from "./socket";
import { readToken } from "./token";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

describe("createApi", () => {
  it("sends the token and unwraps data", async () => {
    const fetcher = vi.fn(() => json(201, { success: true, data: { id: "t1" }, error: null }));
    const api = createApi("tok", fetcher);
    const task = await api.createTask("https://a.b", "找公告");
    expect(task.id).toBe("t1");
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/tasks");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
    expect(JSON.parse(String(init.body))).toEqual({ url: "https://a.b", instruction: "找公告", mode: "ui" });
  });

  it("throws the server error message", async () => {
    const api = createApi("tok", () => json(409, { success: false, data: null, error: "已經有任務在執行" }));
    await expect(api.stopTask("x")).rejects.toMatchObject({ message: "已經有任務在執行", status: 409 });
  });

  it("explains network failures", async () => {
    const api = createApi("tok", () => Promise.reject(new TypeError("Failed to fetch")));
    await expect(api.quota()).rejects.toBeInstanceOf(ApiError);
    await expect(api.quota()).rejects.toThrow("連不到後端");
  });

  it("reads list meta and builds screenshot urls", async () => {
    const api = createApi("a b", () => json(200, { success: true, data: [], error: null, meta: { total: 7, limit: 20, offset: 0 } }));
    expect((await api.listTasks()).total).toBe(7);
    expect(api.screenshotUrl("t1", 2)).toBe("/api/tasks/t1/steps/2/screenshot?token=a%20b");
  });

  it("handles non-JSON error bodies", async () => {
    const api = createApi("tok", () => Promise.resolve(new Response("oops", { status: 500 })));
    await expect(api.getTask("t1")).rejects.toThrow("HTTP 500");
  });
});

describe("readToken", () => {
  it("moves the token from the url into session storage", () => {
    const replaceState = vi.fn();
    const token = readToken({ href: "http://127.0.0.1:8765/?token=abc#x" } as Location, { replaceState } as unknown as History);
    expect(token).toBe("abc");
    expect(replaceState).toHaveBeenCalledWith(null, "", "/#x");
    expect(readToken({ href: "http://127.0.0.1:8765/" } as Location, { replaceState } as unknown as History)).toBe("abc");
  });
});

class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 1;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  constructor() {
    FakeSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.onclose?.({ code: 1000 });
  }
}

describe("LiveSocket", () => {
  it("authenticates, forwards events and reconnects", () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", { OPEN: 1 });
    FakeSocket.instances = [];
    const events: unknown[] = [];
    const states: string[] = [];
    const socket = new LiveSocket("ws://x/ws", "tok", { onEvent: (e) => events.push(e), onState: (s) => states.push(s) },
      () => new FakeSocket() as unknown as WebSocket);
    socket.connect();
    const first = FakeSocket.instances[0];
    first.onopen?.();
    expect(JSON.parse(first.sent[0])).toEqual({ type: "auth", token: "tok" });
    first.onmessage?.({ data: JSON.stringify({ type: "hello" }) });
    first.onmessage?.({ data: "not json" });
    expect(events).toEqual([{ type: "hello" }]);
    expect(socket.send({ type: "stop", task_id: "t1" })).toBe(true);

    first.onclose?.({ code: 1006 });
    vi.advanceTimersByTime(1000);
    expect(FakeSocket.instances).toHaveLength(2);
    expect(states).toEqual(["connecting", "open", "closed", "connecting"]);

    FakeSocket.instances[1].onclose?.({ code: 4401 });
    vi.advanceTimersByTime(20_000);
    expect(FakeSocket.instances).toHaveLength(2); // token 錯誤不重連
    socket.close();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
});
