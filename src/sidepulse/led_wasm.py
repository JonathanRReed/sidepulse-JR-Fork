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
        if not compiled.accepted:
            # Surface the firmware's own verdict for the refused text so
            # validation callers (the editor, parity tests) see the real
            # error, then park the renderer on the last safe program.
            result = super().parse(str(text), now_ms)
            super().parse(self._last_safe_program, now_ms)
            if result.ok:
                # The safety compiler refused something the firmware would
                # accept: never display it, and never report success for it.
                return type(result)(
                    ok=False,
                    error=result.error,
                    error_name="unsafe-presentation",
                    line=0,
                    column=0,
                )
            return result
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
