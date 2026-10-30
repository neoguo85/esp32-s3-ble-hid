#!/bin/bash
# 构建 ESP32 HID Sender.app —— 双击即用的 macOS 图形界面
set -e
cd "$(dirname "$0")"

APP="build/ESP32 HID Sender.app"
rm -rf build
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp app.py "$APP/Contents/Resources/app.py"
cp hid_chars.py "$APP/Contents/Resources/hid_chars.py"
cp Info.plist "$APP/Contents/Info.plist"
cp assets/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
mkdir -p "$APP/Contents/Resources/assets"
cp assets/appicon.png "$APP/Contents/Resources/assets/appicon.png"
printf 'APPL????' > "$APP/Contents/PkgInfo"

cat > "$APP/Contents/MacOS/ESP32HIDSender" <<'LAUNCH'
#!/bin/bash
# 依次探测可用的 Python（需要 tkinter + bleak）
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
for PY in \
  /opt/homebrew/bin/python3 \
  "$HOME/.workbuddy/binaries/python/versions/3.13.12/bin/python3" \
  /usr/local/bin/python3 \
  /usr/bin/python3
do
  if [ -x "$PY" ] && "$PY" -c "import tkinter, bleak" >/dev/null 2>&1; then
    exec "$PY" "$RES/app.py"
  fi
done
osascript -e 'display alert "缺少运行环境" message "需要带 tkinter 与 bleak 的 Python 3。\n\n终端执行： pip3 install bleak"'
exit 1
LAUNCH

chmod +x "$APP/Contents/MacOS/ESP32HIDSender"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 && SIGN="已 ad-hoc 签名" || SIGN="未签名"

echo "✅ 构建完成"
echo "   $(pwd)/$APP"
echo "   $SIGN"
echo ""
echo "把 .app 拖到「应用程序」或桌面，双击即可运行。"
echo "首次发送时系统会询问蓝牙权限，选「好」。"
