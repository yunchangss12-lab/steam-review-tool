"""Steam 差评比对工具 — 安装器（自包含，无需外部依赖）。

双击运行后，引导用户选择安装目录，然后：
  1. 解压内置的主程序（Steam差评比对工具.exe）
  2. 释放 config.local.json 配置模板 + 使用说明
  3. 创建桌面快捷方式 + 开始菜单快捷方式
  4. 写入卸载信息（注册表），支持通过控制面板卸载

主程序 EXE、配置模板、使用说明均作为 PyInstaller 的 data 资源内嵌，
运行时经 sys._MEIPASS 读取。
"""

import os
import shutil
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Steam差评比对工具"
APP_EXE_NAME = "Steam差评比对工具.exe"
VERSION = "1.0.0"


def _resource_dir() -> str:
    """PyInstaller 打包后资源所在目录（_MEIPASS），开发时是 installer 目录。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return base


def _find_payload_files() -> dict[str, str]:
    """定位内嵌的三个资源文件。"""
    res = _resource_dir()
    payload = {
        "exe": os.path.join(res, APP_EXE_NAME),
        "config": os.path.join(res, "config.local.json"),
        "readme": os.path.join(res, "使用说明.md"),
    }
    # 兼容：资源可能直接放在 res 目录或 payload 子目录
    for key, path in list(payload.items()):
        if not os.path.isfile(path):
            alt = os.path.join(res, "payload", os.path.basename(path))
            if os.path.isfile(alt):
                payload[key] = alt
    return payload


def _create_shortcut(target: str, link_path: str, workdir: str) -> None:
    """创建 Windows 快捷方式（.lnk），不依赖 win32com。"""
    # 用 PowerShell + WScript.Shell 创建快捷方式（Windows 自带，无需第三方）
    import subprocess

    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{link_path.replace(chr(39), chr(39)+chr(39))}'); "
        f"$sc.TargetPath = '{target.replace(chr(39), chr(39)+chr(39))}'; "
        f"$sc.WorkingDirectory = '{workdir.replace(chr(39), chr(39)+chr(39))}'; "
        "$sc.Save();"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _write_uninstall_info(install_dir: str) -> None:
    """写入卸载信息到注册表（HKCU，无需管理员权限）。"""
    try:
        import winreg

        key = winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SteamReviewTool",
        )
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "Hunter Studio")
        winreg.SetValueEx(
            key, "DisplayIcon", 0, winreg.REG_SZ,
            os.path.join(install_dir, APP_EXE_NAME),
        )
        uninstaller = os.path.join(install_dir, "卸载工具.bat")
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, uninstaller)
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, install_dir)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
    except Exception:
        pass


def _write_uninstaller_bat(install_dir: str) -> None:
    """在安装目录写一个卸载脚本。"""
    bat_path = os.path.join(install_dir, "卸载工具.bat")
    content = (
        "@echo off\n"
        "chcp 65001 >nul\n"
        "echo 正在卸载 Steam 差评比对工具...\n"
        'reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\SteamReviewTool" /f >nul 2>&1\n'
        'del "%USERPROFILE%\\Desktop\\Steam差评比对工具.lnk" >nul 2>&1\n'
        'del "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Steam差评比对工具.lnk" >nul 2>&1\n'
        'rmdir /s /q "' + install_dir + '" >nul 2>&1\n'
        "echo 卸载完成。\n"
        "pause\n"
    )
    with open(bat_path, "w", encoding="gbk") as f:
        f.write(content)


def _install(install_dir: str, progress: ttk.Progressbar, status: tk.StringVar) -> bool:
    """执行安装，返回是否成功。"""
    payload = _find_payload_files()
    if not os.path.isfile(payload["exe"]):
        messagebox.showerror("安装失败", "找不到主程序文件，安装包可能已损坏。")
        return False

    try:
        os.makedirs(install_dir, exist_ok=True)

        # 1. 复制主程序
        status.set("正在复制主程序...")
        progress["value"] = 20
        target_exe = os.path.join(install_dir, APP_EXE_NAME)
        shutil.copy2(payload["exe"], target_exe)

        # 2. 释放 config 模板（若目标已存在则不覆盖，保护用户已有配置）
        status.set("正在释放配置文件...")
        progress["value"] = 50
        cfg_target = os.path.join(install_dir, "config.local.json")
        if not os.path.isfile(cfg_target):
            shutil.copy2(payload["config"], cfg_target)

        # 3. 释放使用说明
        status.set("正在释放使用说明...")
        progress["value"] = 70
        shutil.copy2(payload["readme"], os.path.join(install_dir, "使用说明.md"))

        # 4. 创建快捷方式
        status.set("正在创建快捷方式...")
        progress["value"] = 85
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        _create_shortcut(target_exe, os.path.join(desktop, APP_NAME + ".lnk"), install_dir)
        startmenu = os.path.join(
            os.environ.get("APPDATA", ""),
            "Microsoft", "Windows", "Start Menu", "Programs",
        )
        _create_shortcut(target_exe, os.path.join(startmenu, APP_NAME + ".lnk"), install_dir)

        # 5. 写卸载信息
        status.set("正在写入卸载信息...")
        _write_uninstall_info(install_dir)
        _write_uninstaller_bat(install_dir)

        progress["value"] = 100
        return True
    except Exception as e:  # noqa: BLE001
        messagebox.showerror("安装失败", f"安装过程中出错：\n{e}")
        return False


def build_installer_ui() -> None:
    root = tk.Tk()
    root.title(f"{APP_NAME} 安装向导")
    root.geometry("520x360")
    root.resizable(False, False)
    root.configure(bg="#f5f6f7")

    card = "#ffffff"
    fg = "#1a1a1a"
    sub = "#666666"
    accent = "#1e9b50"

    # 标题栏
    header = tk.Frame(root, bg=accent)
    header.pack(fill="x")
    tk.Label(
        header, text=f"{APP_NAME} 安装向导", bg=accent, fg="white",
        font=("Microsoft YaHei", 15, "bold"), padx=20, pady=16,
    ).pack(anchor="w")

    body = tk.Frame(root, bg="#f5f6f7")
    body.pack(fill="both", expand=True, padx=20, pady=16)

    tk.Label(
        body, text="本工具用于抓取 Steam 历史差评并比对更新记录，\n"
                   "自动归类并生成开发者回复文案。",
        bg="#f5f6f7", fg=fg, font=("Microsoft YaHei", 10), justify="left",
    ).pack(anchor="w")

    # 安装目录选择
    dir_frame = tk.Frame(body, bg="#f5f6f7")
    dir_frame.pack(fill="x", pady=(16, 4))
    tk.Label(dir_frame, text="安装目录：", bg="#f5f6f7", fg=fg,
             font=("Microsoft YaHei", 10)).pack(side="left")

    default_dir = os.path.join(os.environ.get("LOCALAPPDATA", "C:\\"), "Programs", "SteamReviewTool")
    dir_var = tk.StringVar(value=default_dir)
    dir_entry = tk.Entry(dir_frame, textvariable=dir_var, font=("Consolas", 9), width=40)
    dir_entry.pack(side="left", padx=(0, 6))

    def _browse():
        d = filedialog.askdirectory(title="选择安装目录", initialdir=default_dir)
        if d:
            dir_var.set(d)

    tk.Button(dir_frame, text="浏览…", bg="#e8f0ea", fg=fg, font=("Microsoft YaHei", 9),
              relief="flat", cursor="hand2", command=_browse).pack(side="left")

    # 进度条
    progress = ttk.Progressbar(body, mode="determinate", maximum=100)
    progress.pack(fill="x", pady=(16, 6))

    status_var = tk.StringVar(value="准备就绪。")
    tk.Label(body, textvariable=status_var, bg="#f5f6f7", fg=sub,
             font=("Microsoft YaHei", 9)).pack(anchor="w")

    # 按钮区
    btn_frame = tk.Frame(body, bg="#f5f6f7")
    btn_frame.pack(side="bottom", fill="x", pady=(16, 0))

    def _do_install():
        install_dir = dir_var.get().strip()
        if not install_dir:
            messagebox.showwarning("提示", "请选择安装目录。")
            return
        install_btn.configure(state="disabled")
        ok = _install(install_dir, progress, status_var)
        if ok:
            status_var.set("安装完成！")
            messagebox.showinfo(
                "安装完成",
                f"安装成功！\n\n已创建桌面和开始菜单快捷方式。\n\n"
                f"安装目录：{install_dir}\n\n"
                f"首次使用请在界面「大模型(可选)」区填写 API Key，\n"
                f"或编辑安装目录下的 config.local.json。",
            )
            root.destroy()
        else:
            install_btn.configure(state="normal")

    def _cancel():
        root.destroy()

    install_btn = tk.Button(btn_frame, text="开始安装", bg=accent, fg="white",
                            font=("Microsoft YaHei", 11, "bold"), padx=24, pady=6,
                            relief="flat", cursor="hand2", command=_do_install)
    install_btn.pack(side="right")
    tk.Button(btn_frame, text="取消", bg="#ffffff", fg=fg, font=("Microsoft YaHei", 11),
              padx=18, pady=6, relief="flat", cursor="hand2", command=_cancel).pack(side="right", padx=8)

    root.mainloop()


def main() -> int:
    try:
        build_installer_ui()
        return 0
    except Exception as e:  # noqa: BLE001
        messagebox.showerror("错误", f"安装器运行出错：\n{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
