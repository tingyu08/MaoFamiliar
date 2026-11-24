// WebSocket：連線後先送 auth，斷線自動重連（間隔 1、2、4… 最多 10 秒）

import type { ConnectionState, ServerEvent } from "../state/types";

export type ClientMessage =
  | { type: "confirm_response"; request_id: string; approved: boolean }
  | { type: "user_help_done"; request_id: string }
  | { type: "stop"; task_id: string };

export interface SocketHandlers {
  onEvent: (event: ServerEvent) => void;
  onState: (state: ConnectionState) => void;
}

type SocketFactory = (url: string) => WebSocket;

const MAX_DELAY_MS = 10_000;
const UNAUTHORIZED = 4401;

export function defaultSocketUrl(location: Location = window.location): string {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}/ws`;
}

export class LiveSocket {
  private socket: WebSocket | null = null;
  private delay = 1000;
  private closed = false;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly url: string,
    private readonly token: string,
    private readonly handlers: SocketHandlers,
    private readonly factory: SocketFactory = (u) => new WebSocket(u),
  ) {}

  connect(): void {
    this.closed = false;
    this.handlers.onState("connecting");
    const socket = this.factory(this.url);
    this.socket = socket;
    socket.onopen = () => {
      socket.send(JSON.stringify({ type: "auth", token: this.token }));
      this.delay = 1000;
      this.handlers.onState("open");
    };
    socket.onmessage = (message) => {
      try {
        this.handlers.onEvent(JSON.parse(String(message.data)) as ServerEvent);
      } catch {
        // 忽略無法解析的訊息
      }
    };
    socket.onclose = (event) => {
      this.socket = null;
      this.handlers.onState("closed");
      if (this.closed || event.code === UNAUTHORIZED) return;
      this.timer = setTimeout(() => this.connect(), this.delay);
      this.delay = Math.min(this.delay * 2, MAX_DELAY_MS);
    };
  }

  send(message: ClientMessage): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify(message));
    return true;
  }

  close(): void {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    this.socket?.close();
  }
}
