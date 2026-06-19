# TUI Mouse-Wheel Scrolling

Why the user can scroll the chat history from **anywhere on the screen** with the
mouse wheel — and why this took three attempts to get right.

## TL;DR

The fix lives in **two** files:

- `loop/tui/composer.py` — `Composer` overrides `_on_mouse_scroll_*` to call
  `event.prevent_default()` then `super()._on_mouse_scroll_*()`. **It does NOT
  call `event.stop()`** so the event continues to bubble normally.
- `loop/tui/app.py` — `AgentTUIApp` overrides `on_event` (the only Textual
  hook that fires for **real driver input**) to route wheel events to the
  ChatLog. After `chat_log.scroll_to(...)` it directly posts
  `textual.messages.Update` and `UpdateScroll` to the screen so the compositor
  repaints on the next pump cycle instead of waiting for the next event.

If a future change "simplifies" any of these, scroll will break in real
terminals. Read the **Why** section before doing so.

## Why (the bug)

The user reported a confusing symptom:

> Mouse wheel doesn't immediately scroll the message page, but takes effect
> after switching focus.

Three things had to be true for this to happen:

1. **Wheel events were eaten by `Composer`.** `Composer` extends
   `Textual.ScrollView`. Even when its content is empty, `ScrollView.allow_vertical_scroll`
   is `True` and `Widget._on_mouse_scroll_up` calls `_scroll_up_for_pointer(animate=False)`.
   The default handler stops the event (`event.stop()`) **only when scroll moves**;
   for an empty composer it does not move, so it does **not** stop — the event
   bubbles. So this part isn't the cause, but the existence of the override
   was the reason we kept getting bitten by **other** things downstream.
2. **App-level `on_mouse_scroll_*` was dead code for real driver input.**
   Textual's driver path is `driver bytes → app._dispatch_message(event) → app.on_event(event)`.
   `on_event` then calls `screen._forward_event(event)` which posts the event
   to the **topmost widget at the cursor**, not to the App. That widget
   bubbles to its parent chain, but the App is **not** in that chain (App
   is the parent of the Screen, not of the widget). So `on_mouse_scroll_up`
   on the App class is never invoked by real driver input. Synthetic tests
   that call `app.post_message(event)` make it *look* like it works.
3. **`scroll_to(animate=False)` only schedules one repaint, then goes silent.**
   With animation on, the animator runs an `async` task that updates the
   reactive every frame, each one triggering `watch_scroll_y` → `_refresh_scroll`
   → repaint. With animation off, the scroll position is set once and the
   visual repaint is deferred to `check_idle() → Idle message → screen.update`.
   That chain can be **delayed** in real terminals until the next event
   (focus change, key press, agent token) wakes the loop. The user sees:
   "I scrolled, nothing moved, then I clicked somewhere and it jumped."

Opencode's TUI (the reference design) doesn't have this problem because
**opentui handles mouse at the renderer layer**: a single
`useMouse: !Flag.OPENCODE_DISABLE_MOUSE && input.config.mouse` config flag
decides whether the whole TUI receives mouse events. The prompt component
itself has zero wheel handlers, so events naturally reach the sibling
`<scrollbox>`. Textual gives us no such global mouse flag — we have to do
the routing ourselves.

## The fix, layer by layer

### Layer 1 — `Composer` does not consume wheel events

```python
def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
    event.prevent_default()
    return super()._on_mouse_scroll_up(event)
```

- `event.prevent_default()` stops TextArea's internal scroll behavior
  (which would try to scroll its own mostly-empty viewport).
- **No `event.stop()`** — the event keeps bubbling. The App-level handler
  can still see it.
- `super()._on_mouse_scroll_up` is a no-op for an empty composer (no
  movement, no stop), but if a future Textual version adds behavior we
  don't want to silently lose.

### Layer 2 — `App.on_event` intercepts wheel at the App boundary

This is the **only** hook in Textual's event flow that fires for real
driver input. `on_mouse_scroll_*` on the App class does not.

```python
async def on_event(self, event) -> None:
    if isinstance(event, MouseScrollUp) and not event.is_forwarded:
        if self._forward_scroll_to_chatlog(event, direction=-1):
            event.stop()
            return
    elif isinstance(event, MouseScrollDown) and not event.is_forwarded:
        if self._forward_scroll_to_chatlog(event, direction=1):
            event.stop()
            return
    await super().on_event(event)
```

`_forward_scroll_to_chatlog` does:

1. `chat_log.scroll_to(y=new_y, animate=False, immediate=True)` —
   synchronously sets `scroll_y` (the logical position).
2. `chat_log._set_dirty(chat_log.size.region)` — marks the widget for
   repaint.
3. **`self.screen.post_message(messages.Update(chat_log))`** — wakes the
   screen's `_on_update` handler, which adds chat_log to `_dirty_widgets`.
4. **`self.screen.post_message(messages.UpdateScroll())`** — wakes the
   screen's `_on_update_scroll` handler, which sets the screen's
   `_scroll_required` flag.

Without steps 3 and 4, the visual repaint is delayed until the next
event. With them, the screen's next pump cycle repaints.

### Why not just animate?

Animation would work — the animator task would repaint every frame. But
the user expects **instant** scroll, not a 50ms tween. `animate=False`
with the direct message post is the right tradeoff: instant position,
immediate repaint.

## Tests that guard this regression

- `tests/test_tui_manual_scroll.py` — original 8 tests, all still pass.
  These are the **synthetic** tests that post events directly to widgets.
  They will pass even if the App-level handling is broken, which is
  exactly the gap that bit us.
- `tests/test_status_bar.py::test_app_on_event_intercepts_wheel_before_screen_forward`
  — verifies `App.on_event` is the actual entry point for wheel events.
- `tests/test_status_bar.py::test_wheel_event_with_cursor_over_composer_uses_app_on_event`
  — simulates the real-world case: cursor is over the Composer (the most
  common case while typing) and the user wheels.
- `tests/test_status_bar.py::test_wheel_posts_update_messages_to_screen`
  — the most important regression test. Wraps `screen.post_message` and
  asserts that a wheel scroll results in `Update` and `UpdateScroll`
  messages being posted to the screen. If a future refactor removes these
  posts, scroll will silently break in real terminals; this test catches it.

## Debugging history (in case this recurs)

| Attempt | Symptom | Root cause |
|---|---|---|
| 1: `App.on_mouse_scroll_*` | Scroll never reached the App | App is not in the widget parent chain; mouse events never bubble to App's message pump from real driver input |
| 2: `Composer._on_mouse_scroll_*` forwarding directly | Worked in tests, worked partially in real terminal | Tests use `post_message` which bypasses the driver path; real driver path goes through `App.on_event` which doesn't dispatch to App's `_on_message` |
| 3: `App.on_event` override | Worked in real terminal, but visual was delayed | `scroll_to(animate=False)` only schedules one repaint via idle; idle was being deferred past the user's wheel release |
| **Final**: `App.on_event` + direct `Update`/`UpdateScroll` post to screen | Works in real terminal, instant repaint | The screen's `_dirty_widgets` is populated by `_on_update` messages; posting them directly wakes the compositor on the next pump cycle |

## Files

- `loop/tui/composer.py` — Composer wheel override
- `loop/tui/app.py` — App.on_event interception + scroll dispatch
- `tests/test_status_bar.py` — regression tests
- `tests/test_tui_manual_scroll.py` — original synthetic tests (kept for
  defense-in-depth; the new tests cover the real-driver path)
