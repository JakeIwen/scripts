import { spawn } from 'node:child_process';
import { config } from './config.mjs';

export function signalGroup(pid, signal) {
  if (!pid) return;
  try { process.kill(-pid, signal); } catch (error) {
    if (error.code !== 'ESRCH') throw error;
  }
}

export function runCommand(argv, { timeoutMs = 5000, signal, environment = {} } = {}) {
  return new Promise((resolve, reject) => {
    signal?.throwIfAborted();
    const child = spawn(argv[0], argv.slice(1), {
      detached: true, stdio: 'ignore',
      env: { ...process.env, ...config.sessionEnvironment, ...environment },
    });
    let timedOut = false;
    let killTimer;
    const terminate = () => {
      timedOut = true;
      signalGroup(child.pid, 'SIGTERM');
      killTimer = setTimeout(() => signalGroup(child.pid, 'SIGKILL'), 500);
    };
    const timer = setTimeout(terminate, timeoutMs);
    signal?.addEventListener('abort', terminate, { once: true });
    const cleanup = () => {
      clearTimeout(timer);
      clearTimeout(killTimer);
      signal?.removeEventListener('abort', terminate);
      // Kill surviving descendants even when the shell exited on TERM first.
      if (timedOut) signalGroup(child.pid, 'SIGKILL');
    };
    child.once('error', error => { cleanup(); reject(error); });
    child.once('exit', (code, exitSignal) => {
      cleanup();
      if (code === 0 && !timedOut) resolve();
      else reject(new Error(timedOut ? 'Command timeout/cancellation' : `Command exited ${code ?? exitSignal}`));
    });
  });
}
