#!/bin/bash
set -e

echo "🔧 安裝系統套件（ffmpeg）..."
apt-get install -y ffmpeg 2>/dev/null || echo "ffmpeg 安裝跳過（非 root）"

echo "📦 安裝 Python 套件..."
pip install -r requirements.txt

echo "✅ Build 完成！"
