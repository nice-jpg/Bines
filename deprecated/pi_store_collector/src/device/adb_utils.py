"""
美团机票价格采集 — ADB 工具模块

封装所有与 Android 设备的 ADB 交互操作。
"""

import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from typing import Optional, Tuple, List, Dict

import config


def _adb_cmd_prefix() -> List[str]:
    """构建 adb 命令前缀（含可选设备序列号）"""
    cmd = ["adb"]
    if config.ADB_DEVICE_SERIAL:
        cmd.extend(["-s", config.ADB_DEVICE_SERIAL])
    return cmd


def run_adb(args: List[str], timeout: int = 30,
           lenient: bool = False) -> str:
    """
    执行 adb 命令并返回 stdout。

    :param args: adb 子命令参数列表（不含 'adb' 本身）
    :param timeout: 超时秒数
    :param lenient: 宽松模式 — 不因非零退出码报错（某些命令如 uiautomator dump
                    即使成功也会返回非零退出码）
    :return: stdout 输出
    :raises RuntimeError: 命令执行失败时（lenient=False）
    """
    cmd = _adb_cmd_prefix() + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0 and not lenient:
            raise RuntimeError(
                f"ADB command failed: {' '.join(cmd)}\n"
                f"stderr: {result.stderr.strip()}"
            )
        # 合并 stdout 和 stderr（部分命令把输出写到 stderr）
        output = result.stdout.strip()
        if not output and result.stderr.strip():
            output = result.stderr.strip()
        return output
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ADB command timed out: {' '.join(cmd)}")


def check_device() -> bool:
    """检查是否有 ADB 设备连接"""
    output = run_adb(["devices"])
    lines = output.strip().split("\n")
    # 第一行是 "List of devices attached", 之后每行是一个设备
    for line in lines[1:]:
        parts = line.strip().split("\t")
        if len(parts) == 2 and parts[1] == "device":
            if config.ADB_DEVICE_SERIAL:
                if parts[0] == config.ADB_DEVICE_SERIAL:
                    return True
            else:
                return True
    return False


def start_app(package: str = config.MEITUAN_PACKAGE,
              activity: str = config.MEITUAN_MAIN_ACTIVITY) -> None:
    """启动指定 App"""
    run_adb(["shell", "am", "start", "-n", f"{package}/{activity}"])
    print(f"[ADB] 已启动 {package}")


def stop_app(package: str = config.MEITUAN_PACKAGE) -> None:
    """强制停止 App"""
    run_adb(["shell", "am", "force-stop", package])
    print(f"[ADB] 已停止 {package}")


def tap(x: int, y: int) -> None:
    """模拟点击屏幕坐标"""
    run_adb(["shell", "input", "tap", str(x), str(y)])
    print(f"[ADB] 点击 ({x}, {y})")


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500) -> None:
    """模拟滑动"""
    run_adb([
        "shell", "input", "swipe",
        str(x1), str(y1), str(x2), str(y2), str(duration_ms),
    ])
    print(f"[ADB] 滑动 ({x1},{y1}) -> ({x2},{y2}), {duration_ms}ms")


def scroll_down() -> None:
    """向下滚动一屏（从屏幕中下部向上滑动）"""
    cx = config.SCREEN_WIDTH // 2
    y_start = int(config.SCREEN_HEIGHT * 0.75)
    y_end = int(config.SCREEN_HEIGHT * 0.25)
    swipe(cx, y_start, cx, y_end, 800)


def scroll_up() -> None:
    """向上滚动一屏"""
    cx = config.SCREEN_WIDTH // 2
    y_start = int(config.SCREEN_HEIGHT * 0.25)
    y_end = int(config.SCREEN_HEIGHT * 0.75)
    swipe(cx, y_start, cx, y_end, 800)


def input_text(text: str) -> None:
    """输入文字（仅支持 ASCII / 拼音）"""
    # 对于中文输入，使用 adb shell am broadcast 方式更可靠
    run_adb(["shell", "input", "text", text])
    print(f"[ADB] 输入文字: {text}")


