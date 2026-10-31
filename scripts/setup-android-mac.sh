#!/bin/bash
# AquaPulse: install everything needed to build and run the Android app on a Mac (Apple Silicon).
# Run:  bash scripts/setup-android-mac.sh   (or bash ~/Downloads/setup-android-mac.sh)
# Safe to run again. Takes 20 to 40 minutes (about 6 GB of downloads).
set -euo pipefail

say() { printf "\n\033[1;34m==> %s\033[0m\n" "$1"; }

# 1. Homebrew (the Mac app installer)
if ! command -v brew >/dev/null 2>&1; then
  say "Installing Homebrew (it may ask for your Mac password)"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
eval "$(/opt/homebrew/bin/brew shellenv)"

# 2. Flutter, Android Studio and the Android command-line tools
say "Installing Flutter, Android Studio and the Android tools"
brew install --cask flutter android-studio android-commandlinetools

# 3. Settings, saved in ~/.zshrc so every new Terminal has them
ANDROID_HOME="$HOME/Library/Android/sdk"
JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
mkdir -p "$ANDROID_HOME"
if ! grep -q "AquaPulse Android" "$HOME/.zshrc" 2>/dev/null; then
  cat >> "$HOME/.zshrc" <<RC

# AquaPulse Android (added by setup-android-mac.sh)
export ANDROID_HOME="\$HOME/Library/Android/sdk"
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export PATH="\$ANDROID_HOME/platform-tools:\$ANDROID_HOME/emulator:\$ANDROID_HOME/cmdline-tools/latest/bin:\$PATH"
RC
fi
export ANDROID_HOME JAVA_HOME
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"

# 4. The Android SDK pieces: platform 35, build tools, emulator and a phone image
say "Downloading the Android SDK (a few GB, please wait)"
SDK="sdkmanager --sdk_root=$ANDROID_HOME"
yes | $SDK --licenses >/dev/null || true
$SDK "cmdline-tools;latest" "platform-tools" "platforms;android-35" "build-tools;35.0.0" \
     "emulator" "system-images;android-35;google_apis;arm64-v8a"

# 5. Tell Flutter where things are, and accept the licences
say "Setting up Flutter"
flutter config --android-sdk "$ANDROID_HOME" >/dev/null
yes | flutter doctor --android-licenses >/dev/null || true

# 6. A virtual phone called "aquapulse" (Pixel 8, Android 15)
if ! "$ANDROID_HOME/cmdline-tools/latest/bin/avdmanager" list avd | grep -q "Name: aquapulse"; then
  say "Making the virtual phone 'aquapulse'"
  echo no | "$ANDROID_HOME/cmdline-tools/latest/bin/avdmanager" create avd -n aquapulse \
    -k "system-images;android-35;google_apis;arm64-v8a" -d pixel_8
fi

say "Checking"
flutter doctor || true

cat <<DONE

✅ Done. "Flutter" and "Android toolchain" should have green ticks above
   (Xcode, Chrome and CocoaPods do not matter for AquaPulse).

Next, in a NEW Terminal window:
  emulator -avd aquapulse &          # starts the virtual phone (first start takes a minute)
  cd "/Users/niazi_127.0.0.1/Documents/Water Management System/aquapulse"
  make up
  make apk && make apk-install       # builds the app and opens it on the phone
DONE
