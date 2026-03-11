#!/bin/bash
set -e

echo "🔧 安裝系統套件（ffmpeg）..."
apt-get install -y ffmpeg 2>/dev/null || echo "ffmpeg 安裝跳過（非 root）"

echo "📦 安裝 Python 套件..."
pip install -r requirements.txt

echo "🔤 下載 Noto Sans TC 字型..."
mkdir -p fonts
python -c "
import urllib.request, os

files = {
    'fonts/NotoSansTC-Regular.otf': 'https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/TraditionalChinese/NotoSansCJKtc-Regular.otf',
    'fonts/NotoSansTC-Bold.otf':    'https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/TraditionalChinese/NotoSansCJKtc-Bold.otf',
}
for path, url in files.items():
    if not os.path.exists(path):
        print(f'下載 {path}...')
        urllib.request.urlretrieve(url, path)
        print(f'✅ {path} 完成')
    else:
        print(f'⏭️ {path} 已存在，跳過')
"

echo "✅ Build 完成！"