def input_chinese(text: str) -> None:
    """
    在已 root 设备上通过 ADBKeyboard 输入中文。
    如果未安装 ADBKeyboard，降级使用剪贴板方式。
    """
    # 方法1: 尝试通过 ADBKeyboard 的 broadcast 输入
    try:
        run_adb([
            "shell", "am", "broadcast",
            "-a", "ADB_INPUT_TEXT",
            "--es", "msg", text,
        ])
        print(f"[ADB] 通过 ADBKeyboard 输入中文: {text}")
        return
    except RuntimeError:
        pass

    # 方法2: 使用剪贴板（需要 root 权限）
    # 将文字写入剪贴板并粘贴
    run_adb(["shell", "su", "-c",
             f"service call clipboard 2 i32 1 i32 {len(text)} "
             f"s16 '{text}'"])
    # 模拟粘贴 (Ctrl+V)
    run_adb(["shell", "input", "keyevent", "279"])  # KEYCODE_PASTE
    print(f"[ADB] 通过剪贴板输入中文: {text}")


def key_event(keycode: int) -> None:
    """发送按键事件"""
    run_adb(["shell", "input", "keyevent", str(keycode)])
    print(f"[ADB] 发送按键 keycode={keycode}")


def key_back() -> None:
    """按返回键"""
    key_event(4)  # KEYCODE_BACK


def key_home() -> None:
    """按 Home 键"""
    key_event(3)  # KEYCODE_HOME


def key_enter() -> None:
    """按回车键"""
    key_event(66)  # KEYCODE_ENTER


def disable_phantom_process_killer() -> None:
    """
    禁用 Android 12+ 的 Phantom Process Killer。
    这是导致 uiautomator dump 被 Killed 的主要原因。
    需要设备已 root 或通过 adb 设置。
    """
    print("[ADB] 尝试禁用 Phantom Process Killer ...")

    # 方法 1: 通过 device_config (Android 12+)
    try:
        run_adb([
            "shell", "device_config", "set_sync_disabled_for_tests", "persistent",
        ], lenient=True)
        run_adb([
            "shell", "device_config", "put",
            "activity_manager",
            "max_phantom_processes", "2147483647",
        ], lenient=True)
        print("[ADB] ✅ 已通过 device_config 禁用 Phantom Process Killer")
        return
    except RuntimeError:
        pass

    # 方法 2: 通过 settings (备选)
    try:
        run_adb([
            "shell", "settings", "put", "global",
            "settings_enable_monitor_phantom_procs", "false",
        ], lenient=True)
        print("[ADB] ✅ 已通过 settings 禁用 Phantom Process Killer")
        return
    except RuntimeError:
        pass

    print("[ADB] ⚠️ 无法自动禁用 Phantom Process Killer，uiautomator dump 可能失败")


def prepare_device() -> None:
    """
    一次性的设备准备工作：
    1. 禁用 Phantom Process Killer
    2. 清理旧的 dump 文件
    """
    disable_phantom_process_killer()

    # 清理旧的 dump 文件
    run_adb(["shell", "rm", "-f", "/sdcard/window_dump.xml"], lenient=True)


def _dump_strategy_standard(device_path: str) -> Optional[str]:
    """策略 1: 标准 uiautomator dump"""
    print("[ADB] dump 策略1: 标准 uiautomator dump ...")
    try:
        output = run_adb(
            ["shell", "uiautomator", "dump", device_path],
            lenient=True, timeout=15,
        )
        time.sleep(config.DUMP_WAIT)

        # 检查输出是否有成功标记
        if "Killed" in output:
            print("[ADB]   → 进程被 Killed")
            return None

        # 拉取文件
        local_tmp = "/tmp/airplicer_ui_dump.xml"
        run_adb(["pull", device_path, local_tmp], lenient=True)

        with open(local_tmp, "r", encoding="utf-8") as f:
            content = f.read()

        if "<hierarchy" in content and len(content) > 100:
            print(f"[ADB]   → ✅ 成功 ({len(content)} bytes)")
            return content
        else:
            print("[ADB]   → 文件为空或内容无效")
            return None
    except Exception as e:
        print(f"[ADB]   → 失败: {e}")
        return None


