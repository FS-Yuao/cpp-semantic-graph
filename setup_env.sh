#!/usr/bin/env bash
# ============================================================
# cpp_semantic_graph Python 环境一键重建脚本
# 用途: 换机器/换项目时,从零创建可用的 venv
#   - 核心依赖: clang / PyYAML / mcp  (full-parse + MCP server + doc content_scan)
#   - 可选依赖: sentence-transformers  (doc embedding 语义关联, 含 torch, 较大)
# 用法:
#   ./setup_env.sh                  # 默认在项目内创建 .venv
#   ./setup_env.sh /path/to/venv    # 指定 venv 路径
#   ./setup_env.sh --with-docs      # 同时装 embedding 依赖
#   ./setup_env.sh /path/to/venv --with-docs
# 前置(系统级,脚本不代装):
#   - python3 >= 3.10
#   - libclang (匹配项目的 LLVM 版本,见 README FAQ;Ubuntu: apt install libclang-18-dev)
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH=""
WITH_DOCS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-docs) WITH_DOCS=1; shift ;;
    -h|--help)   sed -n '2,14p' "$0"; exit 0 ;;
    *)           VENV_PATH="$1"; shift ;;
  esac
done
VENV_PATH="${VENV_PATH:-$SCRIPT_DIR/.venv}"

echo "==> 创建 venv: $VENV_PATH"
python3 -m venv "$VENV_PATH"

PIP="$VENV_PATH/bin/pip"
PY="$VENV_PATH/bin/python"

echo "==> 升级 pip"
"$PIP" install --upgrade pip --quiet

echo "==> 安装核心依赖 (requirements.txt)"
"$PIP" install -r "$SCRIPT_DIR/requirements.txt"

if [[ "$WITH_DOCS" -eq 1 ]]; then
  echo "==> 安装 doc embedding 依赖 (requirements-docs.txt, 含 torch, 体积较大)"
  "$PIP" install -r "$SCRIPT_DIR/requirements-docs.txt"
fi

echo "==> 验证核心依赖可导入"
"$PY" -c "import clang.cindex, yaml, mcp; print('核心依赖 OK: clang / PyYAML / mcp')"

cat <<EOF

==> 完成。venv: $VENV_PATH

后续使用 (假设当前目录是 cpp_semantic_graph 项目根):
  # 设置数据库路径
  export CPP_GRAPH_DB=/path/to/semantic_graph_full.db
  # 启动 MCP server (供 Claude Code / Cursor 等 MCP 客户端连接)
  $VENV_PATH/bin/python -m cpp_semantic_graph.mcp_server.server
  # 全量解析 (注意: --db 是全局参数,必须放在 full-parse 子命令之前)
  PYTHONPATH=$SCRIPT_DIR $VENV_PATH/bin/python -m cpp_semantic_graph \\
    --db /path/to/semantic_graph_full.db \\
    full-parse --config cpp_semantic_graph.yaml
  # 未装 embedding 时后补(可选):
  #   $VENV_PATH/bin/pip install -r $SCRIPT_DIR/requirements-docs.txt
EOF
