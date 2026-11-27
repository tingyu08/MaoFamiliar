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
from familiar.gemini.decisions import Answer, DecisionResult
from familiar.gemini.interactions import AgentTurn
from familiar.knowledge.completion_check import CompletionChecker
from familiar.knowledge.decision_actor import DecisionActor
from familiar.knowledge.reflex import ReflexConfig, ReflexDecider
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


DEFAULT_ELEMENTS = (Element("e1", "button", "送出", 10, 10, 60, 40, css="#go"),)


class FakeExecutor:
    environment = Environment.BROWSER

    def __init__(self, static_screen: bool = False, password: bool = False,
                 elements: tuple[Element, ...] = DEFAULT_ELEMENTS, css_refs: dict[str, str] | None = None,
                 failing_kinds: frozenset[str] = frozenset()):
        self.static_screen = static_screen
        self.password = password
        self.element_list = elements
        self.css_refs = css_refs or {}
        self.failing_kinds = failing_kinds
        self.focus: dict[str, int] | None = None
        self.executed: list[Action] = []
        self.started: list[str] = []
        self._shots = 0

    async def start(self, target: str) -> None:
        self.started.append(target)

    async def screenshot(self) -> Screenshot:
        self._shots += 1
        return Screenshot(frame_png(0 if self.static_screen else self._shots), Frame(320, 200))

    async def execute(self, action: Action) -> ExecResult:
        self.executed.append(action)
        if action.kind.value in self.failing_kinds:
            return ExecResult(False, "模擬失敗")
        target = next((e for e in self.element_list if e.ref == action.args.get("ref")), None)
        locator = ({"role": target.role, "name": target.name, "css": target.css, **target.box,
                    "point": list(target.center), "frame": [320, 200]} if target else None)
        return ExecResult(True, locator=locator)

    async def context_info(self) -> dict[str, str]:
        return {"url": "https://example.test/form", "title": "表單"}

    async def elements(self) -> ElementTable:
        return ElementTable(self.element_list)

    async def is_password_target(self, action: Action) -> bool:
        return self.password

    async def ref_for_css(self, css: str) -> str | None:
        return self.css_refs.get(css)

    async def focus_box(self) -> dict[str, int] | None:
        return self.focus

    async def reveal_focus(self) -> bool:
        self.revealed = getattr(self, "revealed", 0) + 1
        return False

    async def element_at(self, x: float, y: float) -> dict[str, Any] | None:
        hit = next((e for e in self.element_list if e.x1 <= x <= e.x2 and e.y1 <= y <= e.y2), None)
        return {"role": hit.role, "name": hit.name} if hit else {"role": "body", "name": ""}

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
                 on_event: Callable[[dict[str, Any]], None] | None = None, decisions: Any = None,
                 completion: Any = None, reflex: Any = None, reflex_config: ReflexConfig | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.client = client
        clock = clock or (lambda: datetime.now(UTC))
        self.executor = executor or FakeExecutor()
        self.control = TaskControl()
        self.sink = RecordingSink(on_event)
        self.steps: list[StepInfo] = []
        self.statuses: list[TaskStatus] = []

        async def no_sleep(_seconds: float) -> None:
            return None

        router = ModelRouter(RouterConfig(chains={Role.ACTION: chain, Role.DECISION: ("decision-1",)}),
                             clock=clock, sleep=no_sleep)
        actor = DecisionActor(router, decisions) if decisions is not None else None
        checker = CompletionChecker(router, completion) if completion is not None else None
        decider = ReflexDecider(router, reflex, reflex_config) if reflex is not None else None
        deps = LoopDeps(router=router, client=client, backend=CustomFcBackend(), executor=self.executor,
                        events=self.sink, control=self.control, safety=SafetyConfig(),
                        clock=clock, on_step=self._on_step, on_status=self._on_status,
                        actor=actor, completion=checker, reflex=decider)
        self.loop = AgentLoop(deps, config or LoopConfig())

    async def _on_step(self, info: StepInfo) -> None:
        self.steps.append(info)

    async def _on_status(self, status: TaskStatus) -> None:
        self.statuses.append(status)

    async def run(self, spec: TaskSpec = SPEC):
        return await self.loop.run(spec)


class ScriptedDecisions:
    """照順序回傳 Decisions 結果。項目可以是 (選項, 信心) → choice；浮點數 → noul 機率；或例外。"""

    def __init__(self, *script: Any):
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def decide(self, *, model, image_png, questions):
        self.requests.append({"model": model, "questions": questions, "png": image_png})
        item = self.script.pop(0) if self.script else ("none", 0.99)
        if isinstance(item, Exception):
            raise item
        key = next(iter(questions))
        if isinstance(item, float):
            answer = Answer(kind="noul", probability=item, confidence=max(item, 1 - item))
        else:
            answer = Answer(kind="choice", choice=item[0], confidence=item[1])
        return DecisionResult(answers={key: answer}, tokens_in=1200)


def later(fn: Callable[[], Any]) -> None:
    """在事件發出後稍晚執行（等待端的 future 建立後才回應）。"""
    asyncio.get_running_loop().call_later(0.01, fn)