def _dump_strategy_compressed(device_path: str) -> Optional[str]:
    """策略 2: 带 --compressed 参数的 uiautomator dump"""
    print("[ADB] dump 策略2: compressed uiautomator dump ...")
    try:
        output = run_adb(
            ["shell", "uiautomator", "dump", "--compressed", device_path],
            lenient=True, timeout=15,
        )
        time.sleep(config.DUMP_WAIT)

        if "Killed" in output:
            print("[ADB]   → 进程被 Killed")
            return None

        local_tmp = "/tmp/airplicer_ui_dump.xml"
        run_adb(["pull", device_path, local_tmp], lenient=True)

        with open(local_tmp, "r", encoding="utf-8") as f:
            content = f.read()

        if "<hierarchy" in content and len(content) > 100:
            print(f"[ADB]   → ✅ 成功 ({len(content)} bytes)")
            return content
        else:
            print("[ADB]   → 文件为空或内容无效")
            return None
    except Exception as e:
        print(f"[ADB]   → 失败: {e}")
        return None


def _dump_strategy_exec_out() -> Optional[str]:
    """策略 3: exec-out 直接输出到 stdout"""
    print("[ADB] dump 策略3: exec-out 直接输出 ...")
    try:
        content = run_adb(
            ["exec-out", "uiautomator", "dump", "/dev/tty"],
            lenient=True, timeout=15,
        )

        if "Killed" in content and "<hierarchy" not in content:
            print("[ADB]   → 进程被 Killed")
            return None

        # 提取 XML 部分
        if "<?xml" in content:
            content = content[content.index("<?xml"):]
        elif "<hierarchy" in content:
            content = content[content.index("<hierarchy"):]

        if "<hierarchy" in content and len(content) > 100:
            print(f"[ADB]   → ✅ 成功 ({len(content)} bytes)")
            return content
        else:
            print("[ADB]   → 输出无效")
            return None
    except Exception as e:
        print(f"[ADB]   → 失败: {e}")
        return None


def _dump_strategy_dumpsys() -> Optional[str]:
    """
    策略 4 (终极备选): 通过 dumpsys activity top 获取 View 层级。
    输出不是标准 uiautomator XML 格式，需要转换。
    """
    print("[ADB] dump 策略4: dumpsys activity top (View Hierarchy) ...")
    try:
        output = run_adb(
            ["shell", "dumpsys", "activity", "top", "-a"],
            lenient=True, timeout=15,
        )

        if not output or "View Hierarchy" not in output:
            print("[ADB]   → dumpsys 无有效输出")
            return None

        # 从 dumpsys 输出中构造一个简化的 XML
        # dumpsys 格式类似:
        #   View Hierarchy:
        #     DecorView@xxx
        #       android.widget.LinearLayout{...}
        #         android.widget.TextView{... text="显示文字" ...}
        xml_nodes = _convert_dumpsys_to_xml(output)
        if xml_nodes:
            print(f"[ADB]   → ✅ 从 dumpsys 提取 {xml_nodes.count('<node')} 个节点")
            return xml_nodes
        else:
            print("[ADB]   → 转换失败")
            return None
    except Exception as e:
        print(f"[ADB]   → 失败: {e}")
        return None


