"""AgentLoop 測試用的假物件：假執行器、照劇本回應的模型、可攔截事件的 sink。"""

from __future__ import annotations

import asyncio
import io
from datetime import UTC, datetime
from typing import Any, Callable

from PIL import Image, ImageDraw

from familiar.backends.custom_fc import CustomFcBackend
from familiar.core.agent_loop import AgentLoop
from familiar.core.agent_types import LoopConfig, LoopDeps, StepInfo, TaskSpec
from familiar.core.control import TaskControl
from familiar.core.models import Action, Environment, Frame
from familiar.core.safety import SafetyConfig
from familiar.core.tasks import TaskStatus
from familiar.executors.base import ExecResult, Screenshot
from familiar.executors.elements import Element, ElementTable
from familiar.gemini.interactions import AgentTurn
from familiar.gemini.model_router import ModelRouter, Role, RouterConfig

SPEC = TaskSpec(task_id="t1", instruction="送出表單", environment=Environment.BROWSER)


class ApiError(Exception):
    def __init__(self, status_code: int, message: str = ""):
        super().__init__(message)
        self.status_code = status_code


def frame_png(n: int) -> bytes:
    """每次畫面不同（方塊位置隨 n 移動），避免觸發卡住偵測。"""
    image = Image.new("RGB", (320, 200), (30, 30, 30))
    x = (n * 37) % 260
    ImageDraw.Draw(image).rectangle((x, 20 + (n * 23) % 120, x + 50, 70 + (n * 23) % 120), fill=(240, 240, 240))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class FakeExecutor:
    environment = Environment.BROWSER

    def __init__(self, static_screen: bool = False, password: bool = False):
        self.static_screen = static_screen
        self.password = password
        self.executed: list[Action] = []
        self._shots = 0

    async def start(self, target: str) -> None:
        pass

    async def screenshot(self) -> Screenshot:
        self._shots += 1
        return Screenshot(frame_png(0 if self.static_screen else self._shots), Frame(320, 200))

    async def execute(self, action: Action) -> ExecResult:
        self.executed.append(action)
        return ExecResult(True)

    async def context_info(self) -> dict[str, str]:
        return {"url": "https://example.test/form", "title": "表單"}

    async def elements(self) -> ElementTable:
        return ElementTable((Element("e1", "button", "送出", 10, 10, 60, 40),))

    async def is_password_target(self, action: Action) -> bool:
        return self.password

    async def close(self) -> None:
        pass


def call(name: str, call_id: str = "c1", **arguments) -> dict[str, Any]:
    return {"id": call_id, "name": name, "arguments": {"intent": name, **arguments}}


def turn(*calls: dict[str, Any], text: str = "", interaction_id: str = "i") -> AgentTurn:
    return AgentTurn(interaction_id=interaction_id, function_calls=tuple(calls), text=text, tokens_in=100)


class ScriptedClient:
    """依序回傳劇本中的結果；項目可以是 AgentTurn、例外，或 dict[model → 結果]。"""

    def __init__(self, *script: Any):
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def create_turn(self, *, model, system_instruction, input, tools, previous_interaction_id):
        self.requests.append({"model": model, "input": input, "previous": previous_interaction_id})
        item = self.script.pop(0)
        if isinstance(item, dict):
            item = item[model]
        if isinstance(item, Exception):
            raise item
        return item


class RecordingSink:
    def __init__(self, on_event: Callable[[dict[str, Any]], None] | None = None):
        self.events: list[dict[str, Any]] = []
        self.on_event = on_event

    async def emit(self, event):
        self.events.append(dict(event))
        if self.on_event:
            self.on_event(dict(event))

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]


class Harness:
    def __init__(self, client: ScriptedClient, executor: FakeExecutor | None = None,
                 config: LoopConfig | None = None, chain=("lite", "flash"),
                 on_event: Callable[[dict[str, Any]], None] | None = None):
        self.client = client
        self.executor = executor or FakeExecutor()
        self.control = TaskControl()
        self.sink = RecordingSink(on_event)
        self.steps: list[StepInfo] = []
        self.statuses: list[TaskStatus] = []

        async def no_sleep(_seconds: float) -> None:
            return None

        router = ModelRouter(RouterConfig(chains={Role.ACTION: chain}), clock=lambda: datetime.now(UTC),
                             sleep=no_sleep)
        deps = LoopDeps(router=router, client=client, backend=CustomFcBackend(), executor=self.executor,
                        events=self.sink, control=self.control, safety=SafetyConfig(),
                        clock=lambda: datetime.now(UTC), on_step=self._on_step, on_status=self._on_status)
        self.loop = AgentLoop(deps, config or LoopConfig())

    async def _on_step(self, info: StepInfo) -> None:
        self.steps.append(info)

    async def _on_status(self, status: TaskStatus) -> None:
        self.statuses.append(status)

    async def run(self):
        return await self.loop.run(SPEC)


def later(fn: Callable[[], Any]) -> None:
    """在事件發出後稍晚執行（等待端的 future 建立後才回應）。"""
    asyncio.get_running_loop().call_later(0.01, fn)
