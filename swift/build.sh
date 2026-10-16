#!/bin/bash
# 把 Swift 悬浮面板编译成一个自包含的 macOS .app
# 产物：build/WBHistoryPanel.app （内含 server.py / wbhistory.py / web/）
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
BUILD="$ROOT/build"
APP="$BUILD/WBHistoryPanel.app"
NAME="WBHistoryPanel"

echo "==> 编译 $NAME"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# 1) 编译可执行文件
echo "    swiftc …"
swiftc -O \
  -target "$(uname -m)-apple-macosx13.0" \
  -o "$APP/Contents/MacOS/$NAME" \
  "$HERE/Panel.swift"

# 2) 拷贝服务端与前端资源进 .app
echo "    拷贝资源 …"
cp "$ROOT/server.py"     "$APP/Contents/Resources/"
cp "$ROOT/wbhistory.py"  "$APP/Contents/Resources/"
cp -R "$ROOT/web"        "$APP/Contents/Resources/"

# 3) Info.plist：菜单栏常驻（无 Dock 图标）+ 放开 localhost 访问
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>WBHistoryPanel</string>
    <key>CFBundleDisplayName</key>     <string>会话面板</string>
    <key>CFBundleIdentifier</key>      <string>local.wbhistory.panel</string>
    <key>CFBundleExecutable</key>      <string>$NAME</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleShortVersionString</key> <string>1.0.0</string>
    <key>CFBundleVersion</key>         <string>1</string>
    <key>LSMinimumSystemVersion</key>  <string>13.0</string>
    <key>LSUIElement</key>             <true/>
    <key>NSHighResolutionCapable</key> <true/>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsLocalNetworking</key> <true/>
        <key>NSExceptionDomains</key>
        <dict>
            <key>127.0.0.1</key>
            <dict>
                <key>NSExceptionAllowsInsecureHTTPLoads</key> <true/>
                <key>NSIncludesSubdomains</key> <true/>
            </dict>
            <key>localhost</key>
            <dict>
                <key>NSExceptionAllowsInsecureHTTPLoads</key> <true/>
                <key>NSIncludesSubdomains</key> <true/>
            </dict>
        </dict>
    </dict>
</dict>
</plist>
PLIST

# 4) 简易签名（避免 Gatekeeper 直接拦截；ad-hoc，不代表公证）
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "    （未签名，首次打开需右键→打开）"

echo "==> 完成：$APP"
echo "    双击即可运行；菜单栏会出现「会话」，点击显示/隐藏面板。"