def _convert_dumpsys_to_xml(dumpsys_output: str) -> Optional[str]:
    """
    将 dumpsys activity top 的 View Hierarchy 转换为
    与 uiautomator dump 兼容的简化 XML 格式。
    """
    lines = dumpsys_output.split("\n")
    nodes = []

    # 找到 View Hierarchy 部分
    in_hierarchy = False
    for line in lines:
        if "View Hierarchy:" in line:
            in_hierarchy = True
            continue
        if in_hierarchy:
            # View Hierarchy 在遇到空行或新的 section 时结束
            if not line.strip() or (line.strip() and not line.startswith(" ")):
                if nodes:  # 已经有数据了就停止
                    break
                continue

            # 解析每一行的 View 信息
            # 格式: "    android.widget.TextView{id/xxx V.. ... 100,200-300,400 #xxxxxxxx ...}"
            stripped = line.strip()
            if not stripped:
                continue

            # 提取类名
            class_match = re.match(r"([\w.$]+)\{", stripped)
            if not class_match:
                continue

            class_name = class_match.group(1)

            # 提取坐标 (格式: x1,y1-x2,y2)
            coord_match = re.search(r"(\d+),(\d+)-(\d+),(\d+)", stripped)
            bounds = ""
            if coord_match:
                x1, y1, x2, y2 = (int(coord_match.group(i)) for i in range(1, 5))
                bounds = f"[{x1},{y1}][{x2},{y2}]"

            # 提取文本 (格式: text="...")
            text = ""
            text_match = re.search(r'text="([^"]*)"', stripped) or \
                         re.search(r"text=(\S+)", stripped)
            if text_match:
                text = text_match.group(1)

            # 提取 resource-id
            rid = ""
            id_match = re.search(r"id/(\S+)", stripped)
            if id_match:
                rid = id_match.group(1).rstrip("}")

            # 提取 content-desc
            desc = ""
            desc_match = re.search(r'desc="([^"]*)"', stripped)
            if desc_match:
                desc = desc_match.group(1)

            # 只收集有文本或有坐标的节点
            if bounds and (text or desc):
                nodes.append(
                    f'<node class="{class_name}" text="{text}" '
                    f'content-desc="{desc}" resource-id="{rid}" '
                    f'bounds="{bounds}" clickable="true" />'
                )

    if not nodes:
        return None

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<hierarchy rotation="0">\n'
    for n in nodes:
        xml += f"  {n}\n"
    xml += "</hierarchy>"
    return xml


def dump_ui(save_path: Optional[str] = None) -> ET.Element:
    """
    获取当前 UI 层级，返回解析后的 XML 根节点。

    依次尝试 4 种策略：
    1. 标准 uiautomator dump
    2. 压缩模式 uiautomator dump --compressed
    3. exec-out 直接输出
    4. dumpsys activity top 提取 (终极备选)

    :param save_path: 可选，保存 XML 文件的本地路径
    :return: XML Element 根节点
    """
    device_path = "/sdcard/window_dump.xml"

    # 先清理旧文件
    run_adb(["shell", "rm", "-f", device_path], lenient=True)

    xml_content = None

    # 依次尝试各策略
    strategies = [
        lambda: _dump_strategy_standard(device_path),
        lambda: _dump_strategy_compressed(device_path),
        _dump_strategy_exec_out,
        _dump_strategy_dumpsys,
    ]

    for strategy in strategies:
        xml_content = strategy()
        if xml_content and "<hierarchy" in xml_content:
            break

    if not xml_content or "<hierarchy" not in xml_content:
        raise RuntimeError(
            "UI dump 失败: 所有策略均未成功。\n"
            "请尝试:\n"
            "  1. 手动运行 'adb shell uiautomator dump' 检查是否正常\n"
            "  2. 重启设备后重试\n"
            "  3. 确认设备上无其他自动化工具在运行"
        )

    # 可选保存调试副本
    if save_path:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(xml_content)

    root = ET.fromstring(xml_content)
    return root


def _parse_bounds(bounds_str: str) -> Tuple[int, int, int, int]:
    """解析 bounds 属性 '[x1,y1][x2,y2]' 为 (x1, y1, x2, y2)"""
    match = re.findall(r"\[(\d+),(\d+)\]", bounds_str)
    if len(match) != 2:
        raise ValueError(f"无法解析 bounds: {bounds_str}")
    x1, y1 = int(match[0][0]), int(match[0][1])
    x2, y2 = int(match[1][0]), int(match[1][1])
    return x1, y1, x2, y2


