'use strict'
// SPDX-License-Identifier: MIT
// One-line watchdog for a Mineflayer control program. It only reads Mineflayer
// state and events, and hands every finding to the caller's report callback.
const { Worker } = require('node:worker_threads')
const { pathToFileURL } = require('node:url')

const CONTROLS = ['forward', 'back', 'left', 'right', 'jump']
const round = v => Math.round(v * 10) / 10
const where = p => p && { x: round(p.x), y: round(p.y), z: round(p.z) }
const goalKey = goal => {
  if (!goal) return null
  const fields = ['x', 'y', 'z', 'rangeSq'].filter(k => typeof goal[k] === 'number')
  return `${goal.constructor?.name ?? 'goal'}(${fields.map(k => goal[k]).join(',')})`
}

// Runs in a worker: the main thread cannot report while a synchronous loop holds it.
const WORKER = `
const { workerData } = require('node:worker_threads')
const fs = require('node:fs')
const beat = new Int32Array(workerData.buffer)
let last = Atomics.load(beat, 0), since = Date.now(), reported = false
setInterval(async () => {
  const now = Atomics.load(beat, 0)
  if (now !== last || Atomics.load(beat, 1)) { last = now; since = Date.now(); reported = false; return }
  if (reported || Date.now() - since < workerData.blockedMs) return
  reported = true
  const event = { type: 'blocked', urgent: true, seconds: Math.round((Date.now() - since) / 1000),
    message: 'The program has not returned to its event loop for ' + Math.round((Date.now() - since) / 1000) + ' s: a synchronous loop? Mineflayer cannot act until it returns.' }
  try { fs.writeSync(2, 'watchdog: ' + event.message + '\\n') } catch {}
  if (workerData.onBlocked) {
    try { const m = await import(workerData.onBlocked); await (m.default ?? m.onBlocked)(event, workerData.context) }
    catch (error) { try { fs.writeSync(2, 'watchdog: onBlocked failed: ' + (error?.message ?? error) + '\\n') } catch {} }
  }
}, workerData.checkMs)
`

/**
 * Start watching `bot`. Returns { failure(error), pause(), resume(), close() }.
 *
 * Findings go to `report(event)`; each event has `type`, `urgent` and a
 * one-line `message`:
 * - `stuck`: movement keys held, or a pathfinder goal active, yet the bot
 *   moved less than `minMove` blocks and dug nothing and its inventory did not
 *   change for `stuckMs`. Reported once until the bot makes progress again.
 * - `repeat`: the same thing `repeatCount` times within `repeatWindowMs`: the
 *   pathfinder found no path, the same goal was set again, or the same error
 *   was passed to `failure()`.
 * - `blocked` (urgent): the main thread did not return to its event loop for
 *   `blockedMs` (a synchronous loop). The callback cannot run then, so a worker
 *   writes to stderr and, when `onBlocked` names a module, calls its default
 *   export with (event, blockedContext). 0 turns this check off.
 */
