from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from importlib import resources

LED_WASM_RESOURCE = "sdled.wasm"
ERROR_NAMES = (
    "ok",
    "null-input",
    "too-long",
    "too-many-lines",
    "too-many-animation-lines",
    "syntax",
    "bad-color",
    "bad-index",
    "bad-time",
    "bad-brightness",
    "bad-repeat",
    "trailing-input",
)


class LedWasmUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class LedWasmParseResult:
    ok: bool
    error: int
    error_name: str
    line: int
    column: int


def load_packaged_wasm() -> bytes:
    return (
        resources.files("sidepulse.resources")
        .joinpath(LED_WASM_RESOURCE)
        .read_bytes()
    )


class SdLedWasmController:
    """In-process wrapper around the firmware/websim LEDS.LED WASM engine."""

    def __init__(self, led_count: int = 8, wasm_bytes: bytes | None = None):
        self.led_count = 2 if int(led_count) == 2 else 8
        try:
            from JavaScriptCore import JSContext
        except Exception as exc:  # pragma: no cover - exercised off macOS/PyObjC.
            raise LedWasmUnavailableError("JavaScriptCore is unavailable.") from exc

        self.context = JSContext.alloc().init()
        wasm_base64 = base64.b64encode(wasm_bytes or load_packaged_wasm()).decode("ascii")
        self.context.evaluateScript_(_javascript_controller(wasm_base64))
        exception = self.context.exception()
        if exception is not None:
            detail = exception.toString()
            raise LedWasmUnavailableError(f"Could not instantiate sdled.wasm: {detail}")

        self._parse = self.context.objectForKeyedSubscript_("sdledParse")
        self._step = self.context.objectForKeyedSubscript_("sdledStep")
        self._step_batch = self.context.objectForKeyedSubscript_("sdledStepBatch")
        self._reset = self.context.objectForKeyedSubscript_("sdledReset")

    def reset(self, now_ms: int) -> None:
        self._reset.callWithArguments_([self.led_count, int(now_ms)])

    def parse(self, text: str, now_ms: int) -> LedWasmParseResult:
        value = self._parse.callWithArguments_([self.led_count, str(text), int(now_ms)])
        data = json.loads(value.toString())
        return LedWasmParseResult(
            ok=bool(data["ok"]),
            error=int(data["error"]),
            error_name=str(data["errorName"]),
            line=int(data["line"]),
            column=int(data["column"]),
        )

    def step(self, now_ms: int) -> list[tuple[int, int, int]]:
        value = self._step.callWithArguments_([self.led_count, int(now_ms)])
        data = json.loads(value.toString())
        return [
            (
                int(data[index]),
                int(data[index + 1]),
                int(data[index + 2]),
            )
            for index in range(0, len(data), 3)
        ]

    def step_batch(
        self,
        start_ms: int,
        interval_ms: int,
        frames: int,
    ) -> list[list[tuple[int, int, int]]]:
        """Render `frames` consecutive frames in ONE JavaScriptCore call.

        The per-frame PyObjC round-trip is the hottest path in the app
        while the Screen Bar animates; batching amortizes it without any
        assumption about the program's periodicity."""
        value = self._step_batch.callWithArguments_(
            [self.led_count, int(start_ms), int(interval_ms), int(frames)]
        )
        data = json.loads(value.toString())
        stride = self.led_count * 3
        return [
            [
                (
                    int(data[base + index]),
                    int(data[base + index + 1]),
                    int(data[base + index + 2]),
                )
                for index in range(0, stride, 3)
            ]
            for base in range(0, len(data), stride)
        ]


