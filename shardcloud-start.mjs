#!/usr/bin/env node
import { mkdir, rm, rename, stat } from 'node:fs/promises'
import { createWriteStream } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { pipeline } from 'node:stream/promises'
import { Readable } from 'node:stream'
import { join } from 'node:path'
import process from 'node:process'

const ROOT = process.cwd()
const ARCHIVE = join(ROOT, '.dsh-source.tar.gz')
const SRC = join(ROOT, '.dsh-source')
const REPO = 'https://github.com/0299jolgue/deepseek-harness-gpt/archive/refs/heads/master.tar.gz'

function run(command, args, env = process.env) {
  const r = spawnSync(command, args, { stdio: 'inherit', env })
  if (r.error) throw r.error
  if (r.status !== 0) process.exit(r.status ?? 1)
}

async function download(url, file) {
  const response = await fetch(url, { redirect: 'follow' })
  if (!response.ok || response.body === null) throw new Error(`download failed: HTTP ${response.status}`)
  await pipeline(Readable.fromWeb(response.body), createWriteStream(file))
}

await rm(SRC, { recursive: true, force: true })
await rm(ARCHIVE, { force: true })
await mkdir(SRC, { recursive: true })

console.log('[shardcloud] downloading reduced runtime source...')
await download(REPO, ARCHIVE)

// Keep runtime/build sources while leaving docs, CI, tests and translation copies out.
run('tar', [
  '-xzf', ARCHIVE,
  '-C', SRC,
  '--strip-components=1',
  '--exclude=.agents',
  '--exclude=.claude',
  '--exclude=.github',
  '--exclude=.gitlab-ci.yml',
  '--exclude=website',
  '--exclude=BENCHMARK.md',
  '--exclude=AGENTS.md',
  '--exclude=*.i18n.yaml',
  '--exclude=*.zh.md',
  '--exclude=**/tests',
  '--exclude=**/test',
  '--exclude=**/__tests__',
  '--exclude=**/__snapshots__',
  '--exclude=**/fixtures',
  '--exclude=**/*.test.ts',
  '--exclude=**/*.test.tsx',
  '--exclude=**/*.test.js',
  '--exclude=**/*.test.mjs',
  '--exclude=**/*.spec.ts',
  '--exclude=**/*.spec.tsx',
  '--exclude=**/*.spec.js',
  '--exclude=**/*.spec.mjs',
  '--exclude=**/*.map',
])

await rm(ARCHIVE, { force: true })

// Replace the deployment directory with the pruned checkout, but keep this launcher.
const entries = (await import('node:fs/promises')).readdir(SRC)
for (const name of await entries) {
  if (name === 'shardcloud-start.mjs') continue
  await rm(join(ROOT, name), { recursive: true, force: true })
}
for (const name of await entries) {
  if (name === 'shardcloud-start.mjs') continue
  await rename(join(SRC, name), join(ROOT, name))
}
await rm(SRC, { recursive: true, force: true })

const env = {
  ...process.env,
  DSH_ALLOW_PUBLIC_BIND: '1',
  DSH_TELEMETRY_DISABLED: '1',
  DSH_WEB_MODE: 'shardcloud',
}

console.log('[shardcloud] installing dependencies...')
run('corepack', ['enable'], env)
run('corepack', ['pnpm', 'install', '--frozen-lockfile'], env)

console.log('[shardcloud] starting DeepSeek Harness on public port 80...')
run('corepack', ['pnpm', 'run', 'dsh', '--', 'web', '--host', '0.0.0.0', '--port', '80', '--no-open'], env)
