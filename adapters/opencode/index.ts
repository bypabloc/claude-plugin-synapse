// Synapse para OpenCode V2 (spec 001 · 02 §7.3): antes de cada tool vigilada llama a hooks/dispatch.py --host opencode
// y traduce su veredicto: block → throw, ask → ctx.permission.assert, allow/pass → sigue (permisos nativos).
// Ante cualquier error (spawn, timeout, salida inválida) pide confirmación: nunca deja pasar por un fallo propio.
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const DISPATCH = path.join(ROOT, 'hooks', 'dispatch.py')
const TOOLS = new Set(['shell', 'bash', 'read', 'write', 'edit', 'patch', 'apply_patch'])
const TIMEOUT_MS = 15_000

export type Verdict = { verdict: 'block' | 'ask' | 'allow' | 'pass'; reason: string; hook: string; rule: string; target: string }
type Outcome = Verdict | { error: string }

export function runDispatch(python: string, payload: unknown, timeoutMs = TIMEOUT_MS): Promise<Outcome> {
  return new Promise(resolve => {
    let out = ''
    let settled = false
    const done = (value: Outcome) => {
      if (!settled) {
        settled = true
        resolve(value)
      }
    }
    let child: ReturnType<typeof spawn>
    try {
      const [cmd, ...pre] = python.split(' ').filter(Boolean)
      child = spawn(cmd, [...pre, DISPATCH, '--host', 'opencode'], { stdio: ['pipe', 'pipe', 'ignore'], windowsHide: true })
    } catch (e) {
      return done({ error: `no se pudo lanzar ${python}: ${String(e)}` })
    }
    const timer = setTimeout(() => {
      child.kill()
      done({ error: `sin respuesta en ${timeoutMs / 1000} s` })
    }, timeoutMs)
    child.on('error', e => {
      clearTimeout(timer)
      done({ error: `no se pudo lanzar ${python}: ${e.message}` })
    })
    child.stdout?.on('data', chunk => (out += String(chunk)))
    child.on('close', code => {
      clearTimeout(timer)
      if (code !== 0) return done({ error: `dispatch salió con ${code}` })
      try {
        const parsed = JSON.parse(out) as Verdict
        if (!['block', 'ask', 'allow', 'pass'].includes(parsed.verdict)) throw new Error('veredicto inválido')
        done(parsed)
      } catch {
        done({ error: 'salida inválida de dispatch' })
      }
    })
    child.stdin?.end(JSON.stringify(payload))
  })
}

export default {
  id: 'bypabloc.synapse',
  async setup(ctx: any) {
    const python = String(ctx?.options?.python ?? (process.platform === 'win32' ? 'python' : 'python3'))
    ctx.tool.hook('execute.before', async (ev: any) => {
      if (!TOOLS.has(ev.tool)) return
      const payload = {
        tool: ev.tool, input: ev.input, sessionID: ev.sessionID, callID: ev.id,
        directory: ctx.location?.directory, project: ctx.location?.project?.directory,
      }
      const outcome = await runDispatch(python, payload)
      if ('verdict' in outcome && outcome.verdict === 'block') throw new Error(outcome.reason)
      if ('error' in outcome || outcome.verdict === 'ask') {
        const reason = 'error' in outcome
          ? `Synapse no pudo evaluar (${outcome.error}): confirma manualmente`
          : outcome.reason
        await ctx.permission.assert({
          sessionID: ev.sessionID, agent: ev.agent,
          source: { type: 'tool', messageID: ev.messageID, id: ev.id },
          action: 'synapse', resources: ['target' in outcome && outcome.target ? outcome.target : ev.tool],
          metadata: { reason },
        })
      }
    })
  },
}
