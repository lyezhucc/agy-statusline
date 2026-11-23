#!/usr/bin/env bash
set -euo pipefail

# Antigravity CLI (agy) 2-line Statusline HUD installer / upgrader / uninstaller
# Repo: https://github.com/lyezhucc/agy-statusline

RAW_URL="${AGY_STATUSLINE_URL:-https://raw.githubusercontent.com/lyezhucc/agy-statusline/main/statusline.py}"
AGY_DIR="${AGY_CONFIG_DIR:-$HOME/.gemini/antigravity-cli}"
TARGET_SCRIPT="$AGY_DIR/statusline.py"
SETTINGS_FILE="$AGY_DIR/settings.json"

MODE="install"
STACK_WITH_DEFAULT="true"

usage() {
  cat <<'EOF'
用法: install.sh [选项]

选项:
  --stack       在官方默认底栏正下方叠加 2 行 HUD（默认）
  --replace     替换官方默认底栏，仅显示本 HUD
  --uninstall   卸载脚本并移除 settings.json 中的 statusLine 配置
  -h, --help    显示帮助信息
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stack)
      STACK_WITH_DEFAULT="true"
      shift
      ;;
    --replace)
      STACK_WITH_DEFAULT="false"
      shift
      ;;
    --uninstall)
      MODE="uninstall"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知选项: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误: 未检测到 python3，请先安装 Python 3。" >&2
  exit 1
fi

update_settings() {
  local action="$1"
  local stack_flag="$2"
  python3 - "$SETTINGS_FILE" "$action" "$stack_flag" <<'PY'
import json, os, shutil, sys, tempfile

settings_path, action, stack_flag = sys.argv[1], sys.argv[2], sys.argv[3]
parent = os.path.dirname(settings_path)
os.makedirs(parent, exist_ok=True)

data = {}
if os.path.exists(settings_path):
    with open(settings_path, "r", encoding="utf-8") as fh:
        raw = fh.read().strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"错误: {settings_path} 不是合法 JSON，已中止以防覆盖: {e}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(data, dict):
            print(f"错误: {settings_path} 顶层必须是 JSON 对象，已中止。", file=sys.stderr)
            sys.exit(1)
    shutil.copy2(settings_path, settings_path + ".bak")

if action == "install":
    data["statusLine"] = {
        "type": "command",
        "command": "~/.gemini/antigravity-cli/statusline.py",
        "stack_with_default": (stack_flag == "true"),
    }
elif action == "uninstall":
    data.pop("statusLine", None)

fd, tmp_path = tempfile.mkstemp(prefix=".settings.", dir=parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, settings_path)
except Exception:
    if os.path.exists(tmp_path):
        os.unlink(tmp_path)
    raise
PY
}

if [[ "$MODE" == "uninstall" ]]; then
  if [[ -f "$TARGET_SCRIPT" ]]; then
    rm -f "$TARGET_SCRIPT"
    echo "✓ 已删除 $TARGET_SCRIPT"
  fi
  if [[ -f "$SETTINGS_FILE" ]]; then
    update_settings "uninstall" "$STACK_WITH_DEFAULT"
    echo "✓ 已从 $SETTINGS_FILE 移除 statusLine 配置（原配置已备份至 settings.json.bak）"
  fi
  echo "卸载完成。已打开的 agy 终端可输入 /statusline reset 立即恢复默认底栏。"
  exit 0
fi

mkdir -p "$AGY_DIR"

TMP_SCRIPT="$(mktemp "$AGY_DIR/.statusline.py.XXXXXX")"
trap 'rm -f "$TMP_SCRIPT"' EXIT

SCRIPT_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/statusline.py" ]]; then
  cp "$SCRIPT_DIR/statusline.py" "$TMP_SCRIPT"
  echo "✓ 使用本地脚本 $SCRIPT_DIR/statusline.py"
else
  if ! command -v curl >/dev/null 2>&1; then
    echo "错误: 未检测到 curl，无法下载 $RAW_URL。" >&2
    exit 1
  fi
  echo "正在下载 statusline.py ..."
  curl -fsSL "$RAW_URL" -o "$TMP_SCRIPT"
fi

# Verify syntax before replacing
python3 -m py_compile "$TMP_SCRIPT"
chmod 755 "$TMP_SCRIPT"
mv "$TMP_SCRIPT" "$TARGET_SCRIPT"
trap - EXIT

update_settings "install" "$STACK_WITH_DEFAULT"

echo ""
echo "✓ 安装 / 升级完成！"
echo "  脚本路径: $TARGET_SCRIPT"
echo "  配置文件: $SETTINGS_FILE (stack_with_default=$STACK_WITH_DEFAULT)"
echo ""
echo "生效方式:"
echo "  - 新开的 agy 终端会自动生效"
echo "  - 已打开的 agy 终端可直接输入: /statusline ~/.gemini/antigravity-cli/statusline.py"
echo "  - 临时开关: /statusline off  |  /statusline on"