def _get_center(bounds_str: str) -> Tuple[int, int]:
    """从 bounds 属性获取元素中心坐标"""
    x1, y1, x2, y2 = _parse_bounds(bounds_str)
    return (x1 + x2) // 2, (y1 + y2) // 2


def find_elements_by_text(root: ET.Element, text: str,
                          exact: bool = False) -> List[Dict]:
    """
    在 UI dump 的 XML 中查找包含指定文本的元素。

    :param root: XML 根节点
    :param text: 要查找的文本
    :param exact: 是否精确匹配
    :return: 匹配元素列表，每个元素包含 text, bounds, center_x, center_y 等信息
    """
    results = []
    for node in root.iter("node"):
        node_text = node.get("text", "")
        content_desc = node.get("content-desc", "")

        matched = False
        match_text = ""
        if exact:
            if node_text == text or content_desc == text:
                matched = True
                match_text = node_text or content_desc
        else:
            if text in node_text or text in content_desc:
                matched = True
                match_text = node_text or content_desc

        if matched:
            bounds = node.get("bounds", "")
            if bounds:
                cx, cy = _get_center(bounds)
                results.append({
                    "text": match_text,
                    "content_desc": content_desc,
                    "resource_id": node.get("resource-id", ""),
                    "class": node.get("class", ""),
                    "bounds": bounds,
                    "center_x": cx,
                    "center_y": cy,
                    "clickable": node.get("clickable", "false") == "true",
                })
    return results


def find_elements_by_resource_id(root: ET.Element, resource_id: str) -> List[Dict]:
    """
    在 UI dump 的 XML 中按 resource-id 查找元素。

    :param root: XML 根节点
    :param resource_id: resource-id（支持部分匹配）
    :return: 匹配元素列表
    """
    results = []
    for node in root.iter("node"):
        rid = node.get("resource-id", "")
        if resource_id in rid:
            bounds = node.get("bounds", "")
            if bounds:
                cx, cy = _get_center(bounds)
                results.append({
                    "text": node.get("text", ""),
                    "content_desc": node.get("content-desc", ""),
                    "resource_id": rid,
                    "class": node.get("class", ""),
                    "bounds": bounds,
                    "center_x": cx,
                    "center_y": cy,
                    "clickable": node.get("clickable", "false") == "true",
                })
    return results


def find_and_tap(root: ET.Element, text: str, exact: bool = False) -> bool:
    """
    在 UI dump 中查找文本元素并点击第一个匹配项。

    :return: 是否成功找到并点击
    """
    elements = find_elements_by_text(root, text, exact=exact)
    if elements:
        el = elements[0]
        tap(el["center_x"], el["center_y"])
        return True
    print(f"[ADB] 未找到包含文本 '{text}' 的元素")
    return False


def screenshot(local_path: str = "/tmp/airplicer_screenshot.png") -> str:
    """截取屏幕截图并保存到本地"""
    device_path = "/sdcard/screenshot.png"
    run_adb(["shell", "screencap", "-p", device_path])
    run_adb(["pull", device_path, local_path])
    print(f"[ADB] 截图已保存: {local_path}")
    return local_path


def get_current_package() -> str:
    """获取当前前台应用包名"""
    output = run_adb([
        "shell", "dumpsys", "window", "displays",
    ])
    # 尝试匹配 mCurrentFocus 或 mFocusedApp
    for line in output.split("\n"):
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            match = re.search(r"(\S+)/(\S+)", line)
            if match:
                return match.group(1)
    return ""


def wait_for_app(package: str = config.MEITUAN_PACKAGE,
                 timeout: int = config.PAGE_LOAD_TIMEOUT) -> bool:
    """等待指定 App 出现在前台"""
    start = time.time()
    while time.time() - start < timeout:
        current = get_current_package()
        if package in current:
            return True
        time.sleep(1)
    return False
