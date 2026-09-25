import { resolveAttachRunningConnection } from './node_modules/@steipete/oracle/dist/src/browser/attachRunning.js';

export const CONNECTION_GUIDANCE = '请在已登录的 Google Chrome 打开 chrome://inspect/#remote-debugging，开启原生远程调试，然后点击软件的“连接已登录的 Chrome”，并在 Chrome 弹窗中允许连接。软件不会启动其他浏览器或复制登录信息。';

export function validateNativeEndpoint(value, port) {
  try {
    const socket = new URL(value.browserWSEndpoint);
    if (value.host !== '127.0.0.1' || value.port !== port || socket.protocol !== 'ws:' ||
        !['127.0.0.1', 'localhost', '[::1]'].includes(socket.hostname) || Number(socket.port) !== port ||
        !/^\/devtools\/browser\/[^/?#]+$/.test(socket.pathname) || socket.username || socket.password || socket.search || socket.hash) return null;
    return { host: '127.0.0.1', port, browserWSEndpoint: socket.href };
  } catch { return null; }
}

// Discovery never opens a WebSocket or requests browser authorization. Only an
// explicit login calls this; status uses the broker's existing connection.
export async function browserEndpoint(_stateDir, { resolve = resolveAttachRunningConnection, port = Number(process.env.PDF2ZH_CHATGPT_BROWSER_PORT || 9222) } = {}) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw Object.assign(new Error('PDF2ZH_CHATGPT_BROWSER_PORT 必须是有效的本机端口。'), { code: 'INVALID_ENDPOINT' });
  try {
    const resolved = await resolve({ attachRunning: true, remoteChrome: { host: '127.0.0.1', port } }, () => {});
    const endpoint = validateNativeEndpoint(resolved, port);
    if (!endpoint) throw new Error('Invalid local native Chrome endpoint');
    return endpoint;
  } catch {
    throw Object.assign(new Error(CONNECTION_GUIDANCE), { code: 'NATIVE_DEBUGGING_REQUIRED', status: 'needs_connection' });
  }
}