def _javascript_controller(wasm_base64: str) -> str:
    payload = json.dumps(wasm_base64)
    errors = json.dumps(list(ERROR_NAMES))
    return f"""
(function (global) {{
  "use strict";

  var ERROR_NAMES = {errors};
  var wasmBase64 = {payload};
  var instance = null;
  var exports = null;
  var memory = null;
  var inputPtr = 0;
  var outputPtr = 0;
  var initialized = {{}};

  function b64ToBytes(base64) {{
    var chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=";
    var bytes = [];
    var buffer = 0;
    var bits = 0;
    for (var i = 0; i < base64.length; i += 1) {{
      var c = base64.charAt(i);
      if (c === "=") {{
        break;
      }}
      var value = chars.indexOf(c);
      if (value < 0) {{
        continue;
      }}
      buffer = (buffer << 6) | value;
      bits += 6;
      if (bits >= 8) {{
        bits -= 8;
        bytes.push((buffer >> bits) & 255);
      }}
    }}
    return new Uint8Array(bytes);
  }}

  function normalizeLedCount(ledCount) {{
    return Number(ledCount) === 2 ? 2 : 8;
  }}

  function instantiate() {{
    if (instance !== null) {{
      return;
    }}
    var module = new WebAssembly.Module(b64ToBytes(wasmBase64));
    instance = new WebAssembly.Instance(module, {{}});
    exports = instance.exports;
    memory = exports.memory;
    inputPtr = exports.sdled_input_ptr();
    outputPtr = exports.sdled_output_ptr();
  }}

  function ensureInitialized(ledCount, nowMs) {{
    instantiate();
    var count = normalizeLedCount(ledCount);
    if (!initialized[count]) {{
      exports.sdled_reset(count, Math.floor(nowMs));
      initialized[count] = true;
    }}
    return count;
  }}

  function decodeParseResult(packed) {{
    var error = (packed >>> 8) & 255;
    return {{
      ok: (packed & 1) === 1,
      error: error,
      errorName: ERROR_NAMES[error] || ("error-" + error),
      line: (packed >>> 16) & 255,
      column: (packed >>> 24) & 255
    }};
  }}

  function writeInput(text) {{
    var input = new Uint8Array(memory.buffer, inputPtr, 512);
    for (var i = 0; i < input.length; i += 1) {{
      input[i] = 0;
    }}
    var length = 0;
    for (var j = 0; j < text.length; j += 1) {{
      var code = text.charCodeAt(j);
      if (code <= 127) {{
        if (length < 512) input[length] = code;
        length += 1;
      }} else if (code <= 2047) {{
        if (length < 512) input[length] = 192 | (code >> 6);
        if (length + 1 < 512) input[length + 1] = 128 | (code & 63);
        length += 2;
      }} else {{
        if (length < 512) input[length] = 224 | (code >> 12);
        if (length + 1 < 512) input[length + 1] = 128 | ((code >> 6) & 63);
        if (length + 2 < 512) input[length + 2] = 128 | (code & 63);
        length += 3;
      }}
    }}
    return length;
  }}

  global.sdledReset = function (ledCount, nowMs) {{
    instantiate();
    var count = normalizeLedCount(ledCount);
    exports.sdled_reset(count, Math.floor(nowMs));
    initialized[count] = true;
  }};

  global.sdledParse = function (ledCount, text, nowMs) {{
    var count = ensureInitialized(ledCount, nowMs);
    var length = writeInput(String(text));
    var packed = exports.sdled_parse(count, length > 512 ? 513 : length, Math.floor(nowMs));
    return JSON.stringify(decodeParseResult(packed));
  }};

  global.sdledStep = function (ledCount, nowMs) {{
    var count = ensureInitialized(ledCount, nowMs);
    exports.sdled_step(count, Math.floor(nowMs));
    var output = new Uint8Array(memory.buffer, outputPtr, count * 3);
    var pixels = [];
    for (var i = 0; i < output.length; i += 1) {{
      pixels.push(output[i]);
    }}
    return JSON.stringify(pixels);
  }};

  global.sdledStepBatch = function (ledCount, startMs, intervalMs, frames) {{
    var count = ensureInitialized(ledCount, startMs);
    var total = Math.max(1, Math.floor(frames));
    var interval = Math.max(1, Math.floor(intervalMs));
    var start = Math.floor(startMs);
    var pixels = [];
    for (var frame = 0; frame < total; frame += 1) {{
      exports.sdled_step(count, start + frame * interval);
      var output = new Uint8Array(memory.buffer, outputPtr, count * 3);
      for (var i = 0; i < output.length; i += 1) {{
        pixels.push(output[i]);
      }}
    }}
    return JSON.stringify(pixels);
  }};
}})(this);
"""