function watchdog (bot, {
  report,
  stuckMs = 15000,
  minMove = 1,
  checkMs = 1000,
  repeatCount = 3,
  repeatWindowMs = 120000,
  blockedMs = 5000,
  onBlocked,
  blockedContext,
  now = Date.now
} = {}) {
  if (typeof report !== 'function') throw new TypeError('watchdog needs a report(event) callback')
  let paused = false, closed = false
  let anchor = null, anchorAt = now(), stuckReported = false, goalReached = false
  const counts = new Map(), reported = new Set()
  const emit = event => { try { report(event) } catch {} }

  const progress = () => { anchor = bot.entity?.position?.clone?.() ?? where(bot.entity?.position); anchorAt = now(); stuckReported = false }
  const repeat = (key, message, detail) => {
    if (paused || closed) return
    const t = now(), list = (counts.get(key) ?? []).filter(at => t - at < repeatWindowMs)
    list.push(t); counts.set(key, list)
    if (list.length < repeatCount) { reported.delete(key); return }
    if (reported.has(key)) return
    reported.add(key)
    emit({ type: 'repeat', urgent: false, key, count: list.length, seconds: Math.round((t - list[0]) / 1000),
      position: where(bot.entity?.position), message, ...detail })
  }

  const trying = () => {
    const pressed = CONTROLS.filter(c => bot.controlState?.[c])
    const goal = !goalReached && bot.pathfinder?.goal
    return (pressed.length || goal) ? { pressed, goal: goalKey(goal) } : null
  }
  const tick = () => {
    if (paused || closed) return
    const p = bot.entity?.position
    if (!p) return
    const attempt = trying()
    if (!attempt || !anchor) return progress()
    const dx = p.x - anchor.x, dy = p.y - anchor.y, dz = p.z - anchor.z
    if (dx * dx + dy * dy + dz * dz >= minMove * minMove) return progress()
    if (stuckReported || now() - anchorAt < stuckMs) return
    stuckReported = true
    const seconds = Math.round((now() - anchorAt) / 1000)
    const wall = Boolean(bot.entity.isCollidedHorizontally)
    emit({ type: 'stuck', urgent: false, seconds, position: where(p), controls: attempt.pressed, goal: attempt.goal,
      againstWall: wall, inWater: Boolean(bot.entity.isInWater),
      message: `Trying to move (${[...attempt.pressed, ...(attempt.goal ? [attempt.goal] : [])].join(', ')}) but moved less than ${minMove} block in ${seconds} s${wall ? ', pressed against a wall' : ''}.` })
  }
  const timer = setInterval(tick, checkMs)
  timer.unref?.()

  const listeners = [
    [bot, 'diggingCompleted', progress],
    [bot, 'goal_reached', () => { goalReached = true; progress() }],
    [bot, 'goal_updated', (goal, dynamic) => {
      goalReached = false
      progress()
      if (goal && !dynamic) repeat(`goal:${goalKey(goal)}`, `The same pathfinder goal ${goalKey(goal)} was set again and again.`, { goal: goalKey(goal) })
    }],
    [bot, 'path_update', results => {
      if (results?.status === 'noPath') repeat('noPath', 'The pathfinder found no path again and again.', { goal: goalKey(bot.pathfinder?.goal) })
    }],
    [bot.inventory, 'updateSlot', progress]
  ]
  for (const [target, name, fn] of listeners) target?.on?.(name, fn)

  let worker, heartbeat, beat
  if (blockedMs > 0) {
    beat = new Int32Array(new SharedArrayBuffer(8))
    const module = onBlocked && (/^[a-z]+:/.test(String(onBlocked)) ? String(onBlocked) : pathToFileURL(String(onBlocked)).href)
    worker = new Worker(WORKER, { eval: true,
      workerData: { buffer: beat.buffer, blockedMs, checkMs: Math.max(50, Math.min(1000, blockedMs / 5)), onBlocked: module, context: blockedContext } })
    worker.unref()
    worker.on('error', () => {})
    heartbeat = setInterval(() => Atomics.add(beat, 0, 1), Math.max(25, Math.min(500, blockedMs / 10)))
    heartbeat.unref?.()
  }

  return {
    /** Count an error (or any string) the program caught; the same one `repeatCount` times is reported. */
    failure (error) {
      const text = String(error?.message ?? error).slice(0, 200)
      repeat(`error:${text}`, `The same error keeps happening: ${text}`, { error: text })
    },
    /** Stop reporting, for example while the bot is meant to stand still. */
    pause () { paused = true; if (beat) Atomics.store(beat, 1, 1) },
    resume () { paused = false; progress(); if (beat) Atomics.store(beat, 1, 0) },
    close () {
      if (closed) return
      closed = true
      clearInterval(timer); clearInterval(heartbeat)
      for (const [target, name, fn] of listeners) target?.off?.(name, fn) ?? target?.removeListener?.(name, fn)
      worker?.terminate()
    }
  }
}

module.exports = { watchdog }
