// 即時連線：WebSocket 事件 → reducer；提供開始、停止、確認、求助完成等動作

import { useCallback, useEffect, useReducer, useRef } from "react";
import type { Api } from "../api/client";
import { ApiError } from "../api/client";
import { LiveSocket, defaultSocketUrl } from "../api/socket";
import { initialState, liveReducer } from "../state/reducer";
import type { ServerEvent } from "../state/types";

export function useLive(api: Api, token: string, socketUrl: string = defaultSocketUrl()) {
  const [state, dispatch] = useReducer(liveReducer, initialState);
  const socketRef = useRef<LiveSocket | null>(null);

  const showNotice = useCallback((message: string) => {
    dispatch({ type: "event", event: { type: "error", message } });
  }, []);

  useEffect(() => {
    const onEvent = (event: ServerEvent) => {
      dispatch({ type: "event", event });
      const taskId = event.type === "hello" ? event.current_task_id : null;
      if (typeof taskId === "string") {
        api.getTask(taskId).then((detail) => dispatch({ type: "load_task", detail })).catch(() => undefined);
      }
    };
    const socket = new LiveSocket(socketUrl, token, { onEvent, onState: (value) => dispatch({ type: "connection", value }) });
    socketRef.current = socket;
    socket.connect();
    api
      .quota()
      .then((quota) => dispatch({ type: "event", event: { type: "quota_update", quota } }))
      .catch(() => undefined);
    return () => socket.close();
  }, [api, token, socketUrl]);

  const start = useCallback(
    async (url: string, instruction: string): Promise<boolean> => {
      try {
        await api.createTask(url, instruction);
        return true;
      } catch (error) {
        showNotice(error instanceof ApiError ? error.message : "建立任務失敗");
        return false;
      }
    },
    [api, showNotice],
  );

  const stop = useCallback(async () => {
    if (!state.taskId) return;
    try {
      await api.stopTask(state.taskId);
    } catch (error) {
      showNotice(error instanceof ApiError ? error.message : "停止任務失敗");
    }
  }, [api, state.taskId, showNotice]);

  const send = useCallback(
    (message: Parameters<LiveSocket["send"]>[0]) => {
      if (socketRef.current?.send(message)) {
        dispatch({ type: "prompt_answered" });
      } else {
        showNotice("連線中斷，請稍候再試");
      }
    },
    [showNotice],
  );

  const answerConfirm = useCallback(
    (approved: boolean) => {
      if (state.confirm) send({ type: "confirm_response", request_id: state.confirm.requestId, approved });
    },
    [state.confirm, send],
  );

  const helpDone = useCallback(() => {
    if (state.help) send({ type: "user_help_done", request_id: state.help.requestId });
  }, [state.help, send]);

  const dismissNotice = useCallback(() => dispatch({ type: "dismiss_notice" }), []);

  return { state, start, stop, answerConfirm, helpDone, dismissNotice };
}
