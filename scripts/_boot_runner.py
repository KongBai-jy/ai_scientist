"""极简 boot runner：
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
    log_fp = open(boot_log, "a", encoding="utf-8", errors="replace", buffering=1)
    tee_out = Tee(sys.stdout, log_fp)
    tee_err = Tee(sys.stderr, log_fp)

    tee_out.write("--- boot runner started ---\n")
    tee_out.write("python: {}\n".format(python_exe))
    tee_out.write("entry:  {}\n".format(main_py))
    tee_out.write("args:   {}\n".format(" ".join(rest) or "(none)"))
    tee_out.write("log:    {}\n".format(boot_log))
    tee_out.flush()

    cmd = [python_exe, "-u", main_py, *rest]
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
            creationflags=0,
        )
        # 子进程所有输出（stdout+stderr 合并）逐字节以 utf-8 解码后双写。
        raw = completed.stdout or b""
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = raw.decode("mbcs", errors="replace")
        tee_out.write(text)
        if not text.endswith("\n"):
            tee_out.write("\n")
        tee_out.write("--- boot runner finished (exit={}) ---\n".format(completed.returncode))
        log_fp.flush()
        log_fp.close()
        return completed.returncode
    except FileNotFoundError as e:
        tee_err.write("boot runner: cannot locate python or entry: {}\n".format(e))
        log_fp.flush()
        log_fp.close()
        return 3
    except Exception as e:
        tee_err.write("boot runner unhandled error: {}\n".format(e))
        log_fp.flush()
        log_fp.close()
        return 4


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception as _e:
        print("Fatal in boot runner:", repr(_e), file=sys.stderr)
        sys.exit(99)
