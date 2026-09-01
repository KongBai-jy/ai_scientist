r"""极简 boot runner：
- 执行给定命令（一般是 venv\Scripts\python.exe src\main.py）
- 同时把 stdout/stderr 按 utf-8 写入 boot.log（追加）和继承控制台（便于崩溃时一眼看到）
用法：scripts\_boot_runner.py "<boot_log_path>" "<python_exe>" "<main_py_path>" [extra main.py args...]
"""
import io
import os
import subprocess
import sys


class Tee(io.TextIOBase):
    """把写入分发给 N 个 text streams（类似 *nix tee）。"""

    def __init__(self, *streams):
        super().__init__()
        self._streams = [s for s in streams if s is not None]

    def write(self, data: str):
        if not data:
            return 0
        total = 0
        for s in self._streams:
            try:
                total += s.write(data) or 0
                if hasattr(s, "flush"):
                    s.flush()
            except Exception:
                pass
        return total or len(data)

    def flush(self):
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass


def main(argv):
    if len(argv) < 4:
        print("USAGE: _boot_runner.py <boot_log> <python_exe> <main_py> [args...]",
              file=sys.stderr)
        return 2

    boot_log = argv[1]
    python_exe = argv[2]
    main_py = argv[3]
    rest = argv[4:]

    os.makedirs(os.path.dirname(os.path.abspath(boot_log)) or ".", exist_ok=True)
    # start.bat 每次启动前会清空 boot.log；这里追加，避免子进程覆盖。
    # newline="\n"：否则子进程本已带 CRLF，再被转义成 \r\r\n，日志行距翻倍。
    log_fp = open(boot_log, "a", encoding="utf-8", errors="replace", buffering=1, newline="\n")
    tee_out = Tee(sys.stdout, log_fp)
    tee_err = Tee(sys.stderr, log_fp)

    tee_out.write("--- boot runner started ---\n")
    tee_out.write("python: {}\n".format(python_exe))
    tee_out.write("entry:  {}\n".format(main_py))
    tee_out.write("args:   {}\n".format(" ".join(rest) or "(none)"))
    tee_out.write("log:    {}\n".format(boot_log))
    tee_out.flush()

    cmd = [python_exe, "-u", main_py, *rest]
    # 子进程 stdout 是管道，中文 Windows 下 Python 默认按 cp936 编码日志，
    # 而这里固定按 utf-8 解码 -> 中文变替换符。强制子进程用 utf-8 吐字。
    child_env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=child_env,
        )
    except FileNotFoundError as e:
        tee_err.write("boot runner: cannot locate python or entry: {}\n".format(e))
        log_fp.close()
        return 3

    # 逐行流式转发：uvicorn 是常驻进程，必须边跑边写，
    # 否则启动窗口与 boot.log 会一路空白，崩溃时也拿不到 Traceback。
    try:
        for raw_line in proc.stdout:
            try:
                text = raw_line.decode("utf-8", errors="replace")
            except Exception:
                text = raw_line.decode("mbcs", errors="replace")
            if text.endswith("\r\n"):
                text = text[:-2] + "\n"
            tee_out.write(text)
    except KeyboardInterrupt:
        proc.terminate()
        tee_err.write("boot runner: interrupted, terminating child\n")
    except Exception as e:
        tee_err.write("boot runner unhandled error: {}\n".format(e))
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass

    returncode = proc.wait()
    tee_out.write("--- boot runner finished (exit={}) ---\n".format(returncode))
    log_fp.close()
    return returncode


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as _e:
        print("Fatal in boot runner:", repr(_e), file=sys.stderr)
        sys.exit(99)
