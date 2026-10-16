"""Tests for setup_env.py. No real prompts or files outside tmp_path."""

import setup_env

SECRET = "sk-test-not-a-real-key-123"


def answers(*values):
    queue = list(values)
    return lambda prompt: queue.pop(0)


def test_writes_key_and_model_without_printing_key(tmp_path, capsys):
    env_path = tmp_path / ".env"
    code = setup_env.main(
        getpass_fn=answers(SECRET),
        input_fn=answers("claude-haiku-4-5-20251001"),
        env_path=env_path,
    )
    assert code == 0
    content = env_path.read_text(encoding="utf-8")
    assert f"APP_ANTHROPIC_API_KEY={SECRET}" in content
    assert "APP_ANTHROPIC_MODEL=claude-haiku-4-5-20251001" in content
    assert "ANTHROPIC_API_KEY=" not in content.replace("APP_ANTHROPIC_API_KEY=", "")

    captured = capsys.readouterr()
    assert SECRET not in captured.out
    assert SECRET not in captured.err


def test_blank_model_uses_default(tmp_path):
    env_path = tmp_path / ".env"
    setup_env.main(getpass_fn=answers(SECRET), input_fn=answers(""), env_path=env_path)
    assert "APP_ANTHROPIC_MODEL=claude-haiku-4-5-20251001" in env_path.read_text(encoding="utf-8")


def test_key_is_read_with_hidden_prompt(tmp_path):
    prompts = []

    def fake_getpass(prompt):
        prompts.append(prompt)
        return SECRET

    setup_env.main(getpass_fn=fake_getpass, input_fn=answers(""), env_path=tmp_path / ".env")
    assert len(prompts) == 1
    assert "APP_ANTHROPIC_API_KEY" in prompts[0]


def test_empty_key_writes_nothing(tmp_path):
    env_path = tmp_path / ".env"
    assert setup_env.main(getpass_fn=answers(""), input_fn=answers(""), env_path=env_path) == 1
    assert not env_path.exists()


def test_existing_env_kept_unless_confirmed(tmp_path, capsys):
    env_path = tmp_path / ".env"
    env_path.write_text("APP_ANTHROPIC_API_KEY=old\n", encoding="utf-8")

    assert setup_env.main(getpass_fn=answers(SECRET), input_fn=answers("n"), env_path=env_path) == 1
    assert env_path.read_text(encoding="utf-8") == "APP_ANTHROPIC_API_KEY=old\n"

    assert setup_env.main(getpass_fn=answers(SECRET), input_fn=answers("y", ""), env_path=env_path) == 0
    assert f"APP_ANTHROPIC_API_KEY={SECRET}" in env_path.read_text(encoding="utf-8")
    assert SECRET not in capsys.readouterr().out
