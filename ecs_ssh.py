# -*- coding: utf-8 -*-
# ECS SSH 管理（页面在 /ecs-ssh）
# ------------------------------------------------------------
# 在网页上连上 ECS，用自然语言让大模型给排查/操作方案，你自己点确认才执行。
#
# 安全设计（这是这个工具的重点，改动前先读完）：
#   1. 凭据不落盘：主机密码 / 私钥只放在服务端内存里的会话对象中，断开或超时即销毁，
#      不写数据库、不写日志、不回传给前端，前端也不存 localStorage。
#      会话令牌是随机串，只活在当前页面的 JS 变量里，刷新页面即失效。
#   2. 主机指纹：第一次连直接记下（accept-new，不再人工核对），之后每次连接都比对；
#      指纹变了会告警，要你确认才继续，防中间人。记录在 data/ssh_known_hosts
#      （0600，标准 known_hosts 格式）。
#   3. AI 不自动执行：模型只输出命令清单，每条都由人在页面上点「执行」。
#      模型给的 risk 标签一律不信，服务端用正则自己重新判定。
#   4. 只读模式默认开：白名单校验每个管道/分号段的第一个命令，含重定向、$()、反引号
#      一律拒绝；关掉只读后，破坏性命令要前端二次确认，几条灾难级命令永久禁止。
#   5. 超时 + 输出截断 + 速率限制，避免一条命令把会话拖死。
#
# 本地起服务：python ecs_ssh.py（只监听 127.0.0.1:8007）
import base64
import hashlib
import io
import json
import os
import posixpath
import re
import select
import shlex
import socket
import stat
import threading
import time
from pathlib import Path

import requests
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

try:
    import paramiko
except Exception:  # 没装 paramiko 时页面还能打开，只是连不上，给出明确提示
    paramiko = None

try:
    from model_config import llm_endpoint, llm_key, llm_model
except Exception:  # 单独拷贝本文件运行时的兜底
    def llm_key():
        return ''

    def llm_model():
        return 'deepseek-chat'

    def llm_endpoint(base=''):
        return (base or 'https://api.deepseek.com').rstrip('/') + '/chat/completions'

router = APIRouter()
_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATE_FILE = _BASE_DIR / 'templates' / 'ecs-ssh.html'
_DATA_DIR = _BASE_DIR / 'data'
KNOWN_HOSTS_FILE = _DATA_DIR / 'ssh_known_hosts'
AUDIT_FILE = _DATA_DIR / 'ssh_audit.jsonl'

IDLE_TIMEOUT = 30 * 60          # 会话闲置多久自动断开（秒）
MAX_SESSIONS = 3
CONNECT_TIMEOUT = 15
CMD_TIMEOUT_DEFAULT = 30
CMD_TIMEOUT_MAX = 120
MAX_CMD_LEN = 2000
MAX_OUT_BYTES = 200_000
MAX_OUT_LINES = 2000
HEAD_LINES = 1000               # 截断时保留开头多少行
TAIL_LINES = 1000               # 截断时保留结尾多少行
RATE_PER_MIN = 120              # 单个会话每分钟最多执行多少条
HISTORY_KEEP = 6                # 给大模型回看的最近命令条数
AI_STEP_MAX = 6
_ID_RE = re.compile(r'^[A-Za-z0-9._\-]{1,253}$')
# 行首或分隔符之后的 sudo（管道、分号、&& 后面也算）
SUDO_RE = re.compile(r'(^|[;&|]\s*)sudo(?!\s+-n)\b\s*')
# cd 的识别：命令名 + 可选参数 + 可选的分隔符（&& / ;）+ 后面的命令
CD_RE = re.compile(r'''^\s*cd(?:\s+(?P<t>"[^"]*"|'[^']*'|(?:[^\s;&|<>\\]|\\.)+))?\s*(?P<sep>&&|;)?\s*(?P<rest>.*)$''')
# 连 shell 都算不上、但补全时要能补出来的东西
SHELL_BUILTINS = {
    'cd', 'pwd', 'echo', 'printf', 'export', 'unset', 'alias', 'unalias', 'history',
    'jobs', 'bg', 'fg', 'exit', 'logout', 'clear', 'reset', 'ulimit', 'umask', 'set',
    'shift', 'source', 'true', 'false', 'test', 'time', 'help', 'sudo', 'su',
}
MAX_COMPLETE_ITEMS = 200       # 补全一次最多回多少个候选，防止刷屏


class Session:
    """一条 SSH 连接（凭据只在这里，随对象一起被回收）。"""

    __slots__ = ('token', 'client', 'host', 'port', 'user', 'auth', 'os_info',
                 'created', 'last', 'lock', 'cmd_times', 'history',
                 'readonly', 'allow_sudo', 'timeout', 'count',
                 'cwd', 'prev_cwd', 'home', 'sftp', 'sftp_lock', 'cmd_cache')

    def __init__(self, client, host, port, user, auth, os_info, readonly, allow_sudo, timeout):
        self.token = base64.urlsafe_b64encode(os.urandom(32)).decode('ascii').rstrip('=')
        self.client = client
        self.host = host
        self.port = port
        self.user = user
        self.auth = auth
        self.os_info = os_info
        self.created = time.time()
        self.last = time.time()
        self.lock = threading.Lock()
        self.cmd_times = []
        self.history = []
        self.readonly = readonly
        self.allow_sudo = allow_sudo
        self.timeout = timeout
        self.count = 0
        self.cwd = ''            # 会话当前目录（每条命令前自动 cd 回去）
        self.prev_cwd = ''       # 给 cd - 用
        self.home = ''           # 登录目录，ps1 里显示成 ~
        self.sftp = None         # 复用的 SFTP 通道，只用来查路径 / 列目录
        self.sftp_lock = threading.RLock()
        self.cmd_cache = None    # PATH 里的命令名，第一次补全时抓一次


_SESSIONS = {}
_SESS_LOCK = threading.Lock()


def _drop(token):
    with _SESS_LOCK:
        sess = _SESSIONS.pop(token, None)
    if sess is not None:
        try:
            if sess.sftp is not None:
                sess.sftp.close()
        except Exception:
            pass
        try:
            sess.client.close()
        except Exception:
            pass
    return sess


