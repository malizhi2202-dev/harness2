#!/usr/bin/env bash
# harness2 一键源码编译 + 安装 + 验证
#
#   ./start.sh                完整流程：建 venv → 可编辑安装 → 入口验证 → 自检 → demo
#   ./start.sh --install      只做编译安装（venv + uv pip install -e），不跑验证
#   ./start.sh --link         全局安装到 ~/.local/bin（任意目录可敲 harness2，重启后仍有效）
#   ./start.sh --check        只跑环境自检
#   ./start.sh --demo         只跑 demo（需已安装）
#   ./start.sh --test         跑仓库自带 pytest
#   ./start.sh --serve        起 litellm bridge（需 GOOGLE_CLOUD_PROJECT + .[bridge]，默认不装）
#
# harness2 本体是纯 Python 包 + CLI（harness2 / harness2-setup / harness2-demo /
# harness2-check / harness2-judge / harness2-report / harness2-bridge），没有前后端
# 服务、没有 Go/Rust 需要 make。所以「源码编译安装」= 源码树内 editable 安装：不拉
# 远端包体，代码改动立即生效，egg-info/dist-info 由 setuptools 构建后端现场生成。
#
# 依赖：uv（~/.local/bin）+ Python >=3.12,<3.14（系统默认 3.14 不满足，用 3.13）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

export PATH="$HOME/.local/bin:$PATH"
export UV_DEFAULT_INDEX="${UV_DEFAULT_INDEX:-https://mirrors.aliyun.com/pypi/simple}"

command -v uv >/dev/null || { echo "✗ 找不到 uv（https://astral.sh/uv）"; exit 1; }

VENV="$ROOT/.venv"
PY="$VENV/bin/python"
LOGS="$ROOT/.logs"
mkdir -p "$LOGS"

do_install() {
  echo "==> [1/4] 建 venv（Python 3.13；pyproject 要求 >=3.12,<3.14）"
  if [ ! -x "$PY" ]; then
    uv venv --python 3.13 "$VENV"
  fi
  "$PY" -V

  echo "==> [2/4] 源码可编辑安装：uv pip install -e '.[render]'"
  uv pip install -e '.[render]'

  echo "==> [3/4] 确认是 editable 安装（.pth 指回源码树，不是拷贝进 site-packages）"
  SP="$("$PY" -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')"
  sed -n '1p' "$SP"/*harness2*.pth 2>/dev/null || echo "    (setuptools>=64 的 editable finder 模式，无 .pth 指向)"

  echo "==> [4/4] 验证 7 个入口 + 包可导入"
  for cmd in harness2 harness2-setup harness2-demo harness2-check \
             harness2-judge harness2-report harness2-bridge; do
    [ -x "$VENV/bin/$cmd" ] || { echo "✗ 缺入口 $cmd"; exit 1; }
    printf '    ✓ %-16s %s\n' "$cmd" "$("$VENV/bin/$cmd" --help 2>&1 | head -1)"
  done
  "$PY" -c 'import harness2; print("    ✓ import harness2", harness2.__file__)'
}

do_link() {
  echo "==> 全局安装到 ~/.local/bin（重启后仍可用：该目录已在 .bashrc / .profile 的 PATH 里）"
  BIN="$HOME/.local/bin"
  mkdir -p "$BIN"
  for cmd in harness2 harness2-setup harness2-demo harness2-check \
             harness2-judge harness2-report harness2-bridge; do
    cat > "$BIN/$cmd" <<EOF
#!/usr/bin/env bash
# harness2 全局入口 -> $VENV/bin/$cmd
# 直接调用 venv 里的 console script，不依赖 activate，任意目录可用。
exec "$VENV/bin/$cmd" "\$@"
EOF
    chmod +x "$BIN/$cmd"
    printf '    ✓ %s\n' "$BIN/$cmd"
  done
  # 短别名：harness -> harness2（防止手抖漏掉 2）
  printf '#!/usr/bin/env bash\n# 别名：harness -> harness2\nexec "%s/bin/harness2" "$@"\n' "$VENV" > "$BIN/harness"
  chmod +x "$BIN/harness"
  echo "    ✓ $BIN/harness（别名）"
  case ":$PATH:" in
    *":$BIN:"*) echo "    ✓ $BIN 已在 PATH 中" ;;
    *) echo "    ⚠ $BIN 不在当前 PATH；确认 .bashrc 里有 export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
  esac
  command -v harness2 >/dev/null && echo "    ✓ 新终端可直接使用：harness2 / harness2-setup / harness2-demo ..."
}

do_check() {
  echo "==> 环境自检"
  "$PY" -m harness2.cli.setup --help >/dev/null && echo "    ✓ setup CLI 可加载"
  # 基准套件需要 third_party 材料；这份 copy 可能没有 —— 如实报告，不当失败
  "$PY" - <<'PY'
from harness2 import install
missing = install.missing_runners()
print("    ✓ 基准 runner/patches 齐全" if not missing else f"    ⚠ {missing}")
PY
}

do_demo() {
  echo "==> 跑 demo（本地确定性 editor + 真实 Python 执行，无模型调用、不花钱）"
  # demo 输出目录必须干净：schedule 的 _resume() 见到已有 summary.json 就按断点续跑，
  # 直接把任务标成 skipped 返回（execution_count=0、editor_calls=0），等于什么都没跑。
  DEMO_OUT="${HARNESS2_DEMO_OUT:-$ROOT/runs/demo-fresh}"
  if [ -e "$DEMO_OUT" ]; then
    echo "    ⚠ $DEMO_OUT 已存在（含上次 summary.json），改用带时间戳的新目录避免假 skipped"
    DEMO_OUT="$ROOT/runs/demo-$(date +%Y%m%d-%H%M%S)"
  fi
  mkdir -p "$ROOT/runs"
  "$PY" -m harness2.cli.demo --output "$DEMO_OUT" --k 2 --mode both 2>&1 | tail -20
  echo "    ✓ 产物：$DEMO_OUT"
  "$PY" - "$DEMO_OUT/demo.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"    真实执行次数 execution_count={d['execution_count']}  "
      f"editor_calls={d['editor_calls']}  failed={d['failed']}")
if d["execution_count"] == 0:
    raise SystemExit("✗ execution_count=0：demo 未真正执行")
PY
}

do_test() {
  echo "==> pytest（仓库自带用例）"
  uv pip install -q pytest
  "$PY" -m pytest -q tests 2>&1 | tail -15
}

do_serve() {
  echo "==> 启动 litellm bridge（Vertex 模型代理）"
  [ -x "$VENV/bin/litellm" ] || { echo "✗ bridge 依赖未装：uv pip install -e '.[bridge]'"; exit 1; }
  [ -n "${GOOGLE_CLOUD_PROJECT:-}" ] || { echo "✗ 需要 GOOGLE_CLOUD_PROJECT（可写入项目根 .env）"; exit 1; }
  exec "$VENV/bin/harness2-bridge" --host 127.0.0.1
}

case "${1:---all}" in
  --install) do_install ;;
  --link)    do_link ;;
  --check)   do_check ;;
  --demo)    do_demo ;;
  --test)    do_test ;;
  --serve)   do_serve ;;
  --all|"")  do_install; do_check; do_demo ;;
  -h|--help) sed -n '2,20p' "$0" ;;
  *) echo "未知参数: $1"; sed -n '2,20p' "$0"; exit 1 ;;
esac
