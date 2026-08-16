"""Safety-enforcing facade for the packaged LED WASM runtime."""

from __future__ import annotations

from . import _led_wasm_legacy as _legacy

RawSdLedWasmController = _legacy.SdLedWasmController


class SdLedWasmController(RawSdLedWasmController):
    """Compile every visible program before the renderer can display it."""

    def __init__(self, led_count: int = 8, wasm_bytes: bytes | None = None):
        super().__init__(led_count=led_count, wasm_bytes=wasm_bytes)
        self._last_safe_program = "off 250ms"

    def parse(self, text: str, now_ms: int):
        from .presentation_compiler import compile_presentation_program

        compiled = compile_presentation_program(
            str(text),
            led_count=self.led_count,
            fallback=self._last_safe_program,
        )
        program = compiled.program
        result = super().parse(program, now_ms)
        if result.ok:
            self._last_safe_program = program
        return result


_legacy.RawSdLedWasmController = RawSdLedWasmController
_legacy.SdLedWasmController = SdLedWasmController

for _name in dir(_legacy):
    if _name.startswith("__") or _name in globals():
        continue
    globals()[_name] = getattr(_legacy, _name)

__all__ = tuple(sorted(name for name in globals() if not name.startswith("_")))