def _sweep():
    now = time.time()
    with _SESS_LOCK:
        stale = [t for t, s in _SESSIONS.items() if now - s.last > IDLE_TIMEOUT]
    for t in stale:
        _drop(t)


def _get(token):
    _sweep()
    with _SESS_LOCK:
        sess = _SESSIONS.get(str(token or ''))
    if sess is not None:
        sess.last = time.time()
    return sess


def _fail(message, **extra):
    payload = {'ok': False, 'message': message}
    payload.update(extra)
    return JSONResponse(payload)


def _fingerprint(key):
    """SHA256 指纹，格式和 ssh-keygen -lf 一致。"""
    try:
        raw = key.asbytes()
        return 'SHA256:' + base64.b64encode(hashlib.sha256(raw).digest()).decode('ascii').rstrip('=')
    except Exception:
        try:
            return 'MD5:' + ':'.join('%02x' % b for b in key.get_fingerprint())
        except Exception:
            return '未知'


def _kh_name(host, port):
    return host if int(port) == 22 else '[%s]:%d' % (host, int(port))

# =========================================================
# 命令安全闸门
# =========================================================
# 只读白名单：只读模式下，每个管道 / 分号段的第一条命令都必须在表里。
READONLY_CMDS = {
    # 只读模式下允许出现的命令：全部是「看一眼」的，不写盘、不改状态、不外传
    'ls', 'll', 'cat', 'head', 'tail', 'wc', 'grep', 'egrep', 'zgrep', 'zgrep',
    'awk', 'sed', 'cut', 'sort', 'uniq', 'tr', 'find', 'file', 'stat', 'du', 'df',
    'tree', 'md5sum', 'sha256sum', 'basename', 'dirname', 'realpath', 'readlink',
    'lsblk', 'blkid', 'lscpu', 'lsmem', 'lspci', 'lsusb', 'modinfo', 'mount', 'findmnt',
    'uname', 'hostname', 'hostnamectl', 'whoami', 'id', 'groups', 'logname', 'uptime',
    'date', 'cal', 'timedatectl', 'free', 'vmstat', 'iostat', 'top', 'ps', 'pgrep',
    'pstree', 'lsof', 'ss', 'netstat', 'ip', 'ifconfig', 'route', 'arp', 'ping',
    'getent', 'dig', 'nslookup', 'host', 'systemctl', 'journalctl', 'service', 'dmesg',
    'last', 'lastlog', 'w', 'who', 'env', 'printenv', 'echo', 'which', 'whereis',
    'type', 'command', 'help', 'sysctl', 'docker', 'podman', 'kubectl', 'crictl',
    'cd', 'pwd', 'pushd', 'popd',          # 只改会话自己的当前目录，不碰服务器
}
# 只读模式下也必须拒绝的参数（会真的动数据）
READONLY_DENY_ARGS = ('-delete', '-exec', '-execdir', '-ok', '-fprint', '--delete')
# 灾难级命令：任何模式下都不执行
BLOCKED_PATTERNS = (
    r'rm\s+(-[a-zA-Z]+\s+)*-[a-zA-Z]*[rR][a-zA-Z]*f[a-zA-Z]*\s+/(\s|$)',
    r'rm\s+(-[a-zA-Z]+\s+)*-[a-zA-Z]*f[a-zA-Z]*[rR][a-zA-Z]*\s+/(\s|$)',
    r':\s*\(\s*\)\s*\{.*\}',                      # fork 炸弹
    r'\bmkfs(\.\w+)?\b',
    r'\bdd\b[^\n]*of=/dev/(sd|nvme|vd|hd)',
    r'>\s*/dev/(sd|nvme|vd|hd)',
    r'\b(shutdown|reboot|halt|poweroff)\b',
    r'\binit\s+0\b',
    r'\bmv\s+/\s+\*',
)
# 破坏性命令：允许，但必须前端二次确认
DESTRUCTIVE_PATTERNS = (
    r'\brm\b', r'\brmdir\b', r'\bunlink\b', r'\bshred\b', r'\btruncate\b',
    r'\bmkfs', r'\bdd\b', r'\bfdisk\b', r'\bparted\b', r'\bwipefs\b',
    r'\bkill\b', r'\bkillall\b', r'\bpkill\b', r'\bkill\s+-9\b',
    r'\bsystemctl\s+(stop|restart|disable|mask|kill)\b',
    r'\bservice\s+\S+\s+(stop|restart)\b',
    r'\buserdel\b', r'\bgroupdel\b', r'\bpasswd\b', r'\bchpasswd\b',
    r'\bcrontab\s+-r\b', r'\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f)\b',
    r'\biptables\b', r'\bnft\b', r'\bufw\b', r'\bfirewall-cmd\b',
    r'\bdocker\s+(rm|rmi|stop|kill|system\s+prune|volume\s+rm|network\s+rm)\b',
    r'\bapt(-get)?\s+(remove|purge|autoremove)\b', r'\byum\s+remove\b',
    r'\bdnf\s+remove\b', r'\bswapoff\b', r'\bumount\b',
    r'>(?!\s*/dev/null)\s*\S+', r'\bsed\s+-i\b', r'\bchmod\b', r'\bchown\b', r'\bchattr\b',
    r'\btar\b[^\n]*\s--delete\b', r'\brsync\b[^\n]*--delete\b',
    r'\bmysql\b[^\n]*\b(drop|delete|truncate)\b',
    r'\bpip3?\s+uninstall\b', r'\bnpm\s+uninstall\b',
    r'\bsed\s+(-\w+\s+)*-i',
    r'\bawk\b[^\n]*\bi\s+inplace\b',
    r'\b(mysql|psql|redis-cli|mongo|mongosh)\b[^\n]*\b(drop|delete|truncate|update|insert|alter|grant|revoke|flushall|flushdb|set|del)\b',
    r'\b(curl|wget)\b[^\n]*\s(-[a-zA-Z]*[oO]|--output)\b',
    r'\b(shutdown|reboot|halt|poweroff)\b',
)


