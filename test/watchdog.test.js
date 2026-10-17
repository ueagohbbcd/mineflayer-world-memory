'use strict'
const test = require('node:test'), assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const fs = require('node:fs'), os = require('node:os'), path = require('node:path')
const { watchdog } = require('../watchdog')

const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
function fakeBot () {
  const bot = new EventEmitter()
  bot.entity = { position: { x: 0, y: 64, z: 0 }, isCollidedHorizontally: false }
  bot.controlState = {}
  bot.inventory = new EventEmitter()
  return bot
}

test('reports a bot that holds a movement key but does not move, once per episode', async t => {
  const bot = fakeBot(), events = []
  const dog = watchdog(bot, { report: e => events.push(e), stuckMs: 120, checkMs: 20, blockedMs: 0 })
  t.after(() => dog.close())
  await delay(200)
  assert.equal(events.length, 0, 'standing still with no keys is not stuck')
  bot.controlState.forward = true
  bot.entity.isCollidedHorizontally = true
  await delay(300)
  assert.equal(events.length, 1)
  assert.equal(events[0].type, 'stuck')
  assert.equal(events[0].urgent, false)
  assert.equal(events[0].againstWall, true)
  assert.deepEqual(events[0].controls, ['forward'])
  bot.entity.position = { x: 3, y: 64, z: 0 }
  await delay(60)
  bot.entity.position = { x: 3, y: 64, z: 0 }
  await delay(250)
  assert.equal(events.length, 2, 'stuck again after moving')
  dog.pause()
  await delay(250)
  assert.equal(events.length, 2, 'paused')
})

test('digging or an inventory change counts as progress', async t => {
  const bot = fakeBot(), events = []
  const dog = watchdog(bot, { report: e => events.push(e), stuckMs: 150, checkMs: 20, blockedMs: 0 })
  t.after(() => dog.close())
  bot.controlState.forward = true
  for (let i = 0; i < 6; i++) {
    await delay(50)
    if (i % 2) bot.emit('diggingCompleted', {})
    else bot.inventory.emit('updateSlot', 0, null, null)
  }
  assert.equal(events.length, 0)
})

test('an active pathfinder goal counts as trying to move until it is reached', async t => {
  const bot = fakeBot(), events = []
  class GoalBlock { constructor (x, y, z) { Object.assign(this, { x, y, z }) } }
  bot.pathfinder = { goal: new GoalBlock(5, 64, 5) }
  const dog = watchdog(bot, { report: e => events.push(e), stuckMs: 100, checkMs: 20, blockedMs: 0 })
  t.after(() => dog.close())
  bot.emit('goal_reached', bot.pathfinder.goal)
  await delay(200)
  assert.equal(events.length, 0)
  bot.emit('goal_updated', bot.pathfinder.goal, false)
  await delay(200)
  assert.equal(events[0]?.goal, 'GoalBlock(5,64,5)')
})

test('repeated no-path results, goals and errors are reported once each', async t => {
  const bot = fakeBot(), events = []
  bot.pathfinder = { goal: null }
  const dog = watchdog(bot, { report: e => events.push(e), blockedMs: 0, repeatCount: 3 })
  t.after(() => dog.close())
  for (let i = 0; i < 5; i++) bot.emit('path_update', { status: 'noPath' })
  for (let i = 0; i < 3; i++) bot.emit('goal_updated', { x: 1, y: 2, z: 3 }, false)
  for (let i = 0; i < 3; i++) bot.emit('goal_updated', { x: 9, y: 9, z: 9 }, true)
  dog.failure(new Error('Took to long to decide path to goal!'))
  dog.failure('Took to long to decide path to goal!')
  dog.failure(new Error('Took to long to decide path to goal!'))
  assert.deepEqual(events.map(e => e.key), ['noPath', 'goal:Object(1,2,3)', 'error:Took to long to decide path to goal!'])
  assert.ok(events.every(e => e.type === 'repeat' && !e.urgent && e.message))
})

test('a synchronous loop is reported from the worker', async t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'watchdog-'))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  const out = path.join(dir, 'blocked.json'), handler = path.join(dir, 'on-blocked.mjs')
  fs.writeFileSync(handler, "import fs from 'node:fs'\nexport default (event, context) => fs.writeFileSync(context.out, JSON.stringify(event))\n")
  const dog = watchdog(fakeBot(), { report () {}, blockedMs: 300, onBlocked: handler, blockedContext: { out } })
  t.after(() => dog.close())
  await delay(200) // Let the worker start.
  const until = Date.now() + 1500
  while (Date.now() < until && !fs.existsSync(out)) {} // Hold the main thread.
  const event = JSON.parse(fs.readFileSync(out, 'utf8'))
  assert.equal(event.type, 'blocked')
  assert.equal(event.urgent, true)
})

test('watchdog has CJS and ESM entry points and needs a report callback', async () => {
  assert.equal((await import('../watchdog.mjs')).watchdog, watchdog)
  assert.throws(() => watchdog(fakeBot(), {}), /report/)
})
