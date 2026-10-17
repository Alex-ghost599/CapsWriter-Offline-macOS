#!/bin/zsh
# 从 Finder 双击时由 Terminal 启动，固定使用现有环境，不自动安装或升级。
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print -u2 '缺少现有 .venv/bin/python，请先完成项目环境安装。'
  exit 1
fi
.venv/bin/python scripts/capswriter_entry.py "${@:-start}"
result=$?
if [[ $result -ne 0 && -t 0 ]]; then
  read '?启动未完成。按回车关闭窗口。'
fi
exit $result