def _segments(command):
    """把命令按管道 / 分号 / && / || 拆开，逐段校验。"""
    parts = re.split(r'(\|\||&&|[|;\n])', command)
    segs = []
    for part in parts:
        part = part.strip()
        if not part or part in ('|', ';', '&&', '||', '\n'):
            continue
        segs.append(part)
    return segs


def _is_readonly(command):
    """只读判定：不执行任何东西，纯看字符串。"""
    cmd = command.strip()
    if not cmd or '\n' in cmd:
        return False
    # 把输出丢进 /dev/null、以及 2>&1 这类写法先摘掉：它们不写任何文件，
    # 是只读排查里最常见的写法（否则 du ... 2>/dev/null 会被误判成写操作）
    probe = re.sub(r'\d?>{1,2}\s*/dev/null', ' ', cmd)
    probe = re.sub(r'\d?>\s*&\s*\d', ' ', probe)
    if re.search(r'[<>`]|\$\(', probe):      # 重定向 / 命令替换 / 反引号
        return False
    for bad in READONLY_DENY_ARGS:
        if bad in cmd:
            return False
    segs = _segments(probe)
    if not segs:
        return False
    for seg in segs:
        for word in seg.split():
            if '=' in word and not word.startswith('-'):
                continue                      # FOO=bar 这类前置变量
            head = os.path.basename(word)
            if head == 'sudo':
                continue
            if head not in READONLY_CMDS:
                return False
            break
    return True


def _classify(command):
    """返回 (level, reason)：blocked / destructive / high / low。

    注意：模型给的 risk 一律不信，以这里为准。
    """
    low = command.strip().lower()
    for pat in BLOCKED_PATTERNS:
        if re.search(pat, low):
            return 'blocked', '这条命令属于不可逆的灾难级操作，工具里永久禁止执行'
    for pat in DESTRUCTIVE_PATTERNS:
        if re.search(pat, low):
            return 'destructive', '会改动甚至删掉服务器上的东西'
    for pat in (r'^\s*sudo\b', r'\bsystemctl\s+(start|enable|reload|daemon-reload)\b',
                r'\bapt(-get)?\s+(install|upgrade|update)\b', r'\byum\s+(install|update)\b',
                r'\bdnf\s+(install|update)\b', r'\bdocker\s+(run|start|restart|pull|exec)\b',
                r'\bmkdir\b', r'\btouch\b', r'\bcp\b', r'\bmv\b', r'\bln\b',
                r'\bgit\s+(pull|push|fetch|checkout|merge)\b', r'\bnpm\s+(install|ci)\b',
                r'\bpip3?\s+install\b', r'\btee\b', r'\bexport\b'):
        if re.search(pat, low):
            return 'high', '会改变服务器状态'
    return 'low', '看起来只是查看'


# 需要交互 / 会一直刷屏的命令，页面里跑不了（没有 PTY，只会挂到超时）
INTERACTIVE_RE = re.compile(
    r'^\s*(vim?|nano|emacs|pico|less|more|man|info|htop|watch|screen|tmux|'
    r'tail\s+-f|journalctl\s+-f|top\s*$|bash\s*$|sh\s*$|zsh\s*$|fish\s*$|'
    r'su\s|sudo\s+-i|sudo\s+su|ssh\s|scp\s|sftp\s|mysql\s*$|psql\s*$|'
    r'redis-cli\s*$|mongo\s*$|python3?\s*$|node\s*$|sqlite3\s*$|ftp\s)')


def _interactive_reason(command):
    hit = INTERACTIVE_RE.search(command.strip())
    if hit:
        return ('「%s」这类命令需要交互界面，在网页里跑不了（会话没有终端）。'
                '想看得用一次性输出的写法，比如 top -b -n1、tail -n 200、journalctl -n 100。'
                % hit.group(1).strip())
    return ''

def _trim_output(text, truncated):
    """太长时保留开头 + 结尾，中间标注省略了多少行。"""
    lines = text.split('\n')
    if len(lines) <= MAX_OUT_LINES:
        return text, truncated
    head = lines[:HEAD_LINES]
    tail = lines[-TAIL_LINES:]
    skipped = len(lines) - HEAD_LINES - TAIL_LINES
    return '\n'.join(head + ['', '…… 中间省略 %d 行 ……' % skipped, ''] + tail), True


