# -*- mode: python ; coding: utf-8 -*-
# AI Scientist 打包配置（PyInstaller）
# 用法: pyinstaller build_exe.spec
# 产物: dist/ai_scientist.exe（onefile，前端 web/ 打包在内）

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('web', 'web'),          # 前端静态资源打进包内
    ] + collect_data_files('chromadb') + collect_data_files('tiktoken'),
    hiddenimports=[
        # uvicorn 动态导入
        'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        # langchain 家族动态导入
        'langchain_community', 'langchain_core',
        'sse_starlette',
        # chromadb 遥测等模块是运行时懒加载，静态分析抓不到，必须全量收集
        'posthog',
    ] + collect_submodules('chromadb'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 明显用不到的重量级依赖，减小体积
        'tkinter', 'unittest', 'pydoc_data',
        'matplotlib', 'IPython', 'notebook', 'jupyter',
        'pygments', 'pytest',
        'torch', 'tensorflow', 'keras',
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ai_scientist',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # 保留控制台窗口（服务日志可见，便于排查）
    disable_windowed_traceback=False,
)
