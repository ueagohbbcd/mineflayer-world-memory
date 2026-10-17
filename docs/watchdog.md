# Watchdog

A one-line safety net for a Mineflayer control program. It notices when the
program's bot is stuck, when the program keeps retrying the same thing, and
when the program's own thread is blocked by a synchronous loop. It only reads
Mineflayer state and events, never presses keys or changes goals, and hands
each finding to your callback.

```js
import { watchdog } from 'mineflayer-world-memory/watchdog.mjs'

const dog = watchdog(bot, { report: event => console.log(event.message) })
// ... the program ...
dog.close()
```

## Findings

Every event has `type`, `urgent` (boolean) and a one-line `message`, plus the
bot's `position` where it applies.

| `type`    | When                                                                                                                                                                                                                                  | Extra fields                                          |
| --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| `stuck`   | A movement key (`forward`, `back`, `left`, `right`, `jump`) is held, or a pathfinder goal is active and not yet reached, yet the bot moved less than `minMove` blocks, dug nothing and its inventory did not change for `stuckMs`. Reported once until the bot makes progress again. | `seconds`, `controls`, `goal`, `againstWall`, `inWater` |
| `repeat`  | The same thing `repeatCount` times within `repeatWindowMs`: the pathfinder reported `noPath`, the same non-dynamic goal was set again, or the same error was passed to `dog.failure()`. Reported once until the count drops below the limit. | `key`, `count`, `seconds`, `goal` or `error`           |
| `blocked` | Urgent. The program did not return to its event loop for `blockedMs`, so Mineflayer could not act. See below.                                                                                                                         | `seconds`                                             |

## Options

| Option           | Default  | Meaning                                                                                      |
| ---------------- | -------- | -------------------------------------------------------------------------------------------- |
| `report`         | required | Called with each finding. Errors it throws are ignored.                                      |
| `stuckMs`        | `15000`  | How long trying to move without progress counts as stuck.                                    |
| `minMove`        | `1`      | Distance in blocks that counts as moving.                                                    |
| `checkMs`        | `1000`   | How often the bot's state is checked.                                                        |
| `repeatCount`    | `3`      | How many repeats are reported.                                                               |
| `repeatWindowMs` | `120000` | Window for counting repeats.                                                                 |
| `blockedMs`      | `5000`   | How long a blocked event loop is reported after; `0` turns the check off (and the worker).   |
| `onBlocked`      | unset    | Path or URL of a module whose default export is called as `(event, blockedContext)`.         |
| `blockedContext` | unset    | Structured-clonable data passed to `onBlocked`.                                              |

## Methods

- `dog.failure(error)`: count an error your program caught (or any string).
- `dog.pause()` / `dog.resume()`: stop reporting while the bot is meant to stand still with a key held, for example while waiting at a gate.
- `dog.close()`: remove listeners and stop the worker. The watchdog never keeps the process alive.

## Blocked event loop

While a synchronous loop runs, no callback in the program can run, including
`report`. A small worker thread watches a heartbeat that the main thread
updates. When the heartbeat stops for `blockedMs`, the worker writes the
message to stderr and, when `onBlocked` is given, imports that module and
calls its default export from the worker. Use it to deliver the urgent report
through a channel that does not need the main thread.