def _audit(entry):
    """审计日志：只记命令和结果，绝不记密码 / 私钥。"""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with io.open(AUDIT_FILE, 'a', encoding='utf-8', newline='') as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + '\n')
        try:
            os.chmod(AUDIT_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        pass          # 只读文件系统（比如 serverless）就只留内存记录

# =========================================================
# SSH 连接（含主机指纹记录与变化告警）
# =========================================================
if paramiko is not None:
    class _HostKeyUnknown(Exception):
        """服务器出示了一个我们没记录过的主机密钥。"""

    class _ProbePolicy(paramiko.MissingHostKeyPolicy):
        def __init__(self):
            self.key = None

        def missing_host_key(self, client, hostname, key):
            self.key = key
            raise _HostKeyUnknown()
else:
    class _HostKeyUnknown(Exception):
        pass

    _ProbePolicy = None

PROBE_CMD = (
    'sh -c \'echo "##uname"; uname -srm 2>/dev/null; echo "##id"; id 2>/dev/null; '
    'echo "##host"; hostname 2>/dev/null; echo "##cpu"; nproc 2>/dev/null; '
    'echo "##os"; (cat /etc/os-release 2>/dev/null || cat /etc/system-release 2>/dev/null) | head -4; '
    'echo "##path"; echo "$PATH"\''
)


def _new_client():
    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()          # 机器上 ~/.ssh/known_hosts 已信任的直接生效
    except Exception:
        pass
    if KNOWN_HOSTS_FILE.exists():
        try:
            client.load_host_keys(str(KNOWN_HOSTS_FILE))
        except Exception:
            pass
    return client


def _close(client):
    try:
        client.close()
    except Exception:
        pass


def _attempt(kwargs):
    """连一次。返回 (client, err)：
       err 为 None 表示成功；err['fatal'] 是给用户看的失败原因；
       err['key'] 表示服务器出示了一个我们没记录（或对不上）的主机密钥。
    """
    client = _new_client()
    probe = _ProbePolicy()
    client.set_missing_host_key_policy(probe)
    try:
        client.connect(**kwargs)
        return client, None
    except _HostKeyUnknown:
        key = probe.key
        _close(client)
        return None, {'key': key, 'changed': False}
    except paramiko.BadHostKeyException as exc:
        # paramiko 发现「记过的钥匙和对方出示的不一样」时先于上面的策略抛这个
        _close(client)
        return None, {'key': exc.key, 'saved': exc.expected_key, 'changed': True}
    except Exception as exc:
        _close(client)
        return None, {'fatal': _explain(exc)}


def _remember_host(kh_name, key):
    """把主机指纹写进 data/ssh_known_hosts（0600，标准 known_hosts 格式）。

    先删掉这台机器的旧记录：对方换了密钥类型（比如 RSA 换 ed25519）时，
    留着旧条目会让下一次连接又被判成「密钥不一致」。
    """
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        hosts = paramiko.HostKeys()
        if KNOWN_HOSTS_FILE.exists():
            try:
                hosts.load(str(KNOWN_HOSTS_FILE))
            except Exception:
                pass
        try:
            del hosts[kh_name]
        except Exception:
            pass          # 没这条记录就直接写新的
        hosts.add(kh_name, key.get_name(), key)
        hosts.save(str(KNOWN_HOSTS_FILE))
        try:
            # Linux 下就是 0600；Windows 的 os.chmod 只影响只读位，改不了 ACL
            os.chmod(KNOWN_HOSTS_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def _load_pkey(text, passphrase):
    for cls in ('Ed25519Key', 'ECDSAKey', 'RSAKey', 'DSSKey'):
        klass = getattr(paramiko, cls, None)
        if klass is None:
            continue
        try:
            return klass.from_private_key(io.StringIO(text), password=passphrase or None)
        except Exception:
            continue
    return None


def _explain(exc):
    text = str(exc)
    if paramiko is not None and isinstance(exc, paramiko.AuthenticationException):
        return ('登录被拒绝：用户名 / 密码 / 密钥不对。很多云主机默认禁止密码登录，'
                '如果确认密码没错，改用密钥试试。')
    if paramiko is not None and isinstance(exc, paramiko.BadHostKeyException):
        return '主机密钥和已知记录不一致，已经中止连接。'
    if isinstance(exc, socket.timeout) or 'timed out' in text.lower():
        return '连接超时：检查 IP、端口，以及云主机安全组有没有放行这个端口。'
    if isinstance(exc, socket.gaierror) or 'Name or service not known' in text:
        return '域名解析不了，检查主机地址填对没有。'
    if isinstance(exc, ConnectionRefusedError) or 'refused' in text.lower():
        return '对方拒绝了连接：端口不对，或者 sshd 没在跑。'
    if isinstance(exc, PermissionError):
        return '读不了这个密钥文件（权限或路径不对）。'
    return '连接失败：' + text[:200]


def _run(client, command, timeout):
    """执行一条命令，返回 (stdout, stderr, 退出码, 是否超时, 是否截断)。"""
    chan = client.get_transport().open_session(timeout=10)
    chan.settimeout(timeout)
    chan.exec_command(command)
    out = bytearray()
    err = bytearray()
    truncated = False
    timed_out = False
    deadline = time.time() + timeout
    while True:
        if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
            break
        left = deadline - time.time()
        if left <= 0:
            timed_out = True
            break
        try:
            ready = select.select([chan], [], [], min(0.2, left))[0]
        except Exception:
            break
        if chan in ready:
            try:
                if chan.recv_ready():
                    chunk = chan.recv(65536)
                    if chunk:
                        out += chunk
                if chan.recv_stderr_ready():
                    chunk = chan.recv_stderr(65536)
                    if chunk:
                        err += chunk
            except Exception:
                break
        if len(out) + len(err) > MAX_OUT_BYTES:
            truncated = True
            break
    code = None
    if timed_out:
        try:
            chan.close()
        except Exception:
            pass
    else:
        try:
            if chan.status_event.wait(3):
                code = chan.recv_exit_status()
        except Exception:
            code = None
    try:
        chan.close()
    except Exception:
        pass
    so = out.decode('utf-8', 'replace')
    se = err.decode('utf-8', 'replace')
    so, t1 = _trim_output(so, truncated)
    se, t2 = _trim_output(se, False)
    return so, se, code, timed_out, (t1 or t2)


def _capture_env(client):
    """连上之后抓一次环境信息，给 AI 当上下文（只读命令）。"""
    try:
        out = _run(client, PROBE_CMD, 10)[0]
    except Exception:
        return {}
    info = {}
    key = None
    for line in out.split('\n'):
        line = line.rstrip()
        if line.startswith('##'):
            key = line[2:].strip()
            info[key] = ''
        elif key:
            info[key] = (info[key] + ' ' + line.strip()).strip()
    info = {k: v for k, v in info.items() if v}
    # os-release 一整段太长，状态栏只放 PRETTY_NAME
    m = re.search(r'PRETTY_NAME="([^"]+)"', info.get('os', ''))
    if m:
        info['os'] = m.group(1)
    elif info.get('os'):
        info['os'] = info['os'].split('\n')[0][:60]
    return info

# =========================================================
# 动作：连接 / 执行 / AI 方案 / 状态
# =========================================================
def _do_connect(p):
    if paramiko is None:
        return _fail('服务端没装 paramiko，先在终端执行：pip install paramiko')
    _sweep()
    host = str(p.get('host') or '').strip()
    if not _ID_RE.match(host):
        return _fail('主机地址只支持 IP 或域名，别带 http:// 和空格')
    try:
        port = int(p.get('port') or 22)
    except (TypeError, ValueError):
        port = 22
    if not 0 < port < 65536:
        return _fail('端口要在 1 - 65535 之间')
    user = re.sub(r'[^A-Za-z0-9._@\-]', '', str(p.get('user') or 'root'))[:32]
    if not user:
        return _fail('用户名不能为空')
    auth = str(p.get('auth') or 'password')
    if auth not in ('password', 'key', 'keyfile'):
        auth = 'password'
    password = str(p.get('password') or '')
    passphrase = str(p.get('passphrase') or '')
    key_text = str(p.get('key') or '')
    key_path = str(p.get('keyfile') or '').strip()
    try:
        timeout = int(p.get('timeout') or CMD_TIMEOUT_DEFAULT)
    except (TypeError, ValueError):
        timeout = CMD_TIMEOUT_DEFAULT
    timeout = max(5, min(CMD_TIMEOUT_MAX, timeout))
    readonly = p.get('readonly')
    readonly = True if readonly is None else bool(readonly)
    allow_sudo = bool(p.get('allow_sudo'))
    trust_fp = str(p.get('trust_fingerprint') or '')

    kwargs = dict(hostname=host, port=port, username=user, timeout=CONNECT_TIMEOUT,
                  banner_timeout=25, auth_timeout=25, allow_agent=False, look_for_keys=False)
    if auth == 'password':
        if not password:
            return _fail('请输入密码')
        kwargs['password'] = password
    elif auth == 'key':
        if len(key_text) < 40:
            return _fail('把私钥内容整段贴进来（包含 BEGIN / END 那两行）')
        pkey = _load_pkey(key_text, passphrase)
        if pkey is None:
            return _fail('这段私钥读不了：格式不对，或者口令填错了')
        kwargs['pkey'] = pkey
    else:
        if not key_path:
            return _fail('填一下密钥文件路径，比如 ~/.ssh/id_ed25519')
        path = os.path.expanduser(key_path)
        if not os.path.isfile(path):
            return _fail('这个路径下没有文件：' + path)
        kwargs['key_filename'] = path
        if passphrase:
            kwargs['passphrase'] = passphrase

    kh_name = _kh_name(host, port)
    probe_client = _new_client()
    saved_fps = _stored_fps(probe_client, kh_name)
    saved_fp = saved_fps[0] if saved_fps else ''
    _close(probe_client)

    client, err = _attempt(kwargs)
    if err and err.get('fatal'):
        return _fail(err['fatal'])
    if err:
        key = err.get('key')
        fp = _fingerprint(key) if key is not None else ''
        key_type = key.get_name() if key is not None else ''
        if err.get('changed'):
            old_fp = _fingerprint(err['saved']) if err.get('saved') is not None else saved_fp
            if not (trust_fp and trust_fp == fp):
                return _fail('服务器出示的主机密钥和上次记录的不一样（也可能是对方重装了系统，'
                             '也可能是有人在中间冒充）。确认之前不要继续。',
                             need_trust=True, changed=True, fingerprint=fp, key_type=key_type,
                             host=kh_name, saved_fingerprint=old_fp)
            _remember_host(kh_name, key)
            client, err = _attempt(kwargs)
            if err:
                return _fail(err.get('fatal') or '指纹刚更新就又变了，已中止（这件事不正常）。')
        else:
            # 第一次连这台机器：直接记下指纹继续（OpenSSH accept-new 的做法），
            # 之后每次连接都会比对；对不上才会告警。
            _remember_host(kh_name, key)
            client, err = _attempt(kwargs)
            if err:
                return _fail(err.get('fatal') or '记下指纹后重连失败，已中止。')

    with _SESS_LOCK:
        if len(_SESSIONS) >= MAX_SESSIONS:
            alive = len(_SESSIONS)
            try:
                client.close()
            except Exception:
                pass
            return _fail('已经开着 %d 条连接了，先断开一条再连。' % alive)

    try:
        client.get_transport().set_keepalive(30)
    except Exception:
        pass
    os_info = _capture_env(client)
    sess = Session(client, host, port, user, auth, os_info, readonly, allow_sudo, timeout)
    sess.cwd = _home(sess)          # 默认落在登录目录，提示符里显示成 ~
    with _SESS_LOCK:
        _SESSIONS[sess.token] = sess
    host_fp = _remote_fp(client) or saved_fp
    _audit({'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'event': 'connect',
            'host': host, 'port': port, 'user': user, 'auth': auth, 'fp': host_fp})
    return JSONResponse({'ok': True, 'session': sess.token, 'host': host, 'port': port,
                         'user': user, 'auth': auth, 'os': os_info, 'readonly': readonly,
                         'allow_sudo': allow_sudo, 'timeout': timeout,
                         'fingerprint': host_fp, 'idle_timeout': IDLE_TIMEOUT,
                         'cwd': _display(sess, sess.cwd)})


def _remote_fp(client):
    """服务器这次出示的主机密钥指纹（最准确，直接用传输层拿）。"""
    try:
        return _fingerprint(client.get_transport().get_remote_server_key())
    except Exception:
        return ''


def _stored_fps(client, kh_name):
    """已记录在这台机器名下的指纹列表。

    注意：paramiko 的 HostKeys.lookup() 返回的是 {keytype: key} 映射
    （没记录时返回 None），不是单个 key，别直接丢给 _fingerprint。
    """
    try:
        found = client.get_host_keys().lookup(kh_name)
    except Exception:
        return []
    if not found:
        return []
    try:
        keys = list(found.values())
    except Exception:
        keys = [found]
    return [_fingerprint(k) for k in keys]



# =========================================================
# 会话目录保持 + Tab 补全
# 全部走 SFTP：路径只做字符串解析和列目录，一个字都不拼进 shell，
# 所以补全本身没有注入面，只读模式下也能用。
# =========================================================
def _sftp(sess):
    """每个会话复用一个 SFTP 通道；通道断了就重开一次。"""
    with sess.sftp_lock:
        cli = sess.sftp
        if cli is not None:
            try:
                cli.stat('.')
                return cli
            except Exception:
                try:
                    cli.close()
                except Exception:
                    pass
                sess.sftp = None
        try:
            cli = sess.client.open_sftp()
        except Exception:
            return None
        try:
            cli.get_channel().settimeout(10)
        except Exception:
            pass
        sess.sftp = cli
        return cli


def _home(sess):
    if not sess.home:
        cli = _sftp(sess)
        home = ''
        if cli is not None:
            try:
                home = (cli.normalize('.') or '').strip()
            except Exception:
                home = ''
        if not home.startswith('/'):
            home = '/root' if sess.user == 'root' else '/home/' + sess.user
        sess.home = home.rstrip('/') or '/'
    return sess.home


def _norm(path):
    path = (path or '/').strip() or '/'
    if not path.startswith('/'):
        path = '/' + path
    out = posixpath.normpath(path)
    return '/' if out == '/' else (out.rstrip('/') or '/')


def _unescape(text):
    return (text.replace('\\ ', ' ').replace("\\'", "'")
                .replace('\\"', '"').replace('\\\\', '\\'))


def _resolve(sess, target, base=None):
    """用户写的路径 -> 绝对路径。纯字符串运算，不碰服务器。"""
    raw = (target or '').strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        raw = raw[1:-1]
    raw = _unescape(raw)
    home = _home(sess)
    here = base or sess.cwd or home
    if raw == '':
        return here
    if raw == '~':
        return home
    if raw == '-':
        return sess.prev_cwd or here
    if raw.startswith('~'):
        return _norm(home + '/' + raw[1:].lstrip('/'))
    if raw.startswith('/'):
        return _norm(raw)
    return _norm(posixpath.join(here, raw))


def _display(sess, path):
    """绝对路径 -> 给人看的形式（登录目录显示成 ~）。"""
    home = _home(sess)
    if not path:
        return '~'
    if path == home:
        return '~'
    if path.startswith(home + '/'):
        return '~' + path[len(home):]
    return path


def _stat(sess, path):
    cli = _sftp(sess)
    if cli is None:
        return None
    try:
        return cli.stat(path)
    except Exception:
        try:
            return cli.lstat(path)
        except Exception:
            return None


def _entries(sess, path):
    """列目录，返回 [(名字, 是否目录)]；读不了返回 None（和「空目录」区分开）。"""
    cli = _sftp(sess)
    if cli is None:
        return None
    try:
        attrs = cli.listdir_attr(path)
    except Exception:
        return None
    out = []
    for a in attrs:
        name = getattr(a, 'filename', '') or ''
        if not name or name in ('.', '..'):
            continue
        mode = a.st_mode or 0
        is_dir = stat.S_ISDIR(mode)
        if not is_dir and stat.S_ISLNK(mode):
            try:
                is_dir = stat.S_ISDIR(cli.stat(posixpath.join(path, name)).st_mode or 0)
            except Exception:
                is_dir = False
        out.append((name, is_dir))
    out.sort(key=lambda x: (not x[1], x[0].lower()))
    return out


def _list_commands(sess):
    """PATH 里的命令名，抓一次缓存在会话里。"""
    if sess.cmd_cache is not None:
        return sess.cmd_cache
    names = set(SHELL_BUILTINS)
    path = ''
    with sess.sftp_lock:
        info = sess.os_info or {}
        path = str(info.get('path') or '')
        for folder in path.split(':'):
            folder = folder.strip()
            if not folder:
                continue
            items = _entries(sess, _norm(folder))
            if not items:
                continue
            for name, is_dir in items:
                if not is_dir:
                    names.add(name)
    sess.cmd_cache = sorted(names)
    return sess.cmd_cache


def _cd_step(sess, target):
    """切换会话目录。返回 (错误信息, 需要回显的目录)。"""
    raw = (target or '').strip()
    if not raw:
        path = _home(sess)
    else:
        probe = raw.replace('\\ ', '\x00')          # 转义空格不算分隔
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
            probe = ''
        if len(probe.split()) > 1:
            return 'cd: 参数太多了', ''
        path = _resolve(sess, raw)
    st = _stat(sess, path)
    if st is None:
        return 'cd: %s: 没有这个目录' % path, ''
    if not stat.S_ISDIR(st.st_mode or 0):
        return 'cd: %s: 不是目录' % path, ''
    sess.prev_cwd = sess.cwd or _home(sess)
    sess.cwd = path
    return '', (_display(sess, path) if raw == '-' else '')


def _alive(sess):
    try:
        tr = sess.client.get_transport()
        return bool(tr) and tr.is_active()
    except Exception:
        return False


def _do_exec(p):
    sess = _get(p.get('session'))
    if sess is None:
        return _fail('会话已断开或超时了，重新连接一下。')
    command = str(p.get('command') or '').strip()
    if not command:
        return _fail('命令是空的')
    if len(command) > MAX_CMD_LEN:
        return _fail('命令太长了（上限 %d 字符）' % MAX_CMD_LEN)
    if '\n' in command or '\r' in command:
        return _fail('一次只执行一条命令，别带换行')

    # ---- cd：会话自己记着当前目录（每条命令前自动 cd 回去）。
    # 路径只做字符串解析 + SFTP 校验，永远不进 shell，所以没有注入面。
    hit = CD_RE.match(command) if command.startswith('cd') else None
    if hit is not None:
        rest = hit.group('rest') or ''
        if (rest and hit.group('sep') not in ('&&', ';')) or rest.lstrip().startswith('|'):
            hit = None
    if hit is not None:
        err, echo_dir = _cd_step(sess, (hit.group('t') or '').strip())
        rest = (hit.group('rest') or '').strip()
        if err or not rest:
            code = 1 if err else 0
            sess.count += 1
            sess.history.append({'cmd': command, 'out': err or echo_dir, 'code': code})
            del sess.history[:-HISTORY_KEEP]
            _audit({'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'event': 'exec', 'host': sess.host,
                    'user': sess.user, 'cmd': command, 'level': 'low', 'code': code,
                    'dur': 0, 'timeout': False})
            return JSONResponse({'ok': True, 'command': command, 'stdout': echo_dir,
                                 'stderr': err, 'exit_code': code, 'duration': 0,
                                 'timed_out': False, 'truncated': False, 'level': 'low',
                                 'cwd': _display(sess, sess.cwd), 'count': sess.count})
        command = rest

    why = _interactive_reason(command)
    if why:
        return _fail(why)
    level, reason = _classify(command)
    if level == 'blocked':
        return _fail(reason + '：' + command[:120], blocked=True)
    # sudo 可能出现在管道 / 分号后面，所以整行都要查；关闭 sudo 时一律拒绝
    has_sudo = bool(SUDO_RE.search(command))
    if has_sudo and not sess.allow_sudo:
        return _fail('这次会话没开 sudo。要提权就先断开，在连接时打开「允许 sudo」再连。')
    if sess.readonly and not (level == 'low' and _is_readonly(command)):
        return _fail('会话开着只读模式，这条会改动服务器，先不执行：' + command[:120],
                     readonly_blocked=True, level=level)
    if level == 'destructive' and not p.get('ack'):
        return _fail(reason + '。确认要执行的话，在弹窗里点确定。',
                     needs_confirm=True, command=command, level=level)

    # 统一改写成 sudo -n：非交互执行，宁可直接失败，也不让会话挂在密码提示上
    hist_cmd = SUDO_RE.sub(lambda m: m.group(1) + 'sudo -n ', command) if has_sudo else command
    # 会话保持工作目录：每条命令前先 cd 回当前目录（路径走 shlex.quote，注入不了）
    run_cmd = ('cd ' + shlex.quote(sess.cwd) + ' && ' + hist_cmd) if sess.cwd else hist_cmd

    now = time.time()
    with sess.lock:
        sess.cmd_times = [t for t in sess.cmd_times if now - t < 60]
        if len(sess.cmd_times) >= RATE_PER_MIN:
            return _fail('一分钟内执行得有点多，缓一下再继续。')
        sess.cmd_times.append(now)
        start = time.time()
        try:
            out, err, code, timed_out, truncated = _run(sess.client, run_cmd, sess.timeout)
        except Exception as exc:
            if not _alive(sess):
                _drop(sess.token)
                return _fail('连接已经掉了，重新连一次。')
            return _fail('命令没能跑起来：' + str(exc)[:200])
        dur = round(time.time() - start, 2)
        sess.count += 1
        sess.history.append({'cmd': hist_cmd, 'out': (out or err)[:600], 'code': code})
        del sess.history[:-HISTORY_KEEP]

    if timed_out and not _alive(sess):
        _drop(sess.token)
    _audit({'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'event': 'exec', 'host': sess.host,
            'user': sess.user, 'cmd': run_cmd, 'level': level, 'code': code,
            'dur': dur, 'timeout': timed_out})
    return JSONResponse({'ok': True, 'command': hist_cmd, 'stdout': out, 'stderr': err,
                         'exit_code': code, 'duration': dur, 'timed_out': timed_out,
                         'truncated': truncated, 'level': level, 'count': sess.count,
                         'cwd': _display(sess, sess.cwd)})


AI_SYSTEM = '\n'.join([
    '你是一位资深 Linux 运维，正在通过 SSH 帮用户管理一台服务器。',
    '用户会用大白话说想干什么，你要把它翻译成一条条能在非交互 shell 里执行的命令。',
    '硬性要求：',
    '1. 只输出 JSON，不要 Markdown 代码块，不要解释；',
    '2. steps 最多 6 条，每条只放一条命令，不能有换行；',
    '3. 命令必须是「一次性输出」的写法：禁止 vim / less / top / tail -f 这类要交互的，',
    '   要看进程用 top -b -n1 或 ps，要看日志用 tail -n 200 / journalctl -n 100；',
    '4. 先诊断再动手：除非用户明确要求改，前面的步骤尽量是查看类命令；',
    '5. why 用一句中文说明这条命令干什么、看什么，别写套话；',
    '6. 不确定就说出来：如果缺信息，把「需要先确认什么」写进第一个步骤的 why 里；',
    '7. 绝对不要给出 rm -rf、mkfs、dd 写盘、关机重启这类不可逆命令，除非用户明确要求，',
    '   且要在 why 里写清后果。',
    '输出格式：',
    '{"summary":"一句话说明你的思路","steps":[{"cmd":"","why":""}]}',
])


def _post_llm(system, user, max_tokens, temperature):
    key = llm_key()
    if not key:
        return None, '没配模型 Key'
    body = {
        'model': llm_model(),
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': False,
    }
    try:
        resp = requests.post(
            llm_endpoint(), json=body, timeout=(15, 90),
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
        )
    except requests.RequestException as exc:
        return None, '模型请求失败：' + str(exc)[:100]
    if resp.status_code >= 400:
        return None, '模型返回错误 ' + str(resp.status_code)
    try:
        return resp.json()['choices'][0]['message']['content'], ''
    except (ValueError, KeyError, IndexError, TypeError):
        return None, '模型返回格式异常'


def _extract_json(text):
    raw = re.sub(r'```[a-zA-Z]*', '', text or '')
    start, end = raw.find('{'), raw.rfind('}')
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _do_ai(p):
    sess = _get(p.get('session'))
    if sess is None:
        return _fail('会话已断开或超时了，重新连接一下。')
    prompt = str(p.get('prompt') or '').strip()[:600]
    if not prompt:
        return _fail('先说要干什么，比如「看看磁盘为什么满了」')
    if not llm_key():
        return _fail('服务端还没配模型 Key，AI 方案用不了。')
    os_info = sess.os_info or {}
    lines = ['目标主机：%s@%s:%d' % (sess.user, sess.host, sess.port)]
    if os_info.get('os'):
        lines.append('系统：' + os_info['os'][:160])
    if os_info.get('uname'):
        lines.append('内核：' + os_info['uname'][:100])
    if os_info.get('id'):
        lines.append('当前身份：' + os_info['id'][:100])
    lines.append('当前目录：' + _display(sess, sess.cwd))
    lines.append('会话模式：' + ('只读（只能给查看类命令）' if sess.readonly else '可写（能改状态，谨慎）'))
    lines.append('是否允许 sudo：' + ('是' if sess.allow_sudo else '否'))
    if sess.history:
        lines.append('刚才执行过的命令和大致输出：')
        for item in sess.history[-4:]:
            lines.append('- `%s` -> %s' % (item['cmd'][:120], (item['out'] or '').replace('\n', ' ')[:200]))
    lines.append('用户想做的事：' + prompt)
    lines.append('请给出命令方案。')
    raw, note = _post_llm(AI_SYSTEM, '\n'.join(lines), 1200, 0.25)
    if raw is None:
        return _fail(note or '模型没返回内容')
    data = _extract_json(raw)
    rows = data.get('steps')
    if not isinstance(rows, list):
        return _fail('模型这次没给出可用的命令，换个说法再试。')
    steps = []
    for row in rows[:AI_STEP_MAX]:
        if not isinstance(row, dict):
            continue
        cmd = re.sub(r'\s+', ' ', str(row.get('cmd') or '')).strip()[:500]
        if not cmd or len(cmd) < 2:
            continue
        level, reason = _classify(cmd)
        why = str(row.get('why') or '').strip()[:200]
        steps.append({'cmd': cmd, 'why': why, 'level': level, 'level_reason': reason,
                      'readonly': level == 'low' and _is_readonly(cmd),
                      'blocked': level == 'blocked',
                      'interactive': _interactive_reason(cmd)})
    if not steps:
        return _fail('模型这次没给出可用的命令，换个说法再试。')
    return JSONResponse({'ok': True, 'summary': str(data.get('summary') or '')[:300],
                         'steps': steps, 'readonly': sess.readonly})


def _do_complete(p):
    """Tab 补全：命令名走 PATH，参数走目录列表。纯 SFTP，不执行任何命令。"""
    sess = _get(p.get('session'))
    if sess is None:
        return _fail('会话已断开或超时了，重新连接一下。')
    line = str(p.get('line') or '')
    if len(line) > MAX_CMD_LEN:
        line = line[:MAX_CMD_LEN]
    try:
        pos = int(p.get('pos'))
    except (TypeError, ValueError):
        pos = len(line)
    pos = max(0, min(len(line), pos))
    head = line[:pos]
    start = pos
    while start > 0 and head[start - 1] not in (' ', '\t'):
        start -= 1
    token = head[start:pos]
    before = head[:start]
    # 第一个词（sudo 后面那个也算）补命令名，其余补路径
    at_cmd = bool(re.match(r'^\s*(sudo(\s+-\S+)*\s*)?$', before))
    kind = 'cmd' if (at_cmd and '/' not in token) else 'path'
    items = []
    message = ''
    with sess.sftp_lock:
        if kind == 'cmd':
            low = token.lower()
            names = _list_commands(sess)
            items = [c for c in names if c.startswith(token)] or \
                    [c for c in names if c.lower().startswith(low) and c[0].islower()]
        else:
            if token.startswith('~') and '/' not in token:
                dir_lit, name = '~/', token[1:]
            elif '/' in token:
                cut = token.rfind('/')
                dir_lit, name = token[:cut + 1], token[cut + 1:]
            else:
                dir_lit, name = '', token
            base = _resolve(sess, dir_lit) if dir_lit else (sess.cwd or _home(sess))
            entries = _entries(sess, base)
            if entries is None:
                return JSONResponse({'ok': True, 'items': [], 'token': token, 'start': start,
                                     'end': pos, 'kind': kind,
                                     'message': '这个目录读不了：' + (dir_lit or _display(sess, base))})
            want = _unescape(name)
            for nm, is_dir in entries:
                if want and not nm.startswith(want):
                    continue
                if not want and nm.startswith('.'):
                    continue          # 隐藏文件要点一个 . 才列出来
                items.append(dir_lit + nm.replace(' ', '\\ ') + ('/' if is_dir else ''))
    return JSONResponse({'ok': True, 'items': items[:MAX_COMPLETE_ITEMS], 'token': token,
                         'start': start, 'end': pos, 'kind': kind, 'message': message})


def _do_state(token):
    sess = _get(token)
    if sess is None:
        return {'ok': True, 'connected': False}
    return {'ok': True, 'connected': True, 'host': sess.host, 'port': sess.port,
            'user': sess.user, 'readonly': sess.readonly, 'allow_sudo': sess.allow_sudo,
            'timeout': sess.timeout, 'count': sess.count, 'os': sess.os_info,
            'cwd': _display(sess, sess.cwd),
            'connected_for': int(time.time() - sess.created),
            'idle_left': max(0, int(IDLE_TIMEOUT - (time.time() - sess.last)))}


def _do_readonly(token, value):
    sess = _get(token)
    if sess is None:
        return _fail('会话已断开或超时了，重新连接一下。')
    sess.readonly = bool(value)
    _audit({'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'event': 'readonly',
            'host': sess.host, 'user': sess.user, 'value': sess.readonly})
    return JSONResponse({'ok': True, 'readonly': sess.readonly})


# =========================================================
# 路由
# =========================================================
@router.get('/ecs-ssh', response_class=HTMLResponse)
def ecs_ssh_page():
    if _TEMPLATE_FILE.exists():
        html = _TEMPLATE_FILE.read_text(encoding='utf-8')
    else:
        html = ('<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">'
                '<body style="font-family:system-ui"><h2>模板文件缺失</h2>'
                '<p>请确认 templates/ecs-ssh.html 存在。</p></body></html>')
    return HTMLResponse(html)


async def _payload(request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


@router.post('/ecs-ssh/api/connect')
async def ecs_ssh_connect(request: Request):
    return await run_in_threadpool(_do_connect, await _payload(request))


@router.post('/ecs-ssh/api/exec')
async def ecs_ssh_exec(request: Request):
    return await run_in_threadpool(_do_exec, await _payload(request))


@router.post('/ecs-ssh/api/ai')
async def ecs_ssh_ai(request: Request):
    return await run_in_threadpool(_do_ai, await _payload(request))


@router.post('/ecs-ssh/api/complete')
async def ecs_ssh_complete(request: Request):
    return await run_in_threadpool(_do_complete, await _payload(request))


@router.post('/ecs-ssh/api/disconnect')
async def ecs_ssh_disconnect(request: Request):
    data = await _payload(request)
    token = str(data.get('session') or '')
    sess = _drop(token)
    if sess is not None:
        _audit({'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'event': 'disconnect',
                'host': sess.host, 'user': sess.user, 'cmds': sess.count})
    return JSONResponse({'ok': True})


@router.get('/ecs-ssh/api/state')
def ecs_ssh_state(session: str = ''):
    return JSONResponse(_do_state(session))


@router.post('/ecs-ssh/api/readonly')
async def ecs_ssh_readonly(request: Request):
    data = await _payload(request)
    return _do_readonly(str(data.get('session') or ''), data.get('readonly'))


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    _standalone = FastAPI(title='ECS SSH 管理', description='连上 ECS，AI 出方案，你点确认', version='1.0.0')
    _standalone.include_router(router)
    uvicorn.run(_standalone, host='127.0.0.1', port=8007)
