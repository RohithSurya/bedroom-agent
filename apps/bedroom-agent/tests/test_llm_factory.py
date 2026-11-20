from llm.factory import build_llm_client


def test_default_template_kwargs_disable_thinking():
    c = build_llm_client(model="m", timeout_s=1, base_url="http://127.0.0.1:9/v1")
    assert c.chat_template_kwargs == {"enable_thinking": False}


def test_template_kwargs_are_configurable():
    c = build_llm_client(
        model="m",
        timeout_s=1,
        base_url="http://127.0.0.1:9/v1",
        chat_template_kwargs={"reasoning_strength": "low"},
    )
    assert c.chat_template_kwargs == {"reasoning_strength": "low"}
