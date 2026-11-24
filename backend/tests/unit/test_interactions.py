from types import SimpleNamespace

from familiar.gemini.interactions import GeminiInteractionsClient, turn_from_interaction


def fake_interaction():
    return SimpleNamespace(
        id="int-1",
        output_text="好的",
        steps=[
            SimpleNamespace(type="model_output"),
            SimpleNamespace(type="function_call", id="c1", name="click", arguments={"x": 1, "y": 2}),
        ],
        usage=SimpleNamespace(total_input_tokens=1340, total_output_tokens=33),
    )


def test_turn_from_interaction():
    turn = turn_from_interaction(fake_interaction())
    assert turn.interaction_id == "int-1"
    assert turn.text == "好的"
    assert turn.function_calls == ({"id": "c1", "name": "click", "arguments": {"x": 1, "y": 2}},)
    assert (turn.tokens_in, turn.tokens_out) == (1340, 33)


def test_missing_fields_are_tolerated():
    turn = turn_from_interaction(SimpleNamespace(id=None, output_text=None, steps=None, usage=None))
    assert turn.function_calls == () and turn.text == "" and turn.tokens_in == 0


async def test_client_passes_arguments_and_omits_empty_previous_id():
    captured = {}

    class FakeInteractions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return fake_interaction()

    sdk = SimpleNamespace(interactions=FakeInteractions())
    client = GeminiInteractionsClient(sdk)
    turn = await client.create_turn(model="m", system_instruction="s", input=[{"type": "text", "text": "hi"}],
                                    tools=[{"type": "function"}], previous_interaction_id=None)
    assert turn.interaction_id == "int-1"
    assert captured["model"] == "m" and captured["system_instruction"] == "s"
    assert "previous_interaction_id" not in captured

    await client.create_turn(model="m", system_instruction="s", input=[], tools=[], previous_interaction_id="p")
    assert captured["previous_interaction_id"] == "p"
