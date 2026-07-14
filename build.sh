#!/bin/bash

# TPP Chat Assistant Build Script

echo "=== TPP Live Assistant Build Script ==="
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed"
    exit 1
fi

echo "📦 Installing dependencies..."
pip install -r requirements.txt
pip install pyinstaller

echo ""
echo "🎬 Installing Playwright browsers..."
python -m playwright install chromium

echo ""
read -p "What do you want to build? (1=Windows EXE, 2=macOS APP, 3=Both): " choice

case $choice in
    1)
        echo "🏗️ Building Windows EXE..."
        pyinstaller --windowed --onefile --name TPPchat --icon=icon.ico main.py
        echo "✅ Done! Output: dist/TPPchat.exe"
        ;;
    2)
        echo "🏗️ Building macOS APP..."
        pyinstaller build.spec
        echo "✅ Done! Output: dist/TPPchat.app"
        ;;
    3)
        echo "🏗️ Building for both platforms..."
        echo "Building Windows EXE..."
        pyinstaller --windowed --onefile --name TPPchat --icon=icon.ico main.py
        echo "✅ Windows EXE done: dist/TPPchat.exe"

        echo ""
        echo "Building macOS APP..."
        pyinstaller build.spec
        echo "✅ macOS APP done: dist/TPPchat.app"
        ;;
    *)
        echo "❌ Invalid choice"
        exit 1
        ;;
esac

echo ""
echo "=== Build Complete ==="
