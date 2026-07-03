from __future__ import annotations


def add_codex_model_options(
    command: list[str],
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    service_tier: str | None = None,
    fast_mode: bool | None = None,
) -> None:
    if model:
        command.extend(["--model", model])
    if reasoning_effort:
        command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    if service_tier:
        command.extend(["-c", f'service_tier="{service_tier}"'])
    if fast_mode is not None:
        command.extend(["-c", f"features.fast_mode={'true' if fast_mode else 'false'}"])
